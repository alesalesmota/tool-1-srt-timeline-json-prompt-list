"""Per-language translation guidance and spoken-form normalization."""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class LanguageRulepack:
    code: str
    language_name: str
    cta_template: str
    cta_notes: tuple[str, ...]
    localized_channel_behavior: str
    bad_literal_patterns: tuple[tuple[str, str], ...]
    bad_literal_examples: tuple[str, ...]
    protected_terms: tuple[str, ...]
    chapter_word: str
    verse_word: str
    verses_word: str
    range_word: str
    ordinal_books: dict[int, str]
    reference_example: str
    spoken_replacements: tuple[tuple[str, str], ...] = ()
    alignment_join_aliases: tuple[tuple[str, tuple[str, ...]], ...] = ()
    elision_prefixes: tuple[str, ...] = ()
    subtitle_non_breaking_abbreviations: tuple[str, ...] = ()
    subtitle_no_leading_tokens: tuple[str, ...] = ()
    subtitle_no_trailing_tokens: tuple[str, ...] = ()
    subtitle_preferred_break_tokens: tuple[str, ...] = ()


_GENERIC_PROTECTED_TERMS = ("Amen", "Hallelujah", "Yahweh", "Messiah")

_RULEPACKS: dict[str, LanguageRulepack] = {
    "en": LanguageRulepack(
        code="en",
        language_name="English",
        cta_template="Subscribe to {channel}",
        cta_notes=(
            'For bell-icon CTAs, prefer idiomatic phrasing like "Click the bell" or "Tap the bell", not a pronoun-only command.',
        ),
        localized_channel_behavior="Preserve the configured localized channel name exactly.",
        bad_literal_patterns=(),
        bad_literal_examples=(),
        protected_terms=_GENERIC_PROTECTED_TERMS,
        chapter_word="chapter",
        verse_word="verse",
        verses_word="verses",
        range_word="through",
        ordinal_books={1: "First", 2: "Second", 3: "Third"},
        reference_example="John chapter 18 verse 2",
        spoken_replacements=(
            (r"\bSt\.", "Saint"),
            (r"\bMt\.", "Mount"),
        ),
        subtitle_non_breaking_abbreviations=("mr.", "mrs.", "ms.", "dr.", "st.", "jr.", "sr.", "vs."),
        subtitle_no_leading_tokens=("and", "or", "but", "to", "of", "the", "a", "an", "for", "with", "in", "on", "at"),
        subtitle_no_trailing_tokens=("a", "an", "the", "and", "or", "but", "to", "of", "for", "with", "in", "on", "at"),
        subtitle_preferred_break_tokens=("chapter", "verse", "verses", "through"),
    ),
    "de": LanguageRulepack(
        code="de",
        language_name="German",
        cta_template="Abonniere {channel}",
        cta_notes=(
            'For bell-icon CTAs, prefer idiomatic phrasing like "Klicke auf die Glocke" instead of literal pronoun-only commands.',
        ),
        localized_channel_behavior="Preserve the configured German channel name exactly and do not retranslate it.",
        bad_literal_patterns=(
            (r"\bKlicke es\b", "Output contains awkward literal German CTA phrasing."),
            (r"\bSubscribe to\b", "Output still contains English CTA wording."),
        ),
        bad_literal_examples=("Klicke es",),
        protected_terms=_GENERIC_PROTECTED_TERMS,
        chapter_word="Kapitel",
        verse_word="Vers",
        verses_word="Verse",
        range_word="bis",
        ordinal_books={1: "Erster", 2: "Zweiter", 3: "Dritter"},
        reference_example="Johannes Kapitel 18 Vers 2",
        spoken_replacements=(
            (r"\bHl\.", "Heilig"),
        ),
        alignment_join_aliases=(
            ("zum", ("zu", "dem")),
            ("zur", ("zu", "der")),
            ("im", ("in", "dem")),
            ("am", ("an", "dem")),
        ),
        subtitle_non_breaking_abbreviations=("z.b.", "bzw.", "usw.", "dr.", "hl."),
        subtitle_no_leading_tokens=(
            "und", "oder", "aber", "der", "die", "das", "dem", "den", "des",
            "ein", "eine", "einer", "einem", "einen", "zu", "zur", "zum",
            "im", "am", "von", "vom", "mit", "auf", "an", "in",
        ),
        subtitle_no_trailing_tokens=(
            "der", "die", "das", "dem", "den", "des", "ein", "eine", "einer",
            "einem", "einen", "zu", "zur", "zum", "im", "am", "von", "vom",
            "mit", "auf", "an", "in",
        ),
        subtitle_preferred_break_tokens=("kapitel", "vers", "verse", "bis"),
    ),
    "es": LanguageRulepack(
        code="es",
        language_name="Spanish",
        cta_template="Suscríbete a {channel}",
        cta_notes=(
            'For bell-icon CTAs, prefer idiomatic phrasing like "Haz clic en la campana" or "Pulsa la campana".',
        ),
        localized_channel_behavior="Preserve the configured Spanish channel name exactly and do not translate it again.",
        bad_literal_patterns=(
            (r"\bSubscribe to\b", "Output still contains English CTA wording."),
            (r"\bClick here\b", "Output still contains English CTA wording."),
        ),
        bad_literal_examples=(),
        protected_terms=_GENERIC_PROTECTED_TERMS,
        chapter_word="capítulo",
        verse_word="versículo",
        verses_word="versículos",
        range_word="al",
        ordinal_books={1: "Primera", 2: "Segunda", 3: "Tercera"},
        reference_example="Juan capítulo 18 versículo 2",
        spoken_replacements=(
            (r"\bSta\.", "Santa"),
            (r"\bSto\.", "Santo"),
        ),
        alignment_join_aliases=(
            ("del", ("de", "el")),
            ("al", ("a", "el")),
        ),
        subtitle_non_breaking_abbreviations=("sr.", "sra.", "dr.", "dra.", "sta.", "sto."),
        subtitle_no_leading_tokens=(
            "el", "la", "los", "las", "un", "una", "unos", "unas", "de",
            "del", "al", "a", "y", "o", "que", "en", "con", "por", "para",
        ),
        subtitle_no_trailing_tokens=(
            "el", "la", "los", "las", "un", "una", "unos", "unas", "de",
            "del", "al", "a", "y", "o", "que", "en", "con", "por", "para",
        ),
        subtitle_preferred_break_tokens=("capítulo", "versículo", "versículos", "al"),
    ),
    "fr": LanguageRulepack(
        code="fr",
        language_name="French",
        cta_template="Abonne-toi à {channel}",
        cta_notes=(
            'For bell-icon CTAs, prefer idiomatic phrasing like "Clique dessus" or "Active la cloche", never "Cliquez-la".',
        ),
        localized_channel_behavior="Preserve the configured French channel name exactly and keep the CTA idiomatic.",
        bad_literal_patterns=(
            (r"\bCliquez-la\b", "Output contains awkward literal French CTA phrasing."),
            (r"\bse leva de la soupe\b", "Output contains a literal French calque that sounds unnatural."),
            (r"\bextraordinaire détermination\b", "Output contains awkward literal French phrasing."),
            (r"\bSubscribe to\b", "Output still contains English CTA wording."),
        ),
        bad_literal_examples=("Cliquez-la", "se leva de la soupe"),
        protected_terms=_GENERIC_PROTECTED_TERMS,
        chapter_word="chapitre",
        verse_word="verset",
        verses_word="versets",
        range_word="à",
        ordinal_books={1: "Premier", 2: "Deuxième", 3: "Troisième"},
        reference_example="Jean chapitre 18 verset 2",
        spoken_replacements=(
            (r"\bSt\.", "Saint"),
            (r"\bSte\.", "Sainte"),
        ),
        elision_prefixes=("c", "d", "j", "l", "m", "n", "qu", "s", "t"),
        subtitle_non_breaking_abbreviations=("m.", "mme.", "dr.", "st.", "ste."),
        subtitle_no_leading_tokens=(
            "de", "du", "des", "à", "au", "aux", "et", "ou", "que", "qui",
            "le", "la", "les", "un", "une", "en", "dans", "sur", "pour",
            "ce", "cet", "cette", "ces", "se", "l'", "d'", "qu'", "s'",
            "c'", "j'", "n'", "t'",
        ),
        subtitle_no_trailing_tokens=(
            "de", "du", "des", "à", "au", "aux", "et", "ou", "que", "qui",
            "le", "la", "les", "un", "une", "en", "dans", "sur", "pour",
            "ce", "cet", "cette", "ces",
        ),
        subtitle_preferred_break_tokens=("chapitre", "verset", "versets", "à"),
    ),
    "it": LanguageRulepack(
        code="it",
        language_name="Italian",
        cta_template="Iscriviti a {channel}",
        cta_notes=(
            'For bell-icon CTAs, prefer idiomatic phrasing like "Clicca sulla campanella" instead of literal pronoun-only commands.',
        ),
        localized_channel_behavior="Preserve the configured Italian channel name exactly and do not retranslate it.",
        bad_literal_patterns=(
            (r"\bClicca-la\b", "Output contains awkward literal Italian CTA phrasing."),
            (r"\bSubscribe to\b", "Output still contains English CTA wording."),
        ),
        bad_literal_examples=("Clicca-la",),
        protected_terms=_GENERIC_PROTECTED_TERMS,
        chapter_word="capitolo",
        verse_word="versetto",
        verses_word="versetti",
        range_word="a",
        ordinal_books={1: "Primo", 2: "Secondo", 3: "Terzo"},
        reference_example="Giovanni capitolo 18 versetto 2",
        spoken_replacements=(
            (r"\bS\.", "San"),
            (r"\bS\.ta", "Santa"),
        ),
        alignment_join_aliases=(
            ("dell", ("di", "il")),
            ("all", ("a", "il")),
            ("nell", ("in", "il")),
            ("sull", ("su", "il")),
        ),
        elision_prefixes=("l", "d", "all", "dall", "dell", "nell", "sull", "un"),
        subtitle_non_breaking_abbreviations=("sig.", "sig.ra", "dott.", "s.", "s.ta"),
        subtitle_no_leading_tokens=(
            "e", "o", "ma", "che", "di", "del", "della", "dell'", "a", "al",
            "alla", "all'", "da", "dal", "dalla", "dall'", "in", "nel",
            "nell'", "con", "per", "il", "lo", "la", "i", "gli", "le",
            "un", "una", "uno", "si",
        ),
        subtitle_no_trailing_tokens=(
            "e", "o", "ma", "che", "di", "del", "della", "a", "al", "alla",
            "da", "dal", "dalla", "in", "nel", "con", "per", "il", "lo",
            "la", "i", "gli", "le", "un", "una", "uno",
        ),
        subtitle_preferred_break_tokens=("capitolo", "versetto", "versetti", "a"),
    ),
}

