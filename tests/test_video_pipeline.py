from __future__ import annotations

import importlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

from tool1_dashboard.database import Tool1Database
from tool1_dashboard.providers import CliRunner
from tool1_dashboard.runtime import utc_now
from tool1_dashboard.service import Tool1Service


class FakeCliRunner:
    """Fake CLI runner that returns canned LLM responses for episode pipeline tests."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def probe(self, *, force: bool = False) -> dict[str, object]:
        return {
            "codex": {"available": True, "logged_in": True},
            "claude": {"available": True, "logged_in": True},
            "openai": {"available": True, "logged_in": False},
        }

    def run_structured(self, *, provider, model, api_key=None, system_prompt, user_prompt, schema, workdir, artifact_dir):
        artifact_dir.mkdir(parents=True, exist_ok=True)
        self.calls.append({
            "provider": provider,
            "model": model,
            "api_key": api_key,
            "schema": schema,
            "workdir": str(workdir),
            "artifact_dir": str(artifact_dir),
        })
        # Determine which stage based on schema shape
        if "world_style" in schema.get("properties", {}):
            # Visual bible / consistency guide
            parsed = {
                "world_style": {
                    "setting": "Ancient desert kingdom",
                    "look": "Cinematic realism",
                    "palette": "Muted sand and charcoal",
                    "lighting": "Golden dusk and firelight",
                    "camera_language": "Measured documentary framing",
                    "negative_rules": "No text overlays",
                },
                "characters": [
                    {
                        "character_id": "prophet_001",
                        "label": "Desert prophet",
                        "visual_description": "Elderly man with olive skin, deep-set eyes, and a weathered face",
                        "wardrobe": "Rough wool robe and wooden staff",
                        "demeanor": "Calm but urgent",
                        "usage_notes": "Keep him rugged and serious",
                    }
                ],
                "continuity_rules": ["Keep the same prophet face and robe silhouette."],
                "environment_rules": ["Preserve a windblown desert atmosphere."],
            }
        elif "scenes" in schema.get("properties", {}):
            # Scene planning
            parsed = {
                "scenes": [
                    {"start": 0.0, "end": 3.0, "duration": 3.0, "text": "First scene content."},
                    {"start": 3.0, "end": 6.0, "duration": 3.0, "text": "Second scene content."},
                ]
            }
        else:
            # Prompt generation
            batch_payload = json.loads(user_prompt.split("Batch payload:\n", 1)[1])
            prompts = []
            for scene in batch_payload["scenes"]:
                if "action" in schema.get("properties", {}).get("prompts", {}).get("items", {}).get("properties", {}):
                    prompts.append({
                        "scene_id": scene["scene_id"],
                        "subject": "elderly desert prophet with olive skin",
                        "setting": "windswept desert camp at dusk",
                        "action": "he steps out of a dark tent",
                        "camera": "slow push in from wide to medium",
                        "look": "cinematic realism",
                        "lighting": "dusk backlight",
                        "rules": "no text",
                        "character_refs": ["prophet_001"],
                        "prompt": "An elderly desert prophet with olive skin steps out of a dark tent in a windswept desert camp at dusk, slow push in from wide to medium, cinematic realism, dusk backlight, no text.",
                    })
                else:
                    prompts.append({
                        "scene_id": scene["scene_id"],
                        "subject": "elderly desert prophet with olive skin",
                        "setting": "windswept desert camp at dusk",
                        "composition": "medium-wide frame with prophet off-center",
                        "look": "cinematic realism",
                        "lighting": "dusk backlight",
                        "rules": "no text",
                        "character_refs": ["prophet_001"],
                        "prompt": "An elderly desert prophet with olive skin stands in a windswept desert camp at dusk, medium-wide frame, cinematic realism, dusk backlight, no text.",
                    })
            parsed = {"prompts": prompts}

        # Write stdout/stderr files
        stdout_path = artifact_dir / "stdout.txt"
        stderr_path = artifact_dir / "stderr.txt"
        stdout_path.write_text("fake stdout", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        return {
            "parsed": parsed,
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "command_payload": {"fake": True},
        }


class ProbeStateCliRunner(FakeCliRunner):
    def __init__(self, probe_state: dict[str, object] | None = None) -> None:
        super().__init__()
        self._probe_state = probe_state or super().probe()

    def probe(self, *, force: bool = False) -> dict[str, object]:
        return self._probe_state


class FailingProviderCliRunner(ProbeStateCliRunner):
    def __init__(
        self,
        *,
        fail_provider: str,
        fail_message: str,
        probe_state: dict[str, object] | None = None,
    ) -> None:
        super().__init__(probe_state=probe_state)
        self.fail_provider = fail_provider
        self.fail_message = fail_message

    def run_structured(self, *, provider, model, api_key=None, system_prompt, user_prompt, schema, workdir, artifact_dir):
        artifact_dir.mkdir(parents=True, exist_ok=True)
        self.calls.append({
            "provider": provider,
            "model": model,
            "api_key": api_key,
            "schema": schema,
            "workdir": str(workdir),
            "artifact_dir": str(artifact_dir),
        })
        stdout_path = artifact_dir / "stdout.txt"
        stderr_path = artifact_dir / "stderr.txt"
        stdout_path.write_text("failing stdout", encoding="utf-8")
        stderr_path.write_text(self.fail_message, encoding="utf-8")
        if provider == self.fail_provider:
            raise RuntimeError(self.fail_message)
        return super().run_structured(
            provider=provider,
            model=model,
            api_key=api_key,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            schema=schema,
            workdir=workdir,
            artifact_dir=artifact_dir,
        )


def _make_service(temp_path: Path, cli_runner=None) -> Tool1Service:
    return Tool1Service(
        db=Tool1Database(temp_path / "db.sqlite"),
        cli_runner=cli_runner,
    )


def _make_client(app_module, service: Tool1Service):
    original = app_module.service
    app_module.service = service
    return TestClient(app_module.app), original


def _patches(temp_path: Path):
    return (
        patch("tool1_dashboard.service.EPISODES_ROOT", temp_path / "episodes"),
        patch("tool1_dashboard.service.AGENTS_ROOT", temp_path / "config" / "agents"),
        patch("tool1_dashboard.templates.AGENTS_ROOT", temp_path / "config" / "agents"),
    )


class FakeAsyncResponse:
    def __init__(self, status_code: int, payload: dict[str, object], text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self) -> dict[str, object]:
        return self._payload


class FakeAsyncClient:
    def __init__(self, response: FakeAsyncResponse, recorder: list[dict[str, object]]) -> None:
        self.response = response
        self.recorder = recorder

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def get(self, url: str, headers: dict[str, str] | None = None):
        self.recorder.append({"url": url, "headers": headers or {}})
        return self.response


def _seed_voice_profile(
    service: Tool1Service,
    temp_path: Path,
    profile_id: str,
    language_code: str,
    *,
    stored_language_code: str | None = None,
) -> str:
    audio_path = temp_path / f"{profile_id}.wav"
    audio_path.write_bytes(b"RIFF....WAVEfmt ")
    now = utc_now()
    service.db.create_voice_profile({
        "id": profile_id,
        "name": f"Voice {language_code}",
        "language_code": stored_language_code if stored_language_code is not None else language_code,
        "audio_file": audio_path.name,
        "audio_path": str(audio_path),
        "latents_path": None,
        "has_latents": 1,
        "created_at": now,
        "updated_at": now,
    })
    return profile_id


def _seed_translation_profile(service: Tool1Service, profile_id: str, language_code: str) -> str:
    now = utc_now()
    service.db.create_translation_profile({
        "id": profile_id,
        "name": f"Translation {language_code}",
        "provider": "deepl",
        "api_key_ref": "fake-key",
        "model": "deepl-v2",
        "is_default": 0,
        "created_at": now,
        "updated_at": now,
    })
    return profile_id


def _build_profile_assignments(
    service: Tool1Service,
    temp_path: Path,
    languages: list[str],
    *,
    master_language: str = "en",
    include_voice_for: list[str] | None = None,
    include_translation_for: list[str] | None = None,
) -> tuple[dict[str, str], dict[str, str]]:
    include_voice_for = include_voice_for or list(languages)
    include_translation_for = include_translation_for or [lang for lang in languages if lang != master_language]
    voice_profiles: dict[str, str] = {}
    translation_profiles: dict[str, str] = {}
    for language_code in include_voice_for:
        voice_profiles[language_code] = _seed_voice_profile(
            service,
            temp_path,
            f"vp-{language_code.replace('-', '').lower()}",
            language_code,
        )
    for language_code in include_translation_for:
        if language_code == master_language:
            continue
        translation_profiles[language_code] = _seed_translation_profile(
            service,
            f"tp-{language_code.replace('-', '').lower()}",
            language_code,
        )
    return voice_profiles, translation_profiles


class NicheProjectApiTests(unittest.TestCase):
    """Tests for the niche project + episode API endpoints."""

    def setUp(self) -> None:
        self.app_module = importlib.import_module("tool1_dashboard.app")

    def test_create_and_list_niche_projects(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path)
                client, original = _make_client(self.app_module, service)
                try:
                    resp = client.post("/api/niche-projects", json={
                        "name": "Religion Channel",
                        "master_language": "en",
                        "configured_languages": ["en", "pt-BR", "es"],
                    })
                    self.assertEqual(resp.status_code, 200)
                    project = resp.json()["project"]
                    self.assertEqual(project["title"], "Religion Channel")
                    self.assertTrue(project["id"].startswith("niche-"))

                    resp = client.get("/api/niche-projects")
                    self.assertEqual(resp.status_code, 200)
                    projects = resp.json()["projects"]
                    self.assertEqual(len(projects), 1)
                    self.assertEqual(projects[0]["episode_count"], 0)

                    project_id = project["id"]
                    resp = client.get(f"/api/niche-projects/{project_id}")
                    self.assertEqual(resp.status_code, 200)
                    detail = resp.json()
                    self.assertEqual(detail["project"]["configured_languages"], ["en", "pt-BR", "es"])
                finally:
                    self.app_module.service = original

    def test_update_niche_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path)
                client, original = _make_client(self.app_module, service)
                try:
                    resp = client.post("/api/niche-projects", json={
                        "name": "Sports Channel",
                        "master_language": "en",
                        "configured_languages": ["en"],
                    })
                    project_id = resp.json()["project"]["id"]
                    resp = client.put(f"/api/niche-projects/{project_id}", json={
                        "configured_languages": ["en", "pt-BR", "fr"],
                    })
                    self.assertEqual(resp.status_code, 200)
                    resp = client.get(f"/api/niche-projects/{project_id}")
                    self.assertEqual(
                        resp.json()["project"]["configured_languages"],
                        ["en", "pt-BR", "fr"],
                    )
                finally:
                    self.app_module.service = original

    def test_niche_project_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path)
                client, original = _make_client(self.app_module, service)
                try:
                    resp = client.get("/api/niche-projects/nonexistent")
                    self.assertEqual(resp.status_code, 404)
                finally:
                    self.app_module.service = original

    def test_delete_niche_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path)
                client, original = _make_client(self.app_module, service)
                try:
                    resp = client.post("/api/niche-projects", json={
                        "name": "Deletable",
                        "master_language": "en",
                    })
                    project_id = resp.json()["project"]["id"]
                    resp = client.delete(f"/api/niche-projects/{project_id}")
                    self.assertEqual(resp.status_code, 200)
                    resp = client.get(f"/api/niche-projects/{project_id}")
                    self.assertEqual(resp.status_code, 404)
                finally:
                    self.app_module.service = original


class TranslationProfileApiTests(unittest.TestCase):

    def setUp(self) -> None:
        self.app_module = importlib.import_module("tool1_dashboard.app")

    def test_list_and_detail_mask_translation_api_keys(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path)
                client, original = _make_client(self.app_module, service)
                try:
                    created = service.create_translation_profile(
                        name="OpenAI Main",
                        provider="openai",
                        api_key="sk-test-secret-1234",
                        model="gpt-5.4-mini",
                    )
                    resp = client.get("/api/translation-profiles")
                    self.assertEqual(resp.status_code, 200)
                    profile = resp.json()["profiles"][0]
                    self.assertNotIn("api_key_ref", profile)
                    self.assertTrue(profile["has_api_key"])
                    self.assertEqual(profile["provider_label"], "OpenAI API")

                    resp = client.get(f"/api/translation-profiles/{created['id']}")
                    self.assertEqual(resp.status_code, 200)
                    detail = resp.json()
                    self.assertNotIn("api_key_ref", detail)
                    self.assertTrue(detail["api_key_masked"])
                finally:
                    self.app_module.service = original

    def test_create_translation_profile_rejects_placeholder_provider(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path)
                client, original = _make_client(self.app_module, service)
                try:
                    resp = client.post("/api/translation-profiles", json={
                        "name": "Codex Preview",
                        "provider": "codex_cli",
                        "api_key": "unused",
                        "model": "gpt-5.4",
                    })
                    self.assertEqual(resp.status_code, 400)
                    self.assertIn("OpenAI API", resp.json()["detail"])
                finally:
                    self.app_module.service = original

    def test_discover_openai_models_with_inline_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path)
                client, original = _make_client(self.app_module, service)
                recorder: list[dict[str, object]] = []
                fake_response = FakeAsyncResponse(200, {
                    "data": [
                        {"id": "gpt-5.4-mini", "owned_by": "openai"},
                        {"id": "gpt-4o", "owned_by": "openai"},
                        {"id": "gpt-image-1", "owned_by": "openai"},
                    ]
                })
                try:
                    with patch(
                        "tool1_dashboard.service.httpx.AsyncClient",
                        side_effect=lambda *args, **kwargs: FakeAsyncClient(fake_response, recorder),
                    ):
                        resp = client.post("/api/translation-profiles/openai/discover", json={"api_key": "sk-inline"})
                    self.assertEqual(resp.status_code, 200)
                    payload = resp.json()
                    self.assertEqual(payload["recommended_model"], "gpt-5.4-mini")
                    self.assertEqual([item["id"] for item in payload["models"]], ["gpt-5.4-mini", "gpt-4o"])
                    self.assertEqual(recorder[0]["headers"]["Authorization"], "Bearer sk-inline")
                finally:
                    self.app_module.service = original

    def test_discover_openai_models_with_saved_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path)
                client, original = _make_client(self.app_module, service)
                recorder: list[dict[str, object]] = []
                fake_response = FakeAsyncResponse(200, {
                    "data": [{"id": "gpt-4o-mini", "owned_by": "openai"}]
                })
                try:
                    profile = service.create_translation_profile(
                        name="Saved Key",
                        provider="openai",
                        api_key="sk-saved",
                        model="gpt-4o-mini",
                    )
                    with patch(
                        "tool1_dashboard.service.httpx.AsyncClient",
                        side_effect=lambda *args, **kwargs: FakeAsyncClient(fake_response, recorder),
                    ):
                        resp = client.post("/api/translation-profiles/openai/discover", json={"profile_id": profile["id"]})
                    self.assertEqual(resp.status_code, 200)
                    payload = resp.json()
                    self.assertTrue(payload["from_saved_key"])
                    self.assertEqual(recorder[0]["headers"]["Authorization"], "Bearer sk-saved")
                finally:
                    self.app_module.service = original

    def test_discover_openai_models_rejects_bad_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path)
                client, original = _make_client(self.app_module, service)
                recorder: list[dict[str, object]] = []
                fake_response = FakeAsyncResponse(401, {"error": {"message": "Invalid API key"}})
                try:
                    with patch(
                        "tool1_dashboard.service.httpx.AsyncClient",
                        side_effect=lambda *args, **kwargs: FakeAsyncClient(fake_response, recorder),
                    ):
                        resp = client.post("/api/translation-profiles/openai/discover", json={"api_key": "sk-bad"})
                    self.assertEqual(resp.status_code, 400)
                    self.assertIn("rejected", resp.json()["detail"])
                finally:
                    self.app_module.service = original

    def test_discover_openai_stage_models_with_inline_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path)
                client, original = _make_client(self.app_module, service)
                recorder: list[dict[str, object]] = []
                fake_response = FakeAsyncResponse(200, {
                    "data": [
                        {"id": "gpt-5.4-mini", "owned_by": "openai"},
                        {"id": "gpt-4.1-mini", "owned_by": "openai"},
                    ]
                })
                try:
                    with patch(
                        "tool1_dashboard.service.httpx.AsyncClient",
                        side_effect=lambda *args, **kwargs: FakeAsyncClient(fake_response, recorder),
                    ):
                        resp = client.post("/api/providers/openai/discover", json={"api_key": "sk-inline"})
                    self.assertEqual(resp.status_code, 200)
                    payload = resp.json()
                    self.assertEqual(payload["recommended_model"], "gpt-5.4-mini")
                    self.assertFalse(payload["api_key_saved"])
                    self.assertEqual(recorder[0]["headers"]["Authorization"], "Bearer sk-inline")

                    settings = client.get("/api/settings").json()
                    self.assertEqual(
                        [item["value"] for item in settings["model_catalog"]["openai"]],
                        ["gpt-5.4-mini", "gpt-4.1-mini"],
                    )
                    self.assertFalse(settings["settings"]["stage_provider_openai_has_api_key"])
                    self.assertEqual(settings["settings"]["stage_provider_openai_model_count"], 2)
                finally:
                    self.app_module.service = original


class EpisodeSubmissionApiTests(unittest.TestCase):
    """Tests for episode submission, detail, queue, and board endpoints."""

    def setUp(self) -> None:
        self.app_module = importlib.import_module("tool1_dashboard.app")

    def _create_niche_and_episode(self, client, langs=None, project_payload=None):
        langs = langs or ["en", "pt-BR"]
        payload = {
            "name": "Test Project",
            "master_language": "en",
            "configured_languages": langs,
        }
        if project_payload:
            payload.update(project_payload)
        resp = client.post("/api/niche-projects", json=payload)
        project_id = resp.json()["project"]["id"]
        resp = client.post(f"/api/niche-projects/{project_id}/episodes", json={
            "title": "The Story of Moses",
            "script_text": "In the beginning, there was a man called Moses.",
        })
        episode = resp.json()["episode"]
        return project_id, episode

    def test_submit_episode_creates_language_statuses(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path)
                client, original = _make_client(self.app_module, service)
                try:
                    project_id, episode = self._create_niche_and_episode(client)
                    self.assertTrue(episode["id"].startswith("ep-"))
                    self.assertEqual(episode["pipeline_status"], "idle")
                    self.assertIn("queue_readiness", episode)
                    self.assertFalse(episode["queue_readiness"]["ok"])

                    resp = client.get(f"/api/episodes/{episode['id']}")
                    detail = resp.json()
                    self.assertEqual(len(detail["language_statuses"]), 2)
                    en_status = next(ls for ls in detail["language_statuses"] if ls["language_code"] == "en")
                    self.assertEqual(en_status["translation_status"], "done")
                    self.assertIn("queue_readiness", detail["episode"])
                finally:
                    self.app_module.service = original

    def test_episode_files_api_lists_duplicate_names_and_supports_preview_download(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path)
                client, original = _make_client(self.app_module, service)
                try:
                    _, episode = self._create_niche_and_episode(client, langs=["en"])
                    episode_id = episode["id"]
                    workspace = Path(service.db.get_episode(episode_id)["workspace_dir"])

                    root_stderr_path = workspace / "stderr.txt"
                    root_stderr_path.write_text("root stderr output", encoding="utf-8")

                    nested_stderr_path = workspace / "runs" / "consistency_guide" / "stderr.txt"
                    nested_stderr_path.parent.mkdir(parents=True, exist_ok=True)
                    nested_stderr_path.write_text("", encoding="utf-8")

                    guide_path = workspace / "consistency_guide.json"
                    guide_path.write_text(
                        json.dumps(
                            {
                                "world_style": {"look": "Painterly realism"},
                                "continuity_rules": ["Keep the same robe silhouette."],
                            }
                        ),
                        encoding="utf-8",
                    )

                    archive_path = workspace / "artifacts.zip"
                    archive_path.write_bytes(b"PK\x03\x04demo")

                    files_resp = client.get(f"/api/episodes/{episode_id}/files")
                    self.assertEqual(files_resp.status_code, 200)
                    files_by_path = {
                        item["relative_path"]: item
                        for item in files_resp.json()["files"]
                    }

                    self.assertIn("stderr.txt", files_by_path)
                    self.assertIn("runs/consistency_guide/stderr.txt", files_by_path)
                    self.assertIn("consistency_guide.json", files_by_path)
                    self.assertIn("artifacts.zip", files_by_path)
                    self.assertEqual(files_by_path["stderr.txt"]["preview_type"], "text")
                    self.assertFalse(files_by_path["stderr.txt"]["is_empty"])
                    self.assertEqual(
                        files_by_path["runs/consistency_guide/stderr.txt"]["preview_type"],
                        "empty",
                    )
                    self.assertTrue(files_by_path["runs/consistency_guide/stderr.txt"]["is_empty"])
                    self.assertEqual(files_by_path["runs/consistency_guide/stderr.txt"]["directory"], "runs/consistency_guide")
                    self.assertEqual(files_by_path["artifacts.zip"]["preview_type"], "binary")

                    guide_preview = client.get(
                        f"/api/episodes/{episode_id}/files/content",
                        params={"path": "consistency_guide.json"},
                    )
                    self.assertEqual(guide_preview.status_code, 200)
                    guide_payload = guide_preview.json()
                    self.assertEqual(guide_payload["preview_type"], "json")
                    self.assertIn('\n  "world_style"', guide_payload["text"])
                    self.assertFalse(guide_payload["truncated"])

                    empty_preview = client.get(
                        f"/api/episodes/{episode_id}/files/content",
                        params={"path": "runs/consistency_guide/stderr.txt"},
                    )
                    self.assertEqual(empty_preview.status_code, 200)
                    self.assertEqual(empty_preview.json()["preview_type"], "empty")
                    self.assertEqual(
                        empty_preview.json()["summary"],
                        "This file exists but does not contain data yet.",
                    )

                    binary_preview = client.get(
                        f"/api/episodes/{episode_id}/files/content",
                        params={"path": "artifacts.zip"},
                    )
                    self.assertEqual(binary_preview.status_code, 200)
                    self.assertEqual(binary_preview.json()["preview_type"], "binary")
                    self.assertEqual(
                        binary_preview.json()["summary"],
                        "Preview is not available for this file type.",
                    )

                    download_resp = client.get(
                        f"/api/episodes/{episode_id}/files/download",
                        params={"path": "stderr.txt"},
                    )
                    self.assertEqual(download_resp.status_code, 200)
                    self.assertEqual(download_resp.content, b"root stderr output")
                    self.assertIn("stderr.txt", download_resp.headers["content-disposition"])
                    self.assertTrue(download_resp.headers["content-type"].startswith("text/plain"))
                finally:
                    self.app_module.service = original

    def test_submit_episode_to_nonexistent_project(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path)
                client, original = _make_client(self.app_module, service)
                try:
                    resp = client.post("/api/niche-projects/nonexistent/episodes", json={
                        "title": "Test",
                        "script_text": "Hello",
                    })
                    self.assertEqual(resp.status_code, 404)
                finally:
                    self.app_module.service = original

    def test_queue_episode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path)
                client, original = _make_client(self.app_module, service)
                try:
                    voice_profiles, translation_profiles = _build_profile_assignments(service, temp_path, ["en", "pt-BR"])
                    _, episode = self._create_niche_and_episode(client, project_payload={
                        "language_voice_profiles": voice_profiles,
                        "language_translation_profiles": translation_profiles,
                    })
                    resp = client.post(f"/api/episodes/{episode['id']}/queue", json={})
                    self.assertEqual(resp.status_code, 200)
                    self.assertTrue(resp.json()["queued"])
                    self.assertEqual(resp.json()["start_stage"], "consistency_guide")
                finally:
                    self.app_module.service = original

    def test_queue_episode_custom_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path)
                client, original = _make_client(self.app_module, service)
                try:
                    voice_profiles, _ = _build_profile_assignments(service, temp_path, ["en"], include_translation_for=[])
                    _, episode = self._create_niche_and_episode(client, langs=["en"], project_payload={
                        "language_voice_profiles": voice_profiles,
                    })
                    resp = client.post(f"/api/episodes/{episode['id']}/queue", json={
                        "start_stage": "translation",
                    })
                    self.assertEqual(resp.status_code, 200)
                    self.assertEqual(resp.json()["start_stage"], "translation")
                finally:
                    self.app_module.service = original

    def test_queue_episode_defaults_to_failed_current_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path)
                client, original = _make_client(self.app_module, service)
                try:
                    voice_profiles, translation_profiles = _build_profile_assignments(service, temp_path, ["en", "pt-BR"])
                    _, episode = self._create_niche_and_episode(client, project_payload={
                        "language_voice_profiles": voice_profiles,
                        "language_translation_profiles": translation_profiles,
                    })
                    service.db.update_episode(
                        episode["id"],
                        board_status="Needs Attention",
                        pipeline_status="failed",
                        current_stage="tts",
                        queued_from_stage="consistency_guide",
                    )
                    translated_path = temp_path / "script_pt-BR_resume.txt"
                    translated_path.write_text("Texto pronto para TTS.", encoding="utf-8")
                    service.db.update_episode_language_status(
                        episode["id"],
                        "pt-BR",
                        translation_status="done",
                        script_path=str(translated_path),
                    )

                    resp = client.post(f"/api/episodes/{episode['id']}/queue", json={})
                    self.assertEqual(resp.status_code, 200)
                    self.assertEqual(resp.json()["start_stage"], "tts")
                finally:
                    self.app_module.service = original

    def test_queue_episode_alignment_start_requires_existing_tts_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path)
                client, original = _make_client(self.app_module, service)
                try:
                    voice_profiles, translation_profiles = _build_profile_assignments(service, temp_path, ["en", "pt-BR"])
                    _, episode = self._create_niche_and_episode(client, project_payload={
                        "language_voice_profiles": voice_profiles,
                        "language_translation_profiles": translation_profiles,
                    })

                    resp = client.post(f"/api/episodes/{episode['id']}/queue", json={
                        "start_stage": "alignment",
                    })
                    self.assertEqual(resp.status_code, 400)
                    blocker_codes = {item["code"] for item in resp.json()["detail"]["queue_readiness"]["blockers"]}
                    self.assertIn("missing_tts_assets", blocker_codes)
                finally:
                    self.app_module.service = original

    def test_queue_episode_reset_outputs_rewinds_selected_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path)
                client, original = _make_client(self.app_module, service)
                try:
                    voice_profiles, translation_profiles = _build_profile_assignments(service, temp_path, ["en", "pt-BR"])
                    _, episode = self._create_niche_and_episode(client, project_payload={
                        "language_voice_profiles": voice_profiles,
                        "language_translation_profiles": translation_profiles,
                    })
                    episode_id = episode["id"]

                    translated_path = temp_path / "script_pt-BR.txt"
                    translated_path.write_text("Texto traduzido.", encoding="utf-8")
                    audio_path = temp_path / "narration_pt-BR.wav"
                    audio_path.write_text("fake-audio", encoding="utf-8")
                    srt_path = temp_path / "final_pt-BR.srt"
                    srt_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nOi\n", encoding="utf-8")
                    timeline_path = temp_path / "timeline_pt-BR.json"
                    timeline_path.write_text("[]", encoding="utf-8")

                    service.db.update_episode_language_status(
                        episode_id,
                        "pt-BR",
                        translation_status="done",
                        script_path=str(translated_path),
                        tts_status="done",
                        tts_audio_path=str(audio_path),
                        srt_status="done",
                        srt_path=str(srt_path),
                        timeline_status="done",
                        timeline_path=str(timeline_path),
                    )

                    resp = client.post(f"/api/episodes/{episode_id}/queue", json={
                        "start_stage": "translation",
                        "reset_outputs": True,
                    })
                    self.assertEqual(resp.status_code, 200)
                    self.assertTrue(resp.json()["reset_outputs"])

                    ptbr = service.db.get_episode_language_status(episode_id, "pt-BR")
                    self.assertEqual(ptbr["translation_status"], "pending")
                    self.assertIsNone(ptbr["script_path"])
                    self.assertEqual(ptbr["tts_status"], "pending")
                    self.assertIsNone(ptbr["tts_audio_path"])
                    self.assertEqual(ptbr["srt_status"], "pending")
                    self.assertIsNone(ptbr["srt_path"])
                    self.assertEqual(ptbr["timeline_status"], "pending")
                    self.assertIsNone(ptbr["timeline_path"])
                finally:
                    self.app_module.service = original

    def test_pause_episode_turns_queued_workflow_into_paused_state(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path)
                client, original = _make_client(self.app_module, service)
                try:
                    voice_profiles, translation_profiles = _build_profile_assignments(service, temp_path, ["en", "pt-BR"])
                    _, episode = self._create_niche_and_episode(client, project_payload={
                        "language_voice_profiles": voice_profiles,
                        "language_translation_profiles": translation_profiles,
                    })

                    queue_resp = client.post(f"/api/episodes/{episode['id']}/queue", json={})
                    self.assertEqual(queue_resp.status_code, 200)
                    pause_resp = client.post(f"/api/episodes/{episode['id']}/pause", json={})
                    self.assertEqual(pause_resp.status_code, 200)
                    payload = pause_resp.json()
                    self.assertTrue(payload["paused"])
                    self.assertEqual(payload["resume_stage"], "consistency_guide")

                    refreshed = service.db.get_episode(episode["id"])
                    self.assertEqual(refreshed["pipeline_status"], "paused")
                    self.assertEqual(refreshed["queued_from_stage"], "consistency_guide")
                finally:
                    self.app_module.service = original

    def test_queue_episode_accepts_language_agnostic_voice_profiles_without_mismatch_warning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path)
                client, original = _make_client(self.app_module, service)
                try:
                    shared_voice = _seed_voice_profile(
                        service,
                        temp_path,
                        "vp-shared",
                        "shared",
                        stored_language_code="fr",
                    )
                    translation_profile = _seed_translation_profile(service, "tp-ptbr", "pt-BR")
                    _, episode = self._create_niche_and_episode(client, project_payload={
                        "language_voice_profiles": {
                            "en": shared_voice,
                            "pt-BR": shared_voice,
                        },
                        "language_translation_profiles": {
                            "pt-BR": translation_profile,
                        },
                    })

                    detail = client.get(f"/api/episodes/{episode['id']}")
                    self.assertEqual(detail.status_code, 200)
                    warnings = detail.json()["episode"]["queue_readiness"]["warnings"]
                    self.assertFalse(any(item["code"] == "voice_profile_language_mismatch" for item in warnings))

                    resp = client.post(f"/api/episodes/{episode['id']}/queue", json={})
                    self.assertEqual(resp.status_code, 200)
                    self.assertTrue(resp.json()["queued"])
                finally:
                    self.app_module.service = original

    def test_queue_readiness_ignores_sleeping_voice_engine(self) -> None:
        from tool1_dashboard.tts.manager import WorkerHealth

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path)
                client, original = _make_client(self.app_module, service)
                try:
                    voice_profiles, translation_profiles = _build_profile_assignments(service, temp_path, ["en", "pt-BR"])
                    _, episode = self._create_niche_and_episode(client, project_payload={
                        "language_voice_profiles": voice_profiles,
                        "language_translation_profiles": translation_profiles,
                    })

                    with patch.object(
                        service.tts_manager,
                        "get_worker_health",
                        return_value=WorkerHealth(
                            running=False,
                            worker_id=None,
                            status="sleeping",
                            current_job_id=None,
                            last_heartbeat=None,
                            is_stale=False,
                            pid=None,
                            startup_error=None,
                            missing_dependencies=[],
                            lifecycle_state="sleeping",
                        ),
                    ):
                        detail = client.get(f"/api/episodes/{episode['id']}")
                        self.assertEqual(detail.status_code, 200)
                        warnings = detail.json()["episode"]["queue_readiness"]["warnings"]
                        self.assertFalse(any(item["code"] == "tts_worker_unavailable" for item in warnings))

                        resp = client.post(f"/api/episodes/{episode['id']}/queue", json={})
                        self.assertEqual(resp.status_code, 200)
                        self.assertTrue(resp.json()["queued"])
                finally:
                    self.app_module.service = original

    def test_queue_readiness_warns_when_voice_engine_cannot_start(self) -> None:
        from tool1_dashboard.tts.manager import WorkerHealth

        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path)
                client, original = _make_client(self.app_module, service)
                try:
                    voice_profiles, translation_profiles = _build_profile_assignments(service, temp_path, ["en", "pt-BR"])
                    _, episode = self._create_niche_and_episode(client, project_payload={
                        "language_voice_profiles": voice_profiles,
                        "language_translation_profiles": translation_profiles,
                    })

                    with patch.object(
                        service.tts_manager,
                        "get_worker_health",
                        return_value=WorkerHealth(
                            running=False,
                            worker_id=None,
                            status="unavailable",
                            current_job_id=None,
                            last_heartbeat=None,
                            is_stale=False,
                            pid=None,
                            startup_error="TTS worker failed to start.",
                            missing_dependencies=[],
                            lifecycle_state="unavailable",
                        ),
                    ):
                        detail = client.get(f"/api/episodes/{episode['id']}")
                        self.assertEqual(detail.status_code, 200)
                        warnings = detail.json()["episode"]["queue_readiness"]["warnings"]
                        self.assertTrue(any(item["code"] == "tts_worker_unavailable" for item in warnings))

                        resp = client.post(f"/api/episodes/{episode['id']}/queue", json={})
                        self.assertEqual(resp.status_code, 200)
                        self.assertTrue(resp.json()["queued"])
                finally:
                    self.app_module.service = original

    def test_queue_episode_rejects_when_master_voice_profile_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path)
                client, original = _make_client(self.app_module, service)
                try:
                    _, episode = self._create_niche_and_episode(client, langs=["en"])
                    resp = client.post(f"/api/episodes/{episode['id']}/queue", json={})
                    self.assertEqual(resp.status_code, 400)
                    detail = resp.json()["detail"]
                    self.assertEqual(detail["code"], "queue_blocked")
                    blocker_codes = {item["code"] for item in detail["queue_readiness"]["blockers"]}
                    self.assertIn("missing_voice_profile", blocker_codes)
                finally:
                    self.app_module.service = original

    def test_queue_episode_rejects_when_translation_profile_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path)
                client, original = _make_client(self.app_module, service)
                try:
                    voice_profiles, _ = _build_profile_assignments(
                        service,
                        temp_path,
                        ["en", "pt-BR"],
                        include_translation_for=[],
                    )
                    _, episode = self._create_niche_and_episode(client, project_payload={
                        "language_voice_profiles": voice_profiles,
                    })
                    resp = client.post(f"/api/episodes/{episode['id']}/queue", json={})
                    self.assertEqual(resp.status_code, 400)
                    blocker_codes = {item["code"] for item in resp.json()["detail"]["queue_readiness"]["blockers"]}
                    self.assertIn("missing_translation_profile", blocker_codes)
                finally:
                    self.app_module.service = original

    def test_queue_episode_rejects_when_provider_not_logged_in(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                runner = ProbeStateCliRunner(probe_state={
                    "codex": {"available": True, "logged_in": True},
                    "claude": {"available": True, "logged_in": False},
                })
                service = _make_service(temp_path, cli_runner=runner)
                client, original = _make_client(self.app_module, service)
                try:
                    voice_profiles, _ = _build_profile_assignments(service, temp_path, ["en"], include_translation_for=[])
                    _, episode = self._create_niche_and_episode(client, langs=["en"], project_payload={
                        "language_voice_profiles": voice_profiles,
                        "visual_bible_provider": "claude",
                    })
                    resp = client.post(f"/api/episodes/{episode['id']}/queue", json={})
                    self.assertEqual(resp.status_code, 400)
                    blockers = resp.json()["detail"]["queue_readiness"]["blockers"]
                    self.assertTrue(any(item["code"] == "provider_login_required" for item in blockers))
                finally:
                    self.app_module.service = original

    def test_queue_episode_rejects_when_openai_stage_key_missing(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                runner = ProbeStateCliRunner(probe_state={
                    "codex": {"available": True, "logged_in": True},
                    "claude": {"available": True, "logged_in": True},
                })
                service = _make_service(temp_path, cli_runner=runner)
                client, original = _make_client(self.app_module, service)
                try:
                    voice_profiles, _ = _build_profile_assignments(service, temp_path, ["en"], include_translation_for=[])
                    _, episode = self._create_niche_and_episode(client, langs=["en"], project_payload={
                        "language_voice_profiles": voice_profiles,
                        "visual_bible_provider": "openai",
                        "visual_bible_model": "gpt-5.4-mini",
                    })
                    resp = client.post(f"/api/episodes/{episode['id']}/queue", json={})
                    self.assertEqual(resp.status_code, 400)
                    blockers = resp.json()["detail"]["queue_readiness"]["blockers"]
                    self.assertTrue(any(item["code"] == "provider_api_key_required" for item in blockers))
                finally:
                    self.app_module.service = original

    def test_delete_episode(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path)
                client, original = _make_client(self.app_module, service)
                try:
                    _, episode = self._create_niche_and_episode(client)
                    resp = client.delete(f"/api/episodes/{episode['id']}")
                    self.assertEqual(resp.status_code, 200)
                    resp = client.get(f"/api/episodes/{episode['id']}")
                    self.assertEqual(resp.status_code, 404)
                finally:
                    self.app_module.service = original

    def test_delete_running_episode_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path)
                client, original = _make_client(self.app_module, service)
                try:
                    _, episode = self._create_niche_and_episode(client)
                    service.db.update_episode(episode["id"], pipeline_status="running")
                    resp = client.delete(f"/api/episodes/{episode['id']}")
                    self.assertEqual(resp.status_code, 409)
                finally:
                    self.app_module.service = original

    def test_board_episodes_endpoint(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path)
                client, original = _make_client(self.app_module, service)
                try:
                    self._create_niche_and_episode(client)
                    resp = client.get("/api/board/episodes")
                    self.assertEqual(resp.status_code, 200)
                    episodes = resp.json()["episodes"]
                    self.assertEqual(len(episodes), 1)
                    self.assertIn("language_statuses", episodes[0])
                finally:
                    self.app_module.service = original

    def test_episode_detail_not_found(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path)
                client, original = _make_client(self.app_module, service)
                try:
                    resp = client.get("/api/episodes/nonexistent")
                    self.assertEqual(resp.status_code, 404)
                finally:
                    self.app_module.service = original


class EpisodePipelineServiceTests(unittest.TestCase):
    """Tests for the episode pipeline processing logic at the service layer."""

    def _setup(self, temp_path: Path, runner=None, **project_kwargs):
        """Create a service with FakeCliRunner and a niche project + episode."""
        runner = runner or FakeCliRunner()
        service = _make_service(temp_path, cli_runner=runner)
        payload = {
            "name": "Test Niche",
            "master_language": "en",
            "configured_languages": ["en"],
        }
        payload.update(project_kwargs)
        project = service.create_niche_project(
            **payload,
        )
        return service, project["project"]["id"], runner

    def test_consistency_guide_calls_llm(self) -> None:
        """Consistency guide calls cli_runner.run_structured and writes guide file."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service, pid, runner = self._setup(temp_path)
                episode_result = service.submit_episode(
                    pid,
                    title="Guide Video",
                    script_text="Abraham waits in the desert.\n\nIshmael watches the horizon.",
                )
                episode_id = episode_result["episode"]["id"]

                service._episode_run_consistency_guide(episode_id)

                # Verify LLM was called
                self.assertEqual(len(runner.calls), 1)
                self.assertIn("world_style", runner.calls[0]["schema"]["properties"])

                # Verify guide file
                episode = service.db.get_episode(episode_id)
                self.assertIsNotNone(episode["consistency_guide_path"])
                guide_path = Path(episode["consistency_guide_path"])
                self.assertTrue(guide_path.exists())
                guide = json.loads(guide_path.read_text(encoding="utf-8"))
                self.assertIn("world_style", guide)
                self.assertIn("characters", guide)

    def test_consistency_guide_openai_provider_uses_saved_stage_api_key(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service, pid, runner = self._setup(
                    temp_path,
                    visual_bible_provider="openai",
                    visual_bible_model="gpt-5.4-mini",
                )
                service.db.set_setting("stage_provider_openai_api_key", "sk-stage")
                episode_result = service.submit_episode(
                    pid,
                    title="OpenAI Guide",
                    script_text="Abraham waits in the desert.\n\nIshmael watches the horizon.",
                )
                episode_id = episode_result["episode"]["id"]

                service._episode_run_consistency_guide(episode_id)

                self.assertEqual(len(runner.calls), 1)
                self.assertEqual(runner.calls[0]["provider"], "openai")
                self.assertEqual(runner.calls[0]["api_key"], "sk-stage")

    def test_provider_failure_keeps_episode_failed_at_stage_with_stage_run_log(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                runner = FailingProviderCliRunner(
                    fail_provider="claude",
                    fail_message="Claude limit reached. Run /login or switch provider manually.",
                )
                voice_profiles, _ = _build_profile_assignments(service := _make_service(temp_path, cli_runner=runner), temp_path, ["en"], include_translation_for=[])
                project = service.create_niche_project(
                    name="Provider Failure",
                    master_language="en",
                    configured_languages=["en"],
                    language_voice_profiles=voice_profiles,
                    visual_bible_provider="claude",
                    scene_planning_provider="codex",
                    video_prompt_provider="codex",
                    image_prompt_provider="codex",
                )
                episode_result = service.submit_episode(
                    project["project"]["id"],
                    title="Guide Failure",
                    script_text="One paragraph. Two paragraph.",
                )
                episode_id = episode_result["episode"]["id"]

                service.queue_episode(episode_id)
                service._process_episode(service.db.get_episode(episode_id))

                detail = service.get_episode_detail(episode_id)
                self.assertEqual(detail["episode"]["pipeline_status"], "failed")
                self.assertEqual(detail["episode"]["current_stage"], "consistency_guide")
                self.assertEqual(detail["episode"]["last_error"], "Claude limit reached. Run /login or switch provider manually.")
                self.assertGreaterEqual(len(detail["stage_runs"]), 1)
                latest_run = detail["stage_runs"][0]
                self.assertEqual(latest_run["stage"], "consistency_guide")
                self.assertEqual(latest_run["status"], "failed")
                self.assertIn("Claude limit reached", latest_run["error_message"])

    def test_requeue_after_provider_config_change_restarts_from_failed_stage(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                runner = FailingProviderCliRunner(
                    fail_provider="claude",
                    fail_message="Claude limit reached. Run /login or switch provider manually.",
                )
                service = _make_service(temp_path, cli_runner=runner)
                voice_profiles, _ = _build_profile_assignments(service, temp_path, ["en"], include_translation_for=[])
                project = service.create_niche_project(
                    name="Provider Recovery",
                    master_language="en",
                    configured_languages=["en"],
                    language_voice_profiles=voice_profiles,
                    visual_bible_provider="claude",
                    scene_planning_provider="codex",
                    video_prompt_provider="codex",
                    image_prompt_provider="codex",
                )
                episode_result = service.submit_episode(
                    project["project"]["id"],
                    title="Recovery Episode",
                    script_text="One paragraph. Two paragraph.",
                )
                episode_id = episode_result["episode"]["id"]

                service.queue_episode(episode_id)
                service._process_episode(service.db.get_episode(episode_id))
                failed_episode = service.db.get_episode(episode_id)
                self.assertEqual(failed_episode["current_stage"], "consistency_guide")
                self.assertEqual(failed_episode["pipeline_status"], "failed")

                service.update_niche_project(project["project"]["id"], visual_bible_provider="codex")
                queue_result = service.queue_episode(episode_id)
                self.assertEqual(queue_result["start_stage"], "consistency_guide")
                service._episode_run_consistency_guide(episode_id)

                refreshed_episode = service.db.get_episode(episode_id)
                self.assertIsNotNone(refreshed_episode["consistency_guide_path"])
                detail = service.get_episode_detail(episode_id)
                self.assertEqual(detail["stage_runs"][0]["status"], "completed")
                self.assertEqual(detail["stage_runs"][0]["provider"], "codex")

    def test_running_episode_honors_pause_request_at_stage_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path, cli_runner=FakeCliRunner())
                voice_profiles, _ = _build_profile_assignments(
                    service,
                    temp_path,
                    ["en"],
                    master_language="en",
                    include_translation_for=[],
                )
                project = service.create_niche_project(
                    name="Pause Boundary",
                    master_language="en",
                    configured_languages=["en"],
                    language_voice_profiles=voice_profiles,
                )
                episode_id = service.submit_episode(
                    project["project"]["id"],
                    title="Pause Boundary Episode",
                    script_text="Alpha beta gamma delta.",
                )["episode"]["id"]

                service.queue_episode(episode_id)
                with patch.object(service, "_episode_run_consistency_guide", side_effect=lambda eid: service.pause_episode(eid)):
                    service._process_episode(service.db.get_episode(episode_id))

                paused_episode = service.db.get_episode(episode_id)
                self.assertEqual(paused_episode["pipeline_status"], "paused")
                self.assertEqual(paused_episode["current_stage"], "translation")
                self.assertEqual(paused_episode["queued_from_stage"], "translation")
                self.assertEqual(paused_episode["pause_requested"], 0)

    def test_paused_tts_episode_can_stop_before_alignment(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path, cli_runner=FakeCliRunner())
                voice_profiles, _ = _build_profile_assignments(
                    service,
                    temp_path,
                    ["en"],
                    master_language="en",
                    include_translation_for=[],
                )
                profile_id = voice_profiles["en"]
                project = service.create_niche_project(
                    name="Pause TTS",
                    master_language="en",
                    configured_languages=["en"],
                    language_voice_profiles=voice_profiles,
                )
                episode_id = service.submit_episode(
                    project["project"]["id"],
                    title="Pause During TTS",
                    script_text="Alpha beta gamma delta.",
                )["episode"]["id"]

                audio_path = temp_path / "narration_en.wav"
                audio_path.write_text("fake-audio", encoding="utf-8")
                service.db.update_episode(
                    episode_id,
                    board_status="Running",
                    pipeline_status="paused_for_tts",
                    current_stage="tts",
                    pause_requested=1,
                )
                service.db.update_episode_language_status(
                    episode_id,
                    "en",
                    tts_status="running",
                    tts_job_id="tts-job-pause",
                )
                service.db.create_tts_job({
                    "job_id": "tts-job-pause",
                    "build_id": episode_id,
                    "job_type": "generate",
                    "profile_id": profile_id,
                    "status": "completed",
                    "progress": "Completed",
                    "result_path": str(audio_path),
                    "filename": "narration_en.wav",
                    "payload_json": json.dumps({"texts": ["alpha beta"]}),
                    "meta_json": "{}",
                    "queue_priority": 10,
                    "worker_id": "worker-pause",
                    "control_action": None,
                    "error_message": None,
                    "created_at": 10.0,
                    "updated_at": 20.0,
                    "finished_at": 25.0,
                })

                service._check_paused_tts_episodes()

                paused_episode = service.db.get_episode(episode_id)
                self.assertEqual(paused_episode["pipeline_status"], "paused")
                self.assertEqual(paused_episode["current_stage"], "alignment")
                self.assertEqual(paused_episode["queued_from_stage"], "alignment")
                self.assertEqual(paused_episode["pause_requested"], 0)

    def test_tts_stage_queues_all_pending_languages_before_pause(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path, cli_runner=FakeCliRunner())
                languages = ["en", "es", "fr"]
                voice_profiles, _ = _build_profile_assignments(
                    service,
                    temp_path,
                    languages,
                    master_language="en",
                    include_translation_for=[],
                )
                project = service.create_niche_project(
                    name="Queue All TTS",
                    master_language="en",
                    configured_languages=languages,
                    language_voice_profiles=voice_profiles,
                )
                episode_id = service.submit_episode(
                    project["project"]["id"],
                    title="Queue All TTS Episode",
                    script_text="Alpha beta gamma delta.",
                )["episode"]["id"]

                with patch("tool1_dashboard.tts.manager.TTSManager.ensure_worker_ready", return_value=None):
                    service._episode_run_tts_all(episode_id)

                episode = service.db.get_episode(episode_id)
                self.assertEqual(episode["pipeline_status"], "paused_for_tts")
                self.assertEqual(episode["current_stage"], "tts")
                lang_statuses = service.db.get_episode_language_statuses(episode_id)
                self.assertEqual(len(lang_statuses), 3)
                self.assertTrue(all(ls["tts_status"] == "running" for ls in lang_statuses))
                self.assertTrue(all(ls["tts_job_id"] for ls in lang_statuses))
                jobs = service.db.list_tts_jobs_for_build(episode_id)
                self.assertEqual(len(jobs), 3)
                self.assertTrue(all(job["status"] == "queued" for job in jobs))

    def test_paused_tts_episode_requeues_alignment_after_all_languages_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path, cli_runner=FakeCliRunner())
                languages = ["en", "es", "fr"]
                voice_profiles, _ = _build_profile_assignments(
                    service,
                    temp_path,
                    languages,
                    master_language="en",
                    include_translation_for=[],
                )
                project = service.create_niche_project(
                    name="Resume TTS",
                    master_language="en",
                    configured_languages=languages,
                    language_voice_profiles=voice_profiles,
                )
                episode_id = service.submit_episode(
                    project["project"]["id"],
                    title="Resume All TTS",
                    script_text="Alpha beta gamma delta.",
                )["episode"]["id"]

                service.db.update_episode(
                    episode_id,
                    board_status="Running",
                    pipeline_status="paused_for_tts",
                    current_stage="tts",
                )
                for index, language_code in enumerate(languages, start=1):
                    audio_path = temp_path / f"narration_{language_code}.wav"
                    audio_path.write_text(f"audio-{language_code}", encoding="utf-8")
                    job_id = f"tts-job-{language_code}"
                    service.db.update_episode_language_status(
                        episode_id,
                        language_code,
                        tts_status="running",
                        tts_job_id=job_id,
                    )
                    service.db.create_tts_job({
                        "job_id": job_id,
                        "build_id": episode_id,
                        "job_type": "generate",
                        "profile_id": voice_profiles[language_code],
                        "status": "completed",
                        "progress": "Completed",
                        "result_path": str(audio_path),
                        "filename": audio_path.name,
                        "payload_json": json.dumps({"texts": [f"line-{language_code}"]}),
                        "meta_json": "{}",
                        "queue_priority": 10,
                        "worker_id": f"worker-{language_code}",
                        "control_action": None,
                        "error_message": None,
                        "created_at": float(index),
                        "updated_at": float(index + 10),
                        "finished_at": float(index + 20),
                    })

                service._check_paused_tts_episodes()

                refreshed = service.db.get_episode(episode_id)
                self.assertEqual(refreshed["pipeline_status"], "queued")
                self.assertEqual(refreshed["current_stage"], "alignment")
                self.assertEqual(refreshed["queued_from_stage"], "alignment")
                lang_statuses = service.db.get_episode_language_statuses(episode_id)
                self.assertTrue(all(ls["tts_status"] == "done" for ls in lang_statuses))
                self.assertTrue(all(ls["tts_audio_path"] for ls in lang_statuses))

    def test_paused_tts_episode_backfills_missing_jobs_for_pending_languages(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path, cli_runner=FakeCliRunner())
                languages = ["en", "es"]
                voice_profiles, _ = _build_profile_assignments(
                    service,
                    temp_path,
                    languages,
                    master_language="en",
                    include_translation_for=[],
                )
                project = service.create_niche_project(
                    name="Recover TTS",
                    master_language="en",
                    configured_languages=languages,
                    language_voice_profiles=voice_profiles,
                )
                episode_id = service.submit_episode(
                    project["project"]["id"],
                    title="Recover Missing TTS Job",
                    script_text="Alpha beta gamma delta.",
                )["episode"]["id"]

                audio_path = temp_path / "narration_en.wav"
                audio_path.write_text("audio-en", encoding="utf-8")
                service.db.update_episode(
                    episode_id,
                    board_status="Running",
                    pipeline_status="paused_for_tts",
                    current_stage="tts",
                )
                service.db.update_episode_language_status(
                    episode_id,
                    "en",
                    tts_status="done",
                    tts_audio_path=str(audio_path),
                )
                service.db.update_episode_language_status(
                    episode_id,
                    "es",
                    tts_status="pending",
                    tts_job_id=None,
                )

                with patch("tool1_dashboard.tts.manager.TTSManager.ensure_worker_ready", return_value=None):
                    service._check_paused_tts_episodes()

                refreshed = service.db.get_episode(episode_id)
                self.assertEqual(refreshed["pipeline_status"], "paused_for_tts")
                self.assertEqual(refreshed["current_stage"], "tts")
                es_status = service.db.get_episode_language_status(episode_id, "es")
                self.assertEqual(es_status["tts_status"], "running")
                self.assertTrue(es_status["tts_job_id"])
                jobs = service.db.list_tts_jobs_for_build(episode_id)
                self.assertEqual(len(jobs), 1)
                self.assertEqual(jobs[0]["job_id"], es_status["tts_job_id"])
                self.assertEqual(jobs[0]["status"], "queued")

    def test_paused_tts_episode_requeues_stale_processing_jobs_and_wakes_worker(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path, cli_runner=FakeCliRunner())
                languages = ["en", "es"]
                voice_profiles, _ = _build_profile_assignments(
                    service,
                    temp_path,
                    languages,
                    master_language="en",
                    include_translation_for=[],
                )
                project = service.create_niche_project(
                    name="Recover Stale TTS",
                    master_language="en",
                    configured_languages=languages,
                    language_voice_profiles=voice_profiles,
                )
                episode_id = service.submit_episode(
                    project["project"]["id"],
                    title="Recover Stale Narration",
                    script_text="Alpha beta gamma delta.",
                )["episode"]["id"]

                audio_path = temp_path / "narration_en.wav"
                audio_path.write_text("audio-en", encoding="utf-8")
                service.db.update_episode(
                    episode_id,
                    board_status="Running",
                    pipeline_status="paused_for_tts",
                    current_stage="tts",
                )
                service.db.update_episode_language_status(
                    episode_id,
                    "en",
                    tts_status="done",
                    tts_audio_path=str(audio_path),
                )
                service.db.update_episode_language_status(
                    episode_id,
                    "es",
                    tts_status="running",
                    tts_job_id="tts-job-stale",
                )
                service.db.create_tts_job({
                    "job_id": "tts-job-stale",
                    "build_id": episode_id,
                    "job_type": "generate",
                    "profile_id": voice_profiles["es"],
                    "status": "processing",
                    "progress": "Generating chunk 17/328...",
                    "result_path": None,
                    "filename": "narration_es.wav",
                    "payload_json": json.dumps({"texts": ["uno", "dos", "tres"]}),
                    "meta_json": "{}",
                    "queue_priority": 10,
                    "worker_id": "worker-stale",
                    "control_action": None,
                    "error_message": None,
                    "created_at": 10.0,
                    "updated_at": 0.0,
                    "finished_at": None,
                })

                with patch.object(service.tts_manager, "ensure_worker_ready", return_value=None) as ensure_mock:
                    service._check_paused_tts_episodes()

                refreshed = service.db.get_episode(episode_id)
                self.assertEqual(refreshed["pipeline_status"], "paused_for_tts")
                es_status = service.db.get_episode_language_status(episode_id, "es")
                self.assertEqual(es_status["tts_status"], "running")
                job = service.db.get_tts_job("tts-job-stale")
                self.assertEqual(job["status"], "queued")
                self.assertEqual(job["progress"], "Requeued after worker restart.")
                ensure_mock.assert_called_once_with(intent="pipeline")

    def test_stale_running_provider_stage_is_failed(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service, pid, _ = self._setup(temp_path)
                episode_result = service.submit_episode(
                    pid,
                    title="Stale Guide",
                    script_text="One paragraph. Two paragraph.",
                )
                episode_id = episode_result["episode"]["id"]
                run_id = service.db.start_stage_run(
                    episode_id=episode_id,
                    stage="consistency_guide",
                    provider="codex",
                    template_hash=None,
                    workdir=str(temp_path),
                    command_payload={"provider": "codex", "model": "gpt-5.4-mini"},
                    stdout_path=None,
                    stderr_path=None,
                )
                stale_started_at = (datetime.now(timezone.utc) - timedelta(seconds=120)).isoformat()
                service.db._execute("UPDATE stage_runs SET started_at = ? WHERE id = ?", (stale_started_at, run_id))
                service.db.update_episode(
                    episode_id,
                    board_status="Running",
                    pipeline_status="running",
                    current_stage="consistency_guide",
                )
                service._provider_stage_stale_seconds = 60

                service._check_stale_provider_stage_runs()

                episode = service.db.get_episode(episode_id)
                self.assertEqual(episode["pipeline_status"], "failed")
                self.assertEqual(episode["board_status"], "Needs Attention")
                self.assertIn("timed out after 60 seconds", episode["last_error"])
                detail = service.get_episode_detail(episode_id)
                self.assertEqual(detail["stage_runs"][0]["status"], "failed")
                self.assertIn("timed out after 60 seconds", detail["stage_runs"][0]["error_message"])

    def test_translations_skip_without_profiles(self) -> None:
        """Translations skip languages that have no translation profile configured."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path, cli_runner=FakeCliRunner())
                project = service.create_niche_project(
                    name="Multi-Lang",
                    master_language="en",
                    configured_languages=["en", "pt-BR", "es"],
                    # No translation profiles assigned
                )
                pid = project["project"]["id"]
                episode_result = service.submit_episode(pid, title="Trans Video", script_text="Hello world")
                episode_id = episode_result["episode"]["id"]

                service._episode_run_translations(episode_id)

                lang_statuses = service.db.get_episode_language_statuses(episode_id)
                en = next(ls for ls in lang_statuses if ls["language_code"] == "en")
                ptbr = next(ls for ls in lang_statuses if ls["language_code"] == "pt-BR")
                es = next(ls for ls in lang_statuses if ls["language_code"] == "es")

                # Master is done
                self.assertEqual(en["translation_status"], "done")
                self.assertIsNotNone(en["script_path"])

                # Non-master skipped (no profiles)
                self.assertEqual(ptbr["translation_status"], "skipped")
                self.assertEqual(es["translation_status"], "skipped")

    def test_translations_with_mock_service(self) -> None:
        """Translations call TranslationService when profiles are configured."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path, cli_runner=FakeCliRunner())

                # Create a translation profile
                from tool1_dashboard.runtime import utc_now
                now = utc_now()
                service.db.create_translation_profile({
                    "id": "tp-ptbr",
                    "name": "PT-BR DeepL",
                    "provider": "deepl",
                    "api_key_ref": "fake-key",
                    "model": "deepl-v2",
                    "created_at": now,
                    "updated_at": now,
                })

                project = service.create_niche_project(
                    name="Trans Niche",
                    master_language="en",
                    configured_languages=["en", "pt-BR"],
                    language_translation_profiles={"pt-BR": "tp-ptbr"},
                )
                pid = project["project"]["id"]
                episode_result = service.submit_episode(pid, title="Trans Video", script_text="Hello world")
                episode_id = episode_result["episode"]["id"]

                # Mock TranslationService to avoid real API calls
                mock_result = MagicMock()
                mock_result.translated_script = "Olá mundo"
                mock_result.chunk_results = []
                with patch("tool1_dashboard.translation.TranslationService") as MockTS:
                    mock_instance = MockTS.return_value
                    mock_instance.translate_script = MagicMock(return_value=mock_result)
                    with patch("tool1_dashboard.service.asyncio") as mock_asyncio:
                        mock_asyncio.run.return_value = mock_result
                        service._episode_run_translations(episode_id)

                lang_statuses = service.db.get_episode_language_statuses(episode_id)
                ptbr = next(ls for ls in lang_statuses if ls["language_code"] == "pt-BR")
                self.assertEqual(ptbr["translation_status"], "done")
                self.assertIsNotNone(ptbr["script_path"])
                self.assertEqual(Path(ptbr["script_path"]).read_text(encoding="utf-8"), "Olá mundo")

    def test_chunking_produces_manifest(self) -> None:
        """Chunking stage parses SRT and produces manifest with chunk files."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service, pid, runner = self._setup(temp_path)
                episode_result = service.submit_episode(pid, title="Chunk Video", script_text="Test script")
                episode_id = episode_result["episode"]["id"]
                workspace = Path(service.db.get_episode(episode_id)["workspace_dir"])

                # Write a real SRT for the master language
                srt_content = (
                    "1\n00:00:00,000 --> 00:00:03,000\nFirst subtitle.\n\n"
                    "2\n00:00:03,000 --> 00:00:06,000\nSecond subtitle.\n\n"
                    "3\n00:00:06,000 --> 00:00:09,000\nThird subtitle.\n"
                )
                srt_path = workspace / "final_en.srt"
                srt_path.write_text(srt_content, encoding="utf-8")
                service.db.update_episode_language_status(
                    episode_id, "en", srt_status="done", srt_path=str(srt_path),
                )

                service._episode_run_chunking(episode_id)

                episode = service.db.get_episode(episode_id)
                self.assertIsNotNone(episode["planning_manifest_path"])
                manifest = json.loads(Path(episode["planning_manifest_path"]).read_text(encoding="utf-8"))
                self.assertIn("chunks", manifest)
                self.assertGreater(len(manifest["chunks"]), 0)

    def test_scene_planning_calls_llm_per_chunk(self) -> None:
        """Scene planning calls LLM for each chunk and produces timeline."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service, pid, runner = self._setup(temp_path)
                episode_result = service.submit_episode(pid, title="Scene Video", script_text="Test script")
                episode_id = episode_result["episode"]["id"]
                workspace = Path(service.db.get_episode(episode_id)["workspace_dir"])

                # Write SRT and run chunking first
                srt_content = (
                    "1\n00:00:00,000 --> 00:00:03,000\nFirst subtitle.\n\n"
                    "2\n00:00:03,000 --> 00:00:06,000\nSecond subtitle.\n"
                )
                srt_path = workspace / "final_en.srt"
                srt_path.write_text(srt_content, encoding="utf-8")
                service.db.update_episode_language_status(
                    episode_id, "en", srt_status="done", srt_path=str(srt_path),
                )
                service._episode_run_chunking(episode_id)

                service._episode_run_scene_planning(episode_id)

                # LLM was called (at least once per chunk)
                scene_calls = [c for c in runner.calls if "scenes" in c["schema"].get("properties", {})]
                self.assertGreater(len(scene_calls), 0)

                episode = service.db.get_episode(episode_id)
                self.assertIsNotNone(episode["timeline_draft_path"])
                timeline = json.loads(Path(episode["timeline_draft_path"]).read_text(encoding="utf-8"))
                self.assertIsInstance(timeline, list)
                self.assertGreater(len(timeline), 0)

    def test_save_master_scenes_copies_timeline(self) -> None:
        """Save master scenes creates a copy of the timeline draft."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service, pid, runner = self._setup(temp_path)
                episode_result = service.submit_episode(pid, title="Save Video", script_text="Test")
                episode_id = episode_result["episode"]["id"]
                workspace = Path(service.db.get_episode(episode_id)["workspace_dir"])

                # Write a fake timeline draft
                timeline_data = [
                    {"scene_id": "s01", "start": 0.0, "end": 3.0, "asset_type": "video", "text": "test"},
                ]
                from tool1_dashboard.runtime import write_json
                timeline_path = write_json(workspace / "timeline_draft.json", timeline_data)
                service.db.update_episode(episode_id, timeline_draft_path=str(timeline_path))

                service._episode_save_master_scenes(episode_id)

                episode = service.db.get_episode(episode_id)
                self.assertIsNotNone(episode["master_scenes_path"])
                master = json.loads(Path(episode["master_scenes_path"]).read_text(encoding="utf-8"))
                self.assertEqual(len(master), 1)
                self.assertEqual(master[0]["scene_id"], "s01")

    def test_timeline_mapping_proportional(self) -> None:
        """Timeline mapping creates proportionally scaled timelines per language."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path, cli_runner=FakeCliRunner())
                project = service.create_niche_project(
                    name="TL Map",
                    master_language="en",
                    configured_languages=["en", "pt-BR"],
                )
                pid = project["project"]["id"]
                episode_result = service.submit_episode(pid, title="TL Video", script_text="Test")
                episode_id = episode_result["episode"]["id"]
                workspace = Path(service.db.get_episode(episode_id)["workspace_dir"])

                # Write master timeline (10s total)
                from tool1_dashboard.runtime import write_json
                timeline_data = [
                    {"scene_id": "s01", "start": 0.0, "end": 5.0, "asset_type": "video", "text": "first"},
                    {"scene_id": "s02", "start": 5.0, "end": 10.0, "asset_type": "image", "text": "second"},
                ]
                timeline_path = write_json(workspace / "timeline_draft.json", timeline_data)
                service.db.update_episode(episode_id, timeline_draft_path=str(timeline_path))

                # Write master SRT (10s total)
                master_srt = (
                    "1\n00:00:00,000 --> 00:00:05,000\nFirst subtitle.\n\n"
                    "2\n00:00:05,000 --> 00:00:10,000\nSecond subtitle.\n"
                )
                master_srt_path = workspace / "final_en.srt"
                master_srt_path.write_text(master_srt, encoding="utf-8")
                service.db.update_episode_language_status(
                    episode_id, "en", srt_status="done", srt_path=str(master_srt_path),
                )

                # Write pt-BR SRT (12s total — 1.2x ratio)
                ptbr_srt = (
                    "1\n00:00:00,000 --> 00:00:06,000\nPrimeira legenda.\n\n"
                    "2\n00:00:06,000 --> 00:00:12,000\nSegunda legenda.\n"
                )
                ptbr_srt_path = workspace / "final_pt-BR.srt"
                ptbr_srt_path.write_text(ptbr_srt, encoding="utf-8")
                service.db.update_episode_language_status(
                    episode_id, "pt-BR", srt_status="done", srt_path=str(ptbr_srt_path),
                )

                service._episode_run_timeline_mapping(episode_id)

                # Check master (just copied)
                en_status = service.db.get_episode_language_status(episode_id, "en")
                self.assertEqual(en_status["timeline_status"], "done")
                en_tl = json.loads(Path(en_status["timeline_path"]).read_text(encoding="utf-8"))
                self.assertEqual(len(en_tl), 2)
                self.assertEqual(en_tl[0]["start"], 0.0)
                self.assertEqual(en_tl[1]["end"], 10.0)

                # Check pt-BR (scaled by 1.2x ratio)
                ptbr_status = service.db.get_episode_language_status(episode_id, "pt-BR")
                self.assertEqual(ptbr_status["timeline_status"], "done")
                ptbr_tl = json.loads(Path(ptbr_status["timeline_path"]).read_text(encoding="utf-8"))
                self.assertEqual(len(ptbr_tl), 2)
                self.assertEqual(ptbr_tl[0]["start"], 0.0)
                # Last scene end should be clamped to lang total (12.0)
                self.assertEqual(ptbr_tl[1]["end"], 12.0)
                # First scene end should be approximately 6.0 (5.0 * 1.2)
                self.assertAlmostEqual(ptbr_tl[0]["end"], 6.0, places=1)

    def test_niche_project_auto_includes_master_language(self) -> None:
        """Master language is always included in configured_languages."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path)
                project = service.create_niche_project(
                    name="Auto-include test",
                    master_language="en",
                    configured_languages=["pt-BR", "es"],
                )
                p = service.db.get_niche_project(project["project"]["id"])
                langs = json.loads(p["configured_languages"])
                self.assertIn("en", langs)
                self.assertEqual(langs[0], "en")

    def test_niche_project_episodes_appear_in_board(self) -> None:
        """Episodes submitted to niche project appear in board endpoint."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path)
                project = service.create_niche_project(
                    name="Board Test",
                    master_language="en",
                    configured_languages=["en", "pt-BR"],
                )
                pid = project["project"]["id"]
                service.submit_episode(pid, title="Video A", script_text="Script A")
                service.submit_episode(pid, title="Video B", script_text="Script B")

                board = service.list_all_episodes_for_board()
                self.assertEqual(len(board), 2)
                titles = {v["title"] for v in board}
                self.assertEqual(titles, {"Video A", "Video B"})
                for v in board:
                    self.assertEqual(len(v["language_statuses"]), 2)

    def test_settings_payload_does_not_upsert_templates_on_read(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path, cli_runner=FakeCliRunner())
                with patch.object(service.db, "upsert_template", side_effect=AssertionError("settings read should not write templates")):
                    payload = service.get_settings_payload()
                self.assertIn("templates", payload)
                self.assertGreater(len(payload["templates"]), 0)
                self.assertIn("voice_tts_presets", payload["settings"])
                self.assertIn("voice_tts_limits", payload["settings"])
                self.assertEqual(payload["settings"]["voice_tts_default_preset"], "natural_stable")

    def test_episode_tts_submission_chunks_text_and_snapshots_profile_tuning(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path, cli_runner=FakeCliRunner())
                voice_profiles, _ = _build_profile_assignments(
                    service,
                    temp_path,
                    ["pt-BR"],
                    master_language="pt-BR",
                    include_translation_for=[],
                )
                profile_id = voice_profiles["pt-BR"]
                service.update_voice_profile(
                    profile_id,
                    tts_config={
                        "preset": "expressive",
                        "temperature": 0.78,
                        "top_p": 0.9,
                        "top_k": 55,
                        "speed": 1.01,
                        "chunk_max_chars": 120,
                        "silence_gap_seconds": 0.18,
                    },
                )
                project = service.create_niche_project(
                    name="TTS Chunking",
                    master_language="pt-BR",
                    configured_languages=["pt-BR"],
                    language_voice_profiles=voice_profiles,
                )
                episode = service.submit_episode(
                    project["project"]["id"],
                    title="Long narration",
                    script_text=" ".join(["Primeira frase com ritmo realista."] * 40),
                )["episode"]

                with patch("tool1_dashboard.tts.manager.TTSManager.ensure_worker_ready") as ensure_mock, patch(
                    "tool1_dashboard.tts.manager.TTSManager.submit_tts_job",
                    return_value="tts-job-1",
                ) as submit_mock:
                    service._episode_run_tts_all(episode["id"])

                ensure_mock.assert_called_once_with(intent="pipeline")
                payload = submit_mock.call_args.kwargs["payload"]
                self.assertTrue(payload["chunked"])
                self.assertGreater(len(payload["texts"]), 1)
                self.assertEqual(payload["language"], "pt")
                self.assertEqual(payload["tts_config"]["preset"], "expressive")
                self.assertEqual(payload["tts_config"]["chunk_max_chars"], 120)
                self.assertEqual(payload["tts_config"]["silence_gap_seconds"], 0.18)

    def test_episode_detail_and_board_include_live_tts_progress(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with _patches(temp_path)[0], _patches(temp_path)[1], _patches(temp_path)[2]:
                service = _make_service(temp_path, cli_runner=FakeCliRunner())
                voice_profiles, _ = _build_profile_assignments(
                    service,
                    temp_path,
                    ["en"],
                    master_language="en",
                    include_translation_for=[],
                )
                profile_id = voice_profiles["en"]
                project = service.create_niche_project(
                    name="TTS Visibility",
                    master_language="en",
                    configured_languages=["en"],
                    language_voice_profiles=voice_profiles,
                )
                episode = service.submit_episode(
                    project["project"]["id"],
                    title="Live TTS",
                    script_text="Alpha beta gamma delta.",
                )["episode"]
                episode_id = episode["id"]

                now = utc_now()
                service.db.update_episode(
                    episode_id,
                    board_status="Running",
                    pipeline_status="paused_for_tts",
                    current_stage="tts",
                    updated_at=now,
                )
                service.db.update_episode_language_status(
                    episode_id,
                    "en",
                    tts_status="running",
                    tts_job_id="tts-job-live",
                )
                service.db.create_tts_job({
                    "job_id": "tts-job-live",
                    "build_id": episode_id,
                    "job_type": "generate",
                    "profile_id": profile_id,
                    "status": "processing",
                    "progress": "Generating chunk 2/4...",
                    "result_path": None,
                    "filename": "narration_en.wav",
                    "payload_json": json.dumps({
                        "texts": ["one", "two", "three", "four"],
                        "chunked": True,
                    }),
                    "meta_json": "{}",
                    "queue_priority": 10,
                    "worker_id": "worker-live",
                    "control_action": None,
                    "error_message": None,
                    "created_at": 10.0,
                    "updated_at": 20.0,
                    "finished_at": None,
                })

                detail = service.get_episode_detail(episode_id)
                lang_status = detail["language_statuses"][0]
                self.assertEqual(lang_status["tts_job_status"], "processing")
                self.assertEqual(lang_status["tts_job_current_chunk"], 2)
                self.assertEqual(lang_status["tts_job_total_chunks"], 4)
                self.assertEqual(lang_status["tts_job_percent"], 50)

                active_job = detail["episode"]["active_tts_job"]
                self.assertIsNotNone(active_job)
                self.assertEqual(active_job["language_code"], "en")
                self.assertEqual(active_job["current_chunk"], 2)
                self.assertEqual(active_job["total_chunks"], 4)
                self.assertEqual(active_job["percent"], 50)

                board_episode = service.list_all_episodes_for_board()[0]
                self.assertEqual(board_episode["active_tts_job"]["job_id"], "tts-job-live")
                self.assertEqual(board_episode["language_statuses"][0]["tts_job_percent"], 50)


if __name__ == "__main__":
    unittest.main()
