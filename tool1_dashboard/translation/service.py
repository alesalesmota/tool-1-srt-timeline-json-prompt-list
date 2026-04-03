"""Translation orchestrator — chunk, translate, assemble."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from .adapter import TranslationAdapter, TranslationError
from .chunker import (
    TranslationChunk,
    build_scene_aware_chunks,
    build_text_chunks,
)
from .prompts import build_translation_prompt, build_translation_repair_prompt

log = logging.getLogger(__name__)
_ENGLISH_CTA_PATTERNS = (
    re.compile(r"\bsubscribe to\b", re.IGNORECASE),
    re.compile(r"\bshare this video\b", re.IGNORECASE),
    re.compile(r"\blike this video\b", re.IGNORECASE),
)
_SUSPICIOUS_WORD_EXPANSION_RATIO = 1.6
_SUSPICIOUS_WORD_EXPANSION_MARGIN = 180
_MIN_PARAGRAPH_WORDS = 6


def _normalize_compare_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def _significant_paragraphs(text: str) -> list[str]:
    paragraphs: list[str] = []
    for paragraph in re.split(r"\n\s*\n+", str(text or "").strip()):
        normalized = _normalize_compare_text(paragraph)
        if len(normalized.split()) >= _MIN_PARAGRAPH_WORDS:
            paragraphs.append(normalized)
    return paragraphs


def _is_non_english_target(target_lang: str) -> bool:
    normalized = str(target_lang or "").strip().lower()
    return not normalized.startswith("en") and "english" not in normalized


def _translation_quality_issues(
    *,
    source_text: str,
    translated_text: str,
    target_lang: str,
    words_in: int,
    words_out: int,
    source_channel_name: str = "",
    target_channel_name: str = "",
) -> list[str]:
    issues: list[str] = []
    translated_clean = str(translated_text or "").strip()
    if not translated_clean:
        return ["Model returned empty translation."]

    if words_in > 0:
        suspicious_limit = max(
            int(words_in * _SUSPICIOUS_WORD_EXPANSION_RATIO),
            words_in + _SUSPICIOUS_WORD_EXPANSION_MARGIN,
        )
        if words_out > suspicious_limit:
            issues.append("Output is suspiciously long and may contain duplicated source text.")

    translated_norm = _normalize_compare_text(translated_clean)
    source_paragraphs = _significant_paragraphs(source_text)
    leaked_source = [paragraph for paragraph in source_paragraphs if paragraph and paragraph in translated_norm]
    if leaked_source:
        issues.append("Output still contains untranslated source paragraphs.")

    if _is_non_english_target(target_lang):
        if any(pattern.search(translated_clean) for pattern in _ENGLISH_CTA_PATTERNS):
            issues.append("Output still contains English CTA wording.")

    if source_channel_name and target_channel_name:
        if re.search(re.escape(source_channel_name), translated_clean, flags=re.IGNORECASE):
            issues.append(f'Output still contains the source channel name "{source_channel_name}".')
        if re.search(re.escape(source_channel_name), source_text, flags=re.IGNORECASE) and not re.search(
            re.escape(target_channel_name),
            translated_clean,
            flags=re.IGNORECASE,
        ):
            issues.append(f'Output did not use the configured channel name "{target_channel_name}".')

    return issues


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

    async def _translate_chunk_with_repair(
        self,
        *,
        chunk: TranslationChunk,
        context: str,
        source_lang: str,
        target_lang: str,
        provider: str,
        api_key: str,
        model: str,
        total_chunks: int,
        channel_name: str,
        source_channel_name: str,
        target_channel_name: str,
    ) -> tuple[str, int]:
        prompt = build_translation_prompt(
            chunk=chunk.text,
            context=context,
            source_lang=source_lang,
            target_lang=target_lang,
            chunk_index=chunk.index,
            total_chunks=total_chunks,
            channel_name=channel_name,
            source_channel_name=source_channel_name,
            target_channel_name=target_channel_name,
        )

        translated = str(
            await self.adapter.translate_chunk(
                provider=provider,
                api_key=api_key,
                model=model,
                prompt=prompt,
            )
            or ""
        ).strip()
        words_out = len(translated.split())
        issues = _translation_quality_issues(
            source_text=chunk.text,
            translated_text=translated,
            target_lang=target_lang,
            words_in=chunk.word_count,
            words_out=words_out,
            source_channel_name=source_channel_name,
            target_channel_name=target_channel_name,
        )
        if not issues:
            return translated, words_out

        repair_prompt = build_translation_repair_prompt(
            chunk=chunk.text,
            invalid_output=translated,
            issues=issues,
            context=context,
            source_lang=source_lang,
            target_lang=target_lang,
            source_channel_name=source_channel_name,
            target_channel_name=target_channel_name,
        )
        repaired = str(
            await self.adapter.translate_chunk(
                provider=provider,
                api_key=api_key,
                model=model,
                prompt=repair_prompt,
            )
            or ""
        ).strip()
        repaired_words_out = len(repaired.split())
        repair_issues = _translation_quality_issues(
            source_text=chunk.text,
            translated_text=repaired,
            target_lang=target_lang,
            words_in=chunk.word_count,
            words_out=repaired_words_out,
            source_channel_name=source_channel_name,
            target_channel_name=target_channel_name,
        )
        if repair_issues:
            issue_text = "; ".join(repair_issues)
            raise TranslationError(provider.title(), 200, f"Translation quality check failed: {issue_text}")
        return repaired, repaired_words_out

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
        source_channel_name: str = "",
        target_channel_name: str = "",
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
            try:
                translated, words_out = await self._translate_chunk_with_repair(
                    chunk=chunk,
                    context=context,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    provider=provider,
                    api_key=api_key,
                    model=model,
                    total_chunks=total_chunks,
                    channel_name=channel_name,
                    source_channel_name=source_channel_name,
                    target_channel_name=target_channel_name,
                )
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
