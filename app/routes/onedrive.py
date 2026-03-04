"""Routes for OneDrive Personal connection and file browsing/downloading."""

from __future__ import annotations

import os

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

import msal

from ..services import onedrive_service
from ..services.job_registry import register_job
from ..tasks import download_onedrive_file_task, download_onedrive_folder_task

onedrive_bp = Blueprint("onedrive", __name__, url_prefix="/onedrive")


# ── Helpers ──────────────────────────────────────────────────────────────


def _cfg():
    return current_app.config


def _msal_app(token_cache=None):
    return onedrive_service.get_msal_app(
        client_id=_cfg()["ONEDRIVE_CLIENT_ID"],
        client_secret=_cfg()["ONEDRIVE_CLIENT_SECRET"],
        authority=_cfg()["ONEDRIVE_AUTHORITY"],
        token_cache=token_cache,
    )


def _get_token() -> str | None:
    """Load token from Redis cache and acquire silently.  Returns None if not connected."""
    account_id = session.get("onedrive_account_id")
    if not account_id:
        return None
    redis_url = _cfg()["CELERY_BROKER_URL"]
    cache = onedrive_service.load_token_cache(redis_url, account_id)
    app = _msal_app(cache)
    try:
        token = onedrive_service.get_access_token(app, account_id, _cfg()["ONEDRIVE_SCOPES"])
        onedrive_service.save_token_cache(redis_url, account_id, cache)
        return token
    except RuntimeError:
        return None


def _local_folders(base: str, max_depth: int = 2) -> list[str]:
    results: list[str] = [""]
    for root, dirs, _ in os.walk(base):
        rel = os.path.relpath(root, base)
        depth = 0 if rel == "." else rel.count(os.sep) + 1
        if depth >= max_depth:
            dirs.clear()
            continue
        for d in sorted(dirs):
            sub_rel = os.path.join(rel, d) if rel != "." else d
            results.append(sub_rel.replace(os.sep, "/"))
    return results


def _resolve_dest(base: str, subfolder: str) -> str:
    resolved = os.path.realpath(os.path.join(base, subfolder))
    base_resolved = os.path.realpath(base)
    if not (resolved == base_resolved or resolved.startswith(base_resolved + os.sep)):
        raise ValueError(f"Destination path escapes the allowed directory: {subfolder}")
    return resolved


# ── Routes ───────────────────────────────────────────────────────────────


@onedrive_bp.route("/connect")
def connect():
    """Initiate the OAuth 2.0 Authorization Code flow."""
    if not _cfg().get("ONEDRIVE_CLIENT_ID"):
        flash("OneDrive is not configured. Set ONEDRIVE_CLIENT_ID and ONEDRIVE_CLIENT_SECRET.", "danger")
        return redirect(url_for("storage.connect"))

    app = _msal_app()
    flow = onedrive_service.initiate_auth_code_flow(
        app,
        redirect_uri=_cfg()["ONEDRIVE_REDIRECT_URI"],
        scopes=_cfg()["ONEDRIVE_SCOPES"],
    )
    session["onedrive_flow"] = flow
    return redirect(flow["auth_uri"])


@onedrive_bp.route("/callback")
def callback():
    """Handle the OAuth callback — exchange code for tokens."""
    error = request.args.get("error")
    if error:
        flash(f"OneDrive login failed: {request.args.get('error_description', error)}", "danger")
        return redirect(url_for("storage.connect"))

    flow = session.pop("onedrive_flow", None)
    if not flow:
        flash("OAuth session expired or invalid. Please try connecting again.", "danger")
        return redirect(url_for("storage.connect"))

    cache = msal.SerializableTokenCache()
    app = _msal_app(cache)

    try:
        result = onedrive_service.acquire_token_by_auth_code_flow(
            app,
            flow=flow,
            auth_response=request.args.to_dict(),
        )
    except RuntimeError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("storage.connect"))

    # Extract account ID
    accounts = app.get_accounts()
    account_id = None
    if accounts:
        account_id = accounts[0].get("home_account_id")
    if not account_id:
        account_id = result.get("id_token_claims", {}).get("oid")
    if not account_id:
        flash("Unable to determine OneDrive account identifier. Please try connecting again.", "danger")
        return redirect(url_for("storage.connect"))

    # Persist token cache to Redis
    redis_url = _cfg()["CELERY_BROKER_URL"]
    onedrive_service.save_token_cache(redis_url, account_id, cache)

    display_name = result.get("id_token_claims", {}).get("name", "OneDrive User")

    # Clear any Azure Blob Storage session state; set OneDrive state
    session.pop("account_name", None)
    session.pop("account_key", None)
    session["connection_type"] = "onedrive"
    session["onedrive_account_id"] = account_id
    session["onedrive_display_name"] = display_name

    flash(f"Connected to OneDrive as {display_name}.", "success")
    return redirect(url_for("onedrive.browse"))


