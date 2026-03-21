# Tool 1 CLI-First Dashboard

Local browser dashboard for Tool 1 of the Creator Studio workflow.

It takes:
- narration audio
- script text

It produces:
- `final.srt`
- `timeline.json`
- `prompt_list.txt`

## What It Uses

- the existing alignment pipeline for subtitle timing
- repo-local prompt templates for scene planning and prompt generation
- `claude` CLI and `codex` CLI as supervised AI workers
- SQLite for board state and run history
- repo-local job folders under `workspace/videos/`

## Run It

```bash
python run_tool1_dashboard.py
```

On Windows you can also run:

```bat
Run Tool 1 Dashboard.bat
```

## Main Areas

- `tool1_dashboard/app.py`: FastAPI app
- `tool1_dashboard/service.py`: job orchestration and worker loop
- `tool1_dashboard/providers.py`: Codex/Claude CLI runner
- `tool1_dashboard/ui/index.html`: kanban dashboard UI
- `config/agents/`: editable stage templates
- `workspace/videos/`: job artifacts and exports

## Tests

```bash
python -m unittest discover -s tests -v
```
