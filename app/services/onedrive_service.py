"""Microsoft Graph / OneDrive Personal service wrapper.

Authentication uses MSAL with a Redis-backed SerializableTokenCache so that
both the Flask web process and the Celery worker share the same token state and
automatic silent refresh.
"""

from __future__ import annotations

import os
import time
from collections import Counter
from typing import Any

import msal
import requests

# ── Redis token cache helpers ─────────────────────────────────────────────


def _redis_client(redis_url: str):
    """Return a redis.Redis instance from *redis_url*."""
    import redis as redis_lib  # imported here to avoid hard dependency at module level
    return redis_lib.from_url(redis_url, decode_responses=True)


def _cache_key(account_id: str) -> str:
    return f"storage_client:msal_cache:{account_id}"


def load_token_cache(redis_url: str, account_id: str) -> msal.SerializableTokenCache:
    """Load the MSAL token cache for *account_id* from Redis."""
    cache = msal.SerializableTokenCache()
    if not redis_url or not account_id:
        return cache
    r = _redis_client(redis_url)
    data = r.get(_cache_key(account_id))
    if data:
        cache.deserialize(data)
    return cache


def save_token_cache(redis_url: str, account_id: str, cache: msal.SerializableTokenCache) -> None:
    """Persist the MSAL token cache to Redis with a 90-day TTL."""
    if cache.has_state_changed:
        r = _redis_client(redis_url)
        r.setex(_cache_key(account_id), 60 * 60 * 24 * 90, cache.serialize())


def delete_token_cache(redis_url: str, account_id: str) -> None:
    """Remove the token cache from Redis (used on disconnect)."""
    r = _redis_client(redis_url)
    r.delete(_cache_key(account_id))


# ── MSAL app factory ──────────────────────────────────────────────────────


def get_msal_app(
    client_id: str,
    client_secret: str,
    authority: str,
    token_cache: msal.SerializableTokenCache | None = None,
) -> msal.ConfidentialClientApplication:
    """Build a ConfidentialClientApplication, optionally with a pre-loaded cache."""
    return msal.ConfidentialClientApplication(
        client_id,
        authority=authority,
        client_credential=client_secret,
        token_cache=token_cache,
    )


# ── OAuth flow ────────────────────────────────────────────────────────────

# Scopes that MSAL manages automatically – passing them explicitly raises ValueError
_RESERVED_SCOPES = {"offline_access", "openid", "profile"}


def _filter_scopes(scopes: list[str]) -> list[str]:
    """Remove reserved scopes that MSAL adds automatically."""
    return [s for s in scopes if s.lower() not in _RESERVED_SCOPES]


def initiate_auth_code_flow(
    msal_app: msal.ConfidentialClientApplication,
    redirect_uri: str,
    scopes: list[str],
) -> dict:
    """Start the auth code flow.  Returns a flow dict to store in the session."""
    return msal_app.initiate_auth_code_flow(
        _filter_scopes(scopes),
        redirect_uri=redirect_uri,
    )


def acquire_token_by_auth_code_flow(
    msal_app: msal.ConfidentialClientApplication,
    flow: dict,
    auth_response: dict,
) -> dict:
    """Complete the auth code flow from the callback query params."""
    result = msal_app.acquire_token_by_auth_code_flow(flow, auth_response)
    if "error" in result:
        raise RuntimeError(f"Token acquisition failed: {result.get('error_description', result['error'])}")
    return result


def get_auth_url(
    msal_app: msal.ConfidentialClientApplication,
    redirect_uri: str,
    scopes: list[str],
    state: str,
) -> str:
    """Return the Microsoft login URL to redirect the user to."""
    return msal_app.get_authorization_request_url(
        _filter_scopes(scopes),
        redirect_uri=redirect_uri,
        state=state,
    )


def acquire_token_by_code(
    msal_app: msal.ConfidentialClientApplication,
    code: str,
    scopes: list[str],
    redirect_uri: str,
) -> dict:
    """Exchange the auth code for tokens.  Returns the result dict from MSAL."""
    result = msal_app.acquire_token_by_authorization_code(
        code,
        scopes=_filter_scopes(scopes),
        redirect_uri=redirect_uri,
    )
    if "error" in result:
        raise RuntimeError(f"Token acquisition failed: {result.get('error_description', result['error'])}")
    return result


