from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable

from .models import ScriptDocument, ScriptWord, WordTiming
from .normalize_script import normalize_word


@dataclass
class RawAlignedWord:
    word: str
    start: float
    end: float
    confidence: float | None = None


def _word_from_script(script_word: ScriptWord, start: float, end: float, source: str, approximate: bool, confidence: float | None = None) -> WordTiming:
    if end < start:
        end = start
    return WordTiming(
        word=script_word.word,
        start=max(0.0, float(start)),
        end=max(0.0, float(end)),
        index=script_word.index,
        confidence=confidence,
        source=source,
        approximate=approximate,
        normalized=script_word.normalized,
        text_start=script_word.text_start,
        text_end=script_word.text_end,
        render_start=script_word.render_start,
        render_end=script_word.render_end,
        leading_text=script_word.leading_text,
        trailing_text=script_word.trailing_text,
    )


def _allocate_times(start: float, end: float, count: int) -> list[tuple[float, float]]:
    if count <= 0:
        return []
    if end < start:
        end = start
    if end == start:
        end = start + (0.18 * count)
    step = (end - start) / float(count)
    result: list[tuple[float, float]] = []
    cursor = start
    for index in range(count):
        word_start = cursor
        if index == count - 1:
            word_end = end
        else:
            word_end = cursor + step
        result.append((word_start, word_end))
        cursor = word_end
    return result


def normalize_raw_words(raw_words: Iterable[RawAlignedWord]) -> list[RawAlignedWord]:
    normalized: list[RawAlignedWord] = []
    for raw_word in raw_words:
        cleaned = normalize_word(raw_word.word)
        if not cleaned:
            continue
        normalized.append(
            RawAlignedWord(
                word=cleaned,
                start=float(raw_word.start),
                end=float(raw_word.end),
                confidence=raw_word.confidence,
            )
        )
    return normalized


def map_raw_words_to_script(
    raw_words: Iterable[RawAlignedWord],
    script_document: ScriptDocument,
    source: str,
    audio_duration: float,
) -> tuple[list[WordTiming], int, int, int, list[str]]:
    normalized_raw = normalize_raw_words(raw_words)
    script_words = script_document.words
    mapped: list[WordTiming | None] = [None] * len(script_words)
    warnings: list[str] = []
    mismatch_count = 0
    approximate_count = 0
    dropped_count = 0

    script_tokens = [word.normalized for word in script_words]
    raw_tokens = [word.word for word in normalized_raw]
    matcher = SequenceMatcher(a=script_tokens, b=raw_tokens, autojunk=False)
    opcodes = matcher.get_opcodes()

    for tag, i1, i2, j1, j2 in opcodes:
        if tag != "equal":
            continue
        for script_offset, raw_offset in zip(range(i1, i2), range(j1, j2)):
            raw_word = normalized_raw[raw_offset]
            mapped[script_offset] = _word_from_script(
                script_words[script_offset],
                start=raw_word.start,
                end=raw_word.end,
                source=source,
                approximate=False,
                confidence=raw_word.confidence,
            )

    for tag, i1, i2, j1, j2 in opcodes:
        if tag == "equal":
            continue
        if i1 == i2:
            continue
        mismatch_count += i2 - i1
        raw_slice = normalized_raw[j1:j2]
        if raw_slice:
            block_start = raw_slice[0].start
            block_end = raw_slice[-1].end
        else:
            previous = next((mapped[index] for index in range(i1 - 1, -1, -1) if mapped[index] is not None), None)
            upcoming = next((mapped[index] for index in range(i2, len(mapped)) if mapped[index] is not None), None)
            if previous is not None and upcoming is not None and upcoming.start > previous.end:
                block_start = previous.end
                block_end = upcoming.start
            elif previous is not None:
                block_start = previous.end
                block_end = min(audio_duration, previous.end + (0.18 * (i2 - i1)))
            elif upcoming is not None:
                block_end = upcoming.start
                block_start = max(0.0, upcoming.start - (0.18 * (i2 - i1)))
            else:
                block_start = 0.0
                block_end = max(audio_duration, 0.18 * (i2 - i1))

        distributed = _allocate_times(block_start, block_end, i2 - i1)
        for script_index, (start, end) in zip(range(i1, i2), distributed):
            mapped[script_index] = _word_from_script(
                script_words[script_index],
                start=start,
                end=end,
                source=f"{source}_approx",
                approximate=True,
            )
            approximate_count += 1
        warnings.append(
            f"{source} mismatch near script words {i1 + 1}-{i2}; timings were approximated."
        )

    finalized = [word for word in mapped if word is not None]
    if len(finalized) != len(script_words):
        dropped_count = len(script_words) - len(finalized)
        warnings.append(f"{dropped_count} script words could not be assigned timings.")
    return finalized, mismatch_count, approximate_count, dropped_count, warnings

