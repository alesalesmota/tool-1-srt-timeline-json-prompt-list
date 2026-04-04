"""Shared deterministic translation quality checks."""

from __future__ import annotations

import re

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
    issues: list[str] = []
    translated_clean = str(translated_text or "").strip()
    if not translated_clean:
        return ["Model returned empty translation."]

    if words_in and words_in > 0 and words_out is not None:
        suspicious_limit = max(
            int(words_in * _SUSPICIOUS_WORD_EXPANSION_RATIO),
            words_in + _SUSPICIOUS_WORD_EXPANSION_MARGIN,
        )
        if words_out > suspicious_limit:
            issues.append("Output is suspiciously long and may contain duplicated source text.")

    translated_norm = normalize_compare_text(translated_clean)
    leaked_source = [paragraph for paragraph in significant_paragraphs(source_text) if paragraph and paragraph in translated_norm]
    if leaked_source:
        issues.append("Translation still contains untranslated source paragraphs.")

    if is_non_english_target(language_code) and any(pattern.search(translated_clean) for pattern in _ENGLISH_CTA_PATTERNS):
        issues.append("Translation still contains English CTA wording.")

    pack = resolve_language_rulepack(language_code)
    for pattern, issue in pack.bad_literal_patterns:
        if re.search(pattern, translated_clean, flags=re.IGNORECASE):
            issues.append(issue)

    if source_channel_name and target_channel_name:
        if re.search(re.escape(source_channel_name), translated_clean, flags=re.IGNORECASE):
            issues.append(f'Translation still contains the source channel name "{source_channel_name}".')
        if re.search(re.escape(source_channel_name), source_text, flags=re.IGNORECASE) and not re.search(
            re.escape(target_channel_name),
            translated_clean,
            flags=re.IGNORECASE,
        ):
            issues.append(f'Translation did not use the configured channel name "{target_channel_name}".')

    deduped: list[str] = []
    for issue in issues:
        if issue not in deduped:
            deduped.append(issue)
    return deduped
