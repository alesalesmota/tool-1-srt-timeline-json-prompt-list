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

If the TTS runtime is missing, the Voice Profiles page now shows the worker as unavailable and voice tests fail fast with the exact startup error instead of sitting in the queue forever. Voice profiles are language-agnostic, and `Play test` now generates a fresh default sample on demand without asking for manual test text.

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
python -m unittest discover -s tests -v
```

Current baseline: `101` passing tests.
