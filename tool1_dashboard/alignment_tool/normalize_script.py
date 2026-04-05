from __future__ import annotations

import re

from .models import ScriptDocument, ScriptWord
from ..translation.language_rules import normalize_alignment_token

WORD_PATTERN = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*", re.UNICODE)
OPEN_PUNCTUATION = "\"'([{«“‘"
CLOSE_PUNCTUATION = "\"'.,;:!?)]}»”’"
TRANSLATION_TABLE = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u00a0": " ",
    }
)

def normalize_word(value: str, language_code: str = "en") -> str:
    cleaned = value.translate(TRANSLATION_TABLE).lower()
    return normalize_alignment_token(cleaned)


def _normalize_text(raw_text: str) -> str:
    text = raw_text.replace("\r\n", "\n").replace("\r", "\n").translate(TRANSLATION_TABLE)
    normalized_paragraphs: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        collapsed = re.sub(r"[ \t]+", " ", paragraph)
        collapsed = re.sub(r"\s*\n\s*", " ", collapsed)
        collapsed = collapsed.strip()
        if collapsed:
            normalized_paragraphs.append(collapsed)
    if not normalized_paragraphs:
        return ""
    return "\n\n".join(normalized_paragraphs)


def _leading_text(gap: str) -> str:
    stripped = gap.strip()
    if stripped and all(char in OPEN_PUNCTUATION for char in stripped):
        return stripped
    return ""


def _trailing_text(gap: str) -> str:
    stripped = gap.strip()
    if stripped and all(char in CLOSE_PUNCTUATION for char in stripped):
        return stripped
    return ""


def _build_paragraph_word_ranges(canonical_text: str, words: list[ScriptWord]) -> list[tuple[int, int]]:
    if not canonical_text or not words:
        return []
    boundaries = [0]
    boundaries.extend(match.end() for match in re.finditer(r"\n\s*\n", canonical_text))
    boundaries.append(len(canonical_text))
    ranges: list[tuple[int, int]] = []
    word_cursor = 0
    for start, end in zip(boundaries, boundaries[1:]):
        while word_cursor < len(words) and words[word_cursor].text_end <= start:
            word_cursor += 1
        range_start = word_cursor
        while word_cursor < len(words) and words[word_cursor].text_start < end:
            word_cursor += 1
        if word_cursor > range_start:
            ranges.append((range_start, word_cursor))
    return ranges


def normalize_script(raw_text: str, language_code: str = "en") -> ScriptDocument:
    canonical_text = _normalize_text(raw_text)
    if not canonical_text:
        raise ValueError("Script is empty after normalization.")
    matches = list(WORD_PATTERN.finditer(canonical_text))
    if not matches:
        raise ValueError("Script does not contain any readable words.")

    words: list[ScriptWord] = []
    for index, match in enumerate(matches):
        previous_end = matches[index - 1].end() if index > 0 else 0
        next_start = matches[index + 1].start() if index + 1 < len(matches) else len(canonical_text)
        gap_before = canonical_text[previous_end:match.start()]
        gap_after = canonical_text[match.end():next_start]
        leading_text = _leading_text(gap_before)
        trailing_text = _trailing_text(gap_after)
        words.append(
            ScriptWord(
                index=index,
                word=match.group(0),
                normalized=normalize_word(match.group(0), language_code),
                text_start=match.start(),
                text_end=match.end(),
                render_start=match.start() - len(leading_text),
                render_end=match.end() + len(trailing_text),
                leading_text=leading_text,
                trailing_text=trailing_text,
            )
        )

    alignment_text = " ".join(word.word for word in words)
    paragraphs = [paragraph for paragraph in canonical_text.split("\n\n") if paragraph.strip()]
    paragraph_word_ranges = _build_paragraph_word_ranges(canonical_text, words)
    return ScriptDocument(
        source_text=raw_text,
        canonical_text=canonical_text,
        alignment_text=alignment_text,
        words=words,
        paragraphs=paragraphs,
        paragraph_word_ranges=paragraph_word_ranges,
        language_code=language_code,
    )
