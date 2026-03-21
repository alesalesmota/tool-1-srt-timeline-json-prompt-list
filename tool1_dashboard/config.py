from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = PROJECT_ROOT / "tool1_dashboard"
UI_DIR = PACKAGE_ROOT / "ui"
WORKSPACE_ROOT = PROJECT_ROOT / "workspace"
VIDEOS_ROOT = WORKSPACE_ROOT / "videos"
DATABASE_PATH = WORKSPACE_ROOT / "tool1_dashboard.db"
CONFIG_ROOT = PROJECT_ROOT / "config"
AGENTS_ROOT = CONFIG_ROOT / "agents"
DOCS_ROOT = PROJECT_ROOT / "docs"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = int(os.environ.get("TOOL1_DASHBOARD_PORT", "8020"))

BOARD_STATUSES = (
    "Draft",
    "Queued",
    "Running",
    "Review",
    "Done",
    "Needs Attention",
)

PIPELINE_STAGES = (
    "draft",
    "alignment",
    "planning_prep",
    "scene_planning",
    "visual_bible",
    "video_prompt_generation",
    "image_prompt_generation",
    "review",
    "export",
)

RUNNABLE_STAGES = (
    "alignment",
    "planning_prep",
    "scene_planning",
    "visual_bible",
    "video_prompt_generation",
    "image_prompt_generation",
)

PROVIDERS = ("claude", "codex")
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
    "leading_video_scene_count": 20,
    "planning_chunk_seconds": 360,
    "planning_overlap_seconds": 30,
    "prompt_batch_size": 24,
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
