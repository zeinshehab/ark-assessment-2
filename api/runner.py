"""Queued, background execution of review runs, and their storage.

A run takes minutes (rate-limited free tiers), so the API never runs one inside a request:
  POST /api/runs  -> checks limits, saves a "queued" record, puts the id on a queue, answers 202 at once
  worker          -> takes the next id, runs the review loop in a thread, saves progress after every step
  GET /api/runs/x -> reads the saved record (the UI polls it)

Why a thread: the provider calls are synchronous and sleep while pacing/backing off. On the event loop they would
freeze the whole server, including /healthz. Each run gets its own thread with its own event loop (D4).

Limits (abuse and cost protection): at most MAX_QUEUED_RUNS waiting runs, and RUNS_PER_USER_PER_HOUR per user.
Both answer 429. The per-user counter lives in memory, so it resets when the app restarts (acceptable for one
instance; several instances would need a shared store).

Storage: one JSON file per run in DATA_DIR/runs (a Docker volume), written atomically so a poll never reads half a
file. No database: one instance, a handful of users, and runs are append-only.
"""
import asyncio
import collections
import json
import logging
import os
import re
import secrets
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from reviewer_loop import config, metrics, orchestrator
from reviewer_loop.errors import classify
from reviewer_loop.logs import current_run_id, redact

log = logging.getLogger("api.runner")

RUN_ID = re.compile(r"^[0-9]{10}-[0-9a-f]{6}$")  # ids are validated before they touch the filesystem
FINISHED = {"converged", "deadlock", "timeout", "error", "interrupted"}


class LimitExceeded(Exception):
    def __init__(self, message: str, retry_after_s: int):
        super().__init__(message)
        self.retry_after_s = retry_after_s


class RunStore:
    def __init__(self, directory: Path):
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def _path(self, run_id: str) -> Path:
        if not RUN_ID.match(run_id):
            raise KeyError(run_id)
        return self.directory / f"{run_id}.json"

    def save(self, record: dict) -> None:
        # write to a temp file, then rename: readers see the old or the new file, never a partial one
        fd, tmp = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=1)
        os.replace(tmp, self._path(record["id"]))

    def get(self, run_id: str) -> dict | None:
        try:
            return json.loads(self._path(run_id).read_text(encoding="utf-8"))
        except (KeyError, FileNotFoundError):
            return None

    def list(self, user: str, limit: int = 50) -> list[dict]:
        records = []
        for path in sorted(self.directory.glob("*.json"), reverse=True):  # ids start with a timestamp
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("user") == user:
                records.append({k: record.get(k) for k in ("id", "task", "status", "error_type", "created",
                                                           "duration_s")})
            if len(records) >= limit:
                break
        return records

    def mark_interrupted(self) -> int:
        """At startup: runs that were queued or running when the app stopped will never finish. Say so."""
        count = 0
        for path in self.directory.glob("*.json"):
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("status") not in FINISHED:
                record.update(status="interrupted", error_type=None,
                              error="The service restarted before this run finished. Please submit it again.")
                self.save(record)
                count += 1
        return count


@dataclass
class Job:
    run_id: str
    task: str
    max_iterations: int | None
    inject_wrong_finding: bool


class Runner:
    def __init__(self, store: RunStore):
        self.store = store
        self.queue: asyncio.Queue[Job] = asyncio.Queue()
        self.recent: dict[str, collections.deque] = collections.defaultdict(collections.deque)
        self.workers: list[asyncio.Task] = []
        self.running = 0

    def start(self) -> None:
        for n in range(config.get_settings().max_concurrent_runs):
            self.workers.append(asyncio.create_task(self._worker(), name=f"run-worker-{n}"))

    async def stop(self) -> None:
        for worker in self.workers:
            worker.cancel()
        await asyncio.gather(*self.workers, return_exceptions=True)

    def submit(self, user: str, task: str, max_iterations: int | None, inject_wrong_finding: bool) -> dict:
        settings = config.get_settings()
        if self.queue.qsize() >= settings.max_queued_runs:
            raise LimitExceeded("Too many runs are waiting. Try again in a few minutes.", 120)
        now, window = time.time(), self.recent[user]
        while window and now - window[0] > 3600:
            window.popleft()
        if len(window) >= settings.runs_per_user_per_hour:
            raise LimitExceeded(f"You have started {len(window)} runs in the last hour (limit "
                                f"{settings.runs_per_user_per_hour}).", int(3600 - (now - window[0])) + 1)
        window.append(now)

        run_id = f"{int(now)}-{secrets.token_hex(3)}"
        record = {"id": run_id, "user": user, "task": task, "status": "queued", "max_iterations": max_iterations,
                  "inject_wrong_finding": inject_wrong_finding, "created": time.strftime("%Y-%m-%d %H:%M:%S"),
                  "app_version": settings.app_version}
        self.store.save(record)
        self.queue.put_nowait(Job(run_id, task, max_iterations, inject_wrong_finding))
        metrics.QUEUE_DEPTH.set(self.queue.qsize())
        log.info("run queued", extra={"run_id": run_id, "user": user, "task_chars": len(task)})
        return record

    async def _worker(self) -> None:
        while True:
            job = await self.queue.get()
            metrics.QUEUE_DEPTH.set(self.queue.qsize())
            self.running += 1
            metrics.RUNS_IN_PROGRESS.inc()
            try:
                await asyncio.to_thread(self._execute, job)
            finally:
                self.running -= 1
                metrics.RUNS_IN_PROGRESS.dec()
                self.queue.task_done()

    def _execute(self, job: Job) -> None:
        """Runs in a worker thread with its own event loop."""
        current_run_id.set(job.run_id)
        meta = {k: v for k, v in (self.store.get(job.run_id) or {}).items()
                if k in ("id", "user", "created", "max_iterations", "app_version")}
        started = time.monotonic()
        log.info("run started", extra={"run_id": job.run_id})

        def on_progress(record: dict) -> None:
            self.store.save({**record, **meta})

        try:
            record = asyncio.run(orchestrator.run_with_mcp(
                job.task, task_id=job.run_id, max_iterations=job.max_iterations,
                inject_wrong_finding=job.inject_wrong_finding, on_progress=on_progress))
        except Exception as exc:  # last line of defence: run_with_mcp already turns failures into records
            log.exception("run crashed outside the review loop", extra={"run_id": job.run_id})
            record = {"task": job.task, "status": "error", "error_type": classify(exc),
                      "error": redact(f"{type(exc).__name__}: {exc}")[:500]}
        record = {**record, **meta}
        self.store.save(record)

        seconds = time.monotonic() - started
        metrics.RUNS.labels(record["status"], record.get("error_type") or "none").inc()
        metrics.RUN_DURATION.observe(seconds)
        level = logging.INFO if record["status"] != "error" else logging.WARNING
        log.log(level, "run finished", extra={"run_id": job.run_id, "status": record["status"],
                                              "error_type": record.get("error_type"), "duration_s": round(seconds, 1),
                                              "rounds": len(record.get("iterations") or [])})
