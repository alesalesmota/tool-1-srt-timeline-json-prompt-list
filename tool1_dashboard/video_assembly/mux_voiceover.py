from __future__ import annotations

from pathlib import Path

from .ffmpeg_utils import run_command
from .models import ProjectConfig


def mux_voiceover(config: ProjectConfig, visual_master_path: Path, job_id: str) -> Path:
    final_video_path = config.output_dir / f"final_video_{job_id}.mp4"
    run_command(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(visual_master_path),
            "-i",
            str(config.voiceover_path),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            str(final_video_path),
        ],
        "mux voiceover",
    )
    return final_video_path
