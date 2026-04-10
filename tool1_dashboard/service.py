from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import httpx
import json
import logging
import os
import re
import shutil
import threading
import uuid
from pathlib import Path
from typing import Any

from .alignment_tool.extract_script import extract_script_text
from .alignment_tool.normalize_script import normalize_script
from .alignment_tool.config import LANGUAGE_PROFILES
from .alignment_tool.config import OUTPUT_ROOT as ALIGNMENT_OUTPUT_ROOT
from .alignment_tool.config import TEMP_ROOT as ALIGNMENT_TEMP_ROOT
from .alignment_tool.mfa_resources import mfa_resource_status, prepare_mfa_language_resources_async
from .alignment_tool.orchestrator import run_alignment_job
from .alignment_tool.runtime import probe_health as alignment_health
from .chunking import build_gap_fill_batches, build_planning_chunks, build_prompt_batches
from .config import (
    AGENTS_ROOT,
    BOARD_STATUSES,
    DEFAULT_ALIGNMENT_OPTIONS,
    DEFAULT_SETTINGS,
    EPISODE_PIPELINE_STAGES,
    EPISODE_RUNNABLE_STAGES,
    EPISODE_PER_LANGUAGE_STAGES,
    EPISODES_ROOT,
    IMAGE_PROMPT_STAGE,
    MAX_PREVIEW_CHARS,
    MODEL_CATALOG,
    PROVIDERS,
    SCENE_STAGE,
    TARGET_LANGUAGES,
    VIDEO_PROMPT_STAGE,
    VISUAL_BIBLE_STAGE,
)
from .database import Tool1Database
from .launch_runtime import get_runtime_info, runtime_url_from_info
from .providers import CliRunner
from .runtime import (
    clamp_preview,
    ensure_dir,
    make_job_id,
    read_json,
    read_jsonl,
    read_text,
    safe_filename,
    utc_now,
    write_json,
    write_jsonl,
    write_text,
)
from .srt_chunker.srt_io import parse_srt_text
from .templates import TemplateStore
from .translation.language_rules import build_spoken_script
from .translation.quality import (
    apply_channel_cta_fallback,
    collect_translation_quality_issues,
)
from .translation_profiles import (
    is_runnable_translation_profile_provider,
    mask_secret,
    normalize_openai_model,
    recommended_openai_model,
    sanitize_translation_profile,
    sort_openai_models,
)
from .tts.constants import STALE_PROCESSING_SECONDS
from .tts.voice_config import (
    DEFAULT_VOICE_TTS_PRESET,
    chunk_text_for_voice_tts,
    normalize_voice_tts_config,
    serialize_voice_tts_config,
    voice_tts_limits_payload,
    voice_tts_presets_payload,
)
from .validators import (
    SceneMergeConfig,
    apply_default_asset_types,
    image_prompt_output_schema,
    merge_scene_chunks,
    normalize_prompt_payloads,
    normalize_scene_payload,
    normalize_visual_bible,
    scene_output_schema,
    validate_prompt_payloads,
    validate_timeline,
    video_prompt_output_schema,
    visual_bible_output_schema,
)


log = logging.getLogger(__name__)

QUEUE_PROVIDER_TARGETS = (
    ("consistency_guide", "Consistency Guide", "visual_bible_provider", "visual_bible_model"),
    ("scene_planning", "Scene Planning", "scene_planning_provider", "scene_planning_model"),
    ("video_prompt_generation", "Video Prompt Generation", "video_prompt_provider", "video_prompt_model"),
    ("image_prompt_generation", "Image Prompt Generation", "image_prompt_provider", "image_prompt_model"),
)
PROVIDER_STRUCTURED_STAGES = {stage for stage, *_ in QUEUE_PROVIDER_TARGETS}
QUEUE_STAGE_PROVIDER_BLOCKER_CODES = {
    "missing_provider",
    "provider_unavailable",
    "provider_api_key_required",
    "provider_login_required",
}
STAGE_PROVIDER_OPENAI_API_KEY_SETTING = "stage_provider_openai_api_key"
STAGE_PROVIDER_OPENAI_MODELS_SETTING = "stage_provider_openai_models_json"
STAGE_PROVIDER_OPENAI_RECOMMENDED_MODEL_SETTING = "stage_provider_openai_recommended_model"
STAGE_PROVIDER_OPENAI_SYNCED_AT_SETTING = "stage_provider_openai_models_synced_at"


class QueueBlockedError(ValueError):
    def __init__(
        self,
        *,
        episode_id: str,
        start_stage: str,
        queue_readiness: dict[str, Any],
    ) -> None:
        self.episode_id = episode_id
        self.start_stage = start_stage
        self.queue_readiness = queue_readiness
        super().__init__(self._build_message())

    def _build_message(self) -> str:
        blockers = self.queue_readiness.get("blockers") or []
        if not blockers:
            return "Episode is not ready to queue."
        first = blockers[0].get("message") or "Episode is not ready to queue."
        remaining = len(blockers) - 1
        if remaining > 0:
            return f"{first} (+{remaining} more blocker{'s' if remaining != 1 else ''})"
        return first

    def to_detail(self) -> dict[str, Any]:
        return {
            "code": "queue_blocked",
            "message": str(self),
            "episode_id": self.episode_id,
            "start_stage": self.start_stage,
            "queue_readiness": self.queue_readiness,
        }


