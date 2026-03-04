import os


class Config:
    """Application configuration sourced from environment variables."""

    SECRET_KEY: str = os.environ.get("SECRET_KEY", "dev-secret-change-me")

    # Default download path inside the container (volume mount target)
    DOWNLOAD_PATH: str = os.environ.get("DOWNLOAD_PATH", "/data")

    # Optional pre-configured Azure credentials (can also be entered via UI)
    AZURE_STORAGE_ACCOUNT_NAME: str = os.environ.get("AZURE_STORAGE_ACCOUNT_NAME", "")
    AZURE_STORAGE_ACCOUNT_KEY: str = os.environ.get("AZURE_STORAGE_ACCOUNT_KEY", "")

    # Celery / Redis
    CELERY_BROKER_URL: str = os.environ.get("CELERY_BROKER_URL", "redis://redis:6379/0")
    CELERY_RESULT_BACKEND: str = os.environ.get("CELERY_RESULT_BACKEND", "redis://redis:6379/0")

    # OneDrive / Microsoft Graph OAuth
    ONEDRIVE_CLIENT_ID: str = os.environ.get("ONEDRIVE_CLIENT_ID", "")
    ONEDRIVE_CLIENT_SECRET: str = os.environ.get("ONEDRIVE_CLIENT_SECRET", "")
    ONEDRIVE_REDIRECT_URI: str = os.environ.get(
        "ONEDRIVE_REDIRECT_URI", "http://localhost:8080/onedrive/callback"
    )
    # Use 'consumers' for personal Microsoft accounts only
    ONEDRIVE_AUTHORITY: str = os.environ.get(
        "ONEDRIVE_AUTHORITY", "https://login.microsoftonline.com/consumers"
    )
    # Do NOT include reserved scopes (offline_access, openid, profile) – MSAL adds them automatically
    ONEDRIVE_SCOPES: list[str] = ["Files.Read.All", "User.Read"]

    # Ensure session cookie is sent on OAuth top-level redirects
    SESSION_COOKIE_SAMESITE: str = "Lax"
    SESSION_COOKIE_SECURE: bool = os.environ.get("SESSION_COOKIE_SECURE", "false").lower() == "true"
