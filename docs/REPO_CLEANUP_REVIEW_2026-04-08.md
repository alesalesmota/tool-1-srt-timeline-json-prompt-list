# Repo Cleanup Review (2026-04-08)

This document is a review only.

Nothing was deleted or moved.

The goal was to find files and folders that can likely be archived outside the repo later, so the project folder stays cleaner and AI agents have less noise to scan.

## Important First Conclusion

The main translation and TTS source code is still in use.

These are **not** old unused reference tools:

- `tool1_dashboard/translation/`
- `tool1_dashboard/tts/`
- `tool1_dashboard/alignment_tool/`
- `tool1_dashboard/video_assembly/`
- `tool1_dashboard/srt_chunker/`

Why I believe they are still active:

- The main app service imports them directly.
- The test suite covers them directly.
- The README describes them as current parts of the app.

So the biggest cleanup opportunity is **not** deleting integrated source code.
The biggest opportunity is moving generated data, logs, benchmark runs, screenshots, old episode workspaces, and historical planning files out of the project folder.

## Important Note About Tokens

`.gitignore` helps Git, but it does **not** fully solve the “AI sees too many files” problem.

If a large folder still sits inside the repo directory, agents can still notice it while exploring the project.

So even ignored folders like `workspace/` can still create clutter for local AI work if they stay inside the repo path.

## How I Reviewed The Repo

I checked:

- the largest folders and files by size
- which modules are imported by the live app
- which modules are covered by tests
- which folders are already ignored by Git
- which folders look like generated outputs instead of source code

## Top-Level Size Snapshot

Approximate sizes on 2026-04-08:

| Path | Approx size | What it is | First recommendation |
|---|---:|---|---|
| `workspace/` | 8384 MB | local working data, episode outputs, benchmarks, TTS audio, DB | review and archive parts of it outside repo |
| `tool1_dashboard/` | 3771 MB | main app code plus alignment temp/output folders | keep code, archive generated alignment folders |
| `.playwright-cli/` | 71 MB | browser automation logs and captures | archive outside repo |
| `output/` | 26 MB | Playwright screenshots and logs | archive outside repo |
| `tests/` | 1.5 MB | automated tests | keep |
| `plans/` | 0.12 MB | planning docs | keep active ones, review completed ones |
| `archive/` | 0.16 MB | historical docs | review for external archive |
| `PROJECT_REGISTRY.md` | 0.11 MB | main continuity file | keep, but consider splitting history later |

## Best Archive Candidates

These are the highest-value cleanup targets.

They are either clearly generated data, clearly historical material, or clearly local tool output.

### 1. `tool1_dashboard/alignment_tool/temp/`

- Approx size: `3672 MB`
- What it is:
  - temporary alignment runs
  - repeated audio-processing attempts
  - many large folders created during subtitle/audio alignment experiments
- Why it looks archivable:
  - this is already ignored in `.gitignore`
  - the folder name itself is `temp`
  - I found many repeated rerun folders for the same languages
- My recommendation:
  - very strong candidate to move outside the repo folder after review
- Risk:
  - low, if you do not need these old temporary alignment attempts inside the app anymore

Examples:

- `tool1_dashboard/alignment_tool/temp/20260403-150353-2e347e44-980c-479f-857e-a98932b2`
- `tool1_dashboard/alignment_tool/temp/20260404-215143-bff644b8-c1a2-46b7-ac72-f3f84376`
- `tool1_dashboard/alignment_tool/temp/20260404-235401-normalized-audio`

### 2. `workspace/benchmarks/`

- Approx size: `1389 MB`
- What it is:
  - benchmark runs used to compare subtitle/alignment quality
  - mostly diagnostic history, not app source
- Why it looks archivable:
  - current runtime does not read from this folder
  - it is mainly mentioned in `PROJECT_REGISTRY.md` as evidence/history
  - the folder names are clearly experiment snapshots
- My recommendation:
  - strong candidate to move outside repo into a dedicated benchmark archive
- Risk:
  - low

Examples:

- `workspace/benchmarks/alignment-20260404`
- `workspace/benchmarks/alignment-20260404-density`
- `workspace/benchmarks/alignment-20260404-density-v2`
- `workspace/benchmarks/alignment-20260405-fr-it-cleanup-all`

### 3. `workspace/tts/output/`

- Approx size: `2862 MB`
- What it is:
  - generated narration WAV files
  - voice-test WAV files
  - TTS job output history
- Why it looks archivable:
  - this is output, not source code
  - the app already treats it like retained output, not permanent source
  - many files are very large completed narrations from older jobs
- My recommendation:
  - archive older finished outputs outside repo
  - keep only the newest ones you still need for active work
- Risk:
  - medium
  - if a currently active episode still depends on a file here, moving it too early could be annoying

Large examples:

- `workspace/tts/output/2e347e44-980c-479f-857e-a98932b2e87b_narration_es_narration.wav` — `184.57 MB`
- `workspace/tts/output/bff644b8-c1a2-46b7-ac72-f3f843762a3d_narration_de_narration.wav` — `182.41 MB`
- `workspace/tts/output/d596e093-85fa-4a91-8db9-5e0d7c4c369d_narration_fr_narration.wav` — `149.34 MB`

