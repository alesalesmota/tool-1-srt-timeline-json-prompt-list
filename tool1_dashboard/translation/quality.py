"""Shared deterministic translation quality checks."""

from __future__ import annotations

import re
from typing import Any

from .language_rules import resolve_language_rulepack

_ENGLISH_CTA_PATTERNS = (
    re.compile(r"\bsubscribe to\b", re.IGNORECASE),
    re.compile(r"\bshare this video\b", re.IGNORECASE),
    re.compile(r"\blike this video\b", re.IGNORECASE),
    re.compile(r"\bclick here\b", re.IGNORECASE),
)
_SUSPICIOUS_WORD_EXPANSION_RATIO = 1.6
_SUSPICIOUS_WORD_EXPANSION_MARGIN = 180
_SUSPICIOUS_WORD_COMPRESSION_RATIO = 0.4
_SUSPICIOUS_PUNCTUATION_RUN = re.compile(r"[!?.,:;()\[\]{}<>\"'`~@#$%^&*_+=\\/|-]{5,}")
_TEXT_CORRUPTION_PATTERN = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\ufffd]")
_MIN_PARAGRAPH_WORDS = 6
_SUMMARY_LABELS = {
    "empty_output": "Empty output",
    "source_leakage": "Source text leaked through",
    "paragraph_structure": "Paragraph structure changed too much",
    "duplicate_paragraphs": "Duplicated paragraphs",
    "english_cta": "English CTA leaked through",
    "bad_literal_pattern": "Bad literal wording",
    "source_channel_name": "Source channel name survived",
    "target_channel_name": "Localized channel name is missing",
    "text_corruption": "Corrupted characters",
    "punctuation_noise": "Punctuation corruption",
    "length_expansion": "Suspiciously long output",
    "length_compression": "Suspiciously short output",
    "numeric_refs": "Numeric references changed",
}


def normalize_compare_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def raw_paragraphs(text: str) -> list[str]:
    return [segment.strip() for segment in re.split(r"\n\s*\n+", str(text or "").strip()) if segment.strip()]


def significant_paragraphs(text: str) -> list[str]:
    paragraphs: list[str] = []
    for paragraph in raw_paragraphs(text):
        normalized = normalize_compare_text(paragraph)
        if len(normalized.split()) >= _MIN_PARAGRAPH_WORDS:
            paragraphs.append(normalized)
    return paragraphs


def is_non_english_target(language_code: str) -> bool:
    return resolve_language_rulepack(language_code).code != "en"


def _issue(*, code: str, message: str, category: str, blocking: bool) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "category": category,
        "blocking": blocking,
    }


def summarize_translation_quality_report(report: dict[str, Any]) -> str:
    issues = list(report.get("blocking_issues") or []) or list(report.get("warnings") or [])
    if not issues:
        return "No deterministic blockers."

    labels: list[str] = []
    for issue in issues:
        code = str(issue.get("code") or "").strip()
        label = _SUMMARY_LABELS.get(code) or str(issue.get("message") or "").strip()
        if label and label not in labels:
            labels.append(label)
        if len(labels) >= 2:
            break
    return " and ".join(labels) if labels else "Deterministic quality checks found issues."


