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
            with patch("tool1_dashboard.service.VIDEOS_ROOT", temp_path / "videos"), \
                 patch("tool1_dashboard.service.AGENTS_ROOT", temp_path / "config" / "agents"), \
                 patch("tool1_dashboard.templates.AGENTS_ROOT", temp_path / "config" / "agents"):
                service = Tool1Service(db=Tool1Database(temp_path / "db.sqlite"))
                original_service = app_module.service
                app_module.service = service
                try:
                    client = TestClient(app_module.app)
                    root = client.get("/")
                    self.assertEqual(root.status_code, 200)
                    self.assertIn('/ui/app.css', root.text)
                    self.assertIn('/ui/app.js', root.text)

                    css = client.get("/ui/app.css")
                    self.assertEqual(css.status_code, 200)
                    self.assertIn("--bg", css.text)

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
                            "leading_video_scene_count": "20",
                        },
                        files={
                            "audio_file": ("voice.wav", b"RIFF", "audio/wav"),
                            "script_file": ("script.txt", b"Hello world", "text/plain"),
                        },
                    )
                    self.assertEqual(create.status_code, 200)
                    job_id = create.json()["job"]["id"]

                    config = client.post(
                        f"/api/jobs/{job_id}/config",
                        json={
                            "scene_planning_provider": "codex",
                            "visual_bible_provider": "claude",
                            "video_prompt_provider": "codex",
                            "image_prompt_provider": "claude",
                            "leading_video_scene_count": 12,
                        },
                    )
                    self.assertEqual(config.status_code, 200)
                    self.assertEqual(config.json()["job"]["leading_video_scene_count"], 12)

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


if __name__ == "__main__":
    unittest.main()
