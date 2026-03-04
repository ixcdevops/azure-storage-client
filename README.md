# Storage Client

A self-hosted web UI for browsing and downloading files from **Azure Blob Storage** and **OneDrive Personal** to a local volume mount. Built with Flask, Celery, and Bootstrap 5. Designed to run as a Docker container — ideal for NAS devices (ZimaOS / CasaOS) or any Docker host.

---

## Features

| Feature | Details |
|---|---|
| **Azure Blob Storage** | Connect with account name + key (UI or env vars), browse containers and virtual folders |
| **OneDrive Personal** | Connect via OAuth 2.0 (Microsoft sign-in), browse and download files and folders |
| **One connection at a time** | Switching sources automatically clears the previous session |
| **Selective download** | Download a single file, a virtual folder, or an entire container/drive folder |
| **Configurable destination** | Set the target sub-folder inside the volume mount before downloading |
| **Background job queue** | Downloads run as Celery tasks; the UI never blocks |
| **Job history** | Track progress, state, and completion of all queued downloads |
| **On-demand details** | Folder count, file count, total size, file-type breakdown — fetched only on click |
| **Local file manager** | Browse, create, rename, and delete folders on the mounted volume |
| **Confirmation dialogs** | Bulk "Download All" actions require explicit confirmation |
| **Non-root container** | Runs as UID/GID `1000` to avoid permission issues on NAS volume mounts |

---

## Project Structure

```
storage_client/
├── app/
│   ├── __init__.py              # Flask app factory + Celery initialisation
│   ├── config.py                # Configuration from environment variables
│   ├── tasks.py                 # Celery tasks (Azure blob + OneDrive file/folder downloads)
│   ├── routes/
│   │   ├── storage.py           # Azure connection, container/blob browsing, download
│   │   ├── onedrive.py          # OneDrive OAuth flow, browsing, download
│   │   ├── files.py             # Local volume file manager (browse, create, rename, delete)
│   │   └── jobs.py              # Job history page
│   ├── services/
│   │   ├── blob_service.py      # Azure Blob SDK wrapper (list, download, get_details)
│   │   ├── onedrive_service.py  # Microsoft Graph API wrapper + MSAL token cache
│   │   ├── file_service.py      # Local filesystem CRUD with path-traversal guard
│   │   └── job_registry.py      # Redis-backed job metadata store (48 h TTL, max 200 jobs)
│   ├── templates/
│   │   ├── base.html            # Bootstrap 5 layout + navbar
│   │   ├── connect.html         # Azure login form + OneDrive sign-in button
│   │   ├── containers.html      # Azure container listing
│   │   ├── blobs.html           # Azure blob browser with virtual folder navigation
│   │   ├── onedrive_browse.html # OneDrive file/folder browser
│   │   ├── files.html           # Local file manager
│   │   ├── jobs.html            # Job history table with auto-refresh
│   │   └── partials/
│   │       └── alerts.html      # Flash message partial
│   └── static/
│       └── style.css
├── DATA/                        # Default local volume mount (host path)
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── run.py                       # Entrypoint — exposes celery_app for the worker CLI
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
| `redis` | Redis 7 Alpine | Task broker, result backend, MSAL token cache |

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

### Core

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | `dev-secret-change-me` | Flask session secret — **change in production** |
| `DOWNLOAD_PATH` | `/data` | Absolute path inside the container where files are written |
| `CELERY_BROKER_URL` | `redis://redis:6379/0` | Redis URL for the Celery broker |
| `CELERY_RESULT_BACKEND` | `redis://redis:6379/0` | Redis URL for storing task results |

### Azure Blob Storage *(optional)*

| Variable | Default | Description |
|---|---|---|
| `AZURE_STORAGE_ACCOUNT_NAME` | _(empty)_ | Pre-configure the storage account name |
| `AZURE_STORAGE_ACCOUNT_KEY` | _(empty)_ | Pre-configure the account key |

If both are set the app connects automatically on startup and skips the login screen.

