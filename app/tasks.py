"""Celery background tasks for Azure Blob and OneDrive downloads."""

from __future__ import annotations

import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from celery import shared_task

from .services import blob_service
from .services import onedrive_service


@shared_task(bind=True, name="app.tasks.download_blob")
def download_blob_task(
    self,
    account_name: str,
    account_key: str,
    container_name: str,
    blob_name: str,
    dest_dir: str,
) -> dict:
    """Download a single blob to *dest_dir*."""
    self.update_state(
        state="PROGRESS",
        meta={"status": f"Downloading {blob_name}…", "done": 0, "total": 1},
    )
    try:
        client = blob_service.get_client(account_name, account_key)
        path = blob_service.download_blob(client, container_name, blob_name, dest_dir)
        return {"status": "success", "message": f"Saved to {path}", "done": 1, "total": 1}
    except Exception as exc:
        # Returning (not raising) keeps state as SUCCESS with an error payload
        return {"status": "error", "message": str(exc), "done": 0, "total": 1}


@shared_task(bind=True, name="app.tasks.download_prefix")
def download_prefix_task(
    self,
    account_name: str,
    account_key: str,
    container_name: str,
    prefix: str,
    dest_dir: str,
) -> dict:
    """Download all blobs under *prefix* to *dest_dir*, reporting progress."""
    try:
        client = blob_service.get_client(account_name, account_key)

        # Enumerate first so we know the total
        container = client.get_container_client(container_name)
        blobs = list(container.list_blobs(name_starts_with=prefix))
        total = len(blobs)

        if total == 0:
            return {"status": "success", "message": "No blobs found under prefix.", "done": 0, "total": 0}

        done = 0
        for blob in blobs:
            self.update_state(
                state="PROGRESS",
                meta={"status": f"Downloading {blob.name}", "done": done, "total": total},
            )
            blob_service.download_blob(client, container_name, blob.name, dest_dir)
            done += 1

        return {
            "status": "success",
            "message": f"Downloaded {done} of {total} file(s).",
            "done": done,
            "total": total,
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc), "done": 0, "total": 0}


# ── OneDrive tasks ────────────────────────────────────────────────────────


def _get_onedrive_token(account_id: str, client_id: str, client_secret: str, authority: str, scopes: list[str]) -> str:
    """Reconstruct MSAL app, load Redis token cache, and acquire a fresh token."""
    import os
    redis_url = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
    cache = onedrive_service.load_token_cache(redis_url, account_id)
    msal_app = onedrive_service.get_msal_app(client_id, client_secret, authority, cache)
    token = onedrive_service.get_access_token(msal_app, account_id, scopes)
    onedrive_service.save_token_cache(redis_url, account_id, cache)
    return token


@shared_task(bind=True, name="app.tasks.download_onedrive_file")
def download_onedrive_file_task(
    self,
    account_id: str,
    client_id: str,
    client_secret: str,
    authority: str,
    scopes: list[str],
    item_id: str,
    item_name: str,
    dest_dir: str,
) -> dict:
    """Download a single OneDrive file to *dest_dir*."""
    self.update_state(
        state="PROGRESS",
        meta={"status": f"Downloading {item_name}…", "done": 0, "total": 1},
    )
    try:
        token = _get_onedrive_token(account_id, client_id, client_secret, authority, scopes)
        path = onedrive_service.download_file(token, item_id, dest_dir, item_name)
        return {"status": "success", "message": f"Saved to {path}", "done": 1, "total": 1}
    except Exception as exc:
        return {"status": "error", "message": str(exc), "done": 0, "total": 1}


@shared_task(bind=True, name="app.tasks.download_onedrive_folder")
def download_onedrive_folder_task(
    self,
    account_id: str,
    client_id: str,
    client_secret: str,
    authority: str,
    scopes: list[str],
    item_id: str,
    folder_name: str,
    dest_dir: str,
) -> dict:
    """Recursively download all files in a OneDrive folder to *dest_dir*."""
    try:
        token = _get_onedrive_token(account_id, client_id, client_secret, authority, scopes)

        # Enumerate all files first to report progress
        self.update_state(
            state="PROGRESS",
            meta={"status": "Enumerating files…", "done": 0, "total": 0},
        )
        all_files = onedrive_service.list_items_recursive(token, item_id)
        total = len(all_files)

        if total == 0:
            return {"status": "success", "message": "No files found in folder.", "done": 0, "total": 0}

        done = 0
        errors: list[str] = []
        token_lock = threading.Lock()
        token_holder = [token]  # mutable container for sharing across threads

        def _refresh_token() -> str:
            with token_lock:
                token_holder[0] = _get_onedrive_token(
                    account_id, client_id, client_secret, authority, scopes
                )
                return token_holder[0]

        def _download_one(file_item: dict) -> str:
            rel_path = file_item.get("relative_path", file_item["name"])
            file_dest_dir = os.path.join(dest_dir, folder_name, os.path.dirname(rel_path))
            with token_lock:
                current_token = token_holder[0]
            onedrive_service.download_file(
                current_token, file_item["id"], file_dest_dir, file_item["name"]
            )
            return file_item["name"]

        max_workers = int(os.environ.get("ONEDRIVE_DOWNLOAD_WORKERS", 4))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(_download_one, f): f for f in all_files
            }
            for future in as_completed(futures):
                file_item = futures[future]
                try:
                    future.result()
                except Exception as exc:
                    errors.append(f"{file_item['name']}: {exc}")
                done += 1
                # Refresh token periodically
                if done % 20 == 0:
                    _refresh_token()
                self.update_state(
                    state="PROGRESS",
                    meta={
                        "status": f"Downloaded {done}/{total}",
                        "done": done,
                        "total": total,
                    },
                )

        if errors:
            return {
                "status": "error",
                "message": f"Downloaded {done - len(errors)}/{total}. Errors: {'; '.join(errors[:5])}",
                "done": done - len(errors),
                "total": total,
            }

        return {
            "status": "success",
            "message": f"Downloaded {done} of {total} file(s) from '{folder_name}'.",
            "done": done,
            "total": total,
        }
    except Exception as exc:
        return {"status": "error", "message": str(exc), "done": 0, "total": 0}
