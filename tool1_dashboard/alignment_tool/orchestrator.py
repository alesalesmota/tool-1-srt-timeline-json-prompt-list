from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Callable

from .align_with_mfa import run_mfa_alignment
from .align_with_whisperx import run_whisperx_alignment
from .config import OUTPUT_ROOT, TEMP_ROOT, resolve_language_profile
from .export_json import write_json
from .export_srt import write_srt
from .extract_script import extract_script_text
from .guided_chunking import run_guided_chunked_mfa
from .load_inputs import validate_audio_file, validate_script_file
from .mfa_resources import ensure_mfa_language_resources
from .models import (
    AlignmentArtifacts,
    AlignmentResult,
    EngineConfig,
    SegmentationConfig,
    SegmentationDiagnostics,
    SubtitleSegment,
    WordTiming,
)
from .normalize_audio import normalize_audio_file
from .normalize_script import normalize_script
from .parse_alignment import RawAlignedWord, map_raw_words_to_script
from .report import build_alignment_report
from .runtime import ensure_dir, log_event, make_run_id, runtime_profile, write_run_log
from .segment_subtitles import segment_words


@dataclass
class _AlignmentCandidate:
    strategy: str
    engine: str
    mapped_words: list[WordTiming]
    segments: list[SubtitleSegment]
    warnings: list[str]
    mismatch_count: int
    approximate_word_count: int
    dropped_word_count: int
    mapping_diagnostics: dict[str, int]
    segmentation_diagnostics: SegmentationDiagnostics
    raw_words: list[RawAlignedWord] | None = None
    chunk_count: int = 0


def _engine_attempts(config: EngineConfig) -> list[str]:
    attempts = [config.primary_engine]
    if config.fallback_engine and config.fallback_engine != config.primary_engine:
        attempts.append(config.fallback_engine)
    return attempts


def _integrity_limit(script_word_count: int) -> int:
    return max(25, int(math.ceil(script_word_count * 0.003)))


def _reading_speed_warning_count(candidate: _AlignmentCandidate, max_reading_cps: float) -> int:
    return sum(1 for segment in candidate.segments if segment.reading_cps > max_reading_cps)


def _max_reading_cps(candidate: _AlignmentCandidate) -> float:
    return max((segment.reading_cps for segment in candidate.segments), default=0.0)


def _candidate_sort_key(candidate: _AlignmentCandidate, max_reading_cps: float) -> tuple[float, ...]:
    return (
        float(candidate.dropped_word_count),
        float(candidate.approximate_word_count),
        float(candidate.mismatch_count),
        float(_reading_speed_warning_count(candidate, max_reading_cps)),
        float(_max_reading_cps(candidate)),
    )


def _candidate_metrics(candidate: _AlignmentCandidate, max_reading_cps: float) -> dict[str, object]:
    sorted_cps = sorted(segment.reading_cps for segment in candidate.segments)
    p95_index = max(0, min(len(sorted_cps) - 1, math.ceil(0.95 * len(sorted_cps)) - 1)) if sorted_cps else 0
    return {
        "strategy": candidate.strategy,
        "engine": candidate.engine,
        "chunk_count": candidate.chunk_count,
        "dropped_word_count": candidate.dropped_word_count,
        "approximate_word_count": candidate.approximate_word_count,
        "mismatch_count": candidate.mismatch_count,
        "reading_speed_warning_count": _reading_speed_warning_count(candidate, max_reading_cps),
        "max_reading_cps": _max_reading_cps(candidate),
        "p95_reading_cps": sorted_cps[p95_index] if sorted_cps else 0.0,
        "segments_over_24_cps": candidate.segmentation_diagnostics.segments_over_24_cps,
        "segments_over_30_cps": candidate.segmentation_diagnostics.segments_over_30_cps,
    }


