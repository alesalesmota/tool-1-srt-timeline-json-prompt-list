from __future__ import annotations

from .models import AlignmentReport, NormalizedAudioInfo, ScriptDocument, SubtitleSegment, WordTiming


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
) -> AlignmentReport:
    average_segment_duration = 0.0
    if segments:
        average_segment_duration = sum(segment.end - segment.start for segment in segments) / len(segments)
    status = "success"
    if warnings:
        status = "success_with_warnings"
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
    )
