from __future__ import annotations

import re
from pathlib import Path

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
ALL_ASSET_EXTENSIONS = IMAGE_EXTENSIONS | VIDEO_EXTENSIONS

_PROMPT_NUMBER_RE = re.compile(r"prompt\s*(\d+)", re.IGNORECASE)
_SCENE_NUMBER_RE = re.compile(r"scene[_\s]*(\d+)", re.IGNORECASE)
_ASSET_NUMBER_RE = re.compile(r"asset[_\s]*(\d+)", re.IGNORECASE)
_LEADING_NUMBER_RE = re.compile(r"^(\d+)")


def _extract_number(filename: str) -> int | None:
    """Extract a scene/prompt number from a filename using multiple patterns."""
    for pattern in (_PROMPT_NUMBER_RE, _SCENE_NUMBER_RE, _ASSET_NUMBER_RE):
        match = pattern.search(filename)
        if match:
            return int(match.group(1))
    match = _LEADING_NUMBER_RE.search(filename)
    if match:
        return int(match.group(1))
    return None


def _detect_asset_type(path: Path) -> str | None:
    ext = path.suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in VIDEO_EXTENSIONS:
        return "video"
    return None


def scan_assets(assets_dir: Path) -> list[Path]:
    """Return all recognised media files inside *assets_dir*, sorted."""
    if not assets_dir.exists():
        return []
    return sorted(
        (p for p in assets_dir.iterdir() if p.is_file() and p.suffix.lower() in ALL_ASSET_EXTENSIONS),
        key=lambda p: (
            _extract_number(p.stem) if _extract_number(p.stem) is not None else 9999,
            p.name,
        ),
    )


def resolve_assets(
    scenes: list[dict],
    assets_dir: Path,
) -> dict[str, dict]:
    """Match asset files to scenes.

    Returns ``{scene_id: {"asset_file": relative_path, "asset_type": type}}``
    for every scene.  When no matching file is found the entry is omitted —
    the caller should handle missing entries (e.g. generate a blank frame).
    """
    available = scan_assets(assets_dir)
    if not available:
        return {}

    # Build a lookup: number -> list[Path] (multiple assets might share a number)
    number_to_files: dict[int, list[Path]] = {}
    for asset_path in available:
        num = _extract_number(asset_path.stem)
        if num is not None:
            number_to_files.setdefault(num, []).append(asset_path)

    result: dict[str, dict] = {}
    used: set[Path] = set()

    # --- Pass 1: match by number (promptN, sceneN, assetN, or bare number) ---
    for index, scene in enumerate(scenes):
        scene_id = scene.get("scene_id", f"scene_{index + 1:03d}")
        expected_type = scene.get("asset_type")

        # Try matching scene number (1-based index)
        scene_number = index + 1
        scene_id_num = _extract_number(scene_id)

        candidates: list[Path] = []
        for num in {scene_number, scene_id_num} - {None}:
            candidates.extend(number_to_files.get(num, []))

        # Pick best candidate that matches expected type and is not yet used
        chosen = None
        for c in candidates:
            if c in used:
                continue
            detected = _detect_asset_type(c)
            if expected_type and detected and detected != expected_type:
                continue
            chosen = c
            break
        # If no type-matched candidate, pick any unused candidate
        if chosen is None:
            for c in candidates:
                if c not in used:
                    chosen = c
                    break

        if chosen is not None:
            used.add(chosen)
            detected = _detect_asset_type(chosen)
            result[scene_id] = {
                "asset_file": f"assets/{chosen.name}",
                "asset_type": detected or expected_type or "image",
            }

    # --- Pass 2: for any unmatched scenes, assign remaining files in order ---
    remaining = [p for p in available if p not in used]
    remaining_iter = iter(remaining)
    for index, scene in enumerate(scenes):
        scene_id = scene.get("scene_id", f"scene_{index + 1:03d}")
        if scene_id in result:
            continue
        asset_path = next(remaining_iter, None)
        if asset_path is None:
            break
        detected = _detect_asset_type(asset_path)
        result[scene_id] = {
            "asset_file": f"assets/{asset_path.name}",
            "asset_type": detected or scene.get("asset_type", "image"),
        }

    return result


def generate_blank_frame(assets_dir: Path, width: int = 1920, height: int = 1080) -> Path:
    """Create a 1-pixel black PNG stretched to *width*x*height* if it doesn't exist."""
    blank_path = assets_dir / "_blank_black.png"
    if blank_path.exists():
        return blank_path

    from .ffmpeg_utils import run_command

    assets_dir.mkdir(parents=True, exist_ok=True)
    run_command(
        [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", f"color=c=black:s={width}x{height}:d=1",
            "-frames:v", "1",
            str(blank_path),
        ],
        "generate blank black frame",
    )
    return blank_path
