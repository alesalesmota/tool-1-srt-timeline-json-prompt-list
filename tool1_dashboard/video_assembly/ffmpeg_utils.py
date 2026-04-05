from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .exceptions import CommandExecutionError


def run_command(
    command: list[str],
    description: str,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise CommandExecutionError(description, command, result.stdout, result.stderr)
    return result


def ffprobe_json(path: Path) -> dict:
    result = run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_streams",
            "-show_format",
            str(path),
        ],
        f"ffprobe {path.name}",
    )
    return json.loads(result.stdout or "{}")


def probe_duration(path: Path) -> float | None:
    data = ffprobe_json(path)
    format_section = data.get("format", {})
    raw_duration = format_section.get("duration")
    if raw_duration is None:
        for stream in data.get("streams", []):
            if stream.get("codec_type") == "video" and stream.get("duration") is not None:
                raw_duration = stream.get("duration")
                break
    if raw_duration is None:
        return None
    return float(raw_duration)
