from __future__ import annotations

import json
from pathlib import Path

from .asset_resolver import generate_blank_frame, resolve_assets
from .exceptions import TimelineValidationError
from .models import MotionSpec, ProjectConfig, RetimeSpec, SceneSpec

_VOICEOVER_EXTENSIONS = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac"}
_SUBTITLE_EXTENSIONS = {".srt", ".ass", ".ssa", ".vtt"}

_DEFAULT_FPS = 30
_DEFAULT_WIDTH = 1920
_DEFAULT_HEIGHT = 1080


def _require_fields(payload: dict, required: list[str], scope: str, errors: list[str]) -> None:
    for field_name in required:
        if field_name not in payload:
            errors.append(f"{scope} is missing required field `{field_name}`.")


def _find_file_by_extensions(directory: Path, extensions: set[str]) -> str | None:
    """Return the first file in *directory* whose extension is in *extensions*."""
    if not directory.exists():
        return None
    for path in sorted(directory.iterdir()):
        if path.is_file() and path.suffix.lower() in extensions:
            return path.name
    return None


def _convert_tool1_timeline(scenes_list: list, project_dir: Path) -> dict:
    """Convert Tool 1's flat-array timeline into the nested format Tool 2 expects.

    Tool 1 exports ``[{scene_id, start, end, duration, text, asset_type, ...}]``.
    Tool 2 needs ``{"project": {...}, "scenes": [...]}``.
    """
    input_dir = project_dir / "input"
    assets_dir = input_dir / "assets"

    # --- Build project config ---
    # Check for optional project_config.json override
    config_path = input_dir / "project_config.json"
    if config_path.exists():
        try:
            overrides = json.loads(config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            overrides = {}
    else:
        overrides = {}

    voiceover_file = overrides.get("voiceover_file") or _find_file_by_extensions(
        input_dir, _VOICEOVER_EXTENSIONS
    )
    subtitle_file = overrides.get("subtitle_file") or _find_file_by_extensions(
        input_dir, _SUBTITLE_EXTENSIONS
    )

    project = {
        "fps": overrides.get("fps", _DEFAULT_FPS),
        "width": overrides.get("width", _DEFAULT_WIDTH),
        "height": overrides.get("height", _DEFAULT_HEIGHT),
        "voiceover_file": voiceover_file or "voiceover.wav",
    }
    if subtitle_file:
        project["subtitle_file"] = subtitle_file

    # --- Resolve assets ---
    asset_map = resolve_assets(scenes_list, assets_dir)

    # --- Enrich each scene ---
    enriched_scenes: list[dict] = []
    for index, scene in enumerate(scenes_list):
        scene_id = scene.get("scene_id", f"scene_{index + 1:03d}")
        asset_info = asset_map.get(scene_id)

        if asset_info is not None:
            asset_file = asset_info["asset_file"]
            asset_type = asset_info["asset_type"]
        else:
            # Generate a blank black frame for missing assets
            blank = generate_blank_frame(
                assets_dir,
                project["width"],
                project["height"],
            )
            asset_file = f"assets/{blank.name}"
            asset_type = "image"

        enriched: dict = {
            **scene,
            "asset_id": scene.get("asset_id", f"asset_{index + 1:03d}"),
            "asset_file": scene.get("asset_file", asset_file),
            "asset_type": asset_type if "asset_type" not in scene else scene["asset_type"],
            "motion": scene.get("motion", {"enabled": False, "mode": "static"}),
            "retime": scene.get("retime", {"enabled": False, "mode": "auto"}),
        }
        enriched_scenes.append(enriched)

    return {"project": project, "scenes": enriched_scenes}


def load_timeline(project_dir: Path) -> tuple[ProjectConfig, list[SceneSpec]]:
    timeline_path = project_dir / "input" / "timeline.json"
    if not timeline_path.exists():
        raise TimelineValidationError([f"Timeline file not found: {timeline_path}"])

    try:
        raw = json.loads(timeline_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TimelineValidationError(
            [f"Timeline file is not valid JSON: {exc.msg} at line {exc.lineno}"]
        ) from exc

    # --- Auto-detect Tool 1 flat-array format ---
    if isinstance(raw, list):
        raw = _convert_tool1_timeline(raw, project_dir)

    errors: list[str] = []
    project_payload = raw.get("project")
    scenes_payload = raw.get("scenes")
    if not isinstance(project_payload, dict):
        errors.append("`project` must be an object.")
        project_payload = {}
    if not isinstance(scenes_payload, list):
        errors.append("`scenes` must be an array.")
        scenes_payload = []

    _require_fields(
        project_payload,
        ["fps", "width", "height", "voiceover_file"],
        "project",
        errors,
    )

    config: ProjectConfig | None = None
    if not errors:
        config = ProjectConfig(
            project_dir=project_dir,
            fps=int(project_payload["fps"]),
            width=int(project_payload["width"]),
            height=int(project_payload["height"]),
            voiceover_file=str(project_payload["voiceover_file"]),
            subtitle_file=(
                str(project_payload["subtitle_file"])
                if project_payload.get("subtitle_file")
                else None
            ),
            timeline_path=timeline_path,
        )

    scenes: list[SceneSpec] = []
    required_scene_fields = [
        "scene_id",
        "asset_id",
        "asset_type",
        "asset_file",
        "start",
        "end",
        "duration",
    ]
    for index, scene_payload in enumerate(scenes_payload, start=1):
        scope = f"scene[{index}]"
        if not isinstance(scene_payload, dict):
            errors.append(f"{scope} must be an object.")
            continue

        _require_fields(scene_payload, required_scene_fields, scope, errors)
        if any(field_name not in scene_payload for field_name in required_scene_fields):
            continue

        motion_payload = scene_payload.get("motion") or {}
        retime_payload = scene_payload.get("retime") or {}

        try:
            scene = SceneSpec(
                scene_id=str(scene_payload["scene_id"]),
                asset_id=str(scene_payload["asset_id"]),
                asset_type=str(scene_payload["asset_type"]).lower(),
                asset_file=str(scene_payload["asset_file"]),
                start=float(scene_payload["start"]),
                end=float(scene_payload["end"]),
                duration=float(scene_payload["duration"]),
                text=str(scene_payload.get("text", "")),
                motion=MotionSpec(
                    enabled=bool(motion_payload.get("enabled", False)),
                    mode=str(motion_payload.get("mode", "static")),
                ),
                retime=RetimeSpec(
                    enabled=bool(retime_payload.get("enabled", False)),
                    mode=str(retime_payload.get("mode", "auto")),
                ),
            )
        except (TypeError, ValueError) as exc:
            errors.append(f"{scope} contains invalid numeric data: {exc}")
            continue

        scenes.append(scene)

    if errors or config is None:
        raise TimelineValidationError(errors)

    return config, scenes