def get_access_token(
    msal_app: msal.ConfidentialClientApplication,
    account_id: str,
    scopes: list[str],
) -> str:
    """Silently acquire (and if needed refresh) an access token.

    Raises RuntimeError if an interactive login is required again.
    """
    accounts = msal_app.get_accounts()
    account = next((a for a in accounts if a["home_account_id"] == account_id), None)
    if account is None:
        # Try any account in the cache (single-user app)
        account = accounts[0] if accounts else None

    if account is None:
        raise RuntimeError("No cached account found — please reconnect to OneDrive.")

    result = msal_app.acquire_token_silent(_filter_scopes(scopes), account=account)
    if result is None or "access_token" not in result:
        raise RuntimeError("Silent token acquisition failed — please reconnect to OneDrive.")
    return result["access_token"]


# ── Graph API helpers ─────────────────────────────────────────────────────

GRAPH_BASE = "https://graph.microsoft.com/v1.0"


def _headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "Accept": "application/json"}


def _graph_get(token: str, url: str, params: dict | None = None) -> dict:
    """GET *url* from Microsoft Graph, retrying on 429 throttle responses."""
    for attempt in range(5):
        resp = requests.get(url, headers=_headers(token), params=params, timeout=30)
        if resp.status_code == 429:
            retry_after = int(resp.headers.get("Retry-After", 2 ** attempt))
            time.sleep(retry_after)
            continue
        resp.raise_for_status()
        return resp.json()
    resp.raise_for_status()
    return {}


def get_user_info(token: str) -> dict:
    """Return basic profile info for the signed-in user."""
    return _graph_get(token, f"{GRAPH_BASE}/me", params={"$select": "displayName,userPrincipalName"})


def list_items(token: str, item_id: str | None = None, path: str | None = None) -> list[dict]:
    """List children of a OneDrive folder.

    Pass *item_id* to list by Drive item ID, or *path* (relative to drive root)
    to list by path.  Pass neither to list the drive root.

    Returns a list of normalised item dicts.
    """
    if item_id:
        url = f"{GRAPH_BASE}/me/drive/items/{item_id}/children"
    elif path:
        url = f"{GRAPH_BASE}/me/drive/root:/{path.lstrip('/')}:/children"
    else:
        url = f"{GRAPH_BASE}/me/drive/root/children"

    items = []
    params: dict[str, Any] = {
        "$select": "id,name,folder,file,size,lastModifiedDateTime,@microsoft.graph.downloadUrl",
        "$top": 200,
    }

    while url:
        data = _graph_get(token, url, params=params)
        for raw in data.get("value", []):
            items.append(_normalise_item(raw))
        url = data.get("@odata.nextLink")
        params = {}  # nextLink already includes all params

    return sorted(items, key=lambda x: (not x["is_folder"], x["name"].lower()))


def _normalise_item(raw: dict) -> dict:
    return {
        "id": raw["id"],
        "name": raw["name"],
        "is_folder": "folder" in raw,
        "size": raw.get("size", 0),
        "size_display": _human_size(raw.get("size", 0)),
        "last_modified": raw.get("lastModifiedDateTime", ""),
        "download_url": raw.get("@microsoft.graph.downloadUrl", ""),
        "child_count": raw.get("folder", {}).get("childCount", 0),
    }


def get_item(token: str, item_id: str) -> dict:
    """Fetch a single item by ID and return a normalised dict."""
    raw = _graph_get(
        token,
        f"{GRAPH_BASE}/me/drive/items/{item_id}",
        params={"$select": "id,name,folder,file,size,lastModifiedDateTime,parentReference"},
    )
    return _normalise_item(raw)


def get_item_by_path(token: str, path: str) -> dict:
    """Fetch item metadata by drive-relative path."""
    raw = _graph_get(
        token,
        f"{GRAPH_BASE}/me/drive/root:/{path.lstrip('/')}",
        params={"$select": "id,name,folder,file,size,lastModifiedDateTime,parentReference"},
    )
    return _normalise_item(raw)


