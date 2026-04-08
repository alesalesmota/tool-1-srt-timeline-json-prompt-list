# Side Files Archive

This project uses an official sibling archive workspace for historical runtime artifacts that no longer need to stay inside the repo path.

## Archive Root

`C:\Users\Blue_\Desktop\PROJETOS\CREATOR STUDIO\TOOL 1 - SIDE FILES ARCHIVE`

## Purpose

The archive exists to keep the repo focused on:

- current source code
- current tests and config
- the live local database
- active current work only

The archive is for older runtime history, including things like:

- old alignment temp runs
- benchmark outputs
- old Playwright captures and logs
- older generated TTS outputs that are no longer part of active work
- finished episode workspaces that were intentionally moved out of the repo

## Path Rules

Archived items preserve their original repo-relative paths under:

`from-repo\...`

Example:

- repo path:
  - `workspace\benchmarks\alignment-20260404`
- archive path:
  - `C:\Users\Blue_\Desktop\PROJETOS\CREATOR STUDIO\TOOL 1 - SIDE FILES ARCHIVE\from-repo\workspace\benchmarks\alignment-20260404`

## Important Runtime Rule

The dashboard does not read from the sibling archive automatically.

If something was archived, it is no longer part of the app's live runtime working set unless a human or agent brings it back intentionally.

## Agent Guidance

- Prefer the repo first for normal engineering work.
- Check the sibling archive only when you need historical context or files that were intentionally moved out of the repo.
- Use the manifests in the archive root to see what was moved, when, and where it came from.
