# Azure Storage Client

A self-hosted web UI for browsing and downloading Azure Blob Storage data to a local volume mount. Built with Flask, Celery, and Bootstrap 5. Designed to run as a Docker container — ideal for NAS devices (ZimaOS / CasaOS) or any Docker host.

---

## Features

| Feature | Details |
|---|---|
| **Connect via UI or env vars** | Enter account name + key in the browser, or pre-configure via environment variables |
| **Browse containers & blobs** | Navigate virtual folders with breadcrumb navigation |
| **Selective download** | Download a single file, a virtual folder, or an entire container |
| **Configurable destination** | Set the target sub-folder inside the volume mount before downloading |
| **Background job queue** | Downloads run as Celery tasks; the UI never blocks |
| **Job history** | Track progress, state, and completion of all queued downloads |
| **On-demand details** | View folder count, file count, total size, and file-type breakdown — fetched only when requested |
| **Local file manager** | Browse, create, rename, and delete folders on the mounted volume |
| **Confirmation dialogs** | Bulk "Download All" actions require explicit confirmation |

---

## Project Structure

```
storage_client/
├── app/
│   ├── __init__.py          # Flask app factory + Celery initialisation
│   ├── config.py            # Configuration from environment variables
│   ├── tasks.py             # Celery background tasks (download_blob_task, download_prefix_task)
│   ├── routes/
│   │   ├── storage.py       # Azure connection, container/blob browsing, download, /api/details
│   │   ├── files.py         # Local volume file manager (browse, create, rename, delete)
│   │   └── jobs.py          # Job history page
│   ├── services/
│   │   ├── blob_service.py  # Azure Blob SDK wrapper (list, download, get_details)
│   │   ├── file_service.py  # Local filesystem CRUD with path-traversal guard
│   │   └── job_registry.py  # Redis-backed job metadata store (48 h TTL, max 200 jobs)
│   ├── templates/
│   │   ├── base.html        # Bootstrap 5 layout + navbar
│   │   ├── connect.html     # Login form (account name + key)
│   │   ├── containers.html  # Container listing
│   │   ├── blobs.html       # Blob browser with virtual folder navigation
│   │   ├── files.html       # Local file manager
│   │   ├── jobs.html        # Job history table with auto-refresh
│   │   └── partials/
│   │       └── alerts.html  # Flash message partial
│   └── static/
│       └── style.css
├── DATA/                    # Default local volume mount (host path)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── run.py                   # Entrypoint — exposes celery_app for the worker CLI
├── .env.example
└── .gitignore
```

---

## Quick Start

### Option 1 — Docker Compose (recommended)

```bash
# 1. Clone / copy the project
cd storage_client

# 2. Configure environment (optional — credentials can also be entered in the UI)
cp .env.example .env
# Edit .env and set SECRET_KEY, and optionally AZURE_STORAGE_ACCOUNT_NAME / KEY

# 3. Start all services
docker compose up -d

# 4. Open the UI
http://localhost:8080
```

Three services start:

| Service | Container | Role |
|---|---|---|
| `storage-client` | Flask + Gunicorn (port 8080) | Web UI |
| `celery-worker` | Celery (concurrency 4) | Background downloads |
| `redis` | Redis 7 Alpine | Task broker + result backend |

Downloads are written to `./DATA` on the host (mapped to `/data` inside both the web and worker containers).

### Option 2 — Local development

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux / macOS

pip install -r requirements.txt
cp .env.example .env            # edit as needed

# You need a running Redis instance:
# docker run -d -p 6379:6379 redis:7-alpine

# Terminal 1 — Flask dev server
python run.py

# Terminal 2 — Celery worker
celery -A run.celery_app worker --loglevel=info
```

---

## Configuration

All settings are read from environment variables (or a `.env` file when running locally).

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | `dev-secret-change-me` | Flask session secret — **change in production** |
| `DOWNLOAD_PATH` | `/data` | Absolute path inside the container where files are written |
| `AZURE_STORAGE_ACCOUNT_NAME` | _(empty)_ | Pre-configure the storage account name (optional) |
| `AZURE_STORAGE_ACCOUNT_KEY` | _(empty)_ | Pre-configure the account key (optional) |
| `CELERY_BROKER_URL` | `redis://redis:6379/0` | Redis URL for the Celery broker |
| `CELERY_RESULT_BACKEND` | `redis://redis:6379/0` | Redis URL for storing task results |