def _needs_guided_chunk_retry(candidate: _AlignmentCandidate, script_word_count: int) -> bool:
    return (
        candidate.dropped_word_count > 0
        or candidate.approximate_word_count > _integrity_limit(script_word_count)
        or candidate.mismatch_count > 25
    )


def _build_candidate_from_raw_words(
    *,
    strategy: str,
    engine: str,
    raw_words: list[RawAlignedWord],
    raw_warnings: list[str],
    script_document,
    language_code: str,
    audio_duration: float,
    segmentation_config: SegmentationConfig,
) -> _AlignmentCandidate:
    mapped_words, mismatch_count, approximate_word_count, dropped_word_count, mapping_warnings, mapping_diagnostics = map_raw_words_to_script(
        raw_words,
        script_document,
        source=engine,
        audio_duration=audio_duration,
        language_code=language_code,
    )
    segmentation_result = segment_words(script_document, mapped_words, segmentation_config)
    return _AlignmentCandidate(
        strategy=strategy,
        engine=engine,
        mapped_words=mapped_words,
        segments=segmentation_result.segments,
        warnings=[*raw_warnings, *mapping_warnings, *segmentation_result.warnings],
        mismatch_count=mismatch_count,
        approximate_word_count=approximate_word_count,
        dropped_word_count=dropped_word_count,
        mapping_diagnostics=mapping_diagnostics,
        segmentation_diagnostics=segmentation_result.diagnostics,
        raw_words=raw_words,
        chunk_count=1,
    )


