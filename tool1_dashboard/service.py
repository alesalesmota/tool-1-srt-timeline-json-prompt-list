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
            episode = self.db.next_queued_episode()
            if episode is not None:
                self._process_episode(episode)
                continue
            self._check_paused_tts_episodes()
            with self._condition:
                self._condition.wait(timeout=1.0)

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
            p["configured_languages"] = json.loads(p.get("configured_languages") or "[]")
            p["language_voice_profiles"] = json.loads(p.get("language_voice_profiles") or "{}")
            p["language_translation_profiles"] = json.loads(p.get("language_translation_profiles") or "{}")
            p["episode_count"] = len(self.db.list_episodes(p["id"]))
        return projects

    def get_niche_project_detail(self, project_id: str) -> dict[str, Any]:
        project = self.db.get_niche_project(project_id)
        if project is None:
            raise FileNotFoundError("Niche project not found.")
        configured_langs = json.loads(project.get("configured_languages") or "[]")
        project["configured_languages"] = configured_langs
        project["language_voice_profiles"] = json.loads(project.get("language_voice_profiles") or "{}")
        project["language_translation_profiles"] = json.loads(project.get("language_translation_profiles") or "{}")
        episodes = self.db.list_episodes(project_id)

        # Attach per-episode language statuses
        for ep in episodes:
            ep["language_statuses"] = self.db.get_episode_language_statuses(ep["id"])

        # Compute statistics
        by_status: dict[str, int] = {}
        for ep in episodes:
            ps = ep.get("pipeline_status") or "idle"
            by_status[ps] = by_status.get(ps, 0) + 1
        done_count = by_status.get("done", 0)
        total = len(episodes)
        statistics = {
            "total_episodes": total,
            "by_status": by_status,
            "languages_configured": len(configured_langs),
            "completion_rate": round((done_count / total) * 100) if total > 0 else 0,
        }

        # Include profiles for dropdowns
        voice_profiles = self.list_voice_profiles()
        translation_profiles = self.list_translation_profiles()

        return {
            "project": project,
            "episodes": episodes,
            "statistics": statistics,
            "voice_profiles": voice_profiles,
            "translation_profiles": translation_profiles,
        }

    def update_niche_project(
        self,
        project_id: str,
        **fields: Any,
    ) -> dict[str, Any]:
        project = self.db.get_niche_project(project_id)
        if project is None:
            raise FileNotFoundError("Niche project not found.")
        # Serialize JSON fields
        for key in ("configured_languages", "language_voice_profiles", "language_translation_profiles"):
            if key in fields and not isinstance(fields[key], str):
                fields[key] = json.dumps(fields[key])
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
        for ep in episodes:
            ps = ep.get("pipeline_status") or "idle"
            bs = ep.get("board_status") or "Draft"
            if filter_status == "draft" and ps == "idle" and bs == "Draft":
                self.queue_episode(ep["id"])
                queued_ids.append(ep["id"])
            elif filter_status == "failed" and ps == "failed":
                self.queue_episode(ep["id"])
                queued_ids.append(ep["id"])
        return {"queued_count": len(queued_ids), "episode_ids": queued_ids}

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
        stage_runs = self.db.list_stage_runs(episode_id)

        # Attach TTS job progress for each language with an active TTS job
        for ls in lang_statuses:
            tts_job_id = ls.get("tts_job_id")
            if tts_job_id and ls.get("tts_status") in ("running", "pending"):
                job = self.db.get_tts_job(tts_job_id)
                if job:
                    ls["tts_job_status"] = job.get("status")
                    ls["tts_job_progress"] = job.get("progress")

        # Include worker health
        worker_health = self.get_worker_health()

        return {
            "episode": episode,
            "language_statuses": lang_statuses,
            "stage_runs": stage_runs,
            "worker_health": worker_health,
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

        if stage == "translation":
            self.db.update_episode_language_status(
                episode_id, language_code,
                translation_status="pending", error_message=None,
            )
            self._episode_retry_single_translation(episode_id, language_code)
        elif stage == "tts":
            self.db.update_episode_language_status(
                episode_id, language_code,
                tts_status="pending", error_message=None,
            )
            self._episode_retry_single_tts(episode_id, language_code)
        else:
            raise ValueError(f"Retry not supported for stage '{stage}'.")

        return {"retried": True, "language_code": language_code, "stage": stage}

    def _episode_retry_single_translation(self, episode_id: str, lang: str) -> None:
        """Retry translation for a single language."""
        from .translation import TranslationService

        episode = self.db.get_episode(episode_id)
        project = self.db.get_niche_project(episode["niche_project_id"])
        translation_profiles = json.loads(project.get("language_translation_profiles") or "{}")
        settings = self._global_settings()
        workspace = self._episode_workspace(episode)

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
            svc = TranslationService()
            result = asyncio.run(svc.translate_script(
                source_script=episode["script_text"],
                source_lang=episode["master_language"],
                target_lang=lang,
                provider=profile["provider"],
                api_key=profile["api_key_ref"],
                model=profile["model"],
                max_words_per_chunk=settings.get("translation_chunk_max_words", 800),
                context_tail_words=settings.get("translation_context_tail_words", 200),
            ))
            translated_path = workspace / f"script_{lang}.txt"
            write_text(translated_path, result.translated_script)
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
            self.db.update_episode_language_status(
                episode_id, lang,
                translation_status="done",
                script_path=str(translated_path),
            )
        except Exception as exc:
            self.db.update_episode_language_status(
                episode_id, lang,
                translation_status="failed",
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

        # Get script text
        if lang == episode["master_language"]:
            script_text = episode["script_text"]
        else:
            lang_status = self.db.get_episode_language_status(episode_id, lang)
            script_path = lang_status.get("script_path") if lang_status else None
            if script_path and Path(script_path).exists():
                script_text = read_text(Path(script_path))
            else:
                script_text = episode["script_text"]

        self.db.update_episode_language_status(episode_id, lang, tts_status="running")
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
            "worker_id": health.worker_id,
            "current_job_id": health.current_job_id,
            "last_heartbeat": health.last_heartbeat,
            "is_stale": health.is_stale,
            "pid": health.pid,
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
        project = self.db.get_niche_project(episode["niche_project_id"])
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
        project = self.db.get_niche_project(episode["niche_project_id"])
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
        project = self.db.get_niche_project(episode["niche_project_id"])
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
