from __future__ import annotations

import re
from pathlib import Path

from .models import SubtitleCue

TIMING_RE = re.compile(
    r"^(?P<start>\d{2}:\d{2}:\d{2},\d{3})\s+-->\s+(?P<end>\d{2}:\d{2}:\d{2},\d{3})$"
)


def parse_timestamp_ms(value: str) -> int:
    hours, minutes, remainder = value.split(":")
    seconds, milliseconds = remainder.split(",")
    return (
        int(hours) * 3_600_000
        + int(minutes) * 60_000
        + int(seconds) * 1_000
        + int(milliseconds)
    )


def format_timestamp_ms(value: int) -> str:
    value = max(0, int(value))
    hours, remainder = divmod(value, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def parse_srt_text(raw_text: str) -> list[SubtitleCue]:
    normalized = raw_text.replace("\r\n", "\n").replace("\r", "\n").lstrip("\ufeff").strip()
    if not normalized:
        raise ValueError("The SRT file is empty.")

    blocks = [block for block in re.split(r"\n\s*\n", normalized) if block.strip()]
    cues: list[SubtitleCue] = []

    for block_number, block in enumerate(blocks, start=1):
        lines = [line.rstrip() for line in block.split("\n")]
        while lines and not lines[0].strip():
            lines.pop(0)
        while lines and not lines[-1].strip():
            lines.pop()
        if len(lines) < 2:
            raise ValueError(f"SRT block {block_number} is incomplete.")

        if lines[0].strip().isdigit():
            cue_index = int(lines[0].strip())
            timing_line = lines[1].strip()
            text_lines = lines[2:]
        else:
            cue_index = block_number
            timing_line = lines[0].strip()
            text_lines = lines[1:]

        match = TIMING_RE.match(timing_line)
        if not match:
            raise ValueError(f"SRT block {block_number} has an invalid timing line.")

        start_ms = parse_timestamp_ms(match.group("start"))
        end_ms = parse_timestamp_ms(match.group("end"))
        if end_ms < start_ms:
            raise ValueError(f"SRT block {block_number} ends before it starts.")

        text = "\n".join(line.strip() for line in text_lines).strip()
        if not text:
            raise ValueError(f"SRT block {block_number} has no text.")

        cues.append(
            SubtitleCue(
                index=cue_index,
                start_ms=start_ms,
                end_ms=end_ms,
                text=text,
            )
        )

    return cues


def read_srt_file(path: Path) -> list[SubtitleCue]:
    return parse_srt_text(path.read_text(encoding="utf-8", errors="replace"))


def cues_to_srt(cues: list[SubtitleCue], restart_numbering: bool = True) -> str:
    lines: list[str] = []
    for offset, cue in enumerate(cues, start=1):
        rendered_index = offset if restart_numbering else cue.index
        lines.append(str(rendered_index))
        lines.append(
            f"{format_timestamp_ms(cue.start_ms)} --> {format_timestamp_ms(cue.end_ms)}"
        )
        lines.extend(cue.text.splitlines())
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def chunk_to_text(cues: list[SubtitleCue]) -> str:
    lines: list[str] = []
    for cue in cues:
        lines.append(f"[{cue.index}] {format_timestamp_ms(cue.start_ms)} --> {format_timestamp_ms(cue.end_ms)}")
        lines.extend(cue.text.splitlines())
        lines.append("")
    return "\n".join(lines).strip() + "\n"
