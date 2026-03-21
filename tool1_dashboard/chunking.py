from __future__ import annotations

from dataclasses import dataclass

from .srt_chunker.models import SubtitleCue
from .srt_chunker.srt_io import chunk_to_text, cues_to_srt


@dataclass
class PlanningChunk:
    chunk_id: int
    start_seconds: float
    end_seconds: float
    cue_start_index: int
    cue_end_index: int
    cues: list[SubtitleCue]

    @property
    def duration_seconds(self) -> float:
        return round(self.end_seconds - self.start_seconds, 3)

    def to_dict(self) -> dict[str, object]:
        return {
            "chunk_id": self.chunk_id,
            "start_seconds": self.start_seconds,
            "end_seconds": self.end_seconds,
            "duration_seconds": self.duration_seconds,
            "cue_start_index": self.cue_start_index,
            "cue_end_index": self.cue_end_index,
            "cue_count": len(self.cues),
        }

    def as_srt(self) -> str:
        return cues_to_srt(self.cues, restart_numbering=False)

    def as_text(self) -> str:
        return chunk_to_text(self.cues)


def build_planning_chunks(
    cues: list[SubtitleCue],
    *,
    chunk_seconds: int,
    overlap_seconds: int,
) -> tuple[list[PlanningChunk], dict[str, object]]:
    if not cues:
        raise ValueError("Cannot plan scenes without subtitle cues.")

    total_start = cues[0].start_ms / 1000.0
    total_end = cues[-1].end_ms / 1000.0
    total_duration = total_end - total_start

    if total_duration <= chunk_seconds:
        chunk = PlanningChunk(
            chunk_id=1,
            start_seconds=round(total_start, 3),
            end_seconds=round(total_end, 3),
            cue_start_index=cues[0].index,
            cue_end_index=cues[-1].index,
            cues=list(cues),
        )
        return [chunk], {
            "mode": "full_srt",
            "chunk_seconds": chunk_seconds,
            "overlap_seconds": overlap_seconds,
            "total_duration": round(total_duration, 3),
        }

    step = max(1, chunk_seconds - overlap_seconds)
    chunks: list[PlanningChunk] = []
    cursor = total_start
    chunk_id = 1
    while cursor < total_end:
        window_end = min(total_end, cursor + chunk_seconds)
        selected = [
            cue
            for cue in cues
            if (cue.start_ms / 1000.0) < window_end and (cue.end_ms / 1000.0) > cursor
        ]
        if selected:
            chunks.append(
                PlanningChunk(
                    chunk_id=chunk_id,
                    start_seconds=round(cursor, 3),
                    end_seconds=round(window_end, 3),
                    cue_start_index=selected[0].index,
                    cue_end_index=selected[-1].index,
                    cues=selected,
                )
            )
            chunk_id += 1
        if window_end >= total_end:
            break
        cursor += step

    return chunks, {
        "mode": "overlap_chunks",
        "chunk_seconds": chunk_seconds,
        "overlap_seconds": overlap_seconds,
        "step_seconds": step,
        "total_duration": round(total_duration, 3),
    }


def build_prompt_batches(
    scenes: list[dict[str, object]],
    *,
    batch_size: int,
) -> list[dict[str, object]]:
    if batch_size <= 0:
        batch_size = len(scenes) or 1
    batches: list[dict[str, object]] = []
    for start in range(0, len(scenes), batch_size):
        batch_scenes = scenes[start : start + batch_size]
        batches.append(
            {
                "batch_id": len(batches) + 1,
                "scene_start": start + 1,
                "scene_end": start + len(batch_scenes),
                "scenes": batch_scenes,
            }
        )
    return batches
