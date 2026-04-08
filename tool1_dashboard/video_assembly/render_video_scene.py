from __future__ import annotations

from pathlib import Path

from .ffmpeg_utils import probe_duration, run_command
from .models import AssetProbe, ProjectConfig, SceneRenderResult, SceneSpec

TIME_TOLERANCE = 0.05
MAX_SLOW_FACTOR = 1.35
MAX_SPEED_FACTOR = 1.15


def _plan_adjustment(source_duration: float, target_duration: float) -> tuple[float | None, float, str]:
    delta = target_duration - source_duration
    if abs(delta) <= TIME_TOLERANCE:
        return None, 0.0, "direct"

    if source_duration < target_duration:
        stretch_factor = target_duration / source_duration
        if stretch_factor <= MAX_SLOW_FACTOR:
            return stretch_factor, 0.0, f"slow_down_{stretch_factor:.2f}x"
        freeze_duration = max(0.0, target_duration - (source_duration * MAX_SLOW_FACTOR))
        return MAX_SLOW_FACTOR, freeze_duration, f"slow_down_{MAX_SLOW_FACTOR:.2f}x_plus_freeze"

    speed_factor = source_duration / target_duration
    if speed_factor <= MAX_SPEED_FACTOR:
        setpts_factor = target_duration / source_duration
        return setpts_factor, 0.0, f"speed_up_{speed_factor:.2f}x"

    return None, 0.0, "trim"


def render_video_scene(
    config: ProjectConfig,
    scene: SceneSpec,
    probe: AssetProbe,
    output_file: Path,
) -> SceneRenderResult:
    source_duration = probe.duration
    if source_duration is None:
        raise ValueError(f"Video source duration missing for {scene.scene_id}")

    setpts_factor, freeze_duration, adjustment = _plan_adjustment(source_duration, scene.duration)

    filters: list[str] = [
        f"scale={config.width}:{config.height}:force_original_aspect_ratio=increase",
        f"crop={config.width}:{config.height}",
    ]
    if setpts_factor is not None:
        filters.append(f"setpts={setpts_factor:.6f}*PTS")
    filters.append(f"fps={config.fps}")
    if freeze_duration > TIME_TOLERANCE:
        filters.append(f"tpad=stop_mode=clone:stop_duration={freeze_duration:.3f}")
    filters.append(f"trim=duration={scene.duration:.3f}")
    filters.append("setpts=PTS-STARTPTS")

    run_command(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(scene.asset_path(config)),
            "-vf",
            ",".join(filters),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            str(output_file),
        ],
        f"render video scene {scene.scene_id}",
    )

    return SceneRenderResult(
        scene_id=scene.scene_id,
        output_file=output_file,
        source_duration=source_duration,
        target_duration=scene.duration,
        actual_duration=probe_duration(output_file),
        adjustment_summary=adjustment,
    )