class Tool1Service:
    def __init__(self, db: Tool1Database | None = None, cli_runner: CliRunner | None = None) -> None:
        self.db = db or Tool1Database()
        self.cli_runner = cli_runner or CliRunner()
        self.templates = TemplateStore(self.db)
        self._condition = threading.Condition()
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None
        self._provider_stage_stale_seconds = float(
            os.environ.get("TOOL1_PROVIDER_STAGE_STALE_SECONDS", "900")
        )
        self.db.initialize()
        self.templates.ensure_defaults()
        ensure_dir(EPISODES_ROOT)
        from .tts.manager import TTSManager
        self.tts_manager = TTSManager(self.db)

    def start_worker(self) -> None:
        if self._worker_thread and self._worker_thread.is_alive():
            return
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    def stop_worker(self) -> None:
        self._stop_event.set()
        with self._condition:
            self._condition.notify_all()
        if self._worker_thread and self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2)

    def _quiesce_stale_episodes(self) -> None:
        """On startup, transition lingering paused_for_tts episodes to paused
        so that nothing auto-resumes without explicit user action."""
        stale = self.db.list_paused_tts_episodes()
        for ep in stale:
            self.db.update_episode(
                ep["id"],
                board_status="Paused",
                pipeline_status="paused",
                current_stage="tts",
                queued_from_stage="tts",
                pause_requested=0,
                updated_at=utc_now(),
            )

    def _worker_loop(self) -> None:
        self._quiesce_stale_episodes()
        while not self._stop_event.is_set():
            episode = self.db.next_queued_episode()
            if episode is not None:
                self._process_episode(episode)
                continue
            self._check_paused_tts_episodes()
            self._check_stale_provider_stage_runs()
            with self._condition:
                self._condition.wait(timeout=1.0)

    @staticmethod
    def _timestamp_age_seconds(value: Any) -> float | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds())

    def _check_stale_provider_stage_runs(self) -> None:
        """Fail stale provider-driven stage runs so the workflow can be retried."""
        for run in self.db.list_running_stage_runs():
            stage = str(run.get("stage") or "")
            if stage not in PROVIDER_STRUCTURED_STAGES:
                continue
            if run.get("pipeline_status") != "running" or run.get("current_stage") != stage:
                continue
            age_seconds = self._timestamp_age_seconds(run.get("started_at"))
            if age_seconds is None or age_seconds < self._provider_stage_stale_seconds:
                continue
            timeout_seconds = int(self._provider_stage_stale_seconds)
            stage_label = stage.replace("_", " ").title()
            error_message = (
                f"{stage_label} timed out after {timeout_seconds} seconds. "
                "The provider CLI did not finish, so the workflow was marked failed."
            )
            self.db.finish_stage_run(
                int(run["id"]),
                status="failed",
                exit_code=1,
                error_text=error_message,
            )
            self.db.update_episode(
                run["episode_id"],
                board_status="Needs Attention",
                pipeline_status="failed",
                current_stage=stage,
                last_error=error_message,
                updated_at=utc_now(),
            )

    @staticmethod
    def _safe_delete_path(path: Path, allowed_root: Path) -> None:
        candidate = path.resolve()
        root = allowed_root.resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError(f"Refusing to delete path outside workspace roots: {candidate}") from exc
        if candidate == root:
            raise ValueError(f"Refusing to delete root directory: {candidate}")
        if not candidate.exists():
            return
        if candidate.is_dir():
            shutil.rmtree(candidate)
        else:
            candidate.unlink()

    def _global_settings(self) -> dict[str, Any]:
        return {**DEFAULT_SETTINGS, **self.db.get_settings()}

    @staticmethod
    def _parse_json_list(value: Any) -> list[Any]:
        if isinstance(value, list):
            return value
        if value in (None, ""):
            return []
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return []
        return parsed if isinstance(parsed, list) else []

    @staticmethod
    def _parse_json_dict(value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return value
        if value in (None, ""):
            return {}
        try:
            parsed = json.loads(value)
        except (TypeError, json.JSONDecodeError):
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _hydrate_project_record(self, project: dict[str, Any] | None) -> dict[str, Any] | None:
        if project is None:
            return None
        payload = dict(project)
        payload["master_language"] = str(payload.get("master_language") or "en").strip() or "en"
        normalized_languages: list[str] = []
        for language_code in self._parse_json_list(payload.get("configured_languages")):
            normalized = str(language_code or "").strip()
            if normalized and normalized not in normalized_languages:
                normalized_languages.append(normalized)
        payload["configured_languages"] = normalized_languages
        payload["language_voice_profiles"] = self._parse_json_dict(payload.get("language_voice_profiles"))
        payload["language_translation_profiles"] = self._parse_json_dict(payload.get("language_translation_profiles"))
        payload["language_channel_names"] = self._parse_json_dict(payload.get("language_channel_names"))
        payload["channel_replace_prompt"] = bool(int(payload.get("channel_replace_prompt", 1) or 1))
        payload["channel_replace_post"] = bool(int(payload.get("channel_replace_post", 1) or 1))
        return payload

    def _hydrate_episode_record(self, episode: dict[str, Any] | None) -> dict[str, Any] | None:
        if episode is None:
            return None
        payload = dict(episode)
        normalized_languages: list[str] = []
        for language_code in self._parse_json_list(payload.get("configured_languages")):
            normalized = str(language_code or "").strip()
            if normalized and normalized not in normalized_languages:
                normalized_languages.append(normalized)
        payload["configured_languages"] = normalized_languages
        payload["pause_requested"] = bool(int(payload.get("pause_requested") or 0))
        return payload

    @staticmethod
    def _path_exists(value: Any) -> bool:
        return bool(value) and Path(str(value)).exists()

    @staticmethod
    def _read_path_text(value: Any) -> str:
        if not value:
            return ""
        path = Path(str(value))
        if not path.exists() or not path.is_file():
            return ""
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    @classmethod
    def _path_has_non_empty_text(cls, value: Any) -> bool:
        return bool(cls._read_path_text(value).strip())

    @staticmethod
    def _language_list_text(language_codes: list[str]) -> str:
        cleaned = [str(code or "").strip() for code in language_codes if str(code or "").strip()]
        return ", ".join(cleaned) if cleaned else "the configured languages"

    def _default_episode_start_stage(self, episode: dict[str, Any]) -> str:
        current_stage = str(episode.get("current_stage") or "").strip()
        queued_from_stage = str(episode.get("queued_from_stage") or "").strip()
        pipeline_status = str(episode.get("pipeline_status") or "idle").strip().lower()
        if pipeline_status in {"failed", "paused"} and current_stage in EPISODE_RUNNABLE_STAGES:
            return current_stage
        if queued_from_stage in EPISODE_RUNNABLE_STAGES:
            return queued_from_stage
        if current_stage in EPISODE_RUNNABLE_STAGES:
            return current_stage
        return "consistency_guide"

    def _next_runnable_stage(self, stage: str) -> str | None:
        if stage not in EPISODE_RUNNABLE_STAGES:
            return None
        current_index = EPISODE_RUNNABLE_STAGES.index(stage)
        next_index = current_index + 1
        if next_index >= len(EPISODE_RUNNABLE_STAGES):
            return None
        return EPISODE_RUNNABLE_STAGES[next_index]

    def _build_start_stage_blockers(self, episode: dict[str, Any], start_stage: str) -> list[dict[str, Any]]:
        episode_id = episode["id"]
        master_language = str(episode.get("master_language") or "en").strip() or "en"
        configured_languages = self._parse_json_list(episode.get("configured_languages"))
        language_statuses = {
            status["language_code"]: status
            for status in self.db.get_episode_language_statuses(episode_id)
        }
        blockers: list[dict[str, Any]] = []

        if start_stage == "tts":
            missing_scripts = [
                language_code
                for language_code in configured_languages
                if language_code != master_language
                and (
                    str(language_statuses.get(language_code, {}).get("translation_status") or "").lower() != "done"
                    or not self._path_has_non_empty_text(language_statuses.get(language_code, {}).get("script_path"))
                )
            ]
            if missing_scripts:
                blockers.append(self._queue_issue(
                    "missing_translation_assets",
                    (
                        "Start from TTS requires translated scripts for "
                        f"{self._language_list_text(missing_scripts)}."
                    ),
                    stage=start_stage,
                ))

        if start_stage == "alignment":
            missing_alignment_inputs = [
                language_code
                for language_code in configured_languages
                if not self._path_exists(language_statuses.get(language_code, {}).get("tts_audio_path"))
                or not self._path_has_non_empty_text(
                    language_statuses.get(language_code, {}).get("spoken_script_path")
                    or language_statuses.get(language_code, {}).get("script_path")
                )
            ]
            if missing_alignment_inputs:
                blockers.append(self._queue_issue(
                    "missing_tts_assets",
                    (
                        "Start from alignment requires narration audio and script files for "
                        f"{self._language_list_text(missing_alignment_inputs)}."
                    ),
                    stage=start_stage,
                ))

        if start_stage == "chunking":
            master_status = language_statuses.get(master_language, {})
            if not self._path_exists(master_status.get("srt_path")):
                blockers.append(self._queue_issue(
                    "missing_master_srt",
                    "Start from chunking requires the master-language SRT from alignment.",
                    stage=start_stage,
                    language_code=master_language,
                ))

        if start_stage == "scene_planning" and not self._path_exists(episode.get("planning_manifest_path")):
            blockers.append(self._queue_issue(
                "missing_planning_manifest",
                "Start from scene planning requires chunking output first.",
                stage=start_stage,
            ))

        if start_stage in {"video_prompt_generation", "image_prompt_generation"}:
            if not self._path_exists(episode.get("consistency_guide_path")):
                blockers.append(self._queue_issue(
                    "missing_consistency_guide",
                    "Prompt generation requires a consistency guide first.",
                    stage=start_stage,
                ))
            if not self._path_exists(episode.get("timeline_draft_path")):
                blockers.append(self._queue_issue(
                    "missing_timeline_draft",
                    "Prompt generation requires a timeline draft first.",
                    stage=start_stage,
                ))

        if start_stage == "timeline_mapping":
            if not self._path_exists(episode.get("timeline_draft_path")):
                blockers.append(self._queue_issue(
                    "missing_timeline_draft",
                    "Timeline mapping requires the master timeline draft first.",
                    stage=start_stage,
                ))
            missing_timeline_inputs = [
                language_code
                for language_code in configured_languages
                if not self._path_exists(language_statuses.get(language_code, {}).get("srt_path"))
            ]
            if missing_timeline_inputs:
                blockers.append(self._queue_issue(
                    "missing_srt_assets",
                    (
                        "Timeline mapping requires aligned SRT files for "
                        f"{self._language_list_text(missing_timeline_inputs)}."
                    ),
                    stage=start_stage,
                ))

        return blockers

    def _reset_episode_outputs_from_stage(self, episode_id: str, start_stage: str) -> None:
        episode = self._hydrate_episode_record(self.db.get_episode(episode_id))
        if episode is None:
            raise FileNotFoundError("Episode not found.")
        workspace = self._episode_workspace(episode)
        master_language = str(episode.get("master_language") or "en").strip() or "en"
        configured_languages = episode.get("configured_languages") or [master_language]
        script_original_path = workspace / "script_original.txt"
        if not script_original_path.exists():
            write_text(script_original_path, episode.get("script_text") or "")
        script_original_spoken_path = workspace / "script_original_spoken.txt"
        if not script_original_spoken_path.exists():
            write_text(
                script_original_spoken_path,
                build_spoken_script(episode.get("script_text") or "", master_language),
            )

        def reset_languages(*, include_languages: list[str], fields: dict[str, Any]) -> None:
            for language_code in include_languages:
                self.db.update_episode_language_status(
                    episode_id,
                    language_code,
                    **(fields | {"error_message": None}),
                )

        episode_fields: dict[str, Any] = {
            "review_ready": 0,
            "last_error": None,
            "pause_requested": 0,
        }

        if start_stage == "consistency_guide":
            episode_fields.update({
                "consistency_guide_path": None,
                "visual_bible_validation_path": None,
                "planning_manifest_path": None,
                "timeline_draft_path": None,
                "timeline_validation_path": None,
                "prompt_list_draft_path": None,
                "prompt_blueprint_path": None,
                "prompt_validation_path": None,
                "master_scenes_path": None,
            })
            reset_languages(
                include_languages=configured_languages,
                fields={"timeline_status": "pending", "timeline_path": None},
            )
        elif start_stage == "translation":
            self.db.update_episode_language_status(
                episode_id,
                master_language,
                translation_status="done",
                script_path=str(script_original_path),
                spoken_script_path=str(script_original_spoken_path),
                error_message=None,
            )
            reset_languages(
                include_languages=[lang for lang in configured_languages if lang != master_language],
                fields={
                    "translation_status": "pending",
                    "script_path": None,
                    "spoken_script_path": None,
                    "tts_status": "pending",
                    "tts_audio_path": None,
                    "tts_job_id": None,
                    "srt_status": "pending",
                    "srt_path": None,
                    "timeline_status": "pending",
                    "timeline_path": None,
                },
            )
        elif start_stage == "tts":
            reset_languages(
                include_languages=configured_languages,
                fields={
                    "tts_status": "pending",
                    "tts_audio_path": None,
                    "tts_job_id": None,
                    "srt_status": "pending",
                    "srt_path": None,
                    "timeline_status": "pending",
                    "timeline_path": None,
                },
            )
        elif start_stage == "alignment":
            reset_languages(
                include_languages=configured_languages,
                fields={
                    "srt_status": "pending",
                    "srt_path": None,
                    "timeline_status": "pending",
                    "timeline_path": None,
                },
            )
        elif start_stage == "chunking":
            episode_fields.update({
                "planning_manifest_path": None,
                "timeline_draft_path": None,
                "timeline_validation_path": None,
                "prompt_list_draft_path": None,
                "prompt_blueprint_path": None,
                "prompt_validation_path": None,
                "master_scenes_path": None,
            })
            reset_languages(
                include_languages=configured_languages,
                fields={"timeline_status": "pending", "timeline_path": None},
            )
        elif start_stage == "scene_planning":
            episode_fields.update({
                "timeline_draft_path": None,
                "timeline_validation_path": None,
                "prompt_list_draft_path": None,
                "prompt_blueprint_path": None,
                "prompt_validation_path": None,
                "master_scenes_path": None,
            })
            reset_languages(
                include_languages=configured_languages,
                fields={"timeline_status": "pending", "timeline_path": None},
            )
        elif start_stage in {"video_prompt_generation", "image_prompt_generation"}:
            episode_fields.update({
                "prompt_list_draft_path": None,
                "prompt_blueprint_path": None,
                "prompt_validation_path": None,
            })
        elif start_stage == "timeline_mapping":
            reset_languages(
                include_languages=configured_languages,
                fields={"timeline_status": "pending", "timeline_path": None},
            )

        self.db.update_episode(episode_id, **episode_fields, updated_at=utc_now())

    @staticmethod
    def _queue_issue(
        code: str,
        message: str,
        **details: Any,
    ) -> dict[str, Any]:
        payload = {"code": code, "message": message}
        payload.update({key: value for key, value in details.items() if value is not None})
        return payload

    def _resolved_project_config(self, project: dict[str, Any]) -> dict[str, Any]:
        settings = self._global_settings()
        scene_provider = project.get("scene_planning_provider") or settings["default_scene_planning_provider"]
        visual_provider = project.get("visual_bible_provider") or settings["default_visual_bible_provider"]
        video_provider = project.get("video_prompt_provider") or settings["default_video_prompt_provider"]
        image_provider = project.get("image_prompt_provider") or settings["default_image_prompt_provider"]
        return {
            "scene_planning_provider": scene_provider,
            "visual_bible_provider": visual_provider,
            "video_prompt_provider": video_provider,
            "image_prompt_provider": image_provider,
            "scene_planning_model": self._resolve_model_choice(
                scene_provider, project.get("scene_planning_model"), settings["default_scene_planning_model"],
            ),
            "visual_bible_model": self._resolve_model_choice(
                visual_provider, project.get("visual_bible_model"), settings["default_visual_bible_model"],
            ),
            "video_prompt_model": self._resolve_model_choice(
                video_provider, project.get("video_prompt_model"), settings["default_video_prompt_model"],
            ),
            "image_prompt_model": self._resolve_model_choice(
                image_provider, project.get("image_prompt_model"), settings["default_image_prompt_model"],
            ),
            "leading_video_scene_count": int(
                project.get("leading_video_scene_count")
                if project.get("leading_video_scene_count") is not None
                else settings["leading_video_scene_count"]
            ),
        }

    def _stage_provider_openai_api_key(self) -> str:
        return str(self.db.get_setting(STAGE_PROVIDER_OPENAI_API_KEY_SETTING, "") or "").strip()

    def _stage_provider_openai_models(self) -> list[dict[str, Any]]:
        raw = self.db.get_setting(STAGE_PROVIDER_OPENAI_MODELS_SETTING, "[]")
        try:
            parsed = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return []
        if not isinstance(parsed, list):
            return []
        models: list[dict[str, Any]] = []
        for item in parsed:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or "").strip()
            if not model_id:
                continue
            models.append(dict(item))
        return models

    def _stage_provider_model_catalog(self) -> dict[str, list[dict[str, str]]]:
        catalog = {
            provider: [
                {
                    "value": str(option.get("value") or "").strip(),
                    "label": str(option.get("label") or option.get("value") or "").strip(),
                }
                for option in MODEL_CATALOG.get(provider, ())
                if str(option.get("value") or "").strip()
            ]
            for provider in PROVIDERS
        }
        openai_models = self._stage_provider_openai_models()
        if openai_models:
            catalog["openai"] = [
                {
                    "value": str(model.get("id") or "").strip(),
                    "label": " - ".join(
                        part for part in (
                            str(model.get("label") or model.get("id") or "").strip(),
                            str(model.get("capability_label") or "").strip(),
                        ) if part
                    ),
                }
                for model in openai_models
                if str(model.get("id") or "").strip()
            ]
        return catalog

    def _provider_health(self, *, force: bool = False) -> dict[str, Any]:
        payload = self.cli_runner.probe(force=force)
        openai_key = self._stage_provider_openai_api_key()
        openai_models = self._stage_provider_openai_models()
        payload["openai"] = {
            "available": True,
            "logged_in": bool(openai_key),
            "detail": (
                f"Saved API key configured. {len(openai_models)} model option(s) cached."
                if openai_key
                else "No saved API key for OpenAI stage providers."
            ),
            "has_api_key": bool(openai_key),
            "api_key_masked": mask_secret(openai_key),
            "model_count": len(openai_models),
        }
        return payload

    def _stage_provider_api_key(self, provider: str) -> str | None:
        if str(provider or "").strip() in ("openai", "codex"):
            return self._stage_provider_openai_api_key() or None
        return None

    async def _discover_openai_models_with_key(self, resolved_api_key: str) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {resolved_api_key}",
            "Content-Type": "application/json",
        }
        timeout = httpx.Timeout(20.0, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get("https://api.openai.com/v1/models", headers=headers)

        if response.status_code != 200:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            detail = str(payload.get("error", {}).get("message") or "").strip() or response.text[:240].strip()
            if response.status_code == 401:
                raise ValueError(f"OpenAI API key rejected. {detail}".strip())
            raise ValueError(f"OpenAI model discovery failed. {detail}".strip())

        payload = response.json()
        raw_models = payload.get("data")
        if not isinstance(raw_models, list):
            raise ValueError("OpenAI model discovery returned an unexpected payload.")

        normalized_models: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        for raw_model in raw_models:
            if not isinstance(raw_model, dict):
                continue
            normalized = normalize_openai_model(raw_model)
            if not normalized:
                continue
            model_id = str(normalized.get("id") or "").strip()
            if not model_id or model_id in seen_ids:
                continue
            seen_ids.add(model_id)
            normalized_models.append(normalized)

        normalized_models = sort_openai_models(normalized_models)
        recommended_model = recommended_openai_model(normalized_models)
        for model in normalized_models:
            model["recommended"] = model.get("id") == recommended_model

        if not normalized_models:
            raise ValueError("No text-capable OpenAI models were available for this API key.")

        return {
            "models": normalized_models,
            "recommended_model": recommended_model,
        }

    def _build_queue_readiness(
        self,
        *,
        project: dict[str, Any] | None,
        episode: dict[str, Any] | None = None,
        provider_health: dict[str, Any] | None = None,
        voice_profiles: dict[str, dict[str, Any]] | None = None,
        translation_profiles: dict[str, dict[str, Any]] | None = None,
        worker_health: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        blockers: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        hydrated_project = self._hydrate_project_record(project)
        hydrated_episode = self._hydrate_episode_record(episode)

        if hydrated_project is None:
            blockers.append(self._queue_issue(
                "missing_project",
                "Episode is not attached to a niche project.",
                episode_id=hydrated_episode.get("id") if hydrated_episode else None,
            ))
            return {"ok": False, "blockers": blockers, "warnings": warnings}

        provider_health = provider_health or self._provider_health()
        voice_profiles = voice_profiles or {
            profile["id"]: profile
            for profile in self.list_voice_profiles()
        }
        translation_profiles = translation_profiles or {
            profile["id"]: profile
            for profile in self.list_translation_profiles()
        }
        worker_health = worker_health or self.get_worker_health()

        project_id = hydrated_project.get("id")
        master_language = hydrated_project.get("master_language") or hydrated_episode.get("master_language") or "en"
        configured_languages = list(hydrated_project.get("configured_languages") or hydrated_episode.get("configured_languages") or [])
        language_voice_profiles = hydrated_project.get("language_voice_profiles") or {}
        language_translation_profiles = hydrated_project.get("language_translation_profiles") or {}

        if not configured_languages:
            blockers.append(self._queue_issue(
                "missing_configured_languages",
                "Project has no configured languages. Add the master language and any targets before queueing.",
                project_id=project_id,
            ))
        elif master_language not in configured_languages:
            blockers.append(self._queue_issue(
                "master_language_not_configured",
                f"Master language '{master_language}' must be included in configured languages before queueing.",
                project_id=project_id,
                language_code=master_language,
            ))

        for language_code in configured_languages:
            profile_id = str(language_voice_profiles.get(language_code) or "").strip()
            profile = voice_profiles.get(profile_id) if profile_id else None
            if profile is None:
                message = (
                    f"Master language '{language_code}' needs a voice profile before queueing."
                    if language_code == master_language
                    else f"Language '{language_code}' needs a voice profile before queueing."
                )
                blockers.append(self._queue_issue(
                    "missing_voice_profile",
                    message,
                    project_id=project_id,
                    language_code=language_code,
                    profile_id=profile_id or None,
                ))

        for language_code in configured_languages:
            if language_code == master_language:
                continue
            profile_id = str(language_translation_profiles.get(language_code) or "").strip()
            profile = translation_profiles.get(profile_id) if profile_id else None
            if profile is None:
                blockers.append(self._queue_issue(
                    "missing_translation_profile",
                    f"Language '{language_code}' needs a translation profile before queueing.",
                    project_id=project_id,
                    language_code=language_code,
                    profile_id=profile_id or None,
                ))

        resolved_config = self._resolved_project_config(hydrated_project)
        for stage_key, stage_label, provider_field, model_field in QUEUE_PROVIDER_TARGETS:
            provider = str(resolved_config.get(provider_field) or "").strip()
            model = str(resolved_config.get(model_field) or "").strip()
            if not provider:
                blockers.append(self._queue_issue(
                    "missing_provider",
                    f"{stage_label} has no provider selected.",
                    project_id=project_id,
                    stage=stage_key,
                    model=model or None,
                ))
                continue
            health = provider_health.get(provider)
            if not health or not health.get("available"):
                blockers.append(self._queue_issue(
                    "provider_unavailable",
                    f"{stage_label} is set to '{provider}', but that provider is not available on this machine.",
                    project_id=project_id,
                    stage=stage_key,
                    provider=provider,
                    model=model or None,
                ))
                continue
            provider_logged_in = health.get("logged_in")
            if provider == "openai" and not provider_logged_in:
                blockers.append(self._queue_issue(
                    "provider_api_key_required",
                    f"{stage_label} is set to 'openai', but no OpenAI API key is saved for workflow stages.",
                    project_id=project_id,
                    stage=stage_key,
                    provider=provider,
                    model=model or None,
                ))
                continue
            if provider not in {"openai", "codex"} and provider_logged_in is False:
                blockers.append(self._queue_issue(
                    "provider_login_required",
                    f"{stage_label} is set to '{provider}', but the provider is not logged in.",
                    project_id=project_id,
                    stage=stage_key,
                    provider=provider,
                    model=model or None,
                ))

        if worker_health.get("startup_error"):
            warnings.append(self._queue_issue(
                "tts_worker_unavailable",
                "Voice engine unavailable. Queueing is allowed, but TTS will pause until it can start again.",
            ))
        elif str(worker_health.get("device") or "").strip().lower() == "cpu":
            warnings.append(self._queue_issue(
                "tts_worker_cpu_only",
                self._tts_cpu_runtime_warning(),
            ))

        return {
            "ok": len(blockers) == 0,
            "blockers": blockers,
            "warnings": warnings,
        }

    @staticmethod
    def _tts_cpu_runtime_warning() -> str:
        return (
            "Voice engine is available, but running on CPU. Long-form narration will be much slower "
            "until CUDA-enabled PyTorch is installed for this dashboard environment."
        )

    @staticmethod
    def _parse_tts_job_chunk_progress(progress: Any) -> tuple[int | None, int | None]:
        text = " ".join(str(progress or "").split())
        if not text:
            return None, None
        match = re.search(r"chunk\s+(\d+)\s*/\s*(\d+)", text, flags=re.IGNORECASE)
        if not match:
            return None, None
        return int(match.group(1)), int(match.group(2))

    @staticmethod
    def _tts_stage_status_for_job_status(job_status: Any) -> str:
        normalized = str(job_status or "").strip().lower()
        if normalized == "processing":
            return "running"
        if normalized == "queued":
            return "queued"
        if normalized in {"completed", "done"}:
            return "done"
        if normalized in {"error", "failed"}:
            return "failed"
        return "pending"

    def _build_tts_job_client_payload(self, job: dict[str, Any]) -> dict[str, Any]:
        payload = dict(job or {})
        parsed_payload = self._parse_json_dict(payload.get("payload_json"))
        texts = parsed_payload.get("texts")
        total_chunks = len(texts) if isinstance(texts, list) else None
        current_chunk, parsed_total = self._parse_tts_job_chunk_progress(payload.get("progress"))
        if parsed_total:
            total_chunks = parsed_total

        status = str(payload.get("status") or "").strip().lower()
        if status == "completed" and total_chunks and current_chunk is None:
            current_chunk = total_chunks

        percent = None
        if total_chunks and current_chunk:
            percent = max(0, min(100, round((current_chunk / total_chunks) * 100)))

        return {
            "job_id": payload.get("job_id"),
            "status": status or None,
            "progress": str(payload.get("progress") or "").strip() or None,
            "current_chunk": current_chunk,
            "total_chunks": total_chunks,
            "percent": int(percent) if percent is not None else None,
            "updated_at": payload.get("updated_at"),
            "finished_at": payload.get("finished_at"),
        }

    def _decorate_language_statuses_with_tts(
        self,
        language_statuses: list[dict[str, Any]],
        *,
        worker_health: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        active_job: dict[str, Any] | None = None
        active_worker_job_id = str((worker_health or {}).get("current_job_id") or "").strip()
        active_worker_processing = str((worker_health or {}).get("status") or "").strip().lower() == "processing"

        enriched_statuses: list[dict[str, Any]] = []
        for lang_status in language_statuses:
            payload = dict(lang_status)
            job_id = str(payload.get("tts_job_id") or "").strip()
            if not job_id:
                enriched_statuses.append(payload)
                continue

            job = self.db.get_tts_job(job_id)
            if not job:
                enriched_statuses.append(payload)
                continue

            job_payload = self._build_tts_job_client_payload(job)
            payload["tts_job_status"] = job_payload.get("status")
            payload["tts_job_progress"] = job_payload.get("progress")
            payload["tts_job_current_chunk"] = job_payload.get("current_chunk")
            payload["tts_job_total_chunks"] = job_payload.get("total_chunks")
            payload["tts_job_percent"] = job_payload.get("percent")
            payload["tts_job_updated_at"] = job_payload.get("updated_at")
            payload["tts_job_finished_at"] = job_payload.get("finished_at")

            if job_payload.get("status") in {"queued", "processing"}:
                candidate = {
                    **job_payload,
                    "language_code": payload.get("language_code"),
                    "worker_active": active_worker_processing and active_worker_job_id == job_id,
                }
                if active_job is None or (candidate["worker_active"] and not active_job.get("worker_active")):
                    active_job = candidate

            enriched_statuses.append(payload)

        return enriched_statuses, active_job

    def _decorate_episode_for_client(
        self,
        episode: dict[str, Any],
        *,
        project: dict[str, Any] | None = None,
        provider_health: dict[str, Any] | None = None,
        voice_profiles: dict[str, dict[str, Any]] | None = None,
        translation_profiles: dict[str, dict[str, Any]] | None = None,
        worker_health: dict[str, Any] | None = None,
        active_tts_job: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = self._hydrate_episode_record(episode) or {}
        worker_health = worker_health or {}
        payload["queue_readiness"] = self._build_queue_readiness(
            project=project,
            episode=payload,
            provider_health=provider_health,
            voice_profiles=voice_profiles,
            translation_profiles=translation_profiles,
            worker_health=worker_health,
        )
        payload["active_tts_job"] = active_tts_job
        payload["tts_worker_device"] = worker_health.get("device")
        payload["tts_cuda_available"] = worker_health.get("cuda_available")
        payload["tts_gpu_name"] = worker_health.get("gpu_name")
        payload["tts_torch_version"] = worker_health.get("torch_version")
        payload["tts_torch_build"] = worker_health.get("torch_build")
        payload["tts_active_generate_jobs"] = worker_health.get("active_generate_jobs")
        payload["tts_queued_generate_jobs"] = worker_health.get("queued_generate_jobs")
        return payload

    def _decorate_stage_run_for_client(self, run: dict[str, Any]) -> dict[str, Any]:
        payload = dict(run)
        stdout_meta = self._read_preview_meta(payload.get("stdout_path"))
        stderr_meta = self._read_preview_meta(payload.get("stderr_path"))
        payload["stdout_preview"] = self._read_preview_text(payload.get("stdout_path"))
        payload["stderr_preview"] = self._read_preview_text(payload.get("stderr_path"))
        payload["stdout_updated_at"] = stdout_meta["updated_at"]
        payload["stdout_size_bytes"] = stdout_meta["size_bytes"]
        payload["stderr_updated_at"] = stderr_meta["updated_at"]
        payload["stderr_size_bytes"] = stderr_meta["size_bytes"]
        payload["error_message"] = payload.get("error_text") or payload["stderr_preview"] or ""
        return payload

    def _start_structured_stage_run(
        self,
        *,
        episode_id: str,
        stage: str,
        provider: str,
        template_hash: str | None,
        workdir: Path,
        artifact_dir: Path,
        model: str,
        schema: dict[str, Any],
    ) -> int:
        command_payload = {
            "provider": provider,
            "model": model,
            "stage": stage,
            "schema_keys": sorted((schema or {}).get("properties", {}).keys()),
            "artifact_dir": str(artifact_dir),
        }
        return self.db.start_stage_run(
            episode_id=episode_id,
            stage=stage,
            provider=provider,
            template_hash=template_hash,
            workdir=str(workdir),
            command_payload=command_payload,
            stdout_path=None,
            stderr_path=None,
        )

    @staticmethod
    def _known_models_for_provider(provider: str) -> set[str]:
        return {
            str(option.get("value", "")).strip()
            for option in MODEL_CATALOG.get(provider, ())
            if str(option.get("value", "")).strip()
        }

    def _default_model_for_provider(self, provider: str) -> str:
        options = MODEL_CATALOG.get(provider, ())
        if options:
            return str(options[0]["value"])
        return ""

    def _resolve_model_choice(self, provider: str, model: Any, fallback: str) -> str:
        selected = " ".join(str(model or "").split()).strip()
        if not selected:
            return fallback or self._default_model_for_provider(provider)
        known_for_provider = self._known_models_for_provider(provider)
        known_for_other = {
            candidate
            for other_provider in PROVIDERS
            if other_provider != provider
            for candidate in self._known_models_for_provider(other_provider)
        }
        if selected in known_for_provider:
            return selected
        if selected in known_for_other:
            return fallback or self._default_model_for_provider(provider)
        return selected

    @staticmethod
    def _short_text(value: Any, max_words: int) -> str:
        words = " ".join(str(value or "").split()).split()
        if not words:
            return ""
        shortened = words[:max_words]
        while shortened and shortened[-1].lower() in {"and", "or", "with", "of", "the", "a", "an", "but"}:
            shortened = shortened[:-1]
        return " ".join(shortened).strip(" ,;")

    @classmethod
    def _lead_phrase(cls, value: Any, max_words: int) -> str:
        text = " ".join(str(value or "").split())
        if not text:
            return ""
        segments = [segment.strip(" ,;") for segment in re.split(r"[.;]", text) if segment.strip(" ,;")]
        phrase = segments[0] if segments else ""
        if phrase.lower().startswith(("son of", "daughter of")) and len(segments) > 1:
            next_segment = segments[1][0].lower() + segments[1][1:] if segments[1] else ""
            phrase = f"{phrase}, {next_segment}"
        elif len(segments) > 1 and len(phrase.split()) < 5:
            next_segment = segments[1][0].lower() + segments[1][1:] if segments[1] else ""
            phrase = f"{phrase}, {next_segment}"
        comma_parts = [part.strip(" ,;") for part in phrase.split(",") if part.strip(" ,;")]
        if len(comma_parts) >= 2:
            phrase = ", ".join(comma_parts[:2])
        return cls._short_text(phrase, max_words)

    @classmethod
    def _character_aliases(cls, card: dict[str, Any]) -> set[str]:
        label = " ".join(str(card.get("label") or card.get("character_id") or "").split())
        aliases: set[str] = set()
        if label:
            aliases.add(re.sub(r"\s*\(.*?\)", "", label).strip())
            aliases.update(part.strip() for part in re.findall(r"\((.*?)\)", label) if part.strip())
        character_id = " ".join(str(card.get("character_id") or "").split()).replace("_", " ").strip()
        if character_id:
            aliases.add(character_id)
        return {alias for alias in aliases if alias}

    @classmethod
    def _character_prompt_descriptor(cls, card: dict[str, Any]) -> str:
        label = re.sub(r"\s*\(.*?\)", "", " ".join(str(card.get("label") or "").split())).strip()
        visual = cls._short_text(card.get("visual_description"), 18)
        wardrobe = cls._short_text(card.get("wardrobe"), 12)
        detail_parts = [part for part in (visual, wardrobe) if part]
        detail = ", ".join(detail_parts)
        if label and detail:
            return f"{label}: {detail}"
        return label or detail

    @classmethod
    def _scene_character_descriptors(
        cls,
        scene: dict[str, Any],
        characters: list[dict[str, Any]],
    ) -> list[str]:
        haystack = " ".join(
            str(scene.get(field) or "")
            for field in ("text", "visual_intent", "notes")
        )
        if not haystack.strip():
            return []
        descriptors: list[str] = []
        lowered_haystack = haystack.lower()
        for card in characters:
            aliases = cls._character_aliases(card)
            if any(re.search(rf"\b{re.escape(alias.lower())}\b", lowered_haystack) for alias in aliases):
                descriptor = cls._character_prompt_descriptor(card)
                if descriptor:
                    descriptors.append(descriptor)
        return descriptors

    @classmethod
    def _build_prompt_context(
        cls,
        visual_bible: dict[str, Any],
        scenes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        world_style = visual_bible.get("world_style") if isinstance(visual_bible.get("world_style"), dict) else {}
        characters = visual_bible.get("characters") if isinstance(visual_bible.get("characters"), list) else []
        continuity_rules = visual_bible.get("continuity_rules") if isinstance(visual_bible.get("continuity_rules"), list) else []
        environment_rules = visual_bible.get("environment_rules") if isinstance(visual_bible.get("environment_rules"), list) else []

        scene_character_guide: dict[str, list[str]] = {}
        for scene in scenes:
            descriptors = cls._scene_character_descriptors(scene, characters)
            if descriptors:
                scene_character_guide[scene["scene_id"]] = descriptors

        return {
            "style_guide": {
                "setting": cls._short_text(world_style.get("setting"), 28),
                "look": cls._short_text(world_style.get("look"), 24),
                "palette": cls._short_text(world_style.get("palette"), 18),
                "lighting": cls._short_text(world_style.get("lighting"), 18),
                "camera_language": cls._short_text(world_style.get("camera_language"), 18),
                "negative_rules": cls._short_text(world_style.get("negative_rules"), 18),
            },
            "continuity_rules": [
                cls._short_text(rule, 14) for rule in continuity_rules[:4] if cls._short_text(rule, 14)
            ],
            "environment_rules": [
                cls._short_text(rule, 14) for rule in environment_rules[:4] if cls._short_text(rule, 14)
            ],
            "scene_character_guide": scene_character_guide,
        }

    @classmethod
    def _inline_character_descriptor(cls, card: dict[str, Any]) -> str:
        visual = cls._lead_phrase(card.get("visual_description"), 10)
        return visual[0].lower() + visual[1:] if visual else ""

    @staticmethod
    def _ordered_unique(values: list[str]) -> list[str]:
        unique_values: list[str] = []
        seen: set[str] = set()
        for value in values:
            cleaned = " ".join(str(value or "").split()).strip()
            if not cleaned or cleaned in seen:
                continue
            seen.add(cleaned)
            unique_values.append(cleaned)
        return unique_values

    @classmethod
    def _character_lookup(cls, visual_bible: dict[str, Any]) -> dict[str, dict[str, Any]]:
        characters = visual_bible.get("characters") if isinstance(visual_bible.get("characters"), list) else []
        lookup: dict[str, dict[str, Any]] = {}
        for card in characters:
            if not isinstance(card, dict):
                continue
            character_id = " ".join(str(card.get("character_id") or "").split())
            if not character_id:
                continue
            aliases = sorted(cls._character_aliases(card), key=len, reverse=True)
            display_name = re.sub(r"\s*\(.*?\)", "", " ".join(str(card.get("label") or character_id).split())).strip()
            canonical_key = character_id.lower().replace("_", " ")
            unique_aliases = cls._ordered_unique([display_name, *aliases, character_id.replace("_", " ")])
            payload = {
                "key": canonical_key,
                "display_name": display_name or character_id.replace("_", " "),
                "descriptor": cls._inline_character_descriptor(card),
                "aliases": unique_aliases,
                "alias_patterns": [
                    re.compile(rf"\b{re.escape(alias)}\b", re.IGNORECASE)
                    for alias in unique_aliases
                ],
                "alias_possessive_patterns": [
                    re.compile(rf"\b{re.escape(alias)}(?:'s)?\b", re.IGNORECASE)
                    for alias in unique_aliases
                ],
            }
            lookup_keys = {
                character_id.lower(),
                character_id.lower().replace("_", " "),
                *(alias.lower() for alias in aliases),
                *(alias.lower().replace("_", " ") for alias in aliases),
            }
            for key in lookup_keys:
                lookup[key] = payload
        return lookup

    @classmethod
    def _normalize_character_ref(
        cls,
        character_ref: Any,
        character_lookup: dict[str, dict[str, Any]],
    ) -> str | None:
        raw_value = " ".join(str(character_ref or "").split()).strip()
        if not raw_value:
            return None
        candidates = [
            raw_value.lower(),
            raw_value.lower().replace("_", " "),
            re.split(r"[:(,;/|-]", raw_value.lower(), maxsplit=1)[0].strip(),
            re.split(r"[:(,;/|-]", raw_value.lower().replace("_", " "), maxsplit=1)[0].strip(),
        ]
        for candidate in cls._ordered_unique(candidates):
            payload = character_lookup.get(candidate)
            if payload:
                return payload["key"]
        return None

    @classmethod
    def _text_character_refs(
        cls,
        value: Any,
        character_lookup: dict[str, dict[str, Any]],
    ) -> list[str]:
        text = " ".join(str(value or "").split())
        if not text:
            return []
        sanitized = re.sub(r"\bAbraham Accords\b", "", text, flags=re.IGNORECASE)
        matches: list[str] = []
        unique_payloads = {payload["key"]: payload for payload in character_lookup.values()}
        for payload in unique_payloads.values():
            for pattern in payload.get("alias_possessive_patterns", []):
                if pattern.search(sanitized):
                    matches.append(payload["key"])
                    break
        return cls._ordered_unique(matches)

    @classmethod
    def _entry_character_refs(
        cls,
        entry: dict[str, Any],
        character_lookup: dict[str, dict[str, Any]],
    ) -> list[str]:
        refs: list[str] = []
        for raw_ref in entry.get("character_refs", []):
            normalized = cls._normalize_character_ref(raw_ref, character_lookup)
            if normalized:
                refs.append(normalized)
        for field_name in ("subject", "setting", "action", "composition"):
            refs.extend(cls._text_character_refs(entry.get(field_name, ""), character_lookup))
        return cls._ordered_unique(refs)

    @classmethod
    def _expand_subject_with_characters(
        cls,
        subject: str,
        character_refs: list[str],
        character_lookup: dict[str, dict[str, Any]],
    ) -> str:
        expanded = " ".join(str(subject or "").split())
        replaced_any = False
        for character_ref in character_refs[:3]:
            payload = character_lookup.get(character_ref)
            if not payload:
                continue
            descriptor = payload["descriptor"]
            if not descriptor:
                continue
            if descriptor.lower() in expanded.lower():
                continue
            for pattern in payload.get("alias_patterns", []):
                if pattern.search(expanded):
                    expanded = pattern.sub(lambda match: f"{match.group(0)} ({descriptor})", expanded, count=1)
                    replaced_any = True
                    break
        if not replaced_any:
            descriptors = [
                f"{character_lookup[character_ref].get('display_name') or character_lookup[character_ref]['aliases'][0]} ({character_lookup[character_ref]['descriptor']})"
                for character_ref in character_refs[:2]
                if character_lookup.get(character_ref, {}).get("descriptor")
            ]
            if descriptors:
                prefix = " and ".join(descriptors)
                if expanded:
                    expanded = f"{prefix}, {expanded[0].lower()}{expanded[1:]}"
                else:
                    expanded = prefix
        return " ".join(expanded.split())

    @classmethod
    def _enrich_prompt_entries(
        cls,
        prompt_entries: list[dict[str, Any]],
        visual_bible: dict[str, Any],
    ) -> list[dict[str, Any]]:
        character_lookup = cls._character_lookup(visual_bible)
        if not character_lookup:
            return prompt_entries
        enriched_entries: list[dict[str, Any]] = []
        for entry in prompt_entries:
            updated_entry = dict(entry)
            character_refs = cls._entry_character_refs(updated_entry, character_lookup)
            if character_refs:
                updated_entry["character_refs"] = character_refs
                updated_entry["subject"] = cls._expand_subject_with_characters(
                    updated_entry.get("subject", ""),
                    character_refs,
                    character_lookup,
                )
            enriched_entries.append(updated_entry)
        return enriched_entries


    def get_health(self) -> dict[str, Any]:
        return {
            "alignment": alignment_health(),
            "providers": self._provider_health(),
            "languages": [
                {
                    "code": profile.code,
                    "label": profile.label,
                    "mfa_resources": mfa_resource_status(profile.code),
                }
                for profile in LANGUAGE_PROFILES
            ],
        }

    def prepare_language(self, language_code: str) -> dict[str, Any]:
        return prepare_mfa_language_resources_async(language_code)

    def get_settings_payload(self) -> dict[str, Any]:
        settings = self._global_settings()
        openai_api_key = self._stage_provider_openai_api_key()
        openai_models = self._stage_provider_openai_models()
        settings.pop(STAGE_PROVIDER_OPENAI_API_KEY_SETTING, None)
        settings.pop(STAGE_PROVIDER_OPENAI_MODELS_SETTING, None)
        settings.pop(STAGE_PROVIDER_OPENAI_RECOMMENDED_MODEL_SETTING, None)
        settings.pop(STAGE_PROVIDER_OPENAI_SYNCED_AT_SETTING, None)
        settings["stage_provider_openai_has_api_key"] = bool(openai_api_key)
        settings["stage_provider_openai_api_key_masked"] = mask_secret(openai_api_key)
        settings["stage_provider_openai_model_count"] = len(openai_models)
        settings["stage_provider_openai_recommended_model"] = str(
            self.db.get_setting(STAGE_PROVIDER_OPENAI_RECOMMENDED_MODEL_SETTING, "") or ""
        ).strip()
        settings["stage_provider_openai_last_synced_at"] = str(
            self.db.get_setting(STAGE_PROVIDER_OPENAI_SYNCED_AT_SETTING, "") or ""
        ).strip()
        settings["voice_tts_default_preset"] = DEFAULT_VOICE_TTS_PRESET
        settings["voice_tts_presets"] = voice_tts_presets_payload()
        settings["voice_tts_limits"] = voice_tts_limits_payload()
        return {
            "settings": settings,
            "templates": self.templates.list_templates(),
            "agents_root": str(AGENTS_ROOT),
            "model_catalog": self._stage_provider_model_catalog(),
        }

    def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        openai_api_key = payload.pop(STAGE_PROVIDER_OPENAI_API_KEY_SETTING, None)
        for key in DEFAULT_SETTINGS:
            if key in payload:
                self.db.set_setting(key, payload[key])
        if openai_api_key is not None:
            normalized_key = str(openai_api_key or "").strip()
            if normalized_key:
                self.db.set_setting(STAGE_PROVIDER_OPENAI_API_KEY_SETTING, normalized_key)
        return self.get_settings_payload()

    def save_template(self, stage: str, provider: str, body: str) -> dict[str, Any]:
        return self.templates.save_template(stage, provider, body)

    def _read_preview_json(self, path_value: str | None, limit_items: int | None = None) -> Any:
        if not path_value:
            return None
        payload = read_json(Path(path_value))
        if limit_items is not None and isinstance(payload, list):
            return payload[:limit_items]
        return payload

    def _read_preview_jsonl(self, path_value: str | None, limit_items: int | None = None) -> Any:
        if not path_value:
            return None
        payload = read_jsonl(Path(path_value), default=[])
        if limit_items is not None and isinstance(payload, list):
            return payload[:limit_items]
        return payload

    def _read_preview_text(self, path_value: str | None) -> str | None:
        if not path_value:
            return None
        return clamp_preview(read_text(Path(path_value)), MAX_PREVIEW_CHARS)

    def _read_preview_meta(self, path_value: str | None) -> dict[str, Any]:
        if not path_value:
            return {"updated_at": None, "size_bytes": None}
        preview_path = Path(path_value)
        try:
            stat = preview_path.stat()
        except OSError:
            return {"updated_at": None, "size_bytes": None}
        return {
            "updated_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
            "size_bytes": int(stat.st_size),
        }


    def get_target_languages(self) -> list[dict[str, str]]:
        return list(TARGET_LANGUAGES)

    # ── voice profile management ────────────────────────────────────

    @staticmethod
    def _serialize_tts_job(job: dict[str, Any] | None) -> dict[str, Any] | None:
        if job is None:
            return None
        payload = dict(job)
        for raw_key, parsed_key in (("payload_json", "payload"), ("meta_json", "meta")):
            raw_value = payload.get(raw_key, "")
            try:
                payload[parsed_key] = json.loads(raw_value) if raw_value else {}
            except (TypeError, json.JSONDecodeError):
                payload[parsed_key] = {}
        result_path = str(payload.get("result_path") or "").strip()
        result_available = bool(result_path and Path(result_path).exists())
        payload["result_available"] = result_available
        payload["download_url"] = f"/api/tts-jobs/{payload['job_id']}/download" if result_available else None
        return payload

    def _enrich_voice_profile(self, profile: dict[str, Any] | None) -> dict[str, Any] | None:
        if profile is None:
            return None
        payload = dict(profile)
        profile_id = str(payload.get("id") or "").strip()
        payload["tts_config"] = self._resolve_voice_tts_config(payload)
        payload.pop("tts_config_json", None)
        payload["latest_latent_job"] = self._serialize_tts_job(
            self.db.get_latest_latent_job_for_profile(profile_id)
        ) if profile_id else None
        payload["latest_test_job"] = self._serialize_tts_job(
            self.db.get_latest_test_job_for_profile(profile_id)
        ) if profile_id else None
        return payload

    def list_voice_profiles(self) -> list[dict[str, Any]]:
        return [
            enriched
            for profile in self.db.list_voice_profiles()
            if (enriched := self._enrich_voice_profile(profile)) is not None
        ]

    def get_voice_profile(self, profile_id: str) -> dict[str, Any]:
        profile = self.db.get_voice_profile(profile_id)
        if profile is None:
            raise FileNotFoundError("Voice profile not found.")
        return self._enrich_voice_profile(profile) or {}

    @staticmethod
    def _resolve_voice_tts_config(profile_or_raw: Any = None) -> dict[str, Any]:
        if isinstance(profile_or_raw, dict):
            raw = profile_or_raw.get("tts_config_json")
            if raw in (None, ""):
                raw = profile_or_raw.get("tts_config")
            return normalize_voice_tts_config(raw)
        return normalize_voice_tts_config(profile_or_raw)

    def _build_generate_tts_payload(
        self,
        *,
        profile: dict[str, Any],
        language: str,
        script_text: str,
    ) -> dict[str, Any]:
        from .tts.constants import map_language_code

        resolved_text = str(script_text or "").strip()
        if not resolved_text:
            raise ValueError("No valid text to generate audio.")
        xtts_language = map_language_code(str(language or "").strip() or "en")
        tts_config = self._resolve_voice_tts_config(profile)
        texts = chunk_text_for_voice_tts(resolved_text, tts_config)
        if not texts:
            raise ValueError("No valid text to generate audio.")
        return {
            "profile_id": profile.get("id", ""),
            "ref_path": profile.get("audio_path", ""),
            "language": xtts_language,
            "text": resolved_text,
            "texts": texts,
            "chunked": True,
            "tts_config": tts_config,
        }

    def _build_voice_test_payload(
        self,
        *,
        profile: dict[str, Any],
        text: str,
        language: str,
    ) -> dict[str, Any]:
        resolved_text = str(text or "").strip()
        if not resolved_text:
            raise ValueError("No valid text to generate audio.")
        tts_config = self._resolve_voice_tts_config(profile)
        texts = chunk_text_for_voice_tts(resolved_text, tts_config)
        if not texts:
            raise ValueError("No valid text to generate audio.")
        return {
            "profile_id": profile.get("id", ""),
            "ref_path": profile.get("audio_path", ""),
            "language": language,
            "text": resolved_text,
            "texts": texts,
            "chunked": True,
            "tts_config": tts_config,
        }

    @staticmethod
    def _normalize_translation_compare_text(text: str) -> str:
        return re.sub(r"\s+", " ", str(text or "").strip().lower())

    @classmethod
    def _translation_source_paragraphs(cls, text: str) -> list[str]:
        paragraphs: list[str] = []
        for paragraph in re.split(r"\n\s*\n+", str(text or "").strip()):
            normalized = cls._normalize_translation_compare_text(paragraph)
            if len(normalized.split()) >= 6:
                paragraphs.append(normalized)
        return paragraphs

    @staticmethod
    def _translation_uses_non_english_target(language_code: str) -> bool:
        normalized = str(language_code or "").strip().lower()
        return not normalized.startswith("en")

    @classmethod
    def _apply_channel_cta_fallback(
        cls,
        translated_script: str,
        *,
        language_code: str,
        target_channel_name: str,
    ) -> str:
        return apply_channel_cta_fallback(
            translated_script,
            language_code=language_code,
            target_channel_name=target_channel_name,
        )

    @classmethod
    def _translation_script_validation_issues(
        cls,
        *,
        source_script: str,
        translated_script: str,
        language_code: str,
        source_channel_name: str = "",
        target_channel_name: str = "",
    ) -> list[str]:
        return collect_translation_quality_issues(
            source_text=source_script,
            translated_text=translated_script,
            language_code=language_code,
            source_channel_name=source_channel_name,
            target_channel_name=target_channel_name,
        )

    @classmethod
    def _validated_translation_script(
        cls,
        result: Any,
        *,
        language_code: str,
        source_script: str = "",
        source_channel_name: str = "",
        target_channel_name: str = "",
    ) -> str:
        translated_script = str(getattr(result, "translated_script", "") or "").strip()
        status = str(getattr(result, "status", "done") or "done").strip().lower()
        chunk_results = list(getattr(result, "chunk_results", []) or [])
        result_error = str(getattr(result, "error_message", "") or "").strip()

        first_chunk_error = next(
            (
                str(getattr(chunk, "error", "") or "").strip()
                for chunk in chunk_results
                if str(getattr(chunk, "status", "") or "").strip().lower() != "ok"
                and str(getattr(chunk, "error", "") or "").strip()
            ),
            "",
        )
        detail = result_error or first_chunk_error
        suffix = f" First chunk error: {detail}" if detail else ""
        if not translated_script:
            raise ValueError(f"Translation returned empty text for {language_code}.{suffix}")
        if status == "partial":
            raise ValueError(f"Translation returned partial output for {language_code}.{suffix}")
        if status == "error":
            raise ValueError(f"Translation failed for {language_code}.{suffix}")
        if status not in {"done", "completed", "success"}:
            raise ValueError(f"Translation status for {language_code} was '{status or 'unknown'}'.{suffix}")

        if source_channel_name and target_channel_name:
            translated_script = re.sub(
                re.escape(source_channel_name),
                target_channel_name,
                translated_script,
                flags=re.IGNORECASE,
            )
            translated_script = cls._apply_channel_cta_fallback(
                translated_script,
                language_code=language_code,
                target_channel_name=target_channel_name,
            )

        issues = cls._translation_script_validation_issues(
            source_script=source_script,
            translated_script=translated_script,
            language_code=language_code,
            source_channel_name=source_channel_name,
            target_channel_name=target_channel_name,
        )
        if issues:
            issue_text = "; ".join(issues)
            raise ValueError(f"Translation quality validation failed for {language_code}: {issue_text}.{suffix}")
        return translated_script

    @staticmethod
    def _language_readable_script_filename(language_code: str, master_language: str) -> str:
        return "script_original.txt" if language_code == master_language else f"script_{language_code}.txt"

    @staticmethod
    def _language_spoken_script_filename(language_code: str, master_language: str) -> str:
        return "script_original_spoken.txt" if language_code == master_language else f"script_{language_code}_spoken.txt"

    @classmethod
    def _write_language_script_assets(
        cls,
        *,
        workspace: Path,
        language_code: str,
        master_language: str,
        readable_script: str,
    ) -> tuple[Path, Path]:
        readable_path = workspace / cls._language_readable_script_filename(language_code, master_language)
        write_text(readable_path, readable_script)
        spoken_path = workspace / cls._language_spoken_script_filename(language_code, master_language)
        write_text(spoken_path, build_spoken_script(readable_script, language_code))
        return readable_path, spoken_path

    def _ensure_spoken_script_asset(
        self,
        *,
        episode: dict[str, Any],
        language_code: str,
        language_status: dict[str, Any] | None,
    ) -> Path | None:
        status = language_status or {}
        spoken_path_value = status.get("spoken_script_path")
        if spoken_path_value and Path(str(spoken_path_value)).exists():
            return Path(str(spoken_path_value))

        master_language = str(episode.get("master_language") or "en").strip() or "en"
        workspace = self._episode_workspace(episode)
        readable_path_value = status.get("script_path")
        readable_text = self._read_path_text(readable_path_value).strip()
        readable_path: Path | None = Path(str(readable_path_value)) if readable_path_value else None

        if language_code == master_language:
            readable_text = readable_text or str(episode.get("script_text") or "").strip()
            if not readable_text:
                return None
            if readable_path is None:
                readable_path = workspace / self._language_readable_script_filename(language_code, master_language)
            if not readable_path.exists():
                write_text(readable_path, readable_text)
        elif not readable_text:
            return None

        spoken_path = workspace / self._language_spoken_script_filename(language_code, master_language)
        write_text(spoken_path, build_spoken_script(readable_text, language_code))
        update_fields: dict[str, Any] = {"spoken_script_path": str(spoken_path)}
        if readable_path is not None and not status.get("script_path"):
            update_fields["script_path"] = str(readable_path)
        self.db.update_episode_language_status(episode["id"], language_code, **update_fields)
        return spoken_path

    def _preferred_audio_script_path(
        self,
        *,
        episode: dict[str, Any],
        language_code: str,
        language_status: dict[str, Any] | None,
    ) -> Path | None:
        status = language_status or {}
        spoken_path = status.get("spoken_script_path")
        if spoken_path and Path(str(spoken_path)).exists():
            return Path(str(spoken_path))
        generated_path = self._ensure_spoken_script_asset(
            episode=episode,
            language_code=language_code,
            language_status=language_status,
        )
        if generated_path and generated_path.exists():
            return generated_path
        readable_path = status.get("script_path")
        if readable_path and Path(str(readable_path)).exists():
            return Path(str(readable_path))
        return None

    # ── translation profile management ──────────────────────────────

    def list_translation_profiles(self) -> list[dict[str, Any]]:
        return self.db.list_translation_profiles()

    def list_translation_profiles_public(self) -> list[dict[str, Any]]:
        return [sanitize_translation_profile(profile) for profile in self.list_translation_profiles()]

    def get_translation_profile(self, profile_id: str) -> dict[str, Any]:
        profile = self.db.get_translation_profile(profile_id)
        if profile is None:
            raise FileNotFoundError("Translation profile not found.")
        return profile

    def get_translation_profile_public(self, profile_id: str) -> dict[str, Any]:
        return sanitize_translation_profile(self.get_translation_profile(profile_id))

    async def discover_openai_translation_models(
        self,
        *,
        api_key: str = "",
        profile_id: str | None = None,
    ) -> dict[str, Any]:
        profile = None
        resolved_api_key = str(api_key or "").strip()
        if profile_id:
            profile = self.db.get_translation_profile(profile_id)
            if profile is None:
                raise FileNotFoundError("Translation profile not found.")
            if str(profile.get("provider") or "").strip() != "openai":
                raise ValueError("Only OpenAI translation profiles support model discovery.")
            if not resolved_api_key:
                resolved_api_key = str(profile.get("api_key_ref") or "").strip()
        if not resolved_api_key:
            raise ValueError("Paste an OpenAI API key first.")
        discovery = await self._discover_openai_models_with_key(resolved_api_key)

        return {
            "models": discovery["models"],
            "recommended_model": discovery["recommended_model"],
            "from_saved_key": bool(profile_id and not str(api_key or "").strip()),
            "profile_id": profile_id,
        }

    async def discover_openai_stage_provider_models(
        self,
        *,
        api_key: str = "",
    ) -> dict[str, Any]:
        resolved_api_key = str(api_key or "").strip() or self._stage_provider_openai_api_key()
        if not resolved_api_key:
            raise ValueError("Paste an OpenAI API key first.")
        discovery = await self._discover_openai_models_with_key(resolved_api_key)
        self.db.set_setting(STAGE_PROVIDER_OPENAI_MODELS_SETTING, json.dumps(discovery["models"]))
        self.db.set_setting(
            STAGE_PROVIDER_OPENAI_RECOMMENDED_MODEL_SETTING,
            str(discovery["recommended_model"] or "").strip(),
        )
        self.db.set_setting(STAGE_PROVIDER_OPENAI_SYNCED_AT_SETTING, utc_now())
        return {
            "models": discovery["models"],
            "recommended_model": discovery["recommended_model"],
            "from_saved_key": not str(api_key or "").strip(),
            "api_key_saved": bool(self._stage_provider_openai_api_key()),
            "api_key_masked": mask_secret(self._stage_provider_openai_api_key()),
            "last_synced_at": str(self.db.get_setting(STAGE_PROVIDER_OPENAI_SYNCED_AT_SETTING, "") or ""),
        }

    def create_translation_profile(
        self,
        *,
        name: str,
        provider: str,
        api_key: str,
        model: str,
    ) -> dict[str, Any]:
        provider = str(provider or "").strip()
        if not is_runnable_translation_profile_provider(provider):
            raise ValueError("Only OpenAI API translation profiles can be created right now.")
        api_key = str(api_key or "").strip()
        model = " ".join(str(model or "").split()).strip()
        if not api_key:
            raise ValueError("OpenAI API key is required.")
        if not model:
            raise ValueError("Model is required.")
        profile_id = str(uuid.uuid4())[:8]
        now = utc_now()
        self.db.create_translation_profile({
            "id": profile_id,
            "name": name.strip() or f"{provider}-{profile_id}",
            "provider": provider,
            "api_key_ref": api_key,
            "model": model,
            "is_default": 0,
            "created_at": now,
            "updated_at": now,
        })
        return self.db.get_translation_profile(profile_id) or {}

    def update_translation_profile(
        self,
        profile_id: str,
        **fields: Any,
    ) -> dict[str, Any]:
        profile = self.db.get_translation_profile(profile_id)
        if profile is None:
            raise FileNotFoundError("Translation profile not found.")
        allowed = {"name", "provider", "api_key_ref", "model", "is_default"}
        filtered = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if filtered:
            effective_provider = str(filtered.get("provider") or profile.get("provider") or "").strip()
            if (
                any(key in filtered for key in ("provider", "api_key_ref", "model"))
                and not is_runnable_translation_profile_provider(effective_provider)
            ):
                raise ValueError("Only OpenAI API translation profiles can be edited in the current setup flow.")
            if "name" in filtered:
                filtered["name"] = str(filtered["name"]).strip() or profile["name"]
            if "provider" in filtered:
                filtered["provider"] = effective_provider
            if "api_key_ref" in filtered:
                filtered["api_key_ref"] = str(filtered["api_key_ref"] or "").strip()
                if not filtered["api_key_ref"]:
                    filtered.pop("api_key_ref")
            if "model" in filtered:
                filtered["model"] = " ".join(str(filtered["model"] or "").split()).strip()
                if effective_provider == "openai" and not filtered["model"]:
                    raise ValueError("Model is required.")
            self.db.update_translation_profile(profile_id, **filtered)
        return self.db.get_translation_profile(profile_id) or {}

    def delete_translation_profile(self, profile_id: str) -> dict[str, Any]:
        profile = self.db.get_translation_profile(profile_id)
        if profile is None:
            raise FileNotFoundError("Translation profile not found.")
        self.db.delete_translation_profile(profile_id)
        return {"deleted": True, "id": profile_id}

    # ── voice profile management (full CRUD) ─────────────────────────

    def create_voice_profile(
        self,
        *,
        name: str,
        audio_bytes: bytes,
        audio_filename: str,
        language_code: str = "",
    ) -> dict[str, Any]:
        from .config import TTS_PROFILES_DIR
        from .tts.constants import LATENT_PRIORITY

        profile_id = str(uuid.uuid4())[:8]
        now = utc_now()

        TTS_PROFILES_DIR.mkdir(parents=True, exist_ok=True)
        ext = Path(audio_filename).suffix or ".wav"
        audio_file = f"{profile_id}{ext}"
        audio_path = TTS_PROFILES_DIR / audio_file
        audio_path.write_bytes(audio_bytes)

        # Trim to 45 seconds via ffmpeg (best-effort)
        self._trim_audio_if_needed(audio_path)

        self.db.create_voice_profile({
            "id": profile_id,
            "name": name.strip() or f"Profile-{profile_id}",
            "language_code": str(language_code or "").strip(),
            "audio_file": audio_file,
            "audio_path": str(audio_path),
            "latents_path": None,
            "has_latents": 0,
            "tts_config_json": serialize_voice_tts_config({"preset": DEFAULT_VOICE_TTS_PRESET}),
            "created_at": now,
            "updated_at": now,
        })

        job_id: str | None = None
        runtime_warning: str | None = None
        runtime = self.tts_manager.get_runtime_status()
        if runtime.available:
            if runtime.device == "cpu":
                runtime_warning = self._tts_cpu_runtime_warning()
            try:
                self.tts_manager.ensure_worker_ready(intent="interactive")
            except RuntimeError as exc:
                runtime_warning = str(exc)
            else:
                # Queue latent precompute only when XTTS runtime is actually available.
                job_id = self.tts_manager.submit_tts_job(
                    job_type="latent_precompute",
                    profile_id=profile_id,
                    payload={"profile_id": profile_id, "audio_path": str(audio_path)},
                    queue_priority=LATENT_PRIORITY,
                )
        else:
            runtime_warning = runtime.error

        profile = self._enrich_voice_profile(self.db.get_voice_profile(profile_id)) or {}
        profile["latent_job_id"] = job_id
        if runtime_warning:
            profile["runtime_warning"] = runtime_warning
        return profile

    def update_voice_profile(self, profile_id: str, **fields: Any) -> dict[str, Any]:
        profile = self.db.get_voice_profile(profile_id)
        if profile is None:
            raise FileNotFoundError("Voice profile not found.")
        allowed = {"name", "language_code"}
        filtered = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if fields.get("tts_config") is not None:
            filtered["tts_config_json"] = serialize_voice_tts_config(fields.get("tts_config"))
        if filtered:
            self.db.update_voice_profile(profile_id, **filtered)
        return self._enrich_voice_profile(self.db.get_voice_profile(profile_id)) or {}

    def delete_voice_profile(self, profile_id: str) -> dict[str, Any]:
        from .config import TTS_PROFILES_DIR

        profile = self.db.get_voice_profile(profile_id)
        if profile is None:
            raise FileNotFoundError("Voice profile not found.")

        # Remove audio file
        audio_path = profile.get("audio_path")
        if audio_path and Path(audio_path).exists():
            Path(audio_path).unlink(missing_ok=True)

        # Remove latents cache
        latents_path = TTS_PROFILES_DIR / f"{profile_id}_latents.pt"
        latents_path.unlink(missing_ok=True)

        self.db.delete_voice_profile(profile_id)
        return {"deleted": True, "id": profile_id}

    @staticmethod
    def _trim_audio_if_needed(audio_path: Path, max_seconds: int = 45) -> None:
        """Trim audio file to *max_seconds* using ffmpeg (best-effort)."""
        import subprocess
        try:
            result = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", str(audio_path)],
                capture_output=True, text=True, timeout=10,
            )
            duration = float(result.stdout.strip())
            if duration <= max_seconds:
                return

            trimmed = audio_path.with_suffix(".trimmed.wav")
            subprocess.run(
                ["ffmpeg", "-y", "-i", str(audio_path), "-t", str(max_seconds),
                 "-ar", "24000", "-ac", "1", str(trimmed)],
                capture_output=True, timeout=30,
            )
            if trimmed.exists() and trimmed.stat().st_size > 44:
                trimmed.replace(audio_path)
        except (FileNotFoundError, subprocess.TimeoutExpired, ValueError):
            pass  # ffmpeg not available or failed — skip trimming

    @staticmethod
    def _default_voice_test_text(profile_name: str) -> str:
        name = (profile_name or "this voice").strip() or "this voice"
        return (
            f"This is {name}. I am ready for the TTS workflow. "
            "Here is a calm line, a brighter line, and a softer ending so you can hear my range."
        )

    # ── TTS execution ────────────────────────────────────────────────

    def submit_voice_test(
        self,
        profile_id: str,
        text: str | None = None,
        language: str = "en",
    ) -> dict[str, Any]:
        """Submit a quick voice test TTS job."""
        from .tts.constants import TEST_VOICE_PRIORITY, map_language_code

        profile = self.db.get_voice_profile(profile_id)
        if profile is None:
            raise FileNotFoundError("Voice profile not found.")

        resolved_text = str(text or "").strip() or self._default_voice_test_text(str(profile.get("name") or ""))
        xtts_lang = map_language_code(str(language or "").strip() or "en")
        payload = self._build_voice_test_payload(
            profile=profile,
            text=resolved_text,
            language=xtts_lang,
        )

        self.tts_manager.ensure_worker_ready(intent="interactive")
        job_id = self.tts_manager.submit_tts_job(
            job_type="test_voice",
            profile_id=profile_id,
            payload=payload,
            queue_priority=TEST_VOICE_PRIORITY,
        )
        return {
            "job_id": job_id,
            "status": "queued",
            "text": resolved_text,
            "language": xtts_lang,
            "tts_config": payload["tts_config"],
        }

    def get_tts_job_status(self, job_id: str) -> dict[str, Any]:
        result = self.tts_manager.get_job_status(job_id)
        if result is None:
            raise FileNotFoundError("TTS job not found.")
        return result

    def pause_tts_job(self, job_id: str) -> dict[str, Any]:
        if not self.tts_manager.set_job_control(job_id, "pause"):
            raise FileNotFoundError("TTS job not found.")
        return {"job_id": job_id, "control_action": "pause"}

    def resume_tts_job(self, job_id: str) -> dict[str, Any]:
        job = self.db.get_tts_job(job_id)
        if job is None:
            raise FileNotFoundError("TTS job not found.")
        self.db.update_tts_job(job_id, status="queued", control_action=None)
        return {"job_id": job_id, "status": "queued"}

    def stop_tts_job(self, job_id: str) -> dict[str, Any]:
        if not self.tts_manager.set_job_control(job_id, "stop"):
            raise FileNotFoundError("TTS job not found.")
        return {"job_id": job_id, "control_action": "stop"}

    # ── Niche project + Episode pipeline ────────────────────────────────

    def create_niche_project(
        self,
        *,
        name: str,
        master_language: str = "en",
        configured_languages: list[str] | None = None,
        language_voice_profiles: dict[str, str] | None = None,
        language_translation_profiles: dict[str, str] | None = None,
        scene_planning_provider: str = "claude",
        visual_bible_provider: str = "claude",
        video_prompt_provider: str = "codex",
        image_prompt_provider: str = "codex",
        scene_planning_model: str = "haiku",
        visual_bible_model: str = "haiku",
        video_prompt_model: str = "gpt-5.4",
        image_prompt_model: str = "gpt-5.4",
        leading_video_scene_count: int = 20,
    ) -> dict[str, Any]:
        now = utc_now()
        project_id = f"niche-{make_job_id(name)}"
        langs = configured_languages or [master_language]
        if master_language not in langs:
            langs = [master_language] + langs
        workspace = EPISODES_ROOT / project_id
        ensure_dir(workspace)
        self.db.create_niche_project({
            "id": project_id,
            "title": name,
            "master_language": master_language,
            "configured_languages": json.dumps(langs),
            "language_voice_profiles": json.dumps(language_voice_profiles or {}),
            "language_translation_profiles": json.dumps(language_translation_profiles or {}),
            "board_status": "Draft",
            "workspace_dir": str(workspace),
            "scene_planning_provider": scene_planning_provider,
            "visual_bible_provider": visual_bible_provider,
            "video_prompt_provider": video_prompt_provider,
            "image_prompt_provider": image_prompt_provider,
            "scene_planning_model": scene_planning_model,
            "visual_bible_model": visual_bible_model,
            "video_prompt_model": video_prompt_model,
            "image_prompt_model": image_prompt_model,
            "leading_video_scene_count": leading_video_scene_count,
            "created_at": now,
            "updated_at": now,
        })
        return {"project": self.db.get_niche_project(project_id)}

    def list_niche_projects(self) -> list[dict[str, Any]]:
        projects = self.db.list_niche_projects()
        for p in projects:
            hydrated = self._hydrate_project_record(p) or {}
            p.update(hydrated)
            p["episode_count"] = len(self.db.list_episodes(p["id"]))
        return projects

    def get_niche_project_detail(self, project_id: str) -> dict[str, Any]:
        project = self._hydrate_project_record(self.db.get_niche_project(project_id))
        if project is None:
            raise FileNotFoundError("Niche project not found.")
        configured_langs = project.get("configured_languages") or []
        episodes = self.db.list_episodes(project_id)
        provider_health = self._provider_health()
        voice_profiles = {
            profile["id"]: profile
            for profile in self.list_voice_profiles()
        }
        translation_profiles = {
            profile["id"]: profile
            for profile in self.list_translation_profiles()
        }
        worker_health = self.get_worker_health()

        # Attach per-episode language statuses
        hydrated_episodes: list[dict[str, Any]] = []
        for ep in episodes:
            hydrated = self._decorate_episode_for_client(
                ep,
                project=project,
                provider_health=provider_health,
                voice_profiles=voice_profiles,
                translation_profiles=translation_profiles,
                worker_health=worker_health,
            )
            hydrated["language_statuses"] = self.db.get_episode_language_statuses(ep["id"])
            hydrated_episodes.append(hydrated)

        # Compute statistics
        by_status: dict[str, int] = {}
        for ep in hydrated_episodes:
            ps = ep.get("pipeline_status") or "idle"
            by_status[ps] = by_status.get(ps, 0) + 1
        done_count = by_status.get("done", 0)
        total = len(hydrated_episodes)
        statistics = {
            "total_episodes": total,
            "by_status": by_status,
            "languages_configured": len(configured_langs),
            "completion_rate": round((done_count / total) * 100) if total > 0 else 0,
        }

        # Include profiles for dropdowns
        voice_profile_list = self.list_voice_profiles()
        translation_profile_list = self.list_translation_profiles_public()
        project["queue_readiness"] = self._build_queue_readiness(
            project=project,
            provider_health=provider_health,
            voice_profiles=voice_profiles,
            translation_profiles=translation_profiles,
            worker_health=worker_health,
        )

        return {
            "project": project,
            "episodes": hydrated_episodes,
            "statistics": statistics,
            "voice_profiles": voice_profile_list,
            "translation_profiles": translation_profile_list,
        }

    def update_niche_project(
        self,
        project_id: str,
        **fields: Any,
    ) -> dict[str, Any]:
        project = self.db.get_niche_project(project_id)
        if project is None:
            raise FileNotFoundError("Niche project not found.")
        if "configured_languages" in fields and fields["configured_languages"] is not None:
            normalized_languages: list[str] = []
            for language_code in fields["configured_languages"]:
                normalized = str(language_code or "").strip()
                if normalized and normalized not in normalized_languages:
                    normalized_languages.append(normalized)
            master_language = str(project.get("master_language") or "en").strip() or "en"
            if master_language not in normalized_languages:
                normalized_languages.insert(0, master_language)
            fields["configured_languages"] = normalized_languages

        # Serialize JSON fields
        for key in ("configured_languages", "language_voice_profiles", "language_translation_profiles", "language_channel_names"):
            if key in fields and not isinstance(fields[key], str):
                fields[key] = json.dumps(fields[key])
        for bool_key in ("channel_replace_prompt", "channel_replace_post"):
            if bool_key in fields and not isinstance(fields[bool_key], (int, str)):
                fields[bool_key] = int(bool(fields[bool_key]))
        fields["updated_at"] = utc_now()
        self.db.update_niche_project(project_id, **fields)
        return {"updated": True}

    def delete_niche_project(self, project_id: str) -> dict[str, Any]:
        project = self.db.get_niche_project(project_id)
        if project is None:
            raise FileNotFoundError("Niche project not found.")
        self.db.delete_niche_project(project_id)
        workspace = Path(project.get("workspace_dir", ""))
        if workspace.exists():
            import shutil
            shutil.rmtree(workspace, ignore_errors=True)
        return {"deleted": True}

    def batch_queue_episodes(self, project_id: str, filter_status: str = "draft") -> dict[str, Any]:
        project = self.db.get_niche_project(project_id)
        if project is None:
            raise FileNotFoundError("Niche project not found.")
        episodes = self.db.list_episodes(project_id)
        queued_ids: list[str] = []
        blocked: list[dict[str, Any]] = []
        for ep in episodes:
            ps = ep.get("pipeline_status") or "idle"
            bs = ep.get("board_status") or "Draft"
            if filter_status == "draft" and ps == "idle" and bs == "Draft":
                try:
                    self.queue_episode(ep["id"])
                    queued_ids.append(ep["id"])
                except QueueBlockedError as exc:
                    blocked.append({
                        "episode_id": ep["id"],
                        "title": ep.get("title"),
                        "queue_readiness": exc.queue_readiness,
                    })
            elif filter_status == "failed" and ps == "failed":
                try:
                    self.queue_episode(ep["id"])
                    queued_ids.append(ep["id"])
                except QueueBlockedError as exc:
                    blocked.append({
                        "episode_id": ep["id"],
                        "title": ep.get("title"),
                        "queue_readiness": exc.queue_readiness,
                    })
        return {
            "queued_count": len(queued_ids),
            "episode_ids": queued_ids,
            "blocked_count": len(blocked),
            "blocked": blocked,
        }

    def submit_episode(
        self,
        niche_project_id: str,
        *,
        title: str,
        script_text: str,
    ) -> dict[str, Any]:
        """Submit a script to a niche project. Creates episode + language status rows."""
        project = self.db.get_niche_project(niche_project_id)
        if project is None:
            raise FileNotFoundError("Niche project not found.")

        now = utc_now()
        episode_id = f"ep-{make_job_id(title)}"
        master_lang = project.get("master_language") or "en"
        configured_langs = json.loads(project.get("configured_languages") or "[]")
        if not configured_langs:
            configured_langs = [master_lang]

        workspace = Path(project["workspace_dir"]) / episode_id
        ensure_dir(workspace)

        # Save script to workspace
        script_path = workspace / "script_original.txt"
        write_text(script_path, script_text)
        spoken_script_path = workspace / "script_original_spoken.txt"
        write_text(spoken_script_path, build_spoken_script(script_text, master_lang))

        self.db.create_episode({
            "id": episode_id,
            "niche_project_id": niche_project_id,
            "title": title,
            "script_text": script_text,
            "board_status": "Draft",
            "pipeline_status": "idle",
            "current_stage": "draft",
            "queued_from_stage": "consistency_guide",
            "master_language": master_lang,
            "configured_languages": json.dumps(configured_langs),
            "workspace_dir": str(workspace),
            "created_at": now,
            "updated_at": now,
        })

        # Create per-language status rows
        for lang in configured_langs:
            lang_id = f"{episode_id}-{lang}"
            self.db.create_episode_language_status({
                "id": lang_id,
                "episode_id": episode_id,
                "language_code": lang,
                "translation_status": "pending" if lang != master_lang else "done",
                "tts_status": "pending",
                "srt_status": "pending",
                "timeline_status": "pending",
                "script_path": str(script_path) if lang == master_lang else None,
                "spoken_script_path": str(spoken_script_path) if lang == master_lang else None,
                "updated_at": now,
            })

        episode = self._decorate_episode_for_client(
            self.db.get_episode(episode_id) or {},
            project=self._hydrate_project_record(project),
        )
        return {"episode": episode}

    def get_episode_detail(self, episode_id: str) -> dict[str, Any]:
        episode = self.db.get_episode(episode_id)
        if episode is None:
            raise FileNotFoundError("Episode not found.")
        worker_health = self.get_worker_health()
        lang_statuses, active_tts_job = self._decorate_language_statuses_with_tts(
            self.db.get_episode_language_statuses(episode_id),
            worker_health=worker_health,
        )
        stage_runs = [
            self._decorate_stage_run_for_client(run)
            for run in self.db.list_stage_runs(episode_id)
        ]
        project = self._hydrate_project_record(self.db.get_niche_project(episode["niche_project_id"]))
        provider_health = self._provider_health()
        voice_profiles = {
            profile["id"]: profile
            for profile in self.list_voice_profiles()
        }
        translation_profiles = {
            profile["id"]: profile
            for profile in self.list_translation_profiles()
        }
        hydrated_episode = self._decorate_episode_for_client(
            episode,
            project=project,
            provider_health=provider_health,
            voice_profiles=voice_profiles,
            translation_profiles=translation_profiles,
            worker_health=worker_health,
            active_tts_job=active_tts_job,
        )

        return {
            "episode": hydrated_episode,
            "language_statuses": lang_statuses,
            "stage_runs": stage_runs,
            "worker_health": worker_health,
        }

    def queue_episode(
        self,
        episode_id: str,
        start_stage: str | None = None,
        *,
        reset_outputs: bool = False,
    ) -> dict[str, Any]:
        episode = self.db.get_episode(episode_id)
        if episode is None:
            raise FileNotFoundError("Episode not found.")
        episode = self._hydrate_episode_record(episode) or {}
        if str(episode.get("pipeline_status") or "").lower() in {"queued", "running", "paused_for_tts"}:
            raise ValueError("Episode workflow is already active. Pause it or wait for it to finish.")
        stage = start_stage or self._default_episode_start_stage(episode)
        if stage not in EPISODE_RUNNABLE_STAGES:
            raise ValueError(f"Invalid start stage: {stage}")
        project = self._hydrate_project_record(self.db.get_niche_project(episode["niche_project_id"]))
        queue_readiness = self._build_queue_readiness(
            project=project,
            episode=episode,
        )
        filtered_blockers = [
            blocker
            for blocker in (queue_readiness.get("blockers") or [])
            if blocker.get("code") not in QUEUE_STAGE_PROVIDER_BLOCKER_CODES
            or blocker.get("stage") in {None, "", stage}
        ]
        queue_readiness = {
            **queue_readiness,
            "blockers": filtered_blockers,
            "ok": not filtered_blockers,
        }
        stage_blockers = self._build_start_stage_blockers(episode, stage)
        if stage_blockers:
            queue_readiness = {
                **queue_readiness,
                "ok": False,
                "blockers": [*(queue_readiness.get("blockers") or []), *stage_blockers],
            }
        if not queue_readiness["ok"]:
            raise QueueBlockedError(
                episode_id=episode_id,
                start_stage=stage,
                queue_readiness=queue_readiness,
            )
        if reset_outputs:
            self._reset_episode_outputs_from_stage(episode_id, stage)
        # Clear old stage runs from the start stage onward so the UI
        # only shows runs from the current attempt.
        start_idx = EPISODE_RUNNABLE_STAGES.index(stage)
        stages_to_clear = list(EPISODE_RUNNABLE_STAGES[start_idx:])
        self.db.delete_stage_runs_for(episode_id, stages_to_clear)
        self.db.update_episode(
            episode_id,
            board_status="Queued",
            pipeline_status="queued",
            current_stage=stage,
            queued_from_stage=stage,
            pause_requested=0,
            last_error=None,
            updated_at=utc_now(),
        )
        with self._condition:
            self._condition.notify()
        return {"queued": True, "start_stage": stage, "reset_outputs": bool(reset_outputs)}

    def pause_episode(self, episode_id: str) -> dict[str, Any]:
        episode = self.db.get_episode(episode_id)
        if episode is None:
            raise FileNotFoundError("Episode not found.")
        episode = self._hydrate_episode_record(episode) or {}
        pipeline_status = str(episode.get("pipeline_status") or "idle").lower()
        resume_stage = self._default_episode_start_stage(episode)

        if pipeline_status == "queued":
            self.db.update_episode(
                episode_id,
                board_status="Paused",
                pipeline_status="paused",
                current_stage=resume_stage,
                queued_from_stage=resume_stage,
                pause_requested=0,
                updated_at=utc_now(),
            )
            return {
                "paused": True,
                "pause_requested": False,
                "resume_stage": resume_stage,
                "message": f"Workflow paused before {resume_stage}.",
            }

        if pipeline_status in {"running", "paused_for_tts"}:
            self.db.update_episode(
                episode_id,
                pause_requested=1,
                updated_at=utc_now(),
            )
            return {
                "paused": False,
                "pause_requested": True,
                "resume_stage": self._next_runnable_stage(str(episode.get("current_stage") or "")) or resume_stage,
                "message": "Pause requested. The workflow will stop at the next safe boundary.",
            }

        if pipeline_status == "paused":
            return {
                "paused": True,
                "pause_requested": False,
                "resume_stage": resume_stage,
                "message": f"Workflow already paused before {resume_stage}.",
            }

        raise ValueError("Episode workflow is not active.")

    def list_all_episodes_for_board(self) -> list[dict[str, Any]]:
        """Return all episodes with niche project title and per-language progress."""
        episodes = self.db.list_all_episodes_for_board()
        provider_health = self._provider_health()
        voice_profiles = {
            profile["id"]: profile
            for profile in self.list_voice_profiles()
        }
        translation_profiles = {
            profile["id"]: profile
            for profile in self.list_translation_profiles()
        }
        worker_health = self.get_worker_health()
        project_cache: dict[str, dict[str, Any] | None] = {}
        for ep in episodes:
            ep["configured_languages"] = self._parse_json_list(ep.get("configured_languages"))
            lang_statuses, active_tts_job = self._decorate_language_statuses_with_tts(
                self.db.get_episode_language_statuses(ep["id"]),
                worker_health=worker_health,
            )
            ep["language_statuses"] = lang_statuses
            project_id = ep.get("niche_project_id")
            if project_id not in project_cache:
                project_cache[project_id] = self._hydrate_project_record(self.db.get_niche_project(project_id))
            decorated = self._decorate_episode_for_client(
                ep,
                project=project_cache[project_id],
                provider_health=provider_health,
                voice_profiles=voice_profiles,
                translation_profiles=translation_profiles,
                worker_health=worker_health,
                active_tts_job=active_tts_job,
            )
            ep.update(decorated)
        return episodes

    def retry_episode_language(
        self,
        episode_id: str,
        language_code: str,
        stage: str,
    ) -> dict[str, Any]:
        """Retry a specific stage for a single language without re-running the whole pipeline."""
        episode = self.db.get_episode(episode_id)
        if episode is None:
            raise FileNotFoundError("Episode not found.")
        if episode.get("pipeline_status") == "running":
            raise ValueError("Episode pipeline is currently running.")

        lang_status = self.db.get_episode_language_status(episode_id, language_code)
        if lang_status is None:
            raise FileNotFoundError(f"No language status for '{language_code}'.")

        stage = {
            "srt": "alignment",
            "timeline": "timeline_mapping",
        }.get(stage, stage)

        if stage == "translation":
            self.db.update_episode_language_status(
                episode_id, language_code,
                translation_status="pending", script_path=None, spoken_script_path=None, error_message=None,
            )
            self._episode_retry_single_translation(episode_id, language_code)
        elif stage == "tts":
            self.db.update_episode_language_status(
                episode_id, language_code,
                tts_status="pending", error_message=None,
            )
            self._episode_retry_single_tts(episode_id, language_code)
        elif stage == "alignment":
            self.db.update_episode_language_status(
                episode_id,
                language_code,
                srt_status="pending",
                srt_path=None,
                timeline_status="pending",
                timeline_path=None,
                error_message=None,
            )
            self._episode_retry_single_alignment(episode_id, language_code)
            refreshed_status = self.db.get_episode_language_status(episode_id, language_code)
            if (
                refreshed_status
                and refreshed_status.get("srt_status") == "done"
                and self._timeline_mapping_ready(episode_id)
            ):
                self._episode_retry_single_timeline_mapping(episode_id, language_code)
        elif stage == "timeline_mapping":
            self.db.update_episode_language_status(
                episode_id,
                language_code,
                timeline_status="pending",
                timeline_path=None,
                error_message=None,
            )
            self._episode_retry_single_timeline_mapping(episode_id, language_code)
        else:
            raise ValueError(f"Retry not supported for stage '{stage}'.")

        return {"retried": True, "language_code": language_code, "stage": stage}

    def _episode_retry_single_translation(self, episode_id: str, lang: str) -> None:
        """Retry translation for a single language."""
        from .translation import TranslationService

        episode = self.db.get_episode(episode_id)
        project = self.db.get_niche_project(episode["niche_project_id"])
        translation_profiles = json.loads(project.get("language_translation_profiles") or "{}")
        source_channel_name = str(project.get("source_channel_name") or "").strip()
        language_channel_names = json.loads(project.get("language_channel_names") or "{}")
        enable_prompt = bool(int(project.get("channel_replace_prompt", 1) or 1))
        enable_post = bool(int(project.get("channel_replace_post", 1) or 1))
        settings = self._global_settings()
        workspace = self._episode_workspace(episode)
        reviewer_api_key = self._stage_provider_openai_api_key()

        profile_id = translation_profiles.get(lang)
        if not profile_id:
            self.db.update_episode_language_status(
                episode_id, lang,
                translation_status="skipped",
                error_message="No translation profile configured",
            )
            return

        profile = self.db.get_translation_profile(profile_id)
        if profile is None:
            self.db.update_episode_language_status(
                episode_id, lang,
                translation_status="skipped",
                error_message=f"Translation profile '{profile_id}' not found",
            )
            return

        self.db.update_episode_language_status(episode_id, lang, translation_status="running")
        try:
            master_scenes = None
            if episode.get("timeline_draft_path") and Path(episode["timeline_draft_path"]).exists():
                master_scenes = read_json(Path(episode["timeline_draft_path"]), default=[])
            target_channel = language_channel_names.get(lang, "")
            svc = TranslationService()
            result = asyncio.run(svc.translate_script(
                source_script=episode["script_text"],
                source_lang=episode["master_language"],
                target_lang=lang,
                provider=profile["provider"],
                api_key=profile["api_key_ref"],
                model=profile["model"],
                master_scenes=master_scenes,
                max_words_per_chunk=settings.get("translation_chunk_max_words", 800),
                context_tail_words=settings.get("translation_context_tail_words", 200),
                source_channel_name=source_channel_name if enable_prompt else "",
                target_channel_name=target_channel if enable_prompt else "",
                reviewer_required=True,
                reviewer_api_key=reviewer_api_key,
                reviewer_model="gpt-5.4-mini",
            ))
            write_json(workspace / f"translation_log_{lang}.json", [
                {
                    "chunk_index": cr.chunk_index,
                    "scene_ids": cr.scene_ids,
                    "words_in": cr.words_in,
                    "words_out": cr.words_out,
                    "status": cr.status,
                    "error": cr.error,
                }
                for cr in result.chunk_results
            ])
            translated_script = self._validated_translation_script(
                result,
                language_code=lang,
                source_script=episode["script_text"],
                source_channel_name=source_channel_name if enable_post else "",
                target_channel_name=target_channel if enable_post else "",
            )
            translated_path, spoken_path = self._write_language_script_assets(
                workspace=workspace,
                language_code=lang,
                master_language=str(episode.get("master_language") or "en").strip() or "en",
                readable_script=translated_script,
            )
            review_report_raw = getattr(result, "review_report", None)
            review_report = review_report_raw if isinstance(review_report_raw, dict) else None
            if review_report:
                write_json(workspace / f"translation_review_{lang}.json", review_report)
            self.db.update_episode_language_status(
                episode_id, lang,
                translation_status="done",
                script_path=str(translated_path),
                spoken_script_path=str(spoken_path),
                error_message=None,
            )
        except Exception as exc:
            self.db.update_episode_language_status(
                episode_id, lang,
                translation_status="failed",
                script_path=None,
                spoken_script_path=None,
                error_message=str(exc)[:500],
            )

    def _episode_retry_single_tts(self, episode_id: str, lang: str) -> None:
        """Retry TTS for a single language by submitting a new TTS job."""
        episode = self.db.get_episode(episode_id)
        project = self.db.get_niche_project(episode["niche_project_id"])
        voice_profiles = json.loads(project.get("language_voice_profiles") or "{}")

        profile_id = voice_profiles.get(lang)
        if not profile_id:
            self.db.update_episode_language_status(
                episode_id, lang, tts_status="skipped",
                error_message="No voice profile configured",
            )
            return
        profile = self.db.get_voice_profile(profile_id)
        if profile is None:
            self.db.update_episode_language_status(
                episode_id, lang, tts_status="skipped",
                error_message=f"Voice profile '{profile_id}' not found",
            )
            return

        lang_status = self.db.get_episode_language_status(episode_id, lang)
        script_path = self._preferred_audio_script_path(
            episode=episode,
            language_code=lang,
            language_status=lang_status,
        )
        if script_path and script_path.exists():
            script_text = read_text(script_path)
        else:
            script_text = str(episode.get("script_text") or "")

        tts_mgr = self.tts_manager
        tts_mgr.ensure_worker_ready(intent="pipeline")
        payload = self._build_generate_tts_payload(
            profile=profile,
            language=lang,
            script_text=script_text,
        )
        job_id = tts_mgr.submit_tts_job(
            job_type="generate",
            profile_id=profile_id,
            payload=payload,
            build_id=episode_id,
            filename=f"narration_{lang}.wav",
        )
        self.db.update_episode_language_status(
            episode_id,
            lang,
            tts_status="queued",
            tts_job_id=job_id,
            tts_audio_path=None,
            error_message=None,
        )

    def _episode_retry_single_alignment(self, episode_id: str, lang: str) -> None:
        """Retry subtitle alignment for a single language."""
        episode = self.db.get_episode(episode_id)
        if episode is None:
            raise FileNotFoundError("Episode not found.")
        lang_status = self.db.get_episode_language_status(episode_id, lang)
        if lang_status is None:
            raise FileNotFoundError(f"No language status for '{lang}'.")

        workspace = self._episode_workspace(episode)
        audio_path = lang_status.get("tts_audio_path")
        script_path = self._preferred_audio_script_path(
            episode=episode,
            language_code=lang,
            language_status=lang_status,
        )
        if not audio_path or not Path(audio_path).exists():
            self.db.update_episode_language_status(
                episode_id,
                lang,
                srt_status="skipped",
                srt_path=None,
                error_message="No TTS audio available",
            )
            return
        if script_path is None or not script_path.exists():
            self.db.update_episode_language_status(
                episode_id,
                lang,
                srt_status="skipped",
                srt_path=None,
                error_message="No script available",
            )
            return

        self.db.update_episode_language_status(episode_id, lang, srt_status="running", error_message=None)
        try:
            output_root = ensure_dir(workspace / "alignment" / lang)
            result = run_alignment_job(
                audio_path=Path(audio_path),
                script_path=script_path,
                language_code=lang,
                engine_config=None,
                segmentation_config=None,
                output_root=output_root,
            )
            srt_path = workspace / f"final_{lang}.srt"
            shutil.copy2(result.artifacts.final_srt, srt_path)
            self.db.update_episode_language_status(
                episode_id,
                lang,
                srt_status="done",
                srt_path=str(srt_path),
                error_message=None,
            )
        except Exception as exc:
            self.db.update_episode_language_status(
                episode_id,
                lang,
                srt_status="failed",
                srt_path=None,
                error_message=str(exc)[:500],
            )

    def _timeline_mapping_ready(self, episode_id: str) -> bool:
        episode = self.db.get_episode(episode_id)
        if episode is None:
            return False
        timeline_path = episode.get("timeline_draft_path")
        if not timeline_path or not Path(timeline_path).exists():
            return False
        master_status = self.db.get_episode_language_status(episode_id, episode["master_language"])
        master_srt_path = master_status.get("srt_path") if master_status else None
        return bool(master_srt_path and Path(master_srt_path).exists())

    def _load_timeline_mapping_context(
        self,
        episode_id: str,
    ) -> tuple[dict[str, Any], Path, str, list[dict[str, Any]], float]:
        episode = self.db.get_episode(episode_id)
        if episode is None:
            raise FileNotFoundError("Episode not found.")
        workspace = self._episode_workspace(episode)
        master_lang = episode["master_language"]

        timeline_path = episode.get("timeline_draft_path")
        if not timeline_path or not Path(timeline_path).exists():
            raise ValueError("Master timeline draft is missing.")
        master_scenes = read_json(Path(timeline_path), default=[])
        if not master_scenes:
            raise ValueError("Master timeline is empty.")

        master_status = self.db.get_episode_language_status(episode_id, master_lang)
        master_srt_path = master_status.get("srt_path") if master_status else None
        if not master_srt_path or not Path(master_srt_path).exists():
            raise ValueError("Master SRT not available.")
        master_cues = parse_srt_text(Path(master_srt_path).read_text(encoding="utf-8"))
        master_total = master_cues[-1].end_ms / 1000.0 if master_cues else 0.0
        if master_total <= 0:
            raise ValueError("Master SRT has zero duration.")

        return episode, workspace, master_lang, master_scenes, master_total

    def _episode_retry_single_timeline_mapping(
        self,
        episode_id: str,
        lang: str,
        *,
        context: tuple[dict[str, Any], Path, str, list[dict[str, Any]], float] | None = None,
    ) -> str:
        """Retry timeline mapping for a single language."""
        lang_status = self.db.get_episode_language_status(episode_id, lang)
        if lang_status is None:
            raise FileNotFoundError(f"No language status for '{lang}'.")

        try:
            if context is None:
                context = self._load_timeline_mapping_context(episode_id)
            _, workspace, master_lang, master_scenes, master_total = context
            self.db.update_episode_language_status(episode_id, lang, timeline_status="running")

            if lang == master_lang:
                lang_timeline_path = workspace / f"timeline_{lang}.json"
                write_json(lang_timeline_path, master_scenes)
                self.db.update_episode_language_status(
                    episode_id,
                    lang,
                    timeline_status="done",
                    timeline_path=str(lang_timeline_path),
                    error_message=None,
                )
                return "done"

            lang_srt_path = lang_status.get("srt_path")
            if not lang_srt_path or not Path(lang_srt_path).exists():
                self.db.update_episode_language_status(
                    episode_id,
                    lang,
                    timeline_status="skipped",
                    timeline_path=None,
                    error_message=lang_status.get("error_message") or "No SRT available for timing",
                )
                return "skipped"

            lang_cues = parse_srt_text(Path(lang_srt_path).read_text(encoding="utf-8"))
            lang_total = lang_cues[-1].end_ms / 1000.0 if lang_cues else 0.0
            if lang_total <= 0:
                self.db.update_episode_language_status(
                    episode_id,
                    lang,
                    timeline_status="skipped",
                    timeline_path=None,
                    error_message="Zero-duration SRT",
                )
                return "skipped"

            ratio = lang_total / master_total
            lang_boundaries = sorted({c.start_ms / 1000.0 for c in lang_cues} | {c.end_ms / 1000.0 for c in lang_cues})

            def snap_to_boundary(t: float) -> float:
                if not lang_boundaries:
                    return t
                closest = min(lang_boundaries, key=lambda b: abs(b - t))
                if abs(closest - t) <= 0.5:
                    return closest
                return round(t, 3)

            lang_scenes: list[dict[str, Any]] = []
            for i, scene in enumerate(master_scenes):
                mapped = dict(scene)
                raw_start = scene["start"] * ratio
                raw_end = scene["end"] * ratio

                mapped_start = snap_to_boundary(raw_start)
                mapped_end = snap_to_boundary(raw_end)

                if lang_scenes and mapped_start < lang_scenes[-1]["end"]:
                    mapped_start = lang_scenes[-1]["end"]
                if mapped_end - mapped_start < 1.0:
                    mapped_end = mapped_start + 1.0
                if i == len(master_scenes) - 1:
                    mapped_end = lang_total

                mapped["start"] = round(mapped_start, 3)
                mapped["end"] = round(mapped_end, 3)
                mapped["duration"] = round(mapped_end - mapped_start, 3)
                lang_scenes.append(mapped)

            lang_timeline_path = workspace / f"timeline_{lang}.json"
            write_json(lang_timeline_path, lang_scenes)
            self.db.update_episode_language_status(
                episode_id,
                lang,
                timeline_status="done",
                timeline_path=str(lang_timeline_path),
                error_message=None,
            )
            return "done"
        except Exception as exc:
            self.db.update_episode_language_status(
                episode_id,
                lang,
                timeline_status="failed",
                timeline_path=None,
                error_message=str(exc)[:500],
            )
            return "failed"

    def get_translation_preview(self, episode_id: str, language_code: str) -> dict[str, Any]:
        """Return original and translated script side-by-side for preview."""
        episode = self.db.get_episode(episode_id)
        if episode is None:
            raise FileNotFoundError("Episode not found.")
        lang_status = self.db.get_episode_language_status(episode_id, language_code)
        if lang_status is None:
            raise FileNotFoundError(f"No language status for '{language_code}'.")

        original = episode["script_text"] or ""
        translated = ""
        script_path = lang_status.get("script_path")
        if script_path and Path(script_path).exists():
            translated = read_text(Path(script_path))

        # Load translation log if available
        workspace = self._episode_workspace(episode)
        log_path = workspace / f"translation_log_{language_code}.json"
        translation_log = read_json(log_path, default=[]) if log_path.exists() else []

        return {
            "language_code": language_code,
            "original": original,
            "translated": translated,
            "translation_log": translation_log,
            "translation_status": lang_status.get("translation_status", "pending"),
        }

    def get_worker_health(self) -> dict[str, Any]:
        """Return TTS worker health status."""
        health = self.tts_manager.get_worker_health()
        return {
            "running": health.running,
            "status": health.status,
            "lifecycle_state": health.lifecycle_state,
            "worker_id": health.worker_id,
            "current_job_id": health.current_job_id,
            "last_heartbeat": health.last_heartbeat,
            "is_stale": health.is_stale,
            "pid": health.pid,
            "startup_error": health.startup_error,
            "missing_dependencies": list(health.missing_dependencies or []),
            "device": health.device,
            "torch_version": health.torch_version,
            "torch_build": health.torch_build,
            "cuda_available": health.cuda_available,
            "gpu_name": health.gpu_name,
            "active_generate_jobs": health.active_generate_jobs,
            "queued_generate_jobs": health.queued_generate_jobs,
        }

    def get_app_runtime(self) -> dict[str, Any]:
        runtime = get_runtime_info()
        url = runtime_url_from_info(runtime)
        mode = str(runtime.get("mode") or "server").strip().lower() or "server"
        try:
            pid = int(runtime.get("pid") or os.getpid())
        except (TypeError, ValueError):
            pid = os.getpid()
        try:
            port = int(runtime.get("port") or 0)
        except (TypeError, ValueError):
            port = 0
        close_copy = (
            "Closing the Creator Studio window stops the dashboard and the voice engine."
            if runtime.get("window_controls_shutdown")
            else "This session is running as a browser-served page, so closing the tab does not stop the backend."
        )
        return {
            "pid": pid,
            "host": str(runtime.get("host") or "").strip() or "127.0.0.1",
            "port": port,
            "url": url,
            "mode": mode,
            "started_at": str(runtime.get("started_at") or ""),
            "single_instance": True,
            "window_controls_shutdown": bool(runtime.get("window_controls_shutdown")),
            "close_copy": close_copy,
        }

    def get_review_data(self, episode_id: str) -> dict[str, Any]:
        """Fetch all reviewable data for an episode: timeline, guide, prompts, per-lang timelines."""
        episode = self.db.get_episode(episode_id)
        if episode is None:
            raise FileNotFoundError("Episode not found.")

        # Consistency guide
        guide_path = episode.get("consistency_guide_path")
        consistency_guide = read_json(Path(guide_path), default={}) if guide_path and Path(guide_path).exists() else {}

        # Master timeline
        timeline_path = episode.get("timeline_draft_path")
        timeline_draft = read_json(Path(timeline_path), default=[]) if timeline_path and Path(timeline_path).exists() else []

        # Timeline validation
        validation_path = episode.get("timeline_validation_path")
        timeline_validation = read_json(Path(validation_path), default={}) if validation_path and Path(validation_path).exists() else {}

        # Prompts
        prompt_path = episode.get("prompt_list_draft_path")
        prompt_list = read_text(Path(prompt_path)) if prompt_path and Path(prompt_path).exists() else ""
        blueprint_path = episode.get("prompt_blueprint_path")
        prompt_blueprints = read_jsonl(Path(blueprint_path)) if blueprint_path and Path(blueprint_path).exists() else []

        # Per-language timelines
        lang_statuses = self.db.get_episode_language_statuses(episode_id)
        per_lang_timelines: dict[str, list] = {}
        for ls in lang_statuses:
            tl_path = ls.get("timeline_path")
            if tl_path and Path(tl_path).exists():
                per_lang_timelines[ls["language_code"]] = read_json(Path(tl_path), default=[])

        return {
            "episode_id": episode_id,
            "pipeline_status": episode.get("pipeline_status"),
            "consistency_guide": consistency_guide,
            "timeline_draft": timeline_draft,
            "timeline_validation": timeline_validation,
            "prompt_list": prompt_list,
            "prompt_blueprints": prompt_blueprints,
            "per_language_timelines": per_lang_timelines,
        }

    def update_review_data(
        self,
        episode_id: str,
        *,
        consistency_guide: dict | None = None,
        timeline_draft: list | None = None,
        prompt_list: str | None = None,
    ) -> dict[str, Any]:
        """Update reviewable artifacts: consistency guide, timeline, or prompt list."""
        episode = self.db.get_episode(episode_id)
        if episode is None:
            raise FileNotFoundError("Episode not found.")
        workspace = self._episode_workspace(episode)

        updated = []
        if consistency_guide is not None:
            path = workspace / "consistency_guide.json"
            write_json(path, consistency_guide)
            self.db.update_episode(episode_id, consistency_guide_path=str(path))
            updated.append("consistency_guide")

        if timeline_draft is not None:
            path = workspace / "timeline_draft.json"
            write_json(path, timeline_draft)
            self.db.update_episode(episode_id, timeline_draft_path=str(path))
            updated.append("timeline_draft")

        if prompt_list is not None:
            path = workspace / "prompt_list_draft.txt"
            write_text(path, prompt_list)
            self.db.update_episode(episode_id, prompt_list_draft_path=str(path))
            updated.append("prompt_list")

        self.db.update_episode(episode_id, updated_at=utc_now())
        return {"updated": updated}

    def finalize_export(self, episode_id: str) -> dict[str, Any]:
        """Package all episode outputs into a zip for Tool 2 handoff."""
        import zipfile

        episode = self.db.get_episode(episode_id)
        if episode is None:
            raise FileNotFoundError("Episode not found.")
        workspace = self._episode_workspace(episode)
        lang_statuses = self.db.get_episode_language_statuses(episode_id)

        zip_name = f"export_{episode_id}.zip"
        zip_path = workspace / zip_name

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            # Shared assets
            shared_files = [
                ("consistency_guide.json", episode.get("consistency_guide_path")),
                ("timeline_draft.json", episode.get("timeline_draft_path")),
                ("master_scenes.json", episode.get("master_scenes_path")),
                ("prompt_list_draft.txt", episode.get("prompt_list_draft_path")),
                ("prompt_blueprint.jsonl", episode.get("prompt_blueprint_path")),
            ]
            # Also include video/image prompt files
            for name in ("video_prompt_list_draft.txt", "image_prompt_list_draft.txt",
                         "video_prompt_blueprints.json", "image_prompt_blueprints.json"):
                fpath = workspace / name
                if fpath.exists():
                    shared_files.append((name, str(fpath)))

            for arc_name, fpath in shared_files:
                if fpath and Path(fpath).exists():
                    zf.write(fpath, f"shared/{arc_name}")

            # Per-language assets
            for ls in lang_statuses:
                lang = ls["language_code"]
                lang_dir = f"languages/{lang}"

                # Script
                if ls.get("script_path") and Path(ls["script_path"]).exists():
                    zf.write(ls["script_path"], f"{lang_dir}/script_{lang}.txt")
                if ls.get("spoken_script_path") and Path(ls["spoken_script_path"]).exists():
                    zf.write(ls["spoken_script_path"], f"{lang_dir}/script_{lang}_spoken.txt")

                # SRT
                if ls.get("srt_path") and Path(ls["srt_path"]).exists():
                    zf.write(ls["srt_path"], f"{lang_dir}/subtitles_{lang}.srt")

                # Audio
                if ls.get("tts_audio_path") and Path(ls["tts_audio_path"]).exists():
                    zf.write(ls["tts_audio_path"], f"{lang_dir}/narration_{lang}.wav")

                # Timeline
                if ls.get("timeline_path") and Path(ls["timeline_path"]).exists():
                    zf.write(ls["timeline_path"], f"{lang_dir}/timeline_{lang}.json")

        # Update episode status
        self.db.update_episode(
            episode_id,
            board_status="Done",
            pipeline_status="done",
            current_stage="export",
            updated_at=utc_now(),
        )

        return {
            "exported": True,
            "zip_path": str(zip_path),
            "zip_size": zip_path.stat().st_size,
        }

    def delete_episode(self, episode_id: str) -> dict[str, Any]:
        episode = self.db.get_episode(episode_id)
        if episode is None:
            raise FileNotFoundError("Episode not found.")
        if episode.get("pipeline_status") == "running":
            raise ValueError("Episode pipeline is running. Wait for it to finish.")
        workspace = Path(episode.get("workspace_dir") or "")
        self.db.delete_episode(episode_id)
        if workspace.exists():
            self._safe_delete_path(workspace, EPISODES_ROOT)
        return {"deleted": True, "episode_id": episode_id}

    # ── Episode pipeline processor ─────────────────────────────────────

    def _pause_after_stage_if_requested(self, episode_id: str, completed_stage: str) -> bool:
        episode = self._hydrate_episode_record(self.db.get_episode(episode_id))
        if episode is None or not episode.get("pause_requested"):
            return False
        next_stage = self._next_runnable_stage(completed_stage)
        if next_stage is None:
            self.db.update_episode(episode_id, pause_requested=0, updated_at=utc_now())
            return False
        self.db.update_episode(
            episode_id,
            board_status="Paused",
            pipeline_status="paused",
            current_stage=next_stage,
            queued_from_stage=next_stage,
            pause_requested=0,
            updated_at=utc_now(),
        )
        return True

    def _process_episode(self, episode: dict[str, Any]) -> None:
        """Process an episode through the unified TTS-first pipeline. All steps sequential."""
        episode_id = episode["id"]
        start_stage = self._default_episode_start_stage(episode)
        self.db.update_episode(
            episode_id,
            board_status="Running",
            pipeline_status="running",
            pause_requested=0,
        )

        stages = EPISODE_RUNNABLE_STAGES
        start_idx = stages.index(start_stage) if start_stage in stages else 0

        try:
            for stage in stages[start_idx:]:
                self.db.update_episode(episode_id, current_stage=stage, updated_at=utc_now())

                if stage == "consistency_guide":
                    self._episode_run_consistency_guide(episode_id)
                elif stage == "translation":
                    self._episode_run_translations(episode_id)
                elif stage == "tts":
                    self._episode_run_tts_all(episode_id)
                    # TTS is async — if paused, return and let _check_paused_tts_episodes resume
                    refreshed = self.db.get_episode(episode_id)
                    if refreshed and refreshed.get("pipeline_status") == "paused_for_tts":
                        return
                elif stage == "alignment":
                    self._episode_run_alignments(episode_id)
                elif stage == "chunking":
                    self._episode_run_chunking(episode_id)
                elif stage == "scene_planning":
                    self._episode_run_scene_planning(episode_id)
                elif stage == "video_prompt_generation":
                    self._episode_run_prompt_stage(episode_id, "video")
                elif stage == "image_prompt_generation":
                    self._episode_run_prompt_stage(episode_id, "image")
                    self._episode_combine_prompts(episode_id)
                    self._episode_save_master_scenes(episode_id)
                elif stage == "timeline_mapping":
                    self._episode_run_timeline_mapping(episode_id)

                if self._pause_after_stage_if_requested(episode_id, stage):
                    return

            # All stages complete
            self.db.update_episode(
                episode_id,
                board_status="Review",
                pipeline_status="review",
                current_stage="review",
                review_ready=1,
                pause_requested=0,
                updated_at=utc_now(),
            )
        except Exception as exc:
            error_text = str(exc)
            if len(error_text) > 500:
                error_text = error_text[:500] + "…"
            self.db.update_episode(
                episode_id,
                board_status="Needs Attention",
                pipeline_status="failed",
                last_error=error_text,
                pause_requested=0,
                updated_at=utc_now(),
            )

    # ── Episode pipeline helpers ────────────────────────────────────────

    def _resolved_episode_config(self, episode: dict[str, Any]) -> dict[str, Any]:
        """Resolve provider/model config for an episode from its niche project."""
        project = self._hydrate_project_record(self.db.get_niche_project(episode["niche_project_id"]))
        if project is None:
            raise FileNotFoundError("Niche project not found.")
        return self._resolved_project_config(project)

    def _episode_workspace(self, episode: dict[str, Any]) -> Path:
        return Path(episode["workspace_dir"])

    # ── Episode stage implementations ────────────────────────────────────

    def _episode_run_consistency_guide(self, episode_id: str) -> None:
        """Run consistency guide generation using the original script via LLM."""
        episode = self.db.get_episode(episode_id)
        workspace = self._episode_workspace(episode)
        config = self._resolved_episode_config(episode)
        provider = config["visual_bible_provider"]
        script_text = episode["script_text"]

        source_script = {
            "script_filename": "script_original.txt",
            "word_count": len(script_text.split()),
            "paragraph_count": len([p for p in script_text.split("\n\n") if p.strip()]),
            "paragraphs": [p.strip() for p in script_text.split("\n\n") if p.strip()],
        }

        template = self.templates.snapshot_template(workspace, VISUAL_BIBLE_STAGE, provider)
        user_prompt = (
            "Create a consistency guide for this video project.\n"
            "Return English JSON only.\n"
            "Use the clean source script as the only source of truth.\n"
            "Focus on the characters, places, recurring visual elements, props, and continuity rules that must stay consistent across the project.\n"
            "Treat the whole project as one continuous cinematic movie with a locked visual language across every image and video.\n"
            "Push the guide toward dramatic, story-driven, feature-film aesthetics rather than documentary, interview, explainer, or editorial imagery.\n"
            "Make the guide explicitly ban split-screen layouts, multi-panel compositions, white borders or margins, and any visible text inside the frame.\n"
            "The guide must help later prompt-writing stages avoid inventing new designs or drifting away from the story.\n\n"
            f"Source script payload:\n{json.dumps(source_script, ensure_ascii=False, indent=2)}"
        )
        artifact_dir = ensure_dir(workspace / "runs" / "consistency_guide")
        schema = visual_bible_output_schema()
        run_id = self._start_structured_stage_run(
            episode_id=episode_id,
            stage="consistency_guide",
            provider=provider,
            template_hash=template.get("hash"),
            workdir=workspace,
            artifact_dir=artifact_dir,
            model=config["visual_bible_model"],
            schema=schema,
        )
        result: dict[str, Any] | None = None
        parsed_output_path: str | None = None
        validation_path: str | None = None
        try:
            result = self.cli_runner.run_structured(
                provider=provider,
                model=config["visual_bible_model"],
                api_key=self._stage_provider_api_key(provider),
                system_prompt=template["body"],
                user_prompt=user_prompt,
                schema=schema,
                workdir=workspace,
                artifact_dir=artifact_dir,
            )
            parsed_output_path = str(write_json(artifact_dir / "parsed.json", result["parsed"]))
            normalized, report = normalize_visual_bible(result["parsed"])
            validation_path = str(write_json(workspace / "consistency_guide_validation.json", report))
            if report["errors"]:
                raise ValueError("; ".join(report["errors"]))
            guide_path = write_json(workspace / "consistency_guide.json", normalized)
            self.db.update_episode(episode_id, consistency_guide_path=str(guide_path), updated_at=utc_now())
            self.db.finish_stage_run(
                run_id,
                status="completed",
                exit_code=0,
                parsed_output_path=parsed_output_path,
                validation_path=validation_path,
                command_payload=result.get("command_payload"),
                stdout_path=result.get("stdout_path"),
                stderr_path=result.get("stderr_path"),
            )
        except Exception as exc:
            self.db.finish_stage_run(
                run_id,
                status="failed",
                exit_code=1,
                parsed_output_path=parsed_output_path,
                validation_path=validation_path,
                error_text=str(exc),
                command_payload=result.get("command_payload") if result else None,
                stdout_path=result.get("stdout_path") if result else None,
                stderr_path=result.get("stderr_path") if result else None,
            )
            raise

    def _episode_run_translations(self, episode_id: str) -> None:
        """Run translation for each non-master language, one at a time."""
        from .translation import TranslationService

        episode = self.db.get_episode(episode_id)
        master_lang = episode["master_language"]
        langs = json.loads(episode.get("configured_languages") or "[]")
        workspace = self._episode_workspace(episode)
        project = self.db.get_niche_project(episode["niche_project_id"])
        translation_profiles = json.loads(project.get("language_translation_profiles") or "{}")
        source_channel_name = str(project.get("source_channel_name") or "").strip()
        language_channel_names = json.loads(project.get("language_channel_names") or "{}")
        enable_prompt = bool(int(project.get("channel_replace_prompt", 1) or 1))
        enable_post = bool(int(project.get("channel_replace_post", 1) or 1))
        settings = self._global_settings()
        reviewer_api_key = self._stage_provider_openai_api_key()
        source_script = episode["script_text"]

        # Set master language script path
        master_script_path, master_spoken_path = self._write_language_script_assets(
            workspace=workspace,
            language_code=master_lang,
            master_language=master_lang,
            readable_script=source_script,
        )
        self.db.update_episode_language_status(
            episode_id, master_lang,
            translation_status="done",
            script_path=str(master_script_path),
            spoken_script_path=str(master_spoken_path),
            error_message=None,
        )

        # Load master scenes if consistency guide exists (for context)
        master_scenes = None
        if episode.get("timeline_draft_path") and Path(episode["timeline_draft_path"]).exists():
            master_scenes = read_json(Path(episode["timeline_draft_path"]), default=[])

        failed_count = 0
        non_master_langs = [lang for lang in langs if lang != master_lang]

        for lang in non_master_langs:
            profile_id = translation_profiles.get(lang)
            if not profile_id:
                self.db.update_episode_language_status(
                    episode_id, lang,
                    translation_status="skipped",
                    error_message="No translation profile configured",
                )
                continue

            profile = self.db.get_translation_profile(profile_id)
            if profile is None:
                self.db.update_episode_language_status(
                    episode_id, lang,
                    translation_status="skipped",
                    error_message=f"Translation profile '{profile_id}' not found",
                )
                continue

            self.db.update_episode_language_status(episode_id, lang, translation_status="running")
            try:
                target_channel = language_channel_names.get(lang, "")
                translation_svc = TranslationService()
                result = asyncio.run(translation_svc.translate_script(
                    source_script=source_script,
                    source_lang=master_lang,
                    target_lang=lang,
                    provider=profile["provider"],
                    api_key=profile["api_key_ref"],
                    model=profile["model"],
                    master_scenes=master_scenes,
                    max_words_per_chunk=settings.get("translation_chunk_max_words", 800),
                    context_tail_words=settings.get("translation_context_tail_words", 200),
                    source_channel_name=source_channel_name if enable_prompt else "",
                    target_channel_name=target_channel if enable_prompt else "",
                    reviewer_required=True,
                    reviewer_api_key=reviewer_api_key,
                    reviewer_model="gpt-5.4-mini",
                ))
                chunk_log = [
                    {
                        "chunk_index": cr.chunk_index,
                        "scene_ids": cr.scene_ids,
                        "words_in": cr.words_in,
                        "words_out": cr.words_out,
                        "status": cr.status,
                        "error": cr.error,
                    }
                    for cr in result.chunk_results
                ]
                write_json(workspace / f"translation_log_{lang}.json", chunk_log)
                translated_script = self._validated_translation_script(
                    result,
                    language_code=lang,
                    source_script=source_script,
                    source_channel_name=source_channel_name if enable_post else "",
                    target_channel_name=target_channel if enable_post else "",
                )
                translated_path, spoken_path = self._write_language_script_assets(
                    workspace=workspace,
                    language_code=lang,
                    master_language=master_lang,
                    readable_script=translated_script,
                )
                review_report_raw = getattr(result, "review_report", None)
                review_report = review_report_raw if isinstance(review_report_raw, dict) else None
                if review_report:
                    write_json(workspace / f"translation_review_{lang}.json", review_report)

                self.db.update_episode_language_status(
                    episode_id, lang,
                    translation_status="done",
                    script_path=str(translated_path),
                    spoken_script_path=str(spoken_path),
                    error_message=None,
                )
            except Exception as exc:
                failed_count += 1
                self.db.update_episode_language_status(
                    episode_id, lang,
                    translation_status="failed",
                    script_path=None,
                    spoken_script_path=None,
                    error_message=str(exc)[:500],
                )

        if failed_count == len(non_master_langs) and non_master_langs:
            raise RuntimeError("All translations failed.")

    def _episode_tts_script_text(
        self,
        episode: dict[str, Any],
        language_code: str,
        language_status: dict[str, Any] | None,
    ) -> str:
        script_path = self._preferred_audio_script_path(
            episode=episode,
            language_code=language_code,
            language_status=language_status,
        )
        if script_path and script_path.exists():
            return read_text(script_path).strip()
        master_language = str(episode.get("master_language") or "en").strip() or "en"
        if language_code == master_language:
            return str(episode.get("script_text") or "").strip()
        translated_text = self._read_path_text((language_status or {}).get("script_path")).strip()
        return translated_text or str(episode.get("script_text") or "").strip()

    def _queue_episode_tts_jobs(
        self,
        episode_id: str,
        *,
        allow_resubmit_failed: bool,
    ) -> dict[str, Any]:
        episode = self._hydrate_episode_record(self.db.get_episode(episode_id))
        if episode is None:
            raise FileNotFoundError("Episode not found.")
        project = self._hydrate_project_record(self.db.get_niche_project(episode["niche_project_id"])) or {}
        voice_profiles = self._parse_json_dict(project.get("language_voice_profiles"))
        langs = list(episode.get("configured_languages") or [])
        statuses = {
            status["language_code"]: status
            for status in self.db.get_episode_language_statuses(episode_id)
        }
        submitted_jobs = 0
        active_jobs = 0
        any_failed = False
        unrecoverable: list[str] = []
        tts_mgr = None

        for lang in langs:
            lang_status = statuses.get(lang)
            if lang_status is None:
                unrecoverable.append(f"{lang}: missing language status")
                continue

            status = str(lang_status.get("tts_status") or "pending").strip().lower()
            if status in {"done", "skipped"}:
                continue
            if status == "failed" and not allow_resubmit_failed:
                any_failed = True
                continue

            job_id = str(lang_status.get("tts_job_id") or "").strip()
            job = self.db.get_tts_job(job_id) if job_id else None
            job_status = str((job or {}).get("status") or "").strip().lower()

            if job and job_status == "completed":
                result_path = str(job.get("result_path") or "").strip()
                if result_path and Path(result_path).exists():
                    self.db.update_episode_language_status(
                        episode_id,
                        lang,
                        tts_status="done",
                        tts_audio_path=result_path,
                        error_message=None,
                    )
                    continue
                job = None
                job_status = ""

            if job and job_status in {"queued", "processing"}:
                stage_status = self._tts_stage_status_for_job_status(job_status)
                if status != stage_status:
                    self.db.update_episode_language_status(
                        episode_id,
                        lang,
                        tts_status=stage_status,
                        error_message=None,
                    )
                active_jobs += 1
                continue

            if job and job_status in {"error", "failed"} and not allow_resubmit_failed:
                self.db.update_episode_language_status(
                    episode_id,
                    lang,
                    tts_status="failed",
                    error_message=job.get("error_message", "TTS failed"),
                )
                any_failed = True
                continue

            profile_id = str(voice_profiles.get(lang) or "").strip()
            if not profile_id:
                if allow_resubmit_failed:
                    self.db.update_episode_language_status(
                        episode_id,
                        lang,
                        tts_status="skipped",
                        error_message="No voice profile configured",
                    )
                    continue
                unrecoverable.append(f"{lang}: no voice profile configured")
                continue

            profile = self.db.get_voice_profile(profile_id)
            if profile is None:
                if allow_resubmit_failed:
                    self.db.update_episode_language_status(
                        episode_id,
                        lang,
                        tts_status="skipped",
                        error_message=f"Voice profile '{profile_id}' not found",
                    )
                    continue
                unrecoverable.append(f"{lang}: voice profile '{profile_id}' not found")
                continue

            if tts_mgr is None:
                tts_mgr = self.tts_manager
                tts_mgr.ensure_worker_ready(intent="pipeline")

            script_text = self._episode_tts_script_text(episode, lang, lang_status)
            payload = self._build_generate_tts_payload(
                profile=profile,
                language=lang,
                script_text=script_text,
            )
            new_job_id = tts_mgr.submit_tts_job(
                job_type="generate",
                profile_id=profile_id,
                payload=payload,
                build_id=episode_id,
                filename=f"narration_{lang}.wav",
            )
            self.db.update_episode_language_status(
                episode_id,
                lang,
                tts_status="queued",
                tts_job_id=new_job_id,
                tts_audio_path=None,
                error_message=None,
            )
            active_jobs += 1
            submitted_jobs += 1

        return {
            "submitted_jobs": submitted_jobs,
            "active_jobs": active_jobs,
            "any_failed": any_failed,
            "unrecoverable": unrecoverable,
        }

    def _mark_episode_tts_active_languages_running(self, episode_id: str) -> None:
        for lang_status in self.db.get_episode_language_statuses(episode_id):
            job_id = str(lang_status.get("tts_job_id") or "").strip()
            if not job_id:
                continue
            job = self.db.get_tts_job(job_id)
            job_status = str((job or {}).get("status") or "").strip().lower()
            if job_status not in {"queued", "processing"}:
                continue
            if str(lang_status.get("tts_status") or "").strip().lower() == "running":
                continue
            self.db.update_episode_language_status(
                episode_id,
                str(lang_status.get("language_code") or ""),
                tts_status="running",
                error_message=None,
            )

    @staticmethod
    def _paused_tts_failure_message(
        *,
        unrecoverable: list[str],
        unresolved_languages: list[str],
    ) -> str:
        if unrecoverable:
            return "TTS recovery failed: " + "; ".join(unrecoverable)
        if unresolved_languages:
            return (
                "TTS has no active or queued jobs for "
                + ", ".join(unresolved_languages)
                + ". The workflow was marked failed."
            )
        return "TTS could not be recovered."

    def _recover_paused_tts_queue(self, paused_episodes: list[dict[str, Any]]) -> str | None:
        """Requeue stale TTS jobs and wake the shared worker for paused episodes."""
        if not paused_episodes:
            return None
        health = self.tts_manager.get_worker_health()
        if health.is_stale or not health.running:
            self.db.requeue_stale_tts_jobs(STALE_PROCESSING_SECONDS)
        else:
            self.db.requeue_orphaned_processing_tts_jobs(STALE_PROCESSING_SECONDS)

        active_jobs = self.db.list_active_tts_jobs()
        if not active_jobs:
            return None

        try:
            self.tts_manager.ensure_worker_ready(intent="pipeline")
        except Exception as exc:
            return str(exc)[:500]
        return None

    def _episode_run_tts_all(self, episode_id: str) -> None:
        """Queue TTS for every unresolved language, then pause until all jobs settle."""
        tts_queue = self._queue_episode_tts_jobs(episode_id, allow_resubmit_failed=True)
        if tts_queue["active_jobs"] <= 0:
            return
        self._mark_episode_tts_active_languages_running(episode_id)
        self.db.update_episode(
            episode_id,
            board_status="Running",
            pipeline_status="paused_for_tts",
            current_stage="tts",
            last_error=None,
            updated_at=utc_now(),
        )

    def _episode_run_alignments(self, episode_id: str) -> None:
        """Run alignment for each language, one at a time."""
        episode = self.db.get_episode(episode_id)
        langs = json.loads(episode.get("configured_languages") or "[]")
        workspace = self._episode_workspace(episode)

        failed_count = 0
        attempted = 0

        for lang in langs:
            lang_status = self.db.get_episode_language_status(episode_id, lang)
            if not lang_status or lang_status.get("srt_status") == "done":
                continue
            audio_path = lang_status.get("tts_audio_path")
            script_path = self._preferred_audio_script_path(
                episode=episode,
                language_code=lang,
                language_status=lang_status,
            )
            if not audio_path or not Path(audio_path).exists():
                self.db.update_episode_language_status(
                    episode_id, lang, srt_status="skipped",
                    error_message="No TTS audio available",
                )
                continue
            if script_path is None or not script_path.exists():
                self.db.update_episode_language_status(
                    episode_id, lang, srt_status="skipped",
                    error_message="No script available",
                )
                continue

            attempted += 1
            self.db.update_episode_language_status(episode_id, lang, srt_status="running")
            try:
                output_root = ensure_dir(workspace / "alignment" / lang)
                result = run_alignment_job(
                    audio_path=Path(audio_path),
                    script_path=script_path,
                    language_code=lang,
                    engine_config=None,
                    segmentation_config=None,
                    output_root=output_root,
                )
                srt_path = workspace / f"final_{lang}.srt"
                shutil.copy2(result.artifacts.final_srt, srt_path)
                self.db.update_episode_language_status(
                    episode_id, lang,
                    srt_status="done",
                    srt_path=str(srt_path),
                )
            except Exception as exc:
                failed_count += 1
                self.db.update_episode_language_status(
                    episode_id, lang,
                    srt_status="failed",
                    error_message=str(exc)[:500],
                )

        if failed_count == attempted and attempted > 0:
            raise RuntimeError("All alignments failed.")

    def _episode_run_chunking(self, episode_id: str) -> None:
        """Run chunking on master language SRT."""
        episode = self.db.get_episode(episode_id)
        workspace = self._episode_workspace(episode)
        master_lang = episode["master_language"]

        lang_status = self.db.get_episode_language_status(episode_id, master_lang)
        if not lang_status or not lang_status.get("srt_path"):
            raise ValueError(f"Master language SRT not available for {master_lang}")

        srt_path = Path(lang_status["srt_path"])
        if not srt_path.exists():
            raise FileNotFoundError(f"Master SRT file not found: {srt_path}")

        cues = parse_srt_text(srt_path.read_text(encoding="utf-8"))
        settings = self._global_settings()
        chunks, manifest_meta = build_planning_chunks(
            cues,
            chunk_seconds=int(settings["planning_chunk_seconds"]),
            overlap_seconds=int(settings["planning_overlap_seconds"]),
        )

        chunk_dir = ensure_dir(workspace / "planning_chunks")
        manifest = {**manifest_meta, "chunks": []}
        for chunk in chunks:
            chunk_json_path = write_json(
                chunk_dir / f"chunk-{chunk.chunk_id:03d}.json",
                {
                    **chunk.to_dict(),
                    "source_chunk_id": chunk.chunk_id,
                    "srt": chunk.as_srt(),
                    "text": chunk.as_text(),
                },
            )
            write_text(chunk_dir / f"chunk-{chunk.chunk_id:03d}.srt", chunk.as_srt())
            write_text(chunk_dir / f"chunk-{chunk.chunk_id:03d}.txt", chunk.as_text())
            manifest["chunks"].append({**chunk.to_dict(), "json_path": str(chunk_json_path)})

        manifest_path = write_json(chunk_dir / "manifest.json", manifest)
        self.db.update_episode(episode_id, planning_manifest_path=str(manifest_path), updated_at=utc_now())

    def _episode_run_scene_planning(self, episode_id: str) -> None:
        """Run scene planning using master language chunks via LLM."""
        episode = self.db.get_episode(episode_id)
        workspace = self._episode_workspace(episode)
        config = self._resolved_episode_config(episode)
        provider = config["scene_planning_provider"]
        master_lang = episode["master_language"]

        manifest_path = episode.get("planning_manifest_path")
        if not manifest_path or not Path(manifest_path).exists():
            raise ValueError("Planning manifest not found.")
        manifest = read_json(Path(manifest_path))

        lang_status = self.db.get_episode_language_status(episode_id, master_lang)
        if not lang_status or not lang_status.get("srt_path"):
            raise ValueError("Master SRT not available for scene planning.")
        cues = parse_srt_text(Path(lang_status["srt_path"]).read_text(encoding="utf-8"))

        template = self.templates.snapshot_template(workspace, SCENE_STAGE, provider)
        all_scene_groups: list[list[dict[str, Any]]] = []
        warnings: list[str] = []

        for chunk in manifest["chunks"]:
            chunk_id = int(chunk["chunk_id"])
            chunk_payload = read_json(Path(chunk["json_path"]))
            user_prompt = (
                "Create scene JSON for this timed subtitle chunk.\n\n"
                "Rules for this run:\n"
                "- return ordered, non-overlapping scenes only\n"
                "- prefer scenes around 6 to 16 seconds\n"
                "- treat 18 seconds as a soft ceiling unless the text strongly resists splitting\n"
                "- every start and end must be absolute episode seconds, never chunk-relative seconds\n"
                "- every start and end must stay inside this chunk's start_seconds to end_seconds window\n"
                "- anchor boundaries to meaningful subtitle cue ranges\n"
                "- do not decide image versus video here\n"
                "- make each scene one dominant cinematic beat that can become one image or one continuous shot\n"
                "- split when the text changes location, time, subject focus, or dramatic action enough that one frame would feel crowded\n"
                "- do not combine multiple separate events, comparisons, or before/after beats into one scene\n"
                "- never emit placeholder, gap, or SKIP scenes; absorb tiny gaps into adjacent scenes\n"
                "- keep boundary scenes conservative because this chunk overlaps neighboring chunks\n\n"
                f"Chunk metadata:\n{json.dumps(chunk_payload | {'episode_id': episode_id}, ensure_ascii=False, indent=2)}"
            )
            chunk_dir = ensure_dir(workspace / "runs" / "scene_planning" / f"chunk-{chunk_id:03d}")
            schema = scene_output_schema()
            run_id = self._start_structured_stage_run(
                episode_id=episode_id,
                stage="scene_planning",
                provider=provider,
                template_hash=template.get("hash"),
                workdir=workspace,
                artifact_dir=chunk_dir,
                model=config["scene_planning_model"],
                schema=schema,
            )
            result: dict[str, Any] | None = None
            parsed_output_path: str | None = None
            validation_path: str | None = None
            try:
                result = self.cli_runner.run_structured(
                    provider=provider,
                    model=config["scene_planning_model"],
                    api_key=self._stage_provider_api_key(provider),
                    system_prompt=template["body"],
                    user_prompt=user_prompt,
                    schema=schema,
                    workdir=workspace,
                    artifact_dir=chunk_dir,
                )
                parsed_output_path = str(write_json(chunk_dir / "parsed.json", result["parsed"]))
                scene_group, group_warnings = normalize_scene_payload(
                    result["parsed"],
                    chunk_id,
                    chunk_window=chunk_payload,
                )
                validation_path = str(write_json(chunk_dir / "validated.json", scene_group))
                all_scene_groups.append(scene_group)
                warnings.extend(group_warnings)
                self.db.finish_stage_run(
                    run_id,
                    status="completed",
                    exit_code=0,
                    parsed_output_path=parsed_output_path,
                    validation_path=validation_path,
                    command_payload=result.get("command_payload"),
                    stdout_path=result.get("stdout_path"),
                    stderr_path=result.get("stderr_path"),
                )
            except Exception as exc:
                self.db.finish_stage_run(
                    run_id,
                    status="failed",
                    exit_code=1,
                    parsed_output_path=parsed_output_path,
                    validation_path=validation_path,
                    error_text=str(exc),
                    command_payload=result.get("command_payload") if result else None,
                    stdout_path=result.get("stdout_path") if result else None,
                    stderr_path=result.get("stderr_path") if result else None,
                )
                raise

        timeline, report = merge_scene_chunks(
            all_scene_groups,
            config=SceneMergeConfig(
                chunk_metadata=manifest["chunks"],
                overlap_seconds=float(manifest.get("overlap_seconds", 0)),
                cues=cues,
            ),
        )
        timeline = apply_default_asset_types(timeline, config["leading_video_scene_count"])
        report["warnings"].extend(warnings)
        if report["errors"]:
            raise ValueError("; ".join(report["errors"]))

        timeline_path = write_json(workspace / "timeline_draft.json", timeline)
        write_json(workspace / "timeline_validation.json", report)
        self.db.update_episode(
            episode_id,
            timeline_draft_path=str(timeline_path),
            updated_at=utc_now(),
        )

    def _episode_run_prompt_stage(self, episode_id: str, asset_type: str) -> None:
        """Run video or image prompt generation via LLM."""
        episode = self.db.get_episode(episode_id)
        workspace = self._episode_workspace(episode)
        config = self._resolved_episode_config(episode)
        provider = config["video_prompt_provider"] if asset_type == "video" else config["image_prompt_provider"]
        model = config["video_prompt_model"] if asset_type == "video" else config["image_prompt_model"]
        stage = VIDEO_PROMPT_STAGE if asset_type == "video" else IMAGE_PROMPT_STAGE

        timeline_path = episode.get("timeline_draft_path")
        if not timeline_path or not Path(timeline_path).exists():
            raise ValueError("Timeline draft is missing.")
        timeline = read_json(Path(timeline_path), default=[])
        if not timeline:
            raise ValueError("Timeline draft is empty.")

        guide_path = episode.get("consistency_guide_path")
        if not guide_path or not Path(guide_path).exists():
            raise ValueError("Consistency guide is missing.")
        visual_bible = read_json(Path(guide_path))

        scenes = [scene for scene in timeline if scene["asset_type"] == asset_type]
        if not scenes:
            # No scenes for this asset type — write empty blueprint
            write_json(workspace / f"{asset_type}_prompt_blueprints.json", [])
            return

        settings = self._global_settings()
        batch_size = int(settings["prompt_batch_size"])
        batches = build_prompt_batches(scenes, batch_size=batch_size)
        template = self.templates.snapshot_template(workspace, stage, provider)
        schema = video_prompt_output_schema() if asset_type == "video" else image_prompt_output_schema()
        stage_name = "video_prompt_generation" if asset_type == "video" else "image_prompt_generation"
        mode_rules = (
            "Use the structured JSON fields scene_id, subject, setting, action, camera, look, lighting, rules, character_refs, and prompt."
            if asset_type == "video"
            else "Use the structured JSON fields scene_id, subject, setting, composition, look, lighting, rules, character_refs, and prompt."
        )
        target_words = "65 to 95 words" if asset_type == "video" else "45 to 75 words"

        # Collect prompts indexed by scene_id — accepts partial batches
        prompts_by_scene_id: dict[str, dict[str, Any]] = {}
        next_batch_id = len(batches) + 1

        def _run_batch(batch: dict[str, Any], run_label: str) -> None:
            prompt_context = self._build_prompt_context(visual_bible, batch["scenes"])
            user_prompt = (
                f"Generate one {asset_type} prompt per scene in this batch.\n"
                "Return English JSON only.\n"
                "Every final prompt must be self-sufficient and copy-paste ready.\n"
                "Treat every scene as a shot from one continuous cinematic movie with the same visual language across the whole project.\n"
                "Give each prompt exactly one dominant visual moment or shot idea; do not stack multiple scenes, panels, or comparisons into one prompt.\n"
                "The final prompt field must be plain natural-language prose, not a labeled template.\n"
                "Do not use literal tokens like SUBJ, SET, ACT, CAM, COMP, LOOK, LIGHT, or RULES inside the final prompt text.\n"
                "Do not use words like same, previous scene, or as before.\n"
                "Do not include scene_id or asset_type inside the final prompt text.\n"
                "If a scene has character guidance, use those visual traits directly in the final prompt text; names alone are not enough.\n"
                "Prefer concrete visible details over abstract themes or narration summaries.\n"
                "Make the frame feel full-bleed, dramatically composed, and visually rich; avoid empty white space, page layouts, poster layouts, or subjects floating on blank backgrounds.\n"
                "Default toward dramatic, action-ready, emotionally charged, visually epic imagery when the scene allows it.\n"
                "Do not drift into documentary, interview, news, or explainer framing unless the source scene explicitly requires it.\n"
                "Do not request split-screen, diptych, triptych, collage, storyboard, title card, infographic, before/after, or multi-panel layouts.\n"
                "Do not place visible text inside the frame: no subtitles, captions, labels, logos, watermarks, UI, signage, or letters.\n"
                "Avoid repetitive filler like cinematic documentary hybrid, restrained, tactile, or neutral unless it adds specific visual value.\n"
                f"Keep the final prompt compact, ideally around {target_words}.\n"
                f"{mode_rules}\n"
                "Use those structured fields to organize the response, but make the final prompt field read like direct prose for an image or video model.\n\n"
                f"Prompt context:\n{json.dumps(prompt_context, ensure_ascii=False, indent=2)}\n\n"
                f"Batch payload:\n{json.dumps(batch, ensure_ascii=False, indent=2)}"
            )
            batch_dir = ensure_dir(workspace / "runs" / stage / run_label)
            run_id = self._start_structured_stage_run(
                episode_id=episode_id,
                stage=stage_name,
                provider=provider,
                template_hash=template.get("hash"),
                workdir=workspace,
                artifact_dir=batch_dir,
                model=model,
                schema=schema,
            )
            result: dict[str, Any] | None = None
            parsed_output_path: str | None = None
            try:
                result = self.cli_runner.run_structured(
                    provider=provider,
                    model=model,
                    api_key=self._stage_provider_api_key(provider),
                    system_prompt=template["body"],
                    user_prompt=user_prompt,
                    schema=schema,
                    workdir=workspace,
                    artifact_dir=batch_dir,
                )
                parsed_output_path = str(write_json(batch_dir / "parsed.json", result["parsed"]))
                received = result["parsed"].get("prompts", [])
                if isinstance(received, list):
                    for prompt_item in received:
                        sid = str(prompt_item.get("scene_id") or "").strip()
                        if sid and sid not in prompts_by_scene_id:
                            prompts_by_scene_id[sid] = prompt_item
                self.db.finish_stage_run(
                    run_id,
                    status="completed",
                    exit_code=0,
                    parsed_output_path=parsed_output_path,
                    command_payload=result.get("command_payload"),
                    stdout_path=result.get("stdout_path"),
                    stderr_path=result.get("stderr_path"),
                )
            except Exception as exc:
                self.db.finish_stage_run(
                    run_id,
                    status="failed",
                    exit_code=1,
                    parsed_output_path=parsed_output_path,
                    error_text=str(exc),
                    command_payload=result.get("command_payload") if result else None,
                    stdout_path=result.get("stdout_path") if result else None,
                    stderr_path=result.get("stderr_path") if result else None,
                )
                raise

        # --- Initial pass: run all batches, accept partial results ---
        for batch in batches:
            _run_batch(batch, f"batch-{batch['batch_id']:03d}")

        # --- Gap-fill: re-request only missing scenes (up to 2 rounds) ---
        expected_ids = {s["scene_id"] for s in scenes}
        for gap_round in range(2):
            missing_ids = expected_ids - set(prompts_by_scene_id.keys())
            if not missing_ids:
                break
            log.warning(
                "Gap-fill round %d: %d/%d scenes still missing",
                gap_round + 1, len(missing_ids), len(scenes),
            )
            gap_batches = build_gap_fill_batches(
                scenes, set(prompts_by_scene_id.keys()),
                batch_size=batch_size,
                batch_id_offset=next_batch_id,
            )
            next_batch_id += len(gap_batches)
            for gap_batch in gap_batches:
                _run_batch(gap_batch, f"gap-{gap_round + 1}-batch-{gap_batch['batch_id']:03d}")

        # --- Reconstruct ordered payload for downstream validation ---
        final_missing = expected_ids - set(prompts_by_scene_id.keys())
        if final_missing:
            raise ValueError(
                f"Prompt count mismatch: expected {len(scenes)}, received "
                f"{len(scenes) - len(final_missing)}. Missing scene IDs: "
                f"{', '.join(sorted(final_missing)[:10])}"
                f"{'...' if len(final_missing) > 10 else ''}"
            )
        ordered_prompts = [prompts_by_scene_id[s["scene_id"]] for s in scenes]
        log.info(
            "Prompt generation complete: %d/%d scenes covered",
            len(ordered_prompts), len(scenes),
        )

        normalized_entries, _ = normalize_prompt_payloads(scenes, [{"prompts": ordered_prompts}])
        normalized_entries = self._enrich_prompt_entries(normalized_entries, visual_bible)
        prompt_lines, report = validate_prompt_payloads(scenes, [{"prompts": normalized_entries}])
        if report["errors"]:
            raise ValueError("; ".join(report["errors"]))
        for entry, prompt_line in zip(normalized_entries, prompt_lines):
            entry["prompt"] = prompt_line
        write_json(workspace / f"{asset_type}_prompt_blueprints.json", normalized_entries)

    def _episode_combine_prompts(self, episode_id: str) -> None:
        """Combine video + image prompt outputs into final prompt list."""
        episode = self.db.get_episode(episode_id)
        workspace = self._episode_workspace(episode)

        timeline_path = episode.get("timeline_draft_path")
        if not timeline_path or not Path(timeline_path).exists():
            raise ValueError("Timeline draft is missing.")
        scenes = read_json(Path(timeline_path), default=[])
        if not scenes:
            raise ValueError("Timeline draft is empty.")

        stage_sources = {
            "video": workspace / "video_prompt_blueprints.json",
            "image": workspace / "image_prompt_blueprints.json",
        }
        entry_by_scene_id: dict[str, dict[str, Any]] = {}
        for asset_type, source_path in stage_sources.items():
            relevant_scenes = [s for s in scenes if s["asset_type"] == asset_type]
            if not relevant_scenes:
                continue
            if not source_path.exists():
                continue
            stage_entries = read_json(source_path, default=[])
            for entry in stage_entries:
                if isinstance(entry, dict) and entry.get("scene_id"):
                    entry_by_scene_id[entry["scene_id"]] = entry

        ordered_entries: list[dict[str, Any]] = []
        ordered_payloads: list[dict[str, Any]] = []
        for scene in scenes:
            entry = entry_by_scene_id.get(scene["scene_id"])
            if entry is None:
                continue
            updated_entry = dict(entry)
            updated_entry["scene_id"] = scene["scene_id"]
            updated_entry["asset_type"] = scene["asset_type"]
            ordered_entries.append(updated_entry)
            ordered_payloads.append({"scene_id": scene["scene_id"], "prompt": updated_entry.get("prompt", "")})

        prompt_lines, report = validate_prompt_payloads(scenes, [{"prompts": ordered_payloads}])
        if report["errors"]:
            raise ValueError("; ".join(report["errors"]))

        draft_path = write_text(workspace / "prompt_list_draft.txt", "\n".join(prompt_lines).strip() + "\n")
        blueprint_path = write_jsonl(workspace / "prompt_blueprint.jsonl", ordered_entries)

        # Split drafts by asset type
        video_lines = [pl for s, pl in zip(scenes, prompt_lines) if s["asset_type"] == "video"]
        image_lines = [pl for s, pl in zip(scenes, prompt_lines) if s["asset_type"] == "image"]
        write_text(workspace / "video_prompt_list_draft.txt", "\n".join(video_lines).strip() + ("\n" if video_lines else ""))
        write_text(workspace / "image_prompt_list_draft.txt", "\n".join(image_lines).strip() + ("\n" if image_lines else ""))

        self.db.update_episode(
            episode_id,
            prompt_blueprint_path=str(blueprint_path),
            prompt_list_draft_path=str(draft_path),
            updated_at=utc_now(),
        )

    def _episode_save_master_scenes(self, episode_id: str) -> None:
        """Save master scenes reference copy from timeline draft."""
        episode = self.db.get_episode(episode_id)
        workspace = self._episode_workspace(episode)
        timeline_path = episode.get("timeline_draft_path")
        if not timeline_path or not Path(timeline_path).exists():
            raise ValueError("Timeline draft is missing.")
        timeline = read_json(Path(timeline_path), default=[])
        scenes_path = write_json(workspace / "master_scenes.json", timeline)
        self.db.update_episode(episode_id, master_scenes_path=str(scenes_path), updated_at=utc_now())

    def _episode_run_timeline_mapping(self, episode_id: str) -> None:
        """Map master scene structure to each language's duration proportionally."""
        episode, _, _, _, _ = self._load_timeline_mapping_context(episode_id)
        langs = json.loads(episode.get("configured_languages") or "[]")
        context = self._load_timeline_mapping_context(episode_id)

        failed_count = 0
        attempted = 0

        for lang in langs:
            lang_status = self.db.get_episode_language_status(episode_id, lang)
            if not lang_status or lang_status.get("timeline_status") == "done":
                continue
            result = self._episode_retry_single_timeline_mapping(episode_id, lang, context=context)
            if result in {"done", "failed"}:
                attempted += 1
            if result == "failed":
                failed_count += 1

        if failed_count == attempted and attempted > 0:
            raise RuntimeError("All timeline mappings failed.")

    def _check_paused_tts_episodes(self) -> None:
        """Check if any paused episodes have completed TTS and can resume."""
        paused = self.db.list_paused_tts_episodes()
        worker_recovery_error = self._recover_paused_tts_queue(paused)
        for episode in paused:
            episode_id = episode["id"]
            try:
                tts_queue = self._queue_episode_tts_jobs(episode_id, allow_resubmit_failed=False)
            except Exception as exc:
                self.db.update_episode(
                    episode_id,
                    board_status="Needs Attention",
                    pipeline_status="failed",
                    current_stage="tts",
                    last_error=f"TTS recovery failed: {str(exc)[:450]}",
                    pause_requested=0,
                    updated_at=utc_now(),
                )
                continue
            lang_statuses = self.db.get_episode_language_statuses(episode_id)
            any_tts_failed = bool(tts_queue["any_failed"])
            unresolved_languages: list[str] = []

            for ls in lang_statuses:
                status = str(ls.get("tts_status") or "pending").strip().lower()
                if status == "failed":
                    any_tts_failed = True
                elif status not in {"done", "skipped"}:
                    unresolved_languages.append(str(ls.get("language_code") or ""))

            if any_tts_failed:
                self.db.update_episode(
                    episode_id,
                    board_status="Needs Attention",
                    pipeline_status="failed",
                    last_error="One or more TTS jobs failed.",
                    pause_requested=0,
                    updated_at=utc_now(),
                )
                continue

            if unresolved_languages:
                if tts_queue["active_jobs"] > 0:
                    if worker_recovery_error:
                        self.db.update_episode(
                            episode_id,
                            board_status="Needs Attention",
                            pipeline_status="failed",
                            current_stage="tts",
                            last_error=(
                                "TTS worker could not resume queued jobs: "
                                + worker_recovery_error
                            )[:500],
                            pause_requested=0,
                            updated_at=utc_now(),
                        )
                        continue
                    self._mark_episode_tts_active_languages_running(episode_id)
                    if tts_queue["submitted_jobs"] > 0:
                        self.db.update_episode(
                            episode_id,
                            board_status="Running",
                            pipeline_status="paused_for_tts",
                            current_stage="tts",
                            last_error=None,
                            updated_at=utc_now(),
                        )
                    continue
                self.db.update_episode(
                    episode_id,
                    board_status="Needs Attention",
                    pipeline_status="failed",
                    current_stage="tts",
                    last_error=self._paused_tts_failure_message(
                        unrecoverable=tts_queue["unrecoverable"],
                        unresolved_languages=unresolved_languages,
                    ),
                    pause_requested=0,
                    updated_at=utc_now(),
                )
                continue

            if not unresolved_languages:
                refreshed_episode = self._hydrate_episode_record(self.db.get_episode(episode_id)) or {}
                if refreshed_episode.get("pause_requested"):
                    self.db.update_episode(
                        episode_id,
                        board_status="Paused",
                        pipeline_status="paused",
                        current_stage="alignment",
                        queued_from_stage="alignment",
                        pause_requested=0,
                        updated_at=utc_now(),
                    )
                else:
                    # Resume pipeline at alignment
                    self.db.update_episode(
                        episode_id,
                        board_status="Queued",
                        pipeline_status="queued",
                        current_stage="alignment",
                        queued_from_stage="alignment",
                        pause_requested=0,
                        updated_at=utc_now(),
                    )