### OneDrive Personal *(optional)*

| Variable | Default | Description |
|---|---|---|
| `ONEDRIVE_CLIENT_ID` | _(empty)_ | App registration client ID |
| `ONEDRIVE_CLIENT_SECRET` | _(empty)_ | App registration client secret |
| `ONEDRIVE_REDIRECT_URI` | `http://localhost:8080/onedrive/callback` | Must match the URI registered in Azure |

If `ONEDRIVE_CLIENT_ID` is not set the OneDrive sign-in button is shown but disabled on the connect page.

---

## OneDrive Setup

OneDrive requires a one-time app registration in Microsoft Entra ID.

1. Go to [portal.azure.com → App Registrations](https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade) → **New registration**
2. Name: anything (e.g. `Storage Client`)
3. Supported account types: **Personal Microsoft accounts only**
4. Redirect URI: **Web** → `http://localhost:8080/onedrive/callback`
   - For a NAS deployment replace `localhost:8080` with your NAS IP/hostname
5. After creation, copy the **Application (client) ID** → set as `ONEDRIVE_CLIENT_ID`
6. Go to **Certificates & secrets** → **New client secret** → copy the **Value** → set as `ONEDRIVE_CLIENT_SECRET`
7. Go to **API permissions** → **Add a permission** → **Microsoft Graph** → **Delegated** → add:
   - `Files.Read.All`
   - `User.Read`
8. Add both values to your `.env` and restart: `docker compose up -d`

> **Token storage:** MSAL tokens are cached in Redis (keyed by account ID, 90-day TTL) and shared between the web process and Celery workers. Tokens are refreshed automatically — you won't be asked to log in again unless the refresh token expires (~90 days of inactivity).

---

## Usage

### Connecting

Navigate to **http://localhost:8080**. The Connect page offers two options:

**Azure Blob Storage**
1. Enter your **Storage Account Name** and **Account Key**
2. Click **Connect to Azure**

**OneDrive Personal**
1. Click **Sign in with Microsoft**
2. Complete the Microsoft login flow in your browser
3. You are redirected back to the OneDrive file browser

The navbar shows the connected account name and a **Disconnect** button. Connecting to one source automatically disconnects the other.

> **Troubleshooting OAuth:** If you see "Invalid OAuth state", clear your browser cookies for the app (Browser dev tools → Application → Cookies → Clear) or click **Clear Session** in the navbar and try again.

### Browsing & downloading (Azure)

1. The **Storage** nav link opens the container listing.
2. Click a container to enter the blob browser; navigate virtual folders via breadcrumb.
3. Set the **download destination** using the bar at the top — a sub-folder path relative to `/data`. Leave blank for the root.
4. Download options:
   - **Single file** — `↓` button on a file row, queued immediately.
   - **Folder** — `↓` button on a folder row, queues a recursive download.
   - **Download All** / **Download All in View** — queues everything at or below the current level (confirmation dialog shown first).

### Browsing & downloading (OneDrive)

1. The **OneDrive** nav link opens the file browser at your drive root.
2. Click folders to drill in; use the breadcrumb to navigate back up.
3. Set the **download destination** using the same bar as Azure.
4. Download options work identically — single file, folder, or all in view.

### On-demand details

Click the **ⓘ** button on any container, folder, or OneDrive folder row to open a details panel showing:
- Number of sub-folders
- Total file count
- Total size (human-readable)
- Per-extension file-type breakdown

Details are fetched live via AJAX only when clicked — nothing is pre-loaded.

### Job history

- Click **Jobs** in the navbar to see all queued and completed download tasks.
- The page auto-refreshes every 4 seconds while any job is running.
- Each job shows state (`PENDING`, `PROGRESS`, `SUCCESS`, `FAILURE`), a progress bar, file count, and timestamps.

### Local file manager

- Click **Local Files** in the navbar to browse the volume mount.
- Supports: **create folder**, **rename**, and **delete**.
- All operations are scoped to `DOWNLOAD_PATH`; path-traversal attempts are rejected.

