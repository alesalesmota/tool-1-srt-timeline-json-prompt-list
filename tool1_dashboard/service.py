from __future__ import annotations

import json
import re
import shutil
import threading
from pathlib import Path
from typing import Any

from .alignment_tool.config import LANGUAGE_PROFILES
from .alignment_tool.mfa_resources import mfa_resource_status, prepare_mfa_language_resources_async
from .alignment_tool.orchestrator import run_alignment_job
from .alignment_tool.runtime import probe_health as alignment_health
from .chunking import build_planning_chunks, build_prompt_batches
from .config import (
    AGENTS_ROOT,
    BOARD_STATUSES,
    DEFAULT_ALIGNMENT_OPTIONS,
    DEFAULT_SETTINGS,
    IMAGE_PROMPT_STAGE,
    MAX_PREVIEW_CHARS,
    PROVIDERS,
    RUNNABLE_STAGES,
    SCENE_STAGE,
    VIDEO_PROMPT_STAGE,
    VIDEOS_ROOT,
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
        ensure_dir(VIDEOS_ROOT)

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
            if job is None:
                with self._condition:
                    self._condition.wait(timeout=1.0)
                continue
            self._process_job(job)

    def _job_root(self, job_id: str) -> Path:
        return VIDEOS_ROOT / job_id

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

    def _global_settings(self) -> dict[str, Any]:
        return {**DEFAULT_SETTINGS, **self.db.get_settings()}

    def _resolved_job_config(self, job: dict[str, Any]) -> dict[str, Any]:
        settings = self._global_settings()
        return {
            "scene_planning_provider": job.get("scene_planning_provider")
            or job.get("scene_provider")
            or settings["default_scene_planning_provider"],
            "visual_bible_provider": job.get("visual_bible_provider")
            or job.get("prompt_provider")
            or settings["default_visual_bible_provider"],
            "video_prompt_provider": job.get("video_prompt_provider")
            or job.get("prompt_provider")
            or settings["default_video_prompt_provider"],
            "image_prompt_provider": job.get("image_prompt_provider")
            or job.get("prompt_provider")
            or settings["default_image_prompt_provider"],
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
            leading_video_scene_count=max(0, int(leading_video_scene_count)),
        )
        return self.get_job_detail(job_id)

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

    def get_job_detail(self, job_id: str) -> dict[str, Any]:
        job = self.db.get_job(job_id)
        if job is None:
            raise FileNotFoundError("Job not found.")
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
                "prompt_list": self._read_preview_text(job.get("prompt_list_draft_path")),
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
        bible_path = write_json(dirs["review"] / "visual_bible.json", normalized)
        validation_path = write_json(dirs["diagnostics"] / "visual_bible_validation.json", report)
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

    def save_review_prompts(self, job_id: str, prompts: list[str]) -> dict[str, Any]:
        job = self.db.get_job(job_id)
        if job is None:
            raise FileNotFoundError("Job not found.")
        scenes = self._read_preview_json(job.get("timeline_draft_path")) or []
        lines, report = validate_prompt_payloads(scenes, [{"prompts": prompts}])
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
        mapping = {
            "final_srt": job.get("final_srt_path"),
            "alignment_report": job.get("alignment_report_path"),
            "segments_json": job.get("segments_path"),
            "planning_manifest": job.get("planning_manifest_path"),
            "timeline_draft": job.get("timeline_draft_path"),
            "timeline_validation": job.get("timeline_validation_path"),
            "visual_bible": job.get("visual_bible_path"),
            "visual_bible_validation": job.get("visual_bible_validation_path"),
            "prompt_list_draft": job.get("prompt_list_draft_path"),
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
        try:
            manifest = read_json(Path(job["planning_manifest_path"]))
            cues = parse_srt_text(Path(job["final_srt_path"]).read_text(encoding="utf-8"))
            template = self.templates.snapshot_template(self._job_root(job_id), SCENE_STAGE, provider)
            all_scene_groups: list[list[dict[str, Any]]] = []
            warnings: list[str] = []
            commands: list[dict[str, Any]] = []
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
                    "- keep boundary scenes conservative because this chunk overlaps neighboring chunks\n\n"
                    f"Chunk metadata:\n{json.dumps(chunk_payload | {'job_id': job_id}, ensure_ascii=False, indent=2)}"
                )
                chunk_dir = ensure_dir(stage_dir / f"chunk-{chunk_id:03d}")
                result = self.cli_runner.run_structured(
                    provider=provider,
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
                    "template_hash": template["hash"],
                    "leading_video_scene_count": config["leading_video_scene_count"],
                    "commands": commands,
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

    def _run_visual_bible(self, job_id: str) -> None:
        job = self.db.get_job(job_id)
        if job is None:
            raise FileNotFoundError("Job not found.")
        config = self._resolved_job_config(job)
        provider = config["visual_bible_provider"]
        job, stage_dir, run_id = self._begin_stage(job_id, VISUAL_BIBLE_STAGE, provider=provider)
        try:
            timeline = read_json(Path(job["timeline_draft_path"]), default=[])
            if not timeline:
                raise ValueError("Timeline draft is missing.")
            template = self.templates.snapshot_template(self._job_root(job_id), VISUAL_BIBLE_STAGE, provider)
            user_prompt = (
                "Create a visual bible for this video timeline.\n"
                "Return English JSON only.\n"
                "The bible must create stable world style guidance, locked recurring character cards, and continuity rules that can be reused in self-sufficient prompts.\n\n"
                f"Timeline payload:\n{json.dumps({'job_id': job_id, 'scenes': timeline}, ensure_ascii=False, indent=2)}"
            )
            result = self.cli_runner.run_structured(
                provider=provider,
                system_prompt=template["body"],
                user_prompt=user_prompt,
                schema=visual_bible_output_schema(),
                workdir=self._job_root(job_id),
                artifact_dir=stage_dir,
            )
            normalized, report = normalize_visual_bible(result["parsed"])
            if report["errors"]:
                raise ValueError("; ".join(report["errors"]))
            bible_path = write_json(self._job_root(job_id) / "review" / "visual_bible.json", normalized)
            validation_path = write_json(self._job_root(job_id) / "diagnostics" / "visual_bible_validation.json", report)
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
                    "template_hash": template["hash"],
                    "command": result["command_payload"],
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
        try:
            timeline = read_json(Path(job["timeline_draft_path"]), default=[])
            if not timeline:
                raise ValueError("Timeline draft is missing.")
            visual_bible_path = Path(job["visual_bible_path"]) if job.get("visual_bible_path") else None
            if not visual_bible_path or not visual_bible_path.exists():
                raise ValueError("Visual bible is missing.")
            visual_bible = read_json(visual_bible_path)
            scenes = [scene for scene in timeline if scene["asset_type"] == asset_type]
            settings = self._global_settings()
            batches = build_prompt_batches(scenes, batch_size=int(settings["prompt_batch_size"])) if scenes else []
            template = self.templates.snapshot_template(self._job_root(job_id), stage, provider)
            payloads: list[dict[str, Any]] = []
            commands: list[dict[str, Any]] = []
            stdout_parts: list[str] = []
            stderr_parts: list[str] = []
            schema = video_prompt_output_schema() if asset_type == "video" else image_prompt_output_schema()

            for batch in batches:
                batch_dir = ensure_dir(stage_dir / f"batch-{batch['batch_id']:03d}")
                mode_rules = (
                    "Use labels in this exact order: SUBJ, SET, ACT, CAM, LOOK, LIGHT, optional RULES."
                    if asset_type == "video"
                    else "Use labels in this exact order: SUBJ, SET, COMP, LOOK, LIGHT, optional RULES."
                )
                target_words = "65 to 95 words" if asset_type == "video" else "45 to 75 words"
                prompt_context = self._build_prompt_context(visual_bible, batch["scenes"])
                user_prompt = (
                    f"Generate one {asset_type} prompt per scene in this batch.\n"
                    "Return English JSON only.\n"
                    "Every final prompt must be self-sufficient and copy-paste ready.\n"
                    "Do not use words like same, previous scene, or as before.\n"
                    "Do not include scene_id or asset_type inside the final prompt text.\n"
                    "If a scene has character guidance, use those visual traits directly in the final prompt text; names alone are not enough.\n"
                    "Prefer concrete visible details over abstract themes or narration summaries.\n"
                    "Avoid repetitive filler like cinematic documentary hybrid, restrained, tactile, or neutral unless it adds specific visual value.\n"
                    "Do not default to split-screen, title-card, or infographic layouts unless the scene clearly requires comparison or on-screen text.\n"
                    f"Keep the final prompt compact, ideally around {target_words}.\n"
                    f"{mode_rules}\n\n"
                    f"Prompt context:\n{json.dumps(prompt_context, ensure_ascii=False, indent=2)}\n\n"
                    f"Batch payload:\n{json.dumps(batch, ensure_ascii=False, indent=2)}"
                )
                result = self.cli_runner.run_structured(
                    provider=provider,
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
                    "template_hash": template["hash"],
                    "commands": commands,
                },
            )
            self._combine_prompt_outputs(job_id, raise_on_missing=False)
        except Exception as exc:
            self.db.finish_stage_run(
                run_id,
                status="failed",
                exit_code=1,
                error_text=str(exc),
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
        blueprint_path = write_jsonl(review_dir / "prompt_blueprint.jsonl", ordered_entries)
        validation_path = write_json(diagnostics_dir / "prompt_validation.json", report)
        self.db.update_job(
            job_id,
            prompt_list_draft_path=str(prompt_path),
            prompt_blueprint_path=str(blueprint_path),
            prompt_validation_path=str(validation_path),
            warning_count=self._warning_total(job, prompt_report=report),
        )
