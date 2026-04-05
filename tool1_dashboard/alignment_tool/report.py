from __future__ import annotations

import math

from .models import AlignmentReport, NormalizedAudioInfo, ScriptDocument, SubtitleSegment, WordTiming


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = min(len(ordered) - 1, max(0, math.ceil((percentile / 100.0) * len(ordered)) - 1))
    return ordered[rank]


def build_alignment_report(
    engine: str,
    fallback_used: bool,
    fallback_reason: str | None,
    audio_info: NormalizedAudioInfo,
    script_document: ScriptDocument,
    words: list[WordTiming],
    segments: list[SubtitleSegment],
    warnings: list[str],
    approximate_word_count: int,
    mismatch_count: int,
    dropped_word_count: int,
    *,
    strategy: str,
    chunk_count: int,
    max_reading_cps_target: float,
    mapping_diagnostics: dict[str, int] | None = None,
    candidate_metrics: list[dict[str, object]] | None = None,
) -> AlignmentReport:
    average_segment_duration = 0.0
    if segments:
        average_segment_duration = sum(segment.end - segment.start for segment in segments) / len(segments)
    status = "success"
    if warnings:
        status = "success_with_warnings"
    reading_speed_warning_count = sum(1 for segment in segments if segment.reading_cps > max_reading_cps_target)
    max_reading_cps = max((segment.reading_cps for segment in segments), default=0.0)
    p95_reading_cps = _percentile([segment.reading_cps for segment in segments], 95)
    mismatch_warning_count = sum(1 for warning in warnings if "mismatch" in warning.lower())
    warning_summary = {
        "total": len(warnings),
        "mismatch": mismatch_warning_count,
        "reading_speed": reading_speed_warning_count,
        "other": max(0, len(warnings) - mismatch_warning_count - reading_speed_warning_count),
    }
    if mapping_diagnostics:
        warning_summary.update(mapping_diagnostics)
    return AlignmentReport(
        engine=engine,
        fallback_used=fallback_used,
        fallback_reason=fallback_reason,
        audio_duration=audio_info.duration_seconds,
        normalized_audio_properties={
            "sample_rate": audio_info.sample_rate,
            "channels": audio_info.channels,
        },
        script_word_count=len(script_document.words),
        aligned_word_count=len(words),
        approximate_word_count=approximate_word_count,
        dropped_word_count=dropped_word_count,
        mismatch_count=mismatch_count,
        segment_count=len(segments),
        average_segment_duration=average_segment_duration,
        warnings=warnings,
        status=status,
        strategy=strategy,
        warning_summary=warning_summary,
        reading_speed_warning_count=reading_speed_warning_count,
        max_reading_cps=max_reading_cps,
        p95_reading_cps=p95_reading_cps,
        candidate_metrics=list(candidate_metrics or []),
        chunk_count=chunk_count,
    )
