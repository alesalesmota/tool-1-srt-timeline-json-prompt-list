from __future__ import annotations

import importlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from tool1_dashboard.database import Tool1Database
from tool1_dashboard.service import Tool1Service


class ApiTests(unittest.TestCase):
    def test_create_config_visual_bible_template_and_artifact_routes(self) -> None:
        app_module = importlib.import_module("tool1_dashboard.app")
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with patch("tool1_dashboard.service.EPISODES_ROOT", temp_path / "videos"), \
                 patch("tool1_dashboard.service.AGENTS_ROOT", temp_path / "config" / "agents"), \
                 patch("tool1_dashboard.templates.AGENTS_ROOT", temp_path / "config" / "agents"):
                service = Tool1Service(db=Tool1Database(temp_path / "db.sqlite"))
                original_service = app_module.service
                app_module.service = service
                try:
                    client = TestClient(app_module.app)
                    root = client.get("/")
                    self.assertEqual(root.status_code, 200)
                    self.assertIn('/ui/app.css?v=', root.text)
                    self.assertIn('/ui/app.js?v=', root.text)
                    self.assertEqual(root.headers.get("cache-control"), "no-store, no-cache, must-revalidate, max-age=0")

                    css = client.get("/ui/app.css")
                    self.assertEqual(css.status_code, 200)
                    self.assertIn("--bg", css.text)
                    self.assertEqual(css.headers.get("cache-control"), "no-store, no-cache, must-revalidate, max-age=0")

                    js = client.get("/ui/app.js")
                    self.assertEqual(js.status_code, 200)
                    self.assertIn("renderDashboard", js.text)

                    create = client.post(
                        "/api/jobs",
                        data={
                            "title": "API Video",
                            "language_code": "en",
                            "scene_planning_provider": "claude",
                            "visual_bible_provider": "claude",
                            "video_prompt_provider": "codex",
                            "image_prompt_provider": "codex",
                            "scene_planning_model": "haiku",
                            "visual_bible_model": "sonnet",
                            "video_prompt_model": "gpt-5.4",
                            "image_prompt_model": "gpt-5.4-mini",
                            "leading_video_scene_count": "20",
                        },
                        files={
                            "audio_file": ("voice.wav", b"RIFF", "audio/wav"),
                            "script_file": ("script.txt", b"Hello world", "text/plain"),
                        },
                    )
                    self.assertEqual(create.status_code, 200)
                    job_id = create.json()["job"]["id"]
                    self.assertEqual(create.json()["job"]["scene_planning_model"], "haiku")
                    self.assertEqual(create.json()["job"]["visual_bible_model"], "sonnet")
                    self.assertEqual(create.json()["job"]["video_prompt_model"], "gpt-5.4")
                    self.assertEqual(create.json()["job"]["image_prompt_model"], "gpt-5.4-mini")

                    config = client.post(
                        f"/api/jobs/{job_id}/config",
                        json={
                            "scene_planning_provider": "codex",
                            "visual_bible_provider": "claude",
                            "video_prompt_provider": "codex",
                            "image_prompt_provider": "claude",
                            "scene_planning_model": "gpt-5.4-mini",
                            "visual_bible_model": "haiku",
                            "video_prompt_model": "gpt-5.2-codex",
                            "image_prompt_model": "opus",
                            "leading_video_scene_count": 12,
                        },
                    )
                    self.assertEqual(config.status_code, 200)
                    self.assertEqual(config.json()["job"]["leading_video_scene_count"], 12)
                    self.assertEqual(config.json()["job"]["scene_planning_model"], "gpt-5.4-mini")
                    self.assertEqual(config.json()["job"]["visual_bible_model"], "haiku")
                    self.assertEqual(config.json()["job"]["video_prompt_model"], "gpt-5.2-codex")
                    self.assertEqual(config.json()["job"]["image_prompt_model"], "opus")

                    bible = client.post(
                        f"/api/jobs/{job_id}/review/visual-bible",
                        json={
                            "visual_bible": {
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
                                        "visual_description": "Elderly man with olive skin and a weathered face",
                                        "wardrobe": "Rough wool robe and wooden staff",
                                        "demeanor": "Calm but urgent",
                                        "usage_notes": "Keep him rugged and serious",
                                    }
                                ],
                                "continuity_rules": ["Keep the same prophet face and robe silhouette."],
                                "environment_rules": ["Preserve a windblown desert atmosphere."],
                            }
                        },
                    )
                    self.assertEqual(bible.status_code, 200)
                    self.assertTrue(bible.json()["job"]["visual_bible_path"])

                    review_dir = temp_path / "videos" / job_id / "review"
                    diagnostics_dir = temp_path / "videos" / job_id / "diagnostics"
                    review_dir.mkdir(parents=True, exist_ok=True)
                    diagnostics_dir.mkdir(parents=True, exist_ok=True)
                    timeline_path = review_dir / "timeline_draft.json"
                    timeline_path.write_text(
                        '[{"scene_id":"scene_001","start":0,"end":4,"duration":4,"text":"Opening scene","asset_type":"video"},'
                        '{"scene_id":"scene_002","start":4,"end":8,"duration":4,"text":"Closing scene","asset_type":"image"}]',
                        encoding="utf-8",
                    )
                    service.db.update_job(job_id, timeline_draft_path=str(timeline_path))

                    prompts = client.post(
                        f"/api/jobs/{job_id}/review/prompts",
                        json={
                            "video_prompts": [
                                "SUBJ: elderly prophet with olive skin and weathered face; SET: desert camp at dusk; ACT: he steps from a tent; CAM: slow push in; LOOK: cinematic realism; LIGHT: warm dusk firelight; RULES: no text, no watermark"
                            ],
                            "image_prompts": [
                                "SUBJ: desert camp and weathered prophet; SET: barren ridge at dusk; COMP: medium-wide frame with tent and horizon; LOOK: cinematic realism; LIGHT: warm dusk firelight; RULES: no text, no watermark"
                            ],
                        },
                    )
                    self.assertEqual(prompts.status_code, 200)
                    self.assertIn("video_prompt_list_draft", prompts.json()["artifacts"])
                    self.assertIn("image_prompt_list_draft", prompts.json()["artifacts"])

                    video_draft = client.get(f"/api/jobs/{job_id}/artifacts/video_prompt_list_draft")
                    self.assertEqual(video_draft.status_code, 200)
                    self.assertIn("ACT:", video_draft.text)

                    image_draft = client.get(f"/api/jobs/{job_id}/artifacts/image_prompt_list_draft")
                    self.assertEqual(image_draft.status_code, 200)
                    self.assertIn("COMP:", image_draft.text)

                    template = client.post(
                        "/api/templates/visual_bible/claude",
                        json={"body": "new visual bible template"},
                    )
                    self.assertEqual(template.status_code, 200)
                    self.assertEqual(template.json()["body"], "new visual bible template")

                    final_srt = temp_path / "videos" / job_id / "review" / "final.srt"
                    final_srt.parent.mkdir(parents=True, exist_ok=True)
                    final_srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
                    service.db.update_job(job_id, final_srt_path=str(final_srt))

                    artifact = client.get(f"/api/jobs/{job_id}/artifacts/final_srt")
                    self.assertEqual(artifact.status_code, 200)
                    self.assertIn("Hello", artifact.text)
                finally:
                    app_module.service = original_service

    def test_delete_job_removes_workspace_and_alignment_artifacts(self) -> None:
        app_module = importlib.import_module("tool1_dashboard.app")
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with patch("tool1_dashboard.service.EPISODES_ROOT", temp_path / "videos"), \
                 patch("tool1_dashboard.service.AGENTS_ROOT", temp_path / "config" / "agents"), \
                 patch("tool1_dashboard.templates.AGENTS_ROOT", temp_path / "config" / "agents"), \
                 patch("tool1_dashboard.service.ALIGNMENT_OUTPUT_ROOT", temp_path / "alignment_output"), \
                 patch("tool1_dashboard.service.ALIGNMENT_TEMP_ROOT", temp_path / "alignment_temp"):
                service = Tool1Service(db=Tool1Database(temp_path / "db.sqlite"))
                original_service = app_module.service
                app_module.service = service
                try:
                    client = TestClient(app_module.app)
                    create = client.post(
                        "/api/jobs",
                        data={
                            "title": "Delete Me",
                            "language_code": "en",
                            "scene_planning_model": "haiku",
                            "visual_bible_model": "haiku",
                            "video_prompt_model": "gpt-5.4",
                            "image_prompt_model": "gpt-5.4",
                        },
                        files={
                            "audio_file": ("voice.wav", b"RIFF", "audio/wav"),
                            "script_file": ("script.txt", b"Hello world", "text/plain"),
                        },
                    )
                    self.assertEqual(create.status_code, 200)
                    job_id = create.json()["job"]["id"]

                    workspace_dir = temp_path / "videos" / job_id
                    self.assertTrue(workspace_dir.exists())

                    run_log = workspace_dir / "runs" / "alignment" / "fake-stage" / "artifacts" / "fake-run" / "run.log"
                    run_log.parent.mkdir(parents=True, exist_ok=True)
                    run_log.write_text("ok", encoding="utf-8")

                    alignment_output = temp_path / "alignment_output" / "fake-run"
                    alignment_temp = temp_path / "alignment_temp" / "fake-run"
                    alignment_output.mkdir(parents=True, exist_ok=True)
                    alignment_temp.mkdir(parents=True, exist_ok=True)
                    (alignment_output / "final.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n", encoding="utf-8")
                    (alignment_temp / "temp.txt").write_text("temp", encoding="utf-8")

                    run_id = service.db.start_stage_run(
                        job_id,
                        "alignment",
                        None,
                        None,
                        str(workspace_dir),
                        {"stage": "alignment"},
                        str(run_log),
                        None,
                    )
                    service.db.finish_stage_run(run_id, status="completed", exit_code=0, stdout_path=str(run_log))

                    deleted = client.delete(f"/api/jobs/{job_id}")
                    self.assertEqual(deleted.status_code, 200)
                    self.assertTrue(deleted.json()["deleted"])
                    self.assertFalse(workspace_dir.exists())
                    self.assertFalse(alignment_output.exists())
                    self.assertFalse(alignment_temp.exists())

                    detail = client.get(f"/api/jobs/{job_id}")
                    self.assertEqual(detail.status_code, 404)
                finally:
                    app_module.service = original_service

    def test_delete_job_rejects_running_card(self) -> None:
        app_module = importlib.import_module("tool1_dashboard.app")
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_path = Path(temp_dir)
            with patch("tool1_dashboard.service.EPISODES_ROOT", temp_path / "videos"), \
                 patch("tool1_dashboard.service.AGENTS_ROOT", temp_path / "config" / "agents"), \
                 patch("tool1_dashboard.templates.AGENTS_ROOT", temp_path / "config" / "agents"):
                service = Tool1Service(db=Tool1Database(temp_path / "db.sqlite"))
                original_service = app_module.service
                app_module.service = service
                try:
                    client = TestClient(app_module.app)
                    create = client.post(
                        "/api/jobs",
                        data={
                            "title": "Busy Card",
                            "language_code": "en",
                            "scene_planning_model": "haiku",
                            "visual_bible_model": "haiku",
                            "video_prompt_model": "gpt-5.4",
                            "image_prompt_model": "gpt-5.4",
                        },
                        files={
                            "audio_file": ("voice.wav", b"RIFF", "audio/wav"),
                            "script_file": ("script.txt", b"Hello world", "text/plain"),
                        },
                    )
                    self.assertEqual(create.status_code, 200)
                    job_id = create.json()["job"]["id"]

                    service.db.update_job(job_id, board_status="Running", pipeline_status="running", current_stage="scene_planning")
                    service.db.start_stage_run(
                        job_id,
                        "scene_planning",
                        "claude",
                        None,
                        str(temp_path / "videos" / job_id),
                        {"stage": "scene_planning"},
                        None,
                        None,
                    )

                    deleted = client.delete(f"/api/jobs/{job_id}")
                    self.assertEqual(deleted.status_code, 400)
                    self.assertIn("running right now", deleted.json()["detail"])
                    self.assertTrue((temp_path / "videos" / job_id).exists())
                finally:
                    app_module.service = original_service


if __name__ == "__main__":
    unittest.main()
