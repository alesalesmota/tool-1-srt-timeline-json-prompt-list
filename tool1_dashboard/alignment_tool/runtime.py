from __future__ import annotations

import concurrent.futures
import importlib.util
import os
import re
import shutil
import socket
import time
import warnings
from pathlib import Path
from typing import Callable

from .config import DEFAULT_HOST, FRONTEND_DIR, LOG_ROOT, OUTPUT_ROOT, PROJECT_ROOT, TEMP_ROOT

warnings.filterwarnings(
    "ignore",
    message="pkg_resources is deprecated as an API.*",
    category=UserWarning,
)

try:
    import ctranslate2
except Exception:
    ctranslate2 = None

try:
    import torch
except Exception:
    torch = None

FFMPEG_NAME = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
FFMPEG_CANDIDATES = (
    PROJECT_ROOT / "backend" / "tools" / "ffmpeg" / "bin" / FFMPEG_NAME,
    PROJECT_ROOT / "tools" / "ffmpeg" / "bin" / FFMPEG_NAME,
)
MFA_COMMAND_CANDIDATES = (
    PROJECT_ROOT / "alignment_tool" / "tools" / "mfa.cmd",
    PROJECT_ROOT / "alignment_tool" / "tools" / "mfa.bat",
)


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def project_dirs() -> None:
    ensure_dir(OUTPUT_ROOT)
    ensure_dir(TEMP_ROOT)
    ensure_dir(LOG_ROOT)
    ensure_dir(FRONTEND_DIR)


def resolve_ffmpeg_path() -> str:
    for candidate in FFMPEG_CANDIDATES:
        if candidate.is_file():
            return str(candidate)
    located = shutil.which("ffmpeg")
    return located or "ffmpeg"


def module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def command_available(name: str) -> bool:
    return shutil.which(name) is not None


def resolve_mfa_command() -> str | None:
    configured = os.environ.get("ALIGNMENT_MFA_COMMAND")
    if configured:
        configured_path = Path(configured)
        if configured_path.exists():
            return str(configured_path)
        located = shutil.which(configured)
        if located:
            return located
    for candidate in MFA_COMMAND_CANDIDATES:
        if candidate.is_file():
            return str(candidate)
    return shutil.which("mfa")


def sanitize_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return cleaned or "run"


def make_run_id(seed: str) -> str:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{sanitize_name(seed)[:32]}"


_log_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)


def log_event(message: str, collector: Callable[[str], None] | None = None) -> None:
    timestamp = time.strftime("%H:%M:%S")
    line = f"[{timestamp}] {message}"
    if collector is not None:
        collector(line)

    def _write_log() -> None:
        try:
            ensure_dir(LOG_ROOT)
            with (LOG_ROOT / "alignment_tool.log").open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except Exception:
            pass

    _log_executor.submit(_write_log)


def write_run_log(path: Path, entries: list[str]) -> None:
    ensure_dir(path.parent)
    with path.open("w", encoding="utf-8") as handle:
        handle.write("\n".join(entries).strip() + "\n")


def _torch_cuda_info() -> dict[str, object]:
    info: dict[str, object] = {
        "available": False,
        "cuda_version": None,
        "device_count": 0,
        "gpu_name": None,
    }
    if torch is None:
        return info
    try:
        if not torch.cuda.is_available():
            return info
        info["available"] = True
        info["cuda_version"] = getattr(torch.version, "cuda", None)
        info["device_count"] = int(torch.cuda.device_count())
        if torch.cuda.device_count() > 0:
            info["gpu_name"] = torch.cuda.get_device_name(0)
    except Exception:
        return info
    return info


def _ctranslate2_cuda_info() -> dict[str, object]:
    info: dict[str, object] = {
        "available": False,
        "device_count": 0,
        "compute_types": [],
    }
    if ctranslate2 is None:
        return info
    try:
        device_count = int(ctranslate2.get_cuda_device_count())
    except Exception:
        return info
    if device_count <= 0:
        return info
    info["available"] = True
    info["device_count"] = device_count
    try:
        info["compute_types"] = sorted(ctranslate2.get_supported_compute_types("cuda"))
    except Exception:
        info["compute_types"] = []
    return info


def resolve_whisperx_device() -> str:
    env_device = os.environ.get("WHISPERX_DEVICE") or os.environ.get("WHISPER_DEVICE")
    if env_device:
        return env_device.strip().lower()
    return "cuda" if bool(_torch_cuda_info()["available"]) else "cpu"


def resolve_whisperx_compute_type(device: str | None = None) -> str:
    env_compute = os.environ.get("WHISPERX_COMPUTE_TYPE") or os.environ.get("WHISPER_COMPUTE_TYPE")
    if env_compute:
        return env_compute
    resolved_device = (device or resolve_whisperx_device()).lower()
    return "float16" if resolved_device == "cuda" else "int8"


def resolve_faster_whisper_device() -> str:
    env_device = os.environ.get("WHISPER_DEVICE")
    if env_device:
        return env_device.strip().lower()
    if bool(_torch_cuda_info()["available"]) or bool(_ctranslate2_cuda_info()["available"]):
        return "cuda"
    return "cpu"


def resolve_faster_whisper_compute_type(device: str | None = None) -> str:
    env_compute = os.environ.get("WHISPER_COMPUTE_TYPE")
    if env_compute:
        return env_compute
    resolved_device = (device or resolve_faster_whisper_device()).lower()
    return "float16" if resolved_device == "cuda" else "int8"


def runtime_profile() -> dict[str, object]:
    torch_info = _torch_cuda_info()
    ctranslate2_info = _ctranslate2_cuda_info()
    gpu_name = torch_info["gpu_name"] or ("CUDA device detected" if ctranslate2_info["available"] else None)
    return {
        "gpu_available": bool(torch_info["available"]) or bool(ctranslate2_info["available"]),
        "gpu_name": gpu_name,
        "torch_cuda": bool(torch_info["available"]),
        "torch_cuda_version": torch_info["cuda_version"],
        "ctranslate2_cuda": bool(ctranslate2_info["available"]),
        "ctranslate2_compute_types": ctranslate2_info["compute_types"],
        "whisperx_device": resolve_whisperx_device(),
        "whisperx_compute_type": resolve_whisperx_compute_type(),
        "faster_whisper_device": resolve_faster_whisper_device(),
        "faster_whisper_compute_type": resolve_faster_whisper_compute_type(),
        "device_label": f"GPU: {gpu_name}" if gpu_name else "CPU only",
    }


def probe_health() -> dict[str, object]:
    ffmpeg_path = resolve_ffmpeg_path()
    return {
        "ffmpeg": bool(ffmpeg_path and (Path(ffmpeg_path).is_file() or ffmpeg_path == "ffmpeg")),
        "mfa": resolve_mfa_command() is not None,
        "whisperx": module_available("whisperx"),
        "runtime": runtime_profile(),
    }


def find_free_port(preferred: int) -> int:
    port = preferred
    for _ in range(10):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((DEFAULT_HOST, port))
                return port
            except OSError:
                port += 1
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((DEFAULT_HOST, 0))
        return int(sock.getsockname()[1])
