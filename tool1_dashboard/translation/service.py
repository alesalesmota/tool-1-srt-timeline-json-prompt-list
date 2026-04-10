"""Translation orchestrator — chunk, translate, assemble."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from .adapter import TranslationAdapter, TranslationError
from .chunker import (
    TranslationChunk,
    build_scene_aware_chunks,
    build_text_chunks,
)
from .prompts import (
    build_translation_prompt,
    build_translation_repair_prompt,
    build_translation_review_prompt,
    build_translation_script_repair_prompt,
)
from .quality import collect_translation_quality_issues, evaluate_translation_quality

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
    error_message: str | None = None
    deterministic_review: dict[str, Any] | None = None
    review_report: dict[str, Any] | None = None


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
        issues = collect_translation_quality_issues(
            source_text=chunk.text,
            translated_text=translated,
            language_code=target_lang,
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
        repair_issues = collect_translation_quality_issues(
            source_text=chunk.text,
            translated_text=repaired,
            language_code=target_lang,
            words_in=chunk.word_count,
            words_out=repaired_words_out,
            source_channel_name=source_channel_name,
            target_channel_name=target_channel_name,
        )
        if repair_issues:
            issue_text = "; ".join(repair_issues)
            raise TranslationError(provider.title(), 200, f"Translation quality check failed: {issue_text}")
        return repaired, repaired_words_out

    @staticmethod
    def _error_result(
        *,
        target_lang: str,
        translated_parts: list[str],
        chunk_results: list[ChunkResult],
        error_message: str,
        deterministic_review: dict[str, Any] | None = None,
        review_report: dict[str, Any] | None = None,
    ) -> TranslationResult:
        return TranslationResult(
            language_code=target_lang,
            translated_script="\n\n".join(translated_parts).strip(),
            chunk_results=chunk_results,
            status="error",
            error_message=error_message,
            deterministic_review=deterministic_review,
            review_report=review_report,
        )

    @staticmethod
    def _normalize_review_item(item: Any) -> str:
        if isinstance(item, dict):
            message = str(item.get("message") or item.get("issue") or item.get("problem") or "").strip()
            if message:
                return message
            return json.dumps(item, ensure_ascii=False)
        return str(item or "").strip()

    @classmethod
    def _sanitize_review_report(
        cls,
        report: dict[str, Any],
    ) -> dict[str, Any]:
        sanitized = dict(report or {})
        blocking_issues = [
            {"message": message}
            for message in (
                cls._normalize_review_item(item)
                for item in list(sanitized.get("blocking_issues") or [])
            )
            if message
        ]
        warnings = [
            {"message": message}
            for message in (
                cls._normalize_review_item(item)
                for item in list(sanitized.get("warnings") or [])
            )
            if message
        ]
        passed = sanitized.get("passed")
        if not isinstance(passed, bool):
            passed = not blocking_issues
        return {
            "passed": passed,
            "blocking_issues": blocking_issues,
            "warnings": warnings,
            "summary": str(sanitized.get("summary") or "").strip(),
        }

    @classmethod
    def _parse_review_payload(cls, review_text: str) -> dict[str, Any]:
        text = str(review_text or "").strip()
        if not text:
            raise TranslationError("OpenAI", 200, "Translation audit returned empty output.")
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise TranslationError("OpenAI", 200, "Translation audit returned invalid JSON.")
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise TranslationError("OpenAI", 200, f"Translation audit returned invalid JSON: {exc.msg}") from exc
        if not isinstance(payload, dict):
            raise TranslationError("OpenAI", 200, "Translation audit returned an unexpected payload.")
        return cls._sanitize_review_report(payload)

    async def audit_script_quality(
        self,
        *,
        source_script: str,
        translated_script: str,
        source_lang: str,
        target_lang: str,
        reviewer_api_key: str,
        reviewer_model: str,
        source_channel_name: str,
        target_channel_name: str,
    ) -> dict[str, Any]:
        if not str(reviewer_api_key or "").strip():
            raise TranslationError("OpenAI", 500, "Translation audit requires an OpenAI API key.")
        prompt = build_translation_review_prompt(
            source_text=source_script,
            translated_text=translated_script,
            source_lang=source_lang,
            target_lang=target_lang,
            source_channel_name=source_channel_name,
            target_channel_name=target_channel_name,
        )
        review_text = await self.adapter.translate_chunk(
            provider="openai",
            api_key=reviewer_api_key,
            model=reviewer_model,
            prompt=prompt,
        )
        return self._parse_review_payload(review_text)

    async def _repair_full_script(
        self,
        *,
        source_script: str,
        translated_script: str,
        issues: list[str],
        source_lang: str,
        target_lang: str,
        provider: str,
        api_key: str,
        model: str,
        source_channel_name: str,
        target_channel_name: str,
    ) -> str:
        repair_prompt = build_translation_script_repair_prompt(
            source_text=source_script,
            invalid_output=translated_script,
            issues=issues,
            source_lang=source_lang,
            target_lang=target_lang,
            source_channel_name=source_channel_name,
            target_channel_name=target_channel_name,
        )
        repaired = await self.adapter.translate_chunk(
            provider=provider,
            api_key=api_key,
            model=model,
            prompt=repair_prompt,
        )
        return str(repaired or "").strip()

    async def _apply_script_quality_gate(
        self,
        *,
        source_script: str,
        translated_script: str,
        source_lang: str,
        target_lang: str,
        provider: str,
        api_key: str,
        model: str,
        source_channel_name: str,
        target_channel_name: str,
        reviewer_required: bool,
        reviewer_api_key: str,
        reviewer_model: str,
    ) -> tuple[str, dict[str, Any]]:
        del reviewer_required
        del reviewer_api_key
        del reviewer_model

        translated_clean = str(translated_script or "").strip()
        words_in = len(str(source_script or "").split())
        words_out = len(translated_clean.split())
        report = evaluate_translation_quality(
            source_text=source_script,
            translated_text=translated_clean,
            language_code=target_lang,
            words_in=words_in,
            words_out=words_out,
            source_channel_name=source_channel_name,
            target_channel_name=target_channel_name,
        )
        if report["passed"]:
            return translated_clean, report

        repair_issues = [str(issue.get("message") or "").strip() for issue in report["blocking_issues"] if str(issue.get("message") or "").strip()]
        repaired_script = await self._repair_full_script(
            source_script=source_script,
            translated_script=translated_clean,
            issues=repair_issues,
            source_lang=source_lang,
            target_lang=target_lang,
            provider=provider,
            api_key=api_key,
            model=model,
            source_channel_name=source_channel_name,
            target_channel_name=target_channel_name,
        )
        repaired_report = evaluate_translation_quality(
            source_text=source_script,
            translated_text=repaired_script,
            language_code=target_lang,
            words_in=words_in,
            words_out=len(repaired_script.split()),
            source_channel_name=source_channel_name,
            target_channel_name=target_channel_name,
        )
        if not repaired_report["passed"]:
            issues = [
                str(issue.get("message") or "").strip()
                for issue in repaired_report["blocking_issues"]
                if str(issue.get("message") or "").strip()
            ]
            raise TranslationError(
                provider.title(),
                200,
                "Translation quality validation failed after script repair: " + "; ".join(issues),
            )
        return repaired_script, repaired_report

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
        reviewer_required: bool = False,
        reviewer_api_key: str = "",
        reviewer_model: str = "gpt-5.4-mini",
    ) -> TranslationResult:
        """Translate a full script, chunk by chunk."""
        if master_scenes:
            chunks = build_scene_aware_chunks(master_scenes, max_words_per_chunk, source_script=source_script)
        else:
            chunks = build_text_chunks(source_script, max_words_per_chunk)

        if not chunks:
            empty_report = evaluate_translation_quality(
                source_text=source_script,
                translated_text="",
                language_code=target_lang,
                source_channel_name=source_channel_name,
                target_channel_name=target_channel_name,
            )
            return TranslationResult(
                language_code=target_lang,
                translated_script="",
                status="done",
                deterministic_review=empty_report,
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
                chunk_results.append(
                    ChunkResult(
                        chunk_index=chunk.index,
                        scene_ids=chunk.scene_ids,
                        translated_text=translated,
                        words_in=chunk.word_count,
                        words_out=words_out,
                        status="ok",
                    )
                )
                translated_parts.append(translated)
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
                chunk_results.append(
                    ChunkResult(
                        chunk_index=chunk.index,
                        scene_ids=chunk.scene_ids,
                        translated_text="",
                        words_in=chunk.word_count,
                        words_out=0,
                        status="error",
                        error=str(exc),
                    )
                )
                return self._error_result(
                    target_lang=target_lang,
                    translated_parts=translated_parts,
                    chunk_results=chunk_results,
                    error_message=str(exc),
                )

        translated_script = "\n\n".join(translated_parts).strip()
        try:
            final_script, deterministic_review = await self._apply_script_quality_gate(
                source_script=source_script,
                translated_script=translated_script,
                source_lang=source_lang,
                target_lang=target_lang,
                provider=provider,
                api_key=api_key,
                model=model,
                source_channel_name=source_channel_name,
                target_channel_name=target_channel_name,
                reviewer_required=reviewer_required,
                reviewer_api_key=reviewer_api_key,
                reviewer_model=reviewer_model,
            )
        except Exception as exc:
            failed_review = evaluate_translation_quality(
                source_text=source_script,
                translated_text=translated_script,
                language_code=target_lang,
                words_in=len(str(source_script or "").split()),
                words_out=len(translated_script.split()),
                source_channel_name=source_channel_name,
                target_channel_name=target_channel_name,
            )
            return self._error_result(
                target_lang=target_lang,
                translated_parts=translated_parts,
                chunk_results=chunk_results,
                error_message=str(exc),
                deterministic_review=failed_review,
            )

        return TranslationResult(
            language_code=target_lang,
            translated_script=final_script,
            chunk_results=chunk_results,
            status="done",
            deterministic_review=deterministic_review,
        )
