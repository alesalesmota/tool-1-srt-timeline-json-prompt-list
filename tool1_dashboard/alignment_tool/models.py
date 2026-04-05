from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from pathlib import Path
from typing import Any, Optional


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


@dataclass
class ScriptWord:
    index: int
    word: str
    normalized: str
    text_start: int
    text_end: int
    render_start: int
    render_end: int
    leading_text: str = ""
    trailing_text: str = ""


@dataclass
class ScriptDocument:
    source_text: str
    canonical_text: str
    alignment_text: str
    words: list[ScriptWord]
    paragraphs: list[str] = field(default_factory=list)
    paragraph_word_ranges: list[tuple[int, int]] = field(default_factory=list)
    language_code: str = "en"


@dataclass
class WordTiming:
    word: str
    start: float
    end: float
    index: int
    confidence: Optional[float] = None
    source: str = ""
    approximate: bool = False
    normalized: str = ""
    text_start: int = 0
    text_end: int = 0
    render_start: int = 0
    render_end: int = 0
    leading_text: str = ""
    trailing_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


@dataclass
class SubtitleSegment:
    segment_id: int
    start: float
    end: float
    text: str
    line_count: int
    char_count: int
    word_count: int
    reading_cps: float

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


@dataclass
class SegmentationDiagnostics:
    segment_profile: dict[str, int] = field(default_factory=dict)
    segments_over_18_cps: int = 0
    segments_over_24_cps: int = 0
    segments_over_30_cps: int = 0
    short_fast_segment_count: int = 0
    long_text_fast_segment_count: int = 0
    average_chars_per_segment: float = 0.0
    average_words_per_segment: float = 0.0
    optimization_passes: int = 1

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


@dataclass
class SegmentationResult:
    segments: list[SubtitleSegment]
    warnings: list[str]
    diagnostics: SegmentationDiagnostics

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


@dataclass
class SegmentationConfig:
    min_duration: float = 0.9
    preferred_duration: float = 3.0
    max_duration: float = 6.0
    max_chars_per_line: int = 42
    max_lines_per_block: int = 2
    max_chars_per_block: int = 84
    max_reading_cps: float = 18.0

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


@dataclass
class EngineConfig:
    primary_engine: str = "mfa"
    fallback_engine: Optional[str] = "whisperx"
    whisperx_model: str = "small"

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


@dataclass
class NormalizedAudioInfo:
    path: Path
    duration_seconds: float
    sample_rate: int
    channels: int

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


@dataclass
class AlignmentReport:
    engine: str
    fallback_used: bool
    fallback_reason: Optional[str]
    audio_duration: float
    normalized_audio_properties: dict[str, Any]
    script_word_count: int
    aligned_word_count: int
    approximate_word_count: int
    dropped_word_count: int
    mismatch_count: int
    segment_count: int
    average_segment_duration: float
    warnings: list[str]
    status: str
    strategy: str = "single_pass"
    warning_summary: dict[str, Any] = field(default_factory=dict)
    reading_speed_warning_count: int = 0
    max_reading_cps: float = 0.0
    p95_reading_cps: float = 0.0
    candidate_metrics: list[dict[str, Any]] = field(default_factory=list)
    chunk_count: int = 0
    segment_profile: dict[str, int] = field(default_factory=dict)
    segments_over_18_cps: int = 0
    segments_over_24_cps: int = 0
    segments_over_30_cps: int = 0
    short_fast_segment_count: int = 0
    long_text_fast_segment_count: int = 0
    average_chars_per_segment: float = 0.0
    average_words_per_segment: float = 0.0
    optimization_passes: int = 1

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


@dataclass
class AlignmentArtifacts:
    output_dir: Path
    final_srt: Path
    words_json: Path
    segments_json: Path
    alignment_report: Path
    normalized_audio: Path
    run_log: Path

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)


@dataclass
class AlignmentResult:
    run_id: str
    output_dir: Path
    script_document: ScriptDocument
    normalized_audio: NormalizedAudioInfo
    engine_used: str
    fallback_used: bool
    fallback_reason: Optional[str]
    words: list[WordTiming]
    segments: list[SubtitleSegment]
    report: AlignmentReport
    artifacts: AlignmentArtifacts
    logs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return _serialize(self)
