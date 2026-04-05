from __future__ import annotations

from dataclasses import replace
import re

from .models import ScriptDocument, SegmentationConfig, SubtitleSegment, WordTiming

STRONG_BREAKS = ".?!;:"
SOFT_BREAKS = ","


def _render_text(script_document: ScriptDocument, words: list[WordTiming]) -> str:
    if not words:
        return ""
    start = words[0].render_start
    end = words[-1].render_end
    text = script_document.canonical_text[start:end]
    text = re.sub(r"\s*\n\s*", " ", text).strip()
    return re.sub(r"\s{2,}", " ", text)


def _break_score(left: str, right: str, limit: int) -> tuple[float, float]:
    overflow = max(0, len(left) - limit) + max(0, len(right) - limit)
    balance = abs(len(left) - len(right))
    return float(overflow), float(balance)


def _reading_cps(text: str, duration: float) -> float:
    safe_duration = max(duration, 0.01)
    return len(text.replace("\n", "")) / safe_duration


def _format_lines(text: str, config: SegmentationConfig) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return ""
    if config.max_lines_per_block <= 1 or len(text) <= config.max_chars_per_line:
        return text

    spaces = [index for index, char in enumerate(text) if char == " "]
    if not spaces:
        return text

    best_split = None
    best_score = None
    for split in spaces:
        left = text[:split].strip()
        right = text[split + 1 :].strip()
        score = _break_score(left, right, config.max_chars_per_line)
        if best_score is None or score < best_score:
            best_score = score
            best_split = (left, right)
    if best_split is None:
        return text
    return best_split[0] + "\n" + best_split[1]


def _choose_break_index(
    words: list[WordTiming],
    start_index: int,
    script_document: ScriptDocument,
    config: SegmentationConfig,
) -> int:
    last_strong = None
    last_soft = None
    for index in range(start_index, len(words)):
        candidate = words[start_index : index + 1]
        text = _render_text(script_document, candidate)
        duration = candidate[-1].end - candidate[0].start
        char_count = len(re.sub(r"\s+", " ", text))
        trailing = words[index].trailing_text
        if any(mark in trailing for mark in STRONG_BREAKS) and duration >= config.min_duration:
            last_strong = index
        elif any(mark in trailing for mark in SOFT_BREAKS) and duration >= config.min_duration:
            last_soft = index

        predicted_cps = _reading_cps(text, duration)
        if predicted_cps > (config.max_reading_cps * 1.15):
            if last_strong is not None and last_strong >= start_index:
                return last_strong + 1
            if last_soft is not None and last_soft >= start_index:
                return last_soft + 1

        if duration > config.max_duration or char_count > config.max_chars_per_block:
            if last_strong is not None and last_strong >= start_index:
                return last_strong + 1
            if last_soft is not None and last_soft >= start_index:
                return last_soft + 1
            return max(start_index + 1, index)

        if duration >= config.preferred_duration:
            if last_strong is not None and last_strong >= start_index:
                return last_strong + 1
            if last_soft is not None and last_soft >= start_index:
                return last_soft + 1
    return len(words)


def _rebuild_segment(segment: SubtitleSegment, *, start: float, end: float, text: str | None = None) -> SubtitleSegment:
    new_text = text if text is not None else segment.text
    duration = max(end - start, 0.01)
    return SubtitleSegment(
        segment_id=segment.segment_id,
        start=start,
        end=end,
        text=new_text,
        line_count=new_text.count("\n") + 1,
        char_count=len(new_text.replace("\n", "")),
        word_count=segment.word_count,
        reading_cps=_reading_cps(new_text, duration),
    )


def _extend_into_safe_gaps(segments: list[SubtitleSegment], config: SegmentationConfig) -> list[SubtitleSegment]:
    if not segments:
        return []
    extended: list[SubtitleSegment] = []
    for index, segment in enumerate(segments):
        previous_end = segments[index - 1].end if index > 0 else 0.0
        next_start = segments[index + 1].start if index + 1 < len(segments) else segment.end
        gap_before = max(0.0, segment.start - previous_end)
        gap_after = max(0.0, next_start - segment.end)
        left_extension = min(gap_before / 2.0, 0.50)
        right_extension = min(gap_after / 2.0, 0.50)
        extended.append(
            _rebuild_segment(
                segment,
                start=max(0.0, segment.start - left_extension),
                end=segment.end + right_extension,
            )
        )
    return extended


def _merge_short_neighbors(segments: list[SubtitleSegment], config: SegmentationConfig) -> list[SubtitleSegment]:
    if len(segments) < 2:
        return segments
    merged: list[SubtitleSegment] = []
    cursor = 0
    while cursor < len(segments):
        current = segments[cursor]
        current_duration = max(current.end - current.start, 0.01)
        if cursor + 1 < len(segments):
            next_segment = segments[cursor + 1]
            combined_text = _format_lines(f"{current.text} {next_segment.text}", config)
            combined_duration = max(next_segment.end - current.start, 0.01)
            combined_cps = _reading_cps(combined_text, combined_duration)
            if (
                current_duration < 1.15
                and combined_duration <= (config.max_duration + 0.75)
                and combined_cps <= max(current.reading_cps, next_segment.reading_cps, config.max_reading_cps * 1.2)
            ):
                merged.append(
                    SubtitleSegment(
                        segment_id=current.segment_id,
                        start=current.start,
                        end=next_segment.end,
                        text=combined_text,
                        line_count=combined_text.count("\n") + 1,
                        char_count=len(combined_text.replace("\n", "")),
                        word_count=current.word_count + next_segment.word_count,
                        reading_cps=combined_cps,
                    )
                )
                cursor += 2
                continue
        merged.append(current)
        cursor += 1
    return [replace(segment, segment_id=index + 1) for index, segment in enumerate(merged)]


def _optimize_segments(segments: list[SubtitleSegment], config: SegmentationConfig) -> list[SubtitleSegment]:
    optimized = _extend_into_safe_gaps(segments, config)
    optimized = _merge_short_neighbors(optimized, config)
    return [replace(segment, segment_id=index + 1) for index, segment in enumerate(optimized)]


def segment_words(
    script_document: ScriptDocument,
    words: list[WordTiming],
    config: SegmentationConfig,
) -> tuple[list[SubtitleSegment], list[str]]:
    segments: list[SubtitleSegment] = []
    warnings: list[str] = []
    cursor = 0
    segment_id = 1
    while cursor < len(words):
        next_cursor = _choose_break_index(words, cursor, script_document, config)
        if next_cursor <= cursor:
            next_cursor = cursor + 1
        segment_words_slice = words[cursor:next_cursor]
        raw_text = _render_text(script_document, segment_words_slice)
        formatted_text = _format_lines(raw_text, config)
        duration = max(segment_words_slice[-1].end - segment_words_slice[0].start, 0.01)
        reading_cps = _reading_cps(formatted_text, duration)
        segments.append(
            SubtitleSegment(
                segment_id=segment_id,
                start=segment_words_slice[0].start,
                end=segment_words_slice[-1].end,
                text=formatted_text,
                line_count=formatted_text.count("\n") + 1,
                char_count=len(formatted_text.replace("\n", "")),
                word_count=len(segment_words_slice),
                reading_cps=reading_cps,
            )
        )
        segment_id += 1
        cursor = next_cursor

    optimized = _optimize_segments(segments, config)
    for segment in optimized:
        if segment.reading_cps > config.max_reading_cps:
            warnings.append(
                f"Segment {segment.segment_id} exceeds preferred reading speed ({segment.reading_cps:.1f} chars/s)."
            )
    return optimized, warnings
