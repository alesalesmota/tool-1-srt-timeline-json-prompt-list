"""Bridge between Tool 2's RenderPipeline observer protocol and Tool 1's SQLite database."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from .models import AssetProbe, RenderSummary, SceneRenderResult, ValidationReport
from .utils import utc_now

if TYPE_CHECKING:
    from tool1_dashboard.database import Tool1Database


class DashboardRenderObserver:
    """Implements ``PipelineObserver`` protocol, persisting progress to SQLite.

    Each method writes to the ``render_jobs`` and ``render_logs`` tables so
    that the dashboard API can stream live updates to the frontend via SSE.
    """

    def __init__(self, db: Tool1Database, render_job_id: str) -> None:
        self._db = db
        self._job_id = render_job_id
        self._completed_scenes = 0

    # ── PipelineObserver protocol ──────────────────────────────────

    def set_state(self, state: str, message: str, scene_id: str | None = None) -> None:
        now = utc_now()
        self._db.update_render_job(
            self._job_id,
            state=state,
            stage=state,
            current_scene_id=scene_id,
            updated_at=now,
        )
        self._db.append_render_log(
            render_job_id=self._job_id,
            timestamp=now,
            level="INFO",
            stage=state,
            message=message,
            scene_id=scene_id,
        )

    def log(self, level: str, stage: str, message: str, scene_id: str | None = None) -> None:
        self._db.append_render_log(
            render_job_id=self._job_id,
            timestamp=utc_now(),
            level=level,
            stage=stage,
            message=message,
            scene_id=scene_id,
        )

    def set_validation(self, report: ValidationReport) -> None:
        payload = {
            "passed": report.passed,
            "errors": report.errors,
            "warnings": report.warnings,
            "scene_count": report.scene_count,
            "total_duration": report.total_duration,
        }
        self._db.update_render_job(
            self._job_id,
            validation_json=json.dumps(payload, ensure_ascii=False),
            total_scenes=report.scene_count,
            updated_at=utc_now(),
        )

    def set_asset_probes(self, probes: dict[str, AssetProbe]) -> None:
        self._db.append_render_log(
            render_job_id=self._job_id,
            timestamp=utc_now(),
            level="INFO",
            stage="probing",
            message=f"Probed {len(probes)} unique asset(s).",
        )

    def add_scene_result(self, result: SceneRenderResult) -> None:
        self._completed_scenes += 1
        self._db.update_render_job(
            self._job_id,
            completed_scenes=self._completed_scenes,
            current_scene_id=result.scene_id,
            updated_at=utc_now(),
        )

    def complete(self, summary: RenderSummary) -> None:
        now = utc_now()
        outputs = {
            "final_video": str(summary.final_video),
            "visual_master": str(summary.visual_master),
        }
        if summary.manifest_path:
            outputs["manifest"] = str(summary.manifest_path)

        self._db.update_render_job(
            self._job_id,
            state="completed",
            stage="completed",
            outputs_json=json.dumps(outputs, ensure_ascii=False),
            finished_at=now,
            updated_at=now,
        )
        self._db.append_render_log(
            render_job_id=self._job_id,
            timestamp=now,
            level="INFO",
            stage="completed",
            message=f"Render complete — {summary.total_scenes} scenes, {summary.total_duration:.1f}s total.",
        )

    def fail(self, message: str) -> None:
        now = utc_now()
        self._db.update_render_job(
            self._job_id,
            state="failed",
            stage="failed",
            error_message=message,
            finished_at=now,
            updated_at=now,
        )
        self._db.append_render_log(
            render_job_id=self._job_id,
            timestamp=now,
            level="ERROR",
            stage="failed",
            message=message,
        )
