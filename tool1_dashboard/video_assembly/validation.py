from __future__ import annotations

from .exceptions import TimelineValidationError
from .models import ProjectConfig, SceneSpec, ValidationReport

SUPPORTED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
SUPPORTED_VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm"}
TIME_TOLERANCE = 0.05
GAP_WARNING_TOLERANCE = 0.5


def validate_project(config: ProjectConfig, scenes: list[SceneSpec]) -> ValidationReport:
    errors: list[str] = []
    warnings: list[str] = []

    if not config.input_dir.exists():
        errors.append(f"Input directory not found: {config.input_dir}")

    if not scenes:
        errors.append("Timeline must contain at least one scene.")

    voiceover_path = config.voiceover_path
    if not voiceover_path.exists():
        errors.append(f"Voiceover file not found: {voiceover_path}")

    subtitle_path = config.subtitle_path
    if subtitle_path and not subtitle_path.exists():
        errors.append(f"Subtitle file declared but not found: {subtitle_path}")

    total_duration = 0.0
    previous_end: float | None = None
    for index, scene in enumerate(scenes, start=1):
        label = f"{scene.scene_id} (scene {index})"
        total_duration += scene.duration

        if scene.asset_type not in {"image", "video"}:
            errors.append(f"{label}: unsupported asset_type `{scene.asset_type}`.")

        if scene.start >= scene.end:
            errors.append(f"{label}: `start` must be lower than `end`.")

        if abs((scene.end - scene.start) - scene.duration) > TIME_TOLERANCE:
            errors.append(
                f"{label}: `duration` must match `end - start` within {TIME_TOLERANCE:.2f}s."
            )

        if previous_end is not None:
            if scene.start < previous_end - TIME_TOLERANCE:
                errors.append(f"{label}: scene overlaps the previous scene.")
            elif scene.start > previous_end + GAP_WARNING_TOLERANCE:
                warnings.append(f"{label}: timing gap ({scene.start - previous_end:.3f}s) after the previous scene.")
            elif scene.start > previous_end + TIME_TOLERANCE:
                warnings.append(f"{label}: small timing gap ({scene.start - previous_end:.3f}s) after the previous scene.")
        previous_end = scene.end

        asset_path = scene.asset_path(config)
        if not asset_path.exists():
            errors.append(f"{label}: asset file not found: {asset_path}")
            continue

        extension = asset_path.suffix.lower()
        if scene.asset_type == "image" and extension not in SUPPORTED_IMAGE_EXTENSIONS:
            errors.append(f"{label}: asset extension `{extension}` is not supported for images.")
        if scene.asset_type == "video" and extension not in SUPPORTED_VIDEO_EXTENSIONS:
            errors.append(f"{label}: asset extension `{extension}` is not supported for videos.")

        if scene.asset_id not in asset_path.stem:
            warnings.append(
                f"{label}: asset filename does not include the asset_id `{scene.asset_id}`."
            )

    return ValidationReport(
        passed=not errors,
        errors=errors,
        warnings=warnings,
        scene_count=len(scenes),
        total_duration=total_duration,
    )


def validate_or_raise(config: ProjectConfig, scenes: list[SceneSpec]) -> ValidationReport:
    report = validate_project(config, scenes)
    if not report.passed:
        raise TimelineValidationError(report.errors, report.warnings)
    return report
