"""Translation orchestrator — chunk, translate, assemble."""

from __future__ import annotations

import json
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
from .prompts import (
    build_translation_prompt,
    build_translation_repair_prompt,
    build_translation_review_prompt,
    build_translation_script_repair_prompt,
)
from .quality import collect_translation_quality_issues

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
        review_report: dict[str, Any] | None = None,
    ) -> TranslationResult:
        return TranslationResult(
            language_code=target_lang,
            translated_script="\n\n".join(translated_parts).strip(),
            chunk_results=chunk_results,
            status="error",
            error_message=error_message,
            review_report=review_report,
        )

    @staticmethod
    def _normalize_review_issue(item: Any) -> str:
        if isinstance(item, dict):
            criterion = str(item.get("criterion") or "").strip()
            problem = str(item.get("problem") or item.get("issue") or "").strip()
            if criterion and problem:
                return f"[{criterion}] {problem}"
            return problem or json.dumps(item, ensure_ascii=False)
        return str(item or "").strip()

    @staticmethod
    def _review_scores_meet_quality_bar(scores: dict[str, int]) -> bool:
        return bool(scores) and min(scores.values()) >= 4

    @staticmethod
    def _prune_review_false_positives(
        *,
        issues: list[str],
        translated_script: str,
        source_channel_name: str,
    ) -> list[str]:
        translated_lower = str(translated_script or "").lower()
        source_channel_lower = str(source_channel_name or "").strip().lower()
        if not source_channel_lower:
            return issues
        pruned: list[str] = []
        for issue in issues:
            issue_text = str(issue or "").strip()
            issue_lower = issue_text.lower()
            if source_channel_lower in issue_lower and source_channel_lower not in translated_lower:
                continue
            pruned.append(issue_text)
        return pruned

    @classmethod
    def _sanitize_review_report(
        cls,
        report: dict[str, Any],
        *,
        translated_script: str,
        source_channel_name: str,
        target_channel_name: str,
    ) -> dict[str, Any]:
        sanitized = dict(report or {})
        issues = cls._prune_review_false_positives(
            issues=list(sanitized.get("issues") or []),
            translated_script=translated_script,
            source_channel_name=source_channel_name,
        )
        sanitized["issues"] = issues
        scores = dict(sanitized.get("scores") or {})
        source_channel = str(source_channel_name or "").strip()
        target_channel = str(target_channel_name or "").strip()
        if source_channel and target_channel:
            source_present = bool(re.search(re.escape(source_channel), translated_script, flags=re.IGNORECASE))
            target_present = bool(re.search(re.escape(target_channel), translated_script, flags=re.IGNORECASE))
            if not source_present and target_present:
                scores["channel_name_compliance"] = 5
        sanitized["scores"] = scores
        if not bool(sanitized.get("passed")) and cls._review_scores_meet_quality_bar(
            scores
        ):
            sanitized["passed"] = True
        return sanitized

    @staticmethod
    def _parse_review_payload(review_text: str) -> dict[str, Any]:
        text = str(review_text or "").strip()
        if not text:
            raise TranslationError("OpenAI", 200, "Translation quality review returned empty output.")
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            raise TranslationError("OpenAI", 200, "Translation quality review returned invalid JSON.")
        try:
            payload = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise TranslationError("OpenAI", 200, f"Translation quality review returned invalid JSON: {exc.msg}") from exc
        if not isinstance(payload, dict):
            raise TranslationError("OpenAI", 200, "Translation quality review returned an unexpected payload.")

        scores_raw = payload.get("scores")
        scores = scores_raw if isinstance(scores_raw, dict) else {}
        parsed_scores: dict[str, int] = {}
        for key in ("fluency", "naturalness", "faithfulness", "cta_quality", "channel_name_compliance"):
            try:
                parsed_scores[key] = int(scores.get(key))
            except (TypeError, ValueError):
                raise TranslationError("OpenAI", 200, f"Translation quality review omitted score '{key}'.")
        issues = []
        for item in payload.get("issues") or []:
            normalized_issue = TranslationService._normalize_review_issue(item)
            if normalized_issue:
                issues.append(normalized_issue)
        passed = payload.get("passed")
        if not isinstance(passed, bool):
            raise TranslationError("OpenAI", 200, "Translation quality review omitted boolean field 'passed'.")
        return {
            "passed": passed,
            "issues": issues,
            "scores": parsed_scores,
            "summary": str(payload.get("summary") or "").strip(),
            "raw_text": text,
        }

    async def _review_script_quality(
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
            raise TranslationError("OpenAI", 500, "Translation quality review requires an OpenAI API key.")
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
        parsed = self._parse_review_payload(review_text)
        return self._sanitize_review_report(
            parsed,
            translated_script=translated_script,
            source_channel_name=source_channel_name,
            target_channel_name=target_channel_name,
        )

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
    ) -> tuple[str, dict[str, Any] | None]:
        words_in = len(str(source_script or "").split())
        translated_clean = str(translated_script or "").strip()
        words_out = len(translated_clean.split())
        script_issues = collect_translation_quality_issues(
            source_text=source_script,
            translated_text=translated_clean,
            language_code=target_lang,
            words_in=words_in,
            words_out=words_out,
            source_channel_name=source_channel_name,
            target_channel_name=target_channel_name,
        )

        if not script_issues:
            review_report = None
            if reviewer_required:
                review_report = await self._review_script_quality(
                    source_script=source_script,
                    translated_script=translated_clean,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    reviewer_api_key=reviewer_api_key,
                    reviewer_model=reviewer_model,
                    source_channel_name=source_channel_name,
                    target_channel_name=target_channel_name,
                )
                if review_report["passed"]:
                    return translated_clean, review_report
                script_issues = list(review_report["issues"]) or ["LLM quality review rejected the translated script."]
            else:
                return translated_clean, None

        repaired_script = await self._repair_full_script(
            source_script=source_script,
            translated_script=translated_clean,
            issues=script_issues,
            source_lang=source_lang,
            target_lang=target_lang,
            provider=provider,
            api_key=api_key,
            model=model,
            source_channel_name=source_channel_name,
            target_channel_name=target_channel_name,
        )
        repaired_issues = collect_translation_quality_issues(
            source_text=source_script,
            translated_text=repaired_script,
            language_code=target_lang,
            words_in=words_in,
            words_out=len(repaired_script.split()),
            source_channel_name=source_channel_name,
            target_channel_name=target_channel_name,
        )
        if repaired_issues:
            raise TranslationError(
                provider.title(),
                200,
                "Translation quality validation failed after script repair: " + "; ".join(repaired_issues),
            )
        if reviewer_required:
            review_report = await self._review_script_quality(
                source_script=source_script,
                translated_script=repaired_script,
                source_lang=source_lang,
                target_lang=target_lang,
                reviewer_api_key=reviewer_api_key,
                reviewer_model=reviewer_model,
                source_channel_name=source_channel_name,
                target_channel_name=target_channel_name,
            )
            if not review_report["passed"]:
                issues = list(review_report["issues"]) or ["LLM quality review rejected the repaired script."]
                raise TranslationError(
                    "OpenAI",
                    200,
                    "Translation quality review failed after repair: " + "; ".join(issues),
                )
            return repaired_script, review_report
        return repaired_script, None

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
        """Translate a full script, chunk by chunk.

        If *master_scenes* is provided, uses scene-aware chunking (preserves
        scene boundaries).  Otherwise falls back to paragraph-based chunking.

        Context from the previous chunk (last *context_tail_words* words of the
        translated output) is passed to the next chunk for continuity.

        Any unrepaired chunk or failed script-level QA fails the language.
        """
        # Build chunks
        if master_scenes:
            chunks = build_scene_aware_chunks(master_scenes, max_words_per_chunk, source_script=source_script)
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
                failed_chunk = ChunkResult(
                    chunk_index=chunk.index,
                    scene_ids=chunk.scene_ids,
                    translated_text="",
                    words_in=chunk.word_count,
                    words_out=0,
                    status="error",
                    error=str(exc),
                )
                chunk_results.append(failed_chunk)
                return self._error_result(
                    target_lang=target_lang,
                    translated_parts=translated_parts,
                    chunk_results=chunk_results,
                    error_message=str(exc),
                )

        translated_script = "\n\n".join(translated_parts).strip()
        try:
            final_script, review_report = await self._apply_script_quality_gate(
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
            return self._error_result(
                target_lang=target_lang,
                translated_parts=translated_parts,
                chunk_results=chunk_results,
                error_message=str(exc),
            )

        return TranslationResult(
            language_code=target_lang,
            translated_script=final_script,
            chunk_results=chunk_results,
            status="done",
            review_report=review_report,
        )
