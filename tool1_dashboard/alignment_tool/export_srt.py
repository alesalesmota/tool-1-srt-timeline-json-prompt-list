from __future__ import annotations

from pathlib import Path

from .models import SubtitleSegment


def format_timestamp(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    whole_seconds = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis >= 1000:
        whole_seconds += 1
        millis -= 1000
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d},{millis:03d}"


def segments_to_srt(segments: list[SubtitleSegment]) -> str:
    lines: list[str] = []
    for segment in segments:
        lines.append(str(segment.segment_id))
        lines.append(f"{format_timestamp(segment.start)} --> {format_timestamp(segment.end)}")
        lines.append(segment.text)
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def write_srt(path: Path, segments: list[SubtitleSegment]) -> str:
    srt_text = segments_to_srt(segments)
    path.write_text(srt_text, encoding="utf-8")
    return srt_text

