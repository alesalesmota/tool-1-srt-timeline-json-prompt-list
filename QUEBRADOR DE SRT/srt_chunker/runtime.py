from __future__ import annotations

import re
import socket
import time
from pathlib import Path

from .config import DEFAULT_HOST, OUTPUT_ROOT, UI_DIR


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def project_dirs() -> None:
    ensure_dir(OUTPUT_ROOT)
    ensure_dir(UI_DIR)


def sanitize_name(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return cleaned or "run"


def make_run_id(seed: str) -> str:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{sanitize_name(seed)[:40]}"


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
