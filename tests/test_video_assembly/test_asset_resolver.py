from __future__ import annotations

from pathlib import Path

from tool1_dashboard.video_assembly.asset_resolver import resolve_assets, scan_assets


def test_resolve_assets_prefers_matching_type_for_same_number(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir(parents=True)

    (assets_dir / "001_prompt1.mp4").write_bytes(b"video")
    (assets_dir / "001_prompt1.png").write_bytes(b"image")
    (assets_dir / "002_scene2.mp4").write_bytes(b"video")

    scenes = [
        {"scene_id": "scene_001", "asset_type": "image"},
        {"scene_id": "scene_002", "asset_type": "video"},
    ]

    result = resolve_assets(scenes, assets_dir)

    assert result["scene_001"]["asset_file"] == "assets/001_prompt1.png"
    assert result["scene_001"]["asset_type"] == "image"
    assert result["scene_002"]["asset_file"] == "assets/002_scene2.mp4"
    assert result["scene_002"]["asset_type"] == "video"


def test_resolve_assets_assigns_remaining_files_in_sorted_order(tmp_path: Path) -> None:
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir(parents=True)

    (assets_dir / "notes.txt").write_text("ignore", encoding="utf-8")
    (assets_dir / "orphan_b.webp").write_bytes(b"image")
    (assets_dir / "orphan_a.png").write_bytes(b"image")

    scanned = scan_assets(assets_dir)
    assert [path.name for path in scanned] == ["orphan_a.png", "orphan_b.webp"]

    scenes = [
        {"scene_id": "intro", "asset_type": "image"},
        {"scene_id": "outro", "asset_type": "image"},
    ]

    result = resolve_assets(scenes, assets_dir)

    assert result["intro"]["asset_file"] == "assets/orphan_a.png"
    assert result["outro"]["asset_file"] == "assets/orphan_b.webp"
