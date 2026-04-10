"""Translation prompt templates — ported from TRADUTOR app.js."""

from __future__ import annotations

import json

from .language_rules import display_language_name, prompt_guidance_lines, resolve_language_rulepack

DEFAULT_TRANSLATION_PROMPT = (
    "You are a professional translator specializing in natural, fluent "
    "translations that preserve the original author's voice, tone, rhythm, "
    "and style.\n"
    "\n"
    "Your task: Translate the following text from {source_lang} to {target_lang}.\n"
    "\n"
    "STRICT RULES:\n"
    "1. Preserve the EXACT tone, rhythm, and style of the original "
    "(formal, casual, dramatic, humorous, etc.)\n"
    "2. Sound completely NATURAL in {target_lang} — never literal or robotic\n"
    "3. Keep the same paragraph structure and line breaks as the original\n"
    "4. Do NOT add, remove, or summarize any content\n"
    "5. Aim to keep the translated text approximately the same length as the original\n"
    "6. If the text contains names, brands, or technical terms, keep them as-is\n"
    "7. Output ONLY the translated text — no explanations, no headers\n"
    "8. Translate EVERYTHING into {target_lang}; do not leave any sentence, paragraph, or CTA in the source language\n"
    "9. Do NOT output both the original text and the translation; never duplicate source paragraphs\n"
    "10. If the text contains calls to action like subscribe, share, or tell someone, translate those calls to action fully into {target_lang}\n"
    "{language_guidance}"
    "{channel_name_instruction}"
    "{chunk_note}"
    "{context_section}\n"
    "\n"
    "[TEXT TO TRANSLATE]:\n"
    "{text}"
)

DEFAULT_TRANSLATION_REPAIR_PROMPT = (
    "Your previous translation output was invalid.\n"
    "\n"
    "Repair it by translating the source text from {source_lang} to {target_lang} again.\n"
    "\n"
    "STRICT REPAIR RULES:\n"
    "1. Output ONLY the corrected translation in {target_lang}\n"
    "2. Do NOT include any source-language lines, paragraphs, or duplicated original text\n"
    "3. Keep the same paragraph structure and line breaks as the source text\n"
    "4. Fully translate all CTA lines and action phrases into {target_lang}\n"
    "5. Preserve names, brands, and technical terms unless a channel replacement instruction says otherwise\n"
    "6. If a rejected line sounded literal or awkward, rewrite it idiomatically instead of reusing the same wording\n"
    "{language_guidance}"
    "{channel_name_instruction}"
    "{context_section}\n"
    "\n"
    "[WHY THE PREVIOUS OUTPUT WAS REJECTED]:\n"
    "{issues}\n"
    "\n"
    "[SOURCE TEXT TO TRANSLATE]:\n"
    "{text}\n"
    "\n"
    "[REJECTED OUTPUT FOR REFERENCE ONLY — DO NOT REUSE VERBATIM]:\n"
    "{invalid_output}"
)

DEFAULT_TRANSLATION_SCRIPT_REPAIR_PROMPT = (
    "The full translated script was rejected during quality review.\n"
    "\n"
    "Repair the full translation from {source_lang} to {target_lang}.\n"
    "\n"
    "STRICT SCRIPT REPAIR RULES:\n"
    "1. Output ONLY the corrected full translation in {target_lang}\n"
    "2. Keep the same paragraph structure and line breaks as the source script\n"
    "3. Remove all leaked source-language text and all duplicated original text\n"
    "4. Fix awkward literal phrasing so the narration sounds native in {target_lang}\n"
    "5. Fully translate CTA lines and action phrases into natural {target_lang}\n"
    "6. Replace any flagged literal CTA line with an idiomatic local phrase instead of reusing the rejected wording\n"
    "{language_guidance}"
    "{channel_name_instruction}"
    "\n"
    "[WHY THE SCRIPT WAS REJECTED]:\n"
    "{issues}\n"
    "\n"
    "[SOURCE SCRIPT]:\n"
    "{text}\n"
    "\n"
    "[REJECTED TRANSLATION FOR REFERENCE ONLY — DO NOT REUSE VERBATIM]:\n"
    "{invalid_output}"
)

DEFAULT_TRANSLATION_REVIEW_PROMPT = (
    "You are reviewing a translated script only for workflow compatibility and factual integrity.\n"
    "\n"
    "Ignore minor fluency, elegance, style, or preference issues.\n"
    "Do not suggest polish unless the issue is severe enough to block production use.\n"
    "\n"
    "Block only for issues like:\n"
    "- materially wrong meaning or facts\n"
    "- source language leaked through\n"
    "- wrong names, entities, references, or numbers\n"
    "- wrong channel-name handling\n"
    "- duplicated, omitted, or corrupted paragraph content\n"
    "- wording that is likely to break TTS, alignment, or subtitle generation\n"
    "\n"
    "Protected terms may stay untranslated when appropriate: {protected_terms}\n"
    "{language_guidance}"
    "{channel_name_instruction}"
    "\n"
    "Return ONLY strict JSON with this shape:\n"
    '{{"passed": true, "blocking_issues": [], "warnings": [], "summary": "short summary"}}'
    "\n"
    "blocking_issues must contain only real production blockers.\n"
    "warnings may contain optional manual-review notes that do not block production.\n"
    "\n"
    "[SOURCE SCRIPT — {source_lang}]:\n"
    "{source_text}\n"
    "\n"
    "[TRANSLATED SCRIPT — {target_lang}]:\n"
    "{translated_text}"
)


def _guidance_block(target_lang: str, target_channel_name: str = "") -> str:
    lines = prompt_guidance_lines(target_lang, target_channel_name=target_channel_name)
    if not lines:
        return ""
    return "LANGUAGE-SPECIFIC GUIDANCE:\n" + "\n".join(f"- {line}" for line in lines) + "\n"


