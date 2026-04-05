from __future__ import annotations

from dataclasses import dataclass
import re

from .models import (
    ScriptDocument,
    SegmentationConfig,
    SegmentationDiagnostics,
    SegmentationResult,
    SubtitleSegment,
    WordTiming,
)
from ..translation.language_rules import normalize_alignment_token, resolve_language_rulepack

STRONG_BREAKS = ".?!;:"
TOKEN_STRIP = "\"“”‘’«»()[]{}.,;:!?"


@dataclass(frozen=True)
class _SegmentSpan:
    start_index: int
    end_index: int


@dataclass(frozen=True)
class _SegmentCandidate:
    span: _SegmentSpan
    segment: SubtitleSegment
    ends_without_punctuation: int
    short_duration: int
    overflow_amount: float
    boundary_penalty: int


def _render_text(script_document: ScriptDocument, words: list[WordTiming]) -> str:
    if not words:
        return ""
    start = words[0].render_start
    end = words[-1].render_end
    text = script_document.canonical_text[start:end]
    text = re.sub(r"\s*\n\s*", " ", text).strip()
    return re.sub(r"\s{2,}", " ", text)


def _visible_char_count(text: str) -> int:
    return len(text.replace("\n", ""))


def _reading_cps(text: str, duration: float) -> float:
    safe_duration = max(duration, 0.01)
    return _visible_char_count(text) / safe_duration


def _clean_token(token: str) -> str:
    return str(token or "").strip().strip(TOKEN_STRIP).lower()


def _matches_rule_token(token: str, rules: tuple[str, ...]) -> bool:
    cleaned = _clean_token(token)
    normalized = normalize_alignment_token(cleaned)
    for rule in rules:
        rule_clean = str(rule or "").strip().lower()
        if not rule_clean:
            continue
        if rule_clean.endswith("'"):
            if cleaned.startswith(rule_clean):
                return True
            continue
        if cleaned == rule_clean or normalized == normalize_alignment_token(rule_clean):
            return True
    return False


def _is_non_breaking_abbreviation(word: WordTiming, pack) -> bool:
    token = f"{word.word}{word.trailing_text}".strip().lower()
    if token in pack.subtitle_non_breaking_abbreviations:
        return True
    return _matches_rule_token(token, pack.subtitle_non_breaking_abbreviations)


def _segment_ends_cleanly(word: WordTiming, pack) -> bool:
    trailing = str(word.trailing_text or "")
    if not any(mark in trailing for mark in STRONG_BREAKS):
        return False
    return not _is_non_breaking_abbreviation(word, pack)


def _boundary_penalty(left_token: str, right_token: str, pack) -> int:
    penalty = 0
    if _matches_rule_token(left_token, pack.subtitle_no_trailing_tokens):
        penalty += 2
    if _matches_rule_token(right_token, pack.subtitle_no_leading_tokens):
        penalty += 2
    return penalty


def _preferred_break_penalty(left_token: str, left_trailing: str, pack) -> int:
    if any(mark in str(left_trailing or "") for mark in STRONG_BREAKS):
        return 0
    if _matches_rule_token(left_token, pack.subtitle_preferred_break_tokens):
        return 0
    return 1


def _format_lines(text: str, config: SegmentationConfig, pack) -> tuple[str, bool, int]:
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "", True, 0
    if config.max_lines_per_block <= 1:
        fits = len(text) <= config.max_chars_per_line
        return text, fits, 0 if fits else 4
    if len(text) <= config.max_chars_per_line:
        return text, True, 0

    tokens = text.split(" ")
    if len(tokens) < 2:
        return text, False, 4

    best_split: tuple[str, str] | None = None
    best_score: tuple[int, int, int, int] | None = None
    best_penalty = 0
    for split_index in range(1, len(tokens)):
        left = " ".join(tokens[:split_index]).strip()
        right = " ".join(tokens[split_index:]).strip()
        overflow = max(0, len(left) - config.max_chars_per_line) + max(0, len(right) - config.max_chars_per_line)
        boundary_penalty = _boundary_penalty(tokens[split_index - 1], tokens[split_index], pack)
        preferred_penalty = _preferred_break_penalty(tokens[split_index - 1], tokens[split_index - 1][-1:], pack)
        balance = abs(len(left) - len(right))
        score = (overflow, boundary_penalty, preferred_penalty, balance)
        if best_score is None or score < best_score:
            best_score = score
            best_split = (left, right)
            best_penalty = boundary_penalty + preferred_penalty
    if best_split is None or best_score is None:
        return text, False, 4
    formatted = best_split[0] + "\n" + best_split[1]
    return formatted, best_score[0] == 0, best_penalty


