"""Scene-aware and text-based chunking for translation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class TranslationChunk:
    """A chunk of text ready for translation, mapped to scene IDs."""

    index: int
    scene_ids: list[str] = field(default_factory=list)
    text: str = ""
    word_count: int = 0


def _word_count(text: str) -> int:
    return len(text.split())


def build_scene_aware_chunks(
    master_scenes: list[dict],
    max_words_per_chunk: int = 800,
) -> list[TranslationChunk]:
    """Group consecutive scenes into translation chunks.

    Rules:
    - Never split a scene across chunks.
    - If adding a scene would exceed *max_words_per_chunk*, start a new chunk.
    - A single scene that exceeds the limit becomes its own chunk.
    - Each chunk records which scene_ids it contains.
    """
    chunks: list[TranslationChunk] = []
    current_ids: list[str] = []
    current_texts: list[str] = []
    current_words = 0

    for scene in master_scenes:
        scene_id = scene.get("scene_id", "")
        scene_text = str(scene.get("text", "")).strip()
        if not scene_text:
            continue
        scene_words = _word_count(scene_text)

        # Would adding this scene exceed the limit?
        if current_words + scene_words > max_words_per_chunk and current_texts:
            # Flush current chunk
            chunks.append(TranslationChunk(
                index=len(chunks),
                scene_ids=list(current_ids),
                text="\n\n".join(current_texts),
                word_count=current_words,
            ))
            current_ids = []
            current_texts = []
            current_words = 0

        current_ids.append(scene_id)
        current_texts.append(scene_text)
        current_words += scene_words

    # Flush remaining
    if current_texts:
        chunks.append(TranslationChunk(
            index=len(chunks),
            scene_ids=list(current_ids),
            text="\n\n".join(current_texts),
            word_count=current_words,
        ))

    return chunks


def build_text_chunks(
    text: str,
    max_words: int = 800,
) -> list[TranslationChunk]:
    """Fallback paragraph-first chunking when master_scenes is not available.

    Ported from TRADUTOR app.js chunkText (lines 934-970):
    1. Split by double newlines (paragraphs).
    2. For paragraphs that exceed max_words, split by sentences.
    3. Group small paragraphs into chunks up to max_words.
    """
    paragraphs = re.split(r"\n\n+", text.strip())
    chunks: list[TranslationChunk] = []
    current_parts: list[str] = []
    current_words = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        para_words = _word_count(para)

        # Large paragraph — split by sentences
        if para_words > max_words:
            # Flush current
            if current_parts:
                chunks.append(_make_text_chunk(len(chunks), current_parts, current_words))
                current_parts = []
                current_words = 0

            sentences = re.findall(r"[^.!?]+[.!?]+", para)
            if not sentences:
                sentences = [para]
            sent_parts: list[str] = []
            sent_words = 0
            for sentence in sentences:
                sentence = sentence.strip()
                sw = _word_count(sentence)
                if sent_words + sw > max_words and sent_parts:
                    chunks.append(_make_text_chunk(len(chunks), sent_parts, sent_words))
                    sent_parts = [sentence]
                    sent_words = sw
                else:
                    sent_parts.append(sentence)
                    sent_words += sw
            if sent_parts:
                chunks.append(_make_text_chunk(len(chunks), sent_parts, sent_words))
            continue

        # Normal paragraph — group into current chunk
        if current_words + para_words > max_words and current_parts:
            chunks.append(_make_text_chunk(len(chunks), current_parts, current_words))
            current_parts = [para]
            current_words = para_words
        else:
            current_parts.append(para)
            current_words += para_words

    if current_parts:
        chunks.append(_make_text_chunk(len(chunks), current_parts, current_words))

    return chunks


def _make_text_chunk(index: int, parts: list[str], word_count: int) -> TranslationChunk:
    return TranslationChunk(
        index=index,
        scene_ids=[],
        text="\n\n".join(parts),
        word_count=word_count,
    )
