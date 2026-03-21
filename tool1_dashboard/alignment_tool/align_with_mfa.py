from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable

from .config import LanguageProfile, resolve_mfa_resources
from .mfa_resources import run_mfa_command
from .models import ScriptDocument
from .parse_alignment import RawAlignedWord
from .runtime import resolve_mfa_command

LONG_FORM_MFA_MAX_DURATION_SECONDS = int(os.environ.get("ALIGNMENT_MFA_MAX_DURATION_SECONDS", "7200"))
LONG_FORM_MFA_MAX_WORDS = int(os.environ.get("ALIGNMENT_MFA_MAX_WORDS", "15000"))


def mfa_available() -> bool:
    return resolve_mfa_command() is not None


def _parse_text_value(raw_value: str) -> str:
    value = raw_value.split("=", 1)[-1].strip()
    if value.startswith('"') and value.endswith('"') and len(value) >= 2:
        value = value[1:-1]
    return value.replace('""', '"')


def parse_textgrid_words(path: Path) -> list[RawAlignedWord]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    current_tier = ""
    words: list[RawAlignedWord] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if line.startswith("name = "):
            current_tier = _parse_text_value(line)
        elif current_tier == "words" and line.startswith("intervals ["):
            if index + 3 >= len(lines):
                break
            start_line = lines[index + 1].strip()
            end_line = lines[index + 2].strip()
            text_line = lines[index + 3].strip()
            try:
                start = float(start_line.split("=", 1)[-1].strip())
                end = float(end_line.split("=", 1)[-1].strip())
            except ValueError:
                index += 1
                continue
            text = _parse_text_value(text_line)
            if text and text not in {"sp", "sil", "<eps>"}:
                words.append(RawAlignedWord(word=text, start=start, end=end))
            index += 3
        index += 1
    return words


def _long_form_skip_reason(script_word_count: int, audio_duration_seconds: float | None) -> str | None:
    reasons: list[str] = []
    if audio_duration_seconds is not None and audio_duration_seconds > LONG_FORM_MFA_MAX_DURATION_SECONDS:
        reasons.append(f"audio is {audio_duration_seconds / 60:.1f} minutes long")
    if script_word_count > LONG_FORM_MFA_MAX_WORDS:
        reasons.append(f"script has {script_word_count} words")
    if not reasons:
        return None
    return (
        "MFA skipped: this narration is too large for the app's current single-pass MFA mode "
        f"({', '.join(reasons)}). The app will continue with WhisperX."
    )


def _looks_like_no_alignments_error(raw_output: str) -> bool:
    text = raw_output.lower()
    return "noalignmentserror" in text or "there were no successful alignments" in text


def _summarize_mfa_failure(raw_output: str) -> str:
    if _looks_like_no_alignments_error(raw_output):
        return (
            "MFA could not align this audio/script pair. In this tool that usually means the narration "
            "is too hard to align as one uninterrupted block, so the app will continue with WhisperX."
        )
    cleaned = " ".join(raw_output.split())
    if len(cleaned) > 500:
        cleaned = cleaned[:497] + "..."
    return cleaned or "MFA failed."


def _run_mfa_attempt(command: list[str]) -> subprocess.CompletedProcess[str]:
    return run_mfa_command(command)


def run_mfa_alignment(
    normalized_audio_path: Path,
    script_document: ScriptDocument,
    language_profile: LanguageProfile,
    temp_dir: Path,
    audio_duration_seconds: float | None = None,
    logger: Callable[[str], None] | None = None,
) -> tuple[list[RawAlignedWord], list[str]]:
    if resolve_mfa_command() is None:
        raise RuntimeError("Montreal Forced Aligner CLI is not installed or not on PATH.")

    skip_reason = _long_form_skip_reason(len(script_document.words), audio_duration_seconds)
    if skip_reason:
        raise RuntimeError(skip_reason)

    resources = resolve_mfa_resources(language_profile)
    corpus_dir = temp_dir / "mfa_corpus"
    output_dir = temp_dir / "mfa_output"
    corpus_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    corpus_audio = corpus_dir / "input.wav"
    corpus_lab = corpus_dir / "input.lab"
    shutil.copy2(normalized_audio_path, corpus_audio)
    corpus_lab.write_text(script_document.alignment_text, encoding="utf-8")

    base_command = [
        "align",
        str(corpus_dir),
        resources["dictionary"],
        resources["acoustic"],
        str(output_dir),
        "--single_speaker",
        "--clean",
    ]
    if logger is not None:
        logger(f"Running MFA with dictionary '{resources['dictionary']}' and acoustic model '{resources['acoustic']}'.")

    attempts = [
        ("default", []),
        ("wider beam", ["--beam", "100", "--retry_beam", "400"]),
    ]
    raw_failure_output = ""
    for index, (label, extra_args) in enumerate(attempts):
        result = _run_mfa_attempt(base_command + extra_args)
        if result.returncode == 0:
            break
        raw_failure_output = (result.stderr or result.stdout or "MFA failed").strip()
        if index == 0 and _looks_like_no_alignments_error(raw_failure_output):
            if logger is not None:
                logger("MFA could not align on the first pass. Retrying with a wider beam.")
            continue
        raise RuntimeError(_summarize_mfa_failure(raw_failure_output))
    else:
        raise RuntimeError(_summarize_mfa_failure(raw_failure_output))

    textgrid_path = next(output_dir.glob("**/*.TextGrid"), None)
    if textgrid_path is None:
        raise RuntimeError("MFA did not produce a TextGrid output.")
    raw_words = parse_textgrid_words(textgrid_path)
    if not raw_words:
        raise RuntimeError("MFA output did not contain any word timings.")
    return raw_words, []
