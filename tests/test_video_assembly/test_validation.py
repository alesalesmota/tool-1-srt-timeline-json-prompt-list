from __future__ import annotations

from pathlib import Path

import pytest

from tool1_dashboard.video_assembly.exceptions import TimelineValidationError
from tool1_dashboard.video_assembly.models import ProjectConfig, SceneSpec
from tool1_dashboard.video_assembly.validation import validate_or_raise, validate_project


def test_validate_project_reports_timing_and_asset_errors(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    input_dir = project_dir / "input"
    assets_dir = input_dir / "assets"
    assets_dir.mkdir(parents=True)

    (input_dir / "voiceover.wav").write_bytes(b"RIFF....WAVEfmt ")
    (assets_dir / "asset_001.png").write_bytes(b"png-data")
    (assets_dir / "image-two.gif").write_bytes(b"gif-data")

    config = ProjectConfig(
        project_dir=project_dir,
        fps=30,
        width=1920,
        height=1080,
        voiceover_file="voiceover.wav",
        subtitle_file="captions.srt",
    )
    scenes = [
        SceneSpec(
            scene_id="scene_001",
            asset_id="asset_001",
            asset_type="image",
            asset_file="assets/asset_001.png",
            start=0.0,
            end=1.0,
            duration=1.0,
        ),
        SceneSpec(
            scene_id="scene_002",
            asset_id="asset_002",
            asset_type="image",
            asset_file="assets/image-two.gif",
            start=0.8,
            end=1.4,
            duration=0.4,
        ),
    ]

    report = validate_project(config, scenes)

    assert report.passed is False
    assert any("Subtitle file declared but not found" in message for message in report.errors)
    assert any("scene overlaps the previous scene" in message for message in report.errors)
    assert any("duration" in message for message in report.errors)
    assert any("not supported for images" in message for message in report.errors)
    assert any("does not include the asset_id `asset_002`" in message for message in report.warnings)


def test_validate_project_allows_warning_only_gaps(tmp_path: Path) -> None:
    project_dir = tmp_path / "project"
    input_dir = project_dir / "input"
    assets_dir = input_dir / "assets"
    assets_dir.mkdir(parents=True)

    (input_dir / "voiceover.wav").write_bytes(b"RIFF....WAVEfmt ")
    (assets_dir / "asset_001.png").write_bytes(b"png-data")
    (assets_dir / "asset_002.png").write_bytes(b"png-data")

    config = ProjectConfig(
        project_dir=project_dir,
        fps=30,
        width=1920,
        height=1080,
        voiceover_file="voiceover.wav",
    )
    scenes = [
        SceneSpec(
            scene_id="scene_001",
            asset_id="asset_001",
            asset_type="image",
            asset_file="assets/asset_001.png",
            start=0.0,
            end=1.0,
            duration=1.0,
        ),
        SceneSpec(
            scene_id="scene_002",
            asset_id="asset_002",
            asset_type="image",
            asset_file="assets/asset_002.png",
            start=1.2,
            end=2.0,
            duration=0.8,
        ),
    ]

    report = validate_project(config, scenes)

    assert report.passed is True
    assert report.errors == []
    assert any("small timing gap" in message for message in report.warnings)


def test_validate_or_raise_raises_on_invalid_project(tmp_path: Path) -> None:
    config = ProjectConfig(
        project_dir=tmp_path / "missing-project",
        fps=30,
        width=1920,
        height=1080,
        voiceover_file="voiceover.wav",
    )
    scenes = [
        SceneSpec(
            scene_id="scene_001",
            asset_id="asset_001",
            asset_type="image",
            asset_file="assets/asset_001.png",
            start=0.0,
            end=1.0,
            duration=1.0,
        )
    ]

    with pytest.raises(TimelineValidationError) as exc_info:
        validate_or_raise(config, scenes)

    assert "Input directory not found" in str(exc_info.value)
