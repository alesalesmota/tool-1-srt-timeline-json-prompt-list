from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any


def _serialize(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _serialize(item) for key, item in asdict(value).items()}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, list):
        return [_serialize(item) for item in value]
    if isinstance(value, dict):
        return {key: _serialize(item) for key, item in value.items()}
    return value


@dataclass(frozen=True)
class SubtitleCue:
    index: int
    start_ms: int
    end_ms: int
    text: str

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)

    @property
    def char_count(self) -> int:
        return len(self.text.replace("\n", " ").strip())

    @property
    def word_count(self) -> int:
        return len([word for word in self.text.replace("\n", " ").split() if word])

    @property
    def line_count(self) -> int:
        return len([line for line in self.text.splitlines() if line.strip()]) or 1

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


@dataclass(frozen=True)
class ChunkConfig:
    max_words: int = 800
    max_chars: int = 5000
    max_entries: int = 120
    max_duration_seconds: int = 300
    restart_numbering: bool = True

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


@dataclass
class SubtitleChunk:
    chunk_id: int
    cues: list[SubtitleCue]
    word_count: int
    char_count: int
    duration_seconds: float
    start_ms: int
    end_ms: int
    original_index_start: int
    original_index_end: int
    warning: str | None = None

    @property
    def cue_count(self) -> int:
        return len(self.cues)

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


@dataclass
class ChunkRunResult:
    run_id: str
    output_dir: Path
    original_filename: str
    config: ChunkConfig
    total_cues: int
    total_words: int
    total_chars: int
    total_duration_seconds: float
    chunks: list[SubtitleChunk]
    warnings: list[str]
    manifest_path: Path
    zip_path: Path
    first_chunk_preview: str

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)
