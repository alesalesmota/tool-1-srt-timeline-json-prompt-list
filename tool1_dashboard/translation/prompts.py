"""Translation prompt templates — ported from TRADUTOR app.js."""

from __future__ import annotations

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

    if context:
        context_section = (
            "\n[CONTEXT from previous section — do NOT translate this, "
            "use it only for continuity reference]:\n" + context
        )
    else:
        context_section = ""

    rule_offset = 0
    if source_channel_name and target_channel_name:
        channel_name_instruction = (
            f'11. IMPORTANT: Whenever the text mentions the channel name '
            f'"{source_channel_name}", replace it with "{target_channel_name}" '
            f'in your translation\n'
        )
        rule_offset = 1
    else:
        channel_name_instruction = ""

    if total_chunks > 1:
        chunk_note = (
            f"{11 + rule_offset}. This is chunk {chunk_index + 1} of {total_chunks} "
            "— maintain consistency with the context provided\n"
        )
    else:
        chunk_note = ""

    return tpl.format(
        source_lang=source_lang,
        target_lang=target_lang,
        channel_name=channel_name,
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
    if context:
        context_section = (
            "\n[CONTEXT from previous section — do NOT translate this, "
            "use it only for continuity reference]:\n" + context
        )
    else:
        context_section = ""

    if source_channel_name and target_channel_name:
        channel_name_instruction = (
            f'6. IMPORTANT: Whenever the text mentions the channel name '
            f'"{source_channel_name}", replace it with "{target_channel_name}" '
            f'in your translation\n'
        )
    else:
        channel_name_instruction = ""

    issue_text = "\n".join(f"- {issue}" for issue in issues) if issues else "- Invalid translation output"
    return tpl.format(
        source_lang=source_lang,
        target_lang=target_lang,
        channel_name_instruction=channel_name_instruction,
        context_section=context_section,
        issues=issue_text,
        text=chunk,
        invalid_output=invalid_output,
    )
