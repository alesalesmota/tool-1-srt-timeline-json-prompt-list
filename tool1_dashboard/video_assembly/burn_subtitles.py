from __future__ import annotations

from pathlib import Path

from .ffmpeg_utils import run_command
from .models import ProjectConfig


def burn_subtitles(
    config: ProjectConfig,
    video_path: Path,
    job_id: str,
) -> Path:
    """Burn an SRT/ASS subtitle file into the video using FFmpeg's subtitles filter."""
    subtitle_path = config.subtitle_path
    if subtitle_path is None:
        raise ValueError("No subtitle file configured.")

    output_path = config.output_dir / f"final_video_subtitled_{job_id}.mp4"

    # On Windows the subtitles filter needs forward slashes and escaped colons
    safe_sub_path = str(subtitle_path).replace("\\", "/").replace(":", "\\:")

    run_command(
        [
            "ffmpeg", "-y",
            "-i", str(video_path),
            "-vf", f"subtitles='{safe_sub_path}':force_style='FontSize=24,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,Outline=2'",
            "-c:a", "copy",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", "18",
            str(output_path),
        ],
        "burn subtitles",
    )
    return output_path