### 4. `workspace/videos/`

- Approx size: `241 MB`
- What it is:
  - older video-workspace structure
- Why it looks archivable:
  - the current app code uses `workspace/episodes/`, not `workspace/videos/`
  - I found no current code reference to `workspace/videos/`
  - this makes it look like legacy workspace history
- My recommendation:
  - strong candidate to archive outside repo
- Risk:
  - low

Main folder:

- `workspace/videos/20260322-160537-204`

### 5. `.playwright-cli/`

- Approx size: `70.94 MB`
- What it is:
  - browser automation logs
  - page snapshots
  - tool-generated diagnostics
- Why it looks archivable:
  - not app source
  - not user-facing product data
  - mostly tool history
- My recommendation:
  - move outside repo or clean periodically
- Risk:
  - very low

### 6. `output/playwright/`

- Approx size: `25.79 MB`
- What it is:
  - screenshots and logs from browser smoke tests
- Why it looks archivable:
  - review artifacts, not runtime app files
  - useful for history, but not needed for the app to run
- My recommendation:
  - archive outside repo after review
- Risk:
  - very low

Examples:

- `output/playwright/dashboard-smoke-launch.out.log` — `24.42 MB`
- `output/playwright/language-setup-open-before-wait.png`
- `output/playwright/assembly-smoke-before-load-more.png`

### 7. `tool1_dashboard/alignment_tool/output/real_diagnostic/`

- Approx size: `96.79 MB`
- What it is:
  - saved diagnostic output from a real alignment run
- Why it looks archivable:
  - this is output history, not source code
  - the specific `real_diagnostic` folder is not referenced by current runtime code
- My recommendation:
  - archive outside repo after review
- Risk:
  - low

### 8. Temporary root files

These are small, but they add noise:

| Path | What it is | Recommendation |
|---|---|---|
| `tmp_fix_app_js.py` | one-off helper script that edits `app.js` | archive outside repo or delete later after review |
| `2026-04-08-092308-this-session-is-being-continued-from-a-previous-c.txt` | raw session continuation transcript | archive outside repo |
| `test-results/.last-run.json` | Playwright/runner state marker | safe to move/delete later |
| `__pycache__/` | Python bytecode cache | safe to move/delete later |
| `.pytest_cache/` | pytest cache | safe to move/delete later |
| `.uv-cache/` | package/tool cache | safe to move/delete later |

## Good Candidates, But Review Carefully First

These folders are very likely real sources of clutter, but they may still hold current working data.

### 9. `workspace/episodes/`

- Approx size: `3884 MB`
- What it is:
  - the current project-based episode workspace
  - this is where the app stores per-episode outputs
- Why it is not an immediate “safe archive all” folder:
  - this is part of the live product workflow
  - active episodes may still rely on these files
- My recommendation:
  - do **not** move the whole folder blindly
  - instead review finished episode folders one by one

The big finding inside this folder:

- almost all the size is concentrated in one niche:
  - `workspace/episodes/niche-20260326-133703-religi-o` — `3884 MB`

And inside that niche, two episodes dominate:

- `ep-20260402-201657-205` — `2659 MB`
- `ep-20260328-230851-204` — `1225 MB`

### 10. Finished episode folders `204` and `205`

These are probably the best “manual review first” cleanup targets.

#### Episode `205`

- Path:
  - `workspace/episodes/niche-20260326-133703-religi-o/ep-20260402-201657-205`
- Approx size:
  - `2659 MB`
- Biggest parts:
  - `alignment/` — `1425 MB`
  - `assembly/shared_assets/` — `545 MB`
  - `export_ep-20260402-201657-205.zip` — `682 MB`
  - `runs/` — small

What this means in simple terms:

- this episode folder contains the final useful outputs
- but it also contains a lot of heavy process history and repeated attempts
- there is probably duplication between the final export zip and the internal working folders

#### Episode `204`

- Path:
  - `workspace/episodes/niche-20260326-133703-religi-o/ep-20260328-230851-204`
- Approx size:
  - `1225 MB`
- Biggest parts:
  - `alignment/` — `547 MB`
  - `export_ep-20260328-230851-204.zip` — `671 MB`

My recommendation for episodes `204` and `205`:

- if these episodes are considered finished, archive the whole episode folder outside repo
- if you still want fast local access in the app, keep them for now
- if you want a middle ground, archive only:
  - old `alignment/` reruns
  - old `runs/`
  - the export zip, if you already stored it elsewhere

### 11. Repeated alignment retries inside episode folders

Example:

- `workspace/episodes/niche-20260326-133703-religi-o/ep-20260402-201657-205/alignment/es`

This single language folder is `539 MB`, and it contains several reruns of the same work:

- `20260403-150353-2e347e44-980c-479f-857e-a98932b2`
- `20260403-204758-c2f1b125-2294-47b3-b4e1-ebd36803`
- `20260403-235454-b4dac263-0205-406a-a5e8-aed91d1b`
- `20260403-235506-b4dac263-0205-406a-a5e8-aed91d1b`
- `20260403-235709-b4dac263-0205-406a-a5e8-aed91d1b`

