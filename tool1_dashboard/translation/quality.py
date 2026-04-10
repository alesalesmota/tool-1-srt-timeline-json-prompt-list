"""Shared deterministic translation quality checks."""

from __future__ import annotations

from collections import Counter
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
_MIN_PARAGRAPH_WORDS = 6

ERROR_CATEGORY_LABELS: dict[str, str] = {
    "wrong_name": "Wrong names",
    "leftover_source_language": "Leftover English",
    "literal_phrasing": "Awkward phrasing",
    "faithfulness": "Meaning drift",
    "cta_quality": "CTA needs rewrite",
    "channel_name": "Channel name issue",
    "duplication": "Duplicated text",
    "empty_output": "Empty output",
    "provider_error": "Provider unavailable",
}

_REVIEW_CATEGORY_PATTERNS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "wrong_name",
        (
            "wrong name",
            "wrong names",
            "proper name",
            "historical name",
            "historical names",
            "historical figure",
            "name should stay",
        ),
    ),
    (
        "leftover_source_language",
        (
            "leftover english",
            "stayed in english",
            "left in english",
            "source-language",
            "source language",
            "untranslated",
            "english phrase",
            "english wording",
        ),
    ),
    (
        "cta_quality",
        (
            "cta",
            "call to action",
            "subscribe",
            "share this video",
            "click here",
            "like this video",
        ),
    ),
    (
        "channel_name",
        (
            "channel name",
            "channel-name",
        ),
    ),
    (
        "duplication",
        (
            "duplicate",
            "duplicated",
            "repeated paragraph",
            "repeated line",
        ),
    ),
    (
        "literal_phrasing",
        (
            "awkward",
            "unnatural",
            "literal",
            "robotic",
            "calque",
            "stiff wording",
            "broken wording",
            "phrasing",
        ),
    ),
    (
        "faithfulness",
        (
            "mistranslation",
            "meaning drift",
            "meaning changed",
            "not faithful",
            "incorrect",
            "inaccurate",
            "wrong historical",
            "faithfulness",
        ),
    ),
)


def normalize_compare_text(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())


def significant_paragraphs(text: str) -> list[str]:
    paragraphs: list[str] = []
    for paragraph in re.split(r"\n\s*\n+", str(text or "").strip()):
        normalized = normalize_compare_text(paragraph)
        if len(normalized.split()) >= _MIN_PARAGRAPH_WORDS:
            paragraphs.append(normalized)
    return paragraphs


def is_non_english_target(language_code: str) -> bool:
    return resolve_language_rulepack(language_code).code != "en"


def apply_channel_cta_fallback(
    translated_script: str,
    *,
    language_code: str,
    target_channel_name: str,
) -> str:
    if not translated_script or not target_channel_name:
        return translated_script
    pack = resolve_language_rulepack(language_code)
    if pack.code == "en":
        return translated_script
    replacement = pack.cta_template.format(channel=target_channel_name)
    pattern = re.compile(rf"\bSubscribe to\s+{re.escape(target_channel_name)}\b", re.IGNORECASE)
    return pattern.sub(replacement, translated_script)


def categorize_translation_issue_text(issue: Any) -> str:
    text = normalize_compare_text(str(issue or ""))
    if not text:
        return "provider_error"
    if "empty translation" in text or "empty output" in text or "empty chunk" in text:
        return "empty_output"
    for category, fragments in _REVIEW_CATEGORY_PATTERNS:
        if any(fragment in text for fragment in fragments):
            return category
    if "failed to connect" in text or "connection refused" in text or "timed out" in text:
        return "provider_error"
    return "faithfulness"


def summarize_translation_categories(
    categories: list[str],
    *,
    issues: list[str] | None = None,
    max_items: int = 2,
) -> str:
    labels: list[str] = []
    for category in categories:
        label = ERROR_CATEGORY_LABELS.get(category)
        if label and label not in labels:
            labels.append(label)
        if len(labels) >= max_items:
            break
    if not labels and issues:
        return str(issues[0] or "").strip()
    if not labels:
        return ""
    if len(labels) == 1:
        return labels[0]
    return f"{labels[0]} and {labels[1]}"


