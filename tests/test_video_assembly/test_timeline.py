from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from tool1_dashboard.video_assembly.exceptions import TimelineValidationError
from tool1_dashboard.video_assembly.timeline import load_timeline


def test_load_timeline_converts_tool1_flat_array_and_fills_defaults(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    input_dir = project_dir / "input"
    assets_dir = input_dir / "assets"
    assets_dir.mkdir(parents=True)

    (input_dir / "narration_en.wav").write_bytes(b"RIFF....WAVEfmt ")
    (input_dir / "captions_en.srt").write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
    (assets_dir / "001_prompt1.png").write_bytes(b"png-data")
    blank_frame = assets_dir / "_blank_black.png"
    blank_frame.write_bytes(b"blank")

    timeline_payload = [
        {
            "scene_id": "scene_001",
            "start": 0.0,
            "end": 1.0,
            "duration": 1.0,
            "text": "Opening scene.",
            "asset_type": "image",
        },
        {
            "scene_id": "scene_002",
            "start": 1.0,
            "end": 2.5,
            "duration": 1.5,
            "text": "Missing asset scene.",
            "asset_type": "image",
        },
    ]
    (input_dir / "timeline.json").write_text(json.dumps(timeline_payload), encoding="utf-8")

    with patch("tool1_dashboard.video_assembly.timeline.generate_blank_frame", return_value=blank_frame):
        config, scenes = load_timeline(project_dir)

    assert config.fps == 30
    assert config.width == 1920
    assert config.height == 1080
    assert config.voiceover_file == "narration_en.wav"
    assert config.subtitle_file == "captions_en.srt"
    assert [scene.asset_id for scene in scenes] == ["asset_001", "asset_002"]
    assert [scene.asset_file for scene in scenes] == [
        "assets/001_prompt1.png",
        "assets/_blank_black.png",
    ]
    assert scenes[0].motion.mode == "static"
    assert scenes[0].retime.mode == "auto"


def test_load_timeline_raises_for_invalid_json(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    input_dir = project_dir / "input"
    input_dir.mkdir(parents=True)
    (input_dir / "timeline.json").write_text("{not-json", encoding="utf-8")

    with pytest.raises(TimelineValidationError) as exc_info:
        load_timeline(project_dir)

    assert "Timeline file is not valid JSON" in str(exc_info.value)
