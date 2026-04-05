"""Integration tests for the video assembly pipeline (Tasks 5.1, 5.3, 11.1).

Tests the full flow: create episode → mock export artifacts → upload assets
→ validate → render → verify output. FFmpeg calls are mocked so these tests
run without FFmpeg installed.
"""

from __future__ import annotations

import json
import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from tool1_dashboard.database import Tool1Database
from tool1_dashboard.runtime import utc_now
from tool1_dashboard.service import Tool1Service
from tool1_dashboard.video_assembly.dashboard_observer import DashboardRenderObserver
from tool1_dashboard.video_assembly.models import (
    AssetProbe,
    RenderSummary,
    SceneRenderResult,
    ValidationReport,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_service(tmp: Path) -> Tool1Service:
    """Create a service with a fresh database in *tmp*."""
    return Tool1Service(
        db=Tool1Database(tmp / "test.db"),
        cli_runner=MagicMock(),
    )


def _create_test_project(service: Tool1Service) -> str:
    """Create a niche project and return its id."""
    result = service.create_niche_project(
        name="integration-test",
        master_language="en",
        configured_languages=["en", "es"],
    )
    return result["project"]["id"]


def _create_test_episode(service: Tool1Service, project_id: str) -> str:
    """Create a draft episode and return its id."""
    result = service.submit_episode(
        project_id,
        title="test episode",
        script_text="This is the first sentence. This is the second sentence.",
    )
    return result["episode"]["id"]


def _write_test_timeline(path: Path, scene_count: int = 3, total_duration: float = 9.0) -> None:
    """Write a flat-array timeline JSON with *scene_count* scenes."""
    dur = total_duration / scene_count
    scenes = []
    for i in range(scene_count):
        scenes.append({
            "scene_id": f"scene_{i + 1:03d}",
            "start": round(dur * i, 3),
            "end": round(dur * (i + 1), 3),
            "duration": round(dur, 3),
            "text": f"Scene {i + 1} narration text.",
            "asset_type": "image" if i > 0 else "video",
        })
    path.write_text(json.dumps(scenes, ensure_ascii=False), encoding="utf-8")


def _write_dummy_audio(path: Path) -> None:
    """Write a minimal file to act as a voiceover wav."""
    path.write_bytes(b"\x00" * 1024)


def _write_dummy_srt(path: Path) -> None:
    """Write a minimal SRT file."""
    path.write_text(
        "1\n00:00:00,000 --> 00:00:03,000\nFirst.\n\n"
        "2\n00:00:03,000 --> 00:00:06,000\nSecond.\n\n"
        "3\n00:00:06,000 --> 00:00:09,000\nThird.\n",
        encoding="utf-8",
    )


def _write_small_png(path: Path) -> None:
    """Write a 1x1 black PNG (valid, minimal)."""
    # Minimal valid PNG: 8-byte sig + IHDR + IDAT + IEND
    import struct
    import zlib

    def _chunk(chunk_type: bytes, data: bytes) -> bytes:
        raw = chunk_type + data
        return struct.pack(">I", len(data)) + raw + struct.pack(">I", zlib.crc32(raw) & 0xFFFFFFFF)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = _chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    raw_pixel = b"\x00\x00\x00\x00"  # filter-byte + RGB
    idat = _chunk(b"IDAT", zlib.compress(raw_pixel))
    iend = _chunk(b"IEND", b"")
    path.write_bytes(sig + ihdr + idat + iend)


def _write_small_mp4(path: Path) -> None:
    """Write a tiny file that pretends to be an mp4."""
    path.write_bytes(b"\x00" * 2048)


def _setup_export_artifacts(
    service: Tool1Service,
    episode_id: str,
    languages: list[str],
    scene_count: int = 3,
) -> None:
    """Simulate a completed export by writing per-language artifacts and updating the DB."""
    episode = service.db.get_episode(episode_id)
    workspace = service._episode_workspace(episode)
    workspace.mkdir(parents=True, exist_ok=True)

    # Master timeline (needed for scene list)
    master_timeline = workspace / "timeline_draft.json"
    _write_test_timeline(master_timeline, scene_count=scene_count)
    service.db.update_episode(
        episode_id,
        timeline_draft_path=str(master_timeline),
        current_stage="export",
        pipeline_status="done",
        board_status="Done",
    )

    # Per-language artifacts
    for lang in languages:
        lang_dir = workspace / "lang" / lang
        lang_dir.mkdir(parents=True, exist_ok=True)

        timeline_path = lang_dir / f"timeline_{lang}.json"
        audio_path = lang_dir / f"narration_{lang}.wav"
        srt_path = lang_dir / f"subtitles_{lang}.srt"

        # Different duration per language to verify per-language rendering
        total_dur = 9.0 if lang == "en" else 10.5
        _write_test_timeline(timeline_path, scene_count=scene_count, total_duration=total_dur)
        _write_dummy_audio(audio_path)
        _write_dummy_srt(srt_path)

        # Language status rows already created by submit_episode — just update
        service.db.update_episode_language_status(
            episode_id,
            lang,
            translation_status="done",
            tts_status="done",
            srt_status="done",
            timeline_status="done",
            tts_audio_path=str(audio_path),
            srt_path=str(srt_path),
            timeline_path=str(timeline_path),
        )


def _upload_test_assets(
    service: Tool1Service,
    episode_id: str,
    scene_count: int = 3,
) -> None:
    """Upload small test files as scene assets."""
    episode = service.db.get_episode(episode_id)
    workspace = service._episode_workspace(episode)
    shared_dir = workspace / "assembly" / "shared_assets"
    shared_dir.mkdir(parents=True, exist_ok=True)

    scenes_resp = service.list_episode_scenes(episode_id)
    for scene in scenes_resp["scenes"]:
        sid = scene["scene_id"]
        if scene["asset_type"] == "video":
            filename = f"{sid}.mp4"
            fpath = shared_dir / filename
            _write_small_mp4(fpath)
            asset_type = "video"
        else:
            filename = f"{sid}.png"
            fpath = shared_dir / filename
            _write_small_png(fpath)
            asset_type = "image"

        existing = service.db.get_scene_asset(episode_id, sid)
        payload = {
            "id": f"sa-{episode_id}-{sid}",
            "episode_id": episode_id,
            "scene_id": sid,
            "asset_type": asset_type,
            "original_filename": filename,
            "stored_filename": filename,
            "file_path": str(fpath),
            "file_size": fpath.stat().st_size,
        }
        if existing:
            service.db.update_scene_asset(episode_id, sid, file_path=str(fpath))
        else:
            service.db.create_scene_asset(payload)


# ---------------------------------------------------------------------------
# Tests — DashboardRenderObserver
# ---------------------------------------------------------------------------

class TestDashboardRenderObserver(unittest.TestCase):
    """Unit tests for the DashboardRenderObserver (Task 5.1)."""

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.tmp = Path(self._tmp)
        self.db = Tool1Database(self.tmp / "obs_test.db")
        self.db.initialize()
        self.job_id = "rj-test-001"
        self.db.create_render_job({
            "id": self.job_id,
            "episode_id": "ep-test",
            "language_code": "en",
            "state": "idle",
            "stage": "idle",
            "total_scenes": 0,
            "completed_scenes": 0,
            "project_dir": str(self.tmp),
        })

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_set_state_updates_job_and_writes_log(self) -> None:
        obs = DashboardRenderObserver(self.db, self.job_id)
        obs.set_state("rendering", "Rendering scenes", scene_id="scene_001")

        job = self.db.get_render_job(self.job_id)
        self.assertEqual(job["state"], "rendering")
        self.assertEqual(job["stage"], "rendering")
        self.assertEqual(job["current_scene_id"], "scene_001")

        logs = self.db.list_render_logs(self.job_id)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["message"], "Rendering scenes")

    def test_log_writes_log_entry_only(self) -> None:
        obs = DashboardRenderObserver(self.db, self.job_id)
        obs.log("WARNING", "probing", "Asset too short", scene_id="scene_002")

        # Job state should not change
        job = self.db.get_render_job(self.job_id)
        self.assertEqual(job["state"], "idle")

        logs = self.db.list_render_logs(self.job_id)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["level"], "WARNING")
        self.assertEqual(logs[0]["scene_id"], "scene_002")

    def test_set_validation_stores_json_and_scene_count(self) -> None:
        obs = DashboardRenderObserver(self.db, self.job_id)
        report = ValidationReport(passed=True, scene_count=5, total_duration=30.0)
        obs.set_validation(report)

        job = self.db.get_render_job(self.job_id)
        self.assertEqual(job["total_scenes"], 5)
        validation = json.loads(job["validation_json"])
        self.assertTrue(validation["passed"])
        self.assertEqual(validation["total_duration"], 30.0)

    def test_add_scene_result_increments_counter(self) -> None:
        obs = DashboardRenderObserver(self.db, self.job_id)
        for i in range(3):
            result = SceneRenderResult(
                scene_id=f"scene_{i + 1:03d}",
                output_file=self.tmp / f"scene_{i + 1:03d}.mp4",
                source_duration=5.0,
                target_duration=3.0,
                actual_duration=3.0,
                adjustment_summary="direct",
            )
            obs.add_scene_result(result)

        job = self.db.get_render_job(self.job_id)
        self.assertEqual(job["completed_scenes"], 3)

    def test_complete_sets_final_state_and_outputs(self) -> None:
        obs = DashboardRenderObserver(self.db, self.job_id)
        summary = RenderSummary(
            job_id=self.job_id,
            project_dir=self.tmp,
            final_video=self.tmp / "final.mp4",
            visual_master=self.tmp / "master.mp4",
            scene_results=[],
            asset_probes={},
            total_duration=9.0,
            total_scenes=3,
            started_at=utc_now(),
            finished_at=utc_now(),
            manifest_path=self.tmp / "manifest.json",
        )
        obs.complete(summary)

        job = self.db.get_render_job(self.job_id)
        self.assertEqual(job["state"], "completed")
        self.assertIsNotNone(job["finished_at"])
        outputs = json.loads(job["outputs_json"])
        self.assertIn("final_video", outputs)
        self.assertIn("manifest", outputs)

    def test_fail_sets_error_state(self) -> None:
        obs = DashboardRenderObserver(self.db, self.job_id)
        obs.fail("FFmpeg crashed")

        job = self.db.get_render_job(self.job_id)
        self.assertEqual(job["state"], "failed")
        self.assertEqual(job["error_message"], "FFmpeg crashed")
        self.assertIsNotNone(job["finished_at"])


