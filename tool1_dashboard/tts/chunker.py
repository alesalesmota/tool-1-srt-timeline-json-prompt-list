"""Sentence-aware text chunking for TTS (max 200 chars per chunk)."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .constants import CHUNK_MAX_CHARS


@dataclass
class TTSChunk:
    index: int
    text: str
    char_count: int


def _split_hard_limit(segment: str, max_chars: int) -> list[str]:
    """Split a segment that exceeds max_chars by word boundaries."""
    segment = segment.strip()
    if not segment:
        return []
    if len(segment) <= max_chars:
        return [segment]

    tokens = segment.split(" ")
    if len(tokens) <= 1:
        # Single very long word — force-split by char count
        return [segment[i : i + max_chars] for i in range(0, len(segment), max_chars)]

    parts: list[str] = []
    current = ""
    for token in tokens:
        candidate = token if not current else f"{current} {token}"
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            parts.append(current)
        if len(token) <= max_chars:
            current = token
        else:
            parts.extend(
                token[i : i + max_chars] for i in range(0, len(token), max_chars)
            )
            current = ""
    if current:
        parts.append(current)
    return parts


def chunk_text_for_tts(
    text: str, max_chars: int = CHUNK_MAX_CHARS
) -> list[TTSChunk]:
    """Split *text* into TTS-friendly chunks of at most *max_chars* characters.

    Uses sentence-ending punctuation as the primary split point, then falls
    back to word boundaries for oversized sentences.
    """
    clean_text = re.sub(r"\s+", " ", text).strip()
    if not clean_text:
        return []

    sentences = re.split(r"(?<=[.?!])\s+", clean_text)
    raw_chunks: list[str] = []
    current_chunk = ""

    for sentence in sentences:
        for part in _split_hard_limit(sentence, max_chars):
            if not current_chunk:
                current_chunk = part
                continue
            candidate = f"{current_chunk} {part}"
            if len(candidate) <= max_chars:
                current_chunk = candidate
            else:
                raw_chunks.append(current_chunk)
                current_chunk = part

    if current_chunk:
        raw_chunks.append(current_chunk)

    return [
        TTSChunk(index=i, text=chunk, char_count=len(chunk))
        for i, chunk in enumerate(raw_chunks)
        if chunk
    ]
