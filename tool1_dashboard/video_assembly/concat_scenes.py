from __future__ import annotations

from pathlib import Path

from .ffmpeg_utils import run_command
from .models import ProjectConfig


def concatenate_scenes(
    config: ProjectConfig,
    scene_files: list[Path],
) -> tuple[Path, Path]:
    concat_list_path = config.temp_dir / "concat_list.txt"
    concat_lines = [f"file '{scene_file.resolve().as_posix()}'" for scene_file in scene_files]
    concat_list_path.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")

    visual_master_path = config.temp_dir / "visual_master.mp4"
    run_command(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list_path),
            "-an",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(visual_master_path),
        ],
        "concatenate scenes",
    )
    return concat_list_path, visual_master_path
