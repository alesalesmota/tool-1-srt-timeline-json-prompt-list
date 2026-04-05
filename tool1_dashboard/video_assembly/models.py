from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .utils import resolve_input_path, utc_now

AssetType = Literal["image", "video"]
JobState = Literal[
    "idle",
    "validating",
    "probing",
    "rendering",
    "concatenating",
    "muxing",
    "subtitling",
    "completed",
    "failed",
]


@dataclass(slots=True)
class MotionSpec:
    enabled: bool = False
    mode: str = "static"


@dataclass(slots=True)
class RetimeSpec:
    enabled: bool = False
    mode: str = "auto"


@dataclass(slots=True)
class ProjectConfig:
    project_dir: Path
    fps: int
    width: int
    height: int
    voiceover_file: str
    subtitle_file: str | None = None
    timeline_path: Path | None = None

    @property
    def input_dir(self) -> Path:
        return self.project_dir / "input"

    @property
    def temp_dir(self) -> Path:
        return self.project_dir / "temp"

    @property
    def output_dir(self) -> Path:
        return self.project_dir / "output"

    @property
    def scenes_dir(self) -> Path:
        return self.temp_dir / "scenes"

    @property
    def jobs_dir(self) -> Path:
        return self.temp_dir / "jobs"

    @property
    def voiceover_path(self) -> Path:
        return resolve_input_path(self.project_dir, self.voiceover_file)

    @property
    def subtitle_path(self) -> Path | None:
        if not self.subtitle_file:
            return None
        return resolve_input_path(self.project_dir, self.subtitle_file)


@dataclass(slots=True)
class SceneSpec:
    scene_id: str
    asset_id: str
    asset_type: AssetType
    asset_file: str
    start: float
    end: float
    duration: float
    text: str = ""
    motion: MotionSpec = field(default_factory=MotionSpec)
    retime: RetimeSpec = field(default_factory=RetimeSpec)

    def asset_path(self, config: ProjectConfig) -> Path:
        return resolve_input_path(config.project_dir, self.asset_file)


@dataclass(slots=True)
class AssetProbe:
    asset_id: str
    type: AssetType
    path: Path
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    codec: str | None = None
    container: str | None = None


@dataclass(slots=True)
class SceneRenderResult:
    scene_id: str
    output_file: Path
    source_duration: float | None
    target_duration: float
    actual_duration: float | None
    adjustment_summary: str


@dataclass(slots=True)
class ValidationReport:
    passed: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    scene_count: int = 0
    total_duration: float = 0.0


@dataclass(slots=True)
class RenderEvent:
    timestamp: str
    level: str
    stage: str
    message: str
    scene_id: str | None = None


@dataclass(slots=True)
class RenderSummary:
    job_id: str
    project_dir: Path
    final_video: Path
    visual_master: Path
    scene_results: list[SceneRenderResult]
    asset_probes: dict[str, AssetProbe]
    total_duration: float
    total_scenes: int
    started_at: str
    finished_at: str
    manifest_path: Path | None = None


@dataclass(slots=True)
class RenderJob:
    job_id: str
    project_dir: Path
    state: JobState = "idle"
    stage: str = "idle"
    current_scene_id: str | None = None
    created_at: str = field(default_factory=utc_now)
    updated_at: str = field(default_factory=utc_now)
    started_at: str | None = None
    finished_at: str | None = None
    error_message: str | None = None
    validation: ValidationReport | None = None
    logs: list[RenderEvent] = field(default_factory=list)
    scene_results: list[SceneRenderResult] = field(default_factory=list)
    asset_probes: dict[str, AssetProbe] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)
    event_version: int = 0
