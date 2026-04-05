from __future__ import annotations

import json
from pathlib import Path

from tool1_dashboard.database import Tool1Database
from tool1_dashboard.runtime import utc_now
from tool1_dashboard.video_assembly.dashboard_observer import DashboardRenderObserver
from tool1_dashboard.video_assembly.models import (
    AssetProbe,
    RenderSummary,
    SceneRenderResult,
    ValidationReport,
)


def _seed_render_job(db: Tool1Database, render_job_id: str = "job-1") -> None:
    db.create_render_job(
        {
            "id": render_job_id,
            "episode_id": "episode-1",
            "language_code": "en",
            "state": "queued",
            "stage": "queued",
            "created_at": utc_now(),
            "updated_at": utc_now(),
        }
    )


def test_dashboard_observer_persists_progress_and_completion(tmp_path: Path) -> None:
    db = Tool1Database(tmp_path / "db.sqlite")
    db.initialize()
    _seed_render_job(db)

    observer = DashboardRenderObserver(db, "job-1")
    observer.set_state("rendering", "Rendering scene_001", "scene_001")
    observer.set_validation(
        ValidationReport(
            passed=True,
            errors=[],
            warnings=["Minor warning"],
            scene_count=1,
            total_duration=2.0,
        )
    )
    observer.set_asset_probes(
        {
            "asset_001": AssetProbe(
                asset_id="asset_001",
                type="image",
                path=tmp_path / "input" / "assets" / "asset_001.png",
            )
        }
    )
    observer.add_scene_result(
        SceneRenderResult(
            scene_id="scene_001",
            output_file=tmp_path / "scene_001.mp4",
            source_duration=2.0,
            target_duration=2.0,
            actual_duration=2.0,
            adjustment_summary="no-op",
        )
    )

    final_video = tmp_path / "final_video.mp4"
    visual_master = tmp_path / "visual_master.mp4"
    manifest_path = tmp_path / "render_manifest.json"
    final_video.write_bytes(b"video")
    visual_master.write_bytes(b"visual")
    manifest_path.write_text("{}", encoding="utf-8")

    observer.complete(
        RenderSummary(
            job_id="job-1",
            project_dir=tmp_path,
            final_video=final_video,
            visual_master=visual_master,
            scene_results=[],
            asset_probes={},
            total_duration=2.0,
            total_scenes=1,
            started_at=utc_now(),
            finished_at=utc_now(),
            manifest_path=manifest_path,
        )
    )

    job = db.get_render_job("job-1")
    assert job is not None
    assert job["state"] == "completed"
    assert job["stage"] == "completed"
    assert job["current_scene_id"] == "scene_001"
    assert job["completed_scenes"] == 1
    assert job["total_scenes"] == 1

    validation_payload = json.loads(job["validation_json"])
    assert validation_payload["passed"] is True
    assert validation_payload["warnings"] == ["Minor warning"]

    outputs_payload = json.loads(job["outputs_json"])
    assert outputs_payload["final_video"] == str(final_video)
    assert outputs_payload["visual_master"] == str(visual_master)
    assert outputs_payload["manifest"] == str(manifest_path)

    logs = db.list_render_logs("job-1")
    assert [(log["stage"], log["level"]) for log in logs] == [
        ("rendering", "INFO"),
        ("probing", "INFO"),
        ("completed", "INFO"),
    ]


def test_dashboard_observer_marks_failure_and_logs_error(tmp_path: Path) -> None:
    db = Tool1Database(tmp_path / "db.sqlite")
    db.initialize()
    _seed_render_job(db, render_job_id="job-2")

    observer = DashboardRenderObserver(db, "job-2")
    observer.fail("ffmpeg crashed")

    job = db.get_render_job("job-2")
    assert job is not None
    assert job["state"] == "failed"
    assert job["stage"] == "failed"
    assert job["error_message"] == "ffmpeg crashed"
    assert job["finished_at"]

    logs = db.list_render_logs("job-2")
    assert len(logs) == 1
    assert logs[0]["stage"] == "failed"
    assert logs[0]["level"] == "ERROR"
    assert logs[0]["message"] == "ffmpeg crashed"