def _line_break_penalty(text: str, pack) -> int:
    if "\n" not in text:
        return 0
    left, right = text.split("\n", 1)
    left_tokens = left.split()
    right_tokens = right.split()
    if not left_tokens or not right_tokens:
        return 0
    return _boundary_penalty(left_tokens[-1], right_tokens[0], pack) + _preferred_break_penalty(
        left_tokens[-1],
        left_tokens[-1][-1:],
        pack,
    )


def _segment_bounds(words: list[WordTiming], start_index: int, end_index: int, extension_cap: float = 0.75) -> tuple[float, float]:
    start_word = words[start_index]
    end_word = words[end_index]
    previous_end = words[start_index - 1].end if start_index > 0 else 0.0
    next_start = words[end_index + 1].start if end_index + 1 < len(words) else end_word.end
    gap_before = max(0.0, start_word.start - previous_end)
    gap_after = max(0.0, next_start - end_word.end)
    left_extension = min(gap_before / 2.0, extension_cap)
    right_extension = min(gap_after / 2.0, extension_cap)
    return max(0.0, start_word.start - left_extension), end_word.end + right_extension


def _target_duration(char_count: int, max_reading_cps: float) -> float:
    return char_count / max(max_reading_cps, 0.01)


def _allocate_gap(gap: float, left_need: float, right_need: float, cap: float = 0.75) -> tuple[float, float]:
    usable_gap = max(0.0, gap)
    if usable_gap <= 0.0:
        return 0.0, 0.0
    max_left = min(cap, usable_gap)
    max_right = min(cap, usable_gap)
    if left_need <= 0.0 and right_need <= 0.0:
        left_share = min(usable_gap / 2.0, max_left)
        right_share = min(usable_gap - left_share, max_right)
        return left_share, right_share

    total_need = max(0.0001, left_need + right_need)
    left_share = min(max_left, usable_gap * (left_need / total_need))
    remaining = usable_gap - left_share
    right_share = min(max_right, remaining)
    if right_need > 0.0:
        right_share = min(max_right, usable_gap * (right_need / total_need))
        remaining = usable_gap - right_share
        left_share = min(max_left, remaining)

    remaining = usable_gap - left_share - right_share
    if remaining > 0.0:
        left_residual = max(0.0, max_left - left_share)
        right_residual = max(0.0, max_right - right_share)
        if left_need >= right_need and left_residual > 0.0:
            take = min(remaining, left_residual)
            left_share += take
            remaining -= take
        if remaining > 0.0 and right_residual > 0.0:
            take = min(remaining, right_residual)
            right_share += take
    return left_share, right_share


def _build_segment_candidate(
    script_document: ScriptDocument,
    words: list[WordTiming],
    span: _SegmentSpan,
    config: SegmentationConfig,
    pack,
) -> _SegmentCandidate:
    selected_words = words[span.start_index : span.end_index + 1]
    raw_text = _render_text(script_document, selected_words)
    formatted_text, _, format_penalty = _format_lines(raw_text, config, pack)
    start, end = _segment_bounds(words, span.start_index, span.end_index)
    duration = max(end - start, 0.01)
    segment = SubtitleSegment(
        segment_id=0,
        start=start,
        end=end,
        text=formatted_text,
        line_count=formatted_text.count("\n") + 1 if formatted_text else 1,
        char_count=_visible_char_count(formatted_text),
        word_count=len(selected_words),
        reading_cps=_reading_cps(formatted_text, duration),
    )
    next_token = words[span.end_index + 1].word if span.end_index + 1 < len(words) else ""
    boundary_penalty = format_penalty + _boundary_penalty(words[span.end_index].word, next_token, pack)
    return _SegmentCandidate(
        span=span,
        segment=segment,
        ends_without_punctuation=0 if _segment_ends_cleanly(words[span.end_index], pack) else 1,
        short_duration=1 if duration < 1.2 else 0,
        overflow_amount=max(0.0, segment.reading_cps - config.max_reading_cps),
        boundary_penalty=boundary_penalty,
    )


