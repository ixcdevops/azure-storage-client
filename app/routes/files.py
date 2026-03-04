"""Routes for managing files and folders on the local volume mount."""

from __future__ import annotations

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from ..services import file_service
from ..services.file_service import PathTraversalError

files_bp = Blueprint("files", __name__, url_prefix="/files")


# ── Helpers ─────────────────────────────────────────────────────────────────

def _base() -> str:
    return current_app.config["DOWNLOAD_PATH"]


def _breadcrumbs(rel_path: str) -> list[dict]:
    crumbs: list[dict] = []
    if rel_path:
        parts = rel_path.strip("/").split("/")
        for i, part in enumerate(parts):
            crumbs.append(
                {
                    "name": part,
                    "path": "/".join(parts[: i + 1]),
                }
            )
    return crumbs


# ── Routes ──────────────────────────────────────────────────────────────────

@files_bp.route("/")
def browse():
    rel_path = request.args.get("path", "")
    try:
        entries = file_service.list_dir(_base(), rel_path)
    except (FileNotFoundError, PathTraversalError) as exc:
        flash(str(exc), "danger")
        entries = []
        rel_path = ""

    return render_template(
        "files.html",
        entries=entries,
        rel_path=rel_path,
        breadcrumbs=_breadcrumbs(rel_path),
    )


@files_bp.route("/create-folder", methods=["POST"])
def create_folder():
    rel_path = request.form.get("rel_path", "")
    folder_name = request.form.get("folder_name", "").strip()

    if not folder_name:
        flash("Folder name is required.", "danger")
    else:
        try:
            file_service.create_folder(_base(), rel_path, folder_name)
            flash(f"Folder '{folder_name}' created.", "success")
        except (PathTraversalError, OSError) as exc:
            flash(f"Error creating folder: {exc}", "danger")

    return redirect(url_for("files.browse", path=rel_path))


@files_bp.route("/rename", methods=["POST"])
def rename():
    rel_path = request.form.get("rel_path", "")
    old_name = request.form.get("old_name", "").strip()
    new_name = request.form.get("new_name", "").strip()

    if not old_name or not new_name:
        flash("Both old and new names are required.", "danger")
    else:
        try:
            file_service.rename(_base(), rel_path, old_name, new_name)
            flash(f"Renamed '{old_name}' → '{new_name}'.", "success")
        except (PathTraversalError, FileNotFoundError, OSError) as exc:
            flash(f"Error renaming: {exc}", "danger")

    return redirect(url_for("files.browse", path=rel_path))


@files_bp.route("/delete", methods=["POST"])
def delete():
    rel_path = request.form.get("rel_path", "")
    name = request.form.get("name", "").strip()

    if not name:
        flash("Name is required.", "danger")
    else:
        try:
            file_service.delete(_base(), rel_path, name)
            flash(f"Deleted '{name}'.", "success")
        except (PathTraversalError, FileNotFoundError, OSError) as exc:
            flash(f"Error deleting: {exc}", "danger")

    return redirect(url_for("files.browse", path=rel_path))