In simple terms:

- the folder is keeping several attempts of the same language step
- that is useful for debugging
- but it is not “clean project source code”

My recommendation:

- archive older reruns outside repo after you decide which run is the keeper

### 12. `workspace/episodes/*retry-project*`

- Count found: `34`
- Size impact: tiny
- Noise impact: real

What they are:

- many small throwaway niche project folders created during retry/recovery work

Why they matter:

- they do not use much disk
- but they increase file/folder count and make the workspace look messy

My recommendation:

- archive or remove later after review
- low urgency for disk space, moderate value for reducing folder noise

## Token-Heavy Files To Slim, Not Remove

These are not “unused”, but they likely matter for AI token usage.

### 13. `PROJECT_REGISTRY.md`

- Approx size: `110 KB`
- Why it matters:
  - it is the biggest high-value text file in the repo root
  - future agents are likely to read it first
- Why I would **not** archive it:
  - your project rules explicitly want this file as the main continuity source
- Better cleanup idea:
  - keep the root registry
  - move older changelog history into external archive notes later
  - leave only current state, current architecture, current priorities, and recent changes in the root file

### 14. `plans/completed/` and parts of `archive/`

These are not runtime code.

They are mostly historical guidance.

Good examples:

- `plans/completed/ASSEMBLY_LIGHTWEIGH_PLAN.md`
- `plans/completed/PERF_PLAN_SMART_REFRESH.md`
- `plans/completed/PERF_PLAN_VIDEO_ENCODING.md`
- `archive/context_cleanup_2026-04-04/...`

My recommendation:

- keep active planning docs in the repo
- consider moving completed plans and old archived context docs outside repo if `PROJECT_REGISTRY.md` already preserves the important outcome

Risk:

- low to medium
- mainly a context/history tradeoff, not a runtime risk

## Things I Would Keep In The Repo

I would keep these where they are:

| Path | Why keep it |
|---|---|
| `tool1_dashboard/translation/` | live translation engine used by app |
| `tool1_dashboard/tts/` | live TTS engine used by app |
| `tool1_dashboard/alignment_tool/` code files | live alignment engine used by app |
| `tool1_dashboard/video_assembly/` | live assembly/render engine used by app |
| `tool1_dashboard/srt_chunker/` | live chunking logic used by app |
| `config/agents/` | active prompt templates used by workflow stages |
| `tests/` | protection against breaking working features |
| `workspace/creator_studio.db` | live database for the current local app |
| `workspace/episodes/` active current work | live user/project data |
| `.moat/` | active Drawbridge task integration |

## Suggested Archive Order

If the goal is “clean repo with low risk”, I would do it in this order:

1. `tool1_dashboard/alignment_tool/temp/`
2. `workspace/benchmarks/`
3. `workspace/videos/`
4. `.playwright-cli/`
5. `output/playwright/`
6. `tool1_dashboard/alignment_tool/output/real_diagnostic/`
7. `tmp_fix_app_js.py` and the session transcript `.txt`
8. old files from `workspace/tts/output/`
9. old finished episode folders under `workspace/episodes/`
10. completed plans and old archive docs
11. later: slim `PROJECT_REGISTRY.md` by moving older history out

## Approximate Space Recovery

Approximate cleanup potential without touching active episode folders:

- `tool1_dashboard/alignment_tool/temp/` — `3672 MB`
- `workspace/benchmarks/` — `1389 MB`
- `workspace/tts/output/` — `2862 MB`
- `workspace/videos/` — `241 MB`
- `.playwright-cli/` — `71 MB`
- `output/playwright/` — `26 MB`
- `tool1_dashboard/alignment_tool/output/real_diagnostic/` — `97 MB`

Approximate total:

- about `8358 MB` (`~8.3 GB`)

If you later also archive the two big finished episode folders:

- `ep-20260402-201657-205` — `2659 MB`
- `ep-20260328-230851-204` — `1225 MB`

Extra possible reduction:

- about `3884 MB` (`~3.8 GB`)

## Suggested External Archive Structure

If you want this to stay organized, I would suggest a sibling archive folder outside the repo, something like:

```text
CREATOR_STUDIO_ARCHIVE/
  tool1-local-runs/
    alignment-temp/
    alignment-benchmarks/
    tts-output/
    finished-episodes/
    playwright/
    temp-scripts/
    session-transcripts/
  repo-history/
    completed-plans/
    old-archive-docs/
```

This keeps the repo itself focused on:

- source code
- tests
- active configs
- active working data only

## Final Summary

The main source code is not the problem.

The real clutter comes from:

- generated alignment temp data
- benchmark result folders
- generated TTS WAV files
- old episode workspaces
- Playwright logs/screenshots
- old helper scripts and raw session transcripts
- historical planning/context docs that are no longer operational

The highest-value cleanup is to archive **generated runtime history** outside the repo first.

That will reduce both disk clutter and the number of files sitting inside the project path, which is the part most likely to help AI agents stay more focused.
