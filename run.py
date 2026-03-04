"""Entrypoint – run the Flask application."""

from dotenv import load_dotenv

load_dotenv()  # read .env if present

from app import create_app  # noqa: E402

app = create_app()

# Expose celery_app at module level so the Celery CLI can find it:
#   celery -A run.celery_app worker
celery_app = app.extensions["celery"]

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