def build_translation_prompt(
    chunk: str,
    context: str,
    source_lang: str,
    target_lang: str,
    chunk_index: int,
    total_chunks: int,
    channel_name: str = "",
    source_channel_name: str = "",
    target_channel_name: str = "",
    template: str | None = None,
) -> str:
    """Build a translation prompt with context and chunk metadata.

    Mirrors TRADUTOR's buildTranslationPrompt (app.js lines 1046-1065).
    """
    tpl = template or DEFAULT_TRANSLATION_PROMPT
    source_label = display_language_name(source_lang)
    target_label = display_language_name(target_lang)

    if context:
        context_section = (
            "\n[CONTEXT from previous section — do NOT translate this, "
            "use it only for continuity reference]:\n" + context
        )
    else:
        context_section = ""

    language_guidance = _guidance_block(target_lang, target_channel_name=target_channel_name)
    if source_channel_name and target_channel_name:
        channel_name_instruction = (
            f'IMPORTANT: If the script mentions the YouTube channel name '
            f'"{source_channel_name}", always replace it with "{target_channel_name}". '
            f'Do not keep, translate, paraphrase, or partially preserve the source channel name.\n'
        )
    else:
        channel_name_instruction = ""

    if total_chunks > 1:
        chunk_note = (
            f"This is chunk {chunk_index + 1} of {total_chunks}. Maintain consistency with the context provided.\n"
        )
    else:
        chunk_note = ""

    return tpl.format(
        source_lang=source_label,
        target_lang=target_label,
        channel_name=channel_name,
        language_guidance=language_guidance,
        channel_name_instruction=channel_name_instruction,
        chunk_note=chunk_note,
        context_section=context_section,
        text=chunk,
    )


def build_translation_repair_prompt(
    *,
    chunk: str,
    invalid_output: str,
    issues: list[str],
    context: str,
    source_lang: str,
    target_lang: str,
    source_channel_name: str = "",
    target_channel_name: str = "",
    template: str | None = None,
) -> str:
    tpl = template or DEFAULT_TRANSLATION_REPAIR_PROMPT
    source_label = display_language_name(source_lang)
    target_label = display_language_name(target_lang)
    if context:
        context_section = (
            "\n[CONTEXT from previous section — do NOT translate this, "
            "use it only for continuity reference]:\n" + context
        )
    else:
        context_section = ""

    language_guidance = _guidance_block(target_lang, target_channel_name=target_channel_name)
    if source_channel_name and target_channel_name:
        channel_name_instruction = (
            f'IMPORTANT: If the script mentions the YouTube channel name '
            f'"{source_channel_name}", always replace it with "{target_channel_name}". '
            f'Do not keep, translate, paraphrase, or partially preserve the source channel name.\n'
        )
    else:
        channel_name_instruction = ""

    issue_text = "\n".join(f"- {issue}" for issue in issues) if issues else "- Invalid translation output"
    return tpl.format(
        source_lang=source_label,
        target_lang=target_label,
        language_guidance=language_guidance,
        channel_name_instruction=channel_name_instruction,
        context_section=context_section,
        issues=issue_text,
        text=chunk,
        invalid_output=invalid_output,
    )


def build_translation_script_repair_prompt(
    *,
    source_text: str,
    invalid_output: str,
    issues: list[str],
    source_lang: str,
    target_lang: str,
    source_channel_name: str = "",
    target_channel_name: str = "",
    template: str | None = None,
) -> str:
    tpl = template or DEFAULT_TRANSLATION_SCRIPT_REPAIR_PROMPT
    source_label = display_language_name(source_lang)
    target_label = display_language_name(target_lang)
    language_guidance = _guidance_block(target_lang, target_channel_name=target_channel_name)
    if source_channel_name and target_channel_name:
        channel_name_instruction = (
            f'IMPORTANT: If the script mentions the YouTube channel name "{source_channel_name}", '
            f'always replace it with "{target_channel_name}". '
            f'Do not keep, translate, paraphrase, or partially preserve the source channel name.\n'
        )
    else:
        channel_name_instruction = ""
    issue_text = "\n".join(f"- {issue}" for issue in issues) if issues else "- Quality review rejected the script."
    return tpl.format(
        source_lang=source_label,
        target_lang=target_label,
        language_guidance=language_guidance,
        channel_name_instruction=channel_name_instruction,
        issues=issue_text,
        text=source_text,
        invalid_output=invalid_output,
    )


def build_translation_review_prompt(
    *,
    source_text: str,
    translated_text: str,
    source_lang: str,
    target_lang: str,
    target_channel_name: str = "",
    source_channel_name: str = "",
    template: str | None = None,
) -> str:
    tpl = template or DEFAULT_TRANSLATION_REVIEW_PROMPT
    source_label = display_language_name(source_lang)
    target_label = display_language_name(target_lang)
    pack = resolve_language_rulepack(target_lang)
    language_guidance = _guidance_block(target_lang, target_channel_name=target_channel_name)
    if source_channel_name and target_channel_name:
        channel_name_instruction = (
            f'Configured localized channel name: "{target_channel_name}". '
            f'Reject the script if it keeps "{source_channel_name}" instead.\n'
        )
    else:
        channel_name_instruction = ""
    return tpl.format(
        source_lang=source_label,
        target_lang=target_label,
        source_text=source_text,
        translated_text=translated_text,
        language_guidance=language_guidance,
        channel_name_instruction=channel_name_instruction,
        protected_terms=json.dumps(list(pack.protected_terms), ensure_ascii=False),
    )
