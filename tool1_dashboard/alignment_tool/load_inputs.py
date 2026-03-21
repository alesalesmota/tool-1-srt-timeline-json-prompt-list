from __future__ import annotations

from pathlib import Path

SUPPORTED_AUDIO_SUFFIXES = {".wav", ".mp3"}
SUPPORTED_SCRIPT_SUFFIXES = {".txt", ".docx"}


def validate_audio_file(path: Path) -> Path:
    if not path.exists():
        raise FileNotFoundError(f"Audio file not found: {path}")
    if not path.is_file():
        raise ValueError(f"Audio path is not a file: {path}")
    if path.suffix.lower() not in SUPPORTED_AUDIO_SUFFIXES:
        raise ValueError("Unsupported audio format. Use .wav or .mp3.")
    return path


def validate_script_file(path: Path) -> Path:
    if not path.exists():
        raise FileNotFoundError(f"Script file not found: {path}")
    if not path.is_file():
        raise ValueError(f"Script path is not a file: {path}")
    if path.suffix.lower() not in SUPPORTED_SCRIPT_SUFFIXES:
        raise ValueError("Unsupported script format. Use .txt or .docx.")
    return path

