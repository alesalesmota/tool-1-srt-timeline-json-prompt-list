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
    "duplication": "Inspect provider/model output because the translation contains duplicated passages.",
    "source_text_leak": "Inspect the translation prompt/profile and verify the source text is fully translated.",
    "english_cta_leak": "Inspect the prompt/profile so CTA language is fully localized.",
    "source_channel_leak": "Inspect the prompt/profile and channel replacement settings.",
    "missing_target_channel_name": "Inspect prompt/profile/config so the localized channel name is inserted.",
    "digits_present": "Adjust prompt and normalization logic so narration writes numbers as words.",
    "gibberish_output": "Inspect provider/model output or prompt because the text is not usable narration.",
    "language_rule_violation": "Inspect prompt/profile and language-specific guidance before retrying.",
}

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
    "quota_exceeded": "Quota exceeded",
    "invalid_api_key": "Invalid API key",
    "rate_limited": "Rate limited",
    "network_timeout": "Timeout",
    "source_text_leak": "Source text leaked",
    "english_cta_leak": "English CTA leaked",
    "source_channel_leak": "Source channel leaked",
    "missing_target_channel_name": "Target channel missing",
    "digits_present": "Digits present",
    "gibberish_output": "Gibberish output",
    "language_rule_violation": "Language rule issue",
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


def _normalized_paragraphs(
    text: str,
    *,
    min_words: int | None = None,
) -> list[str]:
    paragraphs: list[str] = []
    for paragraph in re.split(r"\n\s*\n+", str(text or "").strip()):
        normalized = normalize_compare_text(paragraph)
        if not normalized:
            continue
        if min_words is not None and len(normalized.split()) < min_words:
            continue
        paragraphs.append(normalized)
    return paragraphs


def significant_paragraphs(text: str) -> list[str]:
    return _normalized_paragraphs(text, min_words=_MIN_PARAGRAPH_WORDS)


def structural_paragraphs(text: str) -> list[str]:
    return _normalized_paragraphs(text)


def _paragraph_positions(paragraphs: list[str]) -> dict[str, list[int]]:
    positions: dict[str, list[int]] = {}
    for index, paragraph in enumerate(paragraphs):
        positions.setdefault(paragraph, []).append(index)
    return positions


def _repeated_paragraph_positions(
    paragraphs: list[str],
    *,
    min_words: int | None = _MIN_PARAGRAPH_WORDS,
) -> list[tuple[str, tuple[int, ...]]]:
    return [
        (paragraph, tuple(indices))
        for paragraph, indices in _paragraph_positions(paragraphs).items()
        if len(indices) > 1 and (min_words is None or len(paragraph.split()) >= min_words)
    ]