def evaluate_translation_quality(
    *,
    source_text: str,
    translated_text: str,
    language_code: str,
    words_in: int | None = None,
    words_out: int | None = None,
    source_channel_name: str = "",
    target_channel_name: str = "",
) -> dict[str, Any]:
    translated_clean = str(translated_text or "").strip()
    blocking_issues: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []

    if not translated_clean:
        report = {
            "passed": False,
            "blocking_issues": [
                _issue(
                    code="empty_output",
                    message="Model returned empty translation.",
                    category="content",
                    blocking=True,
                )
            ],
            "warnings": [],
        }
        report["summary"] = summarize_translation_quality_report(report)
        return report

    if words_in and words_in > 0 and words_out is not None:
        suspicious_long_limit = max(
            int(words_in * _SUSPICIOUS_WORD_EXPANSION_RATIO),
            words_in + _SUSPICIOUS_WORD_EXPANSION_MARGIN,
        )
        suspicious_short_limit = max(1, int(words_in * _SUSPICIOUS_WORD_COMPRESSION_RATIO))
        if words_out > suspicious_long_limit:
            issue = _issue(
                code="length_expansion",
                message="Output is suspiciously long and may contain duplicated or repeated content.",
                category="structure",
                blocking=bool(words_in <= 20 or words_out > suspicious_long_limit * 2),
            )
            if issue["blocking"]:
                blocking_issues.append(issue)
            else:
                warnings.append(issue)
        if words_out < suspicious_short_limit:
            warnings.append(
                _issue(
                    code="length_compression",
                    message="Output is much shorter than the source and may be missing content.",
                    category="structure",
                    blocking=False,
                )
            )

    translated_norm = normalize_compare_text(translated_clean)
    source_paragraphs = significant_paragraphs(source_text)
    translated_paragraphs = significant_paragraphs(translated_clean)
    leaked_source = [paragraph for paragraph in source_paragraphs if paragraph and paragraph in translated_norm]
    if leaked_source:
        blocking_issues.append(
            _issue(
                code="source_leakage",
                message="Translation still contains untranslated source paragraphs.",
                category="fidelity",
                blocking=True,
            )
        )

    if source_paragraphs and translated_paragraphs:
        translated_unique_count = len({paragraph for paragraph in translated_paragraphs})
        if translated_unique_count < len(translated_paragraphs):
            blocking_issues.append(
                _issue(
                    code="duplicate_paragraphs",
                    message="Translation contains duplicated paragraph blocks.",
                    category="structure",
                    blocking=True,
                )
            )
        if abs(len(translated_paragraphs) - len(source_paragraphs)) >= max(2, len(source_paragraphs) // 3):
            blocking_issues.append(
                _issue(
                    code="paragraph_structure",
                    message="Paragraph structure changed too much from the source script.",
                    category="structure",
                    blocking=True,
                )
            )

    if is_non_english_target(language_code) and any(pattern.search(translated_clean) for pattern in _ENGLISH_CTA_PATTERNS):
        blocking_issues.append(
            _issue(
                code="english_cta",
                message="Translation still contains English CTA wording.",
                category="language",
                blocking=True,
            )
        )

    pack = resolve_language_rulepack(language_code)
    for pattern, issue in pack.bad_literal_patterns:
        if re.search(pattern, translated_clean, flags=re.IGNORECASE):
            blocking_issues.append(
                _issue(
                    code="bad_literal_pattern",
                    message=str(issue),
                    category="language",
                    blocking=True,
                )
            )

    if source_channel_name and target_channel_name:
        if re.search(re.escape(source_channel_name), translated_clean, flags=re.IGNORECASE):
            blocking_issues.append(
                _issue(
                    code="source_channel_name",
                    message=f'Translation still contains the source channel name "{source_channel_name}".',
                    category="channel_name",
                    blocking=True,
                )
            )
        if re.search(re.escape(source_channel_name), source_text, flags=re.IGNORECASE) and not re.search(
            re.escape(target_channel_name),
            translated_clean,
            flags=re.IGNORECASE,
        ):
            blocking_issues.append(
                _issue(
                    code="target_channel_name",
                    message=f'Translation did not use the configured channel name "{target_channel_name}".',
                    category="channel_name",
                    blocking=True,
                )
            )

    if _TEXT_CORRUPTION_PATTERN.search(translated_clean):
        blocking_issues.append(
            _issue(
                code="text_corruption",
                message="Translation contains corrupted characters or control symbols.",
                category="format",
                blocking=True,
            )
        )

    if _SUSPICIOUS_PUNCTUATION_RUN.search(translated_clean):
        blocking_issues.append(
            _issue(
                code="punctuation_noise",
                message="Translation contains suspicious punctuation or character noise.",
                category="format",
                blocking=True,
            )
        )

    source_digits = re.findall(r"\d+", str(source_text or ""))
    translated_digits = re.findall(r"\d+", translated_clean)
    if len(source_digits) >= 3 and len(translated_digits) < max(1, len(source_digits) // 2):
        warnings.append(
            _issue(
                code="numeric_refs",
                message="Numeric references changed noticeably and should be checked manually.",
                category="fidelity",
                blocking=False,
            )
        )

    deduped_blocking: list[dict[str, Any]] = []
    seen_blockers: set[tuple[str, str]] = set()
    for issue in blocking_issues:
        key = (str(issue.get("code") or ""), str(issue.get("message") or ""))
        if key in seen_blockers:
            continue
        seen_blockers.add(key)
        deduped_blocking.append(issue)

    deduped_warnings: list[dict[str, Any]] = []
    seen_warnings: set[tuple[str, str]] = set()
    for issue in warnings:
        key = (str(issue.get("code") or ""), str(issue.get("message") or ""))
        if key in seen_warnings:
            continue
        seen_warnings.add(key)
        deduped_warnings.append(issue)

    report = {
        "passed": not deduped_blocking,
        "blocking_issues": deduped_blocking,
        "warnings": deduped_warnings,
    }
    report["summary"] = summarize_translation_quality_report(report)
    return report


def collect_translation_quality_issues(
    *,
    source_text: str,
    translated_text: str,
    language_code: str,
    words_in: int | None = None,
    words_out: int | None = None,
    source_channel_name: str = "",
    target_channel_name: str = "",
) -> list[str]:
    report = evaluate_translation_quality(
        source_text=source_text,
        translated_text=translated_text,
        language_code=language_code,
        words_in=words_in,
        words_out=words_out,
        source_channel_name=source_channel_name,
        target_channel_name=target_channel_name,
    )
    return [str(issue.get("message") or "").strip() for issue in report["blocking_issues"] if str(issue.get("message") or "").strip()]
