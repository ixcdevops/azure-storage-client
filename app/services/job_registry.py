"""Thin Redis-backed registry for tracking submitted download tasks."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import TYPE_CHECKING

import redis as redis_lib

if TYPE_CHECKING:
    from celery import Celery

REGISTRY_KEY = "storage_client:jobs"
JOB_META_PREFIX = "storage_client:job:"
MAX_JOBS = 200
JOB_TTL_SECONDS = 60 * 60 * 48  # 48 hours


def _r(redis_url: str) -> redis_lib.Redis:
    return redis_lib.from_url(redis_url, decode_responses=True)


def register_job(redis_url: str, task_id: str, meta: dict) -> None:
    """Store submission metadata and add *task_id* to the ordered registry list."""
    r = _r(redis_url)
    meta["submitted"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    r.setex(f"{JOB_META_PREFIX}{task_id}", JOB_TTL_SECONDS, json.dumps(meta))
    r.lpush(REGISTRY_KEY, task_id)
    r.ltrim(REGISTRY_KEY, 0, MAX_JOBS - 1)


def get_all_jobs(redis_url: str, celery_app: "Celery") -> list[dict]:
    """Return a list of job dicts ordered newest-first, augmented with live Celery state."""
    r = _r(redis_url)
    task_ids: list[str] = r.lrange(REGISTRY_KEY, 0, -1)  # type: ignore[assignment]

    jobs: list[dict] = []
    for tid in task_ids:
        raw = r.get(f"{JOB_META_PREFIX}{tid}")
        meta: dict = json.loads(raw) if raw else {}
        meta["task_id"] = tid

        result = celery_app.AsyncResult(tid)
        state = result.state
        meta["state"] = state

        info = result.info or {}
        if state == "SUCCESS":
            payload = info if isinstance(info, dict) else {}
            meta["message"] = payload.get("message", "")
            meta["done"] = payload.get("done", "")
            meta["total"] = payload.get("total", "")
            meta["ok"] = payload.get("status") == "success"
        elif state == "FAILURE":
            meta["message"] = str(info)
            meta["ok"] = False
        elif state in ("PROGRESS", "STARTED"):
            meta["message"] = info.get("status", "") if isinstance(info, dict) else ""
            meta["done"] = info.get("done", 0) if isinstance(info, dict) else 0
            meta["total"] = info.get("total", 0) if isinstance(info, dict) else 0
            meta["ok"] = True
        else:  # PENDING / RETRY / REVOKED
            meta["message"] = ""
            meta["ok"] = True

        jobs.append(meta)

    return jobs
