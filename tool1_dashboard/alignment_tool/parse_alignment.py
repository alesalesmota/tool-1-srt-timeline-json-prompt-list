from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Iterable

from ..translation.language_rules import component_aliases_for_token, joined_alias_for_components
from .models import ScriptDocument, ScriptWord, WordTiming
from .normalize_script import normalize_word


@dataclass
class RawAlignedWord:
    word: str
    start: float
    end: float
    confidence: float | None = None


@dataclass(frozen=True)
class _RescueOp:
    kind: str
    script_count: int
    raw_count: int
    fuzzy: bool = False


def _word_from_script(
    script_word: ScriptWord,
    start: float,
    end: float,
    source: str,
    approximate: bool,
    confidence: float | None = None,
) -> WordTiming:
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


def normalize_raw_words(raw_words: Iterable[RawAlignedWord], language_code: str = "en") -> list[RawAlignedWord]:
    normalized: list[RawAlignedWord] = []
    for raw_word in raw_words:
        cleaned = normalize_word(raw_word.word, language_code)
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


def _token_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(a=left, b=right, autojunk=False).ratio()


def _token_equivalent(left: str, right: str) -> bool:
    return bool(left and right and left == right)


def _fuzzy_match(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if min(len(left), len(right)) < 5:
        return False
    return _token_similarity(left, right) >= 0.84


def _joined_parts_match(token: str, parts: tuple[str, ...], language_code: str) -> bool:
    joined_alias = joined_alias_for_components(parts, language_code)
    direct_join = "".join(parts)
    return token in {joined_alias, direct_join}


def _component_match(token: str, parts: tuple[str, ...], language_code: str) -> bool:
    token_aliases = component_aliases_for_token(token, language_code)
    return parts in token_aliases or "".join(parts) == token


def _rescue_local_block(
    script_tokens: list[str],
    raw_tokens: list[str],
    language_code: str,
) -> list[_RescueOp] | None:
    if not script_tokens:
        return []
    if len(script_tokens) > 4 or len(raw_tokens) > 4:
        return None

    @lru_cache(maxsize=None)
    def solve(script_index: int, raw_index: int) -> tuple[int, tuple[_RescueOp, ...]] | None:
        if script_index == len(script_tokens) and raw_index == len(raw_tokens):
            return (0, ())
        if script_index >= len(script_tokens) or raw_index > len(raw_tokens):
            return None

        best: tuple[int, tuple[_RescueOp, ...]] | None = None

        def consider(
            op: _RescueOp,
            next_script_index: int,
            next_raw_index: int,
            penalty: int,
        ) -> None:
            nonlocal best
            remainder = solve(next_script_index, next_raw_index)
            if remainder is None:
                return
            candidate = (penalty + remainder[0], (op,) + remainder[1])
            if best is None or candidate[0] < best[0]:
                best = candidate

        script_token = script_tokens[script_index]

        if raw_index < len(raw_tokens):
            raw_token = raw_tokens[raw_index]
            if _token_equivalent(script_token, raw_token):
                consider(_RescueOp("direct", 1, 1), script_index + 1, raw_index + 1, 0)
            elif _fuzzy_match(script_token, raw_token):
                consider(_RescueOp("fuzzy", 1, 1, fuzzy=True), script_index + 1, raw_index + 1, 1)

        if raw_index + 1 < len(raw_tokens):
            raw_pair = (raw_tokens[raw_index], raw_tokens[raw_index + 1])
            if _joined_parts_match(script_token, raw_pair, language_code):
                consider(_RescueOp("merge", 1, 2), script_index + 1, raw_index + 2, 0)
            elif _fuzzy_match(script_token, "".join(raw_pair)):
                consider(_RescueOp("merge", 1, 2, fuzzy=True), script_index + 1, raw_index + 2, 1)

        if script_index + 1 < len(script_tokens) and raw_index < len(raw_tokens):
            script_pair = (script_tokens[script_index], script_tokens[script_index + 1])
            raw_token = raw_tokens[raw_index]
            if _component_match(raw_token, script_pair, language_code):
                consider(_RescueOp("split", 2, 1), script_index + 2, raw_index + 1, 0)
            elif _fuzzy_match(raw_token, "".join(script_pair)):
                consider(_RescueOp("split", 2, 1, fuzzy=True), script_index + 2, raw_index + 1, 1)

        return best

    result = solve(0, 0)
    if result is None:
        return None
    return list(result[1])


def map_raw_words_to_script(
    raw_words: Iterable[RawAlignedWord],
    script_document: ScriptDocument,
    source: str,
    audio_duration: float,
    language_code: str = "en",
) -> tuple[list[WordTiming], int, int, int, list[str], dict[str, int]]:
    normalized_raw = normalize_raw_words(raw_words, language_code)
    script_words = script_document.words
    mapped: list[WordTiming | None] = [None] * len(script_words)
    warnings: list[str] = []
    mismatch_count = 0
    approximate_count = 0
    dropped_count = 0
    diagnostics = {
        "mismatch_blocks": 0,
        "isolated_mismatch_blocks": 0,
        "clustered_mismatch_blocks": 0,
        "merge_rescues": 0,
        "split_rescues": 0,
        "fuzzy_rescues": 0,
        "approximate_blocks": 0,
    }

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
        if tag == "equal" or i1 == i2:
            continue

        script_slice = script_words[i1:i2]
        raw_slice = normalized_raw[j1:j2]
        script_slice_tokens = script_tokens[i1:i2]
        raw_slice_tokens = raw_tokens[j1:j2]
        rescue_plan = _rescue_local_block(script_slice_tokens, raw_slice_tokens, language_code)

        if rescue_plan:
            local_script_index = 0
            local_raw_index = 0
            for operation in rescue_plan:
                current_script_words = script_slice[local_script_index:local_script_index + operation.script_count]
                current_raw_words = raw_slice[local_raw_index:local_raw_index + operation.raw_count]
                op_source = source
                if operation.kind == "merge":
                    diagnostics["merge_rescues"] += 1
                    op_source = f"{source}_merge"
                    first_raw = current_raw_words[0]
                    last_raw = current_raw_words[-1]
                    mapped[i1 + local_script_index] = _word_from_script(
                        current_script_words[0],
                        start=first_raw.start,
                        end=last_raw.end,
                        source=op_source,
                        approximate=False,
                        confidence=first_raw.confidence,
                    )
                elif operation.kind == "split":
                    diagnostics["split_rescues"] += 1
                    op_source = f"{source}_split"
                    first_raw = current_raw_words[0]
                    distributed = _allocate_times(first_raw.start, first_raw.end, operation.script_count)
                    for offset, (start, end) in enumerate(distributed):
                        mapped[i1 + local_script_index + offset] = _word_from_script(
                            current_script_words[offset],
                            start=start,
                            end=end,
                            source=op_source,
                            approximate=False,
                            confidence=first_raw.confidence,
                        )
                else:
                    raw_word = current_raw_words[0]
                    if operation.fuzzy:
                        diagnostics["fuzzy_rescues"] += 1
                        op_source = f"{source}_fuzzy"
                    mapped[i1 + local_script_index] = _word_from_script(
                        current_script_words[0],
                        start=raw_word.start,
                        end=raw_word.end,
                        source=op_source,
                        approximate=False,
                        confidence=raw_word.confidence,
                    )
                local_script_index += operation.script_count
                local_raw_index += operation.raw_count
            continue

        diagnostics["mismatch_blocks"] += 1
        clustered_block = (i2 - i1) > 2 or (j2 - j1) > 2
        if not clustered_block:
            diagnostics["isolated_mismatch_blocks"] += 1
        else:
            diagnostics["clustered_mismatch_blocks"] += 1
        diagnostics["approximate_blocks"] += 1
        mismatch_count += i2 - i1

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
        label = "clustered mismatch" if clustered_block else "mismatch"
        warnings.append(
            f"{source} {label} near script words {i1 + 1}-{i2}; timings were approximated."
        )

    finalized = [word for word in mapped if word is not None]
    if len(finalized) != len(script_words):
        dropped_count = len(script_words) - len(finalized)
        warnings.append(f"{dropped_count} script words could not be assigned timings.")
    return finalized, mismatch_count, approximate_count, dropped_count, warnings, diagnostics
