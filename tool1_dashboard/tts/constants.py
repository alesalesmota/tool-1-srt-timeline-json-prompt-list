"""TTS constants: language maps, job priorities, worker configuration."""

from __future__ import annotations

# XTTS-v2 supported languages mapped from langdetect codes
XTTS_LANG_MAP: dict[str, str] = {
    "pt": "pt",
    "en": "en",
    "es": "es",
    "fr": "fr",
    "de": "de",
    "it": "it",
    "pl": "pl",
    "tr": "tr",
    "ru": "ru",
    "nl": "nl",
    "cs": "cs",
    "ar": "ar",
    "zh": "zh-cn",
    "ja": "ja",
    "ko": "ko",
    "hu": "hu",
    "hi": "hi",
}

LANG_NAMES: dict[str, str] = {
    "pt": "Portugues",
    "en": "English",
    "es": "Espanol",
    "fr": "Francais",
    "de": "Deutsch",
    "it": "Italiano",
    "pl": "Polski",
    "tr": "Turkce",
    "ru": "Russkiy",
    "nl": "Nederlands",
    "cs": "Cestina",
    "ar": "Arabic",
    "zh-cn": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "hu": "Magyar",
    "hi": "Hindi",
}

# Filename-based language hints (e.g. [FR].docx, [EN].txt)
FILENAME_LANG_MAP: dict[str, str] = {
    "pt": "pt",
    "br": "pt",
    "en": "en",
    "us": "en",
    "uk": "en",
    "es": "es",
    "fr": "fr",
    "de": "de",
    "al": "de",
    "it": "it",
    "pl": "pl",
    "tr": "tr",
    "ru": "ru",
    "nl": "nl",
    "cs": "cs",
    "ar": "ar",
    "zh": "zh-cn",
    "cn": "zh-cn",
    "ja": "ja",
    "jp": "ja",
    "ko": "ko",
    "kr": "ko",
    "hu": "hu",
    "hi": "hi",
}

SUPPORTED_TTS_LANGUAGES: set[str] = set(XTTS_LANG_MAP.values())

# Job queue priorities (lower = higher priority)
LATENT_PRIORITY = 5
TEST_VOICE_PRIORITY = 0
GENERATE_PRIORITY = 20

# Worker configuration
HEARTBEAT_INTERVAL = 5  # seconds between heartbeats
HEARTBEAT_STALE_TIMEOUT = 120  # seconds before a worker is considered stale
WORKER_POLL_INTERVAL = 0.5  # seconds between queue polls
WORKER_IDLE_RECHECK_SECONDS = 2  # seconds between manager-side idle checks
INTERACTIVE_IDLE_SHUTDOWN_SECONDS = 20  # stop shortly after profile/test usage ends
PIPELINE_IDLE_SHUTDOWN_SECONDS = 60  # stay warm until the TTS pipeline fully drains
CHUNK_RETRY_ATTEMPTS = 3
STALE_PROCESSING_SECONDS = 90  # requeue jobs stuck longer than this

# Audio constants
AUDIO_SAMPLE_RATE = 24000
MAX_REFERENCE_AUDIO_SECONDS = 45
SILENCE_GAP_SECONDS = 0.15
FAILED_CHUNK_SECONDS = 0.5
WAV_MERGE_READ_FRAMES = 64_000
CHUNK_MAX_CHARS = 200

# Upload limits
MAX_PROFILE_AUDIO_BYTES = 25 * 1024 * 1024  # 25 MB

# Output retention
MAX_OUTPUT_FILES = 300
MAX_OUTPUT_TOTAL_BYTES = 1500 * 1024 * 1024  # 1.5 GB
OUTPUT_RETENTION_HOURS = 72
MAX_JOB_HISTORY = 250

# Test voice timeout
TEST_VOICE_TIMEOUT_SECONDS = 180
TEST_VOICE_WAIT_STEP_SECONDS = 0.4


def map_language_code(code: str) -> str:
    """Map a Creator Studio language code (e.g. 'pt-BR') to an XTTS-v2 code ('pt').

    Falls back to 'en' for unknown codes.
    """
    base = code.split("-")[0].lower()
    return XTTS_LANG_MAP.get(base, "en")
