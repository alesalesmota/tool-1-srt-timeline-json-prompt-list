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
_MIN_PARAGRAPH_WORDS = 6
_MAX_EXCERPT_CHARS = 180
_DIGIT_PATTERN = re.compile(r"\d")
_REPEATED_CHAR_TOKEN = re.compile(r"(.)\1{5,}")
_ASCII_CONSONANT_CLUSTER = re.compile(r"^[bcdfghjklmnpqrstvwxyz]{8,}$", re.IGNORECASE)

_NEXT_ACTION_HINTS = {
    "empty_output": "Inspect provider/model behavior or adjust the translation prompt.",
    "suspicious_length": "Inspect provider/model output or prompt leakage before retrying.",
    "source_text_leak": "Inspect the translation prompt/profile and verify the source text is fully translated.",
    "english_cta_leak": "Inspect the prompt/profile so CTA language is fully localized.",
    "source_channel_leak": "Inspect the prompt/profile and channel replacement settings.",
    "missing_target_channel_name": "Inspect prompt/profile/config so the localized channel name is inserted.",
    "digits_present": "Adjust prompt and normalization logic so narration writes numbers as words.",
    "gibberish_output": "Inspect provider/model output or prompt because the text is not usable narration.",
    "language_rule_violation": "Inspect prompt/profile and language-specific guidance before retrying.",
}


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


def _trim_excerpt(value: str, limit: int = _MAX_EXCERPT_CHARS) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _excerpt_from_match(text: str, match: re.Match[str] | None, *, radius: int = 36) -> str | None:
    if not match:
        return None
    start = max(0, match.start() - radius)
    end = min(len(text), match.end() + radius)
    return _trim_excerpt(text[start:end])


def _make_finding(
    category: str,
    message: str,
    *,
    offending_excerpt: str | None = None,
    next_action: str | None = None,
    blocking: bool = True,
) -> dict[str, Any]:
    return {
        "category": category,
        "message": message,
        "offending_excerpt": _trim_excerpt(offending_excerpt) if offending_excerpt else None,
        "next_action": next_action or _NEXT_ACTION_HINTS.get(category, "Inspect the translation prompt/profile and retry."),
        "blocking": blocking,
        "scope": "content",
    }


def _dedupe_findings(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for finding in findings:
        key = (
            str(finding.get("category") or "").strip(),
            str(finding.get("message") or "").strip(),
            str(finding.get("offending_excerpt") or "").strip(),
        )
        if key in seen:
            continue
        deduped.append(finding)
        seen.add(key)
    return deduped


def _find_gibberish_excerpt(translated_text: str) -> str | None:
    for raw_token in re.findall(r"\S+", str(translated_text or "")):
        token = raw_token.strip("()[]{}<>\"'.,;:!?")
        if len(token) < 6:
            continue
        if _REPEATED_CHAR_TOKEN.search(token):
            return raw_token
        normalized = re.sub(r"[^A-Za-z]", "", token)
        if len(normalized) >= 8 and _ASCII_CONSONANT_CLUSTER.fullmatch(normalized):
            return raw_token
    return None


def collect_translation_quality_findings(
    *,
    source_text: str,
    translated_text: str,
    language_code: str,
    words_in: int | None = None,
    words_out: int | None = None,
    source_channel_name: str = "",
    target_channel_name: str = "",
) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    translated_clean = str(translated_text or "").strip()
    if not translated_clean:
        return [
            _make_finding(
                "empty_output",
                "Model returned empty translation.",
            )
        ]

    if words_in and words_in > 0 and words_out is not None:
        suspicious_limit = max(
            int(words_in * _SUSPICIOUS_WORD_EXPANSION_RATIO),
            words_in + _SUSPICIOUS_WORD_EXPANSION_MARGIN,
        )
        if words_out > suspicious_limit:
            findings.append(
                _make_finding(
                    "suspicious_length",
                    "Output is suspiciously long and may contain duplicated source text.",
                    offending_excerpt=translated_clean,
                )
            )

    translated_norm = normalize_compare_text(translated_clean)
    leaked_source = [paragraph for paragraph in significant_paragraphs(source_text) if paragraph and paragraph in translated_norm]
    if leaked_source:
        findings.append(
            _make_finding(
                "source_text_leak",
                "Translation still contains untranslated source paragraphs.",
                offending_excerpt=leaked_source[0],
            )
        )

    if is_non_english_target(language_code):
        cta_match = next((pattern.search(translated_clean) for pattern in _ENGLISH_CTA_PATTERNS if pattern.search(translated_clean)), None)
        if cta_match:
            findings.append(
                _make_finding(
                    "english_cta_leak",
                    "Translation still contains English CTA wording.",
                    offending_excerpt=_excerpt_from_match(translated_clean, cta_match),
                )
            )

    pack = resolve_language_rulepack(language_code)
    for pattern, issue in pack.bad_literal_patterns:
        match = re.search(pattern, translated_clean, flags=re.IGNORECASE)
        if match:
            findings.append(
                _make_finding(
                    "language_rule_violation",
                    issue,
                    offending_excerpt=_excerpt_from_match(translated_clean, match),
                )
            )

    if source_channel_name and target_channel_name:
        source_channel_match = re.search(re.escape(source_channel_name), translated_clean, flags=re.IGNORECASE)
        if source_channel_match:
            findings.append(
                _make_finding(
                    "source_channel_leak",
                    f'Translation still contains the source channel name "{source_channel_name}".',
                    offending_excerpt=_excerpt_from_match(translated_clean, source_channel_match),
                )
            )
        if re.search(re.escape(source_channel_name), source_text, flags=re.IGNORECASE) and not re.search(
            re.escape(target_channel_name),
            translated_clean,
            flags=re.IGNORECASE,
        ):
            findings.append(
                _make_finding(
                    "missing_target_channel_name",
                    f'Translation did not use the configured channel name "{target_channel_name}".',
                )
            )

    digit_match = _DIGIT_PATTERN.search(translated_clean)
    if digit_match:
        findings.append(
            _make_finding(
                "digits_present",
                "Translation still contains digits; narration must use numbers written as words.",
                offending_excerpt=_excerpt_from_match(translated_clean, digit_match),
            )
        )

    gibberish_excerpt = _find_gibberish_excerpt(translated_clean)
    if gibberish_excerpt:
        findings.append(
            _make_finding(
                "gibberish_output",
                "Translation contains gibberish or non-word output that is unsafe for narration.",
                offending_excerpt=gibberish_excerpt,
            )
        )

    return _dedupe_findings(findings)


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
    return [
        str(finding.get("message") or "").strip()
        for finding in collect_translation_quality_findings(
            source_text=source_text,
            translated_text=translated_text,
            language_code=language_code,
            words_in=words_in,
            words_out=words_out,
            source_channel_name=source_channel_name,
            target_channel_name=target_channel_name,
        )
        if str(finding.get("message") or "").strip()
    ]