@onedrive_bp.route("/browse")
def browse():
    """Browse OneDrive files and folders."""
    token = _get_token()
    if token is None:
        flash("Please connect to OneDrive first.", "warning")
        return redirect(url_for("storage.connect"))

    item_id = request.args.get("item_id")  # None = root

    try:
        items = onedrive_service.list_items(token, item_id=item_id)
    except Exception as exc:
        flash(f"Error listing OneDrive items: {exc}", "danger")
        items = []

    # Breadcrumbs
    breadcrumbs: list[dict] = []
    if item_id:
        try:
            breadcrumbs = onedrive_service.get_parent_chain(token, item_id)
            current_item = onedrive_service.get_item(token, item_id)
            breadcrumbs.append({"id": item_id, "name": current_item["name"]})
        except Exception:
            pass

    base = _cfg()["DOWNLOAD_PATH"]
    dest_subfolder = session.get("dest_subfolder", "")

    return render_template(
        "onedrive_browse.html",
        items=items,
        item_id=item_id,
        breadcrumbs=breadcrumbs,
        local_folders=_local_folders(base),
        dest_subfolder=dest_subfolder,
        display_name=session.get("onedrive_display_name", "OneDrive"),
    )


@onedrive_bp.route("/download", methods=["POST"])
def download():
    """Queue a OneDrive download task."""
    account_id = session.get("onedrive_account_id")
    if not account_id:
        flash("Please connect to OneDrive first.", "warning")
        return redirect(url_for("storage.connect"))

    item_id = request.form.get("item_id", "")
    item_name = request.form.get("item_name", "item")
    is_folder = request.form.get("is_folder", "0") == "1"
    current_item_id = request.form.get("current_item_id", "")  # for redirect back

    dest_subfolder = session.get("dest_subfolder", "")
    base = _cfg()["DOWNLOAD_PATH"]
    try:
        dest_dir = _resolve_dest(base, dest_subfolder)
        os.makedirs(dest_dir, exist_ok=True)
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("onedrive.browse", item_id=current_item_id or None))

    redis_url = _cfg()["CELERY_BROKER_URL"]
    client_id = _cfg()["ONEDRIVE_CLIENT_ID"]
    client_secret = _cfg()["ONEDRIVE_CLIENT_SECRET"]
    authority = _cfg()["ONEDRIVE_AUTHORITY"]
    scopes = _cfg()["ONEDRIVE_SCOPES"]

    if is_folder:
        task = download_onedrive_folder_task.delay(
            account_id, client_id, client_secret, authority, scopes, item_id, item_name, dest_dir
        )
        register_job(redis_url, task.id, {
            "download_type": "onedrive_folder",
            "label": item_name,
            "dest": dest_subfolder or "/",
            "source": "OneDrive",
        })
        flash(
            f"Queued OneDrive folder download: '{item_name}' → {dest_subfolder or '/'}. "
            f'<a href="{url_for("jobs.index")}">View jobs →</a>',
            "info",
        )
    else:
        task = download_onedrive_file_task.delay(
            account_id, client_id, client_secret, authority, scopes, item_id, item_name, dest_dir
        )
        register_job(redis_url, task.id, {
            "download_type": "onedrive_file",
            "label": item_name,
            "dest": dest_subfolder or "/",
            "source": "OneDrive",
        })
        flash(
            f"Queued OneDrive file: '{item_name}' → {dest_subfolder or '/'}. "
            f'<a href="{url_for("jobs.index")}">View jobs →</a>',
            "info",
        )

    return redirect(url_for("onedrive.browse", item_id=current_item_id or None))


@onedrive_bp.route("/api/details/<item_id>")
def api_details(item_id: str):
    """Return JSON stats for a OneDrive folder (on-demand AJAX)."""
    token = _get_token()
    if token is None:
        return jsonify({"error": "Not connected"}), 401
    try:
        details = onedrive_service.get_details(token, item_id=item_id)
        return jsonify(details)
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500


@onedrive_bp.route("/disconnect", methods=["POST"])
def disconnect():
    """Clear OneDrive session state and remove cached tokens."""
    account_id = session.pop("onedrive_account_id", None)
    if account_id:
        try:
            onedrive_service.delete_token_cache(_cfg()["CELERY_BROKER_URL"], account_id)
        except Exception:
            pass
    session.pop("onedrive_display_name", None)
    session.pop("connection_type", None)
    flash("Disconnected from OneDrive.", "info")
    return redirect(url_for("storage.connect"))