def _candidate_generation_limit_reached(
    candidate: _SegmentCandidate,
    config: SegmentationConfig,
    clean_format: bool,
) -> bool:
    return (
        (candidate.segment.end - candidate.segment.start) > 7.0
        or candidate.segment.char_count > config.max_chars_per_block
        or candidate.segment.word_count > 24
        or not clean_format
    )


def _build_candidates_for_start(
    script_document: ScriptDocument,
    words: list[WordTiming],
    start_index: int,
    config: SegmentationConfig,
    pack,
) -> list[_SegmentCandidate]:
    candidates: list[_SegmentCandidate] = []
    fallback: _SegmentCandidate | None = None
    hard_end = min(len(words), start_index + 24)
    for end_index in range(start_index, hard_end):
        span = _SegmentSpan(start_index, end_index)
        selected_words = words[start_index : end_index + 1]
        raw_text = _render_text(script_document, selected_words)
        formatted_text, clean_format, _ = _format_lines(raw_text, config, pack)
        candidate = _build_segment_candidate(script_document, words, span, config, pack)
        if fallback is None:
            fallback = candidate
        if _candidate_generation_limit_reached(candidate, config, clean_format):
            if not candidates and fallback is not None:
                candidates.append(fallback)
            break
        if _visible_char_count(formatted_text) > config.max_chars_per_block:
            if not candidates and fallback is not None:
                candidates.append(fallback)
            break
        candidates.append(candidate)
    if not candidates and fallback is not None:
        candidates.append(fallback)
    return candidates


def _combine_score(candidate: _SegmentCandidate, suffix: tuple[float, ...]) -> tuple[float, ...]:
    return (
        float(1 if candidate.segment.reading_cps > 18.0 else 0) + suffix[0],
        max(max(candidate.segment.reading_cps, 18.0), suffix[1]),
        candidate.overflow_amount + suffix[2],
        float(candidate.ends_without_punctuation) + suffix[3],
        float(candidate.short_duration) + suffix[4],
        1.0 + suffix[5],
        float(candidate.boundary_penalty) + suffix[6],
    )


