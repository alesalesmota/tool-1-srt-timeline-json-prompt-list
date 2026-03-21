from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Callable

from .config import LanguageProfile
from .parse_alignment import RawAlignedWord
from .runtime import resolve_whisperx_compute_type, resolve_whisperx_device


def whisperx_available() -> bool:
    return importlib.util.find_spec("whisperx") is not None


def run_whisperx_alignment(
    normalized_audio_path: Path,
    language_profile: LanguageProfile,
    model_name: str,
    logger: Callable[[str], None] | None = None,
) -> tuple[list[RawAlignedWord], list[str]]:
    if not whisperx_available():
        raise RuntimeError("WhisperX is not installed in this environment.")

    import whisperx

    device = resolve_whisperx_device()
    compute_type = resolve_whisperx_compute_type(device)
    if logger is not None:
        logger(f"Running WhisperX model '{model_name}' on {device} ({compute_type}).")

    model = whisperx.load_model(model_name, device=device, compute_type=compute_type, language=language_profile.whisperx_code)
    audio = whisperx.load_audio(str(normalized_audio_path))
    transcription = model.transcribe(audio, language=language_profile.whisperx_code)
    segments = transcription.get("segments") or []
    if not segments:
        raise RuntimeError("WhisperX transcription returned no segments.")

    align_model, metadata = whisperx.load_align_model(language_code=language_profile.whisperx_code, device=device)
    aligned = whisperx.align(
        segments,
        align_model,
        metadata,
        audio,
        device,
        return_char_alignments=False,
    )
    raw_words: list[RawAlignedWord] = []
    for segment in aligned.get("segments", []):
        for word in segment.get("words", []):
            text = (word.get("word") or "").strip()
            start = word.get("start")
            end = word.get("end")
            if not text or start is None or end is None:
                continue
            raw_words.append(
                RawAlignedWord(
                    word=text,
                    start=float(start),
                    end=float(end),
                    confidence=word.get("score"),
                )
            )
    if not raw_words:
        raise RuntimeError("WhisperX alignment produced no word timings.")
    return raw_words, []