def _adjacent_repeated_spans(
    paragraphs: list[str],
    *,
    max_span: int = 3,
    min_total_words: int | None = _MIN_PARAGRAPH_WORDS,
) -> list[tuple[tuple[int, int], list[str]]]:
    spans: list[tuple[tuple[int, int], list[str]]] = []
    total = len(paragraphs)
    if total < 2:
        return spans
    max_window = min(max_span, total // 2)
    for span_len in range(1, max_window + 1):
        for start in range(0, total - (span_len * 2) + 1):
            left = paragraphs[start:start + span_len]
            right = paragraphs[start + span_len:start + (span_len * 2)]
            total_words = sum(len(paragraph.split()) for paragraph in left)
            if left == right and (min_total_words is None or total_words >= min_total_words):
                spans.append(((start, span_len), left))
    return spans


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


def apply_channel_name_fallback(
    translated_script: str,
    *,
    source_channel_name: str,
    target_channel_name: str,
) -> str:
    if not translated_script or not source_channel_name or not target_channel_name:
        return translated_script
    if normalize_compare_text(source_channel_name) == normalize_compare_text(target_channel_name):
        return translated_script
    pattern = re.compile(re.escape(source_channel_name), re.IGNORECASE)
    return pattern.sub(target_channel_name, translated_script)


def categorize_translation_issue_text(issue: Any) -> str:
    text = normalize_compare_text(str(issue or ""))
    if not text:
        return "provider_error"
    if "quota exceeded" in text or ("quota" in text and "exceeded" in text):
        return "quota_exceeded"
    if "invalid api key" in text or "api key is invalid" in text or "authentication" in text or "unauthorized" in text:
        return "invalid_api_key"
    if "rate limited" in text or "too many requests" in text:
        return "rate_limited"
    if "timed out" in text or "timeout" in text:
        return "network_timeout"
    if "empty translation" in text or "empty output" in text or "empty chunk" in text:
        return "empty_output"
    if "digits" in text and "words" in text:
        return "digits_present"
    if "gibberish" in text or "non-word output" in text:
        return "gibberish_output"
    if "source channel" in text:
        return "source_channel_leak"
    if "configured channel name" in text or "target channel name" in text:
        return "missing_target_channel_name"
    if "untranslated source paragraphs" in text:
        return "source_text_leak"
    if "english cta wording" in text:
        return "english_cta_leak"
    for category, fragments in _REVIEW_CATEGORY_PATTERNS:
        if any(fragment in text for fragment in fragments):
            return category
    if "failed to connect" in text or "connection refused" in text:
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


def _join_excerpt_lines(lines: list[str]) -> str | None:
    cleaned = [str(line or "").strip() for line in lines if str(line or "").strip()]
    if not cleaned:
        return None
    return _trim_excerpt(" / ".join(cleaned))


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

    source_paragraphs = structural_paragraphs(source_text)
    translated_paragraphs = structural_paragraphs(translated_clean)
    source_adjacent_spans = {
        key
        for key, _ in _adjacent_repeated_spans(source_paragraphs)
    }
    unexpected_adjacent_span = next(
        (
            (key, lines)
            for key, lines in _adjacent_repeated_spans(translated_paragraphs)
            if key not in source_adjacent_spans
        ),
        None,
    )
    if str(source_text or "").strip() and unexpected_adjacent_span:
        (_, span_len), span_lines = unexpected_adjacent_span
        findings.append(
            _make_finding(
                "duplication",
                "Translation contains a duplicated adjacent paragraph span."
                if span_len > 1
                else "Translation contains a duplicated adjacent paragraph.",
                offending_excerpt=_join_excerpt_lines(span_lines),
            )
        )

    source_repeated_positions = {
        positions
        for _, positions in _repeated_paragraph_positions(source_paragraphs)
    }
    translated_repeated_positions = _repeated_paragraph_positions(translated_paragraphs)
    unexpected_duplicate = next(
        (
            (paragraph, positions)
            for paragraph, positions in translated_repeated_positions
            if positions not in source_repeated_positions
        ),
        None,
    )
    if str(source_text or "").strip() and unexpected_duplicate:
        duplicate_excerpt, _ = unexpected_duplicate
        findings.append(
            _make_finding(
                "duplication",
                "Translation contains duplicated translated paragraphs that are not repeated in the source chunk structure.",
                offending_excerpt=duplicate_excerpt,
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
    findings = collect_translation_quality_findings(
        source_text=source_text,
        translated_text=translated_text,
        language_code=language_code,
        words_in=words_in,
        words_out=words_out,
        source_channel_name=source_channel_name,
        target_channel_name=target_channel_name,
    )
    issues = [
        str(finding.get("message") or "").strip()
        for finding in findings
        if str(finding.get("message") or "").strip()
    ]
    categories: list[str] = []
    for finding in findings:
        category = str(finding.get("category") or "").strip()
        if category and category not in categories:
            categories.append(category)
    return {
        "issues": issues,
        "categories": categories,
        "summary": summarize_translation_categories(categories, issues=issues),
        "findings": findings,
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
