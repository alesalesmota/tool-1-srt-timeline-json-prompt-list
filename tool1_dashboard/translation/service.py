"""Translation orchestrator — chunk, translate, assemble."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from .adapter import TranslationAdapter, TranslationError
from .chunker import (
    TranslationChunk,
    build_scene_aware_chunks,
    build_text_chunks,
)
from .prompts import (
    build_translation_prompt,
    build_translation_review_prompt,
    extract_sensitive_terms,
)
from .quality import (
    ERROR_CATEGORY_LABELS,
    apply_channel_cta_fallback,
    apply_channel_name_fallback,
    categorize_translation_issue_text,
    collect_translation_quality_findings,
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
    provider: str = ""
    model: str = ""
    category: str | None = None
    offending_excerpt: str | None = None
    next_action: str | None = None
    diagnostics: list[dict[str, Any]] = field(default_factory=list)


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
    provider: str = ""
    model: str = ""
    review_enabled: bool = False
    diagnostics: dict[str, Any] | None = None


class TranslationValidationError(Exception):
    """Raised when deterministic translation checks fail."""

    def __init__(
        self,
        message: str,
        *,
        diagnostics: list[dict[str, Any]] | None = None,
        review_report: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.diagnostics = diagnostics or []
        self.review_report = review_report


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

    async def _translate_chunk(
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
        return translated, len(translated.split())

    @staticmethod
    def _first_diagnostic(diagnostics: list[dict[str, Any]] | None) -> dict[str, Any] | None:
        return diagnostics[0] if diagnostics else None

    @classmethod
    def _summary_message(cls, diagnostics: list[dict[str, Any]] | None, fallback: str) -> str:
        first = cls._first_diagnostic(diagnostics)
        if not first:
            return fallback
        return str(first.get("message") or fallback).strip() or fallback

    @classmethod
    def _recommended_next_action(cls, blockers: list[dict[str, Any]], warnings: list[dict[str, Any]]) -> str | None:
        for item in list(blockers or []) + list(warnings or []):
            action = str(item.get("next_action") or "").strip()
            if action:
                return action
        return None

    @classmethod
    def _build_warning_from_review_issue(cls, issue: str) -> dict[str, Any]:
        return {
            "category": "ai_review_notice",
            "message": str(issue or "").strip() or "AI review reported a minor suggestion.",
            "offending_excerpt": None,
            "next_action": "Optional: inspect the AI review note manually if you want to polish the translation.",
            "blocking": False,
            "scope": "review",
        }

    @classmethod
    def _build_diagnostics_summary(
        cls,
        *,
        status: str,
        provider: str,
        model: str,
        review_enabled: bool,
        blockers: list[dict[str, Any]] | None = None,
        warnings: list[dict[str, Any]] | None = None,
        review_report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        blockers = list(blockers or [])
        warnings = list(warnings or [])
        return {
            "status": status,
            "provider": provider,
            "model": model,
            "review_enabled": review_enabled,
            "blockers": blockers,
            "warnings": warnings,
            "recommended_next_action": cls._recommended_next_action(blockers, warnings),
            "review_report": review_report,
        }
    @classmethod
    def _error_result(
        cls,
        *,
        target_lang: str,
        translated_parts: list[str],
        chunk_results: list[ChunkResult],
        error_message: str,
        provider: str,
        model: str,
        review_enabled: bool,
        diagnostics: list[dict[str, Any]] | None = None,
        error_summary: str | None = None,
        error_categories: list[str] | None = None,
        review_report: dict[str, Any] | None = None,
    ) -> TranslationResult:
        diagnostics = list(diagnostics or [])
        categories = [str(item or "").strip() for item in error_categories or [] if str(item or "").strip()]
        if not categories:
            categories = [
                str(item.get("category") or "").strip()
                for item in diagnostics
                if str(item.get("category") or "").strip()
            ]
        if not categories and error_message:
            categories = [categorize_translation_issue_text(error_message)]
        issue_candidates = [
            str(item.get("message") or "").strip()
            for item in diagnostics
            if str(item.get("message") or "").strip()
        ] or [error_message]
        summary = (
            str(error_summary or "").strip()
            or cls._summary_message(diagnostics, "")
            or summarize_translation_categories(categories, issues=issue_candidates)
        )
        return TranslationResult(
            language_code=target_lang,
            translated_script="\n\n".join(translated_parts).strip(),
            chunk_results=chunk_results,
            status="error",
            error_message=error_message,
            error_summary=summary or None,
            error_categories=categories,
            review_report=review_report,
            provider=provider,
            model=model,
            review_enabled=review_enabled,
            diagnostics=cls._build_diagnostics_summary(
                status="error",
                provider=provider,
                model=model,
                review_enabled=review_enabled,
                blockers=diagnostics,
                warnings=[],
                review_report=review_report,
            ),
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
    def _apply_channel_fallbacks(
        translated_text: str,
        *,
        language_code: str,
        source_channel_name: str,
        target_channel_name: str,
    ) -> str:
        normalized = apply_channel_name_fallback(
            translated_text,
            source_channel_name=source_channel_name,
            target_channel_name=target_channel_name,
        )
        return apply_channel_cta_fallback(
            normalized,
            language_code=language_code,
            target_channel_name=target_channel_name,
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
        if not bool(sanitized.get("passed")) and cls._review_scores_meet_quality_bar(scores):
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

    @staticmethod
    def _classify_provider_failure(
        exc: Exception,
        *,
        provider: str,
        model: str,
        scope: str = "provider",
    ) -> dict[str, Any]:
        raw_message = str(exc or "").strip() or "Unknown provider error."
        provider_name = str(getattr(exc, "provider", None) or provider or "Provider").strip() or "Provider"
        status = getattr(exc, "status", None)
        message_lower = raw_message.lower()

        if (
            status == 429
            and any(keyword in message_lower for keyword in ("quota", "insufficient", "billing", "credit"))
        ):
            category = "quota_exceeded"
            message = f"{provider_name} quota exceeded."
            next_action = "Switch provider/key/model or wait for quota reset."
        elif status in {401, 403} or any(
            keyword in message_lower
            for keyword in ("api key", "invalid key", "authentication", "unauthorized", "permission denied")
        ):
            category = "invalid_api_key"
            message = f"{provider_name} API key is invalid or missing access."
            next_action = "Fix the provider credentials for this translation profile."
        elif status == 429 or any(keyword in message_lower for keyword in ("rate limit", "too many requests")):
            category = "rate_limited"
            message = f"{provider_name} rate limited the translation request."
            next_action = "Retry later or change provider/model."
        elif isinstance(exc, httpx.TimeoutException) or status in {408, 504} or "timeout" in message_lower or "timed out" in message_lower:
            category = "network_timeout"
            message = f"{provider_name} timed out during translation."
            next_action = "Retry later or inspect connectivity/provider health."
        elif any(keyword in message_lower for keyword in ("empty response", "returned empty", "empty output")):
            category = "empty_output"
            message = f"{provider_name} returned empty output."
            next_action = "Inspect provider/model behavior or adjust the translation prompt."
        else:
            category = "provider_error"
            message = f"{provider_name} returned an error during translation."
            next_action = "Inspect provider response, credentials, and model configuration."

        return {
            "category": category,
            "message": message,
            "offending_excerpt": None,
            "next_action": next_action,
            "blocking": True,
            "scope": scope,
            "provider": provider,
            "model": model,
            "raw_error": raw_message,
        }

    @staticmethod
    def _classify_unexpected_failure(
        exc: Exception,
        *,
        provider: str,
        model: str,
        scope: str = "system",
    ) -> dict[str, Any]:
        raw_message = str(exc or "").strip() or exc.__class__.__name__
        return {
            "category": "system_error",
            "message": "Translation pipeline raised an unexpected error.",
            "offending_excerpt": None,
            "next_action": "Inspect the application logs and translation pipeline code before retrying.",
            "blocking": True,
            "scope": scope,
            "provider": provider,
            "model": model,
            "raw_error": raw_message,
        }

    @classmethod
    def _raise_if_invalid_chunk(
        cls,
        *,
        chunk: TranslationChunk,
        translated_text: str,
        target_lang: str,
        source_channel_name: str,
        target_channel_name: str,
    ) -> None:
        findings = collect_translation_quality_findings(
            source_text=chunk.text,
            translated_text=translated_text,
            language_code=target_lang,
            words_in=chunk.word_count,
            words_out=len(str(translated_text or "").split()),
            source_channel_name=source_channel_name,
            target_channel_name=target_channel_name,
        )
        if findings:
            raise TranslationValidationError(
                cls._summary_message(findings, "Translation chunk failed deterministic validation."),
                diagnostics=findings,
            )

    async def _apply_script_quality_gate(
        self,
        *,
        source_script: str,
        translated_script: str,
        source_lang: str,
        target_lang: str,
        source_channel_name: str,
        target_channel_name: str,
        review_source_channel_name: str,
        review_target_channel_name: str,
        reviewer_required: bool,
        reviewer_provider: str,
        reviewer_api_key: str,
        reviewer_model: str,
        reviewer_base_url: str,
        sensitive_terms: list[str],
    ) -> tuple[str, dict[str, Any] | None, list[dict[str, Any]]]:
        translated_clean = str(translated_script or "").strip()
        findings = collect_translation_quality_findings(
            source_text=source_script,
            translated_text=translated_clean,
            language_code=target_lang,
            words_in=len(str(source_script or "").split()),
            words_out=len(translated_clean.split()),
            source_channel_name=source_channel_name,
            target_channel_name=target_channel_name,
        )
        if findings:
            raise TranslationValidationError(
                self._summary_message(findings, "Translation failed deterministic validation."),
                diagnostics=findings,
            )

        warnings: list[dict[str, Any]] = []
        review_report = None
        if reviewer_required:
            try:
                review_report = await self._review_script_quality(
                    source_script=source_script,
                    translated_script=translated_clean,
                    source_lang=source_lang,
                    target_lang=target_lang,
                    reviewer_provider=reviewer_provider,
                    reviewer_api_key=reviewer_api_key,
                    reviewer_model=reviewer_model,
                    reviewer_base_url=reviewer_base_url,
                    source_channel_name=review_source_channel_name,
                    target_channel_name=review_target_channel_name,
                    sensitive_terms=sensitive_terms,
                )
            except TranslationError as exc:
                diagnostic = self._classify_provider_failure(
                    exc,
                    provider="openai",
                    model=reviewer_model,
                    scope="review",
                )
                diagnostic["message"] = "AI review failed: " + str(diagnostic.get("message") or "").strip()
                raise TranslationValidationError(
                    str(diagnostic.get("message") or "AI review failed."),
                    diagnostics=[diagnostic],
                ) from exc
            except Exception as exc:
                diagnostic = self._classify_unexpected_failure(
                    exc,
                    provider="openai",
                    model=reviewer_model,
                    scope="review",
                )
                diagnostic["message"] = "AI review failed: translation reviewer crashed."
                raise TranslationValidationError(
                    str(diagnostic.get("message") or "AI review failed."),
                    diagnostics=[diagnostic],
                ) from exc
            if not review_report["passed"]:
                review_findings = [
                    {
                        "category": "ai_review_failed",
                        "message": str(issue or "").strip() or "Optional AI review rejected the translation.",
                        "offending_excerpt": None,
                        "next_action": "Optional AI review is enabled. Disable it or adjust the translation/review prompt if you want this extra gate.",
                        "blocking": True,
                        "scope": "review",
                    }
                    for issue in list(review_report.get("issues") or []) or ["Optional AI review rejected the translation."]
                ]
                raise TranslationValidationError(
                    self._summary_message(review_findings, "Optional AI review rejected the translation."),
                    diagnostics=review_findings,
                    review_report=review_report,
                )
            warnings = [
                self._build_warning_from_review_issue(issue)
                for issue in list(review_report.get("issues") or [])
                if str(issue or "").strip()
            ]
        return translated_clean, review_report, warnings

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
        enforced_source_channel_name: str = "",
        enforced_target_channel_name: str = "",
        reviewer_required: bool = False,
        reviewer_provider: str = "openai",
        reviewer_api_key: str = "",
        reviewer_model: str = "gpt-4.1-mini",
        reviewer_base_url: str = "",
    ) -> TranslationResult:
        """Translate a full script, chunk by chunk."""
        if master_scenes:
            chunks = build_scene_aware_chunks(master_scenes, max_words_per_chunk, source_script=source_script)
        else:
            chunks = build_text_chunks(source_script, max_words_per_chunk)

        if not chunks:
            return TranslationResult(
                language_code=target_lang,
                translated_script="",
                status="done",
                provider=provider,
                model=model,
                review_enabled=reviewer_required,
                diagnostics=self._build_diagnostics_summary(
                    status="done",
                    provider=provider,
                    model=model,
                    review_enabled=reviewer_required,
                    blockers=[],
                    warnings=[],
                    review_report=None,
                ),
            )

        total_chunks = len(chunks)
        context = ""
        chunk_results: list[ChunkResult] = []
        translated_parts: list[str] = []
        effective_source_channel_name = str(source_channel_name or enforced_source_channel_name or "").strip()
        effective_target_channel_name = str(target_channel_name or enforced_target_channel_name or "").strip()
        sensitive_terms = extract_sensitive_terms(
            source_script,
            source_channel_name=effective_source_channel_name,
            target_channel_name=effective_target_channel_name,
            target_lang=target_lang,
        )

        for chunk in chunks:
            try:
                translated, words_out = await self._translate_chunk(
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
                translated = self._apply_channel_fallbacks(
                    translated,
                    language_code=target_lang,
                    source_channel_name=enforced_source_channel_name,
                    target_channel_name=enforced_target_channel_name,
                )
                self._raise_if_invalid_chunk(
                    chunk=chunk,
                    translated_text=translated,
                    target_lang=target_lang,
                    source_channel_name=enforced_source_channel_name,
                    target_channel_name=enforced_target_channel_name,
                )
                chunk_results.append(
                    ChunkResult(
                        chunk_index=chunk.index,
                        scene_ids=chunk.scene_ids,
                        translated_text=translated,
                        words_in=chunk.word_count,
                        words_out=words_out,
                        status="ok",
                        provider=provider,
                        model=model,
                    )
                )
                translated_parts.append(translated)
                if context_tail_words > 0:
                    words = translated.split()
                    context = " ".join(words[-context_tail_words:])
                else:
                    context = ""
            except TranslationValidationError as exc:
                diagnostics = list(exc.diagnostics or [])
                first = self._first_diagnostic(diagnostics)
                log.warning(
                    "Translation chunk %d/%d failed deterministic validation: %s",
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
                        error=self._summary_message(diagnostics, str(exc)),
                        provider=provider,
                        model=model,
                        category=first.get("category") if first else None,
                        offending_excerpt=first.get("offending_excerpt") if first else None,
                        next_action=first.get("next_action") if first else None,
                        diagnostics=diagnostics,
                    )
                )
                return self._error_result(
                    target_lang=target_lang,
                    translated_parts=translated_parts,
                    chunk_results=chunk_results,
                    error_message=self._summary_message(diagnostics, str(exc)),
                    provider=provider,
                    model=model,
                    review_enabled=reviewer_required,
                    diagnostics=diagnostics,
                )
            except TranslationError as exc:
                diagnostic = self._classify_provider_failure(
                    exc,
                    provider=provider,
                    model=model,
                )
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
                        provider=provider,
                        model=model,
                        category=str(diagnostic.get("category") or "").strip() or None,
                        offending_excerpt=diagnostic.get("offending_excerpt"),
                        next_action=diagnostic.get("next_action"),
                        diagnostics=[diagnostic],
                    )
                )
                return self._error_result(
                    target_lang=target_lang,
                    translated_parts=translated_parts,
                    chunk_results=chunk_results,
                    error_message=str(diagnostic.get("message") or str(exc)),
                    provider=provider,
                    model=model,
                    review_enabled=reviewer_required,
                    diagnostics=[diagnostic],
                    error_summary=getattr(exc, "error_summary", None),
                    error_categories=list(getattr(exc, "error_categories", []) or []),
                    review_report=getattr(exc, "review_report", None),
                )
            except Exception as exc:
                diagnostic = self._classify_unexpected_failure(
                    exc,
                    provider=provider,
                    model=model,
                )
                log.exception(
                    "Translation chunk %d/%d crashed unexpectedly.",
                    chunk.index + 1,
                    total_chunks,
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
                        provider=provider,
                        model=model,
                        category=str(diagnostic.get("category") or "").strip() or None,
                        offending_excerpt=diagnostic.get("offending_excerpt"),
                        next_action=diagnostic.get("next_action"),
                        diagnostics=[diagnostic],
                    )
                )
                return self._error_result(
                    target_lang=target_lang,
                    translated_parts=translated_parts,
                    chunk_results=chunk_results,
                    error_message=str(diagnostic.get("message") or str(exc)),
                    provider=provider,
                    model=model,
                    review_enabled=reviewer_required,
                    diagnostics=[diagnostic],
                )

        translated_script = "\n\n".join(translated_parts).strip()
        translated_script = self._apply_channel_fallbacks(
            translated_script,
            language_code=target_lang,
            source_channel_name=enforced_source_channel_name,
            target_channel_name=enforced_target_channel_name,
        )
        try:
            final_script, review_report, warnings = await self._apply_script_quality_gate(
                source_script=source_script,
                translated_script=translated_script,
                source_lang=source_lang,
                target_lang=target_lang,
                source_channel_name=enforced_source_channel_name,
                target_channel_name=enforced_target_channel_name,
                review_source_channel_name=source_channel_name or enforced_source_channel_name,
                review_target_channel_name=target_channel_name or enforced_target_channel_name,
                reviewer_required=reviewer_required,
                reviewer_provider=reviewer_provider,
                reviewer_api_key=reviewer_api_key,
                reviewer_model=reviewer_model,
                reviewer_base_url=reviewer_base_url,
                sensitive_terms=sensitive_terms,
            )
        except TranslationValidationError as exc:
            diagnostics = list(exc.diagnostics or [])
            return self._error_result(
                target_lang=target_lang,
                translated_parts=translated_parts,
                chunk_results=chunk_results,
                error_message=self._summary_message(diagnostics, str(exc)),
                provider=provider,
                model=model,
                review_enabled=reviewer_required,
                diagnostics=diagnostics,
                review_report=exc.review_report,
            )
        except TranslationError as exc:
            diagnostic = self._classify_provider_failure(
                exc,
                provider=provider,
                model=model,
            )
            return self._error_result(
                target_lang=target_lang,
                translated_parts=translated_parts,
                chunk_results=chunk_results,
                error_message=str(diagnostic.get("message") or str(exc)),
                provider=provider,
                model=model,
                review_enabled=reviewer_required,
                diagnostics=[diagnostic],
                error_summary=getattr(exc, "error_summary", None),
                error_categories=list(getattr(exc, "error_categories", []) or []),
                review_report=getattr(exc, "review_report", None),
            )
        except Exception as exc:
            diagnostic = self._classify_unexpected_failure(
                exc,
                provider=provider,
                model=model,
            )
            return self._error_result(
                target_lang=target_lang,
                translated_parts=translated_parts,
                chunk_results=chunk_results,
                error_message=str(diagnostic.get("message") or str(exc)),
                provider=provider,
                model=model,
                review_enabled=reviewer_required,
                diagnostics=[diagnostic],
            )

        return TranslationResult(
            language_code=target_lang,
            translated_script=final_script,
            chunk_results=chunk_results,
            status="done",
            review_report=review_report,
            provider=provider,
            model=model,
            review_enabled=reviewer_required,
            diagnostics=self._build_diagnostics_summary(
                status="done",
                provider=provider,
                model=model,
                review_enabled=reviewer_required,
                blockers=[],
                warnings=warnings,
                review_report=review_report,
            ),
        )
