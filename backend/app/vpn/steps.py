"""Step log and compensations (saga) for write operations."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Awaitable, Callable

from ..opnsense.errors import OpnsenseError

log = logging.getLogger(__name__)

Compensation = Callable[[], Awaitable[None]]


@dataclass
class Step:
    key: str
    title: str
    status: str = "pending"  # pending | running | done | skipped | warning | failed
    detail: str | None = None


@dataclass
class Job:
    kind: str
    name: str
    steps: list[Step]
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: str = "running"  # running | succeeded | failed
    error: dict | None = None
    rollback: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    result: dict | None = None
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def step(self, key: str) -> Step:
        return next(s for s in self.steps if s.key == key)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["total"] = len(self.steps)
        d["completed"] = sum(s.status in ("done", "skipped", "warning") for s in self.steps)
        return d


class JobStore:
    """Jobs live in memory only; they exist purely for progress reporting."""

    MAX = 50

    def __init__(self):
        self._jobs: dict[str, Job] = {}

    def add(self, job: Job) -> Job:
        self._jobs[job.id] = job
        while len(self._jobs) > self.MAX:
            self._jobs.pop(next(iter(self._jobs)))
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)


class Saga:
    def __init__(self, job: Job):
        self.job = job
        self._compensations: list[tuple[str, Compensation]] = []

    def on_rollback(self, title: str, fn: Compensation) -> None:
        self._compensations.append((title, fn))

    async def run(self, key: str, fn: Callable[[Step], Awaitable[None]]) -> None:
        step = self.job.step(key)
        step.status = "running"
        await fn(step)
        if step.status == "running":
            step.status = "done"

    async def rollback(self) -> None:
        for title, fn in reversed(self._compensations):
            try:
                await fn()
                self.job.rollback.append({"action": title, "status": "ok"})
            except OpnsenseError as e:
                log.error("Rollback '%s' failed: %s", title, e.message)
                self.job.rollback.append({"action": title, "status": "failed", "error": e.message})
            except Exception as e:  # noqa: BLE001 – keep rolling back
                log.exception("Rollback '%s' failed", title)
                self.job.rollback.append({"action": title, "status": "failed", "error": str(e)})
        self._compensations.clear()
