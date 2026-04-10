from __future__ import annotations

import re
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse

from .config import OUTPUT_ROOT, UI_DIR
from .models import ChunkConfig
from .runtime import project_dirs
from .service import create_chunk_run
from .srt_io import cues_to_srt

project_dirs()

app = FastAPI(title="Quebrador de SRT")
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(127\.0\.0\.1|localhost)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

INDEX_HTML = UI_DIR / "index.html"


def _safe_run_id(run_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", run_id):
        raise HTTPException(status_code=400, detail="Invalid run id.")
    return run_id


def _serialize_result(result) -> dict[str, object]:
    return {
        "run_id": result.run_id,
        "stats": {
            "total_chunks": len(result.chunks),
            "total_cues": result.total_cues,
            "total_words": result.total_words,
            "total_chars": result.total_chars,
            "total_duration_seconds": result.total_duration_seconds,
        },
        "config": result.config.to_dict(),
        "warnings": result.warnings,
        "downloads": {
            "manifest": f"/api/runs/{result.run_id}/manifest.json",
            "zip": f"/api/runs/{result.run_id}/chunks.zip",
            "original_srt": f"/api/runs/{result.run_id}/original.srt",
        },
        "chunks": [
            {
                **chunk.to_dict(),
                "srt_url": f"/api/runs/{result.run_id}/chunks/chunk-{chunk.chunk_id:03d}.srt",
                "text_url": f"/api/runs/{result.run_id}/chunks/chunk-{chunk.chunk_id:03d}.txt",
                "srt_preview": cues_to_srt(
                    chunk.cues,
                    restart_numbering=result.config.restart_numbering,
                ),
            }
            for chunk in result.chunks
        ],
        "preview": {
            "first_chunk_srt": result.first_chunk_preview,
        },
    }


@app.get("/", response_class=HTMLResponse)
async def root() -> str:
    if not INDEX_HTML.exists():
        raise HTTPException(status_code=500, detail="UI is missing.")
    return INDEX_HTML.read_text(encoding="utf-8")


@app.get("/api/health")
async def health() -> dict[str, object]:
    return {
        "ok": True,
        "app": "Quebrador de SRT",
        "output_root": str(OUTPUT_ROOT),
    }


@app.post("/api/chunk")
async def chunk_srt(
    srt_file: UploadFile = File(...),
    max_words: int = Form(800),
    max_chars: int = Form(5000),
    max_entries: int = Form(120),
    max_duration_seconds: int = Form(300),
    restart_numbering: str = Form("true"),
) -> dict[str, object]:
    filename = srt_file.filename or "subtitles.srt"
    if not filename.lower().endswith(".srt"):
        raise HTTPException(status_code=400, detail="Upload an .srt file.")

    payload = await srt_file.read()
    if not payload:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    restart = restart_numbering.strip().lower() not in {"false", "0", "no", "off"}
    config = ChunkConfig(
        max_words=max(0, max_words),
        max_chars=max(0, max_chars),
        max_entries=max(0, max_entries),
        max_duration_seconds=max(0, max_duration_seconds),
        restart_numbering=restart,
    )

    try:
        result = create_chunk_run(
            original_filename=filename,
            srt_text=payload.decode("utf-8-sig", errors="replace"),
            config=config,
            output_root=OUTPUT_ROOT,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return _serialize_result(result)


@app.get("/api/runs/{run_id}/{file_path:path}")
async def download_artifact(run_id: str, file_path: str) -> FileResponse:
    run_id = _safe_run_id(run_id)
    run_root = (OUTPUT_ROOT / run_id).resolve()
    candidate = (run_root / file_path).resolve()
    if run_root != candidate and run_root not in candidate.parents:
        raise HTTPException(status_code=400, detail="Invalid artifact path.")
    if not candidate.exists() or candidate.is_dir():
        raise HTTPException(status_code=404, detail="Artifact not found.")
    return FileResponse(str(candidate), filename=Path(file_path).name)
