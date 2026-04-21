from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = PROJECT_ROOT / "tool1_dashboard"
UI_DIR = PACKAGE_ROOT / "ui"
WORKSPACE_ROOT = PROJECT_ROOT / "workspace"
EPISODES_ROOT = WORKSPACE_ROOT / "episodes"
DATABASE_PATH = WORKSPACE_ROOT / "creator_studio.db"
CONFIG_ROOT = PROJECT_ROOT / "config"
AGENTS_ROOT = CONFIG_ROOT / "agents"
DOCS_ROOT = PROJECT_ROOT / "docs"

# TTS paths
TTS_ROOT = WORKSPACE_ROOT / "tts"
TTS_PROFILES_DIR = TTS_ROOT / "profiles"
TTS_OUTPUT_DIR = TTS_ROOT / "output"
TTS_CHUNKS_DIR = TTS_ROOT / "chunks"

# Translation config path
TRANSLATION_CONFIG_ROOT = CONFIG_ROOT / "translation"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = int(os.environ.get("TOOL1_DASHBOARD_PORT", "8020"))

BOARD_STATUSES = (
    "Draft",
    "Queued",
    "Paused",
    "Running",
    "Review",
    "Done",
)

# -- Episode pipeline (TTS-first unified pipeline) --
EPISODE_PIPELINE_STAGES = (
    "draft",
    "consistency_guide",
    "translation",
    "tts",
    "alignment",
    "chunking",
    "scene_planning",
    "video_prompt_generation",
    "image_prompt_generation",
    "timeline_mapping",
    "review",
    "export",
    "asset_upload",
    "assembly_validation",
    "video_render",
    "final_review",
)

EPISODE_RUNNABLE_STAGES = (
    "consistency_guide",
    "translation",
    "tts",
    "alignment",
    "chunking",
    "scene_planning",
    "video_prompt_generation",
    "image_prompt_generation",
    "timeline_mapping",
)

VIDEO_ASSEMBLY_STAGES = ("asset_upload", "assembly_validation", "video_render")

# Per-language stages (these loop over each configured language sequentially)
EPISODE_PER_LANGUAGE_STAGES = ("translation", "tts", "alignment", "timeline_mapping")

# -- Pipeline statuses --
PIPELINE_STATUSES = (
    "idle",
    "queued",
    "paused",
    "running",
    "review",
    "done",
    "failed",
    "paused_for_tts",
)

# -- Translation providers --
TRANSLATION_PROVIDERS = ("gemini", "openai", "anthropic")

# -- Supported target languages --
TARGET_LANGUAGES = (
    {"code": "pt-BR", "label": "Portuguese (Brazil)"},
    {"code": "en", "label": "English"},
    {"code": "es", "label": "Spanish"},
    {"code": "fr", "label": "French"},
    {"code": "de", "label": "German"},
    {"code": "it", "label": "Italian"},
    {"code": "ja", "label": "Japanese"},
    {"code": "zh", "label": "Chinese"},
    {"code": "ru", "label": "Russian"},
    {"code": "ar", "label": "Arabic"},
    {"code": "ko", "label": "Korean"},
    {"code": "nl", "label": "Dutch"},
    {"code": "pl", "label": "Polish"},
    {"code": "tr", "label": "Turkish"},
    {"code": "hi", "label": "Hindi"},
    {"code": "hu", "label": "Hungarian"},
    {"code": "cs", "label": "Czech"},
)

PROVIDERS = ("claude", "codex", "openai")
MODEL_CATALOG = {
    "claude": (
        {"value": "haiku", "label": "Haiku"},
        {"value": "sonnet", "label": "Sonnet"},
        {"value": "opus", "label": "Opus"},
    ),
    "codex": (
        {"value": "gpt-5.4", "label": "GPT-5.4"},
        {"value": "gpt-5.4-mini", "label": "GPT-5.4 Mini"},
        {"value": "gpt-5.2-codex", "label": "GPT-5.2 Codex"},
        {"value": "gpt-5.1-codex-mini", "label": "GPT-5.1 Codex Mini"},
    ),
    "openai": (
        {"value": "gpt-5.4-mini", "label": "GPT-5.4 Mini"},
        {"value": "gpt-5.4", "label": "GPT-5.4"},
        {"value": "gpt-4o-mini", "label": "GPT-4o Mini"},
        {"value": "gpt-4.1-mini", "label": "GPT-4.1 Mini"},
    ),
}
SCENE_STAGE = "scene_planning"
VISUAL_BIBLE_STAGE = "visual_bible"
VIDEO_PROMPT_STAGE = "video_prompt_generation"
IMAGE_PROMPT_STAGE = "image_prompt_generation"
TEMPLATE_STAGES = (
    SCENE_STAGE,
    VISUAL_BIBLE_STAGE,
    VIDEO_PROMPT_STAGE,
    IMAGE_PROMPT_STAGE,
)

DEFAULT_SETTINGS = {
    "default_scene_planning_provider": "claude",
    "default_visual_bible_provider": "claude",
    "default_video_prompt_provider": "codex",
    "default_image_prompt_provider": "codex",
    "default_scene_planning_model": "haiku",
    "default_visual_bible_model": "haiku",
    "default_video_prompt_model": "gpt-5.4",
    "default_image_prompt_model": "gpt-5.4",
    "leading_video_scene_count": 20,
    "planning_chunk_seconds": 360,
    "planning_overlap_seconds": 30,
    "prompt_batch_size": 8,
    # Translation defaults
    "translation_chunk_max_words": 800,
    "translation_context_tail_words": 200,
    "translation_reviewer_provider": "openai",
    "translation_reviewer_model": "gpt-4.1-mini",
    "translation_ai_review_enabled": False,
    # TTS defaults
    "tts_chunk_max_chars": 200,
    "tts_audio_sample_rate": 24000,
    "tts_silence_gap_seconds": 0.15,
    "tts_auto_start_worker": False,
}

DEFAULT_ALIGNMENT_OPTIONS = {
    "primary_engine": "mfa",
    "fallback_engine": "whisperx",
    "whisperx_model": "small",
    "min_duration": 0.9,
    "preferred_duration": 3.0,
    "max_duration": 6.0,
    "max_chars_per_line": 42,
    "max_lines_per_block": 2,
    "max_chars_per_block": 84,
    "max_reading_cps": 18.0,
}

MAX_PREVIEW_CHARS = 40_000
