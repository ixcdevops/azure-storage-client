import os

from celery import Celery, Task
from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix

from .config import Config


def celery_init_app(app: Flask) -> Celery:
    """Create and bind a Celery instance to the Flask app."""

    class FlaskTask(Task):
        def __call__(self, *args: object, **kwargs: object) -> object:
            with app.app_context():
                return self.run(*args, **kwargs)

    celery_app = Celery(app.name, task_cls=FlaskTask)
    celery_app.conf.update(
        broker_url=app.config["CELERY_BROKER_URL"],
        result_backend=app.config["CELERY_RESULT_BACKEND"],
        result_extended=True,  # store task name / args in result
        task_track_started=True,
        task_serializer="json",
        result_serializer="json",
        accept_content=["json"],
    )
    celery_app.set_default()
    app.extensions["celery"] = celery_app
    return celery_app


def create_app() -> Flask:
    """Application factory."""
    app = Flask(__name__)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1, x_prefix=1)
    app.config.from_object(Config)

    # Ensure the download directory exists
    download_path = app.config["DOWNLOAD_PATH"]
    os.makedirs(download_path, exist_ok=True)

    # Celery
    celery_init_app(app)

    # ------------------------------------------------------------------
    # Register blueprints
    # ------------------------------------------------------------------
    from .routes.storage import storage_bp  # noqa: E402
    from .routes.files import files_bp  # noqa: E402
    from .routes.jobs import jobs_bp  # noqa: E402
    from .routes.onedrive import onedrive_bp  # noqa: E402

    app.register_blueprint(storage_bp)
    app.register_blueprint(files_bp)
    app.register_blueprint(jobs_bp)
    app.register_blueprint(onedrive_bp)

    return app
