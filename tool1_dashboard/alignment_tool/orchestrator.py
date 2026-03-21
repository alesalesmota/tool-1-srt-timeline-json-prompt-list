from __future__ import annotations

from pathlib import Path
from typing import Callable

from .align_with_mfa import run_mfa_alignment
from .align_with_whisperx import run_whisperx_alignment
from .config import OUTPUT_ROOT, TEMP_ROOT, resolve_language_profile
from .export_json import write_json
from .export_srt import write_srt
from .extract_script import extract_script_text
from .load_inputs import validate_audio_file, validate_script_file
from .mfa_resources import ensure_mfa_language_resources
from .models import AlignmentArtifacts, AlignmentResult, EngineConfig, SegmentationConfig
from .normalize_audio import normalize_audio_file
from .normalize_script import normalize_script
from .parse_alignment import map_raw_words_to_script
from .report import build_alignment_report
from .runtime import ensure_dir, log_event, make_run_id, runtime_profile, write_run_log
from .segment_subtitles import segment_words


def _engine_attempts(config: EngineConfig) -> list[str]:
    attempts = [config.primary_engine]
    if config.fallback_engine and config.fallback_engine != config.primary_engine:
        attempts.append(config.fallback_engine)
    return attempts


def run_alignment_job(
    audio_path: Path,
    script_path: Path,
    language_code: str,
    engine_config: EngineConfig | None = None,
    segmentation_config: SegmentationConfig | None = None,
    output_root: Path | None = None,
    logger: Callable[[str], None] | None = None,
    progress_callback: Callable[[str, int], None] | None = None,
) -> AlignmentResult:
    engine_config = engine_config or EngineConfig()
    segmentation_config = segmentation_config or SegmentationConfig()
    output_root = output_root or OUTPUT_ROOT
    log_lines: list[str] = []

    def _log(message: str) -> None:
        log_event(message, collector=log_lines.append)
        if logger is not None:
            logger(log_lines[-1])

    def _progress(step: str, percent: int) -> None:
        if progress_callback is not None:
            progress_callback(step, percent)

    _progress("Preparing files", 5)
    audio_path = validate_audio_file(Path(audio_path))
    script_path = validate_script_file(Path(script_path))
    language_profile = resolve_language_profile(language_code)
    run_id = make_run_id(audio_path.stem)
    output_dir = ensure_dir(output_root / run_id)
    temp_dir = ensure_dir(TEMP_ROOT / run_id)
    _log(f"Starting alignment run '{run_id}'.")
    _log(f"Language selected: {language_profile.label} ({language_profile.code}).")
    runtime = runtime_profile()
    _log(
        "Acceleration: "
        f"{runtime['device_label']} | "
        f"WhisperX={runtime['whisperx_device']} ({runtime['whisperx_compute_type']}) | "
        f"faster-whisper={runtime['faster_whisper_device']} ({runtime['faster_whisper_compute_type']})."
    )

    if "mfa" in _engine_attempts(engine_config):
        _progress("Preparing language files", 10)
        ensure_mfa_language_resources(
            language_profile.code,
            logger=_log,
            progress_callback=_progress,
        )

    _progress("Reading script", 15)
    raw_script = extract_script_text(script_path)
    script_document = normalize_script(raw_script)
    _log(f"Loaded script with {len(script_document.words)} words.")

    _progress("Normalizing audio", 30)
    normalized_audio = normalize_audio_file(audio_path, output_dir / "normalized_audio.wav", logger=_log)
    _log(f"Normalized audio duration: {normalized_audio.duration_seconds:.2f}s.")

    engine_used = ""
    fallback_used = False
    fallback_reason = None
    report_warnings: list[str] = []
    engine_errors: list[str] = []
    mapped_words = []
    mismatch_count = 0
    approximate_word_count = 0
    dropped_word_count = 0

    for position, engine_name in enumerate(_engine_attempts(engine_config)):
        try:
            if engine_name == "mfa":
                _progress("Aligning with MFA", 55 if position == 0 else 65)
            elif engine_name == "whisperx":
                _progress("Aligning with WhisperX", 55 if position == 0 else 65)
            if engine_name == "mfa":
                raw_words, engine_warnings = run_mfa_alignment(
                    normalized_audio.path,
                    script_document,
                    language_profile,
                    temp_dir,
                    audio_duration_seconds=normalized_audio.duration_seconds,
                    logger=_log,
                )
            elif engine_name == "whisperx":
                raw_words, engine_warnings = run_whisperx_alignment(
                    normalized_audio.path,
                    language_profile,
                    model_name=engine_config.whisperx_model,
                    logger=_log,
                )
            else:
                raise ValueError(f"Unsupported engine '{engine_name}'.")

            mapped_words, mismatch_count, approximate_word_count, dropped_word_count, mapping_warnings = map_raw_words_to_script(
                raw_words,
                script_document,
                source=engine_name,
                audio_duration=normalized_audio.duration_seconds,
            )
            engine_used = engine_name
            report_warnings.extend(engine_warnings)
            report_warnings.extend(mapping_warnings)
            if position > 0:
                fallback_used = True
                fallback_reason = engine_errors[-1] if engine_errors else None
                _log(f"Fallback engine succeeded: {engine_name}.")
            else:
                _log(f"Primary engine succeeded: {engine_name}.")
            break
        except Exception as exc:
            detail = str(exc).strip() or exc.__class__.__name__
            engine_errors.append(f"{engine_name}: {detail}")
            verb = "skipped" if detail.startswith("MFA skipped:") else "failed"
            _log(f"{engine_name} {verb}: {detail}")
            if position == len(_engine_attempts(engine_config)) - 1:
                raise RuntimeError("All alignment engines failed: " + " | ".join(engine_errors)) from exc

    _progress("Building subtitle blocks", 82)
    segments, segmentation_warnings = segment_words(script_document, mapped_words, segmentation_config)
    report_warnings.extend(segmentation_warnings)
    _log(f"Generated {len(segments)} subtitle segments.")

    _progress("Writing output files", 92)
    final_srt_path = output_dir / "final.srt"
    srt_text = write_srt(final_srt_path, segments)
    words_json_path = output_dir / "words.json"
    segments_json_path = output_dir / "segments.json"
    report_json_path = output_dir / "alignment_report.json"
    run_log_path = output_dir / "run.log"

    write_json(words_json_path, [word.to_dict() for word in mapped_words])
    write_json(segments_json_path, [segment.to_dict() for segment in segments])
    report = build_alignment_report(
        engine=engine_used,
        fallback_used=fallback_used,
        fallback_reason=fallback_reason,
        audio_info=normalized_audio,
        script_document=script_document,
        words=mapped_words,
        segments=segments,
        warnings=report_warnings,
        approximate_word_count=approximate_word_count,
        mismatch_count=mismatch_count,
        dropped_word_count=dropped_word_count,
    )
    write_json(report_json_path, report.to_dict())
    write_run_log(run_log_path, log_lines)
    _log(f"Artifacts saved to {output_dir}.")
    _progress("Done", 100)

    artifacts = AlignmentArtifacts(
        output_dir=output_dir,
        final_srt=final_srt_path,
        words_json=words_json_path,
        segments_json=segments_json_path,
        alignment_report=report_json_path,
        normalized_audio=normalized_audio.path,
        run_log=run_log_path,
    )
    return AlignmentResult(
        run_id=run_id,
        output_dir=output_dir,
        script_document=script_document,
        normalized_audio=normalized_audio,
        engine_used=engine_used,
        fallback_used=fallback_used,
        fallback_reason=fallback_reason,
        words=mapped_words,
        segments=segments,
        report=report,
        artifacts=artifacts,
        logs=log_lines + [f"SRT preview length: {len(srt_text)} characters."],
    )
