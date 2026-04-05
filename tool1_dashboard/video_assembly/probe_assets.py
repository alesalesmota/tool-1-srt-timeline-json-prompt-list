from __future__ import annotations

from pathlib import Path

from .exceptions import AutoVideoError
from .ffmpeg_utils import ffprobe_json
from .models import AssetProbe, ProjectConfig, SceneSpec
from .utils import write_json
from .validation import SUPPORTED_IMAGE_EXTENSIONS, SUPPORTED_VIDEO_EXTENSIONS


def _probe_asset(path: Path, expected_type: str, asset_id: str) -> AssetProbe:
    data = ffprobe_json(path)
    streams = data.get("streams", [])
    video_stream = next((stream for stream in streams if stream.get("codec_type") == "video"), None)
    if video_stream is None:
        raise AutoVideoError(f"Asset has no video stream or readable image data: {path}")

    extension = path.suffix.lower()
    detected_type = "image" if extension in SUPPORTED_IMAGE_EXTENSIONS else "video"
    if detected_type == "video" and extension not in SUPPORTED_VIDEO_EXTENSIONS:
        raise AutoVideoError(f"Unsupported asset extension for probing: {path.suffix}")

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

        probe = _probe_asset(asset_path, scene.asset_type, scene.asset_id)
        probes[scene.asset_id] = probe
        probed_by_path[asset_path] = probe

    write_json(config.temp_dir / "probed_assets.json", probes)
    return probes