If `AZURE_STORAGE_ACCOUNT_NAME` and `AZURE_STORAGE_ACCOUNT_KEY` are both set, the app connects automatically on startup and skips the login screen.

---

## Usage

### Connecting

1. Navigate to **http://localhost:8080**.
2. If credentials are not pre-configured, you are redirected to the **Connect** page.
3. Enter your Azure Storage **Account Name** and **Account Key**, then click **Connect**.
4. Click **Disconnect** in the navbar to clear the session.

### Browsing & downloading

1. The **Containers** page lists all containers in the storage account.
2. Click a container name to enter the **Blob Browser**.
3. Use the breadcrumb to navigate virtual folders.
4. **Set the download destination** using the destination bar at the top of either page. The destination is a path relative to the volume mount root (`/data`). Leave blank to download to the root.
5. Use the download buttons:
   - **Single file** — downloads one blob immediately as a background task.
   - **Folder download** (folder row) — queues a recursive download of that virtual folder.
   - **Download All in View** / **Download All** (container row) — queues a recursive download of everything at the current level and below.
6. Bulk download buttons show a **confirmation dialog** before queuing.

### Job history

- Click **Jobs** in the navbar to see all queued and completed download tasks.
- The page auto-refreshes every 4 seconds while any job is in a running state.
- Each job shows its state (`PENDING`, `PROGRESS`, `SUCCESS`, `FAILURE`), a progress bar, byte count, and timestamps.

### On-demand details

- Click the **ⓘ** (info-circle) button on any container row or folder row to fetch live statistics:
  - Number of virtual sub-folders
  - Total file count
  - Total size (human-readable)
  - Per-extension file-type breakdown
- Details are fetched on demand via `GET /api/details/<container>?prefix=<prefix>` — nothing is pre-loaded, keeping the UI fast.

### Local file manager

- Click **Local Files** in the navbar to browse the volume mount.
- Supports: **create folder**, **rename**, and **delete** (files and folders).
- All operations are scoped to the configured `DOWNLOAD_PATH`; path-traversal attempts are rejected.

---

## Deployment on ZimaOS / CasaOS (NAS)

1. SSH into your ZimaOS device.
2. Copy the project to a permanent location, e.g. `/DATA/AppData/storage-client`.
3. Edit `docker-compose.yml` — replace the relative volume path with an absolute path:
   ```yaml
   volumes:
     - /DATA/AppData/storage-client/DATA:/data
   ```
4. Create a `.env` file with at least a strong `SECRET_KEY`.
5. Run:
   ```bash
   cd /DATA/AppData/storage-client
   docker compose up -d
   ```
6. Access the UI at `http://<nas-ip>:8080`.

---

## Architecture

```
Browser
  │
  ▼
Flask (Gunicorn)          ← port 5000 (mapped to 8080 on host)
  │       │
  │       └─ /api/details  ──► blob_service.get_details()  (Azure SDK, live scan)
  │
  └─ POST /download  ──► Celery task queue  ──► Redis broker
                                │
                         celery-worker (×4 concurrent)
                                │
                         azure-storage-blob SDK
                                │
                         /data  (shared Docker volume)
                                │
                         Host filesystem (./DATA)
```

Session credentials (account name + key) are stored in the **server-side Flask session** (signed cookie). They are never written to disk.

Job metadata is stored in Redis with a 48-hour TTL, capped at 200 entries.

---

## Dependencies

| Package | Version | Purpose |
|---|---|---|
| `flask` | ≥3.0 | Web framework |
| `azure-storage-blob` | ≥12.20 | Azure Blob Storage SDK |
| `celery[redis]` | ≥5.3 | Async task queue |
| `redis` | ≥5.0 | Redis Python client |
| `gunicorn` | ≥22.0 | Production WSGI server |
| `python-dotenv` | ≥1.0 | `.env` file loader |

---

## Security notes

- Change `SECRET_KEY` to a long random string before exposing the app on a network.
- Account keys grant full access to the storage account. Use a read-only SAS token or a separate service principal with minimal permissions for read-only workloads.
- The local file manager rejects any path that resolves outside `DOWNLOAD_PATH` to prevent traversal attacks.
- There is no built-in user authentication. If the app is exposed beyond localhost, place it behind a reverse proxy with authentication (e.g. Nginx + basic auth, Authelia, or Cloudflare Access).
