"""TTS Manager — runs in the main FastAPI process.

Manages the TTS worker subprocess lifecycle and job submission.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

from ..database import Tool1Database
from .constants import (
    HEARTBEAT_STALE_TIMEOUT,
    INTERACTIVE_IDLE_SHUTDOWN_SECONDS,
    PIPELINE_IDLE_SHUTDOWN_SECONDS,
    WORKER_IDLE_RECHECK_SECONDS,
)

log = logging.getLogger(__name__)


@dataclass
class WorkerHealth:
    running: bool
    worker_id: str | None
    status: str  # "idle" | "processing" | "stopped" | "unknown"
    current_job_id: str | None
    last_heartbeat: float | None
    is_stale: bool
    pid: int | None
    startup_error: str | None
    missing_dependencies: list[str]
    lifecycle_state: str  # "sleeping" | "starting" | "processing" | "unavailable"


@dataclass
class WorkerRuntimeStatus:
    available: bool
    missing_dependencies: list[str]
    error: str | None


class TTSManager:
    """Orchestrates TTS worker lifecycle and job submission."""

    def __init__(self, db: Tool1Database) -> None:
        self._db = db
        self._process: subprocess.Popen | None = None
        self._lock = threading.RLock()
        self._shutdown_timer: threading.Timer | None = None
        self._last_activity_at = 0.0
        self._lifecycle_intent = "interactive"
        self._starting_since: float | None = None
        self._active_worker_id: str | None = None
        self._last_startup_error: str | None = None

    @staticmethod
    def _normalize_lifecycle_intent(intent: str | None) -> str:
        return "pipeline" if str(intent or "").strip().lower() == "pipeline" else "interactive"

    @staticmethod
    def _shutdown_cooldown_seconds(intent: str) -> float:
        return (
            PIPELINE_IDLE_SHUTDOWN_SECONDS
            if intent == "pipeline"
            else INTERACTIVE_IDLE_SHUTDOWN_SECONDS
        )

    def _cancel_shutdown_timer_locked(self) -> None:
        if self._shutdown_timer is not None:
            self._shutdown_timer.cancel()
            self._shutdown_timer = None

    def _remember_usage(self, intent: str | None) -> str:
        normalized = self._normalize_lifecycle_intent(intent)
        with self._lock:
            self._lifecycle_intent = normalized
            self._last_activity_at = time.time()
            self._cancel_shutdown_timer_locked()
        return normalized

    def _schedule_shutdown_check(self, delay: float | None = None) -> None:
        with self._lock:
            if not self.is_worker_alive():
                return
            self._cancel_shutdown_timer_locked()
            wait_seconds = WORKER_IDLE_RECHECK_SECONDS if delay is None else max(0.5, float(delay))
            timer = threading.Timer(wait_seconds, self._evaluate_worker_shutdown)
            timer.daemon = True
            self._shutdown_timer = timer
            timer.start()

    def _evaluate_worker_shutdown(self) -> None:
        with self._lock:
            self._shutdown_timer = None

        if not self.is_worker_alive():
            return

        active_jobs = self._db.list_active_tts_jobs()
        now = time.time()
        if active_jobs:
            active_intent = (
                "pipeline"
                if any(str(job.get("job_type") or "") == "generate" for job in active_jobs)
                else "interactive"
            )
            with self._lock:
                self._lifecycle_intent = active_intent
                self._last_activity_at = now
            self._schedule_shutdown_check(WORKER_IDLE_RECHECK_SECONDS)
            return

        with self._lock:
            last_activity_at = self._last_activity_at or now
            intent = self._lifecycle_intent
        remaining = self._shutdown_cooldown_seconds(intent) - (now - last_activity_at)
        if remaining > 0:
            self._schedule_shutdown_check(remaining)
            return

        self.stop_worker()

    @staticmethod
    def _runtime_dependency_labels() -> tuple[tuple[str, str], ...]:
        return (
            ("torch", "torch"),
            ("torchaudio", "torchaudio"),
            ("TTS.api", "Coqui TTS"),
        )

    def get_runtime_status(self) -> WorkerRuntimeStatus:
        missing: list[str] = []
        details: list[str] = []
        for module_name, label in self._runtime_dependency_labels():
            try:
                importlib.import_module(module_name)
            except Exception as exc:  # pragma: no cover - host-specific import errors vary
                missing.append(label)
                details.append(f"{label}: {exc}")
        if not missing:
            return WorkerRuntimeStatus(available=True, missing_dependencies=[], error=None)

        message_parts = [
            f"TTS runtime unavailable. Missing dependencies: {', '.join(missing)}.",
        ]
        if sys.platform.startswith("win") and "Coqui TTS" in missing:
            message_parts.append(
                "On Windows, installing Coqui TTS may also require Microsoft C++ Build Tools."
            )
        if details:
            message_parts.append(f"Details: {'; '.join(details)}")
        return WorkerRuntimeStatus(
            available=False,
            missing_dependencies=missing,
            error=" ".join(message_parts),
        )

    def ensure_worker_ready(
        self,
        *,
        intent: str = "interactive",
        startup_wait_seconds: float = 0.75,
    ) -> None:
        runtime = self.get_runtime_status()
        if not runtime.available:
            with self._lock:
                self._last_startup_error = runtime.error
            raise RuntimeError(runtime.error or "TTS runtime unavailable.")

        self._remember_usage(intent)
        health = self.get_worker_health()
        if health.running and not health.is_stale:
            self._schedule_shutdown_check()
            return

        if health.is_stale and self.is_worker_alive():
            self.stop_worker()

        self.start_worker()
        if self.is_worker_alive():
            self._schedule_shutdown_check()
            return

        time.sleep(startup_wait_seconds)
        if self.is_worker_alive():
            self._schedule_shutdown_check()
            return
        error = "TTS worker failed to start. Check workspace/tts/worker.log for the startup traceback."
        with self._lock:
            self._last_startup_error = error
        raise RuntimeError(error)

    # ── worker lifecycle ─────────────────────────────────────────────

    def start_worker(self) -> bool:
        """Spawn the TTS worker subprocess.  Returns True if started."""
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return False  # already running

            runtime = self.get_runtime_status()
            if not runtime.available:
                self._last_startup_error = runtime.error
                log.error(runtime.error or "TTS runtime unavailable.")
                return False

            from ..config import (
                DATABASE_PATH,
                PROJECT_ROOT,
                TTS_CHUNKS_DIR,
                TTS_OUTPUT_DIR,
                TTS_PROFILES_DIR,
                TTS_ROOT,
            )

            worker_id = f"worker-{os.getpid()}-{uuid.uuid4().hex[:6]}"
            self._cancel_shutdown_timer_locked()
            self._starting_since = time.time()
            self._active_worker_id = worker_id
            self._last_activity_at = self._starting_since
            self._last_startup_error = None

            env = os.environ.copy()
            env["TTS_DB_PATH"] = str(DATABASE_PATH)
            env["TTS_WORKER_ID"] = worker_id
            env["TTS_PROFILES_DIR"] = str(TTS_PROFILES_DIR)
            env["TTS_OUTPUT_DIR"] = str(TTS_OUTPUT_DIR)
            env["TTS_CHUNKS_DIR"] = str(TTS_CHUNKS_DIR)
            env["COQUI_TOS_AGREED"] = "1"
            env["TTS_AGREE_TOS"] = "1"
            env["PYTHONUNBUFFERED"] = "1"

            # Ensure project root is on PYTHONPATH so the worker can
            # import tool1_dashboard.
            python_path = env.get("PYTHONPATH", "")
            project_str = str(PROJECT_ROOT)
            if project_str not in python_path.split(os.pathsep):
                env["PYTHONPATH"] = (
                    f"{project_str}{os.pathsep}{python_path}" if python_path else project_str
                )

            TTS_ROOT.mkdir(parents=True, exist_ok=True)
            log_path = TTS_ROOT / "worker.log"

            log_file = open(log_path, "a", encoding="utf-8")  # noqa: SIM115
            self._process = subprocess.Popen(
                [sys.executable, "-m", "tool1_dashboard.tts.worker"],
                env=env,
                cwd=str(PROJECT_ROOT),
                stdout=log_file,
                stderr=subprocess.STDOUT,
                creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
            )
            log.info("TTS worker started (pid=%s, id=%s)", self._process.pid, worker_id)
            return True

    def stop_worker(self, timeout: float = 10.0) -> None:
        """Terminate the TTS worker subprocess."""
        with self._lock:
            self._cancel_shutdown_timer_locked()
            proc = self._process
            if proc is None or proc.poll() is not None:
                self._process = None
                self._active_worker_id = None
                self._starting_since = None
                self._lifecycle_intent = "interactive"
                self._last_activity_at = 0.0
                return

            log.info("Stopping TTS worker (pid=%s)...", proc.pid)
            proc.terminate()
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                log.warning("TTS worker did not exit in time, killing.")
                proc.kill()
                proc.wait(timeout=5)
            self._process = None
            self._active_worker_id = None
            self._starting_since = None
            self._lifecycle_intent = "interactive"
            self._last_activity_at = 0.0

    def is_worker_alive(self) -> bool:
        with self._lock:
            if self._process is None:
                return False
            return self._process.poll() is None

    def ensure_worker(self) -> None:
        """Start the worker if it is not already running."""
        if not self.is_worker_alive():
            self.start_worker()

    def get_worker_health(self) -> WorkerHealth:
        heartbeat = self._db.get_latest_worker_heartbeat()
        running = self.is_worker_alive()
        runtime = self.get_runtime_status()
        with self._lock:
            active_worker_id = self._active_worker_id
            starting_since = self._starting_since
            remembered_startup_error = self._last_startup_error
        startup_error = runtime.error if not runtime.available else remembered_startup_error

        current_heartbeat = heartbeat
        heartbeat_is_current = current_heartbeat is not None
        if current_heartbeat is not None:
            heartbeat_worker_id = str(current_heartbeat.get("worker_id") or "").strip()
            heartbeat_started_at = float(current_heartbeat.get("started_at") or 0)
            if active_worker_id and heartbeat_worker_id != active_worker_id:
                heartbeat_is_current = False
            elif starting_since and heartbeat_started_at and heartbeat_started_at < (starting_since - 0.01):
                heartbeat_is_current = False

        if running and heartbeat_is_current and current_heartbeat is not None:
            with self._lock:
                self._starting_since = None
                if runtime.available:
                    self._last_startup_error = None
                    startup_error = None

        if current_heartbeat is None or not heartbeat_is_current:
            lifecycle_state = "unavailable" if startup_error else ("starting" if running else "sleeping")
            status = "unavailable" if startup_error else ("starting" if running else "sleeping")
            return WorkerHealth(
                running=running,
                worker_id=active_worker_id or (current_heartbeat or {}).get("worker_id"),
                status=status,
                current_job_id=None,
                last_heartbeat=(current_heartbeat or {}).get("heartbeat_at"),
                is_stale=False,
                pid=self._process.pid if self._process and running else None,
                startup_error=startup_error,
                missing_dependencies=runtime.missing_dependencies,
                lifecycle_state=lifecycle_state,
            )

        hb_time = current_heartbeat.get("heartbeat_at", 0)
        is_stale = (time.time() - hb_time) > HEARTBEAT_STALE_TIMEOUT
        current_job_id = current_heartbeat.get("current_job_id")
        lifecycle_state = "sleeping"
        if startup_error:
            lifecycle_state = "unavailable"
        elif running and not is_stale and current_job_id:
            lifecycle_state = "processing"
        return WorkerHealth(
            running=running,
            worker_id=current_heartbeat.get("worker_id"),
            status=current_heartbeat.get("status", "unknown"),
            current_job_id=current_job_id,
            last_heartbeat=hb_time,
            is_stale=is_stale,
            pid=current_heartbeat.get("pid"),
            startup_error=startup_error,
            missing_dependencies=runtime.missing_dependencies,
            lifecycle_state=lifecycle_state,
        )

    # ── job submission ───────────────────────────────────────────────

    def submit_tts_job(
        self,
        *,
        job_type: str,
        profile_id: str,
        payload: dict[str, Any],
        build_id: str | None = None,
        queue_priority: int = 10,
        filename: str | None = None,
        meta: dict[str, Any] | None = None,
    ) -> str:
        """Create a TTS job record in the database and return the job_id."""
        job_id = str(uuid.uuid4())
        now = time.time()
        self._db.create_tts_job({
            "job_id": job_id,
            "build_id": build_id,
            "job_type": job_type,
            "profile_id": profile_id,
            "status": "queued",
            "progress": "Queued...",
            "payload_json": json.dumps(payload, ensure_ascii=False),
            "meta_json": json.dumps(meta or {}, ensure_ascii=False),
            "queue_priority": queue_priority,
            "filename": filename,
            "created_at": now,
            "updated_at": now,
        })
        return job_id

    def set_job_control(self, job_id: str, action: str | None) -> bool:
        """Set *control_action* on a TTS job ('pause', 'stop', or None to clear)."""
        job = self._db.get_tts_job(job_id)
        if job is None:
            return False
        self._db.update_tts_job(job_id, control_action=action, updated_at=time.time())
        return True

    def get_job_status(self, job_id: str) -> dict[str, Any] | None:
        """Return the TTS job with parsed payload/meta."""
        job = self._db.get_tts_job(job_id)
        if job is None:
            return None
        result = dict(job)
        for src, dst in (("payload_json", "payload"), ("meta_json", "meta")):
            raw = result.get(src, "")
            try:
                result[dst] = json.loads(raw) if raw else {}
            except (json.JSONDecodeError, TypeError):
                result[dst] = {}
        return result
