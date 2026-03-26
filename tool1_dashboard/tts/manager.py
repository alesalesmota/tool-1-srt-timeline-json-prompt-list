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
from dataclasses import asdict, dataclass
from typing import Any

from ..database import Tool1Database
from .constants import HEARTBEAT_STALE_TIMEOUT

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
        self._lock = threading.Lock()

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

    def ensure_worker_ready(self, startup_wait_seconds: float = 0.75) -> None:
        runtime = self.get_runtime_status()
        if not runtime.available:
            raise RuntimeError(runtime.error or "TTS runtime unavailable.")

        if self.is_worker_alive():
            return

        self.start_worker()
        if self.is_worker_alive():
            return

        time.sleep(startup_wait_seconds)
        if self.is_worker_alive():
            return
        raise RuntimeError(
            "TTS worker failed to start. Check workspace/tts/worker.log for the startup traceback."
        )

    # ── worker lifecycle ─────────────────────────────────────────────

    def start_worker(self) -> bool:
        """Spawn the TTS worker subprocess.  Returns True if started."""
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return False  # already running

            runtime = self.get_runtime_status()
            if not runtime.available:
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
            proc = self._process
            if proc is None or proc.poll() is not None:
                self._process = None
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
        startup_error = None if runtime.available else runtime.error

        if heartbeat is None:
            return WorkerHealth(
                running=running,
                worker_id=None,
                status="unavailable" if startup_error else "unknown",
                current_job_id=None,
                last_heartbeat=None,
                is_stale=not running,
                pid=self._process.pid if self._process and running else None,
                startup_error=startup_error,
                missing_dependencies=runtime.missing_dependencies,
            )

        hb_time = heartbeat.get("heartbeat_at", 0)
        is_stale = (time.time() - hb_time) > HEARTBEAT_STALE_TIMEOUT
        return WorkerHealth(
            running=running,
            worker_id=heartbeat.get("worker_id"),
            status=heartbeat.get("status", "unknown"),
            current_job_id=heartbeat.get("current_job_id"),
            last_heartbeat=hb_time,
            is_stale=is_stale,
            pid=heartbeat.get("pid"),
            startup_error=startup_error,
            missing_dependencies=runtime.missing_dependencies,
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
