# PROJECT REGISTRY — Tool 1: Multilingual Planning & Pre-Generation System

> **This file is the cross-conversation source of truth.** Every Claude session must read this first and update it before ending. It captures project state, decisions, user insights, and plans so nothing is lost between conversations.

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

## Two-Tool Architecture

- **Tool 1** (this project) — Planning & pre-generation: translation → TTS → alignment → scene planning → timeline → prompts
- **Tool 2** (separate) — Final video assembly: takes Tool 1 outputs + shared assets → produces final localized videos

## Current State (as of 2026-03-25)

### What Exists
- **Dashboard app** (`tool1_dashboard/`) — Kanban-style pipeline UI (Flask-based)
  - Unified backend with service layer
  - Dark/light theme, responsive layout
  - Views: dashboard, create, job workspace, settings, templates
- **Translation module** (`tool1_dashboard/translation/`) — adapter, chunker, prompts, service
- **TTS module** (`tool1_dashboard/tts/`) — audio, chunker, constants, manager, worker
- **Alignment tool** (`tool1_dashboard/alignment_tool/`)
- **SRT chunker** (`tool1_dashboard/srt_chunker/`)
- **AI agent configs** (`config/agents/`) — scene planning, image prompt gen, video prompt gen, visual bible (Claude + Codex variants)
- **Tests** — api, chunking/validation, cli_runner, pipeline, build_pipeline, translation, tts, video_pipeline
- **Local CLI worker layer** for running pipeline steps

### What's Working
- Kanban UI shell with stage-based workflow
- Backend service architecture
- Test suite passing (as of last known state)

### What Needs Work
- Runtime polish against real live CLI outputs
- Manual browser validation of UI (desktop + mobile)
- Packaging/install quality improvements
- **PRD may be outdated** — user noted `tool_1_multilingual_implementation.md` needs revisiting
- Integration of translation + TTS + alignment into the live pipeline flow

### Git State
- Single initial commit on `main`
- Many uncommitted changes across dashboard, tests, configs, translation, TTS modules
- Remote: `https://github.com/alesalesmota/tool-1-srt-timeline-json-prompt-list.git`

## Architecture Decisions

| Decision | Why | Date |
|----------|-----|------|
| TTS only, no human audio upload | All narration generated via TTS for full automation | 2026-03-24 |
| One master language defines scene structure | Enables shared assets across all languages | 2026-03-24 |
| Duration mismatch MVP: images stretch, videos hold last frame | Keep MVP simple, no slow-motion effects | 2026-03-24 |
| Pre-configured voice/translation profiles per Niche Project | Minimize per-video setup, submit script → get all languages | 2026-03-24 |
| Sequential processing (no parallel TTS/translation) | Local machine constraints, GPU-bound TTS, API rate limits | 2026-03-24 |
| CLI-first approach with kanban dashboard | Approved direction for Tool 1 | Pre-2026-03-25 |

## User Observations & Insights (Not Yet Implemented)

> These are things the user (Blue) said or observed but we haven't acted on yet. CRITICAL for future sessions.

- **2026-03-25**: "the PRD (`tool_1_multilingual_implementation.md`) is probably not updated — we need to reconversate about this"
- **2026-03-25**: A 10+ phase implementation plan was created in a previous conversation but was LOST because it wasn't saved to a file. This must never happen again.
- **2026-03-24**: Niche Project hierarchy — Niche Project (e.g., "Religion Channel") → Videos. Each niche has pre-configured languages, voice profiles, translation profiles.
- **2026-03-24**: Future vision includes multiple Niche Projects (Religion Channel, Sports Channel, etc.), each with their own language channels.

## Phase Plan

> **STATUS: NEEDS RECONSTRUCTION** — A 10+ phase plan was created in a previous conversation but lost. It needs to be rebuilt based on the PRD and current codebase state. This section will be updated when the plan is recreated.

## Change Log

| Date | What Changed |
|------|-------------|
| 2026-03-25 | Created PROJECT_REGISTRY.md for cross-conversation continuity |
| 2026-03-25 | Set up global CLAUDE.md with auto-versioning and doc management behaviors |
| Pre-2026-03-25 | Initial commit with dashboard, translation, TTS, alignment, tests |
| Pre-2026-03-25 | UI shell redesign: dashboard, create, job workspace, settings, templates |
| Pre-2026-03-25 | Local CLI worker layer created |

## Future Improvements & Ideas

- Reconstruct the 10+ phase implementation plan
- Revisit and update the PRD
- Manual smoke testing of the full pipeline
- Package/install quality improvements
- Multiple Niche Project support
- Full automation: submit script → all language versions out