---

## Deployment on ZimaOS / CasaOS (NAS)

1. SSH into your ZimaOS device.
2. Copy the project to a permanent location, e.g. `/DATA/AppData/storage-client`.
3. Edit `docker-compose.yml` — use an absolute host path for the volume:
   ```yaml
   volumes:
     - /DATA/AppData/storage-client/DATA:/data
   ```
4. Check the owner UID/GID of your target directory and update both services if needed:
   ```bash
   stat -c "%u:%g" /DATA/AppData/storage-client/DATA
   # Then in docker-compose.yml:
   user: "1000:1000"   # replace with actual values
   ```
5. Ensure the directory is group-writable:
   ```bash
   chmod g+w /DATA/AppData/storage-client/DATA
   ```
6. For OneDrive, register the redirect URI as `http://<nas-ip>:8080/onedrive/callback` in your app registration.
7. Create a `.env` file with at least a strong `SECRET_KEY`.
8. Start:
   ```bash
   cd /DATA/AppData/storage-client
   docker compose up -d
   ```
9. Access the UI at `http://<nas-ip>:8080`.

---

## Architecture

```
Browser
  │
  ▼
Flask (Gunicorn)                    ← port 5000 (mapped to 8080 on host)
  │       │
  │       ├─ /api/details           ──► blob_service.get_details()     (Azure SDK)
  │       └─ /onedrive/api/details  ──► onedrive_service.get_details() (Graph API)
  │
  ├─ POST /download          ──► Celery task (Azure)     ─┐
  └─ POST /onedrive/download ──► Celery task (OneDrive)  ─┤
                                                           ▼
                                              Redis (broker + results
                                                     + MSAL token cache)
                                                           │
                                              celery-worker (×4 concurrent)
                                                           │
                                         azure-storage-blob SDK / Graph API
                                                           │
                                                    /data  (shared volume)
                                                           │
                                              Host filesystem (./DATA)
```

- Azure credentials (account name + key) are stored in the **Flask session** (signed cookie, never written to disk).
- OneDrive **MSAL token cache** is stored in Redis, keyed by account ID with a 90-day TTL. Both the web process and Celery workers share the same cache, enabling automatic silent token refresh.
- Job metadata is stored in Redis with a 48-hour TTL, capped at 200 entries.

---

## Dependencies

| Package | Version | Purpose |
|---|---|---|
| `flask` | ≥3.0 | Web framework |
| `azure-storage-blob` | ≥12.20 | Azure Blob Storage SDK |
| `msal` | ≥1.28 | Microsoft Authentication Library (OneDrive OAuth) |
| `requests` | ≥2.31 | HTTP client for Microsoft Graph API calls |
| `celery[redis]` | ≥5.3 | Async task queue |
| `redis` | ≥5.0 | Redis Python client |
| `gunicorn` | ≥22.0 | Production WSGI server |
| `python-dotenv` | ≥1.0 | `.env` file loader |

---

## Security Notes

- **Change `SECRET_KEY`** to a long random string before exposing the app on a network.
- **Azure account keys** grant full access to the storage account. For read-only use, prefer a SAS token or a service principal with `Storage Blob Data Reader` role.
- **OneDrive client secret** should be treated as a password — keep it out of source control. The `.env` file is in `.gitignore`.
- **MSAL refresh tokens** are long-lived (~90 days). They are stored in Redis — ensure Redis is not exposed outside the Docker network.
- **Path traversal** — the local file manager and download destination resolver reject any path that resolves outside `DOWNLOAD_PATH`.
- **No built-in auth** — there is no login for the app itself. If exposed beyond localhost, place it behind a reverse proxy with authentication (e.g. Nginx + basic auth, Authelia, or Cloudflare Access).
- **HTTPS** — when running over HTTPS, set `SESSION_COOKIE_SECURE=true` in your environment and update the OneDrive redirect URI to use `https://`.