def _run_engine_candidate(
    *,
    engine_name: str,
    strategy: str,
    normalized_audio_path: Path,
    audio_duration: float,
    script_document,
    language_profile,
    temp_dir: Path,
    engine_config: EngineConfig,
    segmentation_config: SegmentationConfig,
    logger: Callable[[str], None] | None = None,
) -> _AlignmentCandidate:
    if engine_name == "mfa":
        raw_words, raw_warnings = run_mfa_alignment(
            normalized_audio_path,
            script_document,
            language_profile,
            temp_dir / strategy,
            audio_duration_seconds=audio_duration,
            logger=logger,
        )
    elif engine_name == "whisperx":
        raw_words, raw_warnings = run_whisperx_alignment(
            normalized_audio_path,
            language_profile,
            model_name=engine_config.whisperx_model,
            logger=logger,
        )
    else:
        raise ValueError(f"Unsupported engine '{engine_name}'.")
    return _build_candidate_from_raw_words(
        strategy=strategy,
        engine=engine_name,
        raw_words=raw_words,
        raw_warnings=raw_warnings,
        script_document=script_document,
        language_code=language_profile.code,
        audio_duration=audio_duration,
        segmentation_config=segmentation_config,
    )


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
    script_document = normalize_script(raw_script, language_profile.code)
    _log(f"Loaded script with {len(script_document.words)} words.")

    _progress("Normalizing audio", 30)
    normalized_audio = normalize_audio_file(audio_path, output_dir / "normalized_audio.wav", logger=_log)
    _log(f"Normalized audio duration: {normalized_audio.duration_seconds:.2f}s.")

    candidates: list[_AlignmentCandidate] = []
    engine_errors: list[str] = []
    primary_candidate: _AlignmentCandidate | None = None
    primary_strategy = f"single_pass_{engine_config.primary_engine}"

    try:
        _progress(f"Aligning with {engine_config.primary_engine.upper()}", 55)
        primary_candidate = _run_engine_candidate(
            engine_name=engine_config.primary_engine,
            strategy=primary_strategy,
            normalized_audio_path=normalized_audio.path,
            audio_duration=normalized_audio.duration_seconds,
            script_document=script_document,
            language_profile=language_profile,
            temp_dir=temp_dir,
            engine_config=engine_config,
            segmentation_config=segmentation_config,
            logger=_log,
        )
        candidates.append(primary_candidate)
        _log(f"Primary engine succeeded: {engine_config.primary_engine}.")
    except Exception as exc:
        detail = str(exc).strip() or exc.__class__.__name__
        engine_errors.append(f"{engine_config.primary_engine}: {detail}")
        verb = "skipped" if detail.startswith("MFA skipped:") else "failed"
        _log(f"{engine_config.primary_engine} {verb}: {detail}")

    whisperx_candidate: _AlignmentCandidate | None = None
    if primary_candidate is None and engine_config.fallback_engine and engine_config.fallback_engine != engine_config.primary_engine:
        try:
            _progress(f"Aligning with {engine_config.fallback_engine.upper()}", 65)
            fallback_candidate = _run_engine_candidate(
                engine_name=engine_config.fallback_engine,
                strategy=f"{engine_config.fallback_engine}_fallback",
                normalized_audio_path=normalized_audio.path,
                audio_duration=normalized_audio.duration_seconds,
                script_document=script_document,
                language_profile=language_profile,
                temp_dir=temp_dir,
                engine_config=engine_config,
                segmentation_config=segmentation_config,
                logger=_log,
            )
            candidates.append(fallback_candidate)
            if fallback_candidate.engine == "whisperx":
                whisperx_candidate = fallback_candidate
            _log(f"Fallback engine succeeded: {engine_config.fallback_engine}.")
        except Exception as exc:
            detail = str(exc).strip() or exc.__class__.__name__
            engine_errors.append(f"{engine_config.fallback_engine}: {detail}")
            _log(f"{engine_config.fallback_engine} failed: {detail}")

    if primary_candidate is not None and primary_candidate.engine == "mfa" and _needs_guided_chunk_retry(primary_candidate, len(script_document.words)):
        _log("Single-pass MFA exceeded retry thresholds. Evaluating guided chunked alignment.")
        if whisperx_candidate is None:
            try:
                _progress("Aligning with WhisperX", 62)
                whisperx_candidate = _run_engine_candidate(
                    engine_name="whisperx",
                    strategy="whisperx_fallback",
                    normalized_audio_path=normalized_audio.path,
                    audio_duration=normalized_audio.duration_seconds,
                    script_document=script_document,
                    language_profile=language_profile,
                    temp_dir=temp_dir,
                    engine_config=engine_config,
                    segmentation_config=segmentation_config,
                    logger=_log,
                )
                candidates.append(whisperx_candidate)
                _log("WhisperX candidate generated for fallback and chunk guidance.")
            except Exception as exc:
                detail = str(exc).strip() or exc.__class__.__name__
                engine_errors.append(f"whisperx: {detail}")
                _log(f"whisperx failed: {detail}")

        if whisperx_candidate is not None and whisperx_candidate.raw_words:
            try:
                _progress("Running guided chunked MFA", 72)
                mapped_words, guided_warnings, mapping_diagnostics, guided_summary, chunk_count = run_guided_chunked_mfa(
                    normalized_audio_path=normalized_audio.path,
                    script_document=script_document,
                    language_profile=language_profile,
                    temp_dir=temp_dir,
                    audio_duration_seconds=normalized_audio.duration_seconds,
                    guidance_raw_words=whisperx_candidate.raw_words,
                    logger=_log,
                )
                segmentation_result = segment_words(script_document, mapped_words, segmentation_config)
                candidates.append(
                    _AlignmentCandidate(
                        strategy="guided_chunked_mfa",
                        engine="mfa",
                        mapped_words=mapped_words,
                        segments=segmentation_result.segments,
                        warnings=[*guided_warnings, *segmentation_result.warnings],
                        mismatch_count=int(guided_summary["mismatch_count"]),
                        approximate_word_count=int(guided_summary["approximate_word_count"]),
                        dropped_word_count=int(guided_summary["dropped_word_count"]),
                        mapping_diagnostics=mapping_diagnostics,
                        segmentation_diagnostics=segmentation_result.diagnostics,
                        raw_words=None,
                        chunk_count=chunk_count,
                    )
                )
                _log(f"Guided chunked MFA produced {chunk_count} chunks.")
            except Exception as exc:
                detail = str(exc).strip() or exc.__class__.__name__
                engine_errors.append(f"guided_chunked_mfa: {detail}")
                _log(f"guided_chunked_mfa failed: {detail}")

    if not candidates:
        raise RuntimeError("All alignment engines failed: " + " | ".join(engine_errors))

    best_candidate = min(
        candidates,
        key=lambda candidate: _candidate_sort_key(candidate, segmentation_config.max_reading_cps),
    )
    fallback_used = best_candidate.strategy != primary_strategy
    fallback_reason = None
    if fallback_used:
        if best_candidate.strategy == "guided_chunked_mfa":
            fallback_reason = "Single-pass MFA exceeded quality thresholds."
        elif engine_errors:
            fallback_reason = engine_errors[-1]
        else:
            fallback_reason = f"Selected {best_candidate.strategy} because it produced better alignment metrics."
    _log(
        "Selected alignment candidate: "
        f"{best_candidate.strategy} "
        f"(dropped={best_candidate.dropped_word_count}, "
        f"approximate={best_candidate.approximate_word_count}, "
        f"mismatch={best_candidate.mismatch_count})."
    )

    reading_warning_ratio = (
        _reading_speed_warning_count(best_candidate, segmentation_config.max_reading_cps) / max(len(best_candidate.segments), 1)
    )
    if _max_reading_cps(best_candidate) > 30.0 or reading_warning_ratio > 0.10:
        best_candidate.warnings.append(
            "Subtitle density remains high after optimization; review recommended before export."
        )

    _progress("Building subtitle blocks", 82)
    integrity_limit = _integrity_limit(len(script_document.words))
    if best_candidate.dropped_word_count > 0 or best_candidate.approximate_word_count > integrity_limit:
        raise RuntimeError(
            "Alignment failed quality gate: "
            f"strategy={best_candidate.strategy}, "
            f"dropped={best_candidate.dropped_word_count}, "
            f"approximate={best_candidate.approximate_word_count}, "
            f"limit={integrity_limit}."
        )

    _progress("Writing output files", 92)
    final_srt_path = output_dir / "final.srt"
    srt_text = write_srt(final_srt_path, best_candidate.segments)
    words_json_path = output_dir / "words.json"
    segments_json_path = output_dir / "segments.json"
    report_json_path = output_dir / "alignment_report.json"
    run_log_path = output_dir / "run.log"

    write_json(words_json_path, [word.to_dict() for word in best_candidate.mapped_words])
    write_json(segments_json_path, [segment.to_dict() for segment in best_candidate.segments])
    report = build_alignment_report(
        engine=best_candidate.engine,
        fallback_used=fallback_used,
        fallback_reason=fallback_reason,
        audio_info=normalized_audio,
        script_document=script_document,
        words=best_candidate.mapped_words,
        segments=best_candidate.segments,
        warnings=best_candidate.warnings,
        approximate_word_count=best_candidate.approximate_word_count,
        mismatch_count=best_candidate.mismatch_count,
        dropped_word_count=best_candidate.dropped_word_count,
        strategy=best_candidate.strategy,
        chunk_count=best_candidate.chunk_count,
        max_reading_cps_target=segmentation_config.max_reading_cps,
        mapping_diagnostics=best_candidate.mapping_diagnostics,
        segmentation_diagnostics=best_candidate.segmentation_diagnostics,
        candidate_metrics=[
            _candidate_metrics(candidate, segmentation_config.max_reading_cps)
            for candidate in candidates
        ],
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
        engine_used=best_candidate.engine,
        fallback_used=fallback_used,
        fallback_reason=fallback_reason,
        words=best_candidate.mapped_words,
        segments=best_candidate.segments,
        report=report,
        artifacts=artifacts,
        logs=log_lines + [f"SRT preview length: {len(srt_text)} characters."],
    )
