from __future__ import annotations

import asyncio
import json
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
from .chunking import build_planning_chunks, build_prompt_batches
from .config import (
    AGENTS_ROOT,
    BOARD_STATUSES,
    BUILD_TYPES,
    DEFAULT_ALIGNMENT_OPTIONS,
    DEFAULT_SETTINGS,
    IMAGE_PROMPT_STAGE,
    LOCALIZATION_RUNNABLE_STAGES,
    MASTER_PIPELINE_STAGES,
    MASTER_RUNNABLE_STAGES,
    EPISODE_PIPELINE_STAGES,
    EPISODE_RUNNABLE_STAGES,
    EPISODE_PER_LANGUAGE_STAGES,
    MAX_PREVIEW_CHARS,
    MODEL_CATALOG,
    PROVIDERS,
    RUNNABLE_STAGES,
    SCENE_STAGE,
    TARGET_LANGUAGES,
    VIDEO_PROMPT_STAGE,
    EPISODES_ROOT,
    VISUAL_BIBLE_STAGE,
)
from .database import Tool1Database
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
from .validators import (
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


class Tool1Service:
    def __init__(self, db: Tool1Database | None = None, cli_runner: CliRunner | None = None) -> None:
        self.db = db or Tool1Database()
        self.cli_runner = cli_runner or CliRunner()
        self.templates = TemplateStore(self.db)
        self._condition = threading.Condition()
        self._stop_event = threading.Event()
        self._worker_thread: threading.Thread | None = None
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

    def _worker_loop(self) -> None:
        while not self._stop_event.is_set():
            job = self.db.next_queued_job()
            if job is not None:
                self._process_job(job)
                continue
            build = self.db.next_queued_build()
            if build is not None:
                self._process_build(build)
                continue
            episode = self.db.next_queued_episode()
            if episode is not None:
                self._process_episode(episode)
                continue
            self._check_paused_tts_builds()
            self._check_paused_tts_episodes()
            with self._condition:
                self._condition.wait(timeout=1.0)

    def _job_root(self, job_id: str) -> Path:
        return EPISODES_ROOT / job_id

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

    def _job_dirs(self, job_id: str) -> dict[str, Path]:
        root = self._job_root(job_id)
        return {
            "root": ensure_dir(root),
            "inputs": ensure_dir(root / "inputs"),
            "review": ensure_dir(root / "review"),
            "exports": ensure_dir(root / "exports"),
            "drafts": ensure_dir(root / "drafts"),
            "runs": ensure_dir(root / "runs"),
            "diagnostics": ensure_dir(root / "diagnostics"),
            "snapshots": ensure_dir(root / "snapshots"),
        }

    def _alignment_run_ids_for_job(self, job_id: str) -> list[str]:
        run_ids: list[str] = []
        seen: set[str] = set()
        for run in self.db.list_stage_runs(job_id):
            if run.get("stage") != "alignment":
                continue
            stdout_path = str(run.get("stdout_path") or "").strip()
            if not stdout_path:
                continue
            stdout_file = Path(stdout_path)
            run_id = ""
            if stdout_file.name.lower() == "run.log" and stdout_file.parent.name:
                run_id = stdout_file.parent.name
            else:
                parts = [part.lower() for part in stdout_file.parts]
                if "artifacts" in parts:
                    index = parts.index("artifacts")
                    if index + 1 < len(stdout_file.parts):
                        run_id = stdout_file.parts[index + 1]
            if run_id and run_id not in seen:
                seen.add(run_id)
                run_ids.append(run_id)
        return run_ids

    @staticmethod
    def _load_visual_bible_source_script(job: dict[str, Any]) -> dict[str, Any]:
        script_path_value = job.get("script_path")
        if not script_path_value:
            raise ValueError("Original script file is missing.")
        script_path = Path(script_path_value)
        if not script_path.exists():
            raise ValueError("Original script file does not exist anymore.")
        raw_script = extract_script_text(script_path)
        script_document = normalize_script(raw_script)
        paragraphs = script_document.paragraphs or [script_document.canonical_text]
        return {
            "script_filename": job.get("script_filename") or script_path.name,
            "word_count": len(script_document.words),
            "paragraph_count": len(paragraphs),
            "paragraphs": paragraphs,
        }

    def _global_settings(self) -> dict[str, Any]:
        return {**DEFAULT_SETTINGS, **self.db.get_settings()}

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

    def _resolved_job_config(self, job: dict[str, Any]) -> dict[str, Any]:
        settings = self._global_settings()
        scene_provider = (
            job.get("scene_planning_provider")
            or job.get("scene_provider")
            or settings["default_scene_planning_provider"]
        )
        visual_provider = (
            job.get("visual_bible_provider")
            or job.get("prompt_provider")
            or settings["default_visual_bible_provider"]
        )
        video_provider = (
            job.get("video_prompt_provider")
            or job.get("prompt_provider")
            or settings["default_video_prompt_provider"]
        )
        image_provider = (
            job.get("image_prompt_provider")
            or job.get("prompt_provider")
            or settings["default_image_prompt_provider"]
        )
        return {
            "scene_planning_provider": scene_provider,
            "visual_bible_provider": visual_provider,
            "video_prompt_provider": video_provider,
            "image_prompt_provider": image_provider,
            "scene_planning_model": self._resolve_model_choice(
                scene_provider,
                job.get("scene_planning_model"),
                settings["default_scene_planning_model"],
            ),
            "visual_bible_model": self._resolve_model_choice(
                visual_provider,
                job.get("visual_bible_model"),
                settings["default_visual_bible_model"],
            ),
            "video_prompt_model": self._resolve_model_choice(
                video_provider,
                job.get("video_prompt_model"),
                settings["default_video_prompt_model"],
            ),
            "image_prompt_model": self._resolve_model_choice(
                image_provider,
                job.get("image_prompt_model"),
                settings["default_image_prompt_model"],
            ),
            "leading_video_scene_count": int(
                job.get("leading_video_scene_count")
                if job.get("leading_video_scene_count") is not None
                else settings["leading_video_scene_count"]
            ),
        }

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
            payload = {
                "key": canonical_key,
                "display_name": display_name or character_id.replace("_", " "),
                "descriptor": cls._inline_character_descriptor(card),
                "aliases": cls._ordered_unique([display_name, *aliases, character_id.replace("_", " ")]),
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
            for alias in payload["aliases"]:
                pattern = re.compile(rf"\b{re.escape(alias)}(?:'s)?\b", re.IGNORECASE)
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
            for alias in payload["aliases"]:
                pattern = re.compile(rf"\b{re.escape(alias)}\b", re.IGNORECASE)
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

    def _stage_blueprint_path(self, job_id: str, stage: str) -> Path:
        return self._job_root(job_id) / "drafts" / f"{stage}_blueprints.json"

    def create_job(
        self,
        *,
        title: str,
        audio_name: str,
        audio_bytes: bytes,
        script_name: str,
        script_bytes: bytes,
        language_code: str,
        scene_planning_provider: str | None = None,
        visual_bible_provider: str | None = None,
        video_prompt_provider: str | None = None,
        image_prompt_provider: str | None = None,
        scene_planning_model: str | None = None,
        visual_bible_model: str | None = None,
        video_prompt_model: str | None = None,
        image_prompt_model: str | None = None,
        leading_video_scene_count: int | None = None,
    ) -> dict[str, Any]:
        settings = self._global_settings()
        job_id = make_job_id(title)
        dirs = self._job_dirs(job_id)
        audio_filename = safe_filename(audio_name, "audio")
        script_filename = safe_filename(script_name, "script")
        audio_path = dirs["inputs"] / audio_filename
        script_path = dirs["inputs"] / script_filename
        audio_path.write_bytes(audio_bytes)
        script_path.write_bytes(script_bytes)
        now = utc_now()

        scene_provider = scene_planning_provider or settings["default_scene_planning_provider"]
        visual_provider = visual_bible_provider or settings["default_visual_bible_provider"]
        video_provider = video_prompt_provider or settings["default_video_prompt_provider"]
        image_provider = image_prompt_provider or settings["default_image_prompt_provider"]
        scene_model = self._resolve_model_choice(
            scene_provider,
            scene_planning_model,
            settings["default_scene_planning_model"],
        )
        visual_model = self._resolve_model_choice(
            visual_provider,
            visual_bible_model,
            settings["default_visual_bible_model"],
        )
        video_model = self._resolve_model_choice(
            video_provider,
            video_prompt_model,
            settings["default_video_prompt_model"],
        )
        image_model = self._resolve_model_choice(
            image_provider,
            image_prompt_model,
            settings["default_image_prompt_model"],
        )
        video_count = int(
            leading_video_scene_count
            if leading_video_scene_count is not None
            else settings["leading_video_scene_count"]
        )

        self.db.create_job(
            {
                "id": job_id,
                "title": title.strip() or job_id,
                "board_status": "Draft",
                "pipeline_status": "idle",
                "current_stage": "draft",
                "queued_from_stage": "alignment",
                "language_code": language_code,
                "scene_provider": scene_provider,
                "prompt_provider": video_provider,
                "scene_planning_provider": scene_provider,
                "visual_bible_provider": visual_provider,
                "video_prompt_provider": video_provider,
                "image_prompt_provider": image_provider,
                "scene_planning_model": scene_model,
                "visual_bible_model": visual_model,
                "video_prompt_model": video_model,
                "image_prompt_model": image_model,
                "leading_video_scene_count": video_count,
                "workspace_dir": str(dirs["root"]),
                "audio_filename": audio_filename,
                "script_filename": script_filename,
                "audio_path": str(audio_path),
                "script_path": str(script_path),
                "created_at": now,
                "updated_at": now,
            }
        )
        return self.get_job_detail(job_id)

    def list_jobs(self) -> list[dict[str, Any]]:
        jobs = self.db.list_jobs()
        for job in jobs:
            job.update(self._resolved_job_config(job))
        return jobs

    def move_job(self, job_id: str, board_status: str) -> dict[str, Any]:
        if board_status not in BOARD_STATUSES:
            raise ValueError("Invalid board status.")
        self.db.update_job(job_id, board_status=board_status)
        return self.get_job_detail(job_id)

    def queue_job(self, job_id: str, start_stage: str = "alignment") -> dict[str, Any]:
        if start_stage not in RUNNABLE_STAGES:
            raise ValueError("Invalid start stage.")
        self.db.update_job(
            job_id,
            board_status="Queued",
            pipeline_status="queued",
            current_stage=start_stage,
            queued_from_stage=start_stage,
            last_error=None,
            review_ready=0,
        )
        with self._condition:
            self._condition.notify_all()
        return self.get_job_detail(job_id)

    def update_job_config(
        self,
        job_id: str,
        *,
        scene_planning_provider: str,
        visual_bible_provider: str,
        video_prompt_provider: str,
        image_prompt_provider: str,
        scene_planning_model: str,
        visual_bible_model: str,
        video_prompt_model: str,
        image_prompt_model: str,
        leading_video_scene_count: int,
    ) -> dict[str, Any]:
        providers = (
            scene_planning_provider,
            visual_bible_provider,
            video_prompt_provider,
            image_prompt_provider,
        )
        if any(provider not in PROVIDERS for provider in providers):
            raise ValueError("Invalid provider choice.")
        self.db.update_job(
            job_id,
            scene_provider=scene_planning_provider,
            prompt_provider=video_prompt_provider,
            scene_planning_provider=scene_planning_provider,
            visual_bible_provider=visual_bible_provider,
            video_prompt_provider=video_prompt_provider,
            image_prompt_provider=image_prompt_provider,
            scene_planning_model=self._resolve_model_choice(
                scene_planning_provider,
                scene_planning_model,
                self._global_settings()["default_scene_planning_model"],
            ),
            visual_bible_model=self._resolve_model_choice(
                visual_bible_provider,
                visual_bible_model,
                self._global_settings()["default_visual_bible_model"],
            ),
            video_prompt_model=self._resolve_model_choice(
                video_prompt_provider,
                video_prompt_model,
                self._global_settings()["default_video_prompt_model"],
            ),
            image_prompt_model=self._resolve_model_choice(
                image_prompt_provider,
                image_prompt_model,
                self._global_settings()["default_image_prompt_model"],
            ),
            leading_video_scene_count=max(0, int(leading_video_scene_count)),
        )
        return self.get_job_detail(job_id)

    def delete_job(self, job_id: str) -> dict[str, Any]:
        job = self.db.get_job(job_id)
        if job is None:
            raise FileNotFoundError("Job not found.")
        if job.get("pipeline_status") == "running" or self.db.has_running_stage_run(job_id):
            raise ValueError("This card is running right now. Wait for it to finish before deleting it.")

        workspace_dir = Path(job.get("workspace_dir") or self._job_root(job_id))
        alignment_run_ids = self._alignment_run_ids_for_job(job_id)

        self.db.delete_job_records(job_id)

        self._safe_delete_path(workspace_dir, EPISODES_ROOT)
        for run_id in alignment_run_ids:
            self._safe_delete_path(ALIGNMENT_OUTPUT_ROOT / run_id, ALIGNMENT_OUTPUT_ROOT)
            self._safe_delete_path(ALIGNMENT_TEMP_ROOT / run_id, ALIGNMENT_TEMP_ROOT)

        return {
            "deleted": True,
            "job_id": job_id,
            "workspace_dir": str(workspace_dir),
            "alignment_run_ids": alignment_run_ids,
        }

    def get_health(self) -> dict[str, Any]:
        return {
            "alignment": alignment_health(),
            "providers": self.cli_runner.probe(),
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
        return {
            "settings": self._global_settings(),
            "templates": self.templates.list_templates(),
            "agents_root": str(AGENTS_ROOT),
            "model_catalog": MODEL_CATALOG,
        }

    def save_settings(self, payload: dict[str, Any]) -> dict[str, Any]:
        for key in DEFAULT_SETTINGS:
            if key in payload:
                self.db.set_setting(key, payload[key])
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

    def _warning_total(
        self,
        job: dict[str, Any],
        *,
        alignment_warnings: int | None = None,
        timeline_report: dict[str, Any] | None = None,
        visual_bible_report: dict[str, Any] | None = None,
        prompt_report: dict[str, Any] | None = None,
    ) -> int:
        def count_warnings(value: Any) -> int:
            if isinstance(value, dict) and isinstance(value.get("warnings"), list):
                return len(value["warnings"])
            return 0

        total = 0
        if alignment_warnings is None:
            alignment_report_path = job.get("alignment_report_path")
            if alignment_report_path:
                total += count_warnings(read_json(Path(alignment_report_path), default={}))
        else:
            total += max(0, int(alignment_warnings))

        if timeline_report is None:
            timeline_validation_path = job.get("timeline_validation_path")
            if timeline_validation_path:
                total += count_warnings(read_json(Path(timeline_validation_path), default={}))
        else:
            total += count_warnings(timeline_report)

        if visual_bible_report is None:
            visual_bible_validation_path = job.get("visual_bible_validation_path")
            if visual_bible_validation_path:
                total += count_warnings(read_json(Path(visual_bible_validation_path), default={}))
        else:
            total += count_warnings(visual_bible_report)

        if prompt_report is None:
            prompt_validation_path = job.get("prompt_validation_path")
            if prompt_validation_path:
                total += count_warnings(read_json(Path(prompt_validation_path), default={}))
        else:
            total += count_warnings(prompt_report)

        return total

    def get_live_log(self, job_id: str) -> dict[str, Any]:
        job = self.db.get_job(job_id)
        if job is None:
            raise FileNotFoundError("Job not found.")
        running_run = None
        for run in self.db.list_stage_runs(job_id):
            if run.get("status") == "running":
                running_run = run
                break
        if not running_run:
            return {"running": False, "stage": None, "stdout": "", "stderr": ""}
        stage_dir = Path(running_run.get("workdir") or "")
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        if stage_dir.exists():
            for path in sorted(stage_dir.rglob("stdout.txt")):
                content = read_text(path)
                if content.strip():
                    stdout_parts.append(f"--- {path.parent.name} ---\n{content}")
            for path in sorted(stage_dir.rglob("stderr.txt")):
                content = read_text(path)
                if content.strip():
                    stderr_parts.append(f"--- {path.parent.name} ---\n{content}")
        stage_stdout = read_text(stage_dir / "stdout.log") if (stage_dir / "stdout.log").exists() else ""
        stage_stderr = read_text(stage_dir / "stderr.log") if (stage_dir / "stderr.log").exists() else ""
        if stage_stdout.strip():
            stdout_parts.insert(0, stage_stdout)
        if stage_stderr.strip():
            stderr_parts.insert(0, stage_stderr)
        return {
            "running": True,
            "stage": running_run.get("stage"),
            "provider": running_run.get("provider"),
            "started_at": running_run.get("started_at"),
            "stdout": "\n\n".join(stdout_parts),
            "stderr": "\n\n".join(stderr_parts),
        }

    def get_job_detail(self, job_id: str) -> dict[str, Any]:
        job = self.db.get_job(job_id)
        if job is None:
            raise FileNotFoundError("Job not found.")
        self._ensure_review_split_prompt_drafts(job)
        job.update(self._resolved_job_config(job))
        stage_runs = self.db.list_stage_runs(job_id)
        for run in stage_runs:
            run["stdout_preview"] = self._read_preview_text(run.get("stdout_path"))
            run["stderr_preview"] = self._read_preview_text(run.get("stderr_path"))
            command_json = run.get("command_json")
            if isinstance(command_json, str):
                try:
                    run["command"] = json.loads(command_json)
                except json.JSONDecodeError:
                    run["command"] = {"raw": command_json}
            else:
                run["command"] = command_json
        return {
            "job": job,
            "artifacts": {
                "final_srt": self._read_preview_text(job.get("final_srt_path")),
                "alignment_report": self._read_preview_json(job.get("alignment_report_path")),
                "segments": self._read_preview_json(job.get("segments_path"), limit_items=20),
                "planning_manifest": self._read_preview_json(job.get("planning_manifest_path")),
                "timeline": self._read_preview_json(job.get("timeline_draft_path")),
                "timeline_validation": self._read_preview_json(job.get("timeline_validation_path")),
                "visual_bible": self._read_preview_json(job.get("visual_bible_path")),
                "visual_bible_validation": self._read_preview_json(job.get("visual_bible_validation_path")),
                "consistency_guide": self._read_preview_json(job.get("visual_bible_path")),
                "consistency_guide_validation": self._read_preview_json(job.get("visual_bible_validation_path")),
                "prompt_list": self._read_preview_text(job.get("prompt_list_draft_path")),
                "video_prompt_list_draft": self._read_preview_text(str(self._review_video_prompt_draft_path(job_id))),
                "image_prompt_list_draft": self._read_preview_text(str(self._review_image_prompt_draft_path(job_id))),
                "prompt_blueprint": self._read_preview_jsonl(job.get("prompt_blueprint_path"), limit_items=30),
                "prompt_validation": self._read_preview_json(job.get("prompt_validation_path")),
            },
            "stage_runs": stage_runs,
        }

    def save_review_timeline(self, job_id: str, scenes: list[dict[str, Any]]) -> dict[str, Any]:
        job = self.db.get_job(job_id)
        if job is None:
            raise FileNotFoundError("Job not found.")
        report = validate_timeline(scenes)
        dirs = self._job_dirs(job_id)
        timeline_path = write_json(dirs["review"] / "timeline_draft.json", scenes)
        validation_path = write_json(dirs["diagnostics"] / "timeline_validation.json", report)
        self.db.update_job(
            job_id,
            timeline_draft_path=str(timeline_path),
            timeline_validation_path=str(validation_path),
            warning_count=self._warning_total(job, timeline_report=report),
            last_error="; ".join(report["errors"]) if report["errors"] else None,
            board_status="Review",
            pipeline_status="review",
            current_stage="review",
        )
        return self.get_job_detail(job_id)

    def save_review_visual_bible(self, job_id: str, visual_bible: dict[str, Any]) -> dict[str, Any]:
        job = self.db.get_job(job_id)
        if job is None:
            raise FileNotFoundError("Job not found.")
        normalized, report = normalize_visual_bible(visual_bible)
        dirs = self._job_dirs(job_id)
        bible_path = write_json(dirs["review"] / "consistency_guide.json", normalized)
        validation_path = write_json(dirs["diagnostics"] / "consistency_guide_validation.json", report)
        self.db.update_job(
            job_id,
            visual_bible_path=str(bible_path),
            visual_bible_validation_path=str(validation_path),
            warning_count=self._warning_total(job, visual_bible_report=report),
            last_error="; ".join(report["errors"]) if report["errors"] else None,
            board_status="Review",
            pipeline_status="review",
            current_stage="review",
        )
        return self.get_job_detail(job_id)

    def save_review_prompts(
        self,
        job_id: str,
        prompts: list[str] | None = None,
        *,
        video_prompts: list[str] | None = None,
        image_prompts: list[str] | None = None,
    ) -> dict[str, Any]:
        job = self.db.get_job(job_id)
        if job is None:
            raise FileNotFoundError("Job not found.")
        scenes = self._read_preview_json(job.get("timeline_draft_path")) or []
        lines = self._combined_prompt_lines_for_review(
            scenes,
            prompts=prompts,
            video_prompts=video_prompts,
            image_prompts=image_prompts,
        )
        lines, report = validate_prompt_payloads(scenes, [{"prompts": lines}])
        dirs = self._job_dirs(job_id)
        existing_blueprint = self._read_preview_jsonl(job.get("prompt_blueprint_path")) or []
        by_scene_id = {
            entry.get("scene_id"): entry
            for entry in existing_blueprint
            if isinstance(entry, dict) and entry.get("scene_id")
        }
        blueprint_entries: list[dict[str, Any]] = []
        for index, scene in enumerate(scenes):
            if index >= len(lines):
                break
            existing = dict(by_scene_id.get(scene["scene_id"], {}))
            existing.update(
                {
                    "scene_id": scene["scene_id"],
                    "asset_type": scene["asset_type"],
                    "prompt": lines[index],
                }
            )
            blueprint_entries.append(existing)
        prompt_path = write_text(dirs["review"] / "prompt_list_draft.txt", "\n".join(lines).strip() + "\n")
        self._write_split_prompt_drafts(job_id, scenes, lines)
        blueprint_path = write_jsonl(dirs["review"] / "prompt_blueprint.jsonl", blueprint_entries)
        validation_path = write_json(dirs["diagnostics"] / "prompt_validation.json", report)
        self.db.update_job(
            job_id,
            prompt_list_draft_path=str(prompt_path),
            prompt_blueprint_path=str(blueprint_path),
            prompt_validation_path=str(validation_path),
            warning_count=self._warning_total(job, prompt_report=report),
            last_error="; ".join(report["errors"]) if report["errors"] else None,
            board_status="Review",
            pipeline_status="review",
            current_stage="review",
        )
        return self.get_job_detail(job_id)

    @staticmethod
    def _split_prompt_lines_by_asset_type(
        timeline: list[dict[str, Any]],
        prompt_lines: list[str],
    ) -> tuple[list[str], list[str]]:
        video_lines: list[str] = []
        image_lines: list[str] = []
        for scene, prompt_line in zip(timeline, prompt_lines):
            if scene.get("asset_type") == "video":
                video_lines.append(prompt_line)
            else:
                image_lines.append(prompt_line)
        return video_lines, image_lines

    def _review_video_prompt_draft_path(self, job_id: str) -> Path:
        return self._job_root(job_id) / "review" / "video_prompt_list_draft.txt"

    def _review_image_prompt_draft_path(self, job_id: str) -> Path:
        return self._job_root(job_id) / "review" / "image_prompt_list_draft.txt"

    def _write_split_prompt_drafts(
        self,
        job_id: str,
        scenes: list[dict[str, Any]],
        prompt_lines: list[str],
    ) -> tuple[Path, Path]:
        video_prompt_lines, image_prompt_lines = self._split_prompt_lines_by_asset_type(scenes, prompt_lines)
        video_path = self._review_video_prompt_draft_path(job_id)
        image_path = self._review_image_prompt_draft_path(job_id)
        write_text(video_path, "\n".join(video_prompt_lines).strip() + ("\n" if video_prompt_lines else ""))
        write_text(image_path, "\n".join(image_prompt_lines).strip() + ("\n" if image_prompt_lines else ""))
        return video_path, image_path

    def _ensure_review_split_prompt_drafts(self, job: dict[str, Any]) -> None:
        job_id = str(job.get("id") or "")
        if not job_id:
            return
        video_path = self._review_video_prompt_draft_path(job_id)
        image_path = self._review_image_prompt_draft_path(job_id)
        if video_path.exists() and image_path.exists():
            return
        prompt_path_value = job.get("prompt_list_draft_path")
        timeline_path_value = job.get("timeline_draft_path")
        if not prompt_path_value or not timeline_path_value:
            return
        prompt_path = Path(prompt_path_value)
        timeline_path = Path(timeline_path_value)
        if not prompt_path.exists() or not timeline_path.exists():
            return
        scenes = read_json(timeline_path, default=[])
        prompt_lines = [line for line in read_text(prompt_path).splitlines() if line.strip()]
        if scenes and prompt_lines:
            self._write_split_prompt_drafts(job_id, scenes, prompt_lines)

    @staticmethod
    def _normalize_prompt_lines(value: list[str] | None) -> list[str]:
        return [line.strip() for line in (value or []) if str(line or "").strip()]

    def _combined_prompt_lines_for_review(
        self,
        scenes: list[dict[str, Any]],
        *,
        prompts: list[str] | None,
        video_prompts: list[str] | None,
        image_prompts: list[str] | None,
    ) -> list[str]:
        direct_lines = self._normalize_prompt_lines(prompts)
        split_video_lines = self._normalize_prompt_lines(video_prompts)
        split_image_lines = self._normalize_prompt_lines(image_prompts)
        if direct_lines and (split_video_lines or split_image_lines):
            raise ValueError("Send either merged prompts or split video/image prompts, not both.")
        if direct_lines:
            return direct_lines

        video_scene_count = sum(1 for scene in scenes if scene.get("asset_type") == "video")
        image_scene_count = sum(1 for scene in scenes if scene.get("asset_type") == "image")
        if len(split_video_lines) != video_scene_count:
            raise ValueError(f"Expected {video_scene_count} video prompts, received {len(split_video_lines)}.")
        if len(split_image_lines) != image_scene_count:
            raise ValueError(f"Expected {image_scene_count} image prompts, received {len(split_image_lines)}.")

        combined_lines: list[str] = []
        video_index = 0
        image_index = 0
        for scene in scenes:
            if scene.get("asset_type") == "video":
                combined_lines.append(split_video_lines[video_index])
                video_index += 1
            else:
                combined_lines.append(split_image_lines[image_index])
                image_index += 1
        return combined_lines

    def finalize_job(self, job_id: str) -> dict[str, Any]:
        job = self.db.get_job(job_id)
        if job is None:
            raise FileNotFoundError("Job not found.")
        final_srt = Path(job["final_srt_path"]) if job.get("final_srt_path") else None
        timeline_path = Path(job["timeline_draft_path"]) if job.get("timeline_draft_path") else None
        prompt_path = Path(job["prompt_list_draft_path"]) if job.get("prompt_list_draft_path") else None
        if not final_srt or not final_srt.exists():
            raise ValueError("Final SRT is missing.")
        if not timeline_path or not timeline_path.exists():
            raise ValueError("Timeline draft is missing.")
        if not prompt_path or not prompt_path.exists():
            raise ValueError("Prompt list draft is missing.")
        timeline = read_json(timeline_path, default=[])
        timeline_report = validate_timeline(timeline)
        prompt_lines = [line for line in read_text(prompt_path).splitlines() if line.strip()]
        prompt_lines, prompt_report = validate_prompt_payloads(timeline, [{"prompts": prompt_lines}])
        if timeline_report["errors"] or prompt_report["errors"]:
            raise ValueError("Fix validation errors before final export.")
        dirs = self._job_dirs(job_id)
        export_srt = dirs["exports"] / "final.srt"
        export_timeline = dirs["exports"] / "timeline.json"
        export_prompts = dirs["exports"] / "prompt_list.txt"
        export_video_prompts = dirs["exports"] / "video_prompt_list.txt"
        export_image_prompts = dirs["exports"] / "image_prompt_list.txt"
        video_prompt_lines, image_prompt_lines = self._split_prompt_lines_by_asset_type(timeline, prompt_lines)
        shutil.copy2(final_srt, export_srt)
        write_json(export_timeline, timeline)
        write_text(export_prompts, "\n".join(prompt_lines).strip() + "\n")
        write_text(export_video_prompts, "\n".join(video_prompt_lines).strip() + ("\n" if video_prompt_lines else ""))
        write_text(export_image_prompts, "\n".join(image_prompt_lines).strip() + ("\n" if image_prompt_lines else ""))
        self.db.update_job(
            job_id,
            export_timeline_path=str(export_timeline),
            export_prompt_list_path=str(export_prompts),
            export_video_prompt_list_path=str(export_video_prompts),
            export_image_prompt_list_path=str(export_image_prompts),
            board_status="Done",
            pipeline_status="done",
            current_stage="export",
            review_ready=1,
            warning_count=self._warning_total(job, timeline_report=timeline_report, prompt_report=prompt_report),
            last_error=None,
        )
        return self.get_job_detail(job_id)

    def get_artifact_path(self, job_id: str, artifact_key: str) -> Path:
        job = self.db.get_job(job_id)
        if job is None:
            raise FileNotFoundError("Job not found.")
        self._ensure_review_split_prompt_drafts(job)
        mapping = {
            "final_srt": job.get("final_srt_path"),
            "alignment_report": job.get("alignment_report_path"),
            "segments_json": job.get("segments_path"),
            "planning_manifest": job.get("planning_manifest_path"),
            "timeline_draft": job.get("timeline_draft_path"),
            "timeline_validation": job.get("timeline_validation_path"),
            "visual_bible": job.get("visual_bible_path"),
            "visual_bible_validation": job.get("visual_bible_validation_path"),
            "consistency_guide": job.get("visual_bible_path"),
            "consistency_guide_validation": job.get("visual_bible_validation_path"),
            "prompt_list_draft": job.get("prompt_list_draft_path"),
            "video_prompt_list_draft": str(self._review_video_prompt_draft_path(job_id)),
            "image_prompt_list_draft": str(self._review_image_prompt_draft_path(job_id)),
            "prompt_blueprint": job.get("prompt_blueprint_path"),
            "prompt_validation": job.get("prompt_validation_path"),
            "export_timeline": job.get("export_timeline_path"),
            "export_prompt_list": job.get("export_prompt_list_path"),
            "export_video_prompt_list": job.get("export_video_prompt_list_path"),
            "export_image_prompt_list": job.get("export_image_prompt_list_path"),
        }
        target = mapping.get(artifact_key)
        if not target:
            raise FileNotFoundError("Artifact not found.")
        path = Path(target)
        if not path.exists():
            raise FileNotFoundError("Artifact not found.")
        return path

    def _process_job(self, job: dict[str, Any]) -> None:
        job_id = job["id"]
        start_stage = job.get("queued_from_stage") or "alignment"
        self.db.update_job(
            job_id,
            board_status="Running",
            pipeline_status="running",
            current_stage=start_stage,
            last_error=None,
        )
        try:
            if start_stage == "alignment":
                self._run_alignment(job_id)
                self._run_planning_prep(job_id)
                self._run_scene_planning(job_id)
                self._run_visual_bible(job_id)
                self._run_video_prompt_generation(job_id)
                self._run_image_prompt_generation(job_id)
            elif start_stage == "planning_prep":
                self._run_planning_prep(job_id)
                self._run_scene_planning(job_id)
                self._run_visual_bible(job_id)
                self._run_video_prompt_generation(job_id)
                self._run_image_prompt_generation(job_id)
            elif start_stage == "scene_planning":
                self._run_scene_planning(job_id)
                self._run_visual_bible(job_id)
                self._run_video_prompt_generation(job_id)
                self._run_image_prompt_generation(job_id)
            elif start_stage == VISUAL_BIBLE_STAGE:
                self._run_visual_bible(job_id)
                self._run_video_prompt_generation(job_id)
                self._run_image_prompt_generation(job_id)
            elif start_stage == VIDEO_PROMPT_STAGE:
                self._run_video_prompt_generation(job_id)
            elif start_stage == IMAGE_PROMPT_STAGE:
                self._run_image_prompt_generation(job_id)
            self.db.update_job(
                job_id,
                board_status="Review",
                pipeline_status="review",
                current_stage="review",
                review_ready=1,
                queued_from_stage="alignment",
            )
        except Exception as exc:
            current_job = self.db.get_job(job_id) or {}
            self.db.update_job(
                job_id,
                board_status="Needs Attention",
                pipeline_status="failed",
                current_stage=current_job.get("current_stage", start_stage),
                last_error=str(exc).strip() or exc.__class__.__name__,
                review_ready=0,
            )

    def _begin_stage(self, job_id: str, stage: str, provider: str | None = None) -> tuple[dict[str, Any], Path, int]:
        job = self.db.get_job(job_id)
        if job is None:
            raise FileNotFoundError("Job not found.")
        stage_dir = ensure_dir(self._job_root(job_id) / "runs" / stage / utc_now().replace(":", "-"))
        stdout_path = stage_dir / "stdout.log"
        stderr_path = stage_dir / "stderr.log"
        run_id = self.db.start_stage_run(
            job_id,
            stage,
            provider,
            None,
            str(stage_dir),
            {"stage": stage},
            str(stdout_path),
            str(stderr_path),
        )
        self.db.update_job(job_id, current_stage=stage)
        return job, stage_dir, run_id

    @staticmethod
    def _write_stage_logs(stage_dir: Path, stdout_parts: list[str], stderr_parts: list[str]) -> tuple[str, str]:
        stdout_path = write_text(stage_dir / "stdout.log", "\n\n".join(part for part in stdout_parts if part.strip()))
        stderr_path = write_text(stage_dir / "stderr.log", "\n\n".join(part for part in stderr_parts if part.strip()))
        return str(stdout_path), str(stderr_path)

    @staticmethod
    def _collect_stage_logs(stage_dir: Path) -> tuple[list[str], list[str]]:
        stdout_parts: list[str] = []
        stderr_parts: list[str] = []
        for path in sorted(stage_dir.rglob("stdout.txt")):
            content = read_text(path)
            if content.strip():
                stdout_parts.append(content)
        for path in sorted(stage_dir.rglob("stderr.txt")):
            content = read_text(path)
            if content.strip():
                stderr_parts.append(content)
        return stdout_parts, stderr_parts

    def _clear_prompt_outputs(self, job_id: str) -> None:
        self.db.update_job(
            job_id,
            visual_bible_path=None,
            visual_bible_validation_path=None,
            prompt_list_draft_path=None,
            prompt_blueprint_path=None,
            prompt_validation_path=None,
        )

    def _run_alignment(self, job_id: str) -> None:
        job, stage_dir, run_id = self._begin_stage(job_id, "alignment")
        try:
            output_root = ensure_dir(stage_dir / "artifacts")
            result = run_alignment_job(
                audio_path=Path(job["audio_path"]),
                script_path=Path(job["script_path"]),
                language_code=job["language_code"],
                engine_config=None,
                segmentation_config=None,
                output_root=output_root,
            )
            review_dir = self._job_dirs(job_id)["review"]
            final_srt = review_dir / "final.srt"
            shutil.copy2(result.artifacts.final_srt, final_srt)
            report_path = stage_dir / "alignment_report.json"
            segments_path = stage_dir / "segments.json"
            shutil.copy2(result.artifacts.alignment_report, report_path)
            shutil.copy2(result.artifacts.segments_json, segments_path)
            self.db.update_job(
                job_id,
                final_srt_path=str(final_srt),
                alignment_report_path=str(report_path),
                segments_path=str(segments_path),
                warning_count=self._warning_total(job, alignment_warnings=len(result.report.warnings)),
            )
            self.db.finish_stage_run(
                run_id,
                status="completed",
                exit_code=0,
                parsed_output_path=str(report_path),
                validation_path=str(report_path),
                stdout_path=str(result.artifacts.run_log),
                stderr_path=None,
                command_payload={
                    "stage": "alignment",
                    "engine_used": result.engine_used,
                    "fallback_used": result.fallback_used,
                    "alignment_options": DEFAULT_ALIGNMENT_OPTIONS,
                },
            )
        except Exception as exc:
            self.db.finish_stage_run(
                run_id,
                status="failed",
                exit_code=1,
                error_text=str(exc),
            )
            raise

    def _run_planning_prep(self, job_id: str) -> None:
        job, stage_dir, run_id = self._begin_stage(job_id, "planning_prep")
        try:
            final_srt_path = Path(job["final_srt_path"])
            cues = parse_srt_text(final_srt_path.read_text(encoding="utf-8"))
            settings = self._global_settings()
            chunks, manifest_meta = build_planning_chunks(
                cues,
                chunk_seconds=int(settings["planning_chunk_seconds"]),
                overlap_seconds=int(settings["planning_overlap_seconds"]),
            )
            chunk_dir = ensure_dir(self._job_root(job_id) / "drafts" / "planning_chunks")
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
            self.db.update_job(job_id, planning_manifest_path=str(manifest_path))
            self.db.finish_stage_run(
                run_id,
                status="completed",
                exit_code=0,
                parsed_output_path=str(manifest_path),
                validation_path=str(manifest_path),
                command_payload={
                    "stage": "planning_prep",
                    "chunk_count": len(chunks),
                    "settings": {
                        "planning_chunk_seconds": settings["planning_chunk_seconds"],
                        "planning_overlap_seconds": settings["planning_overlap_seconds"],
                    },
                },
            )
        except Exception as exc:
            self.db.finish_stage_run(
                run_id,
                status="failed",
                exit_code=1,
                error_text=str(exc),
            )
            raise

    def _run_scene_planning(self, job_id: str) -> None:
        job = self.db.get_job(job_id)
        if job is None:
            raise FileNotFoundError("Job not found.")
        config = self._resolved_job_config(job)
        provider = config["scene_planning_provider"]
        job, stage_dir, run_id = self._begin_stage(job_id, SCENE_STAGE, provider=provider)
        template_hash: str | None = None
        commands: list[dict[str, Any]] = []
        try:
            manifest = read_json(Path(job["planning_manifest_path"]))
            cues = parse_srt_text(Path(job["final_srt_path"]).read_text(encoding="utf-8"))
            template = self.templates.snapshot_template(self._job_root(job_id), SCENE_STAGE, provider)
            template_hash = template["hash"]
            all_scene_groups: list[list[dict[str, Any]]] = []
            warnings: list[str] = []
            stdout_parts: list[str] = []
            stderr_parts: list[str] = []
            for chunk in manifest["chunks"]:
                chunk_id = int(chunk["chunk_id"])
                chunk_payload = read_json(Path(chunk["json_path"]))
                user_prompt = (
                    "Create scene JSON for this timed subtitle chunk.\n\n"
                    "Rules for this run:\n"
                    "- return ordered, non-overlapping scenes only\n"
                    "- prefer scenes around 6 to 16 seconds\n"
                    "- treat 18 seconds as a soft ceiling unless the text strongly resists splitting\n"
                    "- anchor boundaries to meaningful subtitle cue ranges\n"
                    "- do not decide image versus video here\n"
                    "- make each scene one dominant cinematic beat that can become one image or one continuous shot\n"
                    "- split when the text changes location, time, subject focus, or dramatic action enough that one frame would feel crowded\n"
                    "- do not combine multiple separate events, comparisons, or before/after beats into one scene\n"
                    "- never emit placeholder, gap, or SKIP scenes; absorb tiny gaps into adjacent scenes\n"
                    "- keep boundary scenes conservative because this chunk overlaps neighboring chunks\n\n"
                    f"Chunk metadata:\n{json.dumps(chunk_payload | {'job_id': job_id}, ensure_ascii=False, indent=2)}"
                )
                chunk_dir = ensure_dir(stage_dir / f"chunk-{chunk_id:03d}")
                result = self.cli_runner.run_structured(
                    provider=provider,
                    model=config["scene_planning_model"],
                    system_prompt=template["body"],
                    user_prompt=user_prompt,
                    schema=scene_output_schema(),
                    workdir=self._job_root(job_id),
                    artifact_dir=chunk_dir,
                )
                scene_group, group_warnings = normalize_scene_payload(result["parsed"], chunk_id)
                write_json(chunk_dir / "validated.json", scene_group)
                all_scene_groups.append(scene_group)
                warnings.extend(group_warnings)
                commands.append(result["command_payload"])
                stdout_parts.append(read_text(Path(result["stdout_path"])))
                stderr_parts.append(read_text(Path(result["stderr_path"])))
            timeline, report = merge_scene_chunks(
                all_scene_groups,
                chunk_metadata=manifest["chunks"],
                overlap_seconds=float(manifest.get("overlap_seconds", 0)),
                cues=cues,
            )
            timeline = apply_default_asset_types(timeline, config["leading_video_scene_count"])
            report = validate_timeline(timeline)
            report["warnings"].extend(warnings)
            if report["errors"]:
                raise ValueError("; ".join(report["errors"]))
            timeline_path = write_json(self._job_root(job_id) / "review" / "timeline_draft.json", timeline)
            validation_path = write_json(self._job_root(job_id) / "diagnostics" / "timeline_validation.json", report)
            stdout_path, stderr_path = self._write_stage_logs(stage_dir, stdout_parts, stderr_parts)
            self._clear_prompt_outputs(job_id)
            self.db.update_job(
                job_id,
                timeline_draft_path=str(timeline_path),
                timeline_validation_path=str(validation_path),
                warning_count=self._warning_total(job, timeline_report=report),
            )
            self.db.finish_stage_run(
                run_id,
                status="completed",
                exit_code=0,
                parsed_output_path=str(timeline_path),
                validation_path=str(validation_path),
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                command_payload={
                    "stage": SCENE_STAGE,
                    "provider": provider,
                    "model": config["scene_planning_model"],
                    "template_hash": template_hash,
                    "leading_video_scene_count": config["leading_video_scene_count"],
                    "commands": commands,
                },
            )
        except Exception as exc:
            stdout_parts, stderr_parts = self._collect_stage_logs(stage_dir)
            stdout_path, stderr_path = self._write_stage_logs(stage_dir, stdout_parts, stderr_parts)
            self.db.finish_stage_run(
                run_id,
                status="failed",
                exit_code=1,
                error_text=str(exc),
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                command_payload={
                    "stage": SCENE_STAGE,
                    "provider": provider,
                    "model": config["scene_planning_model"],
                    "template_hash": template_hash,
                    "leading_video_scene_count": config["leading_video_scene_count"],
                    "commands": commands,
                },
            )
            raise

    def _run_visual_bible(self, job_id: str) -> None:
        job = self.db.get_job(job_id)
        if job is None:
            raise FileNotFoundError("Job not found.")
        config = self._resolved_job_config(job)
        provider = config["visual_bible_provider"]
        job, stage_dir, run_id = self._begin_stage(job_id, VISUAL_BIBLE_STAGE, provider=provider)
        template_hash: str | None = None
        context_path: str | None = None
        try:
            source_script = self._load_visual_bible_source_script(job)
            template = self.templates.snapshot_template(self._job_root(job_id), VISUAL_BIBLE_STAGE, provider)
            template_hash = template["hash"]
            context_path = str(write_json(
                stage_dir / "input_context.json",
                {
                    "job_id": job_id,
                    "source_script": source_script,
                },
            ))
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
            result = self.cli_runner.run_structured(
                provider=provider,
                model=config["visual_bible_model"],
                system_prompt=template["body"],
                user_prompt=user_prompt,
                schema=visual_bible_output_schema(),
                workdir=self._job_root(job_id),
                artifact_dir=stage_dir,
            )
            normalized, report = normalize_visual_bible(result["parsed"])
            if report["errors"]:
                raise ValueError("; ".join(report["errors"]))
            bible_path = write_json(self._job_root(job_id) / "review" / "consistency_guide.json", normalized)
            validation_path = write_json(self._job_root(job_id) / "diagnostics" / "consistency_guide_validation.json", report)
            self.db.update_job(
                job_id,
                visual_bible_path=str(bible_path),
                visual_bible_validation_path=str(validation_path),
                prompt_list_draft_path=None,
                prompt_blueprint_path=None,
                prompt_validation_path=None,
                warning_count=self._warning_total(job, visual_bible_report=report),
            )
            self.db.finish_stage_run(
                run_id,
                status="completed",
                exit_code=0,
                parsed_output_path=str(bible_path),
                validation_path=str(validation_path),
                stdout_path=result["stdout_path"],
                stderr_path=result["stderr_path"],
                command_payload={
                    "stage": VISUAL_BIBLE_STAGE,
                    "provider": provider,
                    "model": config["visual_bible_model"],
                    "template_hash": template_hash,
                    "context_path": context_path,
                    "command": result["command_payload"],
                },
            )
        except Exception as exc:
            stdout_parts, stderr_parts = self._collect_stage_logs(stage_dir)
            stdout_path, stderr_path = self._write_stage_logs(stage_dir, stdout_parts, stderr_parts)
            self.db.finish_stage_run(
                run_id,
                status="failed",
                exit_code=1,
                error_text=str(exc),
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                command_payload={
                    "stage": VISUAL_BIBLE_STAGE,
                    "provider": provider,
                    "model": config["visual_bible_model"],
                    "template_hash": template_hash,
                    "context_path": context_path,
                },
            )
            raise

    def _run_video_prompt_generation(self, job_id: str) -> None:
        self._run_prompt_generation_stage(job_id, VIDEO_PROMPT_STAGE, "video")

    def _run_image_prompt_generation(self, job_id: str) -> None:
        self._run_prompt_generation_stage(job_id, IMAGE_PROMPT_STAGE, "image")

    def _run_prompt_generation_stage(self, job_id: str, stage: str, asset_type: str) -> None:
        job = self.db.get_job(job_id)
        if job is None:
            raise FileNotFoundError("Job not found.")
        config = self._resolved_job_config(job)
        provider = config["video_prompt_provider"] if asset_type == "video" else config["image_prompt_provider"]
        job, stage_dir, run_id = self._begin_stage(job_id, stage, provider=provider)
        template_hash: str | None = None
        commands: list[dict[str, Any]] = []
        try:
            timeline = read_json(Path(job["timeline_draft_path"]), default=[])
            if not timeline:
                raise ValueError("Timeline draft is missing.")
            visual_bible_path = Path(job["visual_bible_path"]) if job.get("visual_bible_path") else None
            if not visual_bible_path or not visual_bible_path.exists():
                raise ValueError("Consistency guide is missing.")
            visual_bible = read_json(visual_bible_path)
            scenes = [scene for scene in timeline if scene["asset_type"] == asset_type]
            settings = self._global_settings()
            batches = build_prompt_batches(scenes, batch_size=int(settings["prompt_batch_size"])) if scenes else []
            template = self.templates.snapshot_template(self._job_root(job_id), stage, provider)
            template_hash = template["hash"]
            payloads: list[dict[str, Any]] = []
            stdout_parts: list[str] = []
            stderr_parts: list[str] = []
            schema = video_prompt_output_schema() if asset_type == "video" else image_prompt_output_schema()

            for batch_index, batch in enumerate(batches):
                self.db.update_stage_run_command(run_id, {
                    "stage": stage,
                    "provider": provider,
                    "asset_type": asset_type,
                    "model": config["video_prompt_model"] if asset_type == "video" else config["image_prompt_model"],
                    "batch_index": batch_index,
                    "batch_total": len(batches),
                })
                batch_dir = ensure_dir(stage_dir / f"batch-{batch['batch_id']:03d}")
                mode_rules = (
                    "Use the structured JSON fields scene_id, subject, setting, action, camera, look, lighting, rules, character_refs, and prompt."
                    if asset_type == "video"
                    else "Use the structured JSON fields scene_id, subject, setting, composition, look, lighting, rules, character_refs, and prompt."
                )
                target_words = "65 to 95 words" if asset_type == "video" else "45 to 75 words"
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
                result = self.cli_runner.run_structured(
                    provider=provider,
                    model=config["video_prompt_model"] if asset_type == "video" else config["image_prompt_model"],
                    system_prompt=template["body"],
                    user_prompt=user_prompt,
                    schema=schema,
                    workdir=self._job_root(job_id),
                    artifact_dir=batch_dir,
                )
                payloads.append(result["parsed"])
                commands.append(result["command_payload"])
                stdout_parts.append(read_text(Path(result["stdout_path"])))
                stderr_parts.append(read_text(Path(result["stderr_path"])))

            normalized_entries, _ = normalize_prompt_payloads(scenes, payloads or [{"prompts": []}])
            normalized_entries = self._enrich_prompt_entries(normalized_entries, visual_bible)
            prompt_lines, report = validate_prompt_payloads(scenes, [{"prompts": normalized_entries}])
            if report["errors"]:
                raise ValueError("; ".join(report["errors"]))
            for entry, prompt_line in zip(normalized_entries, prompt_lines):
                entry["prompt"] = prompt_line
            stage_output_path = write_json(self._stage_blueprint_path(job_id, stage), normalized_entries)
            stage_validation_path = write_json(self._job_root(job_id) / "diagnostics" / f"{stage}_validation.json", report)
            stdout_path, stderr_path = self._write_stage_logs(stage_dir, stdout_parts, stderr_parts)
            self.db.finish_stage_run(
                run_id,
                status="completed",
                exit_code=0,
                parsed_output_path=str(stage_output_path),
                validation_path=str(stage_validation_path),
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                command_payload={
                    "stage": stage,
                    "provider": provider,
                    "asset_type": asset_type,
                    "model": config["video_prompt_model"] if asset_type == "video" else config["image_prompt_model"],
                    "template_hash": template_hash,
                    "commands": commands,
                },
            )
            self._combine_prompt_outputs(job_id, raise_on_missing=False)
        except Exception as exc:
            stdout_parts, stderr_parts = self._collect_stage_logs(stage_dir)
            stdout_path, stderr_path = self._write_stage_logs(stage_dir, stdout_parts, stderr_parts)
            self.db.finish_stage_run(
                run_id,
                status="failed",
                exit_code=1,
                error_text=str(exc),
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                command_payload={
                    "stage": stage,
                    "provider": provider,
                    "asset_type": asset_type,
                    "model": config["video_prompt_model"] if asset_type == "video" else config["image_prompt_model"],
                    "template_hash": template_hash,
                    "commands": commands,
                },
            )
            raise

    def _combine_prompt_outputs(self, job_id: str, *, raise_on_missing: bool) -> None:
        job = self.db.get_job(job_id)
        if job is None:
            raise FileNotFoundError("Job not found.")
        scenes = read_json(Path(job["timeline_draft_path"]), default=[])
        if not scenes:
            raise ValueError("Timeline draft is missing.")

        stage_sources = {
            "video": self._stage_blueprint_path(job_id, VIDEO_PROMPT_STAGE),
            "image": self._stage_blueprint_path(job_id, IMAGE_PROMPT_STAGE),
        }
        entry_by_scene_id: dict[str, dict[str, Any]] = {}
        missing_assets: list[str] = []
        for asset_type, source_path in stage_sources.items():
            relevant_scenes = [scene for scene in scenes if scene["asset_type"] == asset_type]
            if not relevant_scenes:
                continue
            if not source_path.exists():
                missing_assets.append(asset_type)
                continue
            stage_entries = read_json(source_path, default=[])
            for entry in stage_entries:
                if isinstance(entry, dict) and entry.get("scene_id"):
                    entry_by_scene_id[entry["scene_id"]] = entry

        if missing_assets:
            if raise_on_missing:
                raise ValueError(f"Missing prompt outputs for: {', '.join(missing_assets)}.")
            return

        ordered_entries: list[dict[str, Any]] = []
        ordered_payloads: list[dict[str, Any]] = []
        for scene in scenes:
            entry = entry_by_scene_id.get(scene["scene_id"])
            if entry is None:
                if raise_on_missing:
                    raise ValueError(f"Missing prompt output for {scene['scene_id']}.")
                return
            updated_entry = dict(entry)
            updated_entry["scene_id"] = scene["scene_id"]
            updated_entry["asset_type"] = scene["asset_type"]
            ordered_entries.append(updated_entry)
            ordered_payloads.append({"scene_id": scene["scene_id"], "prompt": updated_entry.get("prompt", "")})

        prompt_lines, report = validate_prompt_payloads(scenes, [{"prompts": ordered_payloads}])
        if report["errors"]:
            raise ValueError("; ".join(report["errors"]))
        review_dir = self._job_dirs(job_id)["review"]
        diagnostics_dir = self._job_dirs(job_id)["diagnostics"]
        prompt_path = write_text(review_dir / "prompt_list_draft.txt", "\n".join(prompt_lines).strip() + "\n")
        self._write_split_prompt_drafts(job_id, scenes, prompt_lines)
        blueprint_path = write_jsonl(review_dir / "prompt_blueprint.jsonl", ordered_entries)
        validation_path = write_json(diagnostics_dir / "prompt_validation.json", report)
        self.db.update_job(
            job_id,
            prompt_list_draft_path=str(prompt_path),
            prompt_blueprint_path=str(blueprint_path),
            prompt_validation_path=str(validation_path),
            warning_count=self._warning_total(job, prompt_report=report),
        )

    # ── project / build layer (Creator Studio multilingual) ────────

    def _project_root(self, project_id: str) -> Path:
        return EPISODES_ROOT / project_id

    def _project_dirs(self, project_id: str) -> dict[str, Path]:
        root = self._project_root(project_id)
        return {
            "root": ensure_dir(root),
            "inputs": ensure_dir(root / "inputs"),
            "master": ensure_dir(root / "master"),
            "exports": ensure_dir(root / "exports"),
        }

    def _build_dirs(self, project_id: str, build_id: str) -> dict[str, Path]:
        root = self._project_root(project_id)
        build_root = root / "builds" / build_id
        return {
            "root": ensure_dir(build_root),
            "review": ensure_dir(build_root / "review"),
            "exports": ensure_dir(build_root / "exports"),
            "drafts": ensure_dir(build_root / "drafts"),
            "runs": ensure_dir(build_root / "runs"),
            "diagnostics": ensure_dir(build_root / "diagnostics"),
            "snapshots": ensure_dir(build_root / "snapshots"),
        }

    def _build_root(self, build: dict[str, Any]) -> Path:
        project_id = build["project_id"]
        build_id = build["id"]
        return self._project_root(project_id) / "builds" / build_id

    def _resolved_build_config(self, build: dict[str, Any]) -> dict[str, Any]:
        """Resolve provider/model config for a build from its parent project."""
        project = self.db.get_project(build["project_id"])
        if project is None:
            raise FileNotFoundError("Project not found.")
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

    def _begin_build_stage(
        self, build_id: str, stage: str, provider: str | None = None,
    ) -> tuple[dict[str, Any], Path, int]:
        build = self.db.get_build(build_id)
        if build is None:
            raise FileNotFoundError("Build not found.")
        project_id = build["project_id"]
        stage_dir = ensure_dir(
            self._build_dirs(project_id, build_id)["runs"] / stage / utc_now().replace(":", "-")
        )
        stdout_path = stage_dir / "stdout.log"
        stderr_path = stage_dir / "stderr.log"
        run_id = self.db.start_build_stage_run(
            build_id, build_id, stage, provider, None,
            str(stage_dir), {"stage": stage}, str(stdout_path), str(stderr_path),
        )
        self.db.update_build(build_id, current_stage=stage)
        return build, stage_dir, run_id

    def _build_stage_blueprint_path(self, build: dict[str, Any], stage: str) -> Path:
        return self._build_root(build) / "drafts" / f"{stage}_blueprints.json"

    # ── build pipeline processing ────────────────────────────────────

    def _process_build(self, build: dict[str, Any]) -> None:
        build_id = build["id"]
        build_type = build.get("build_type", "master")
        start_stage = build.get("queued_from_stage") or (
            "alignment" if build_type == "master" else "translation"
        )
        self.db.update_build(
            build_id,
            board_status="Running",
            pipeline_status="running",
            current_stage=start_stage,
            last_error=None,
        )
        try:
            if build_type == "master":
                self._process_master_build(build_id, start_stage)
            else:
                self._process_localization_build(build_id, start_stage)
        except Exception as exc:
            current = self.db.get_build(build_id) or {}
            self.db.update_build(
                build_id,
                board_status="Needs Attention",
                pipeline_status="failed",
                current_stage=current.get("current_stage", start_stage),
                last_error=str(exc).strip() or exc.__class__.__name__,
            )

    def _process_master_build(self, build_id: str, start_stage: str) -> None:
        runnable = MASTER_RUNNABLE_STAGES
        start_idx = runnable.index(start_stage) if start_stage in runnable else 0

        stage_methods = {
            "alignment": self._run_build_alignment,
            "planning_prep": self._run_build_planning_prep,
            "scene_planning": self._run_build_scene_planning,
            "visual_bible": self._run_build_visual_bible,
            "video_prompt_generation": self._run_build_video_prompt_generation,
            "image_prompt_generation": self._run_build_image_prompt_generation,
        }

        for stage in runnable[start_idx:]:
            stage_methods[stage](build_id)

        self._save_master_scenes(build_id)

        self.db.update_build(
            build_id,
            board_status="Review",
            pipeline_status="review",
            current_stage="review",
            review_ready=1,
            queued_from_stage="alignment",
        )

    def _process_localization_build(self, build_id: str, start_stage: str) -> None:
        stages = LOCALIZATION_RUNNABLE_STAGES
        start_idx = stages.index(start_stage) if start_stage in stages else 0

        for stage in stages[start_idx:]:
            if stage == "translation":
                self._run_build_translation(build_id)
            elif stage == "tts":
                self._run_build_tts(build_id)
                return  # TTS is async — pipeline pauses here
            elif stage == "alignment":
                self._run_build_loc_alignment(build_id)
            elif stage == "localized_timeline":
                self._build_localized_timeline(build_id)

        self.db.update_build(
            build_id,
            board_status="Review",
            pipeline_status="review",
            current_stage="export",
            review_ready=1,
        )

    def _save_master_scenes(self, build_id: str) -> None:
        build = self.db.get_build(build_id)
        if build is None:
            return
        project_id = build["project_id"]
        timeline_path = build.get("timeline_draft_path")
        if not timeline_path or not Path(timeline_path).exists():
            return
        timeline = read_json(Path(timeline_path))
        master_scenes_path = self._project_root(project_id) / "master" / "master_scenes.json"
        ensure_dir(master_scenes_path.parent)
        write_json(master_scenes_path, timeline)
        self.db.update_project(project_id, master_scenes_path=str(master_scenes_path))

    # ── master build stage adapters ──────────────────────────────────

    def _run_build_alignment(self, build_id: str) -> None:
        build, stage_dir, run_id = self._begin_build_stage(build_id, "alignment")
        try:
            output_root = ensure_dir(stage_dir / "artifacts")
            result = run_alignment_job(
                audio_path=Path(build["audio_path"]),
                script_path=Path(build["script_path"]),
                language_code=build["language_code"],
                engine_config=None,
                segmentation_config=None,
                output_root=output_root,
            )
            project_id = build["project_id"]
            review_dir = self._build_dirs(project_id, build_id)["review"]
            final_srt = review_dir / "final.srt"
            shutil.copy2(result.artifacts.final_srt, final_srt)
            report_path = stage_dir / "alignment_report.json"
            segments_path = stage_dir / "segments.json"
            shutil.copy2(result.artifacts.alignment_report, report_path)
            shutil.copy2(result.artifacts.segments_json, segments_path)
            self.db.update_build(
                build_id,
                srt_path=str(final_srt),
                alignment_report_path=str(report_path),
                segments_path=str(segments_path),
            )
            self.db.finish_stage_run(
                run_id,
                status="completed",
                exit_code=0,
                parsed_output_path=str(report_path),
                validation_path=str(report_path),
                stdout_path=str(result.artifacts.run_log),
                stderr_path=None,
                command_payload={
                    "stage": "alignment",
                    "engine_used": result.engine_used,
                    "fallback_used": result.fallback_used,
                    "alignment_options": DEFAULT_ALIGNMENT_OPTIONS,
                },
            )
        except Exception as exc:
            self.db.finish_stage_run(
                run_id, status="failed", exit_code=1, error_text=str(exc),
            )
            raise

    def _run_build_planning_prep(self, build_id: str) -> None:
        build, stage_dir, run_id = self._begin_build_stage(build_id, "planning_prep")
        try:
            srt_path = Path(build["srt_path"])
            cues = parse_srt_text(srt_path.read_text(encoding="utf-8"))
            settings = self._global_settings()
            chunks, manifest_meta = build_planning_chunks(
                cues,
                chunk_seconds=int(settings["planning_chunk_seconds"]),
                overlap_seconds=int(settings["planning_overlap_seconds"]),
            )
            project_id = build["project_id"]
            chunk_dir = ensure_dir(self._build_root(build) / "drafts" / "planning_chunks")
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
            self.db.update_build(build_id, planning_manifest_path=str(manifest_path))
            self.db.finish_stage_run(
                run_id,
                status="completed",
                exit_code=0,
                parsed_output_path=str(manifest_path),
                validation_path=str(manifest_path),
                command_payload={
                    "stage": "planning_prep",
                    "chunk_count": len(chunks),
                    "settings": {
                        "planning_chunk_seconds": settings["planning_chunk_seconds"],
                        "planning_overlap_seconds": settings["planning_overlap_seconds"],
                    },
                },
            )
        except Exception as exc:
            self.db.finish_stage_run(
                run_id, status="failed", exit_code=1, error_text=str(exc),
            )
            raise

    def _run_build_scene_planning(self, build_id: str) -> None:
        build = self.db.get_build(build_id)
        if build is None:
            raise FileNotFoundError("Build not found.")
        config = self._resolved_build_config(build)
        provider = config["scene_planning_provider"]
        build, stage_dir, run_id = self._begin_build_stage(build_id, SCENE_STAGE, provider=provider)
        template_hash: str | None = None
        commands: list[dict[str, Any]] = []
        try:
            manifest = read_json(Path(build["planning_manifest_path"]))
            cues = parse_srt_text(Path(build["srt_path"]).read_text(encoding="utf-8"))
            build_root = self._build_root(build)
            template = self.templates.snapshot_template(build_root, SCENE_STAGE, provider)
            template_hash = template["hash"]
            all_scene_groups: list[list[dict[str, Any]]] = []
            warnings: list[str] = []
            stdout_parts: list[str] = []
            stderr_parts: list[str] = []
            for chunk in manifest["chunks"]:
                chunk_id = int(chunk["chunk_id"])
                chunk_payload = read_json(Path(chunk["json_path"]))
                user_prompt = (
                    "Create scene JSON for this timed subtitle chunk.\n\n"
                    "Rules for this run:\n"
                    "- return ordered, non-overlapping scenes only\n"
                    "- prefer scenes around 6 to 16 seconds\n"
                    "- treat 18 seconds as a soft ceiling unless the text strongly resists splitting\n"
                    "- anchor boundaries to meaningful subtitle cue ranges\n"
                    "- do not decide image versus video here\n"
                    "- make each scene one dominant cinematic beat that can become one image or one continuous shot\n"
                    "- split when the text changes location, time, subject focus, or dramatic action enough that one frame would feel crowded\n"
                    "- do not combine multiple separate events, comparisons, or before/after beats into one scene\n"
                    "- never emit placeholder, gap, or SKIP scenes; absorb tiny gaps into adjacent scenes\n"
                    "- keep boundary scenes conservative because this chunk overlaps neighboring chunks\n\n"
                    f"Chunk metadata:\n{json.dumps(chunk_payload | {'build_id': build_id}, ensure_ascii=False, indent=2)}"
                )
                chunk_dir = ensure_dir(stage_dir / f"chunk-{chunk_id:03d}")
                result = self.cli_runner.run_structured(
                    provider=provider,
                    model=config["scene_planning_model"],
                    system_prompt=template["body"],
                    user_prompt=user_prompt,
                    schema=scene_output_schema(),
                    workdir=build_root,
                    artifact_dir=chunk_dir,
                )
                scene_group, group_warnings = normalize_scene_payload(result["parsed"], chunk_id)
                write_json(chunk_dir / "validated.json", scene_group)
                all_scene_groups.append(scene_group)
                warnings.extend(group_warnings)
                commands.append(result["command_payload"])
                stdout_parts.append(read_text(Path(result["stdout_path"])))
                stderr_parts.append(read_text(Path(result["stderr_path"])))
            timeline, report = merge_scene_chunks(
                all_scene_groups,
                chunk_metadata=manifest["chunks"],
                overlap_seconds=float(manifest.get("overlap_seconds", 0)),
                cues=cues,
            )
            timeline = apply_default_asset_types(timeline, config["leading_video_scene_count"])
            report = validate_timeline(timeline)
            report["warnings"].extend(warnings)
            if report["errors"]:
                raise ValueError("; ".join(report["errors"]))
            timeline_path = write_json(build_root / "review" / "timeline_draft.json", timeline)
            validation_path = write_json(build_root / "diagnostics" / "timeline_validation.json", report)
            stdout_path, stderr_path = self._write_stage_logs(stage_dir, stdout_parts, stderr_parts)
            self.db.update_build(
                build_id,
                timeline_draft_path=str(timeline_path),
                timeline_validation_path=str(validation_path),
            )
            self.db.finish_stage_run(
                run_id,
                status="completed",
                exit_code=0,
                parsed_output_path=str(timeline_path),
                validation_path=str(validation_path),
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                command_payload={
                    "stage": SCENE_STAGE,
                    "provider": provider,
                    "model": config["scene_planning_model"],
                    "template_hash": template_hash,
                    "leading_video_scene_count": config["leading_video_scene_count"],
                    "commands": commands,
                },
            )
        except Exception as exc:
            stdout_parts_err, stderr_parts_err = self._collect_stage_logs(stage_dir)
            stdout_path, stderr_path = self._write_stage_logs(stage_dir, stdout_parts_err, stderr_parts_err)
            self.db.finish_stage_run(
                run_id,
                status="failed",
                exit_code=1,
                error_text=str(exc),
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                command_payload={
                    "stage": SCENE_STAGE,
                    "provider": provider,
                    "model": config["scene_planning_model"],
                    "template_hash": template_hash,
                    "leading_video_scene_count": config["leading_video_scene_count"],
                    "commands": commands,
                },
            )
            raise

    def _run_build_visual_bible(self, build_id: str) -> None:
        build = self.db.get_build(build_id)
        if build is None:
            raise FileNotFoundError("Build not found.")
        config = self._resolved_build_config(build)
        provider = config["visual_bible_provider"]
        build, stage_dir, run_id = self._begin_build_stage(build_id, VISUAL_BIBLE_STAGE, provider=provider)
        template_hash: str | None = None
        context_path: str | None = None
        try:
            source_script = self._load_visual_bible_source_script(build)
            build_root = self._build_root(build)
            template = self.templates.snapshot_template(build_root, VISUAL_BIBLE_STAGE, provider)
            template_hash = template["hash"]
            context_path = str(write_json(
                stage_dir / "input_context.json",
                {"build_id": build_id, "source_script": source_script},
            ))
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
            result = self.cli_runner.run_structured(
                provider=provider,
                model=config["visual_bible_model"],
                system_prompt=template["body"],
                user_prompt=user_prompt,
                schema=visual_bible_output_schema(),
                workdir=build_root,
                artifact_dir=stage_dir,
            )
            normalized, report = normalize_visual_bible(result["parsed"])
            if report["errors"]:
                raise ValueError("; ".join(report["errors"]))
            bible_path = write_json(build_root / "review" / "consistency_guide.json", normalized)
            validation_path = write_json(build_root / "diagnostics" / "consistency_guide_validation.json", report)
            self.db.update_build(
                build_id,
                visual_bible_path=str(bible_path),
                visual_bible_validation_path=str(validation_path),
            )
            self.db.finish_stage_run(
                run_id,
                status="completed",
                exit_code=0,
                parsed_output_path=str(bible_path),
                validation_path=str(validation_path),
                stdout_path=result["stdout_path"],
                stderr_path=result["stderr_path"],
                command_payload={
                    "stage": VISUAL_BIBLE_STAGE,
                    "provider": provider,
                    "model": config["visual_bible_model"],
                    "template_hash": template_hash,
                    "context_path": context_path,
                    "command": result["command_payload"],
                },
            )
        except Exception as exc:
            stdout_parts, stderr_parts = self._collect_stage_logs(stage_dir)
            stdout_path, stderr_path = self._write_stage_logs(stage_dir, stdout_parts, stderr_parts)
            self.db.finish_stage_run(
                run_id,
                status="failed",
                exit_code=1,
                error_text=str(exc),
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                command_payload={
                    "stage": VISUAL_BIBLE_STAGE,
                    "provider": provider,
                    "model": config["visual_bible_model"],
                    "template_hash": template_hash,
                    "context_path": context_path,
                },
            )
            raise

    def _run_build_video_prompt_generation(self, build_id: str) -> None:
        self._run_build_prompt_generation_stage(build_id, VIDEO_PROMPT_STAGE, "video")

    def _run_build_image_prompt_generation(self, build_id: str) -> None:
        self._run_build_prompt_generation_stage(build_id, IMAGE_PROMPT_STAGE, "image")

    def _run_build_prompt_generation_stage(self, build_id: str, stage: str, asset_type: str) -> None:
        build = self.db.get_build(build_id)
        if build is None:
            raise FileNotFoundError("Build not found.")
        config = self._resolved_build_config(build)
        provider = config["video_prompt_provider"] if asset_type == "video" else config["image_prompt_provider"]
        build, stage_dir, run_id = self._begin_build_stage(build_id, stage, provider=provider)
        template_hash: str | None = None
        commands: list[dict[str, Any]] = []
        try:
            timeline = read_json(Path(build["timeline_draft_path"]), default=[])
            if not timeline:
                raise ValueError("Timeline draft is missing.")
            visual_bible_path = Path(build["visual_bible_path"]) if build.get("visual_bible_path") else None
            if not visual_bible_path or not visual_bible_path.exists():
                raise ValueError("Consistency guide is missing.")
            visual_bible = read_json(visual_bible_path)
            scenes = [scene for scene in timeline if scene["asset_type"] == asset_type]
            settings = self._global_settings()
            batches = build_prompt_batches(scenes, batch_size=int(settings["prompt_batch_size"])) if scenes else []
            build_root = self._build_root(build)
            template = self.templates.snapshot_template(build_root, stage, provider)
            template_hash = template["hash"]
            payloads: list[dict[str, Any]] = []
            stdout_parts: list[str] = []
            stderr_parts: list[str] = []
            schema = video_prompt_output_schema() if asset_type == "video" else image_prompt_output_schema()

            for batch_index, batch in enumerate(batches):
                self.db.update_stage_run_command(run_id, {
                    "stage": stage,
                    "provider": provider,
                    "asset_type": asset_type,
                    "model": config["video_prompt_model"] if asset_type == "video" else config["image_prompt_model"],
                    "batch_index": batch_index,
                    "batch_total": len(batches),
                })
                batch_dir = ensure_dir(stage_dir / f"batch-{batch['batch_id']:03d}")
                mode_rules = (
                    "Use the structured JSON fields scene_id, subject, setting, action, camera, look, lighting, rules, character_refs, and prompt."
                    if asset_type == "video"
                    else "Use the structured JSON fields scene_id, subject, setting, composition, look, lighting, rules, character_refs, and prompt."
                )
                target_words = "65 to 95 words" if asset_type == "video" else "45 to 75 words"
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
                result = self.cli_runner.run_structured(
                    provider=provider,
                    model=config["video_prompt_model"] if asset_type == "video" else config["image_prompt_model"],
                    system_prompt=template["body"],
                    user_prompt=user_prompt,
                    schema=schema,
                    workdir=build_root,
                    artifact_dir=batch_dir,
                )
                payloads.append(result["parsed"])
                commands.append(result["command_payload"])
                stdout_parts.append(read_text(Path(result["stdout_path"])))
                stderr_parts.append(read_text(Path(result["stderr_path"])))

            normalized_entries, _ = normalize_prompt_payloads(scenes, payloads or [{"prompts": []}])
            normalized_entries = self._enrich_prompt_entries(normalized_entries, visual_bible)
            prompt_lines, report = validate_prompt_payloads(scenes, [{"prompts": normalized_entries}])
            if report["errors"]:
                raise ValueError("; ".join(report["errors"]))
            for entry, prompt_line in zip(normalized_entries, prompt_lines):
                entry["prompt"] = prompt_line
            stage_output_path = write_json(self._build_stage_blueprint_path(build, stage), normalized_entries)
            stage_validation_path = write_json(build_root / "diagnostics" / f"{stage}_validation.json", report)
            stdout_path, stderr_path = self._write_stage_logs(stage_dir, stdout_parts, stderr_parts)
            self.db.finish_stage_run(
                run_id,
                status="completed",
                exit_code=0,
                parsed_output_path=str(stage_output_path),
                validation_path=str(stage_validation_path),
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                command_payload={
                    "stage": stage,
                    "provider": provider,
                    "asset_type": asset_type,
                    "model": config["video_prompt_model"] if asset_type == "video" else config["image_prompt_model"],
                    "template_hash": template_hash,
                    "commands": commands,
                },
            )
            self._combine_build_prompt_outputs(build_id, raise_on_missing=False)
        except Exception as exc:
            stdout_parts_err, stderr_parts_err = self._collect_stage_logs(stage_dir)
            stdout_path, stderr_path = self._write_stage_logs(stage_dir, stdout_parts_err, stderr_parts_err)
            self.db.finish_stage_run(
                run_id,
                status="failed",
                exit_code=1,
                error_text=str(exc),
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                command_payload={
                    "stage": stage,
                    "provider": provider,
                    "asset_type": asset_type,
                    "model": config["video_prompt_model"] if asset_type == "video" else config["image_prompt_model"],
                    "template_hash": template_hash,
                    "commands": commands,
                },
            )
            raise

    def _combine_build_prompt_outputs(self, build_id: str, *, raise_on_missing: bool) -> None:
        build = self.db.get_build(build_id)
        if build is None:
            raise FileNotFoundError("Build not found.")
        scenes = read_json(Path(build["timeline_draft_path"]), default=[])
        if not scenes:
            raise ValueError("Timeline draft is missing.")
        build_root = self._build_root(build)

        stage_sources = {
            "video": self._build_stage_blueprint_path(build, VIDEO_PROMPT_STAGE),
            "image": self._build_stage_blueprint_path(build, IMAGE_PROMPT_STAGE),
        }
        entry_by_scene_id: dict[str, dict[str, Any]] = {}
        missing_assets: list[str] = []
        for asset_type, source_path in stage_sources.items():
            relevant_scenes = [scene for scene in scenes if scene["asset_type"] == asset_type]
            if not relevant_scenes:
                continue
            if not source_path.exists():
                missing_assets.append(asset_type)
                continue
            stage_entries = read_json(source_path, default=[])
            for entry in stage_entries:
                if isinstance(entry, dict) and entry.get("scene_id"):
                    entry_by_scene_id[entry["scene_id"]] = entry

        if missing_assets:
            if raise_on_missing:
                raise ValueError(f"Missing prompt outputs for: {', '.join(missing_assets)}.")
            return

        ordered_entries: list[dict[str, Any]] = []
        ordered_payloads: list[dict[str, Any]] = []
        for scene in scenes:
            entry = entry_by_scene_id.get(scene["scene_id"])
            if entry is None:
                if raise_on_missing:
                    raise ValueError(f"Missing prompt output for {scene['scene_id']}.")
                return
            updated_entry = dict(entry)
            updated_entry["scene_id"] = scene["scene_id"]
            updated_entry["asset_type"] = scene["asset_type"]
            ordered_entries.append(updated_entry)
            ordered_payloads.append({"scene_id": scene["scene_id"], "prompt": updated_entry.get("prompt", "")})

        prompt_lines, report = validate_prompt_payloads(scenes, [{"prompts": ordered_payloads}])
        if report["errors"]:
            raise ValueError("; ".join(report["errors"]))
        project_id = build["project_id"]
        review_dir = self._build_dirs(project_id, build_id)["review"]
        diagnostics_dir = self._build_dirs(project_id, build_id)["diagnostics"]
        prompt_path = write_text(review_dir / "prompt_list_draft.txt", "\n".join(prompt_lines).strip() + "\n")
        blueprint_path = write_jsonl(review_dir / "prompt_blueprint.jsonl", ordered_entries)
        validation_path = write_json(diagnostics_dir / "prompt_validation.json", report)
        self.db.update_build(
            build_id,
            prompt_list_draft_path=str(prompt_path),
            prompt_blueprint_path=str(blueprint_path),
            prompt_validation_path=str(validation_path),
        )

    # ── localization build stage methods ─────────────────────────────

    def _run_build_translation(self, build_id: str) -> None:
        build, stage_dir, run_id = self._begin_build_stage(build_id, "translation")
        try:
            from .translation import TranslationService

            profile_id = build.get("translation_profile_id")
            if not profile_id:
                raise ValueError("No translation profile assigned. Set translation_profile_id on the build first.")
            profile = self.db.get_translation_profile(profile_id)
            if profile is None:
                raise ValueError(f"Translation profile '{profile_id}' not found.")

            project = self.db.get_project(build["project_id"])
            if project is None:
                raise FileNotFoundError("Project not found.")

            script_path = project.get("script_path")
            if not script_path or not Path(script_path).exists():
                raise ValueError("Source script not found in project.")
            source_script = read_text(Path(script_path))

            # Load master scenes if available
            master_scenes = None
            master_build = self.db.get_master_build(build["project_id"])
            if master_build:
                timeline_path = master_build.get("timeline_draft_path")
                if timeline_path and Path(timeline_path).exists():
                    master_scenes = read_json(Path(timeline_path), default=[])

            source_lang = project.get("source_language", "en")
            target_lang = build.get("language_code", "")
            settings = self._global_settings()
            translation_svc = TranslationService()

            result = asyncio.run(translation_svc.translate_script(
                source_script=source_script,
                source_lang=source_lang,
                target_lang=target_lang,
                provider=profile["provider"],
                api_key=profile["api_key_ref"],
                model=profile["model"],
                master_scenes=master_scenes,
                max_words_per_chunk=settings.get("translation_chunk_max_words", 800),
                context_tail_words=settings.get("translation_context_tail_words", 200),
            ))

            build_workspace = Path(build.get("workspace_dir", ""))
            ensure_dir(build_workspace)
            output_path = build_workspace / f"script_{target_lang}.txt"
            write_text(output_path, result.translated_script)

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
            log_path = build_workspace / f"translation_log_{target_lang}.json"
            write_json(log_path, chunk_log)

            self.db.update_build(
                build_id,
                translation_draft_path=str(output_path),
                translation_chunks_path=str(log_path),
                script_path=str(output_path),
            )
            self.db.finish_stage_run(
                run_id,
                status="completed",
                exit_code=0,
                parsed_output_path=str(output_path),
                command_payload={
                    "stage": "translation",
                    "provider": profile["provider"],
                    "model": profile["model"],
                    "source_lang": source_lang,
                    "target_lang": target_lang,
                    "chunks_total": len(result.chunk_results),
                    "chunks_ok": sum(1 for cr in result.chunk_results if cr.status == "ok"),
                },
            )
        except Exception as exc:
            self.db.finish_stage_run(
                run_id, status="failed", exit_code=1, error_text=str(exc),
            )
            raise

    def _run_build_tts(self, build_id: str) -> None:
        """Submit TTS job and pause pipeline. Does NOT block."""
        build = self.db.get_build(build_id)
        if build is None:
            raise FileNotFoundError("Build not found.")

        from .tts.chunker import chunk_text_for_tts
        from .tts.constants import GENERATE_PRIORITY, map_language_code

        voice_profile_id = build.get("voice_profile_id")
        if not voice_profile_id:
            raise ValueError("No voice profile assigned. Set voice_profile_id on the build first.")
        profile = self.db.get_voice_profile(voice_profile_id)
        if profile is None:
            raise ValueError(f"Voice profile '{voice_profile_id}' not found.")

        script_path = build.get("script_path") or build.get("translation_draft_path")
        if not script_path or not Path(script_path).exists():
            raise ValueError("Translated script not found. Run translation first.")
        script_text = read_text(Path(script_path))

        chunks = chunk_text_for_tts(script_text)
        if not chunks:
            raise ValueError("Script has no text to synthesize.")

        language_code = build.get("language_code", "en")
        xtts_lang = map_language_code(language_code)

        payload = {
            "texts": [c.text for c in chunks],
            "profile_id": voice_profile_id,
            "ref_path": profile.get("audio_path", ""),
            "language": xtts_lang,
            "original_filename": Path(script_path).stem,
        }
        meta = {
            "total_chunks": len(chunks),
            "profile_name": profile.get("name", ""),
            "language": xtts_lang,
            "build_id": build_id,
        }

        self.tts_manager.ensure_worker()
        job_id = self.tts_manager.submit_tts_job(
            job_type="generate",
            profile_id=voice_profile_id,
            payload=payload,
            meta=meta,
            build_id=build_id,
            queue_priority=GENERATE_PRIORITY,
            filename=f"{Path(script_path).stem}_narration.wav",
        )

        self.db.update_build(
            build_id,
            tts_job_id=job_id,
            current_stage="tts",
            pipeline_status="paused_for_tts",
        )

    def _run_build_loc_alignment(self, build_id: str) -> None:
        build, stage_dir, run_id = self._begin_build_stage(build_id, "alignment")
        try:
            audio_path = build.get("audio_path") or build.get("narration_path")
            if not audio_path or not Path(audio_path).exists():
                raise ValueError("Narration audio not found. Complete TTS first.")
            script_path = build.get("script_path") or build.get("translation_draft_path")
            if not script_path or not Path(script_path).exists():
                raise ValueError("Translated script not found.")

            output_root = ensure_dir(stage_dir / "artifacts")
            result = run_alignment_job(
                audio_path=Path(audio_path),
                script_path=Path(script_path),
                language_code=build["language_code"],
                engine_config=None,
                segmentation_config=None,
                output_root=output_root,
            )
            project_id = build["project_id"]
            review_dir = self._build_dirs(project_id, build_id)["review"]
            final_srt = review_dir / "final.srt"
            shutil.copy2(result.artifacts.final_srt, final_srt)
            report_path = stage_dir / "alignment_report.json"
            segments_path = stage_dir / "segments.json"
            shutil.copy2(result.artifacts.alignment_report, report_path)
            shutil.copy2(result.artifacts.segments_json, segments_path)
            self.db.update_build(
                build_id,
                srt_path=str(final_srt),
                alignment_report_path=str(report_path),
                segments_path=str(segments_path),
            )
            self.db.finish_stage_run(
                run_id,
                status="completed",
                exit_code=0,
                parsed_output_path=str(report_path),
                validation_path=str(report_path),
                stdout_path=str(result.artifacts.run_log),
                stderr_path=None,
                command_payload={
                    "stage": "alignment",
                    "engine_used": result.engine_used,
                    "fallback_used": result.fallback_used,
                },
            )
        except Exception as exc:
            self.db.finish_stage_run(
                run_id, status="failed", exit_code=1, error_text=str(exc),
            )
            raise

    def _build_localized_timeline(self, build_id: str) -> None:
        """Build a localized timeline by proportionally mapping master scene timing."""
        build, stage_dir, run_id = self._begin_build_stage(build_id, "localized_timeline")
        try:
            project = self.db.get_project(build["project_id"])
            if project is None:
                raise FileNotFoundError("Project not found.")

            # Load master scenes
            master_scenes_path = project.get("master_scenes_path")
            if not master_scenes_path or not Path(master_scenes_path).exists():
                raise ValueError("Master scenes not found. Complete the master build first.")
            master_scenes = read_json(Path(master_scenes_path))
            if not master_scenes:
                raise ValueError("Master scenes list is empty.")

            # Load localized SRT
            srt_path = build.get("srt_path")
            if not srt_path or not Path(srt_path).exists():
                raise ValueError("Localized SRT not found. Run alignment first.")
            loc_cues = parse_srt_text(Path(srt_path).read_text(encoding="utf-8"))
            if not loc_cues:
                raise ValueError("Localized SRT has no cues.")

            # Compute master total duration
            master_total = max(
                float(s.get("end", 0)) for s in master_scenes
            ) - min(float(s.get("start", 0)) for s in master_scenes)
            if master_total <= 0:
                master_total = 1.0  # safety

            # Localized total duration (cues use start_ms/end_ms in milliseconds)
            loc_total = (max(c.end_ms for c in loc_cues) - min(c.start_ms for c in loc_cues)) / 1000.0
            if loc_total <= 0:
                loc_total = 1.0

            master_start_offset = min(float(s.get("start", 0)) for s in master_scenes)
            loc_start_offset = min(c.start_ms for c in loc_cues) / 1000.0

            # Build cue boundary times for snapping (convert ms to seconds)
            cue_starts = sorted(
                {c.start_ms / 1000.0 for c in loc_cues}
                | {c.end_ms / 1000.0 for c in loc_cues}
            )

            def snap_to_cue(target: float) -> float:
                """Snap to the nearest cue start time."""
                if not cue_starts:
                    return target
                best = cue_starts[0]
                best_dist = abs(target - best)
                for cs in cue_starts:
                    dist = abs(target - cs)
                    if dist < best_dist:
                        best = cs
                        best_dist = dist
                return best

            # Build localized timeline
            localized_timeline: list[dict[str, Any]] = []
            for scene in master_scenes:
                master_scene_start = float(scene.get("start", 0))
                master_scene_end = float(scene.get("end", 0))

                # Proportional mapping
                ratio_start = (master_scene_start - master_start_offset) / master_total
                ratio_end = (master_scene_end - master_start_offset) / master_total

                loc_scene_start = loc_start_offset + ratio_start * loc_total
                loc_scene_end = loc_start_offset + ratio_end * loc_total

                # Snap to cue boundaries
                loc_scene_start = snap_to_cue(loc_scene_start)
                loc_scene_end = snap_to_cue(loc_scene_end)

                # Ensure end > start
                if loc_scene_end <= loc_scene_start:
                    loc_scene_end = loc_scene_start + 0.5

                localized_entry = dict(scene)
                localized_entry["start"] = round(loc_scene_start, 3)
                localized_entry["end"] = round(loc_scene_end, 3)
                localized_entry["duration"] = round(loc_scene_end - loc_scene_start, 3)
                localized_timeline.append(localized_entry)

            # Fix overlaps: each scene's start must be >= previous scene's start
            for i in range(1, len(localized_timeline)):
                if localized_timeline[i]["start"] < localized_timeline[i - 1]["start"]:
                    localized_timeline[i]["start"] = localized_timeline[i - 1]["start"]
                if localized_timeline[i]["start"] >= localized_timeline[i]["end"]:
                    localized_timeline[i]["end"] = localized_timeline[i]["start"] + 0.5
                localized_timeline[i]["duration"] = round(
                    localized_timeline[i]["end"] - localized_timeline[i]["start"], 3
                )

            # Save
            lang = build.get("language_code", "loc")
            project_id = build["project_id"]
            review_dir = self._build_dirs(project_id, build_id)["review"]
            timeline_path = write_json(review_dir / f"timeline_{lang}.json", localized_timeline)

            self.db.update_build(
                build_id,
                timeline_path=str(timeline_path),
                timeline_draft_path=str(timeline_path),
            )
            self.db.finish_stage_run(
                run_id,
                status="completed",
                exit_code=0,
                parsed_output_path=str(timeline_path),
                command_payload={
                    "stage": "localized_timeline",
                    "master_scenes_count": len(master_scenes),
                    "localized_scenes_count": len(localized_timeline),
                    "master_total_duration": master_total,
                    "localized_total_duration": loc_total,
                },
            )
        except Exception as exc:
            self.db.finish_stage_run(
                run_id, status="failed", exit_code=1, error_text=str(exc),
            )
            raise

    def create_project(
        self,
        *,
        title: str,
        source_language: str = "en",
        audio_name: str,
        audio_bytes: bytes,
        script_name: str,
        script_bytes: bytes,
        scene_planning_provider: str | None = None,
        visual_bible_provider: str | None = None,
        video_prompt_provider: str | None = None,
        image_prompt_provider: str | None = None,
        scene_planning_model: str | None = None,
        visual_bible_model: str | None = None,
        video_prompt_model: str | None = None,
        image_prompt_model: str | None = None,
        leading_video_scene_count: int | None = None,
    ) -> dict[str, Any]:
        settings = self._global_settings()
        project_id = make_job_id(title)
        dirs = self._project_dirs(project_id)
        audio_filename = safe_filename(audio_name, "audio")
        script_filename = safe_filename(script_name, "script")
        audio_path = dirs["inputs"] / audio_filename
        script_path = dirs["inputs"] / script_filename
        audio_path.write_bytes(audio_bytes)
        script_path.write_bytes(script_bytes)
        now = utc_now()

        sp = scene_planning_provider or settings["default_scene_planning_provider"]
        vb = visual_bible_provider or settings["default_visual_bible_provider"]
        vp = video_prompt_provider or settings["default_video_prompt_provider"]
        ip = image_prompt_provider or settings["default_image_prompt_provider"]

        self.db.create_project({
            "id": project_id,
            "title": title.strip() or project_id,
            "source_language": source_language,
            "board_status": "Draft",
            "workspace_dir": str(dirs["root"]),
            "audio_filename": audio_filename,
            "script_filename": script_filename,
            "audio_path": str(audio_path),
            "script_path": str(script_path),
            "scene_planning_provider": sp,
            "visual_bible_provider": vb,
            "video_prompt_provider": vp,
            "image_prompt_provider": ip,
            "scene_planning_model": self._resolve_model_choice(sp, scene_planning_model, settings["default_scene_planning_model"]),
            "visual_bible_model": self._resolve_model_choice(vb, visual_bible_model, settings["default_visual_bible_model"]),
            "video_prompt_model": self._resolve_model_choice(vp, video_prompt_model, settings["default_video_prompt_model"]),
            "image_prompt_model": self._resolve_model_choice(ip, image_prompt_model, settings["default_image_prompt_model"]),
            "leading_video_scene_count": int(leading_video_scene_count if leading_video_scene_count is not None else settings["leading_video_scene_count"]),
            "created_at": now,
            "updated_at": now,
        })

        # Auto-create the master build
        self._create_master_build(project_id, source_language, str(audio_path), str(script_path))

        return self.get_project_detail(project_id)

    def _create_master_build(
        self,
        project_id: str,
        language_code: str,
        audio_path: str,
        script_path: str,
    ) -> str:
        build_id = f"{project_id}-master"
        dirs = self._build_dirs(project_id, build_id)
        now = utc_now()
        self.db.create_build({
            "id": build_id,
            "project_id": project_id,
            "build_type": "master",
            "language_code": language_code,
            "board_status": "Draft",
            "pipeline_status": "idle",
            "current_stage": "draft",
            "queued_from_stage": "alignment",
            "audio_path": audio_path,
            "script_path": script_path,
            "workspace_dir": str(dirs["root"]),
            "created_at": now,
            "updated_at": now,
        })
        return build_id

    def create_localization_build(
        self,
        project_id: str,
        *,
        target_language: str,
        voice_profile_id: str | None = None,
        translation_profile_id: str | None = None,
    ) -> dict[str, Any]:
        project = self.db.get_project(project_id)
        if project is None:
            raise FileNotFoundError("Project not found.")
        # Verify master build is done
        master = self.db.get_master_build(project_id)
        if not master or master.get("pipeline_status") not in ("review", "done"):
            raise ValueError("Master build must be completed before creating localizations.")
        # Check for duplicate language
        existing = self.db.list_localization_builds(project_id)
        for build in existing:
            if build.get("language_code") == target_language:
                raise ValueError(f"Localization for '{target_language}' already exists.")

        build_id = f"{project_id}-{target_language}"
        dirs = self._build_dirs(project_id, build_id)
        now = utc_now()
        self.db.create_build({
            "id": build_id,
            "project_id": project_id,
            "build_type": "localization",
            "language_code": target_language,
            "board_status": "Draft",
            "pipeline_status": "idle",
            "current_stage": "translation",
            "queued_from_stage": "translation",
            "translation_profile_id": translation_profile_id,
            "voice_profile_id": voice_profile_id,
            "workspace_dir": str(dirs["root"]),
            "created_at": now,
            "updated_at": now,
        })
        return self.get_build_detail(build_id)

    def list_projects(self) -> list[dict[str, Any]]:
        projects = self.db.list_projects()
        for project in projects:
            builds = self.db.list_builds(project["id"])
            master = next((b for b in builds if b["build_type"] == "master"), None)
            localizations = [b for b in builds if b["build_type"] == "localization"]
            project["master_build"] = master
            project["localization_count"] = len(localizations)
            project["localizations_summary"] = [
                {
                    "id": b["id"],
                    "language_code": b["language_code"],
                    "pipeline_status": b["pipeline_status"],
                    "board_status": b["board_status"],
                }
                for b in localizations
            ]
        return projects

    def get_project_detail(self, project_id: str) -> dict[str, Any]:
        project = self.db.get_project(project_id)
        if project is None:
            raise FileNotFoundError("Project not found.")
        builds = self.db.list_builds(project_id)
        master = next((b for b in builds if b["build_type"] == "master"), None)
        localizations = [b for b in builds if b["build_type"] == "localization"]
        return {
            "project": project,
            "master_build": master,
            "localizations": localizations,
        }

    def get_build_detail(self, build_id: str) -> dict[str, Any]:
        build = self.db.get_build(build_id)
        if build is None:
            raise FileNotFoundError("Build not found.")
        stage_runs = self.db.list_build_stage_runs(build_id)
        return {
            "build": build,
            "stage_runs": stage_runs,
        }

    def delete_project(self, project_id: str) -> dict[str, Any]:
        project = self.db.get_project(project_id)
        if project is None:
            raise FileNotFoundError("Project not found.")
        workspace_dir = Path(project.get("workspace_dir") or self._project_root(project_id))
        self.db.delete_project(project_id)
        self._safe_delete_path(workspace_dir, EPISODES_ROOT)
        return {"deleted": True, "project_id": project_id}

    def delete_build_record(self, build_id: str) -> dict[str, Any]:
        build = self.db.get_build(build_id)
        if build is None:
            raise FileNotFoundError("Build not found.")
        if build.get("pipeline_status") == "running":
            raise ValueError("Build is running. Wait for it to finish before deleting.")
        workspace_dir = Path(build.get("workspace_dir") or "")
        self.db.delete_build(build_id)
        if workspace_dir.exists():
            self._safe_delete_path(workspace_dir, EPISODES_ROOT)
        return {"deleted": True, "build_id": build_id}

    def queue_build(self, build_id: str, start_stage: str | None = None) -> dict[str, Any]:
        build = self.db.get_build(build_id)
        if build is None:
            raise FileNotFoundError("Build not found.")
        build_type = build.get("build_type", "master")
        valid_stages = MASTER_RUNNABLE_STAGES if build_type == "master" else LOCALIZATION_RUNNABLE_STAGES
        if start_stage is None:
            start_stage = valid_stages[0]
        if start_stage not in valid_stages:
            raise ValueError(f"Invalid start stage '{start_stage}' for {build_type} build.")
        self.db.update_build(
            build_id,
            board_status="Queued",
            pipeline_status="queued",
            current_stage=start_stage,
            queued_from_stage=start_stage,
            last_error=None,
            review_ready=0,
        )
        with self._condition:
            self._condition.notify_all()
        return self.get_build_detail(build_id)

    def get_target_languages(self) -> list[dict[str, str]]:
        return list(TARGET_LANGUAGES)

    # ── voice profile management ────────────────────────────────────

    def list_voice_profiles(self) -> list[dict[str, Any]]:
        return self.db.list_voice_profiles()

    def get_voice_profile(self, profile_id: str) -> dict[str, Any]:
        profile = self.db.get_voice_profile(profile_id)
        if profile is None:
            raise FileNotFoundError("Voice profile not found.")
        return profile

    # ── translation profile management ──────────────────────────────

    def list_translation_profiles(self) -> list[dict[str, Any]]:
        return self.db.list_translation_profiles()

    def get_translation_profile(self, profile_id: str) -> dict[str, Any]:
        profile = self.db.get_translation_profile(profile_id)
        if profile is None:
            raise FileNotFoundError("Translation profile not found.")
        return profile

    def create_translation_profile(
        self,
        *,
        name: str,
        provider: str,
        api_key: str,
        model: str,
    ) -> dict[str, Any]:
        from .config import TRANSLATION_PROVIDERS
        if provider not in TRANSLATION_PROVIDERS:
            raise ValueError(f"Invalid provider. Must be one of: {', '.join(TRANSLATION_PROVIDERS)}")
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
            self.db.update_translation_profile(profile_id, **filtered)
        return self.db.get_translation_profile(profile_id) or {}

    def delete_translation_profile(self, profile_id: str) -> dict[str, Any]:
        profile = self.db.get_translation_profile(profile_id)
        if profile is None:
            raise FileNotFoundError("Translation profile not found.")
        self.db.delete_translation_profile(profile_id)
        return {"deleted": True, "id": profile_id}

    # ── translation execution ───────────────────────────────────────

    def translate_build(
        self,
        build_id: str,
        translation_profile_id: str,
    ) -> dict[str, Any]:
        """Run translation for a localization build (synchronous wrapper)."""
        from .translation import TranslationService

        build = self.db.get_build(build_id)
        if build is None:
            raise FileNotFoundError("Build not found.")
        if build.get("build_type") != "localization":
            raise ValueError("Only localization builds can be translated.")

        project = self.db.get_project(build["project_id"])
        if project is None:
            raise FileNotFoundError("Project not found.")

        profile = self.db.get_translation_profile(translation_profile_id)
        if profile is None:
            raise FileNotFoundError("Translation profile not found.")

        # Load source script
        script_path = project.get("script_path")
        if not script_path or not Path(script_path).exists():
            raise ValueError("Source script not found in project.")
        source_script = read_text(Path(script_path))

        # Load master scenes if available
        master_scenes = None
        master_build = self.db.get_master_build(build["project_id"])
        if master_build:
            timeline_path = master_build.get("timeline_draft_path")
            if timeline_path and Path(timeline_path).exists():
                master_scenes = read_json(Path(timeline_path), default=[])

        # Determine language names
        source_lang = project.get("source_language", "en")
        target_lang = build.get("language_code", "")

        settings = self._global_settings()
        translation_svc = TranslationService()

        # Run the async translation in a sync context
        result = asyncio.run(translation_svc.translate_script(
            source_script=source_script,
            source_lang=source_lang,
            target_lang=target_lang,
            provider=profile["provider"],
            api_key=profile["api_key_ref"],
            model=profile["model"],
            master_scenes=master_scenes,
            max_words_per_chunk=settings.get("translation_chunk_max_words", 800),
            context_tail_words=settings.get("translation_context_tail_words", 200),
        ))

        # Save translated script
        build_workspace = Path(build.get("workspace_dir", ""))
        ensure_dir(build_workspace)
        output_path = build_workspace / f"script_{target_lang}.txt"
        write_text(output_path, result.translated_script)

        # Save chunk log
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
        log_path = build_workspace / f"translation_log_{target_lang}.json"
        write_json(log_path, chunk_log)

        # Update build
        self.db.update_build(
            build_id,
            translation_draft_path=str(output_path),
            translation_chunks_path=str(log_path),
            script_path=str(output_path),
            current_stage="translation",
        )

        return {
            "build_id": build_id,
            "language_code": target_lang,
            "status": result.status,
            "output_path": str(output_path),
            "chunks_total": len(result.chunk_results),
            "chunks_ok": sum(1 for cr in result.chunk_results if cr.status == "ok"),
        }

    # ── voice profile management (full CRUD) ─────────────────────────

    def create_voice_profile(
        self,
        *,
        name: str,
        language_code: str,
        audio_bytes: bytes,
        audio_filename: str,
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
            "language_code": language_code,
            "audio_file": audio_file,
            "audio_path": str(audio_path),
            "latents_path": None,
            "has_latents": 0,
            "created_at": now,
            "updated_at": now,
        })

        # Queue latent precompute job
        job_id = self.tts_manager.submit_tts_job(
            job_type="latent_precompute",
            profile_id=profile_id,
            payload={"profile_id": profile_id, "audio_path": str(audio_path)},
            queue_priority=LATENT_PRIORITY,
        )

        profile = self.db.get_voice_profile(profile_id) or {}
        profile["latent_job_id"] = job_id
        return profile

    def update_voice_profile(self, profile_id: str, **fields: Any) -> dict[str, Any]:
        profile = self.db.get_voice_profile(profile_id)
        if profile is None:
            raise FileNotFoundError("Voice profile not found.")
        allowed = {"name", "language_code"}
        filtered = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if filtered:
            self.db.update_voice_profile(profile_id, **filtered)
        return self.db.get_voice_profile(profile_id) or {}

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

    # ── TTS execution ────────────────────────────────────────────────

    def submit_build_tts(
        self,
        build_id: str,
        voice_profile_id: str,
    ) -> dict[str, Any]:
        """Submit a TTS generation job for a localization build."""
        from .tts.chunker import chunk_text_for_tts
        from .tts.constants import GENERATE_PRIORITY, map_language_code

        build = self.db.get_build(build_id)
        if build is None:
            raise FileNotFoundError("Build not found.")
        if build.get("build_type") != "localization":
            raise ValueError("Only localization builds support TTS.")

        profile = self.db.get_voice_profile(voice_profile_id)
        if profile is None:
            raise FileNotFoundError("Voice profile not found.")

        # Load translated script
        script_path = build.get("script_path") or build.get("translation_draft_path")
        if not script_path or not Path(script_path).exists():
            raise ValueError("Translated script not found. Run translation first.")
        script_text = read_text(Path(script_path))

        # Chunk text for TTS
        chunks = chunk_text_for_tts(script_text)
        if not chunks:
            raise ValueError("Script has no text to synthesize.")

        # Map language
        language_code = build.get("language_code", "en")
        xtts_lang = map_language_code(language_code)

        # Build payload
        payload = {
            "texts": [c.text for c in chunks],
            "profile_id": voice_profile_id,
            "ref_path": profile.get("audio_path", ""),
            "language": xtts_lang,
            "original_filename": Path(script_path).stem,
        }
        meta = {
            "total_chunks": len(chunks),
            "profile_name": profile.get("name", ""),
            "language": xtts_lang,
            "build_id": build_id,
        }

        self.tts_manager.ensure_worker()
        job_id = self.tts_manager.submit_tts_job(
            job_type="generate",
            profile_id=voice_profile_id,
            payload=payload,
            meta=meta,
            build_id=build_id,
            queue_priority=GENERATE_PRIORITY,
            filename=f"{Path(script_path).stem}_narration.wav",
        )

        # Mark build as paused for TTS
        self.db.update_build(
            build_id,
            tts_job_id=job_id,
            pipeline_status="paused_for_tts",
        )

        return {
            "build_id": build_id,
            "tts_job_id": job_id,
            "chunks": len(chunks),
            "language": xtts_lang,
            "status": "queued",
        }

    def submit_voice_test(
        self,
        profile_id: str,
        text: str,
        language: str = "en",
    ) -> dict[str, Any]:
        """Submit a quick voice test TTS job."""
        from .tts.constants import TEST_VOICE_PRIORITY, map_language_code

        profile = self.db.get_voice_profile(profile_id)
        if profile is None:
            raise FileNotFoundError("Voice profile not found.")

        xtts_lang = map_language_code(language)
        payload = {
            "profile_id": profile_id,
            "ref_path": profile.get("audio_path", ""),
            "text": text,
            "language": xtts_lang,
        }

        self.tts_manager.ensure_worker()
        job_id = self.tts_manager.submit_tts_job(
            job_type="test_voice",
            profile_id=profile_id,
            payload=payload,
            queue_priority=TEST_VOICE_PRIORITY,
        )
        return {"job_id": job_id, "status": "queued"}

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

    def get_worker_health(self) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(self.tts_manager.get_worker_health())

    # ── TTS pipeline integration ─────────────────────────────────────

    def _check_paused_tts_builds(self) -> None:
        """Check builds paused for TTS and resume if their job completed."""
        builds = self.db.list_paused_tts_builds()
        for build in builds:
            tts_job_id = build.get("tts_job_id")
            if not tts_job_id:
                continue

            job = self.db.get_tts_job(tts_job_id)
            if job is None:
                continue

            status = job.get("status", "")
            build_id = build["id"]

            if status == "completed":
                result_path = job.get("result_path", "")
                self.db.update_build(
                    build_id,
                    narration_path=result_path,
                    audio_path=result_path,
                    board_status="Queued",
                    pipeline_status="queued",
                    current_stage="alignment",
                    queued_from_stage="alignment",
                )
            elif status == "error":
                error_msg = job.get("error_message", "TTS job failed.")
                self.db.update_build(
                    build_id,
                    board_status="Needs Attention",
                    pipeline_status="failed",
                    last_error=error_msg,
                )
            elif status == "canceled":
                self.db.update_build(
                    build_id,
                    pipeline_status="idle",
                    last_error="TTS job was canceled.",
                )

    # ── Niche project + Episode pipeline (Phase 6) ────────────────────

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
        self.db.create_project({
            "id": project_id,
            "title": name,
            "source_language": master_language,
            "board_status": "Draft",
            "workspace_dir": str(workspace),
            "master_language": master_language,
            "configured_languages": json.dumps(langs),
            "language_voice_profiles": json.dumps(language_voice_profiles or {}),
            "language_translation_profiles": json.dumps(language_translation_profiles or {}),
            "is_niche": 1,
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
        return {"project": self.db.get_project(project_id)}

    def list_niche_projects(self) -> list[dict[str, Any]]:
        projects = self.db.list_niche_projects()
        for p in projects:
            p["configured_languages"] = json.loads(p.get("configured_languages") or "[]")
            p["language_voice_profiles"] = json.loads(p.get("language_voice_profiles") or "{}")
            p["language_translation_profiles"] = json.loads(p.get("language_translation_profiles") or "{}")
            p["episode_count"] = len(self.db.list_episodes(p["id"]))
        return projects

    def get_niche_project_detail(self, project_id: str) -> dict[str, Any]:
        project = self.db.get_project(project_id)
        if project is None:
            raise FileNotFoundError("Niche project not found.")
        project["configured_languages"] = json.loads(project.get("configured_languages") or "[]")
        project["language_voice_profiles"] = json.loads(project.get("language_voice_profiles") or "{}")
        project["language_translation_profiles"] = json.loads(project.get("language_translation_profiles") or "{}")
        episodes = self.db.list_episodes(project_id)
        return {"project": project, "episodes": episodes}

    def update_niche_project(
        self,
        project_id: str,
        **fields: Any,
    ) -> dict[str, Any]:
        project = self.db.get_project(project_id)
        if project is None:
            raise FileNotFoundError("Niche project not found.")
        # Serialize JSON fields
        for key in ("configured_languages", "language_voice_profiles", "language_translation_profiles"):
            if key in fields and not isinstance(fields[key], str):
                fields[key] = json.dumps(fields[key])
        fields["updated_at"] = utc_now()
        self.db.update_project(project_id, **fields)
        return {"updated": True}

    def submit_episode(
        self,
        niche_project_id: str,
        *,
        title: str,
        script_text: str,
    ) -> dict[str, Any]:
        """Submit a script to a niche project. Creates episode + language status rows."""
        project = self.db.get_project(niche_project_id)
        if project is None:
            raise FileNotFoundError("Niche project not found.")

        now = utc_now()
        episode_id = f"ep-{make_job_id(title)}"
        master_lang = project.get("master_language") or project.get("source_language") or "en"
        configured_langs = json.loads(project.get("configured_languages") or "[]")
        if not configured_langs:
            configured_langs = [master_lang]

        workspace = Path(project["workspace_dir"]) / episode_id
        ensure_dir(workspace)

        # Save script to workspace
        script_path = workspace / "script_original.txt"
        write_text(script_path, script_text)

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
                "updated_at": now,
            })

        return {"episode": self.db.get_episode(episode_id)}

    def get_episode_detail(self, episode_id: str) -> dict[str, Any]:
        episode = self.db.get_episode(episode_id)
        if episode is None:
            raise FileNotFoundError("Episode not found.")
        lang_statuses = self.db.get_episode_language_statuses(episode_id)
        stage_runs = self.db.list_build_stage_runs(episode_id)
        return {
            "episode": episode,
            "language_statuses": lang_statuses,
            "stage_runs": stage_runs,
        }

    def queue_episode(self, episode_id: str, start_stage: str | None = None) -> dict[str, Any]:
        episode = self.db.get_episode(episode_id)
        if episode is None:
            raise FileNotFoundError("Episode not found.")
        stage = start_stage or episode.get("queued_from_stage") or "consistency_guide"
        if stage not in EPISODE_RUNNABLE_STAGES:
            raise ValueError(f"Invalid start stage: {stage}")
        self.db.update_episode(
            episode_id,
            board_status="Queued",
            pipeline_status="queued",
            current_stage=stage,
            queued_from_stage=stage,
            last_error=None,
            updated_at=utc_now(),
        )
        with self._condition:
            self._condition.notify()
        return {"queued": True, "start_stage": stage}

    def list_all_episodes_for_board(self) -> list[dict[str, Any]]:
        """Return all episodes with niche project title and per-language progress."""
        episodes = self.db.list_all_episodes_for_board()
        for ep in episodes:
            ep["configured_languages"] = json.loads(ep.get("configured_languages") or "[]")
            lang_statuses = self.db.get_episode_language_statuses(ep["id"])
            ep["language_statuses"] = lang_statuses
        return episodes

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

    def _process_episode(self, episode: dict[str, Any]) -> None:
        """Process an episode through the unified TTS-first pipeline. All steps sequential."""
        episode_id = episode["id"]
        start_stage = episode.get("queued_from_stage") or "consistency_guide"
        self.db.update_episode(episode_id, board_status="Running", pipeline_status="running")

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

            # All stages complete
            self.db.update_episode(
                episode_id,
                board_status="Review",
                pipeline_status="review",
                current_stage="review",
                review_ready=1,
                updated_at=utc_now(),
            )
        except Exception as exc:
            self.db.update_episode(
                episode_id,
                board_status="Needs Attention",
                pipeline_status="failed",
                last_error=str(exc)[:500],
                updated_at=utc_now(),
            )

    # ── Episode pipeline helpers ────────────────────────────────────────

    def _resolved_episode_config(self, episode: dict[str, Any]) -> dict[str, Any]:
        """Resolve provider/model config for an episode from its niche project."""
        project = self.db.get_project(episode["niche_project_id"])
        if project is None:
            raise FileNotFoundError("Niche project not found.")
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
        result = self.cli_runner.run_structured(
            provider=provider,
            model=config["visual_bible_model"],
            system_prompt=template["body"],
            user_prompt=user_prompt,
            schema=visual_bible_output_schema(),
            workdir=workspace,
            artifact_dir=artifact_dir,
        )
        normalized, report = normalize_visual_bible(result["parsed"])
        if report["errors"]:
            raise ValueError("; ".join(report["errors"]))
        guide_path = write_json(workspace / "consistency_guide.json", normalized)
        write_json(workspace / "consistency_guide_validation.json", report)
        self.db.update_episode(episode_id, consistency_guide_path=str(guide_path), updated_at=utc_now())

    def _episode_run_translations(self, episode_id: str) -> None:
        """Run translation for each non-master language, one at a time."""
        from .translation import TranslationService

        episode = self.db.get_episode(episode_id)
        master_lang = episode["master_language"]
        langs = json.loads(episode.get("configured_languages") or "[]")
        workspace = self._episode_workspace(episode)
        project = self.db.get_project(episode["niche_project_id"])
        translation_profiles = json.loads(project.get("language_translation_profiles") or "{}")
        settings = self._global_settings()
        source_script = episode["script_text"]

        # Set master language script path
        master_script_path = workspace / "script_original.txt"
        if not master_script_path.exists():
            write_text(master_script_path, source_script)
        self.db.update_episode_language_status(
            episode_id, master_lang,
            translation_status="done",
            script_path=str(master_script_path),
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
                ))
                translated_path = workspace / f"script_{lang}.txt"
                write_text(translated_path, result.translated_script)

                # Save translation log
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

                self.db.update_episode_language_status(
                    episode_id, lang,
                    translation_status="done",
                    script_path=str(translated_path),
                )
            except Exception as exc:
                failed_count += 1
                self.db.update_episode_language_status(
                    episode_id, lang,
                    translation_status="failed",
                    error_message=str(exc)[:500],
                )

        if failed_count == len(non_master_langs) and non_master_langs:
            raise RuntimeError("All translations failed.")

    def _episode_run_tts_all(self, episode_id: str) -> None:
        """Run TTS for each language, one at a time. Pauses pipeline via async TTS worker."""
        episode = self.db.get_episode(episode_id)
        langs = json.loads(episode.get("configured_languages") or "[]")
        project = self.db.get_project(episode["niche_project_id"])
        voice_profiles = json.loads(project.get("language_voice_profiles") or "{}")
        workspace = Path(episode["workspace_dir"])

        # Process each language sequentially
        for lang in langs:
            profile_id = voice_profiles.get(lang)
            if not profile_id:
                # Skip languages without voice profile
                self.db.update_episode_language_status(
                    episode_id, lang, tts_status="skipped",
                    error_message="No voice profile configured",
                )
                continue

            lang_status = self.db.get_episode_language_status(episode_id, lang)
            if lang_status and lang_status.get("tts_status") == "done":
                continue  # Already done (resuming pipeline)

            self.db.update_episode_language_status(episode_id, lang, tts_status="running")

            # Get script text for this language
            if lang == episode["master_language"]:
                script_text = episode["script_text"]
            else:
                lang_status_row = self.db.get_episode_language_status(episode_id, lang)
                script_path = lang_status_row.get("script_path") if lang_status_row else None
                if script_path and Path(script_path).exists():
                    script_text = read_text(Path(script_path))
                else:
                    script_text = episode["script_text"]

            # Submit TTS job
            from .tts.manager import TTSManager
            tts_mgr = TTSManager(self.db)
            job_id = tts_mgr.submit_tts_job(
                job_type="generate",
                profile_id=profile_id,
                payload={"texts": [script_text]},
                build_id=episode_id,
                filename=f"narration_{lang}.wav",
            )
            self.db.update_episode_language_status(episode_id, lang, tts_job_id=job_id)

            # Pause pipeline — TTS worker will process async
            self.db.update_episode(
                episode_id,
                pipeline_status="paused_for_tts",
                updated_at=utc_now(),
            )
            return  # Exit — pipeline resumes when TTS completes

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
            script_path = lang_status.get("script_path")
            if not audio_path or not Path(audio_path).exists():
                self.db.update_episode_language_status(
                    episode_id, lang, srt_status="skipped",
                    error_message="No TTS audio available",
                )
                continue
            if not script_path or not Path(script_path).exists():
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
                    script_path=Path(script_path),
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
            result = self.cli_runner.run_structured(
                provider=provider,
                model=config["scene_planning_model"],
                system_prompt=template["body"],
                user_prompt=user_prompt,
                schema=scene_output_schema(),
                workdir=workspace,
                artifact_dir=chunk_dir,
            )
            scene_group, group_warnings = normalize_scene_payload(result["parsed"], chunk_id)
            write_json(chunk_dir / "validated.json", scene_group)
            all_scene_groups.append(scene_group)
            warnings.extend(group_warnings)

        timeline, report = merge_scene_chunks(
            all_scene_groups,
            chunk_metadata=manifest["chunks"],
            overlap_seconds=float(manifest.get("overlap_seconds", 0)),
            cues=cues,
        )
        timeline = apply_default_asset_types(timeline, config["leading_video_scene_count"])
        report = validate_timeline(timeline)
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
        batches = build_prompt_batches(scenes, batch_size=int(settings["prompt_batch_size"]))
        template = self.templates.snapshot_template(workspace, stage, provider)
        schema = video_prompt_output_schema() if asset_type == "video" else image_prompt_output_schema()
        payloads: list[dict[str, Any]] = []

        for batch_index, batch in enumerate(batches):
            mode_rules = (
                "Use the structured JSON fields scene_id, subject, setting, action, camera, look, lighting, rules, character_refs, and prompt."
                if asset_type == "video"
                else "Use the structured JSON fields scene_id, subject, setting, composition, look, lighting, rules, character_refs, and prompt."
            )
            target_words = "65 to 95 words" if asset_type == "video" else "45 to 75 words"
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
            batch_dir = ensure_dir(workspace / "runs" / stage / f"batch-{batch['batch_id']:03d}")
            result = self.cli_runner.run_structured(
                provider=provider,
                model=model,
                system_prompt=template["body"],
                user_prompt=user_prompt,
                schema=schema,
                workdir=workspace,
                artifact_dir=batch_dir,
            )
            payloads.append(result["parsed"])

        normalized_entries, _ = normalize_prompt_payloads(scenes, payloads or [{"prompts": []}])
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
        episode = self.db.get_episode(episode_id)
        langs = json.loads(episode.get("configured_languages") or "[]")
        workspace = self._episode_workspace(episode)
        master_lang = episode["master_language"]

        # Load master timeline
        timeline_path = episode.get("timeline_draft_path")
        if not timeline_path or not Path(timeline_path).exists():
            raise ValueError("Master timeline draft is missing.")
        master_scenes = read_json(Path(timeline_path), default=[])
        if not master_scenes:
            raise ValueError("Master timeline is empty.")

        # Load master SRT cues for timing reference
        master_status = self.db.get_episode_language_status(episode_id, master_lang)
        master_srt_path = master_status.get("srt_path") if master_status else None
        if not master_srt_path or not Path(master_srt_path).exists():
            raise ValueError("Master SRT not available.")
        master_cues = parse_srt_text(Path(master_srt_path).read_text(encoding="utf-8"))
        master_total = master_cues[-1].end_ms / 1000.0 if master_cues else 0.0

        failed_count = 0
        attempted = 0

        for lang in langs:
            lang_status = self.db.get_episode_language_status(episode_id, lang)
            if not lang_status or lang_status.get("timeline_status") == "done":
                continue

            self.db.update_episode_language_status(episode_id, lang, timeline_status="running")
            attempted += 1

            try:
                if lang == master_lang:
                    # Master language: just copy timeline as-is
                    lang_timeline_path = workspace / f"timeline_{lang}.json"
                    write_json(lang_timeline_path, master_scenes)
                    self.db.update_episode_language_status(
                        episode_id, lang,
                        timeline_status="done",
                        timeline_path=str(lang_timeline_path),
                    )
                    continue

                # Get language SRT for timing
                lang_srt_path = lang_status.get("srt_path")
                if not lang_srt_path or not Path(lang_srt_path).exists():
                    self.db.update_episode_language_status(
                        episode_id, lang,
                        timeline_status="skipped",
                        error_message="No SRT available for timing",
                    )
                    attempted -= 1  # Don't count as failed
                    continue

                lang_cues = parse_srt_text(Path(lang_srt_path).read_text(encoding="utf-8"))
                lang_total = lang_cues[-1].end_ms / 1000.0 if lang_cues else 0.0

                if master_total <= 0 or lang_total <= 0:
                    self.db.update_episode_language_status(
                        episode_id, lang,
                        timeline_status="skipped",
                        error_message="Zero-duration SRT",
                    )
                    attempted -= 1
                    continue

                ratio = lang_total / master_total

                # Build cue boundary list for snapping
                lang_boundaries = sorted({c.start_ms / 1000.0 for c in lang_cues} | {c.end_ms / 1000.0 for c in lang_cues})

                def snap_to_boundary(t: float) -> float:
                    """Snap a time to the nearest language cue boundary."""
                    if not lang_boundaries:
                        return t
                    closest = min(lang_boundaries, key=lambda b: abs(b - t))
                    # Only snap if within 0.5s
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

                    # Ensure no overlap with previous scene
                    if lang_scenes and mapped_start < lang_scenes[-1]["end"]:
                        mapped_start = lang_scenes[-1]["end"]

                    # Ensure minimum 1s duration
                    if mapped_end - mapped_start < 1.0:
                        mapped_end = mapped_start + 1.0

                    # Clamp last scene to lang total
                    if i == len(master_scenes) - 1:
                        mapped_end = lang_total

                    mapped["start"] = round(mapped_start, 3)
                    mapped["end"] = round(mapped_end, 3)
                    lang_scenes.append(mapped)

                lang_timeline_path = workspace / f"timeline_{lang}.json"
                write_json(lang_timeline_path, lang_scenes)
                self.db.update_episode_language_status(
                    episode_id, lang,
                    timeline_status="done",
                    timeline_path=str(lang_timeline_path),
                )
            except Exception as exc:
                failed_count += 1
                self.db.update_episode_language_status(
                    episode_id, lang,
                    timeline_status="failed",
                    error_message=str(exc)[:500],
                )

        if failed_count == attempted and attempted > 0:
            raise RuntimeError("All timeline mappings failed.")

    def _check_paused_tts_episodes(self) -> None:
        """Check if any paused episodes have completed TTS and can resume."""
        paused = self.db.list_paused_tts_episodes()
        for episode in paused:
            episode_id = episode["id"]
            lang_statuses = self.db.get_episode_language_statuses(episode_id)
            langs = json.loads(episode.get("configured_languages") or "[]")

            # Check if all TTS jobs are done
            all_tts_done = True
            any_tts_failed = False
            for ls in lang_statuses:
                if ls["tts_status"] == "running":
                    # Check the TTS job status
                    tts_job_id = ls.get("tts_job_id")
                    if tts_job_id:
                        job = self.db.get_tts_job(tts_job_id)
                        if job:
                            if job["status"] == "completed":
                                self.db.update_episode_language_status(
                                    episode_id, ls["language_code"],
                                    tts_status="done",
                                    tts_audio_path=job.get("result_path", ""),
                                )
                            elif job["status"] in ("error", "failed"):
                                self.db.update_episode_language_status(
                                    episode_id, ls["language_code"],
                                    tts_status="failed",
                                    error_message=job.get("error_message", "TTS failed"),
                                )
                                any_tts_failed = True
                            else:
                                all_tts_done = False
                    else:
                        all_tts_done = False
                elif ls["tts_status"] in ("pending", "running"):
                    all_tts_done = False
                elif ls["tts_status"] == "failed":
                    any_tts_failed = True

            if any_tts_failed:
                self.db.update_episode(
                    episode_id,
                    board_status="Needs Attention",
                    pipeline_status="failed",
                    last_error="One or more TTS jobs failed.",
                )
            elif all_tts_done:
                # Resume pipeline at alignment
                self.db.update_episode(
                    episode_id,
                    board_status="Queued",
                    pipeline_status="queued",
                    current_stage="alignment",
                    queued_from_stage="alignment",
                    updated_at=utc_now(),
                )
