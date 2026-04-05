from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .config import DEFAULT_SEGMENTATION, PROJECT_ROOT
from .export_json import write_json
from .export_srt import write_srt
from .models import NormalizedAudioInfo, SegmentationConfig, SubtitleSegment, WordTiming
from .normalize_script import normalize_script
from .report import build_alignment_report
from .runtime import ensure_dir, make_run_id
from .segment_subtitles import segment_words
from ..translation.language_rules import build_spoken_script

REPO_ROOT = PROJECT_ROOT.parent


def _load_summary(path: Path) -> list[dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Benchmark summary must be a JSON array.")
    return [dict(item) for item in payload]


def _load_words(path: Path) -> list[WordTiming]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [WordTiming(**item) for item in payload]


def _load_segments(path: Path) -> list[SubtitleSegment]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [SubtitleSegment(**item) for item in payload]


def _script_paths_for_language(episode_dir: Path, language_code: str) -> tuple[Path, Path]:
    if language_code == "en":
        readable_path = episode_dir / "script_original.txt"
        spoken_path = episode_dir / "script_original_spoken.txt"
    else:
        readable_path = episode_dir / f"script_{language_code}.txt"
        spoken_path = episode_dir / f"script_{language_code}_spoken.txt"
    return readable_path, spoken_path


def _load_spoken_script_text(episode_dir: Path, language_code: str) -> tuple[str, str]:
    readable_path, spoken_path = _script_paths_for_language(episode_dir, language_code)
    if spoken_path.exists():
        return spoken_path.read_text(encoding="utf-8"), "spoken_sidecar"
    readable_text = readable_path.read_text(encoding="utf-8")
    return build_spoken_script(readable_text, language_code), "temp_spoken"


def _baseline_words_path(summary_entry: dict[str, Any]) -> Path:
    output_dir = REPO_ROOT / Path(str(summary_entry["output_dir"]))
    words_path = output_dir / "words.json"
    if not words_path.exists():
        raise FileNotFoundError(f"Benchmark words.json not found: {words_path}")
    return words_path


def _baseline_segments_path(summary_entry: dict[str, Any]) -> Path:
    output_dir = REPO_ROOT / Path(str(summary_entry["output_dir"]))
    segments_path = output_dir / "segments.json"
    if not segments_path.exists():
        raise FileNotFoundError(f"Benchmark segments.json not found: {segments_path}")
    return segments_path


def _normalize_language_list(languages: list[str] | None, summary_entries: list[dict[str, Any]]) -> list[str]:
    if languages:
        return list(dict.fromkeys(str(language).strip() for language in languages if str(language).strip()))
    return [str(entry["language"]) for entry in summary_entries]


def _baseline_entry_for_language(summary_entries: list[dict[str, Any]], language_code: str) -> dict[str, Any]:
    for entry in summary_entries:
        if str(entry.get("language")) == language_code:
            return entry
    raise KeyError(f"No benchmark baseline entry found for language '{language_code}'.")


def _segmentation_warnings(
    *,
    segment_warnings: list[str],
    mismatch_warnings: list[str],
    segments,
    max_reading_cps: float,
) -> list[str]:
    warnings = [*mismatch_warnings, *segment_warnings]
    reading_warning_ratio = sum(1 for segment in segments if segment.reading_cps > max_reading_cps) / max(len(segments), 1)
    if max((segment.reading_cps for segment in segments), default=0.0) > 30.0 or reading_warning_ratio > 0.10:
        warnings.append("Subtitle density remains high after optimization; review recommended before export.")
    return warnings


def run_segmentation_benchmark(
    *,
    episode_dir: Path,
    baseline_summary_path: Path,
    output_root: Path,
    languages: list[str] | None = None,
    segmentation_config: SegmentationConfig | None = None,
) -> list[dict[str, Any]]:
    segmentation_config = segmentation_config or DEFAULT_SEGMENTATION
    summary_entries = _load_summary(baseline_summary_path)
    selected_languages = _normalize_language_list(languages, summary_entries)
    resolved_output_root = output_root if output_root.is_absolute() else (REPO_ROOT / output_root)
    benchmark_root = ensure_dir(resolved_output_root.resolve())
    summary_payload: list[dict[str, Any]] = []

    for language_code in selected_languages:
        baseline = _baseline_entry_for_language(summary_entries, language_code)
        words_path = _baseline_words_path(baseline)
        segments_path = _baseline_segments_path(baseline)
        words = _load_words(words_path)
        seed_segments = _load_segments(segments_path)
        spoken_text, script_origin = _load_spoken_script_text(episode_dir, language_code)
        script_document = normalize_script(spoken_text, language_code)
        if len(script_document.words) != len(words):
            raise ValueError(
                f"Spoken script word count mismatch for {language_code}: "
                f"script={len(script_document.words)} words, words.json={len(words)} words."
            )

        segmentation_result = segment_words(script_document, words, segmentation_config, seed_segments=seed_segments)
        warnings = _segmentation_warnings(
            segment_warnings=segmentation_result.warnings,
            mismatch_warnings=[warning for warning in baseline.get("warnings", []) if "mismatch" in str(warning).lower()],
            segments=segmentation_result.segments,
            max_reading_cps=segmentation_config.max_reading_cps,
        )

        run_id = make_run_id(f"{language_code}-segmentation-only")
        output_dir = ensure_dir(benchmark_root / language_code / run_id)
        write_json(output_dir / "words.json", [word.to_dict() for word in words])
        write_json(output_dir / "segments.json", [segment.to_dict() for segment in segmentation_result.segments])
        write_srt(output_dir / "final.srt", segmentation_result.segments)
        if script_origin == "temp_spoken":
            (output_dir / f"script_{language_code}_spoken.txt").write_text(spoken_text, encoding="utf-8")

        audio_info = NormalizedAudioInfo(
            path=output_dir / "normalized_audio.wav",
            duration_seconds=float(baseline["audio_duration"]),
            sample_rate=int(baseline["normalized_audio_properties"]["sample_rate"]),
            channels=int(baseline["normalized_audio_properties"]["channels"]),
        )
        candidate_metrics = [
            {
                "strategy": "segmentation_only_rebenchmark",
                "engine": str(baseline.get("engine", "mfa")),
                "chunk_count": int(baseline.get("chunk_count", 1)),
                "dropped_word_count": int(baseline["dropped_word_count"]),
                "approximate_word_count": int(baseline["approximate_word_count"]),
                "mismatch_count": int(baseline["mismatch_count"]),
                "reading_speed_warning_count": int(segmentation_result.diagnostics.segments_over_18_cps),
                "max_reading_cps": max((segment.reading_cps for segment in segmentation_result.segments), default=0.0),
                "p95_reading_cps": float(baseline.get("p95_reading_cps", 0.0)),
                "segments_over_24_cps": int(segmentation_result.diagnostics.segments_over_24_cps),
                "segments_over_30_cps": int(segmentation_result.diagnostics.segments_over_30_cps),
            }
        ]
        report = build_alignment_report(
            engine=str(baseline.get("engine", "mfa")),
            fallback_used=bool(baseline.get("fallback_used", False)),
            fallback_reason=baseline.get("fallback_reason"),
            audio_info=audio_info,
            script_document=script_document,
            words=words,
            segments=segmentation_result.segments,
            warnings=warnings,
            approximate_word_count=int(baseline["approximate_word_count"]),
            mismatch_count=int(baseline["mismatch_count"]),
            dropped_word_count=int(baseline["dropped_word_count"]),
            strategy="segmentation_only_rebenchmark",
            chunk_count=int(baseline.get("chunk_count", 1)),
            max_reading_cps_target=segmentation_config.max_reading_cps,
            mapping_diagnostics={
                key: int(value)
                for key, value in dict(baseline.get("warning_summary", {})).items()
                if key
                in {
                    "mismatch_blocks",
                    "isolated_mismatch_blocks",
                    "clustered_mismatch_blocks",
                    "merge_rescues",
                    "split_rescues",
                    "fuzzy_rescues",
                    "approximate_blocks",
                }
            },
            segmentation_diagnostics=segmentation_result.diagnostics,
            candidate_metrics=candidate_metrics,
        )
        write_json(output_dir / "alignment_report.json", report.to_dict())

        summary_entry = report.to_dict()
        summary_entry.update(
            {
                "language": language_code,
                "script_origin": script_origin,
                "output_dir": str(output_dir.relative_to(REPO_ROOT)),
            }
        )
        summary_payload.append(summary_entry)

    write_json(benchmark_root / "summary.json", summary_payload)
    return summary_payload


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild subtitle segmentation from existing words.json artifacts.")
    parser.add_argument("--episode-dir", required=True, help="Episode directory containing readable/spoken scripts.")
    parser.add_argument("--baseline-summary", required=True, help="Existing benchmark summary.json to reuse words.json paths from.")
    parser.add_argument("--output-root", required=True, help="Destination folder for the new non-destructive segmentation benchmark.")
    parser.add_argument("--languages", nargs="*", default=None, help="Optional language codes to benchmark.")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    run_segmentation_benchmark(
        episode_dir=Path(args.episode_dir),
        baseline_summary_path=Path(args.baseline_summary),
        output_root=Path(args.output_root),
        languages=list(args.languages) if args.languages else None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
