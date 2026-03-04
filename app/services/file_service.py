"""Local filesystem CRUD operations scoped to a base directory."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class FileEntry:
    name: str
    is_dir: bool
    size: int
    modified: str


class PathTraversalError(Exception):
    """Raised when a resolved path escapes the allowed base directory."""


# ── Internal helpers ────────────────────────────────────────────────────────

def _safe_resolve(base: str, *parts: str) -> str:
    """Join *parts* to *base* and verify the result is under *base*.

    Raises ``PathTraversalError`` if the resolved path escapes the base.
    """
    resolved = os.path.realpath(os.path.join(base, *parts))
    base_resolved = os.path.realpath(base)
    if not resolved.startswith(base_resolved + os.sep) and resolved != base_resolved:
        raise PathTraversalError(f"Access denied: {resolved}")
    return resolved


# ── Public API ──────────────────────────────────────────────────────────────

def list_dir(base: str, rel_path: str = "") -> list[FileEntry]:
    """Return entries in *base*/*rel_path*, sorted folders-first alphabetically."""
    target = _safe_resolve(base, rel_path)
    if not os.path.isdir(target):
        raise FileNotFoundError(f"Directory not found: {rel_path}")

    entries: list[FileEntry] = []
    for name in os.listdir(target):
        full = os.path.join(target, name)
        stat = os.stat(full)
        entries.append(
            FileEntry(
                name=name,
                is_dir=os.path.isdir(full),
                size=stat.st_size if not os.path.isdir(full) else 0,
                modified=datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).strftime("%Y-%m-%d %H:%M:%S"),
            )
        )

    entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
    return entries


def create_folder(base: str, rel_path: str, folder_name: str) -> str:
    """Create a new folder and return its absolute path."""
    target = _safe_resolve(base, rel_path, folder_name)
    os.makedirs(target, exist_ok=True)
    return target


def rename(base: str, rel_path: str, old_name: str, new_name: str) -> str:
    """Rename a file or folder.  Returns the new absolute path."""
    src = _safe_resolve(base, rel_path, old_name)
    dst = _safe_resolve(base, rel_path, new_name)
    if not os.path.exists(src):
        raise FileNotFoundError(f"Not found: {old_name}")
    os.rename(src, dst)
    return dst


def delete(base: str, rel_path: str, name: str) -> None:
    """Delete a file or folder (recursively)."""
    target = _safe_resolve(base, rel_path, name)
    if not os.path.exists(target):
        raise FileNotFoundError(f"Not found: {name}")
    if os.path.isdir(target):
        shutil.rmtree(target)
    else:
        os.remove(target)
