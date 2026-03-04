"""Celery background tasks for Azure Blob downloads."""

from __future__ import annotations

from celery import shared_task

from .services import blob_service


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
