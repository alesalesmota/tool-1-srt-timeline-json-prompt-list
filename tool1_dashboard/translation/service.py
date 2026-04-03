"""Translation orchestrator — chunk, translate, assemble."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from .adapter import TranslationAdapter, TranslationError
from .chunker import (
    TranslationChunk,
    build_scene_aware_chunks,
    build_text_chunks,
)
from .prompts import build_translation_prompt

log = logging.getLogger(__name__)


@dataclass
class ChunkResult:
    chunk_index: int
    scene_ids: list[str]
    translated_text: str
    words_in: int
    words_out: int
    status: str  # "ok" | "error"
    error: str | None = None


@dataclass
class TranslationResult:
    language_code: str
    translated_script: str
    chunk_results: list[ChunkResult] = field(default_factory=list)
    status: str = "done"  # "done" | "partial" | "error"


class TranslationService:
    """Orchestrates chunked translation of a script."""

    def __init__(self, adapter: TranslationAdapter | None = None) -> None:
        self.adapter = adapter or TranslationAdapter()

    async def translate_script(
        self,
        source_script: str,
        source_lang: str,
        target_lang: str,
        provider: str,
        api_key: str,
        model: str,
        master_scenes: list[dict[str, Any]] | None = None,
        max_words_per_chunk: int = 800,
        context_tail_words: int = 200,
        channel_name: str = "",
    ) -> TranslationResult:
        """Translate a full script, chunk by chunk.

        If *master_scenes* is provided, uses scene-aware chunking (preserves
        scene boundaries).  Otherwise falls back to paragraph-based chunking.

        Context from the previous chunk (last *context_tail_words* words of the
        translated output) is passed to the next chunk for continuity.

        Per-chunk errors do not abort the process — failed chunks produce empty
        strings and the result is marked ``"partial"``.
        """
        # Build chunks
        if master_scenes:
            chunks = build_scene_aware_chunks(master_scenes, max_words_per_chunk)
        else:
            chunks = build_text_chunks(source_script, max_words_per_chunk)

        if not chunks:
            return TranslationResult(
                language_code=target_lang,
                translated_script="",
                status="done",
            )

        total_chunks = len(chunks)
        context = ""
        chunk_results: list[ChunkResult] = []
        translated_parts: list[str] = []

        for chunk in chunks:
            prompt = build_translation_prompt(
                chunk=chunk.text,
                context=context,
                source_lang=source_lang,
                target_lang=target_lang,
                chunk_index=chunk.index,
                total_chunks=total_chunks,
                channel_name=channel_name,
            )

            try:
                translated = await self.adapter.translate_chunk(
                    provider=provider,
                    api_key=api_key,
                    model=model,
                    prompt=prompt,
                )
                translated = str(translated or "").strip()
                if not translated:
                    raise TranslationError(provider.title(), 200, "Model returned empty translation.")
                words_out = len(translated.split())
                chunk_results.append(ChunkResult(
                    chunk_index=chunk.index,
                    scene_ids=chunk.scene_ids,
                    translated_text=translated,
                    words_in=chunk.word_count,
                    words_out=words_out,
                    status="ok",
                ))
                translated_parts.append(translated)

                # Update context: last N words of translated output
                if context_tail_words > 0:
                    words = translated.split()
                    context = " ".join(words[-context_tail_words:])
                else:
                    context = ""

            except (TranslationError, Exception) as exc:
                log.warning(
                    "Translation chunk %d/%d failed: %s",
                    chunk.index + 1,
                    total_chunks,
                    exc,
                )
                chunk_results.append(ChunkResult(
                    chunk_index=chunk.index,
                    scene_ids=chunk.scene_ids,
                    translated_text="",
                    words_in=chunk.word_count,
                    words_out=0,
                    status="error",
                    error=str(exc),
                ))
                translated_parts.append("")

        # Determine overall status
        ok_count = sum(1 for r in chunk_results if r.status == "ok")
        if ok_count == total_chunks:
            status = "done"
        elif ok_count > 0:
            status = "partial"
        else:
            status = "error"

        return TranslationResult(
            language_code=target_lang,
            translated_script="\n\n".join(translated_parts).strip(),
            chunk_results=chunk_results,
            status=status,
        )