_LANGUAGE_ALIASES = {
    "de": "de",
    "de-de": "de",
    "german": "de",
    "deutsch": "de",
    "en": "en",
    "en-us": "en",
    "en-gb": "en",
    "english": "en",
    "es": "es",
    "es-es": "es",
    "es-mx": "es",
    "spanish": "es",
    "espanol": "es",
    "español": "es",
    "fr": "fr",
    "fr-fr": "fr",
    "french": "fr",
    "francais": "fr",
    "français": "fr",
    "it": "it",
    "it-it": "it",
    "italian": "it",
    "italiano": "it",
}

_REFERENCE_PATTERN = re.compile(
    r"\b((?:[1-3]\s+)?[A-ZÀ-ÖØ-Ý][A-Za-zÀ-ÖØ-öø-ÿ'’.-]*(?:\s+[A-ZÀ-ÖØ-Ý][A-Za-zÀ-ÖØ-öø-ÿ'’.-]*){0,3})\s+"
    r"(\d{1,3}):(\d{1,3})(?:\s*[-–]\s*(\d{1,3}))?"
)


def normalize_language_code(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return "en"
    normalized = raw.lower()
    if normalized in _LANGUAGE_ALIASES:
        return _LANGUAGE_ALIASES[normalized]
    primary = normalized.split("-", 1)[0]
    return _LANGUAGE_ALIASES.get(primary, primary or "en")


def resolve_language_rulepack(value: str) -> LanguageRulepack:
    return _RULEPACKS.get(normalize_language_code(value), _RULEPACKS["en"])


def display_language_name(value: str) -> str:
    return resolve_language_rulepack(value).language_name


def prompt_guidance_lines(language_code: str, *, target_channel_name: str = "") -> list[str]:
    pack = resolve_language_rulepack(language_code)
    channel_label = target_channel_name or "the configured localized channel name"
    lines = [
        f"Write like a native {pack.language_name} narrator and avoid literal calques or robotic phrasing.",
        f'Render Bible references and citations naturally in {pack.language_name}; spoken example: "{pack.reference_example}".',
        f'Use natural CTA wording in {pack.language_name}; preferred style: "{pack.cta_template.format(channel=channel_label)}".',
        pack.localized_channel_behavior,
    ]
    lines.extend(pack.cta_notes)
    if pack.protected_terms:
        lines.append("These terms may stay as-is when already correct: " + ", ".join(pack.protected_terms) + ".")
    if pack.bad_literal_examples:
        lines.append("Avoid awkward literal phrasing such as: " + ", ".join(pack.bad_literal_examples) + ".")
    return lines


def _normalize_reference_book_name(book_name: str, pack: LanguageRulepack) -> str:
    match = re.match(r"^\s*([1-3])\s+(.+?)\s*$", book_name)
    if not match:
        return book_name.strip()
    prefix = int(match.group(1))
    rest = match.group(2).strip()
    ordinal = pack.ordinal_books.get(prefix)
    if not ordinal:
        return book_name.strip()
    return f"{ordinal} {rest}"


def normalize_alignment_token(value: str) -> str:
    cleaned = str(value or "").strip().lower()
    cleaned = re.sub(r"[^\w]+", "", cleaned, flags=re.UNICODE)
    return cleaned


def component_aliases_for_token(value: str, language_code: str) -> list[tuple[str, ...]]:
    token = normalize_alignment_token(value)
    if not token:
        return []
    pack = resolve_language_rulepack(language_code)
    aliases: list[tuple[str, ...]] = []
    for joined, components in pack.alignment_join_aliases:
        if token == joined:
            aliases.append(tuple(components))
    for prefix in sorted(pack.elision_prefixes, key=len, reverse=True):
        if token.startswith(prefix) and len(token) > len(prefix) + 1:
            remainder = token[len(prefix):]
            if remainder:
                aliases.append((prefix, remainder))
    return aliases


def joined_alias_for_components(values: tuple[str, ...], language_code: str) -> str | None:
    normalized_values = tuple(normalize_alignment_token(value) for value in values if normalize_alignment_token(value))
    if not normalized_values:
        return None
    pack = resolve_language_rulepack(language_code)
    for joined, components in pack.alignment_join_aliases:
        if normalized_values == tuple(components):
            return joined
    joined = "".join(normalized_values)
    for prefix in sorted(pack.elision_prefixes, key=len, reverse=True):
        if len(normalized_values) == 2 and normalized_values[0] == prefix and joined.startswith(prefix):
            return joined
    return None


def build_spoken_script(text: str, language_code: str) -> str:
    pack = resolve_language_rulepack(language_code)
    source_text = str(text or "")
    if not source_text.strip():
        return ""
    for pattern, replacement in pack.spoken_replacements:
        source_text = re.sub(pattern, replacement, source_text)

    def replace_reference(match: re.Match[str]) -> str:
        book = _normalize_reference_book_name(match.group(1), pack)
        chapter = match.group(2)
        verse_start = match.group(3)
        verse_end = match.group(4)
        if verse_end:
            return f"{book} {pack.chapter_word} {chapter} {pack.verses_word} {verse_start} {pack.range_word} {verse_end}"
        return f"{book} {pack.chapter_word} {chapter} {pack.verse_word} {verse_start}"

    return _REFERENCE_PATTERN.sub(replace_reference, source_text)
