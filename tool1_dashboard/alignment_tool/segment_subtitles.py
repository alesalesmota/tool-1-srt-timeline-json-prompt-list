from __future__ import annotations

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


def _choose_break_index(words: list[WordTiming], start_index: int, script_document: ScriptDocument, config: SegmentationConfig) -> int:
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
        reading_cps = len(formatted_text.replace("\n", "")) / duration
        if reading_cps > config.max_reading_cps:
            warnings.append(
                f"Segment {segment_id} exceeds preferred reading speed ({reading_cps:.1f} chars/s)."
            )
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
    return segments, warnings

