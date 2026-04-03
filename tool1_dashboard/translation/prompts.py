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
    "{channel_name_instruction}"
    "{chunk_note}"
    "{context_section}\n"
    "\n"
    "[TEXT TO TRANSLATE]:\n"
    "{text}"
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
            f'8. IMPORTANT: Whenever the text mentions the channel name '
            f'"{source_channel_name}", replace it with "{target_channel_name}" '
            f'in your translation\n'
        )
        rule_offset = 1
    else:
        channel_name_instruction = ""

    if total_chunks > 1:
        chunk_note = (
            f"{8 + rule_offset}. This is chunk {chunk_index + 1} of {total_chunks} "
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
