# PROJECT REGISTRY — Tool 1: Multilingual Planning & Pre-Generation System

> **This file is the cross-conversation source of truth.** Every Claude session must read this first and update it before ending.

---

## Core Idea

Tool 1 is the **multilingual planning and pre-generation engine** of the Creator Studio project. It takes a single script and produces everything needed to generate videos in multiple languages:

- Translated scripts (per language)
- TTS narrations (per language)
- Subtitle alignment & SRT files (per language)
- Master scene planning (shared across languages)
- Localized timelines (per language, same scenes, different durations)
- Prompt lists for image/video asset generation (shared)

**The fundamental principle:** Scenes are defined ONCE by the master language. All other languages reuse the same scene structure but with timing adjusted to their narration duration.

**Consistency guide is per-episode** (not shared at niche project level).

## Two-Tool Architecture

- **Tool 1** (this project) — Planning & pre-generation: translation → TTS → alignment → scene planning → timeline → prompts
- **Tool 2** (separate) — Final video assembly: takes Tool 1 outputs + shared assets → produces final localized videos

## Current State (as of 2026-03-25)

### What Exists & Works
- **Dashboard app** (`tool1_dashboard/`) — FastAPI-based, Kanban-style pipeline UI
  - Unified backend with service layer + SQLite
  - Dark/light theme, responsive layout
  - Views: Pipeline Board, Niche Projects, Episodes, Voice Profiles, Translation Profiles, Settings, Templates
  - All legacy views and logic (Jobs, Projects/Builds) have been fully removed.
- **Translation module** (`tool1_dashboard/translation/`) — adapter, chunker, prompts, service
- **TTS module** (`tool1_dashboard/tts/`) — audio, chunker, constants, manager, worker (XTTS-v2)
- **Alignment tool** (`tool1_dashboard/alignment_tool/`)
- **SRT chunker** (`tool1_dashboard/srt_chunker/`)
- **Episode pipeline** — all 10 stages implemented in `_process_episode()` (service.py)
- **AI agent configs** (`config/agents/`) — scene planning, visual bible, video/image prompts (Claude + Codex)
- **Test suite** — 119 tests passing (api, chunking, cli_runner, pipeline, build, translation, tts, video_pipeline)

### What's Being Worked On
See `IMPLEMENTATION_PLAN.md` and `IMPLEMENTATION_CHECKLIST.md` for the 10-phase plan.

**Currently:** Phases 1-10 complete. The Tool 1 pipeline consolidation is finished.

### Git State
- Branch: `feat/cleanup-and-consolidation` (active)
- All code committed and pushed
- Remote: `https://github.com/alesalesmota/tool-1-srt-timeline-json-prompt-list.git`

## Architecture Decisions

| Decision | Why | Date |
|----------|-----|------|
| TTS only, no human audio upload | All narration generated via TTS for full automation | 2026-03-24 |
| One master language defines scene structure | Enables shared assets across all languages | 2026-03-24 |
| Duration mismatch MVP: images stretch, videos hold last frame | Keep MVP simple | 2026-03-24 |
| Pre-configured voice/translation profiles per Niche Project | Minimize per-video setup | 2026-03-24 |
| Sequential processing (no parallel TTS/translation) | Local machine constraints, GPU-bound TTS | 2026-03-24 |
| Consistency guide per-episode (not per-niche) | More flexibility per episode | 2026-03-25 |
| Remove all legacy code (Jobs, Projects/Builds) | Episodes is the final model, legacy is ~4000 lines of dead weight | 2026-03-25 |
| Archive outdated docs instead of deleting | Preserves reference material | 2026-03-25 |

## User Observations & Insights

- **2026-03-25**: Lost 10+ phase plan between conversations → created IMPLEMENTATION_PLAN.md + IMPLEMENTATION_CHECKLIST.md in repo + updated CLAUDE.md behavior to always persist plans
- **2026-03-25**: Standalone tools (TRADUTOR, TTS, SRT chunker, Whisper UI) all duplicated integrated modules → deleted
- **2026-03-24**: Niche Project hierarchy — each niche has pre-configured languages, voice profiles, translation profiles
- **2026-03-24**: Future: multiple Niche Projects (Religion, Sports, etc.)

## Phase Plan

**See `IMPLEMENTATION_PLAN.md` for full details and `IMPLEMENTATION_CHECKLIST.md` for progress.**

| Phase | Goal | Status |
|-------|------|--------|
| Pre-Implementation | Continuity setup (plan docs, checklist, CLAUDE.md) | DONE |
| Phase 1 | Cleanup & Git Hygiene | DONE |
| Phase 2 | Database Consolidation | DONE |
| Phase 3 | Service Layer — Remove Legacy | DONE |
| Phase 4 | API Layer Consolidation | DONE |
| Phase 5 | Frontend — Remove Legacy Views | DONE |
| Phase 6 | Episode Pipeline Board Enhancement | DONE |
| Phase 7 | Niche Project Detail Enhancement | DONE |
| Phase 8 | TTS & Translation Polish | DONE |
| Phase 9 | Review & Export Phase | DONE |
| Phase 10 | Final Cleanup & Documentation | DONE |

## Change Log

| Date | What Changed |
|------|-------------|
| 2026-03-25 | Phase 10 complete: Final Cleanup & Documentation — verified agent configs and dependencies, removed all remaining references to legacy architecture, all tests pass. Tool 1 is fully transitioned to the episode-first pipeline. |
| 2026-03-25 | Phase 9 complete: Review & Export Phase — timeline editor, consistency guide editor, prompt list editor, per-language timeline read-only view, and fully wired UI handlers for saving review data and finalizing/downloading export zip. |
| 2026-03-25 | Phase 8 complete: TTS & translation polish — per-language retry (translation/TTS), translation preview (side-by-side), TTS worker health indicator, TTS job progress in episode detail, retry buttons on failed languages. 87 tests passing. |
| 2026-03-25 | Phase 7 complete: niche project detail enhancement — stats bar, inline language config with voice/translation profile dropdowns, AI provider/model config, enhanced episode cards with per-language mini-dots, batch operations (queue drafts, re-run failed), added missing `/api/target-languages` endpoint. 87 tests passing. |
| 2026-03-25 | Phase 6 complete: enhanced Episode Pipeline Board with elapsed timers, progress bars, quick actions (Queue, Delete), output file previews, and expandable stage run details with stdout/error logs. Added `/api/episodes/{id}/files` endpoint. |
| 2026-03-25 | Phase 5 complete: removed ~2100 lines of legacy frontend code from app.js (3854→1717 lines). Removed legacy sidebar items, routing, render functions, state, event handlers. Default route changed to pipeline-board. 87 tests pass, 0 JS errors |
| 2026-03-25 | Phases 2-4 complete: removed all legacy code (jobs/projects/builds). database.py rewritten with niche_projects table, service.py reduced from 4500→1737 lines, app.py from 849→496 lines, 87 tests passing |
| 2026-03-25 | Phase 1 complete: deleted 4 standalone tools, archived outdated docs, committed all uncommitted code, 119 tests passing |
| 2026-03-25 | Reconstructed 10-phase implementation plan (saved to IMPLEMENTATION_PLAN.md) |
| 2026-03-25 | Created PROJECT_REGISTRY.md for cross-conversation continuity |
| Pre-2026-03-25 | Built multilingual episode pipeline, translation module, TTS module, UI |
