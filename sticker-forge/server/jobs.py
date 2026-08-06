from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class Job:
    id: str
    status: str = "queued"
    phase: str = ""
    stage: str = "queued"
    error: str | None = None
    result: dict[str, Any] = field(default_factory=dict)
    clips: list[dict[str, Any]] = field(default_factory=list)
    previews: list[str] = field(default_factory=list)
    links: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "id": self.id,
            "status": self.status,
            "phase": self.phase,
            "stage": self.stage,
            "error": self.error,
            "clips": self.clips,
            "previews": self.previews,
            "links": self.links,
        }

        payload.update(self.result)

        return payload


class JobStore:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def create(self) -> Job:
        job = Job(id=uuid.uuid4().hex)

        with self._lock:
            self._jobs[job.id] = job

        return job

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def submit(
        self,
        function: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Job:
        job = self.create()

        thread = threading.Thread(
            target=self._run,
            args=(job, function, args, kwargs),
            daemon=True,
            name=f"sticker-forge-job-{job.id[:8]}",
        )

        thread.start()

        return job

    def _run(
        self,
        job: Job,
        function: Callable[..., Any],
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> None:
        job.status = "running"
        job.stage = "starting"

        try:
            result = function(
                *args,
                job=job,
                **kwargs,
            )

            if isinstance(result, dict):
                job.result.update(result)

                if "clips" in result:
                    job.clips = result["clips"]

                if "previews" in result:
                    job.previews = result["previews"]

                if "links" in result:
                    job.links = result["links"]

            job.status = "done"

            if not job.stage or job.stage == "starting":
                job.stage = "complete"

        except Exception as exc:
            job.status = "error"
            job.stage = "failed"
            job.error = f"{type(exc).__name__}: {exc}"


STORE = JobStore()
