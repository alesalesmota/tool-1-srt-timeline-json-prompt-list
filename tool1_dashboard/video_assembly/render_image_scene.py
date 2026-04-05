from __future__ import annotations

import math
from pathlib import Path

from .ffmpeg_utils import probe_duration, run_command
from .models import ProjectConfig, SceneRenderResult, SceneSpec


def _build_filter(scene: SceneSpec, config: ProjectConfig) -> tuple[str, str]:
    if scene.motion.enabled and scene.motion.mode == "slow_zoom_in":
        frames = max(2, math.ceil(scene.duration * config.fps))
        overscan_width = max(config.width + 2, int(config.width * 1.18))
        overscan_height = max(config.height + 2, int(config.height * 1.18))
        filtergraph = (
            f"scale={overscan_width}:{overscan_height}:force_original_aspect_ratio=increase,"
            f"crop={overscan_width}:{overscan_height},"
            f"zoompan="
            f"z='min(1+0.10*on/{frames},1.10)':"
            f"x='iw/2-(iw/zoom/2)':"
            f"y='ih/2-(ih/zoom/2)':"
            f"d=1:s={config.width}x{config.height}:fps={config.fps}"
        )
        return filtergraph, "slow_zoom_in"

    filtergraph = (
        f"scale={config.width}:{config.height}:force_original_aspect_ratio=increase,"
        f"crop={config.width}:{config.height}"
    )
    return filtergraph, "static"


def render_image_scene(
    config: ProjectConfig,
    scene: SceneSpec,
    output_file: Path,
) -> SceneRenderResult:
    filtergraph, adjustment = _build_filter(scene, config)

    run_command(
        [
            "ffmpeg",
            "-y",
            "-loop",
            "1",
            "-framerate",
            str(config.fps),
            "-i",
            str(scene.asset_path(config)),
            "-t",
            f"{scene.duration:.3f}",
            "-vf",
            filtergraph,
            "-an",
            "-r",
            str(config.fps),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(output_file),
        ],
        f"render image scene {scene.scene_id}",
    )

    return SceneRenderResult(
        scene_id=scene.scene_id,
        output_file=output_file,
        source_duration=None,
        target_duration=scene.duration,
        actual_duration=probe_duration(output_file),
        adjustment_summary=adjustment,
    )
