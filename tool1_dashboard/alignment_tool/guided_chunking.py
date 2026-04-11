from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .align_with_mfa import run_mfa_alignment
from .models import ScriptDocument, ScriptWord, WordTiming
from .parse_alignment import RawAlignedWord, map_raw_words_to_script
from .runtime import ensure_dir, resolve_ffmpeg_path


@dataclass(frozen=True)
class GuidedChunkPlan:
    index: int
    core_start: int
    core_end: int
    align_start: int
    align_end: int


def build_guided_chunks(
    script_document: ScriptDocument,
    *,
    target_words: int = 450,
    hard_max: int = 650,
    overlap_words: int = 12,
) -> list[GuidedChunkPlan]:
    if not script_document.words:
        return []
    paragraph_ranges = script_document.paragraph_word_ranges or [(0, len(script_document.words))]
    core_ranges: list[tuple[int, int]] = []
    current_start: int | None = None
    current_end: int | None = None

    def flush() -> None:
        nonlocal current_start, current_end
        if current_start is not None and current_end is not None and current_end > current_start:
            core_ranges.append((current_start, current_end))
        current_start = None
        current_end = None

    for paragraph_start, paragraph_end in paragraph_ranges:
        paragraph_words = paragraph_end - paragraph_start
        if paragraph_words <= 0:
            continue
        if paragraph_words > hard_max:
            flush()
            cursor = paragraph_start
            while cursor < paragraph_end:
                next_cursor = min(paragraph_end, cursor + target_words)
                if next_cursor - cursor > hard_max:
                    next_cursor = cursor + hard_max
                core_ranges.append((cursor, next_cursor))
                cursor = next_cursor
            continue
        if current_start is None:
            current_start = paragraph_start
            current_end = paragraph_end
            continue
        candidate_count = paragraph_end - current_start
        current_count = current_end - current_start
        if candidate_count > hard_max or (current_count >= target_words and candidate_count > target_words):
            flush()
            current_start = paragraph_start
            current_end = paragraph_end
            continue
        current_end = paragraph_end
    flush()

    if not core_ranges:
        core_ranges = [(0, len(script_document.words))]

    plans: list[GuidedChunkPlan] = []
    for index, (core_start, core_end) in enumerate(core_ranges):
        plans.append(
            GuidedChunkPlan(
                index=index,
                core_start=core_start,
                core_end=core_end,
                align_start=max(0, core_start - overlap_words),
                align_end=min(len(script_document.words), core_end + overlap_words),
            )
        )
    return plans


def build_script_subset(
    script_document: ScriptDocument,
    start_index: int,
    end_index: int,
) -> ScriptDocument:
    selected_words = script_document.words[start_index:end_index]
    if not selected_words:
        raise ValueError("Cannot build an empty script subset.")
    canonical_parts: list[str] = []
    local_words: list[ScriptWord] = []
    cursor = 0
    for local_index, original in enumerate(selected_words):
        if canonical_parts:
            canonical_parts.append(" ")
            cursor += 1
        word_text = original.word
        text_start = cursor
        text_end = cursor + len(word_text)
        local_words.append(
            ScriptWord(
                index=local_index,
                word=word_text,
                normalized=original.normalized,
                text_start=text_start,
                text_end=text_end,
                render_start=text_start,
                render_end=text_end,
                leading_text="",
                trailing_text="",
            )
        )
        canonical_parts.append(word_text)
        cursor = text_end
    canonical_text = "".join(canonical_parts)
    return ScriptDocument(
        source_text=canonical_text,
        canonical_text=canonical_text,
        alignment_text=" ".join(word.word for word in local_words),
        words=local_words,
        paragraphs=[canonical_text],
        paragraph_word_ranges=[(0, len(local_words))],
        language_code=script_document.language_code,
    )


def stitch_chunk_word_timings(
    script_document: ScriptDocument,
    plan: GuidedChunkPlan,
    local_words: list[WordTiming],
    time_offset: float,
) -> dict[int, WordTiming]:
    stitched: dict[int, WordTiming] = {}
    for local_index, local_word in enumerate(local_words):
        global_index = plan.align_start + local_index
        if global_index < plan.core_start or global_index >= plan.core_end:
            continue
        original = script_document.words[global_index]
        stitched[global_index] = WordTiming(
            word=original.word,
            start=max(0.0, time_offset + local_word.start),
            end=max(0.0, time_offset + local_word.end),
            index=global_index,
            confidence=local_word.confidence,
            source=local_word.source,
            approximate=local_word.approximate,
            normalized=original.normalized,
            text_start=original.text_start,
            text_end=original.text_end,
            render_start=original.render_start,
            render_end=original.render_end,
            leading_text=original.leading_text,
            trailing_text=original.trailing_text,
        )
    return stitched


