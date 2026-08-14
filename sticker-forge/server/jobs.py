from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from . import config


def _redact(value: object) -> str:
    text = str(value)
    return text.replace(config.BOT_TOKEN, "<bot-token>") if config.BOT_TOKEN else text


def _public_clip(clip: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in clip.items() if not k.startswith("_")}


@dataclass
class Job:
    id: str
    owner_id: int
    status: str = "queued"
    phase: str = ""
    stage: str = "queued"
    error: str | None = None
    result: dict[str, Any] = field(default_factory=dict)
    clips: list[dict[str, Any]] = field(default_factory=list)
    links: list[dict[str, Any]] = field(default_factory=list)
    work_dir: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def touch(self) -> None:
        self.updated_at = time.time()

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "status": self.status,
            "phase": self.phase,
            "stage": self.stage,
            "error": self.error,
            "clips": [_public_clip(c) for c in self.clips],
            "previews": [c.get("preview") for c in self.clips if c.get("status") == "ok" and c.get("preview")],
            "links": self.links,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        protected = set(payload)
        payload.update({k: v for k, v in self.result.items() if not k.startswith("_") and k not in protected})
        return payload


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()

    def create(self, owner_id: int) -> Job:
        job = Job(id=uuid.uuid4().hex, owner_id=int(owner_id))
        with self._lock:
            self._jobs[job.id] = job
        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def require_owned(self, job_id: str, owner_id: int) -> Job:
        job = self.get(job_id)
        if job is None:
            raise KeyError("Job not found")
        if int(job.owner_id) != int(owner_id):
            raise PermissionError("That job belongs to another Telegram user")
        return job

    def submit(self, function: Callable[..., Any], *args: Any, owner_id: int, **kwargs: Any) -> Job:
        job = self.create(owner_id)
        thread = threading.Thread(
            target=self._run,
            args=(job, function, args, kwargs),
            daemon=True,
            name=f"sticker-forge-job-{job.id[:8]}",
        )
        thread.start()
        return job

    def _run(self, job: Job, function: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> None:
        job.status = "running"
        job.stage = "starting"
        job.error = None
        job.touch()
        try:
            result = function(*args, job=job, **kwargs)
            if isinstance(result, dict):
                job.result.update(result)
                if "clips" in result:
                    job.clips = result["clips"]
                if "links" in result:
                    job.links = result["links"]
            job.status = "done"
            if job.stage in {"", "starting"}:
                job.stage = "complete"
        except Exception as exc:
            job.status = "error"
            job.stage = "failed"
            job.error = f"{type(exc).__name__}: {_redact(exc)}"
        finally:
            job.touch()

    def purge(self, max_age_seconds: int = 6 * 3600) -> list[Job]:
        cutoff = time.time() - max_age_seconds
        removed: list[Job] = []
        with self._lock:
            for job_id, job in list(self._jobs.items()):
                if job.updated_at < cutoff:
                    removed.append(self._jobs.pop(job_id))
        return removed


STORE = JobStore()
