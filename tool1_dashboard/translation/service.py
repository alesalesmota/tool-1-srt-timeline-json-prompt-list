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
    extract_sensitive_terms,
)
from .quality import (
    ERROR_CATEGORY_LABELS,
    analyze_translation_quality,
    categorize_translation_issue_text,
    collect_translation_quality_issues,
    summarize_translation_categories,
)

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
    error_summary: str | None = None
    error_categories: list[str] = field(default_factory=list)
    review_report: dict[str, Any] | None = None


class TranslationService:
    """Orchestrates chunked translation of a script."""

    def __init__(self, adapter: TranslationAdapter | None = None) -> None:
        self.adapter = adapter or TranslationAdapter()

    async def _call_adapter_translate(
        self,
        *,
        provider: str,
        api_key: str,
        model: str,
        prompt: str,
        base_url: str = "",
    ) -> str:
        kwargs: dict[str, Any] = {
            "provider": provider,
            "api_key": api_key,
            "model": model,
            "prompt": prompt,
        }
        if str(base_url or "").strip():
            kwargs["base_url"] = base_url
        try:
            return await self.adapter.translate_chunk(**kwargs)
        except TypeError as exc:
            if "unexpected keyword argument 'base_url'" not in str(exc):
                raise
            kwargs.pop("base_url", None)
            return await self.adapter.translate_chunk(**kwargs)

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
        provider_base_url: str,
        total_chunks: int,
        channel_name: str,
        source_channel_name: str,
        target_channel_name: str,
        sensitive_terms: list[str],
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
            sensitive_terms=sensitive_terms,
        )

        translated = str(
            await self._call_adapter_translate(
                provider=provider,
                api_key=api_key,
                model=model,
                prompt=prompt,
                base_url=provider_base_url,
            )
            or ""
        ).strip()
        words_out = len(translated.split())
        analysis = analyze_translation_quality(
            source_text=chunk.text,
            translated_text=translated,
            language_code=target_lang,
            words_in=chunk.word_count,
            words_out=words_out,
            source_channel_name=source_channel_name,
            target_channel_name=target_channel_name,
        )
        issues = list(analysis["issues"])
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
            sensitive_terms=sensitive_terms,
        )
        repaired = str(
            await self._call_adapter_translate(
                provider=provider,
                api_key=api_key,
                model=model,
                prompt=repair_prompt,
                base_url=provider_base_url,
            )
            or ""
        ).strip()
        repaired_words_out = len(repaired.split())
        repair_analysis = analyze_translation_quality(
            source_text=chunk.text,
            translated_text=repaired,
            language_code=target_lang,
            words_in=chunk.word_count,
            words_out=repaired_words_out,
            source_channel_name=source_channel_name,
            target_channel_name=target_channel_name,
        )
        repair_issues = list(repair_analysis["issues"])
        if repair_issues:
            issue_text = "; ".join(repair_issues)
            raise self._translation_error(
                provider.title(),
                200,
                f"Translation quality check failed: {issue_text}",
                issues=repair_issues,
            )
        return repaired, repaired_words_out

    @classmethod
    def _error_result(
        cls,
        *,
        target_lang: str,
        translated_parts: list[str],
        chunk_results: list[ChunkResult],
        error_message: str,
        error_summary: str | None = None,
        error_categories: list[str] | None = None,
        review_report: dict[str, Any] | None = None,
    ) -> TranslationResult:
        categories = [str(item or "").strip() for item in error_categories or [] if str(item or "").strip()]
        if not categories and error_message:
            categories = [categorize_translation_issue_text(error_message)]
        summary = str(error_summary or "").strip() or summarize_translation_categories(categories, issues=[error_message])
        return TranslationResult(
            language_code=target_lang,
            translated_script="\n\n".join(translated_parts).strip(),
            chunk_results=chunk_results,
            status="error",
            error_message=error_message,
            error_summary=summary or None,
            error_categories=categories,
            review_report=review_report,
        )

    @staticmethod
    def _feedback_from_issues(issues: list[str]) -> tuple[list[str], str]:
        categories: list[str] = []
        for issue in issues:
            category = categorize_translation_issue_text(issue)
            if category not in categories:
                categories.append(category)
        summary = summarize_translation_categories(categories, issues=issues)
        return categories, summary

    @classmethod
    def _translation_error(
        cls,
        provider: str,
        status: int,
        message: str,
        *,
        issues: list[str] | None = None,
        review_report: dict[str, Any] | None = None,
    ) -> TranslationError:
        error = TranslationError(provider, status, message)
        if review_report:
            error.review_report = review_report
            error.error_categories = list(review_report.get("error_categories") or [])
            error.error_summary = str(
                review_report.get("error_summary")
                or review_report.get("summary")
                or ""
            ).strip()
            return error
        issue_list = [str(item or "").strip() for item in issues or [] if str(item or "").strip()]
        if issue_list:
            categories, summary = cls._feedback_from_issues(issue_list)
            error.error_categories = categories
            error.error_summary = summary
        return error

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
        detailed_issues: list[dict[str, str]] = []
        categories: list[str] = []
        for issue in issues:
            category = categorize_translation_issue_text(issue)
            if category not in categories:
                categories.append(category)
            detailed_issues.append({
                "text": issue,
                "category": category,
                "label": ERROR_CATEGORY_LABELS.get(category, category.replace("_", " ").title()),
            })
        if bool(sanitized.get("passed")):
            sanitized["error_categories"] = []
            sanitized["error_summary"] = ""
        else:
            sanitized["error_categories"] = categories
            sanitized["error_summary"] = summarize_translation_categories(categories, issues=issues)
        sanitized["issues_detailed"] = detailed_issues
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
        reviewer_provider: str,
        reviewer_api_key: str,
        reviewer_model: str,
        reviewer_base_url: str,
        source_channel_name: str,
        target_channel_name: str,
        sensitive_terms: list[str],
    ) -> dict[str, Any]:
        if str(reviewer_provider or "").strip() != "openai":
            raise TranslationError("Reviewer", 500, "Translation quality review currently supports only OpenAI.")
        if not str(reviewer_api_key or "").strip():
            raise TranslationError("OpenAI", 500, "Translation quality review requires an OpenAI API key.")
        prompt = build_translation_review_prompt(
            source_text=source_script,
            translated_text=translated_script,
            source_lang=source_lang,
            target_lang=target_lang,
            source_channel_name=source_channel_name,
            target_channel_name=target_channel_name,
            sensitive_terms=sensitive_terms,
        )
        review_text = await self._call_adapter_translate(
            provider=reviewer_provider,
            api_key=reviewer_api_key,
            model=reviewer_model,
            prompt=prompt,
            base_url=reviewer_base_url,
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
        provider_base_url: str,
        source_channel_name: str,
        target_channel_name: str,
        sensitive_terms: list[str],
    ) -> str:
        repair_prompt = build_translation_script_repair_prompt(
            source_text=source_script,
            invalid_output=translated_script,
            issues=issues,
            source_lang=source_lang,
            target_lang=target_lang,
            source_channel_name=source_channel_name,
            target_channel_name=target_channel_name,
            sensitive_terms=sensitive_terms,
        )
        repaired = await self._call_adapter_translate(
            provider=provider,
            api_key=api_key,
            model=model,
            prompt=repair_prompt,
            base_url=provider_base_url,
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
        provider_base_url: str,
        source_channel_name: str,
        target_channel_name: str,
        reviewer_required: bool,
        reviewer_provider: str,
        reviewer_api_key: str,
        reviewer_model: str,
        reviewer_base_url: str,
        sensitive_terms: list[str],
    ) -> tuple[str, dict[str, Any] | None]:
        words_in = len(str(source_script or "").split())
        translated_clean = str(translated_script or "").strip()
        words_out = len(translated_clean.split())
        analysis = analyze_translation_quality(
            source_text=source_script,
            translated_text=translated_clean,
            language_code=target_lang,
            words_in=words_in,
            words_out=words_out,
            source_channel_name=source_channel_name,
            target_channel_name=target_channel_name,
        )
        script_issues = list(analysis["issues"])

        if not script_issues:
            review_report = None
            if reviewer_required:
                review_report = await self._review_script_quality(
                    source_script=source_script,
                    translated_script=translated_clean,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    reviewer_provider=reviewer_provider,
                    reviewer_api_key=reviewer_api_key,
                    reviewer_model=reviewer_model,
                    reviewer_base_url=reviewer_base_url,
                    source_channel_name=source_channel_name,
                    target_channel_name=target_channel_name,
                    sensitive_terms=sensitive_terms,
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
            provider_base_url=provider_base_url,
            source_channel_name=source_channel_name,
            target_channel_name=target_channel_name,
            sensitive_terms=sensitive_terms,
        )
        repaired_analysis = analyze_translation_quality(
            source_text=source_script,
            translated_text=repaired_script,
            language_code=target_lang,
            words_in=words_in,
            words_out=len(repaired_script.split()),
            source_channel_name=source_channel_name,
            target_channel_name=target_channel_name,
        )
        repaired_issues = list(repaired_analysis["issues"])
        if repaired_issues:
            raise self._translation_error(
                provider.title(),
                200,
                "Translation quality validation failed after script repair: " + "; ".join(repaired_issues),
                issues=repaired_issues,
            )
        if reviewer_required:
            review_report = await self._review_script_quality(
                source_script=source_script,
                translated_script=repaired_script,
                source_lang=source_lang,
                target_lang=target_lang,
                reviewer_provider=reviewer_provider,
                reviewer_api_key=reviewer_api_key,
                reviewer_model=reviewer_model,
                reviewer_base_url=reviewer_base_url,
                source_channel_name=source_channel_name,
                target_channel_name=target_channel_name,
                sensitive_terms=sensitive_terms,
            )
            if not review_report["passed"]:
                issues = list(review_report["issues"]) or ["LLM quality review rejected the repaired script."]
                raise self._translation_error(
                    reviewer_provider.title(),
                    200,
                    "Translation quality review failed after repair: " + "; ".join(issues),
                    review_report=review_report,
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
        provider_base_url: str = "",
        master_scenes: list[dict[str, Any]] | None = None,
        max_words_per_chunk: int = 800,
        context_tail_words: int = 200,
        channel_name: str = "",
        source_channel_name: str = "",
        target_channel_name: str = "",
        reviewer_required: bool = False,
        reviewer_provider: str = "openai",
        reviewer_api_key: str = "",
        reviewer_model: str = "gpt-4.1-mini",
        reviewer_base_url: str = "",
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
        sensitive_terms = extract_sensitive_terms(
            source_script,
            source_channel_name=source_channel_name,
            target_channel_name=target_channel_name,
            target_lang=target_lang,
        )

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
                    provider_base_url=provider_base_url,
                    total_chunks=total_chunks,
                    channel_name=channel_name,
                    source_channel_name=source_channel_name,
                    target_channel_name=target_channel_name,
                    sensitive_terms=sensitive_terms,
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
                    error_summary=getattr(exc, "error_summary", None),
                    error_categories=list(getattr(exc, "error_categories", []) or []),
                    review_report=getattr(exc, "review_report", None),
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
                provider_base_url=provider_base_url,
                source_channel_name=source_channel_name,
                target_channel_name=target_channel_name,
                reviewer_required=reviewer_required,
                reviewer_provider=reviewer_provider,
                reviewer_api_key=reviewer_api_key,
                reviewer_model=reviewer_model,
                reviewer_base_url=reviewer_base_url,
                sensitive_terms=sensitive_terms,
            )
        except Exception as exc:
            return self._error_result(
                target_lang=target_lang,
                translated_parts=translated_parts,
                chunk_results=chunk_results,
                error_message=str(exc),
                error_summary=getattr(exc, "error_summary", None),
                error_categories=list(getattr(exc, "error_categories", []) or []),
                review_report=getattr(exc, "review_report", None),
            )

        return TranslationResult(
            language_code=target_lang,
            translated_script=final_script,
            chunk_results=chunk_results,
            status="done",
            review_report=review_report,
        )
