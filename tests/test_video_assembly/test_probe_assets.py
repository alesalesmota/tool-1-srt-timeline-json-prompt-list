from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from tool1_dashboard.video_assembly.models import ProjectConfig, SceneSpec
from tool1_dashboard.video_assembly.probe_assets import probe_assets


def _make_config(project_dir: Path) -> ProjectConfig:
    input_dir = project_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    return ProjectConfig(
        project_dir=project_dir,
        fps=30,
        width=1920,
        height=1080,
        voiceover_file="voiceover.wav",
    )


def test_probe_assets_uses_complete_cached_probes_without_ffprobe(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    config = _make_config(project_dir)
    assets_dir = config.input_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    video_path = assets_dir / "001_scene_001.mp4"
    image_path = assets_dir / "002_scene_002.png"
    video_path.write_bytes(b"video")
    image_path.write_bytes(b"image")
    (config.input_dir / "cached_probes.json").write_text(
        json.dumps(
            {
                "asset_001": {"type": "video", "width": 1920, "height": 1080, "duration": 3.5},
                "asset_002": {"type": "image", "width": 1280, "height": 720, "duration": None},
            }
        ),
        encoding="utf-8",
    )

    scenes = [
        SceneSpec(
            scene_id="scene_001",
            asset_id="asset_001",
            asset_type="video",
            asset_file="assets/001_scene_001.mp4",
            start=0.0,
            end=3.5,
            duration=3.5,
        ),
        SceneSpec(
            scene_id="scene_002",
            asset_id="asset_002",
            asset_type="image",
            asset_file="assets/002_scene_002.png",
            start=3.5,
            end=5.0,
            duration=1.5,
        ),
    ]

    with patch("tool1_dashboard.video_assembly.probe_assets.ffprobe_json", side_effect=AssertionError("ffprobe should not run")):
        probes = probe_assets(config, scenes)

    assert probes["asset_001"].type == "video"
    assert probes["asset_001"].duration == 3.5
    assert probes["asset_001"].path == video_path
    assert probes["asset_002"].type == "image"
    assert probes["asset_002"].duration is None
    assert probes["asset_002"].path == image_path


def test_probe_assets_falls_back_to_ffprobe_for_incomplete_cached_entry(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    config = _make_config(project_dir)
    assets_dir = config.input_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    video_path = assets_dir / "001_scene_001.mp4"
    video_path.write_bytes(b"video")
    (config.input_dir / "cached_probes.json").write_text(
        json.dumps(
            {
                "asset_001": {"type": "video", "width": 1920, "height": 1080}
            }
        ),
        encoding="utf-8",
    )

    scenes = [
        SceneSpec(
            scene_id="scene_001",
            asset_id="asset_001",
            asset_type="video",
            asset_file="assets/001_scene_001.mp4",
            start=0.0,
            end=3.0,
            duration=3.0,
        )
    ]

    with patch(
        "tool1_dashboard.video_assembly.probe_assets.ffprobe_json",
        return_value={
            "streams": [
                {
                    "codec_type": "video",
                    "width": 1920,
                    "height": 1080,
                    "codec_name": "h264",
                    "duration": "3.0",
                }
            ],
            "format": {"duration": "3.0", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
        },
    ) as mock_ffprobe:
        probes = probe_assets(config, scenes)

    assert mock_ffprobe.call_count == 1
    assert probes["asset_001"].duration == 3.0
    assert probes["asset_001"].codec == "h264"
