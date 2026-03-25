"""Tests for the build pipeline (Phase 4): master + localization build processing."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from tool1_dashboard.database import Tool1Database
from tool1_dashboard.runtime import utc_now


def _make_db() -> tuple[str, Tool1Database]:
    tmpdir = tempfile.mkdtemp()
    db = Tool1Database(Path(tmpdir) / "test.db")
    db.initialize()
    return tmpdir, db


def _make_project(db: Tool1Database, tmpdir: str, project_id: str = "proj1") -> dict[str, Any]:
    now = utc_now()
    script_path = Path(tmpdir) / "script.txt"
    script_path.write_text("Hello world.", encoding="utf-8")
    audio_path = Path(tmpdir) / "audio.wav"
    audio_path.write_bytes(b"\x00" * 100)
    db.create_project({
        "id": project_id,
        "title": "Test Project",
        "source_language": "en",
        "workspace_dir": tmpdir,
        "audio_filename": "audio.wav",
        "script_filename": "script.txt",
        "audio_path": str(audio_path),
        "script_path": str(script_path),
        "created_at": now,
        "updated_at": now,
    })
    return db.get_project(project_id)


def _make_master_build(db: Tool1Database, tmpdir: str, project_id: str = "proj1") -> dict[str, Any]:
    build_id = f"{project_id}-master"
    now = utc_now()
    db.create_build({
        "id": build_id,
        "project_id": project_id,
        "build_type": "master",
        "language_code": "en",
        "board_status": "Draft",
        "pipeline_status": "idle",
        "current_stage": "draft",
        "queued_from_stage": "alignment",
        "audio_path": str(Path(tmpdir) / "audio.wav"),
        "script_path": str(Path(tmpdir) / "script.txt"),
        "workspace_dir": tmpdir,
        "created_at": now,
        "updated_at": now,
    })
    return db.get_build(build_id)


def _make_localization_build(
    db: Tool1Database,
    tmpdir: str,
    project_id: str = "proj1",
    lang: str = "pt-BR",
    translation_profile_id: str | None = None,
    voice_profile_id: str | None = None,
) -> dict[str, Any]:
    build_id = f"{project_id}-{lang}"
    now = utc_now()
    db.create_build({
        "id": build_id,
        "project_id": project_id,
        "build_type": "localization",
        "language_code": lang,
        "board_status": "Draft",
        "pipeline_status": "idle",
        "current_stage": "translation",
        "queued_from_stage": "translation",
        "translation_profile_id": translation_profile_id,
        "voice_profile_id": voice_profile_id,
        "workspace_dir": tmpdir,
        "created_at": now,
        "updated_at": now,
    })
    return db.get_build(build_id)


class DbProfileColumnsTests(unittest.TestCase):

    def test_builds_table_has_profile_columns(self):
        tmpdir, db = _make_db()
        project = _make_project(db, tmpdir)
        build = _make_localization_build(
            db, tmpdir, translation_profile_id="tp1", voice_profile_id="vp1",
        )
        self.assertEqual(build["translation_profile_id"], "tp1")
        self.assertEqual(build["voice_profile_id"], "vp1")

    def test_profile_columns_nullable(self):
        tmpdir, db = _make_db()
        _make_project(db, tmpdir)
        build = _make_localization_build(db, tmpdir)
        self.assertIsNone(build["translation_profile_id"])
        self.assertIsNone(build["voice_profile_id"])


class ProcessBuildDispatchTests(unittest.TestCase):

    def setUp(self):
        self._tmpdir, self.db = _make_db()
        _make_project(self.db, self._tmpdir)

    @patch("tool1_dashboard.service.Tool1Service._process_master_build")
    def test_dispatch_master(self, mock_master):
        build = _make_master_build(self.db, self._tmpdir)
        from tool1_dashboard.service import Tool1Service
        svc = Tool1Service(db=self.db)
        self.db.update_build(build["id"], board_status="Queued", pipeline_status="queued")
        build = self.db.get_build(build["id"])
        svc._process_build(build)
        mock_master.assert_called_once_with(build["id"], "alignment")

    @patch("tool1_dashboard.service.Tool1Service._process_localization_build")
    def test_dispatch_localization(self, mock_loc):
        build = _make_localization_build(self.db, self._tmpdir)
        from tool1_dashboard.service import Tool1Service
        svc = Tool1Service(db=self.db)
        self.db.update_build(build["id"], board_status="Queued", pipeline_status="queued")
        build = self.db.get_build(build["id"])
        svc._process_build(build)
        mock_loc.assert_called_once_with(build["id"], "translation")

    @patch("tool1_dashboard.service.Tool1Service._process_master_build")
    def test_dispatch_error_sets_failed(self, mock_master):
        mock_master.side_effect = RuntimeError("kaboom")
        build = _make_master_build(self.db, self._tmpdir)
        from tool1_dashboard.service import Tool1Service
        svc = Tool1Service(db=self.db)
        self.db.update_build(build["id"], board_status="Queued", pipeline_status="queued")
        build = self.db.get_build(build["id"])
        svc._process_build(build)
        updated = self.db.get_build(build["id"])
        self.assertEqual(updated["pipeline_status"], "failed")
        self.assertEqual(updated["board_status"], "Needs Attention")
        self.assertIn("kaboom", updated["last_error"])


class BeginBuildStageTests(unittest.TestCase):

    def setUp(self):
        self._tmpdir, self.db = _make_db()
        _make_project(self.db, self._tmpdir)

    def test_begin_build_stage_creates_run(self):
        build = _make_master_build(self.db, self._tmpdir)
        from tool1_dashboard.service import Tool1Service
        svc = Tool1Service(db=self.db)
        returned_build, stage_dir, run_id = svc._begin_build_stage(build["id"], "alignment")
        self.assertTrue(stage_dir.exists())
        self.assertGreater(run_id, 0)
        updated = self.db.get_build(build["id"])
        self.assertEqual(updated["current_stage"], "alignment")

    def test_begin_build_stage_missing_build(self):
        from tool1_dashboard.service import Tool1Service
        svc = Tool1Service(db=self.db)
        with self.assertRaises(FileNotFoundError):
            svc._begin_build_stage("nonexistent", "alignment")


class ResolvedBuildConfigTests(unittest.TestCase):

    def setUp(self):
        self._tmpdir, self.db = _make_db()
        _make_project(self.db, self._tmpdir)

    def test_config_from_project(self):
        build = _make_master_build(self.db, self._tmpdir)
        from tool1_dashboard.service import Tool1Service
        svc = Tool1Service(db=self.db)
        config = svc._resolved_build_config(build)
        self.assertIn("scene_planning_provider", config)
        self.assertIn("scene_planning_model", config)
        self.assertIn("leading_video_scene_count", config)


class SaveMasterScenesTests(unittest.TestCase):

    def setUp(self):
        self._tmpdir, self.db = _make_db()
        _make_project(self.db, self._tmpdir)

    def test_save_master_scenes(self):
        build = _make_master_build(self.db, self._tmpdir)
        from tool1_dashboard.service import Tool1Service
        svc = Tool1Service(db=self.db)
        # Write a fake timeline
        build_root = Path(self._tmpdir)
        review_dir = build_root / "review"
        review_dir.mkdir(parents=True, exist_ok=True)
        timeline = [
            {"scene_id": "s1", "start": 0, "end": 10, "text": "hello", "asset_type": "video"},
            {"scene_id": "s2", "start": 10, "end": 20, "text": "world", "asset_type": "image"},
        ]
        timeline_path = review_dir / "timeline_draft.json"
        timeline_path.write_text(json.dumps(timeline), encoding="utf-8")
        self.db.update_build(build["id"], timeline_draft_path=str(timeline_path))

        svc._save_master_scenes(build["id"])

        project = self.db.get_project("proj1")
        self.assertIsNotNone(project["master_scenes_path"])
        saved = json.loads(Path(project["master_scenes_path"]).read_text(encoding="utf-8"))
        self.assertEqual(len(saved), 2)
        self.assertEqual(saved[0]["scene_id"], "s1")


class TTSPauseResumeTests(unittest.TestCase):

    def setUp(self):
        self._tmpdir, self.db = _make_db()
        _make_project(self.db, self._tmpdir)

    def test_tts_completion_requeues_at_alignment(self):
        build = _make_localization_build(self.db, self._tmpdir)
        build_id = build["id"]
        # Simulate paused_for_tts with a TTS job
        now = time.time()
        self.db.create_tts_job({
            "job_id": "tts-j1",
            "build_id": build_id,
            "job_type": "generate",
            "profile_id": "p1",
            "status": "completed",
            "result_path": "/tmp/narration.wav",
            "payload_json": "{}",
            "meta_json": "{}",
            "queue_priority": 20,
            "created_at": now,
            "updated_at": now,
        })
        self.db.update_build(
            build_id,
            tts_job_id="tts-j1",
            pipeline_status="paused_for_tts",
        )

        from tool1_dashboard.service import Tool1Service
        svc = Tool1Service(db=self.db)
        svc._check_paused_tts_builds()

        updated = self.db.get_build(build_id)
        self.assertEqual(updated["pipeline_status"], "queued")
        self.assertEqual(updated["board_status"], "Queued")
        self.assertEqual(updated["queued_from_stage"], "alignment")
        self.assertEqual(updated["narration_path"], "/tmp/narration.wav")
        self.assertEqual(updated["audio_path"], "/tmp/narration.wav")

    def test_tts_error_marks_failed(self):
        build = _make_localization_build(self.db, self._tmpdir)
        build_id = build["id"]
        now = time.time()
        self.db.create_tts_job({
            "job_id": "tts-j2",
            "build_id": build_id,
            "job_type": "generate",
            "profile_id": "p1",
            "status": "error",
            "error_message": "GPU OOM",
            "payload_json": "{}",
            "meta_json": "{}",
            "queue_priority": 20,
            "created_at": now,
            "updated_at": now,
        })
        self.db.update_build(
            build_id,
            tts_job_id="tts-j2",
            pipeline_status="paused_for_tts",
        )

        from tool1_dashboard.service import Tool1Service
        svc = Tool1Service(db=self.db)
        svc._check_paused_tts_builds()

        updated = self.db.get_build(build_id)
        self.assertEqual(updated["pipeline_status"], "failed")
        self.assertEqual(updated["board_status"], "Needs Attention")
        self.assertIn("GPU OOM", updated["last_error"])

    def test_tts_canceled_sets_idle(self):
        build = _make_localization_build(self.db, self._tmpdir)
        build_id = build["id"]
        now = time.time()
        self.db.create_tts_job({
            "job_id": "tts-j3",
            "build_id": build_id,
            "job_type": "generate",
            "profile_id": "p1",
            "status": "canceled",
            "payload_json": "{}",
            "meta_json": "{}",
            "queue_priority": 20,
            "created_at": now,
            "updated_at": now,
        })
        self.db.update_build(
            build_id,
            tts_job_id="tts-j3",
            pipeline_status="paused_for_tts",
        )

        from tool1_dashboard.service import Tool1Service
        svc = Tool1Service(db=self.db)
        svc._check_paused_tts_builds()

        updated = self.db.get_build(build_id)
        self.assertEqual(updated["pipeline_status"], "idle")


class LocalizedTimelineTests(unittest.TestCase):

    def setUp(self):
        self._tmpdir, self.db = _make_db()
        project = _make_project(self.db, self._tmpdir)
        # Create master scenes
        master_scenes = [
            {"scene_id": "s1", "start": 0.0, "end": 10.0, "text": "intro", "asset_type": "video", "duration": 10.0},
            {"scene_id": "s2", "start": 10.0, "end": 30.0, "text": "main", "asset_type": "video", "duration": 20.0},
            {"scene_id": "s3", "start": 30.0, "end": 40.0, "text": "outro", "asset_type": "image", "duration": 10.0},
        ]
        master_dir = Path(self._tmpdir) / "master"
        master_dir.mkdir(parents=True, exist_ok=True)
        master_scenes_path = master_dir / "master_scenes.json"
        master_scenes_path.write_text(json.dumps(master_scenes), encoding="utf-8")
        self.db.update_project("proj1", master_scenes_path=str(master_scenes_path))

        # Create localization build
        build = _make_localization_build(self.db, self._tmpdir)
        self.build_id = build["id"]

        # Create a fake SRT for the localized audio
        # SRT cues: 0-5, 5-10, 10-15, 15-20 (total 20s vs master 40s)
        srt_content = (
            "1\n00:00:00,000 --> 00:00:05,000\nOla mundo\n\n"
            "2\n00:00:05,000 --> 00:00:10,000\nParte principal\n\n"
            "3\n00:00:10,000 --> 00:00:15,000\nMais texto\n\n"
            "4\n00:00:15,000 --> 00:00:20,000\nFim\n\n"
        )
        srt_path = Path(self._tmpdir) / "final.srt"
        srt_path.write_text(srt_content, encoding="utf-8")
        self.db.update_build(self.build_id, srt_path=str(srt_path))

        # Create build dirs for runs
        runs_dir = Path(self._tmpdir) / "builds" / self.build_id / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        review_dir = Path(self._tmpdir) / "builds" / self.build_id / "review"
        review_dir.mkdir(parents=True, exist_ok=True)

    def test_localized_timeline_produces_correct_count(self):
        from tool1_dashboard.service import Tool1Service
        svc = Tool1Service(db=self.db)
        svc._build_localized_timeline(self.build_id)

        build = self.db.get_build(self.build_id)
        self.assertIsNotNone(build["timeline_path"])
        timeline = json.loads(Path(build["timeline_path"]).read_text(encoding="utf-8"))
        self.assertEqual(len(timeline), 3)  # same as master

    def test_localized_timeline_preserves_scene_ids(self):
        from tool1_dashboard.service import Tool1Service
        svc = Tool1Service(db=self.db)
        svc._build_localized_timeline(self.build_id)

        build = self.db.get_build(self.build_id)
        timeline = json.loads(Path(build["timeline_path"]).read_text(encoding="utf-8"))
        scene_ids = [s["scene_id"] for s in timeline]
        self.assertEqual(scene_ids, ["s1", "s2", "s3"])

    def test_localized_timeline_proportional(self):
        from tool1_dashboard.service import Tool1Service
        svc = Tool1Service(db=self.db)
        svc._build_localized_timeline(self.build_id)

        build = self.db.get_build(self.build_id)
        timeline = json.loads(Path(build["timeline_path"]).read_text(encoding="utf-8"))

        # Master: s1 = 25%, s2 = 50%, s3 = 25% of 40s
        # Localized total = 20s, so roughly s1~5s, s2~10s, s3~5s
        # With snapping to cue starts (0, 5, 10, 15, 20), proportional maps to:
        # s1: 0->5 (25% of 20 = 5), s2: 5->15 (50% of 20 = 10), s3: 15->20 (25% of 20 = 5)
        self.assertEqual(timeline[0]["start"], 0.0)
        self.assertEqual(timeline[0]["end"], 5.0)
        self.assertEqual(timeline[1]["start"], 5.0)
        self.assertEqual(timeline[1]["end"], 15.0)
        self.assertEqual(timeline[2]["start"], 15.0)
        self.assertEqual(timeline[2]["end"], 20.0)

    def test_localized_timeline_snaps_to_cues(self):
        """Verify that scene boundaries snap to SRT cue start times."""
        from tool1_dashboard.service import Tool1Service
        svc = Tool1Service(db=self.db)
        svc._build_localized_timeline(self.build_id)

        build = self.db.get_build(self.build_id)
        timeline = json.loads(Path(build["timeline_path"]).read_text(encoding="utf-8"))

        cue_starts = {0.0, 5.0, 10.0, 15.0}
        for scene in timeline:
            self.assertIn(scene["start"], cue_starts,
                          f"start {scene['start']} not a cue boundary")


class WorkerLoopBuildPickupTests(unittest.TestCase):

    def test_worker_loop_picks_up_builds(self):
        """Verify _worker_loop processes builds when no legacy jobs exist."""
        tmpdir, db = _make_db()
        _make_project(db, tmpdir)
        build = _make_master_build(db, tmpdir)
        db.update_build(build["id"], board_status="Queued", pipeline_status="queued")

        from tool1_dashboard.service import Tool1Service
        svc = Tool1Service(db=db)

        # next_queued_job returns None
        self.assertIsNone(db.next_queued_job())

        # next_queued_build returns the build
        queued = db.next_queued_build()
        self.assertIsNotNone(queued)
        self.assertEqual(queued["id"], build["id"])


class QueueBuildValidationTests(unittest.TestCase):

    def setUp(self):
        self._tmpdir, self.db = _make_db()
        _make_project(self.db, self._tmpdir)

    def test_queue_build_rejects_invalid_master_stage(self):
        build = _make_master_build(self.db, self._tmpdir)
        from tool1_dashboard.service import Tool1Service
        svc = Tool1Service(db=self.db)
        with self.assertRaises(ValueError):
            svc.queue_build(build["id"], start_stage="translation")

    def test_queue_build_rejects_invalid_loc_stage(self):
        build = _make_localization_build(self.db, self._tmpdir)
        from tool1_dashboard.service import Tool1Service
        svc = Tool1Service(db=self.db)
        with self.assertRaises(ValueError):
            svc.queue_build(build["id"], start_stage="scene_planning")

    def test_queue_build_accepts_valid_master_stage(self):
        build = _make_master_build(self.db, self._tmpdir)
        from tool1_dashboard.service import Tool1Service
        svc = Tool1Service(db=self.db)
        result = svc.queue_build(build["id"], start_stage="alignment")
        self.assertEqual(result["build"]["pipeline_status"], "queued")

    def test_queue_build_accepts_valid_loc_stage(self):
        build = _make_localization_build(self.db, self._tmpdir)
        from tool1_dashboard.service import Tool1Service
        svc = Tool1Service(db=self.db)
        result = svc.queue_build(build["id"], start_stage="translation")
        self.assertEqual(result["build"]["pipeline_status"], "queued")


class MissingProfileTests(unittest.TestCase):

    def setUp(self):
        self._tmpdir, self.db = _make_db()
        _make_project(self.db, self._tmpdir)

    def test_translation_raises_without_profile(self):
        build = _make_localization_build(self.db, self._tmpdir)
        # Create runs dir for _begin_build_stage
        runs_dir = Path(self._tmpdir) / "builds" / build["id"] / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)

        from tool1_dashboard.service import Tool1Service
        svc = Tool1Service(db=self.db)
        with self.assertRaises(ValueError) as ctx:
            svc._run_build_translation(build["id"])
        self.assertIn("translation profile", str(ctx.exception).lower())

    def test_tts_raises_without_voice_profile(self):
        build = _make_localization_build(self.db, self._tmpdir)
        from tool1_dashboard.service import Tool1Service
        svc = Tool1Service(db=self.db)
        with self.assertRaises(ValueError) as ctx:
            svc._run_build_tts(build["id"])
        self.assertIn("voice profile", str(ctx.exception).lower())


class CreateLocalizationBuildProfileTests(unittest.TestCase):

    def setUp(self):
        self._tmpdir, self.db = _make_db()
        _make_project(self.db, self._tmpdir)
        # Make master build done
        build = _make_master_build(self.db, self._tmpdir)
        self.db.update_build(build["id"], pipeline_status="review")

    def test_stores_profile_ids(self):
        from tool1_dashboard.service import Tool1Service
        svc = Tool1Service(db=self.db)
        result = svc.create_localization_build(
            "proj1",
            target_language="pt-BR",
            translation_profile_id="tp1",
            voice_profile_id="vp1",
        )
        build = result["build"]
        self.assertEqual(build["translation_profile_id"], "tp1")
        self.assertEqual(build["voice_profile_id"], "vp1")

    def test_stores_none_profiles(self):
        from tool1_dashboard.service import Tool1Service
        svc = Tool1Service(db=self.db)
        result = svc.create_localization_build(
            "proj1",
            target_language="es",
        )
        build = result["build"]
        self.assertIsNone(build["translation_profile_id"])
        self.assertIsNone(build["voice_profile_id"])


if __name__ == "__main__":
    unittest.main()
