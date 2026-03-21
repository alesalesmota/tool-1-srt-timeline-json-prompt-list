from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import (
    AGENTS_ROOT,
    IMAGE_PROMPT_STAGE,
    PROVIDERS,
    SCENE_STAGE,
    TEMPLATE_STAGES,
    VIDEO_PROMPT_STAGE,
    VISUAL_BIBLE_STAGE,
)
from .database import Tool1Database
from .runtime import ensure_dir, hash_text, write_text

DEFAULT_TEMPLATES: dict[tuple[str, str], str] = {
    (
        SCENE_STAGE,
        "claude",
    ): """You are a scene-planning agent for Tool 1 of a YouTube video workflow.

You receive timed subtitle content from a known script and narration.
Your job is to convert that timed content into contextual scenes.

Rules:
- output JSON only
- use the timing data given
- scene boundaries must follow meaning, not fixed intervals
- 1 contextual block = 1 scene
- do not invent timing not present in the input
- output ordered, non-overlapping scenes only
- prefer scenes around 6 to 16 seconds
- treat 18 seconds as a soft ceiling unless the text strongly resists splitting
- keep first and last overlap-zone scenes conservative

Each scene must include:
- start
- end
- duration
- text
- optional visual_intent
- optional notes
""",
    (
        SCENE_STAGE,
        "codex",
    ): """You are a scene-planning worker for Tool 1.

Return machine-readable JSON only.

Use the supplied timed subtitle chunk to create contextual scenes that can later drive prompt generation and final assembly.

Rules:
- follow meaning, not arbitrary timing windows
- preserve scene order
- use only provided timing
- output ordered, non-overlapping scenes only
- prefer scenes around 6 to 16 seconds
- treat 18 seconds as a soft ceiling unless the text strongly resists splitting
- do not add commentary outside the JSON structure
""",
    (
        VISUAL_BIBLE_STAGE,
        "claude",
    ): """You are the visual-bible agent for Tool 1.

You receive an approved scene timeline for a narrated video.
Your job is to create a compact, reusable visual bible that locks the world style, recurring characters, and continuity rules for later prompt generation.

Rules:
- return JSON only
- write in English
- create self-consistent character cards
- keep character descriptions visually precise and reusable
- keep the world style coherent across both image and video scenes
- avoid vague placeholders like "same as before"
""",
    (
        VISUAL_BIBLE_STAGE,
        "codex",
    ): """You are the visual-bible worker for Tool 1.

Produce a machine-readable visual bible from the provided scene timeline.

Rules:
- output JSON only
- write in English
- define recurring character cards with locked visual descriptions
- define world style, continuity rules, and environment rules
- do not add commentary outside the JSON structure
""",
    (
        VIDEO_PROMPT_STAGE,
        "claude",
    ): """You are the video-prompt agent for Tool 1.

You receive approved video scenes plus a visual bible.
Create one self-sufficient prompt per scene using this exact label order:
SUBJ, SET, ACT, CAM, LOOK, LIGHT, optional RULES.

Rules:
- return JSON only
- write in English
- one prompt per scene
- preserve scene order exactly
- every final prompt must be copy-paste ready on a single line
- each prompt must stand alone without references like "same" or "previous scene"
- do not include scene_id or asset_type inside the final prompt text
""",
    (
        VIDEO_PROMPT_STAGE,
        "codex",
    ): """You are the video-prompt worker for Tool 1.

Produce one compact, structured video-generation prompt per approved video scene.

Rules:
- output JSON only
- write in English
- preserve scene order exactly
- the final prompt must use labels in this order: SUBJ, SET, ACT, CAM, LOOK, LIGHT, optional RULES
- each line must be self-sufficient and must not rely on previous prompts
- do not include scene_id or asset_type inside the final prompt text
""",
    (
        IMAGE_PROMPT_STAGE,
        "claude",
    ): """You are the image-prompt agent for Tool 1.

You receive approved image scenes plus a visual bible.
Create one self-sufficient prompt per scene using this exact label order:
SUBJ, SET, COMP, LOOK, LIGHT, optional RULES.

Rules:
- return JSON only
- write in English
- one prompt per scene
- preserve scene order exactly
- every final prompt must be copy-paste ready on a single line
- each prompt must stand alone without references like "same" or "previous scene"
- do not include scene_id or asset_type inside the final prompt text
""",
    (
        IMAGE_PROMPT_STAGE,
        "codex",
    ): """You are the image-prompt worker for Tool 1.

Produce one compact, structured image-generation prompt per approved image scene.

Rules:
- output JSON only
- write in English
- preserve scene order exactly
- the final prompt must use labels in this order: SUBJ, SET, COMP, LOOK, LIGHT, optional RULES
- each line must be self-sufficient and must not rely on previous prompts
- do not include scene_id or asset_type inside the final prompt text
""",
}


class TemplateStore:
    def __init__(self, db: Tool1Database) -> None:
        self.db = db

    def _template_path(self, stage: str, provider: str) -> Path:
        return AGENTS_ROOT / stage / f"{provider}.md"

    def ensure_defaults(self) -> None:
        for stage in TEMPLATE_STAGES:
            ensure_dir(AGENTS_ROOT / stage)
        for stage in TEMPLATE_STAGES:
            for provider in PROVIDERS:
                body = DEFAULT_TEMPLATES[(stage, provider)]
                path = self._template_path(stage, provider)
                if not path.exists():
                    write_text(path, body)
                self._sync_record(stage, provider, path.read_text(encoding="utf-8"))

    def _sync_record(self, stage: str, provider: str, body: str) -> dict[str, Any]:
        path = self._template_path(stage, provider)
        template_hash = hash_text(body)
        self.db.upsert_template(stage, provider, str(path), body, template_hash)
        return {
            "stage": stage,
            "provider": provider,
            "path": str(path),
            "body": body,
            "hash": template_hash,
        }

    def list_templates(self) -> list[dict[str, Any]]:
        templates: list[dict[str, Any]] = []
        for stage in TEMPLATE_STAGES:
            for provider in PROVIDERS:
                templates.append(self.get_template(stage, provider))
        return templates

    def get_template(self, stage: str, provider: str) -> dict[str, Any]:
        path = self._template_path(stage, provider)
        body = path.read_text(encoding="utf-8") if path.exists() else DEFAULT_TEMPLATES[(stage, provider)]
        return self._sync_record(stage, provider, body)

    def save_template(self, stage: str, provider: str, body: str) -> dict[str, Any]:
        path = self._template_path(stage, provider)
        write_text(path, body)
        return self._sync_record(stage, provider, body)

    def snapshot_template(self, job_root: Path, stage: str, provider: str) -> dict[str, Any]:
        template = self.get_template(stage, provider)
        snapshot_dir = ensure_dir(job_root / "snapshots" / "templates")
        snapshot_path = snapshot_dir / f"{stage}-{provider}-{template['hash'][:12]}.md"
        if not snapshot_path.exists():
            write_text(snapshot_path, template["body"])
        template["snapshot_path"] = str(snapshot_path)
        return template