def _choose_initial_spans(
    script_document: ScriptDocument,
    words: list[WordTiming],
    config: SegmentationConfig,
    pack,
) -> list[_SegmentSpan]:
    if not words:
        return []
    candidate_map = {
        start_index: _build_candidates_for_start(script_document, words, start_index, config, pack)
        for start_index in range(len(words))
    }
    best_scores: list[tuple[float, ...] | None] = [None] * (len(words) + 1)
    best_scores[len(words)] = (0.0, 18.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    best_choices: list[_SegmentSpan | None] = [None] * len(words)
    for start_index in range(len(words) - 1, -1, -1):
        best_score: tuple[float, ...] | None = None
        best_choice: _SegmentSpan | None = None
        for candidate in candidate_map[start_index]:
            suffix = best_scores[candidate.span.end_index + 1]
            if suffix is None:
                continue
            score = _combine_score(candidate, suffix)
            if best_score is None or score < best_score:
                best_score = score
                best_choice = candidate.span
        if best_score is None or best_choice is None:
            fallback = _SegmentSpan(start_index, start_index)
            candidate = _build_segment_candidate(script_document, words, fallback, config, pack)
            best_score = _combine_score(candidate, best_scores[start_index + 1] or (0.0, 18.0, 0.0, 0.0, 0.0, 0.0, 0.0))
            best_choice = fallback
        best_scores[start_index] = best_score
        best_choices[start_index] = best_choice
    spans: list[_SegmentSpan] = []
    cursor = 0
    while cursor < len(words):
        choice = best_choices[cursor]
        if choice is None:
            choice = _SegmentSpan(cursor, cursor)
        spans.append(choice)
        cursor = choice.end_index + 1
    return spans


def _materialize_segments(
    script_document: ScriptDocument,
    words: list[WordTiming],
    spans: list[_SegmentSpan],
    config: SegmentationConfig,
    pack,
) -> list[SubtitleSegment]:
    drafts: list[dict[str, object]] = []
    for span in spans:
        candidate = _build_segment_candidate(script_document, words, span, config, pack)
        drafts.append(
            {
                "span": span,
                "text": candidate.segment.text,
                "line_count": candidate.segment.line_count,
                "char_count": candidate.segment.char_count,
                "word_count": candidate.segment.word_count,
                "raw_start": words[span.start_index].start,
                "raw_end": words[span.end_index].end,
            }
        )
    if not drafts:
        return []

    starts = [float(draft["raw_start"]) for draft in drafts]
    ends = [float(draft["raw_end"]) for draft in drafts]
    first_target = _target_duration(int(drafts[0]["char_count"]), config.max_reading_cps)
    first_duration = max(ends[0] - starts[0], 0.01)
    first_need = max(0.0, first_target - first_duration)
    starts[0] = max(0.0, starts[0] - min(starts[0], 0.75, first_need))

    for index in range(len(drafts) - 1):
        gap = max(0.0, float(drafts[index + 1]["raw_start"]) - float(drafts[index]["raw_end"]))
        left_duration = max(float(drafts[index]["raw_end"]) - float(drafts[index]["raw_start"]), 0.01)
        right_duration = max(float(drafts[index + 1]["raw_end"]) - float(drafts[index + 1]["raw_start"]), 0.01)
        left_need = max(0.0, _target_duration(int(drafts[index]["char_count"]), config.max_reading_cps) - left_duration)
        right_need = max(0.0, _target_duration(int(drafts[index + 1]["char_count"]), config.max_reading_cps) - right_duration)
        left_share, right_share = _allocate_gap(gap, left_need, right_need)
        ends[index] = float(drafts[index]["raw_end"]) + left_share
        starts[index + 1] = float(drafts[index + 1]["raw_start"]) - right_share

    segments: list[SubtitleSegment] = []
    for segment_id, draft in enumerate(drafts, start=1):
        duration = max(ends[segment_id - 1] - starts[segment_id - 1], 0.01)
        text = str(draft["text"])
        segments.append(
            SubtitleSegment(
                segment_id=segment_id,
                start=starts[segment_id - 1],
                end=ends[segment_id - 1],
                text=text,
                line_count=int(draft["line_count"]),
                char_count=int(draft["char_count"]),
                word_count=int(draft["word_count"]),
                reading_cps=_reading_cps(text, duration),
            )
        )
    return segments


def _segment_within_limits(segment: SubtitleSegment, config: SegmentationConfig) -> bool:
    if segment.char_count > config.max_chars_per_block:
        return False
    if segment.line_count > config.max_lines_per_block:
        return False
    if any(len(line) > config.max_chars_per_line for line in segment.text.split("\n")):
        return False
    if (segment.end - segment.start) > 7.0:
        return False
    return True


def _segmentation_score(
    script_document: ScriptDocument,
    words: list[WordTiming],
    spans: list[_SegmentSpan],
    config: SegmentationConfig,
    pack,
) -> tuple[float, ...]:
    segments = _materialize_segments(script_document, words, spans, config, pack)
    if any(not _segment_within_limits(segment, config) for segment in segments):
        return (float("inf"),) * 7
    over_limit = 0.0
    max_cps = 18.0
    overflow = 0.0
    no_punctuation = 0.0
    short_duration = 0.0
    boundary_penalty = 0.0
    for index, segment in enumerate(segments):
        if segment.reading_cps > config.max_reading_cps:
            over_limit += 1.0
        max_cps = max(max_cps, segment.reading_cps)
        overflow += max(0.0, segment.reading_cps - config.max_reading_cps)
        span = spans[index]
        if not _segment_ends_cleanly(words[span.end_index], pack):
            no_punctuation += 1.0
        if (segment.end - segment.start) < 1.2:
            short_duration += 1.0
        boundary_penalty += _line_break_penalty(segment.text, pack)
        if index + 1 < len(spans):
            boundary_penalty += _boundary_penalty(words[span.end_index].word, words[spans[index + 1].start_index].word, pack)
    return (
        over_limit,
        round(max_cps, 6),
        round(overflow, 6),
        no_punctuation,
        short_duration,
        float(len(segments)),
        boundary_penalty,
    )


def _clone_spans(spans: list[_SegmentSpan]) -> list[_SegmentSpan]:
    return [ _SegmentSpan(span.start_index, span.end_index) for span in spans ]


def _shift_variants(spans: list[_SegmentSpan], index: int) -> list[list[_SegmentSpan]]:
    variants: list[list[_SegmentSpan]] = []
    current = spans[index]
    if index > 0:
        previous = spans[index - 1]
        if current.start_index < current.end_index:
            variant = _clone_spans(spans)
            variant[index - 1] = _SegmentSpan(previous.start_index, previous.end_index + 1)
            variant[index] = _SegmentSpan(current.start_index + 1, current.end_index)
            variants.append(variant)
        if previous.start_index < previous.end_index:
            variant = _clone_spans(spans)
            variant[index - 1] = _SegmentSpan(previous.start_index, previous.end_index - 1)
            variant[index] = _SegmentSpan(current.start_index - 1, current.end_index)
            variants.append(variant)
    if index + 1 < len(spans):
        next_span = spans[index + 1]
        if current.start_index < current.end_index:
            variant = _clone_spans(spans)
            variant[index] = _SegmentSpan(current.start_index, current.end_index - 1)
            variant[index + 1] = _SegmentSpan(next_span.start_index - 1, next_span.end_index)
            variants.append(variant)
        if next_span.start_index < next_span.end_index:
            variant = _clone_spans(spans)
            variant[index] = _SegmentSpan(current.start_index, current.end_index + 1)
            variant[index + 1] = _SegmentSpan(next_span.start_index + 1, next_span.end_index)
            variants.append(variant)
    return variants


def _split_variants(spans: list[_SegmentSpan], index: int) -> list[list[_SegmentSpan]]:
    current = spans[index]
    if current.start_index >= current.end_index:
        return []
    variants: list[list[_SegmentSpan]] = []
    for split_index in range(current.start_index + 1, current.end_index + 1):
        variant = _clone_spans(spans)
        replacement = [
            _SegmentSpan(current.start_index, split_index - 1),
            _SegmentSpan(split_index, current.end_index),
        ]
        variants = variants + [variant[:index] + replacement + variant[index + 1 :]]
    return variants


def _merge_variants(spans: list[_SegmentSpan], index: int) -> list[list[_SegmentSpan]]:
    variants: list[list[_SegmentSpan]] = []
    if index > 0:
        variant = _clone_spans(spans)
        merged = _SegmentSpan(variant[index - 1].start_index, variant[index].end_index)
        variants.append(variant[: index - 1] + [merged] + variant[index + 1 :])
    if index + 1 < len(spans):
        variant = _clone_spans(spans)
        merged = _SegmentSpan(variant[index].start_index, variant[index + 1].end_index)
        variants.append(variant[:index] + [merged] + variant[index + 2 :])
    return variants


def _repair_spans(
    script_document: ScriptDocument,
    words: list[WordTiming],
    spans: list[_SegmentSpan],
    config: SegmentationConfig,
    pack,
) -> tuple[list[_SegmentSpan], int]:
    current_spans = spans
    if not current_spans:
        return current_spans, 1
    if not any(segment.reading_cps > config.max_reading_cps for segment in _materialize_segments(script_document, words, current_spans, config, pack)):
        return current_spans, 1

    optimization_passes = 2
    for _ in range(2):
        cursor = 0
        changed = False
        while cursor < len(current_spans):
            current_segments = _materialize_segments(script_document, words, current_spans, config, pack)
            if cursor >= len(current_segments):
                break
            if current_segments[cursor].reading_cps <= config.max_reading_cps:
                cursor += 1
                continue
            current_score = _segmentation_score(script_document, words, current_spans, config, pack)
            best_spans = current_spans
            best_score = current_score

            for variant in _shift_variants(current_spans, cursor):
                variant_score = _segmentation_score(script_document, words, variant, config, pack)
                if variant_score < best_score:
                    best_spans = variant
                    best_score = variant_score
            if best_spans is current_spans:
                for variant in _split_variants(current_spans, cursor):
                    variant_score = _segmentation_score(script_document, words, variant, config, pack)
                    if variant_score < best_score:
                        best_spans = variant
                        best_score = variant_score
            if best_spans is current_spans:
                for variant in _merge_variants(current_spans, cursor):
                    variant_score = _segmentation_score(script_document, words, variant, config, pack)
                    if variant_score < best_score:
                        best_spans = variant
                        best_score = variant_score

            if best_spans is not current_spans:
                current_spans = best_spans
                changed = True
                continue
            cursor += 1
        if not changed:
            break
    return current_spans, optimization_passes


def _build_diagnostics(
    segments: list[SubtitleSegment],
    max_reading_cps: float,
    optimization_passes: int,
) -> SegmentationDiagnostics:
    over_18 = 0
    over_24 = 0
    over_30 = 0
    short_fast = 0
    long_text_fast = 0
    for segment in segments:
        duration = max(segment.end - segment.start, 0.01)
        if segment.reading_cps > max_reading_cps:
            over_18 += 1
            if duration < 1.2:
                short_fast += 1
            elif segment.char_count >= 60 or segment.word_count >= 12:
                long_text_fast += 1
        if segment.reading_cps > 24.0:
            over_24 += 1
        if segment.reading_cps > 30.0:
            over_30 += 1
    total_chars = sum(segment.char_count for segment in segments)
    total_words = sum(segment.word_count for segment in segments)
    other_fast = max(0, over_18 - short_fast - long_text_fast)
    return SegmentationDiagnostics(
        segment_profile={
            "ok": max(0, len(segments) - over_18),
            "short_fast": short_fast,
            "long_text_fast": long_text_fast,
            "other_fast": other_fast,
        },
        segments_over_18_cps=over_18,
        segments_over_24_cps=over_24,
        segments_over_30_cps=over_30,
        short_fast_segment_count=short_fast,
        long_text_fast_segment_count=long_text_fast,
        average_chars_per_segment=(total_chars / len(segments)) if segments else 0.0,
        average_words_per_segment=(total_words / len(segments)) if segments else 0.0,
        optimization_passes=optimization_passes,
    )


def segment_words(
    script_document: ScriptDocument,
    words: list[WordTiming],
    config: SegmentationConfig,
) -> SegmentationResult:
    if not words:
        return SegmentationResult(segments=[], warnings=[], diagnostics=SegmentationDiagnostics())

    pack = resolve_language_rulepack(script_document.language_code)
    spans = _choose_initial_spans(script_document, words, config, pack)
    spans, optimization_passes = _repair_spans(script_document, words, spans, config, pack)
    segments = _materialize_segments(script_document, words, spans, config, pack)
    warnings: list[str] = []
    for segment in segments:
        if segment.reading_cps > config.max_reading_cps:
            warnings.append(
                f"Segment {segment.segment_id} exceeds preferred reading speed ({segment.reading_cps:.1f} chars/s)."
            )
    diagnostics = _build_diagnostics(segments, config.max_reading_cps, optimization_passes)
    return SegmentationResult(segments=segments, warnings=warnings, diagnostics=diagnostics)