def get_parent_chain(token: str, item_id: str) -> list[dict]:
    """Return ordered list of ancestor items from root → parent of *item_id*.

    Used to build breadcrumbs.  Does not include the item itself.
    """
    chain: list[dict] = []
    raw = _graph_get(
        token,
        f"{GRAPH_BASE}/me/drive/items/{item_id}",
        params={"$select": "id,name,parentReference"},
    )
    parent_ref = raw.get("parentReference", {})
    parent_path = parent_ref.get("path", "")  # e.g. /drive/root:/Folder/Sub

    if not parent_path or parent_path.endswith("/root:"):
        return []  # already at root or one level below

    # Strip the /drive/root: prefix and split by /
    relative = parent_path.split("/root:", 1)[-1].lstrip("/")
    if not relative:
        return []

    parts = relative.split("/")
    current_id: str | None = None
    for part in parts:
        if current_id is None:
            # Find root child matching this name
            children = list_items(token)
            match = next((c for c in children if c["name"] == part and c["is_folder"]), None)
        else:
            children = list_items(token, item_id=current_id)
            match = next((c for c in children if c["name"] == part and c["is_folder"]), None)
        if match is None:
            break
        chain.append({"id": match["id"], "name": match["name"]})
        current_id = match["id"]

    return chain


def download_file(token: str, item_id: str, dest_dir: str, filename: str) -> str:
    """Download a single OneDrive file to *dest_dir*.

    Fetches a fresh download URL (via item metadata) to avoid using an expired
    pre-auth URL.  Returns the destination file path.
    """
    # Get a fresh download URL
    data = _graph_get(
        token,
        f"{GRAPH_BASE}/me/drive/items/{item_id}",
        params={"$select": "id,name,@microsoft.graph.downloadUrl"},
    )
    download_url = data.get("@microsoft.graph.downloadUrl")
    if not download_url:
        raise RuntimeError(f"No download URL available for item {item_id}")

    os.makedirs(dest_dir, exist_ok=True)
    dest_path = os.path.join(dest_dir, filename)

    with requests.get(download_url, stream=True, timeout=60) as r:
        r.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in r.iter_content(chunk_size=65536):
                f.write(chunk)

    return dest_path


def list_items_recursive(token: str, item_id: str) -> list[dict]:
    """Return all *file* items under *item_id* recursively.

    Each returned dict has 'id', 'name', 'relative_path' (relative to the
    starting folder), plus the standard normalised fields.
    """
    results: list[dict] = []

    def _walk(folder_id: str, rel_prefix: str) -> None:
        children = list_items(token, item_id=folder_id)
        for child in children:
            if child["is_folder"]:
                _walk(child["id"], os.path.join(rel_prefix, child["name"]))
            else:
                child["relative_path"] = os.path.join(rel_prefix, child["name"])
                results.append(child)

    _walk(item_id, "")
    return results


def get_details(token: str, item_id: str | None = None) -> dict:
    """Return folder statistics: folder_count, file_count, total_size, file_types.

    If *item_id* is None, scans from the drive root.
    Compatible shape with blob_service.get_details().
    """
    folder_count = 0
    file_count = 0
    total_size = 0
    ext_counter: Counter = Counter()

    def _walk(fid: str | None) -> None:
        nonlocal folder_count, file_count, total_size
        children = list_items(token, item_id=fid)
        for child in children:
            if child["is_folder"]:
                folder_count += 1
                _walk(child["id"])
            else:
                file_count += 1
                total_size += child["size"]
                _, ext = os.path.splitext(child["name"])
                ext_counter[ext.lower() if ext else "(none)"] += 1

    _walk(item_id)

    return {
        "folder_count": folder_count,
        "file_count": file_count,
        "total_size": total_size,
        "total_size_display": _human_size(total_size),
        "file_types": [{"ext": ext, "count": cnt} for ext, cnt in ext_counter.most_common()],
    }


# ── Utilities ─────────────────────────────────────────────────────────────


def _human_size(size_bytes: int) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes //= 1024
    return f"{size_bytes:.1f} PB"
