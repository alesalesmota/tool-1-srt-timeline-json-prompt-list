from __future__ import annotations

import subprocess
import wave
from pathlib import Path
from typing import Callable

from .config import DEFAULT_SAMPLE_RATE
from .models import NormalizedAudioInfo
from .runtime import ensure_dir, resolve_ffmpeg_path


def normalize_audio_file(
    source_path: Path,
    destination_path: Path,
    sample_rate: int = DEFAULT_SAMPLE_RATE,
    logger: Callable[[str], None] | None = None,
) -> NormalizedAudioInfo:
    ensure_dir(destination_path.parent)
    ffmpeg_path = resolve_ffmpeg_path()
    if logger is not None:
        logger(f"Normalizing audio with ffmpeg -> {destination_path.name}")
    command = [
        ffmpeg_path,
        "-y",
        "-i",
        str(source_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-c:a",
        "pcm_s16le",
        str(destination_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "ffmpeg failed").strip()
        raise RuntimeError(f"Audio normalization failed: {detail}")

    with wave.open(str(destination_path), "rb") as handle:
        channels = handle.getnchannels()
        frame_rate = handle.getframerate()
        frame_count = handle.getnframes()
        duration = frame_count / float(frame_rate) if frame_rate else 0.0

    return NormalizedAudioInfo(
        path=destination_path,
        duration_seconds=duration,
        sample_rate=frame_rate,
        channels=channels,
    )