# ---------------------------------------------------------------------------
# Tests — Full Assembly Flow
# ---------------------------------------------------------------------------

class TestVideoAssemblyIntegration(unittest.TestCase):
    """End-to-end integration test for the video assembly pipeline (Task 11.1).

    Mocks FFmpeg so the test runs without it installed.  Verifies the full
    database state transitions, observer writes, and service method contracts.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp()
        self.tmp = Path(self._tmp)

        # Patch EPISODES_ROOT so the service creates workspaces in our temp dir
        self._episodes_patch = patch(
            "tool1_dashboard.service.EPISODES_ROOT",
            self.tmp / "episodes",
        )
        self._episodes_patch.start()
        (self.tmp / "episodes").mkdir()

        self.service = _make_service(self.tmp)
        self.project_id = _create_test_project(self.service)
        self.episode_id = _create_test_episode(self.service, self.project_id)

        _setup_export_artifacts(self.service, self.episode_id, ["en", "es"], scene_count=3)

    def tearDown(self) -> None:
        self._episodes_patch.stop()
        shutil.rmtree(self._tmp, ignore_errors=True)

    # -- Assembly start / stage transitions --------------------------------

    def test_start_assembly_moves_to_asset_upload(self) -> None:
        result = self.service.start_assembly(self.episode_id)
        self.assertTrue(result.get("started") or result.get("ok") or "asset_upload" in str(result))

        episode = self.service.db.get_episode(self.episode_id)
        self.assertEqual(episode["current_stage"], "asset_upload")

    def test_scene_list_returns_correct_count(self) -> None:
        scenes = self.service.list_episode_scenes(self.episode_id)
        self.assertEqual(scenes["total_scenes"], 3)
        self.assertEqual(scenes["uploaded_count"], 0)

    # -- Asset upload -----------------------------------------------------

    def test_upload_assets_and_count(self) -> None:
        _upload_test_assets(self.service, self.episode_id, scene_count=3)

        scenes = self.service.list_episode_scenes(self.episode_id)
        self.assertEqual(scenes["uploaded_count"], 3)

        count = self.service.db.count_scene_assets(self.episode_id)
        self.assertEqual(count, 3)

    # -- Validation -------------------------------------------------------

    def test_validate_all_languages_pass(self) -> None:
        _upload_test_assets(self.service, self.episode_id)

        with patch.object(type(self.service), "_ffmpeg_tools_available", staticmethod(lambda: True)):
            result = self.service.validate_assembly(self.episode_id)

        self.assertIn("languages", result)
        self.assertIn("shared", result)
        self.assertTrue(result["shared"]["all_assets_uploaded"])
        for lang in ("en", "es"):
            self.assertTrue(result["languages"][lang]["passed"], f"{lang} should pass validation")

    def test_validate_single_language(self) -> None:
        _upload_test_assets(self.service, self.episode_id)

        with patch.object(type(self.service), "_ffmpeg_tools_available", staticmethod(lambda: True)):
            result = self.service.validate_assembly(self.episode_id, language_code="en")

        self.assertIn("en", result["languages"])
        self.assertNotIn("es", result["languages"])
        self.assertTrue(result["languages"]["en"]["passed"])

    def test_validate_fails_without_assets(self) -> None:
        with patch.object(type(self.service), "_ffmpeg_tools_available", staticmethod(lambda: True)):
            result = self.service.validate_assembly(self.episode_id)

        self.assertFalse(result["shared"]["all_assets_uploaded"])
        self.assertEqual(len(result["shared"]["missing_scenes"]), 3)
        # Languages may still show errors for shared assets
        for lang_result in result["languages"].values():
            self.assertFalse(lang_result["passed"])

    # -- Render (mocked FFmpeg) -------------------------------------------

    @patch("tool1_dashboard.video_assembly.pipeline.load_timeline")
    @patch("tool1_dashboard.video_assembly.pipeline.validate_or_raise")
    @patch("tool1_dashboard.video_assembly.pipeline.probe_assets")
    @patch("tool1_dashboard.video_assembly.pipeline.render_image_scene")
    @patch("tool1_dashboard.video_assembly.pipeline.render_video_scene")
    @patch("tool1_dashboard.video_assembly.pipeline.concatenate_scenes")
    @patch("tool1_dashboard.video_assembly.pipeline.mux_voiceover")
    @patch("tool1_dashboard.video_assembly.pipeline.burn_subtitles")
    def test_full_render_single_language(
        self,
        mock_burn,
        mock_mux,
        mock_concat,
        mock_render_video,
        mock_render_image,
        mock_probe,
        mock_validate,
        mock_load_timeline,
    ) -> None:
        from tool1_dashboard.video_assembly.models import ProjectConfig, SceneSpec, MotionSpec, RetimeSpec

        _upload_test_assets(self.service, self.episode_id)
        self.service.start_assembly(self.episode_id)

        # --- Configure mocks ---
        episode = self.service.db.get_episode(self.episode_id)
        workspace = self.service._episode_workspace(episode)

        def _mock_load_timeline(project_dir):
            config = ProjectConfig(
                project_dir=project_dir,
                fps=30, width=1920, height=1080,
                voiceover_file="voiceover.wav",
                subtitle_file="subtitles.srt",
            )
            scenes = [
                SceneSpec(
                    scene_id=f"scene_{i + 1:03d}",
                    asset_id=f"asset_{i + 1:03d}",
                    asset_type="video" if i == 0 else "image",
                    asset_file=f"assets/{i + 1:03d}_scene_{i + 1:03d}.png",
                    start=round(3.0 * i, 3),
                    end=round(3.0 * (i + 1), 3),
                    duration=3.0,
                    text=f"Scene {i + 1}",
                )
                for i in range(3)
            ]
            return config, scenes

        mock_load_timeline.side_effect = _mock_load_timeline
        mock_validate.return_value = ValidationReport(
            passed=True, scene_count=3, total_duration=9.0,
        )
        mock_probe.return_value = {
            f"asset_{i + 1:03d}": AssetProbe(
                asset_id=f"asset_{i + 1:03d}",
                type="image",
                path=workspace / f"fake_{i}.png",
                width=1920, height=1080,
            )
            for i in range(3)
        }

        def _make_scene_result(config, scene, output_file, probe=None):
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_bytes(b"\x00" * 512)
            return SceneRenderResult(
                scene_id=scene.scene_id,
                output_file=output_file,
                source_duration=None,
                target_duration=scene.duration,
                actual_duration=scene.duration,
                adjustment_summary="mock_direct",
            )

        mock_render_image.side_effect = lambda config, scene, out: _make_scene_result(config, scene, out)
        mock_render_video.side_effect = lambda config, scene, probe, out: _make_scene_result(config, scene, out, probe)

        def _mock_concat(config, scene_files):
            concat_path = config.temp_dir / "concat_list.txt"
            master_path = config.temp_dir / "visual_master.mp4"
            concat_path.parent.mkdir(parents=True, exist_ok=True)
            concat_path.write_text("concat list", encoding="utf-8")
            master_path.write_bytes(b"\x00" * 1024)
            return concat_path, master_path

        mock_concat.side_effect = _mock_concat

        def _mock_mux(config, visual_master, job_id):
            final = config.output_dir / f"final_video_{job_id}.mp4"
            final.parent.mkdir(parents=True, exist_ok=True)
            final.write_bytes(b"\x00" * 2048)
            return final

        mock_mux.side_effect = _mock_mux

        def _mock_burn(config, final_video, job_id):
            subtitled = config.output_dir / f"final_video_subtitled_{job_id}.mp4"
            subtitled.write_bytes(b"\x00" * 2048)
            return subtitled

        mock_burn.side_effect = _mock_burn

        # --- Start render ---
        with (
            patch.object(type(self.service), "_ffmpeg_tools_available", staticmethod(lambda: True)),
            patch.object(self.service, "_assert_tts_not_processing"),
        ):
            result = self.service.start_render(self.episode_id, "en")

        self.assertEqual(result["status"], "started")
        self.assertIn("render_job_id", result)
        render_job_id = result["render_job_id"]

        # Wait for the render thread to complete (max 10s)
        for _ in range(100):
            job = self.service.db.get_render_job(render_job_id)
            if job and job["state"] in ("completed", "failed"):
                break
            time.sleep(0.1)

        job = self.service.db.get_render_job(render_job_id)
        self.assertEqual(job["state"], "completed", f"Render failed: {job.get('error_message')}")
        self.assertEqual(job["completed_scenes"], 3)
        self.assertIsNotNone(job["finished_at"])

        # Verify outputs
        outputs = json.loads(job["outputs_json"])
        self.assertIn("final_video", outputs)
        self.assertTrue(Path(outputs["final_video"]).exists())

        # Verify logs were written
        logs = self.service.db.list_render_logs(render_job_id)
        self.assertGreater(len(logs), 0)
        stages_seen = {log["stage"] for log in logs}
        self.assertIn("completed", stages_seen)

    @patch("tool1_dashboard.video_assembly.pipeline.load_timeline")
    @patch("tool1_dashboard.video_assembly.pipeline.validate_or_raise")
    @patch("tool1_dashboard.video_assembly.pipeline.probe_assets")
    @patch("tool1_dashboard.video_assembly.pipeline.render_image_scene")
    @patch("tool1_dashboard.video_assembly.pipeline.render_video_scene")
    @patch("tool1_dashboard.video_assembly.pipeline.concatenate_scenes")
    @patch("tool1_dashboard.video_assembly.pipeline.mux_voiceover")
    @patch("tool1_dashboard.video_assembly.pipeline.burn_subtitles")
    def test_render_all_languages_sequential(
        self,
        mock_burn,
        mock_mux,
        mock_concat,
        mock_render_video,
        mock_render_image,
        mock_probe,
        mock_validate,
        mock_load_timeline,
    ) -> None:
        """Render all languages and verify they complete sequentially."""
        from tool1_dashboard.video_assembly.models import ProjectConfig, SceneSpec

        _upload_test_assets(self.service, self.episode_id)
        self.service.start_assembly(self.episode_id)

        def _mock_load_timeline(project_dir):
            config = ProjectConfig(
                project_dir=project_dir,
                fps=30, width=1920, height=1080,
                voiceover_file="voiceover.wav",
            )
            scenes = [
                SceneSpec(
                    scene_id="scene_001",
                    asset_id="asset_001",
                    asset_type="image",
                    asset_file="assets/001_scene_001.png",
                    start=0.0, end=3.0, duration=3.0,
                )
            ]
            return config, scenes

        mock_load_timeline.side_effect = _mock_load_timeline
        mock_validate.return_value = ValidationReport(passed=True, scene_count=1, total_duration=3.0)
        mock_probe.return_value = {
            "asset_001": AssetProbe(asset_id="asset_001", type="image", path=Path("/fake.png"))
        }

        def _make_result(config, scene, out):
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"\x00" * 256)
            return SceneRenderResult(
                scene_id=scene.scene_id, output_file=out,
                source_duration=None, target_duration=3.0,
                actual_duration=3.0, adjustment_summary="mock",
            )

        mock_render_image.side_effect = _make_result
        mock_render_video.side_effect = lambda c, s, p, o: _make_result(c, s, o)

        def _mock_concat(config, files):
            p = config.temp_dir / "visual_master.mp4"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(b"\x00" * 512)
            return config.temp_dir / "concat.txt", p

        mock_concat.side_effect = _mock_concat

        def _mock_mux(config, master, job_id):
            f = config.output_dir / f"final_{job_id}.mp4"
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes(b"\x00" * 512)
            return f

        mock_mux.side_effect = _mock_mux
        mock_burn.return_value = None  # no subtitles for simplicity

        # Start render for ALL languages
        with (
            patch.object(type(self.service), "_ffmpeg_tools_available", staticmethod(lambda: True)),
            patch.object(self.service, "_assert_tts_not_processing"),
        ):
            result = self.service.start_render(self.episode_id, "all")

        self.assertEqual(result["status"], "started")
        self.assertEqual(len(result["render_job_ids"]), 2)

        # Wait for all to complete
        for _ in range(100):
            all_done = True
            for jid in result["render_job_ids"]:
                job = self.service.db.get_render_job(jid)
                if not job or job["state"] not in ("completed", "failed"):
                    all_done = False
                    break
            if all_done:
                break
            time.sleep(0.1)

        # Both should be completed
        for jid in result["render_job_ids"]:
            job = self.service.db.get_render_job(jid)
            self.assertEqual(
                job["state"], "completed",
                f"Job {jid} ({job['language_code']}) failed: {job.get('error_message')}",
            )

        # Verify render status grouping
        status = self.service.get_render_status(self.episode_id)
        self.assertIn("en", status["languages"])
        self.assertIn("es", status["languages"])

    def test_render_rejects_concurrent(self) -> None:
        """Second render should be rejected while first is running."""
        _upload_test_assets(self.service, self.episode_id)
        self.service.start_assembly(self.episode_id)

        # Acquire the lock manually to simulate a running render
        self.service._render_lock.acquire()
        try:
            with (
                patch.object(type(self.service), "_ffmpeg_tools_available", staticmethod(lambda: True)),
                patch.object(self.service, "_assert_tts_not_processing"),
            ):
                with self.assertRaises(ValueError) as ctx:
                    self.service.start_render(self.episode_id, "en")
                self.assertIn("already running", str(ctx.exception).lower())
        finally:
            self.service._render_lock.release()

    def test_validate_fails_without_tts(self) -> None:
        """Validation should fail for a language with missing TTS."""
        _upload_test_assets(self.service, self.episode_id)

        # Break TTS for es
        self.service.db.update_episode_language_status(
            self.episode_id, "es",
            tts_status="pending",
            tts_audio_path="",
        )

        with patch.object(type(self.service), "_ffmpeg_tools_available", staticmethod(lambda: True)):
            result = self.service.validate_assembly(self.episode_id)

        self.assertTrue(result["languages"]["en"]["passed"])
        self.assertFalse(result["languages"]["es"]["passed"])
        es_errors = result["languages"]["es"]["errors"]
        self.assertTrue(any("TTS" in e for e in es_errors))


if __name__ == "__main__":
    unittest.main()
