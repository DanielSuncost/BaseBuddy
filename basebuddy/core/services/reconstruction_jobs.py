"""
Background job manager for 3D reconstruction.

Reconstruction takes 30s-minutes (much longer on CPU); running it inside a
Flask request thread blocks the worker and dies on client timeouts. Jobs run
on a single worker thread (models are large; serializing GPU/CPU work avoids
memory blowups) and expose progress for UI polling.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

# Completed/failed jobs older than this are pruned on access.
JOB_RETENTION_S = 3600.0
MAX_JOBS = 200


@dataclass
class Job:
    id: str
    status: str = 'queued'  # queued | running | done | error
    progress: int = 0
    message: str = ''
    error: Optional[str] = None
    result: Optional[dict] = None
    created_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            'id': self.id,
            'status': self.status,
            'progress': self.progress,
            'message': self.message,
            'error': self.error,
            'result': self.result,
        }


class ReconstructionJobManager:
    """Thread-safe registry + single-worker executor for reconstruction jobs."""

    def __init__(self, max_workers: int = 1):
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=max_workers,
                                        thread_name_prefix='recon-job')

    def submit(self, fn: Callable[..., dict], *args: Any, **kwargs: Any) -> str:
        """Run fn(progress_cb, *args, **kwargs) in the background.

        fn receives progress_cb(percent, message) as its first argument and
        must return the job result dict; exceptions mark the job failed.
        """
        job = Job(id=uuid.uuid4().hex[:12])
        with self._lock:
            self._prune_locked()
            self._jobs[job.id] = job

        def progress_cb(percent: int, message: str = '') -> None:
            with self._lock:
                job.progress = max(0, min(99, int(percent)))
                if message:
                    job.message = message

        def run() -> None:
            with self._lock:
                job.status = 'running'
            try:
                result = fn(progress_cb, *args, **kwargs)
                with self._lock:
                    job.status = 'done'
                    job.progress = 100
                    job.result = result
                    job.finished_at = time.time()
            except Exception as e:
                logger.exception(f'Reconstruction job {job.id} failed')
                with self._lock:
                    job.status = 'error'
                    job.error = str(e)
                    job.finished_at = time.time()

        self._pool.submit(run)
        return job.id

    def get(self, job_id: str) -> Optional[dict]:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.to_dict() if job else None

    def _prune_locked(self) -> None:
        now = time.time()
        stale = [jid for jid, j in self._jobs.items()
                 if j.finished_at and now - j.finished_at > JOB_RETENTION_S]
        for jid in stale:
            del self._jobs[jid]
        # Hard cap as a safety net (oldest finished first).
        if len(self._jobs) > MAX_JOBS:
            finished = sorted((j for j in self._jobs.values() if j.finished_at),
                              key=lambda j: j.finished_at)
            for j in finished[:len(self._jobs) - MAX_JOBS]:
                del self._jobs[j.id]


_manager: Optional[ReconstructionJobManager] = None
_manager_lock = threading.Lock()


def get_job_manager() -> ReconstructionJobManager:
    global _manager
    with _manager_lock:
        if _manager is None:
            _manager = ReconstructionJobManager()
        return _manager
