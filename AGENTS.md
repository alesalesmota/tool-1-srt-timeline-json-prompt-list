# AGENTS

Project instructions for AI coding agents.

<!-- DRAWBRIDGE:START -->
## Drawbridge

This project is connected to Drawbridge for browser-to-code UI annotations.

Treat `bridge`, `drawbridge`, `step bridge`, `batch bridge`, and `yolo bridge` as action commands to process Drawbridge tasks instead of asking what the user means.

When a Drawbridge command is requested:

1. Read `.moat/moat-tasks-detail.json` first, then `.moat/moat-tasks.md`.
2. Resolve screenshot paths from `.moat/screenshots/`.
3. For each task, update the status in order: `to do` -> `doing` -> `done` or `failed`.
4. Update `.moat/moat-tasks.md` so the checkbox state matches the JSON status.
5. Default to step mode unless the user explicitly requests batch or yolo.
6. Preserve existing project patterns, design tokens, and accessibility expectations.

If `.moat/` or the task files are missing, tell the user to reconnect Drawbridge from the browser and try again.
<!-- DRAWBRIDGE:END -->

## Side Files Archive

Historical runtime artifacts may be moved out of the repo into the official sibling archive workspace:

- `C:\Users\Blue_\Desktop\PROJETOS\CREATOR STUDIO\TOOL 1 - SIDE FILES ARCHIVE`

Rules:

1. Prefer the repo first for normal coding, debugging, and product work.
2. Check the sibling archive only when you need historical outputs, old logs, old benchmark runs, or episode folders that were intentionally moved out of the repo.
3. Inside the sibling archive, archived items preserve their original repo-relative paths under `from-repo\...`.
4. The app does not read from the sibling archive automatically. Treat it as a human/agent reference workspace, not as a live runtime dependency.
