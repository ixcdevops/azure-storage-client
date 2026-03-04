"""Routes for the background-job history page."""

from __future__ import annotations

from flask import Blueprint, current_app, render_template

from ..services.job_registry import get_all_jobs

jobs_bp = Blueprint("jobs", __name__, url_prefix="/jobs")


@jobs_bp.route("/")
def index():
    redis_url = current_app.config["CELERY_BROKER_URL"]
    celery_app = current_app.extensions["celery"]
    jobs = get_all_jobs(redis_url, celery_app)
    return render_template("jobs.html", jobs=jobs)
