"""Wrapper around the Azure Blob Storage SDK."""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass
from typing import Generator

from azure.storage.blob import BlobServiceClient, ContainerClient


# ── Data classes ────────────────────────────────────────────────────────────

@dataclass
class ContainerInfo:
    name: str
    last_modified: str


@dataclass
class BlobItem:
    name: str
    display_name: str
    size: int
    last_modified: str
    is_prefix: bool  # virtual folder


# ── Public helpers ──────────────────────────────────────────────────────────

def get_client(account_name: str, account_key: str) -> BlobServiceClient:
    """Return a BlobServiceClient authenticated with the given account key."""
    account_url = f"https://{account_name}.blob.core.windows.net"
    return BlobServiceClient(account_url=account_url, credential=account_key)


def list_containers(client: BlobServiceClient) -> list[ContainerInfo]:
    """List all containers in the storage account."""
    containers: list[ContainerInfo] = []
    for c in client.list_containers():
        containers.append(
            ContainerInfo(
                name=c["name"],
                last_modified=str(c.get("last_modified", "")),
            )
        )
    return containers


def list_blobs(
    client: BlobServiceClient,
    container_name: str,
    prefix: str = "",
) -> list[BlobItem]:
    """List blobs and virtual folders inside *container_name* under *prefix*.

    Uses the ``/`` delimiter so that nested blobs appear as virtual folders.
    """
    container: ContainerClient = client.get_container_client(container_name)
    items: list[BlobItem] = []

    for item in container.walk_blobs(name_starts_with=prefix, delimiter="/"):
        # Virtual directory (prefix)
        if hasattr(item, "prefix"):
            display = item.prefix[len(prefix) :].rstrip("/")
            items.append(
                BlobItem(
                    name=item.prefix,
                    display_name=display,
                    size=0,
                    last_modified="",
                    is_prefix=True,
                )
            )
        else:
            display = item.name[len(prefix) :]
            items.append(
                BlobItem(
                    name=item.name,
                    display_name=display,
                    size=item.size or 0,
                    last_modified=str(item.last_modified or ""),
                    is_prefix=False,
                )
            )

    # Sort: folders first, then alphabetically
    items.sort(key=lambda b: (not b.is_prefix, b.display_name.lower()))
    return items


def get_details(
    client: BlobServiceClient,
    container_name: str,
    prefix: str = "",
) -> dict:
    """Return aggregate stats for blobs in *container_name* under *prefix*.

    Only called on-demand (via AJAX) to avoid unnecessary storage operations.
    """
    container = client.get_container_client(container_name)

    total_files = 0
    total_size = 0
    folders: set[str] = set()
    ext_counter: Counter[str] = Counter()

    for blob in container.list_blobs(name_starts_with=prefix):
        total_files += 1
        total_size += blob.size or 0

        # Track unique virtual folders
        # Strip the current prefix so we only count sub-folders
        rel_name = blob.name[len(prefix):]
        parts = rel_name.rsplit("/", 1)
        if len(parts) == 2 and parts[0]:
            folder_parts = parts[0].split("/")
            for i in range(len(folder_parts)):
                folders.add("/".join(folder_parts[: i + 1]))

        # File extension
        base_name = blob.name.rsplit("/", 1)[-1]
        _, _, ext = base_name.rpartition(".")
        ext_key = f".{ext.lower()}" if ext else "(no extension)"
        ext_counter[ext_key] += 1

    # Human-readable size
    if total_size >= 1 << 30:
        size_str = f"{total_size / (1 << 30):.2f} GB"
    elif total_size >= 1 << 20:
        size_str = f"{total_size / (1 << 20):.2f} MB"
    elif total_size >= 1 << 10:
        size_str = f"{total_size / (1 << 10):.2f} KB"
    else:
        size_str = f"{total_size} B"

    # Top extensions sorted by count desc
    file_types = [
        {"ext": ext, "count": count}
        for ext, count in ext_counter.most_common(25)
    ]

    return {
        "container": container_name,
        "prefix": prefix,
        "folder_count": len(folders),
        "file_count": total_files,
        "total_size": total_size,
        "total_size_display": size_str,
        "file_types": file_types,
    }


def download_blob(
    client: BlobServiceClient,
    container_name: str,
    blob_name: str,
    dest_dir: str,
) -> str:
    """Download a single blob to *dest_dir*, preserving its path structure.

    Returns the absolute path of the downloaded file.
    """
    blob_client = client.get_container_client(container_name).get_blob_client(blob_name)
    dest_path = os.path.join(dest_dir, blob_name)
    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

    with open(dest_path, "wb") as f:
        stream = blob_client.download_blob()
        stream.readinto(f)

    return dest_path


def download_prefix(
    client: BlobServiceClient,
    container_name: str,
    prefix: str,
    dest_dir: str,
) -> Generator[str, None, None]:
    """Download all blobs under *prefix* (recursive).  Yields each file path."""
    container = client.get_container_client(container_name)
    for blob in container.list_blobs(name_starts_with=prefix):
        dest_path = os.path.join(dest_dir, blob.name)
        os.makedirs(os.path.dirname(dest_path), exist_ok=True)
        blob_client = container.get_blob_client(blob.name)
        with open(dest_path, "wb") as f:
            stream = blob_client.download_blob()
            stream.readinto(f)
        yield dest_path
