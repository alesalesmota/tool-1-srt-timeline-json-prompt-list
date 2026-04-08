from __future__ import annotations

from pathlib import Path

from .exceptions import AutoVideoError
from .ffmpeg_utils import ffprobe_json
from .models import AssetProbe, ProjectConfig, SceneSpec
from .utils import read_json, write_json
from .validation import SUPPORTED_IMAGE_EXTENSIONS, SUPPORTED_VIDEO_EXTENSIONS


def _detect_asset_type_from_path(path: Path) -> str:
    extension = path.suffix.lower()
    if extension in SUPPORTED_IMAGE_EXTENSIONS:
        return "image"
    if extension in SUPPORTED_VIDEO_EXTENSIONS:
        return "video"
    raise AutoVideoError(f"Unsupported asset extension for probing: {path.suffix}")


def _parse_optional_int(value: object) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed or None


def _parse_optional_float(value: object) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _asset_probe_from_cache(
    path: Path,
    expected_type: str,
    asset_id: str,
    cached_entry: object,
) -> AssetProbe | None:
    if not isinstance(cached_entry, dict):
        return None

    detected_type = _detect_asset_type_from_path(path)
    if expected_type != detected_type:
        raise AutoVideoError(
            f"Asset type mismatch for {asset_id}: timeline says `{expected_type}` but file looks like `{detected_type}`."
        )

    cached_type = str(cached_entry.get("type") or "").strip().lower()
    if cached_type and cached_type != expected_type:
        return None

    width = _parse_optional_int(cached_entry.get("width"))
    height = _parse_optional_int(cached_entry.get("height"))
    duration = _parse_optional_float(cached_entry.get("duration"))
    if width is None or height is None:
        return None
    if detected_type == "video" and duration is None:
        return None

    codec = str(cached_entry.get("codec")).strip() if cached_entry.get("codec") else None
    container = str(cached_entry.get("container")).strip() if cached_entry.get("container") else None
    return AssetProbe(
        asset_id=asset_id,
        type=detected_type,
        path=path,
        width=width,
        height=height,
        duration=duration,
        codec=codec or None,
        container=container or None,
    )


def _load_cached_probes(config: ProjectConfig) -> dict[str, object]:
    cached_probes_path = config.input_dir / "cached_probes.json"
    if not cached_probes_path.exists():
        return {}
    try:
        payload = read_json(cached_probes_path)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _probe_asset(path: Path, expected_type: str, asset_id: str) -> AssetProbe:
    data = ffprobe_json(path)
    streams = data.get("streams", [])
    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if video_stream is None:
        raise AutoVideoError(f"Asset has no video stream or readable image data: {path}")

    detected_type = _detect_asset_type_from_path(path)
    if expected_type != detected_type:
        raise AutoVideoError(
            f"Asset type mismatch for {asset_id}: timeline says `{expected_type}` but file looks like `{detected_type}`."
        )

    duration = None
    if detected_type == "video":
        raw_duration = data.get("format", {}).get("duration") or video_stream.get("duration")
        if raw_duration is None:
            raise AutoVideoError(f"Could not determine duration for video asset: {path}")
        duration = float(raw_duration)

    return AssetProbe(
        asset_id=asset_id,
        type=detected_type,
        path=path,
        width=int(video_stream.get("width", 0) or 0) or None,
        height=int(video_stream.get("height", 0) or 0) or None,
        duration=duration,
        codec=video_stream.get("codec_name"),
        container=data.get("format", {}).get("format_name"),
    )


def probe_assets(config: ProjectConfig, scenes: list[SceneSpec]) -> dict[str, AssetProbe]:
    probes: dict[str, AssetProbe] = {}
    probed_by_path: dict[Path, AssetProbe] = {}
    cached_probes = _load_cached_probes(config)

    for scene in scenes:
        asset_path = scene.asset_path(config)
        if asset_path in probed_by_path:
            existing = probed_by_path[asset_path]
            probes[scene.asset_id] = AssetProbe(
                asset_id=scene.asset_id,
                type=existing.type,
                path=existing.path,
                width=existing.width,
                height=existing.height,
                duration=existing.duration,
                codec=existing.codec,
                container=existing.container,
            )
            continue

        probe = _asset_probe_from_cache(
            asset_path,
            scene.asset_type,
            scene.asset_id,
            cached_probes.get(scene.asset_id),
        )
        if probe is None:
            probe = _probe_asset(asset_path, scene.asset_type, scene.asset_id)
        probes[scene.asset_id] = probe
        probed_by_path[asset_path] = probe

    write_json(config.temp_dir / "probed_assets.json", probes)
    return probes
