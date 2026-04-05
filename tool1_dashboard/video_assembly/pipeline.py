from __future__ import annotations

from pathlib import Path
from typing import Protocol

from .burn_subtitles import burn_subtitles
from .concat_scenes import concatenate_scenes
from .models import (
    AssetProbe,
    RenderSummary,
    SceneRenderResult,
    SceneSpec,
    ValidationReport,
)
from .mux_voiceover import mux_voiceover
from .probe_assets import probe_assets
from .render_image_scene import render_image_scene
from .render_video_scene import render_video_scene
from .timeline import load_timeline
from .utils import ensure_runtime_dirs, utc_now, write_json
from .validation import validate_or_raise


class PipelineObserver(Protocol):
    def set_state(self, state: str, message: str, scene_id: str | None = None) -> None: ...
    def log(self, level: str, stage: str, message: str, scene_id: str | None = None) -> None: ...
    def set_validation(self, report: ValidationReport) -> None: ...
    def set_asset_probes(self, probes: dict[str, AssetProbe]) -> None: ...
    def add_scene_result(self, result: SceneRenderResult) -> None: ...
    def complete(self, summary: RenderSummary) -> None: ...
    def fail(self, message: str) -> None: ...


class NullObserver:
    def set_state(self, state: str, message: str, scene_id: str | None = None) -> None:
        return None

    def log(self, level: str, stage: str, message: str, scene_id: str | None = None) -> None:
        return None

    def set_validation(self, report: ValidationReport) -> None:
        return None

    def set_asset_probes(self, probes: dict[str, AssetProbe]) -> None:
        return None

    def add_scene_result(self, result: SceneRenderResult) -> None:
        return None

    def complete(self, summary: RenderSummary) -> None:
        return None

    def fail(self, message: str) -> None:
        return None


class RenderPipeline:
    def __init__(self, project_dir: Path, job_id: str, observer: PipelineObserver | None = None):
        self.project_dir = Path(project_dir)
        self.job_id = job_id
        self.observer = observer or NullObserver()

    def run(self) -> RenderSummary:
        started_at = utc_now()
        try:
            ensure_runtime_dirs(self.project_dir)

            self.observer.set_state("validating", "Loading and validating timeline")
            config, scenes = load_timeline(self.project_dir)
            validation = validate_or_raise(config, scenes)
            self.observer.set_validation(validation)
            self.observer.log(
                "INFO",
                "validating",
                f"Validated {validation.scene_count} scene(s) for {validation.total_duration:.2f}s total.",
            )

            self.observer.set_state("probing", "Probing assets")
            probes = probe_assets(config, scenes)
            self.observer.set_asset_probes(probes)
            self.observer.log("INFO", "probing", f"Probed {len(probes)} unique asset(s).")

            self.observer.set_state("rendering", "Rendering scene clips")
            scene_results = self._render_scenes(config, scenes, probes)

            self.observer.set_state("concatenating", "Concatenating rendered scenes")
            concat_list_path, visual_master_path = concatenate_scenes(
                config,
                [result.output_file for result in scene_results],
            )
            self.observer.log(
                "INFO",
                "concatenating",
                f"Concat list written to {concat_list_path}",
            )
            self.observer.log(
                "INFO",
                "concatenating",
                f"Visual master written to {visual_master_path}",
            )

            self.observer.set_state("muxing", "Muxing voiceover")
            final_video_path = mux_voiceover(config, visual_master_path, self.job_id)
            self.observer.log("INFO", "muxing", f"Final video written to {final_video_path}")

            if config.subtitle_path and config.subtitle_path.exists():
                self.observer.set_state("subtitling", "Burning subtitles")
                subtitled_path = burn_subtitles(config, final_video_path, self.job_id)
                self.observer.log("INFO", "subtitling", f"Subtitled video written to {subtitled_path}")
                final_video_path = subtitled_path

            finished_at = utc_now()
            summary = RenderSummary(
                job_id=self.job_id,
                project_dir=self.project_dir,
                final_video=final_video_path,
                visual_master=visual_master_path,
                scene_results=scene_results,
                asset_probes=probes,
                total_duration=validation.total_duration,
                total_scenes=len(scenes),
                started_at=started_at,
                finished_at=finished_at,
            )
            manifest_path = config.output_dir / f"render_manifest_{self.job_id}.json"
            summary.manifest_path = manifest_path
            write_json(manifest_path, summary)
            self.observer.complete(summary)
            return summary
        except Exception as exc:
            self.observer.fail(str(exc))
            raise

    def _render_scenes(
        self,
        config,
        scenes: list[SceneSpec],
        probes: dict[str, AssetProbe],
    ) -> list[SceneRenderResult]:
        results: list[SceneRenderResult] = []
        for scene in scenes:
            self.observer.set_state("rendering", f"Rendering {scene.scene_id}", scene.scene_id)
            probe = probes[scene.asset_id]
            self.observer.log(
                "INFO",
                "rendering",
                f"Rendering {scene.scene_id} from {scene.asset_file} to {scene.duration:.2f}s",
                scene.scene_id,
            )
            output_file = config.scenes_dir / f"{scene.scene_id}.mp4"
            if scene.asset_type == "image":
                result = render_image_scene(config, scene, output_file)
            else:
                result = render_video_scene(config, scene, probe, output_file)
            results.append(result)
            self.observer.add_scene_result(result)
            self.observer.log(
                "INFO",
                "rendering",
                f"{scene.scene_id} complete using {result.adjustment_summary}",
                scene.scene_id,
            )
        return results
