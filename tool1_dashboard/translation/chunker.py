"""Scene-aware and text-based chunking for translation."""

from __future__ import annotations

from bisect import bisect_left
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
    *,
    source_script: str = "",
) -> list[TranslationChunk]:
    """Group consecutive scenes into translation chunks.

    Rules:
    - Never split a scene across chunks.
    - If adding a scene would exceed *max_words_per_chunk*, start a new chunk.
    - A single scene that exceeds the limit becomes its own chunk.
    - Each chunk records which scene_ids it contains.

    When *source_script* is provided, chunk the original script text and use
    scene metadata only to attach approximate contiguous ``scene_ids``. This
    preserves the full script even when localized timeline scene text is lossy
    or omits connective paragraphs.
    """
    if str(source_script or "").strip():
        text_chunks = build_text_chunks(source_script, max_words=max_words_per_chunk)
        return _attach_scene_ids_to_text_chunks(text_chunks, master_scenes)

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


def _attach_scene_ids_to_text_chunks(
    text_chunks: list[TranslationChunk],
    master_scenes: list[dict],
) -> list[TranslationChunk]:
    if not text_chunks:
        return []

    scene_entries: list[tuple[str, int]] = []
    for scene in master_scenes:
        scene_id = str(scene.get("scene_id", "")).strip()
        scene_text = str(scene.get("text", "")).strip()
        if not scene_id or not scene_text:
            continue
        scene_entries.append((scene_id, max(_word_count(scene_text), 1)))

    if not scene_entries:
        return [
            TranslationChunk(
                index=chunk.index,
                scene_ids=[],
                text=chunk.text,
                word_count=chunk.word_count,
            )
            for chunk in text_chunks
        ]

    total_scene_words = sum(words for _, words in scene_entries)
    total_text_words = sum(max(chunk.word_count, 1) for chunk in text_chunks)
    scene_prefix: list[int] = []
    running_scene_words = 0
    for _, words in scene_entries:
        running_scene_words += words
        scene_prefix.append(running_scene_words)

    attached: list[TranslationChunk] = []
    scene_start = 0
    text_words_so_far = 0
    scene_count = len(scene_entries)
    chunk_count = len(text_chunks)

    for chunk_index, chunk in enumerate(text_chunks):
        text_words_so_far += max(chunk.word_count, 1)
        remaining_chunks = chunk_count - chunk_index - 1
        remaining_scenes = scene_count - scene_start

        if chunk_index == chunk_count - 1 or scene_start >= scene_count:
            scene_end = scene_count
        else:
            target_scene_words = (text_words_so_far / total_text_words) * total_scene_words
            proposed_end = bisect_left(scene_prefix, target_scene_words) + 1
            min_end = min(scene_count, scene_start + 1)
            max_end = scene_count if remaining_scenes <= remaining_chunks else scene_count - remaining_chunks
            scene_end = max(min_end, min(proposed_end, max_end))

        attached.append(TranslationChunk(
            index=chunk.index,
            scene_ids=[scene_id for scene_id, _ in scene_entries[scene_start:scene_end]],
            text=chunk.text,
            word_count=chunk.word_count,
        ))
        scene_start = scene_end

    return attached


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
