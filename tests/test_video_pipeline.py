from __future__ import annotations

import importlib
import json
import tempfile
import unittest
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

    def probe(self) -> dict[str, object]:
        return {
            "codex": {"available": True, "logged_in": True},
            "claude": {"available": True, "logged_in": True},
        }

    def run_structured(self, *, provider, model, system_prompt, user_prompt, schema, workdir, artifact_dir):
        artifact_dir.mkdir(parents=True, exist_ok=True)
        self.calls.append({
            "provider": provider,
            "model": model,
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

    def probe(self) -> dict[str, object]:
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

    def run_structured(self, *, provider, model, system_prompt, user_prompt, schema, workdir, artifact_dir):
        artifact_dir.mkdir(parents=True, exist_ok=True)
        self.calls.append({
            "provider": provider,
            "model": model,
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


def _seed_voice_profile(service: Tool1Service, temp_path: Path, profile_id: str, language_code: str) -> str:
    audio_path = temp_path / f"{profile_id}.wav"
    audio_path.write_bytes(b"RIFF....WAVEfmt ")
    now = utc_now()
    service.db.create_voice_profile({
        "id": profile_id,
        "name": f"Voice {language_code}",
        "language_code": language_code,
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


if __name__ == "__main__":
    unittest.main()
