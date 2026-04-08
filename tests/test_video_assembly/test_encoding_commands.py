from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tool1_dashboard.video_assembly.burn_subtitles import burn_subtitles
from tool1_dashboard.video_assembly.concat_scenes import concatenate_scenes
from tool1_dashboard.video_assembly.models import AssetProbe, MotionSpec, ProjectConfig, SceneSpec
from tool1_dashboard.video_assembly.render_image_scene import render_image_scene
from tool1_dashboard.video_assembly.render_video_scene import render_video_scene


def _make_config(project_dir: Path, *, subtitle_file: str | None = None) -> ProjectConfig:
    return ProjectConfig(
        project_dir=project_dir,
        fps=30,
        width=1920,
        height=1080,
        voiceover_file="voiceover.wav",
        subtitle_file=subtitle_file,
    )


class TestEncodingCommands(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.project_dir = Path(self._tmp.name)
        (self.project_dir / "input" / "assets").mkdir(parents=True, exist_ok=True)
        (self.project_dir / "temp").mkdir(parents=True, exist_ok=True)
        (self.project_dir / "output").mkdir(parents=True, exist_ok=True)
        (self.project_dir / "input" / "voiceover.wav").write_bytes(b"voice")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_concatenate_scenes_uses_stream_copy(self) -> None:
        config = _make_config(self.project_dir)
        scene_a = self.project_dir / "temp" / "scenes" / "scene_a.mp4"
        scene_b = self.project_dir / "temp" / "scenes" / "scene_b.mp4"
        scene_a.parent.mkdir(parents=True, exist_ok=True)
        scene_a.write_bytes(b"a")
        scene_b.write_bytes(b"b")

        with patch("tool1_dashboard.video_assembly.concat_scenes.run_command") as mock_run:
            concat_list_path, visual_master_path = concatenate_scenes(config, [scene_a, scene_b])

        command = mock_run.call_args.args[0]
        self.assertIn("-c:v", command)
        codec_index = command.index("-c:v")
        self.assertEqual(command[codec_index + 1], "copy")
        self.assertNotIn("libx264", command)
        self.assertNotIn("-pix_fmt", command)
        self.assertEqual(concat_list_path, config.temp_dir / "concat_list.txt")
        self.assertEqual(visual_master_path, config.temp_dir / "visual_master.mp4")
        self.assertIn(scene_a.resolve().as_posix(), concat_list_path.read_text(encoding="utf-8"))
        self.assertIn(scene_b.resolve().as_posix(), concat_list_path.read_text(encoding="utf-8"))

    def test_render_video_scene_uses_fast_preset_and_crf_20(self) -> None:
        config = _make_config(self.project_dir)
        asset_path = self.project_dir / "input" / "assets" / "scene_001.mp4"
        asset_path.write_bytes(b"video")
        output_file = self.project_dir / "temp" / "scenes" / "scene_001.mp4"
        scene = SceneSpec(
            scene_id="scene_001",
            asset_id="asset_001",
            asset_type="video",
            asset_file="assets/scene_001.mp4",
            start=0.0,
            end=3.0,
            duration=3.0,
        )
        probe = AssetProbe(asset_id="asset_001", type="video", path=asset_path, duration=3.0)

        with (
            patch("tool1_dashboard.video_assembly.render_video_scene.run_command") as mock_run,
            patch("tool1_dashboard.video_assembly.render_video_scene.probe_duration", return_value=3.0),
        ):
            render_video_scene(config, scene, probe, output_file)

        command = mock_run.call_args.args[0]
        codec_index = command.index("-c:v")
        self.assertEqual(command[codec_index + 1], "libx264")
        self.assertEqual(command[codec_index + 2:codec_index + 6], ["-preset", "fast", "-crf", "20"])
        self.assertIn("-pix_fmt", command)

    def test_render_image_scene_uses_fast_preset_and_crf_20(self) -> None:
        config = _make_config(self.project_dir)
        asset_path = self.project_dir / "input" / "assets" / "scene_002.png"
        asset_path.write_bytes(b"image")
        output_file = self.project_dir / "temp" / "scenes" / "scene_002.mp4"
        scene = SceneSpec(
            scene_id="scene_002",
            asset_id="asset_002",
            asset_type="image",
            asset_file="assets/scene_002.png",
            start=0.0,
            end=4.0,
            duration=4.0,
            motion=MotionSpec(enabled=True, mode="slow_zoom_in"),
        )

        with (
            patch("tool1_dashboard.video_assembly.render_image_scene.run_command") as mock_run,
            patch("tool1_dashboard.video_assembly.render_image_scene.probe_duration", return_value=4.0),
        ):
            render_image_scene(config, scene, output_file)

        command = mock_run.call_args.args[0]
        codec_index = command.index("-c:v")
        self.assertEqual(command[codec_index + 1], "libx264")
        self.assertEqual(command[codec_index + 2:codec_index + 6], ["-preset", "fast", "-crf", "20"])
        self.assertIn("-pix_fmt", command)

    def test_burn_subtitles_uses_fast_final_encode(self) -> None:
        subtitle_path = self.project_dir / "input" / "subtitles.srt"
        subtitle_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nHi\n", encoding="utf-8")
        video_path = self.project_dir / "output" / "final.mp4"
        video_path.write_bytes(b"video")
        config = _make_config(self.project_dir, subtitle_file="subtitles.srt")

        with patch("tool1_dashboard.video_assembly.burn_subtitles.run_command") as mock_run:
            output_path = burn_subtitles(config, video_path, "job-123")

        command = mock_run.call_args.args[0]
        preset_index = command.index("-preset")
        self.assertEqual(command[preset_index + 1], "fast")
        crf_index = command.index("-crf")
        self.assertEqual(command[crf_index + 1], "18")
        self.assertEqual(output_path, config.output_dir / "final_video_subtitled_job-123.mp4")


if __name__ == "__main__":
    unittest.main()
