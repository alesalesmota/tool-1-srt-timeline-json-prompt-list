from __future__ import annotations

from .models import ChunkConfig, SubtitleChunk, SubtitleCue


def _duration_seconds(cues: list[SubtitleCue]) -> float:
    if not cues:
        return 0.0
    return round((cues[-1].end_ms - cues[0].start_ms) / 1000.0, 3)


def _make_chunk(chunk_id: int, cues: list[SubtitleCue], warning: str | None = None) -> SubtitleChunk:
    return SubtitleChunk(
        chunk_id=chunk_id,
        cues=list(cues),
        word_count=sum(cue.word_count for cue in cues),
        char_count=sum(cue.char_count for cue in cues),
        duration_seconds=_duration_seconds(cues),
        start_ms=cues[0].start_ms,
        end_ms=cues[-1].end_ms,
        original_index_start=cues[0].index,
        original_index_end=cues[-1].index,
        warning=warning,
    )


def _would_exceed(cues: list[SubtitleCue], next_cue: SubtitleCue, config: ChunkConfig) -> bool:
    proposed = cues + [next_cue]
    if config.max_entries > 0 and len(proposed) > config.max_entries:
        return True
    if config.max_words > 0 and sum(cue.word_count for cue in proposed) > config.max_words:
        return True
    if config.max_chars > 0 and sum(cue.char_count for cue in proposed) > config.max_chars:
        return True
    if config.max_duration_seconds > 0 and _duration_seconds(proposed) > config.max_duration_seconds:
        return True
    return False


def _single_cue_warning(cue: SubtitleCue, config: ChunkConfig) -> str | None:
    issues: list[str] = []
    if config.max_entries > 0 and 1 > config.max_entries:
        issues.append("entry count")
    if config.max_words > 0 and cue.word_count > config.max_words:
        issues.append("word limit")
    if config.max_chars > 0 and cue.char_count > config.max_chars:
        issues.append("character limit")
    if config.max_duration_seconds > 0 and (cue.duration_ms / 1000.0) > config.max_duration_seconds:
        issues.append("duration limit")
    if not issues:
        return None
    issue_text = ", ".join(issues)
    return f"Cue {cue.index} is larger than the configured {issue_text} and was kept alone."


def chunk_cues(cues: list[SubtitleCue], config: ChunkConfig) -> tuple[list[SubtitleChunk], list[str]]:
    if not cues:
        raise ValueError("No subtitle cues were found in the SRT file.")

    chunks: list[SubtitleChunk] = []
    warnings: list[str] = []
    current: list[SubtitleCue] = []

    for cue in cues:
        if current and _would_exceed(current, cue, config):
            chunks.append(_make_chunk(len(chunks) + 1, current))
            current = []

        current.append(cue)
        warning = _single_cue_warning(cue, config)
        if warning:
            warnings.append(warning)
            if len(current) == 1:
                chunks.append(_make_chunk(len(chunks) + 1, current, warning=warning))
                current = []

    if current:
        chunks.append(_make_chunk(len(chunks) + 1, current))

    return chunks, warnings
