from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .config import DEFAULT_HOST, WORKSPACE_ROOT

try:  # pragma: no cover - Windows-only import at runtime
    import msvcrt
except ImportError:  # pragma: no cover
    msvcrt = None


APP_RUNTIME_DIR = WORKSPACE_ROOT / "app_runtime"
APP_INSTANCE_LOCK_PATH = APP_RUNTIME_DIR / "instance.lock"
APP_RUNTIME_STATE_PATH = APP_RUNTIME_DIR / "runtime.json"
APP_LAUNCHER_LOG_PATH = APP_RUNTIME_DIR / "launcher.log"

_RUNTIME_LOCK = threading.RLock()
_RUNTIME_INFO: dict[str, Any] = {}
_SHUTDOWN_CALLBACK: Callable[[], None] | None = None


def _ensure_runtime_dir() -> Path:
    APP_RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    return APP_RUNTIME_DIR


def runtime_iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_runtime_info() -> dict[str, Any]:
    return {
        "pid": os.getpid(),
        "host": DEFAULT_HOST,
        "port": None,
        "url": None,
        "mode": "server",
        "window_controls_shutdown": False,
        "started_at": runtime_iso_now(),
    }


def append_launcher_log(message: str) -> Path:
    _ensure_runtime_dir()
    line = f"[{runtime_iso_now()}] {message.rstrip()}\n"
    APP_LAUNCHER_LOG_PATH.write_text(
        APP_LAUNCHER_LOG_PATH.read_text(encoding="utf-8", errors="replace") + line
        if APP_LAUNCHER_LOG_PATH.exists()
        else line,
        encoding="utf-8",
    )
    return APP_LAUNCHER_LOG_PATH


def set_runtime_info(**fields: Any) -> dict[str, Any]:
    with _RUNTIME_LOCK:
        payload = default_runtime_info()
        payload.update(_RUNTIME_INFO)
        payload.update({key: value for key, value in fields.items() if value is not None})
        _RUNTIME_INFO.clear()
        _RUNTIME_INFO.update(payload)
        write_runtime_state(payload)
        return dict(payload)


def get_runtime_info(*, read_from_disk: bool = False) -> dict[str, Any]:
    with _RUNTIME_LOCK:
        if read_from_disk:
            disk_payload = read_runtime_state()
            if disk_payload:
                merged = default_runtime_info()
                merged.update(disk_payload)
                return merged
        payload = default_runtime_info()
        payload.update(_RUNTIME_INFO)
        return payload


def clear_runtime_info() -> None:
    with _RUNTIME_LOCK:
        _RUNTIME_INFO.clear()
        try:
            APP_RUNTIME_STATE_PATH.unlink(missing_ok=True)
        except OSError:
            pass


def write_runtime_state(payload: dict[str, Any]) -> Path:
    _ensure_runtime_dir()
    serializable = dict(payload)
    APP_RUNTIME_STATE_PATH.write_text(
        json.dumps(serializable, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return APP_RUNTIME_STATE_PATH


def read_runtime_state() -> dict[str, Any]:
    if not APP_RUNTIME_STATE_PATH.exists():
        return {}
    try:
        data = json.loads(APP_RUNTIME_STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def runtime_url_from_info(info: dict[str, Any] | None = None) -> str | None:
    payload = info or get_runtime_info()
    url = str(payload.get("url") or "").strip()
    if url:
        return url
    host = str(payload.get("host") or DEFAULT_HOST).strip() or DEFAULT_HOST
    port = payload.get("port")
    try:
        port_value = int(port)
    except (TypeError, ValueError):
        return None
    if port_value <= 0:
        return None
    return f"http://{host}:{port_value}"


def register_shutdown_callback(callback: Callable[[], None] | None) -> None:
    global _SHUTDOWN_CALLBACK
    with _RUNTIME_LOCK:
        _SHUTDOWN_CALLBACK = callback


def request_runtime_shutdown() -> bool:
    with _RUNTIME_LOCK:
        callback = _SHUTDOWN_CALLBACK
    if callback is None:
        return False
    threading.Thread(target=callback, daemon=True).start()
    return True


class SingleInstanceLock:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or APP_INSTANCE_LOCK_PATH
        self._handle: Any | None = None
        self._locked = False

    def acquire(self) -> bool:
        if msvcrt is None:  # pragma: no cover
            raise RuntimeError("Single-instance locking is only supported on Windows.")
        _ensure_runtime_dir()
        handle = open(self.path, "a+b")  # noqa: SIM115
        handle.seek(0)
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            handle.close()
            return False
        handle.seek(0)
        handle.write(b"1")
        handle.flush()
        self._handle = handle
        self._locked = True
        return True

    def release(self) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            handle.seek(0)
            if self._locked and msvcrt is not None:
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        finally:
            handle.close()
            self._handle = None
            self._locked = False

    def __enter__(self) -> "SingleInstanceLock":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

