# Tool 1 Creator Studio Dashboard

Tool 1 is the multilingual planning pipeline for Creator Studio. The dashboard is now centered on the project-scoped workflow:

`Niche Projects -> Project Kanban -> Draft Episode -> Episode Details Overlay -> Explicit Queue`

The app takes a script-only episode draft inside a Niche Project and produces the planning assets needed for downstream video generation:

- per-language translated scripts
- per-language TTS narration audio
- per-language `final.srt`
- per-language `timeline.json`
- prompt lists for asset generation
- export bundles for Tool 2 handoff

## Workflow

1. Open `#/niche-projects`
2. Open a project board
3. Create a Draft episode from the Draft column
4. Configure languages, voice profiles, translation profiles, provider, and model on the same project page
5. Queue the episode explicitly from the card or the episode overlay
6. If a provider stage fails, inspect the error in the overlay, fix the configuration, and requeue from the failed stage

`#/pipeline-board` is now a legacy route and redirects back into the project-scoped flow.

## What It Uses

- FastAPI for the local API and dashboard host
- SQLite for projects, episodes, stage runs, templates, and settings
- repo-local prompt templates in `config/agents/`
- `claude` CLI and `codex` CLI as supervised LLM workers
- integrated translation, TTS, alignment, chunking, planning, timeline, and export stages
- workspace artifact folders under `workspace/`

## Run It

```bash
python run_tool1_dashboard.py
```

On Windows you can also run:

```bat
Run Tool 1 Dashboard.bat
```

## TTS Runtime

Voice cloning and voice-test jobs require the XTTS runtime in the same Python environment as the dashboard.

- `torch`
- `torchaudio`
- `TTS` (Coqui XTTS)

The voice engine now auto-starts on demand for voice-profile prep/tests and pipeline TTS generation, then auto-stops after idle based on usage. If the XTTS runtime is missing or the engine cannot start, the UI shows a clear voice-engine warning and voice tests fail fast with the exact startup error instead of sitting in the queue forever.

For long-form narration, the dashboard environment should use the CUDA build of the pinned PyTorch runtime. CPU-only `torch` still works, but production `generate` jobs become much slower and the UI now warns explicitly when XTTS is running on CPU.

```bash
python -m pip install --upgrade --force-reinstall torch==2.3.1 torchaudio==2.3.1 --index-url https://download.pytorch.org/whl/cu121
```

Voice profiles are language-agnostic, `Play test` generates a fresh default sample on demand without asking for manual test text, and each profile now carries its own narration pacing config:

- presets: `natural_stable`, `balanced`, `expressive`
- advanced controls: `temperature`, `top_p`, `top_k`, `speed`, `chunk_max_chars`, `silence_gap_seconds`
- `Play test` keeps the saved per-profile chunk size so previews stay representative of the profile tuning
- production `generate` keeps the same tuning path but enforces a minimum `chunk_max_chars` of `260` to reduce XTTS calls on long scripts
- the repo TTS chunker is the authoritative narration split layer; XTTS internal text splitting is disabled
- worker health now exposes `device`, `torch_version`, `torch_build`, `cuda_available`, `gpu_name`, and active/queued `generate` counts so paused-TTS views can distinguish CPU slowness from a stuck queue

Windows note: installing `TTS` may also require Microsoft C++ Build Tools.

## Main Areas

- `tool1_dashboard/app.py`: FastAPI routes and API error shaping
- `tool1_dashboard/service.py`: episode orchestration, queue readiness, worker loop, stage-run logging
- `tool1_dashboard/templates.py`: template storage without read-side effects
- `tool1_dashboard/providers.py`: Codex/Claude CLI execution
- `tool1_dashboard/ui/index.html`: dashboard shell
- `tool1_dashboard/ui/app.js`: project-first Kanban UI and overlay workflow
- `tool1_dashboard/ui/app.css`: board, overlay, and readiness styles
- `tests/test_video_pipeline.py`: episode API and pipeline regression coverage

## Tests

```bash
python -m pytest tests -q
```

Current baseline: `208` passing tests and `4` passing subtests.
