from __future__ import annotations

import json
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

from .config import OUTPUT_ROOT
from .chunking import chunk_cues
from .models import ChunkConfig, ChunkRunResult, SubtitleChunk
from .runtime import ensure_dir, make_run_id
from .srt_io import chunk_to_text, cues_to_srt, parse_srt_text


def _manifest_dict(
    run_id: str,
    original_filename: str,
    config: ChunkConfig,
    chunks: list[SubtitleChunk],
    warnings: list[str],
) -> dict[str, object]:
    return {
        "run_id": run_id,
        "original_filename": original_filename,
        "config": config.to_dict(),
        "total_chunks": len(chunks),
        "total_cues": sum(chunk.cue_count for chunk in chunks),
        "total_words": sum(chunk.word_count for chunk in chunks),
        "total_chars": sum(chunk.char_count for chunk in chunks),
        "total_duration_seconds": round(sum(chunk.duration_seconds for chunk in chunks), 3),
        "warnings": warnings,
        "chunks": [
            {
                **chunk.to_dict(),
                "srt_file": f"chunks/chunk-{chunk.chunk_id:03d}.srt",
                "text_file": f"chunks/chunk-{chunk.chunk_id:03d}.txt",
            }
            for chunk in chunks
        ],
    }


def create_chunk_run(
    original_filename: str,
    srt_text: str,
    config: ChunkConfig,
    output_root: Path | None = None,
) -> ChunkRunResult:
    output_root = output_root or OUTPUT_ROOT
    cues = parse_srt_text(srt_text)
    chunks, warnings = chunk_cues(cues, config)

    run_id = make_run_id(Path(original_filename).stem)
    run_dir = ensure_dir(output_root / run_id)
    chunks_dir = ensure_dir(run_dir / "chunks")

    original_srt_path = run_dir / "original.srt"
    original_srt_path.write_text(srt_text.replace("\r\n", "\n"), encoding="utf-8")

    for chunk in chunks:
        srt_path = chunks_dir / f"chunk-{chunk.chunk_id:03d}.srt"
        text_path = chunks_dir / f"chunk-{chunk.chunk_id:03d}.txt"
        srt_path.write_text(
            cues_to_srt(chunk.cues, restart_numbering=config.restart_numbering),
            encoding="utf-8",
        )
        text_path.write_text(chunk_to_text(chunk.cues), encoding="utf-8")

    manifest = _manifest_dict(run_id, original_filename, config, chunks, warnings)
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    zip_path = run_dir / "chunks.zip"
    with ZipFile(zip_path, "w", compression=ZIP_DEFLATED) as archive:
        archive.write(original_srt_path, arcname="original.srt")
        archive.write(manifest_path, arcname="manifest.json")
        for chunk in chunks:
            archive.write(
                chunks_dir / f"chunk-{chunk.chunk_id:03d}.srt",
                arcname=f"chunks/chunk-{chunk.chunk_id:03d}.srt",
            )
            archive.write(
                chunks_dir / f"chunk-{chunk.chunk_id:03d}.txt",
                arcname=f"chunks/chunk-{chunk.chunk_id:03d}.txt",
            )

    first_chunk_preview = ""
    if chunks:
        first_chunk_preview = (
            chunks_dir / f"chunk-{chunks[0].chunk_id:03d}.srt"
        ).read_text(encoding="utf-8")

    return ChunkRunResult(
        run_id=run_id,
        output_dir=run_dir,
        original_filename=original_filename,
        config=config,
        total_cues=len(cues),
        total_words=sum(cue.word_count for cue in cues),
        total_chars=sum(cue.char_count for cue in cues),
        total_duration_seconds=round((cues[-1].end_ms - cues[0].start_ms) / 1000.0, 3),
        chunks=chunks,
        warnings=warnings,
        manifest_path=manifest_path,
        zip_path=zip_path,
        first_chunk_preview=first_chunk_preview,
    )
