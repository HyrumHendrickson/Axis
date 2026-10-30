"""Long model operations (install, start, stop, delete) run in the background.

The Models page polls the job list, so an operation keeps going and stays
visible when the user switches pages or reloads. Only one operation can run on
a model at a time.
"""

from __future__ import annotations

import itertools
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

KEEP_FINISHED = 15 * 60   # seconds a finished job stays listed
MAX_FINISHED = 30


class JobError(Exception):
    """An operation failed in a way the user can act on."""

    user_facing = True


class Cancelled(JobError):
    pass


@dataclass
class Job:
    id: str
    kind: str          # install | start | stop | delete
    engine: str        # ollama | llamacpp | image
    target: str        # what the job acts on (a model name or id)
    label: str         # shown to the user, for example "Installing llama3.1:8b"
    cancellable: bool = True
    state: str = "running"       # running | done | failed | cancelled
    phase: str = ""
    done: Optional[int] = None   # bytes (or steps) finished
    total: Optional[int] = None
    error: str = ""
    result: Dict[str, Any] = field(default_factory=dict)
    started: float = field(default_factory=time.time)
    finished: Optional[float] = None
    rate: Optional[float] = None  # bytes per second, for downloads
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    _rate_mark: Any = field(default=None, repr=False)

    @property
    def cancelled(self) -> bool:
        return self._cancel.is_set()

    def check(self) -> None:
        """Raise Cancelled if the user asked to stop."""
        if self._cancel.is_set():
            raise Cancelled("Cancelled.")

    def progress(self, phase: Optional[str] = None, done: Optional[int] = None, total: Optional[int] = None) -> None:
        if phase is not None:
            self.phase = phase
        if total is not None:
            self.total = total
        if done is not None:
            now = time.time()
            mark = self._rate_mark
            if mark is None or done < mark[1]:
                self._rate_mark = (now, done)
            elif now - mark[0] >= 2.0:
                self.rate = (done - mark[1]) / (now - mark[0])
                self._rate_mark = (now, done)
            self.done = done

    def public(self) -> Dict[str, Any]:
        pct = None
        if self.total and self.done is not None:
            pct = max(0.0, min(100.0, 100.0 * self.done / self.total))
        return {"id": self.id, "kind": self.kind, "engine": self.engine, "target": self.target, "label": self.label,
                "state": self.state, "phase": self.phase, "done": self.done, "total": self.total, "percent": pct,
                "rate": self.rate, "error": self.error, "result": self.result, "started": self.started,
                "finished": self.finished, "cancellable": self.cancellable and self.state == "running",
                "cancel_requested": self._cancel.is_set()}


_LOCK = threading.Lock()
_JOBS: Dict[str, Job] = {}
_IDS = itertools.count(1)


def _prune() -> None:
    now = time.time()
    finished = sorted((j for j in _JOBS.values() if j.finished), key=lambda j: j.finished or 0)
    for j in finished:
        if now - (j.finished or now) > KEEP_FINISHED:
            del _JOBS[j.id]
    finished = [j for j in finished if j.id in _JOBS]
    for j in finished[:-MAX_FINISHED] if len(finished) > MAX_FINISHED else []:
        del _JOBS[j.id]


def busy(engine: str, target: str) -> Optional[Job]:
    with _LOCK:
        for j in _JOBS.values():
            if j.state == "running" and j.engine == engine and j.target == target:
                return j
    return None


def submit(kind: str, engine: str, target: str, label: str, work: Callable[[Job], Optional[Dict[str, Any]]],
           cancellable: bool = True, on_finish: Optional[Callable[[Job], None]] = None) -> Job:
    """Run work(job) in a background thread. Refuses a second job on the same model."""
    with _LOCK:
        _prune()
        for j in _JOBS.values():
            if j.state == "running" and j.engine == engine and j.target == target:
                raise JobError(f"{j.label} is still in progress. Wait for it to finish, or cancel it.")
        job = Job(id=str(next(_IDS)), kind=kind, engine=engine, target=target, label=label, cancellable=cancellable)
        _JOBS[job.id] = job

    def run() -> None:
        try:
            result = work(job)
            if job.cancelled:
                job.state, job.error = "cancelled", "Cancelled."
            else:
                job.state = "done"
                job.result = result or {}
        except Cancelled:
            job.state, job.error = "cancelled", "Cancelled."
        except Exception as exc:  # report every failure on the page instead of losing it
            job.state = "failed"
            known = isinstance(exc, JobError) or getattr(exc, "user_facing", False)
            job.error = str(exc) if known else f"Unexpected error: {exc}"
            if not known:
                traceback.print_exc()
        finally:
            job.finished = time.time()
            if on_finish:
                try:
                    on_finish(job)
                except Exception:
                    pass

    threading.Thread(target=run, name=f"axis-job-{job.id}", daemon=True).start()
    return job


def cancel(job_id: str) -> Job:
    with _LOCK:
        job = _JOBS.get(job_id)
    if job is None:
        raise JobError(f"Job '{job_id}' does not exist.")
    if job.state == "running":
        if not job.cancellable:
            raise JobError(f"{job.label} cannot be cancelled once it has begun.")
        job._cancel.set()
    return job


def cancel_all(wait: float = 5.0) -> int:
    """Ask every running job to stop (used when Axis quits) and give them a moment to clean up.

    Returns how many were running. A job that cannot be cancelled (a system installer that has begun)
    is left to finish.
    """
    with _LOCK:
        running = [j for j in _JOBS.values() if j.state == "running"]
    for job in running:
        if job.cancellable:
            job._cancel.set()
    deadline = time.time() + wait
    while time.time() < deadline and any(j.state == "running" and j.cancellable for j in running):
        time.sleep(0.05)
    return len(running)


def dismiss(job_id: str) -> None:
    with _LOCK:
        job = _JOBS.get(job_id)
        if job is None:
            raise JobError(f"Job '{job_id}' does not exist.")
        if job.state == "running":
            raise JobError("A job that is still running cannot be dismissed. Cancel it first.")
        del _JOBS[job_id]


def get(job_id: str) -> Optional[Job]:
    with _LOCK:
        return _JOBS.get(job_id)


def listing() -> List[Dict[str, Any]]:
    with _LOCK:
        _prune()
        jobs = sorted(_JOBS.values(), key=lambda j: j.started, reverse=True)
    return [j.public() for j in jobs]


def wait(job: Job, timeout: float = 60.0) -> Job:
    """For tests and the command line: block until a job finishes."""
    deadline = time.time() + timeout
    while job.state == "running" and time.time() < deadline:
        time.sleep(0.05)
    return job


def reset_for_tests() -> None:
    with _LOCK:
        for j in _JOBS.values():
            j._cancel.set()
        _JOBS.clear()
