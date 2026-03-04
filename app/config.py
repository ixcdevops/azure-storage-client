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
