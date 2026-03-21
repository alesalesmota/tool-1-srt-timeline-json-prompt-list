from __future__ import annotations

import hashlib
import json
import re
import socket
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DEFAULT_HOST


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def ensure_parent(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def safe_slug(value: str, fallback: str = "job") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value).strip("-").lower()
    return cleaned or fallback


def make_job_id(title: str) -> str:
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{safe_slug(title, 'video')[:40]}"


def safe_filename(name: str, fallback: str) -> str:
    suffix = Path(name).suffix
    stem = Path(name).stem if Path(name).stem else fallback
    return f"{safe_slug(stem, fallback)}{suffix}"


def write_json(path: Path, payload: Any) -> Path:
    ensure_parent(path)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def write_jsonl(path: Path, payloads: list[Any]) -> Path:
    ensure_parent(path)
    lines = [json.dumps(payload, ensure_ascii=False) for payload in payloads]
    path.write_text("\n".join(lines).strip() + ("\n" if lines else ""), encoding="utf-8")
    return path


def read_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [json.loads(line) for line in lines]


def write_text(path: Path, content: str) -> Path:
    ensure_parent(path)
    path.write_text(content, encoding="utf-8")
    return path


def read_text(path: Path, default: str = "") -> str:
    if not path.exists():
        return default
    return path.read_text(encoding="utf-8", errors="replace")


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def clamp_preview(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 1] + "…"


def strip_json_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```") and stripped.endswith("```"):
        stripped = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    return stripped.strip()


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