def _extract_audio_window(
    source_path: Path,
    destination_path: Path,
    start_seconds: float,
    end_seconds: float,
) -> None:
    ensure_dir(destination_path.parent)
    ffmpeg_path = resolve_ffmpeg_path()
    start = max(0.0, float(start_seconds))
    end = max(start + 0.1, float(end_seconds))
    duration = max(0.1, end - start)
    command = [
        ffmpeg_path,
        "-y",
        "-ss",
        f"{start:.3f}",
        "-t",
        f"{duration:.3f}",
        "-i",
        str(source_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(destination_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "ffmpeg failed").strip()
        raise RuntimeError(f"Chunk audio extraction failed: {detail}")


def _run_chunked_mfa_with_windows(
    *,
    normalized_audio_path: Path,
    script_document: ScriptDocument,
    language_profile,
    temp_dir: Path,
    plans: list[GuidedChunkPlan],
    window_builder: Callable[[GuidedChunkPlan], tuple[float, float]],
    logger: Callable[[str], None] | None = None,
    warning_message: str | None = None,
) -> tuple[list[WordTiming], list[str], dict[str, int], dict[str, int], int]:
    chunk_root = ensure_dir(temp_dir / "guided_chunks")
    stitched: dict[int, WordTiming] = {}
    warnings: list[str] = []
    diagnostics = {
        "mismatch_blocks": 0,
        "isolated_mismatch_blocks": 0,
        "clustered_mismatch_blocks": 0,
        "merge_rescues": 0,
        "split_rescues": 0,
        "fuzzy_rescues": 0,
        "approximate_blocks": 0,
    }
    summary = {
        "mismatch_count": 0,
        "approximate_word_count": 0,
        "dropped_word_count": 0,
    }

    for plan in plans:
        window_start, window_end = window_builder(plan)
        if window_end <= window_start:
            window_end = window_start + 0.5
        chunk_audio = chunk_root / f"chunk_{plan.index:03d}.wav"
        _extract_audio_window(normalized_audio_path, chunk_audio, window_start, window_end)
        chunk_document = build_script_subset(script_document, plan.align_start, plan.align_end)
        raw_words, _ = run_mfa_alignment(
            chunk_audio,
            chunk_document,
            language_profile,
            chunk_root / f"chunk_{plan.index:03d}_temp",
            audio_duration_seconds=window_end - window_start,
            logger=logger,
        )
        local_words, mismatch_count, approximate_count, dropped_count, local_warnings, local_diagnostics = map_raw_words_to_script(
            raw_words,
            chunk_document,
            source="mfa_chunk",
            audio_duration=window_end - window_start,
            language_code=script_document.language_code,
        )
        summary["mismatch_count"] += mismatch_count
        summary["approximate_word_count"] += approximate_count
        summary["dropped_word_count"] += dropped_count
        for key, value in local_diagnostics.items():
            diagnostics[key] = diagnostics.get(key, 0) + int(value)
        warnings.extend(f"Chunk {plan.index + 1}: {message}" for message in local_warnings)
        stitched.update(stitch_chunk_word_timings(script_document, plan, local_words, window_start))

    if len(stitched) != len(script_document.words):
        missing = len(script_document.words) - len(stitched)
        raise RuntimeError(f"Chunked MFA left {missing} script words without timings.")

    if warning_message:
        warnings.append(warning_message)
    ordered = [stitched[index] for index in range(len(script_document.words))]
    return ordered, warnings, diagnostics, summary, len(plans)


def run_guided_chunked_mfa(
    *,
    normalized_audio_path: Path,
    script_document: ScriptDocument,
    language_profile,
    temp_dir: Path,
    audio_duration_seconds: float,
    guidance_raw_words: list[RawAlignedWord],
    logger: Callable[[str], None] | None = None,
) -> tuple[list[WordTiming], list[str], dict[str, int], dict[str, int], int]:
    guidance_mapped, _, _, guidance_dropped, guidance_warnings, _ = map_raw_words_to_script(
        guidance_raw_words,
        script_document,
        source="whisperx_guidance",
        audio_duration=audio_duration_seconds,
        language_code=script_document.language_code,
    )
    if guidance_dropped > 0:
        raise RuntimeError("Guided chunking could not derive coarse timings for the full script.")

    plans = build_guided_chunks(script_document)
    if not plans:
        raise RuntimeError("Guided chunking could not build any script chunks.")

    def window_builder(plan: GuidedChunkPlan) -> tuple[float, float]:
        window_start = max(0.0, guidance_mapped[plan.align_start].start - 1.0)
        window_end = min(audio_duration_seconds, guidance_mapped[plan.align_end - 1].end + 1.0)
        if window_end <= window_start:
            window_end = min(audio_duration_seconds, window_start + 0.5)
        return window_start, window_end

    return _run_chunked_mfa_with_windows(
        normalized_audio_path=normalized_audio_path,
        script_document=script_document,
        language_profile=language_profile,
        temp_dir=temp_dir,
        plans=plans,
        window_builder=window_builder,
        logger=logger,
        warning_message=(
            "Guided chunking used a whole-audio WhisperX pass to derive chunk windows."
            if guidance_warnings
            else None
        ),
    )


def run_estimated_chunked_mfa(
    *,
    normalized_audio_path: Path,
    script_document: ScriptDocument,
    language_profile,
    temp_dir: Path,
    audio_duration_seconds: float,
    logger: Callable[[str], None] | None = None,
) -> tuple[list[WordTiming], list[str], dict[str, int], dict[str, int], int]:
    if not script_document.words:
        raise RuntimeError("Estimated chunked MFA needs at least one script word.")

    plans = build_guided_chunks(script_document)
    if not plans:
        raise RuntimeError("Estimated chunked MFA could not build any script chunks.")

    total_words = len(script_document.words)

    def window_builder(plan: GuidedChunkPlan) -> tuple[float, float]:
        estimated_start = audio_duration_seconds * (plan.align_start / total_words)
        estimated_end = audio_duration_seconds * (plan.align_end / total_words)
        estimated_span = max(1.0, estimated_end - estimated_start)
        padding = max(8.0, estimated_span * 0.25)
        return (
            max(0.0, estimated_start - padding),
            min(audio_duration_seconds, estimated_end + padding),
        )

    return _run_chunked_mfa_with_windows(
        normalized_audio_path=normalized_audio_path,
        script_document=script_document,
        language_profile=language_profile,
        temp_dir=temp_dir,
        plans=plans,
        window_builder=window_builder,
        logger=logger,
        warning_message="Estimated chunked MFA used proportional audio windows because WhisperX guidance was unavailable.",
    )
