from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from .models import SegmentationConfig

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_ROOT = PROJECT_ROOT / "alignment_tool" / "output"
TEMP_ROOT = PROJECT_ROOT / "alignment_tool" / "temp"
LOG_ROOT = PROJECT_ROOT / "alignment_tool" / "logs"
FRONTEND_DIR = PROJECT_ROOT / "alignment_tool" / "ui"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = int(os.environ.get("ALIGNMENT_TOOL_PORT", "8010"))
DEFAULT_SAMPLE_RATE = 16000


@dataclass(frozen=True)
class LanguageProfile:
    code: str
    label: str
    whisperx_code: str
    mfa_dictionary: str
    mfa_acoustic: str
    aliases: tuple[str, ...] = ()


LANGUAGE_PROFILES: list[LanguageProfile] = [
    LanguageProfile("en", "English", "en", "english_us_arpa", "english_us_arpa", ("en-US", "en-GB")),
    LanguageProfile("pt-BR", "Portuguese (Brazil)", "pt", "portuguese_mfa", "portuguese_mfa", ("pt", "pt-br")),
    LanguageProfile("es", "Spanish", "es", "spanish_mfa", "spanish_mfa"),
    LanguageProfile("fr", "French", "fr", "french_mfa", "french_mfa"),
    LanguageProfile("de", "German", "de", "german_mfa", "german_mfa"),
    LanguageProfile("it", "Italian", "it", "italian_cv", "italian_cv"),
    LanguageProfile("ko", "Korean", "ko", "korean_mfa", "korean_mfa"),
]

DEFAULT_SEGMENTATION = SegmentationConfig()


def _language_key(code: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", code.upper()).strip("_")


def resolve_language_profile(language_code: str | None) -> LanguageProfile:
    if not language_code:
        return LANGUAGE_PROFILES[0]
    wanted = language_code.strip().lower()
    for profile in LANGUAGE_PROFILES:
        if wanted == profile.code.lower():
            return profile
        if wanted in {alias.lower() for alias in profile.aliases}:
            return profile
    raise ValueError(f"Unsupported language code '{language_code}'.")


def resolve_mfa_resources(profile: LanguageProfile) -> dict[str, str]:
    env_prefix = f"ALIGNMENT_MFA_{_language_key(profile.code)}"
    dictionary = os.environ.get(f"{env_prefix}_DICTIONARY", profile.mfa_dictionary)
    acoustic = os.environ.get(f"{env_prefix}_ACOUSTIC", profile.mfa_acoustic)
    return {"dictionary": dictionary, "acoustic": acoustic}