def _append_issue(
    issues: list[str],
    categories: list[str],
    *,
    issue: str,
    category: str,
) -> None:
    issue_text = str(issue or "").strip()
    if issue_text and issue_text not in issues:
        issues.append(issue_text)
    category_text = str(category or "").strip()
    if category_text and category_text not in categories:
        categories.append(category_text)


def analyze_translation_quality(
    *,
    source_text: str,
    translated_text: str,
    language_code: str,
    words_in: int | None = None,
    words_out: int | None = None,
    source_channel_name: str = "",
    target_channel_name: str = "",
) -> dict[str, Any]:
    issues: list[str] = []
    categories: list[str] = []
    translated_clean = str(translated_text or "").strip()
    if not translated_clean:
        _append_issue(
            issues,
            categories,
            issue="Model returned empty translation.",
            category="empty_output",
        )
        return {
            "issues": issues,
            "categories": categories,
            "summary": summarize_translation_categories(categories, issues=issues),
        }

    if words_in and words_in > 0 and words_out is not None:
        suspicious_limit = max(
            int(words_in * _SUSPICIOUS_WORD_EXPANSION_RATIO),
            words_in + _SUSPICIOUS_WORD_EXPANSION_MARGIN,
        )
        if words_out > suspicious_limit:
            _append_issue(
                issues,
                categories,
                issue="Output is suspiciously long and may contain duplicated source text.",
                category="duplication",
            )

    translated_norm = normalize_compare_text(translated_clean)
    leaked_source = [paragraph for paragraph in significant_paragraphs(source_text) if paragraph and paragraph in translated_norm]
    if leaked_source:
        _append_issue(
            issues,
            categories,
            issue="Translation still contains untranslated source paragraphs.",
            category="leftover_source_language",
        )

    if is_non_english_target(language_code) and any(pattern.search(translated_clean) for pattern in _ENGLISH_CTA_PATTERNS):
        _append_issue(
            issues,
            categories,
            issue="Translation still contains English CTA wording.",
            category="leftover_source_language",
        )
        _append_issue(
            issues,
            categories,
            issue="CTA wording still needs a natural localized rewrite.",
            category="cta_quality",
        )

    translated_paragraphs = significant_paragraphs(translated_clean)
    duplicate_counts = Counter(translated_paragraphs)
    if str(source_text or "").strip() and any(count > 1 for count in duplicate_counts.values()):
        _append_issue(
            issues,
            categories,
            issue="Translation contains duplicated translated paragraphs.",
            category="duplication",
        )

    pack = resolve_language_rulepack(language_code)
    for pattern, issue in pack.bad_literal_patterns:
        if re.search(pattern, translated_clean, flags=re.IGNORECASE):
            _append_issue(
                issues,
                categories,
                issue=issue,
                category="literal_phrasing",
            )

    if source_channel_name and target_channel_name:
        if re.search(re.escape(source_channel_name), translated_clean, flags=re.IGNORECASE):
            _append_issue(
                issues,
                categories,
                issue=f'Translation still contains the source channel name "{source_channel_name}".',
                category="channel_name",
            )
        if re.search(re.escape(source_channel_name), source_text, flags=re.IGNORECASE) and not re.search(
            re.escape(target_channel_name),
            translated_clean,
            flags=re.IGNORECASE,
        ):
            _append_issue(
                issues,
                categories,
                issue=f'Translation did not use the configured channel name "{target_channel_name}".',
                category="channel_name",
            )

    return {
        "issues": issues,
        "categories": categories,
        "summary": summarize_translation_categories(categories, issues=issues),
    }


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
    return analyze_translation_quality(
        source_text=source_text,
        translated_text=translated_text,
        language_code=language_code,
        words_in=words_in,
        words_out=words_out,
        source_channel_name=source_channel_name,
        target_channel_name=target_channel_name,
    )["issues"]
