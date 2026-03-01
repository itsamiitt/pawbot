"""Phase 11 cron scheduler for internal periodic jobs."""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from loguru import logger

try:
    from croniter import croniter
except Exception:  # pragma: no cover - exercised by graceful fallback paths
    croniter = None


@dataclass
class CronJob:
    """Single registered cron job."""

    name: str
    schedule: str
    fn: Callable[[], Any]
    description: str = ""
    last_run: int = 0
    next_run: int = 0
    run_count: int = 0
    last_error: str = ""
    enabled: bool = True


class CronScheduler:
    """
    Runs registered jobs on cron-expression schedules.

    Notes:
    - Jobs execute in daemon threads.
    - Metadata persists to ~/.pawbot/crons.json.
    - Function callables are not persisted and must be re-registered on startup.
    """

    REGISTRY_PATH = os.path.expanduser("~/.pawbot/crons.json")

    def __init__(
        self,
        registry_path: str | os.PathLike[str] | None = None,
        check_interval_seconds: int = 30,
    ):
        self.registry_path = os.path.expanduser(str(registry_path or self.REGISTRY_PATH))
        self.check_interval_seconds = max(1, int(check_interval_seconds))
        self._jobs: dict[str, CronJob] = {}
        self._thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.RLock()
        self._inflight: set[str] = set()
        self._load_registry()

    @staticmethod
    def _next_run_for_schedule(schedule: str, now: int | float | None = None) -> int | None:
        if croniter is None:
            logger.warning("CronScheduler: croniter is unavailable, cannot parse '{}'", schedule)
            return None
        base = now if now is not None else time.time()
        try:
            cron = croniter(schedule, base)
            return int(cron.get_next(float))
        except Exception as exc:
            logger.warning("CronScheduler: invalid schedule '{}' ({})", schedule, exc)
            return None

    def register(
        self,
        name: str,
        schedule: str,
        fn: Callable[[], Any],
        description: str = "",
    ) -> CronJob:
        """
        Register or update a cron job.

        Existing runtime stats are preserved when a job is overwritten.
        """
        next_run = self._next_run_for_schedule(schedule, time.time())
        if next_run is None:
            raise ValueError(f"Invalid cron schedule: {schedule}")

        with self._lock:
            previous = self._jobs.get(name)
            job = CronJob(
                name=name,
                schedule=schedule,
                fn=fn,
                description=description,
                next_run=next_run,
            )
            if previous is not None:
                job.last_run = previous.last_run
                job.run_count = previous.run_count
                job.last_error = previous.last_error
                job.enabled = previous.enabled
            self._jobs[name] = job
            self._save_registry_locked()

        logger.info("Cron registered: {} @ {}, next={}", name, schedule, next_run)
        return job

    def unregister(self, name: str) -> bool:
        """Remove a registered job."""
        with self._lock:
            if name not in self._jobs:
                return False
            del self._jobs[name]
            self._inflight.discard(name)
            self._save_registry_locked()
        logger.info("Cron unregistered: {}", name)
        return True

    def start(self) -> None:
        """Start the scheduler thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="pawbot-cron")
        self._thread.start()
        logger.info("CronScheduler started (interval={}s)", self.check_interval_seconds)

    def stop(self) -> None:
        """Stop the scheduler thread."""
        self._running = False
        logger.info("CronScheduler stopped")

    def enable(self, name: str, enabled: bool = True) -> bool:
        """Enable/disable a job by name."""
        with self._lock:
            job = self._jobs.get(name)
            if job is None:
                return False
            job.enabled = enabled
            self._save_registry_locked()
            return True

    def list_jobs(self) -> list[dict[str, Any]]:
        """List all jobs with runtime status."""
        with self._lock:
            jobs = list(self._jobs.values())
        return [
            {
                "name": j.name,
                "schedule": j.schedule,
                "description": j.description,
                "last_run": j.last_run,
                "next_run": j.next_run,
                "run_count": j.run_count,
                "last_error": j.last_error,
                "enabled": j.enabled,
            }
            for j in jobs
        ]

    def _loop(self) -> None:
        """Main scheduler loop."""
        while self._running:
            try:
                self._check_due_jobs()
            except Exception:
                logger.exception("CronScheduler loop iteration failed")
            time.sleep(self.check_interval_seconds)

    def _check_due_jobs(self, now: int | None = None) -> None:
        """Run jobs due at the given unix timestamp (or current time)."""
        ts = int(now if now is not None else time.time())
        due: list[CronJob] = []

        with self._lock:
            for job in self._jobs.values():
                if not job.enabled:
                    continue
                if job.next_run <= 0:
                    next_run = self._next_run_for_schedule(job.schedule, ts)
                    if next_run is not None:
                        job.next_run = next_run
                if job.next_run and ts >= job.next_run and job.name not in self._inflight:
                    self._inflight.add(job.name)
                    due.append(job)

        for job in due:
            self._run_job(job)

    def _run_job(self, job: CronJob) -> None:
        """Execute one job in a daemon thread and reschedule."""

        def _execute() -> None:
            try:
                logger.info("Cron job starting: {}", job.name)
                job.fn()
                with self._lock:
                    job.run_count += 1
                    job.last_run = int(time.time())
                    job.last_error = ""
                logger.info("Cron job completed: {} (run #{})", job.name, job.run_count)
            except Exception as exc:
                with self._lock:
                    job.last_run = int(time.time())
                    job.last_error = str(exc)
                logger.warning("Cron job failed: {} - {}", job.name, exc)
            finally:
                with self._lock:
                    next_run = self._next_run_for_schedule(job.schedule, time.time())
                    job.next_run = next_run or 0
                    self._inflight.discard(job.name)
                    self._save_registry_locked()

        thread = threading.Thread(target=_execute, daemon=True, name=f"cron-job-{job.name}")
        thread.start()

    def _save_registry(self) -> None:
        with self._lock:
            self._save_registry_locked()

    def _save_registry_locked(self) -> None:
        path = Path(self.registry_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            name: {
                "schedule": job.schedule,
                "description": job.description,
                "last_run": job.last_run,
                "next_run": job.next_run,
                "run_count": job.run_count,
                "last_error": job.last_error,
                "enabled": job.enabled,
            }
            for name, job in self._jobs.items()
        }
        path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

    def _load_registry(self) -> None:
        path = Path(self.registry_path)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return
            for name, meta in data.items():
                if not isinstance(meta, dict):
                    continue
                job = CronJob(
                    name=str(name),
                    schedule=str(meta.get("schedule", "0 * * * *")),
                    fn=lambda: None,
                    description=str(meta.get("description", "")),
                    last_run=int(meta.get("last_run", 0) or 0),
                    next_run=int(meta.get("next_run", 0) or 0),
                    run_count=int(meta.get("run_count", 0) or 0),
                    last_error=str(meta.get("last_error", "")),
                    enabled=bool(meta.get("enabled", True)),
                )
                if job.next_run <= 0 and job.enabled:
                    next_run = self._next_run_for_schedule(job.schedule, time.time())
                    job.next_run = next_run or 0
                self._jobs[job.name] = job
        except Exception as exc:
            logger.warning("Failed to load cron registry '{}': {}", path, exc)
