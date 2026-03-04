"""Routes for connecting to Azure Blob Storage and browsing / downloading blobs."""

from __future__ import annotations

import os

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from ..services import blob_service
from ..services.job_registry import register_job
from ..tasks import download_blob_task, download_prefix_task

storage_bp = Blueprint("storage", __name__)


# ── Helpers ─────────────────────────────────────────────────────────────────

def _get_client():
    """Build a BlobServiceClient from the current session credentials."""
    name = session.get("account_name")
    key = session.get("account_key")
    if not name or not key:
        return None
    return blob_service.get_client(name, key)


def _local_folders(base: str, max_depth: int = 2) -> list[str]:
    """Return relative paths of all sub-directories under *base* up to *max_depth*."""
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
    """Resolve and validate *subfolder* is within *base*.  Returns absolute path."""
    resolved = os.path.realpath(os.path.join(base, subfolder))
    base_resolved = os.path.realpath(base)
    if not (resolved == base_resolved or resolved.startswith(base_resolved + os.sep)):
        raise ValueError(f"Destination path escapes the allowed directory: {subfolder}")
    return resolved


# ── Routes ──────────────────────────────────────────────────────────────────

@storage_bp.route("/")
def index():
    if session.get("account_name"):
        return redirect(url_for("storage.containers"))
    return redirect(url_for("storage.connect"))


@storage_bp.route("/connect", methods=["GET", "POST"])
def connect():
    # Pre-fill from env vars if available
    env_name = current_app.config.get("AZURE_STORAGE_ACCOUNT_NAME", "")
    env_key = current_app.config.get("AZURE_STORAGE_ACCOUNT_KEY", "")

    if request.method == "POST":
        account_name = request.form.get("account_name", "").strip()
        account_key = request.form.get("account_key", "").strip()

        if not account_name or not account_key:
            flash("Both account name and account key are required.", "danger")
            return render_template(
                "connect.html", account_name=account_name, account_key=account_key
            )

        # Validate credentials
        try:
            client = blob_service.get_client(account_name, account_key)
            blob_service.list_containers(client)  # will raise on bad creds
        except Exception as exc:
            flash(f"Connection failed: {exc}", "danger")
            return render_template(
                "connect.html", account_name=account_name, account_key=""
            )

        session["account_name"] = account_name
        session["account_key"] = account_key
        flash(f"Connected to {account_name}.", "success")
        return redirect(url_for("storage.containers"))

    return render_template("connect.html", account_name=env_name, account_key=env_key)


@storage_bp.route("/disconnect", methods=["POST"])
def disconnect():
    session.pop("account_name", None)
    session.pop("account_key", None)
    flash("Disconnected.", "info")
    return redirect(url_for("storage.connect"))


@storage_bp.route("/containers")
def containers():
    client = _get_client()
    if client is None:
        flash("Please connect to a storage account first.", "warning")
        return redirect(url_for("storage.connect"))

    try:
        items = blob_service.list_containers(client)
    except Exception as exc:
        flash(f"Error listing containers: {exc}", "danger")
        items = []

    base = current_app.config["DOWNLOAD_PATH"]
    dest_subfolder = request.args.get("dest", session.get("dest_subfolder", ""))
    session["dest_subfolder"] = dest_subfolder

    return render_template(
        "containers.html",
        containers=items,
        local_folders=_local_folders(base),
        dest_subfolder=dest_subfolder,
    )


@storage_bp.route("/containers/<container_name>")
def browse_blobs(container_name: str):
    client = _get_client()
    if client is None:
        flash("Please connect to a storage account first.", "warning")
        return redirect(url_for("storage.connect"))

    prefix = request.args.get("prefix", "")

    try:
        items = blob_service.list_blobs(client, container_name, prefix)
    except Exception as exc:
        flash(f"Error listing blobs: {exc}", "danger")
        items = []

    # Build breadcrumb parts from prefix
    breadcrumbs = []
    if prefix:
        parts = prefix.rstrip("/").split("/")
        for i, part in enumerate(parts):
            breadcrumbs.append(
                {
                    "name": part,
                    "prefix": "/".join(parts[: i + 1]) + "/",
                }
            )

    base = current_app.config["DOWNLOAD_PATH"]
    dest_subfolder = request.args.get("dest", session.get("dest_subfolder", ""))
    session["dest_subfolder"] = dest_subfolder

    return render_template(
        "blobs.html",
        container_name=container_name,
        prefix=prefix,
        blobs=items,
        breadcrumbs=breadcrumbs,
        local_folders=_local_folders(base),
        dest_subfolder=dest_subfolder,
    )


@storage_bp.route("/set-dest", methods=["POST"])
def set_dest():
    """Persist the chosen download destination in the session, then redirect back."""
    dest_subfolder = request.form.get("dest_subfolder", "").strip()
    base = current_app.config["DOWNLOAD_PATH"]
    try:
        _resolve_dest(base, dest_subfolder)  # validate before storing
        session["dest_subfolder"] = dest_subfolder
        display = dest_subfolder or "/"
        flash(f"Download destination set to: {display}", "info")
    except ValueError as exc:
        flash(str(exc), "danger")

    # Go back to wherever the user came from
    return redirect(request.form.get("next") or url_for("storage.containers"))


@storage_bp.route("/download", methods=["POST"])
def download():
    if not session.get("account_name"):
        flash("Please connect to a storage account first.", "warning")
        return redirect(url_for("storage.connect"))

    account_name: str = session["account_name"]
    account_key: str = session["account_key"]
    container_name = request.form.get("container_name", "")
    blob_name = request.form.get("blob_name", "")
    prefix = request.form.get("prefix", "")
    download_type = request.form.get("download_type", "blob")  # blob | prefix

    # Destination comes exclusively from the session (set via /set-dest)
    dest_subfolder = session.get("dest_subfolder", "")

    base = current_app.config["DOWNLOAD_PATH"]
    try:
        dest_dir = _resolve_dest(base, dest_subfolder)
        os.makedirs(dest_dir, exist_ok=True)
    except ValueError as exc:
        flash(str(exc), "danger")
        return redirect(
            url_for("storage.browse_blobs", container_name=container_name, prefix=prefix)
        )

    redis_url = current_app.config["CELERY_BROKER_URL"]

    if download_type == "prefix":
        target_prefix = prefix
        task = download_prefix_task.delay(
            account_name, account_key, container_name, target_prefix, dest_dir
        )
        label = f"{target_prefix or '(all)'}"  # display name
        register_job(redis_url, task.id, {
            "download_type": "prefix",
            "container_name": container_name,
            "label": label,
            "dest": dest_subfolder or "/",
        })
        flash(
            f"Queued folder download: '{container_name}/{target_prefix}' → {dest_subfolder or '/'}. "
            f'<a href="{url_for("jobs.index")}">View jobs →</a>',
            "info",
        )
    else:
        task = download_blob_task.delay(
            account_name, account_key, container_name, blob_name, dest_dir
        )
        register_job(redis_url, task.id, {
            "download_type": "blob",
            "container_name": container_name,
            "label": blob_name,
            "dest": dest_subfolder or "/",
        })
        flash(
            f"Queued: '{blob_name}' → {dest_subfolder or '/'}. "
            f'<a href="{url_for("jobs.index")}">View jobs →</a>',
            "info",
        )

    return redirect(
        url_for("storage.browse_blobs", container_name=container_name, prefix=prefix)
    )
