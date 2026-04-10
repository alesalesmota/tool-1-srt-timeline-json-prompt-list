# Translation Step Simplification Checklist
> Track progress for the 2026-04-10 translation simplification effort.

## Documentation

- [x] `[Codex]` Create `plans/translation-step-simplification/IMPLEMENTATION_PLAN.md`
- [x] `[Codex]` Create `plans/translation-step-simplification/IMPLEMENTATION_CHECKLIST.md`
- [x] `[Codex]` Create `plans/translation-step-simplification/AGENT_EXECUTION_GUIDE.md`
- [x] `[Codex]` Add root pointers in `IMPLEMENTATION_PLAN.md` and `IMPLEMENTATION_CHECKLIST.md`
- [x] `[Codex]` Update `PROJECT_REGISTRY.md` with the new translation decision and session notes

## Backend Runtime

- [x] `[Codex]` Add backend feature flag for optional AI review, default disabled
- [x] `[Codex]` Disable reviewer calls in the default translation runtime path
- [x] `[Codex]` Remove automatic AI repair from the default translation runtime path
- [x] `[Codex]` Preserve readable-script and spoken-script outputs
- [x] `[Codex]` Preserve retry-from-translation compatibility

## Deterministic Gate

- [x] `[Codex]` Keep existing pipeline-breaking translation blockers
- [x] `[Codex]` Add digits-present blocker
- [x] `[Codex]` Add gibberish-output blocker
- [x] `[Codex]` Add categorized diagnostics and next-action hints
- [x] `[Codex]` Add offending excerpts when available

## Prompt and Diagnostics

- [x] `[Codex]` Update translation prompt with spelled-out-number rule
- [x] `[Codex]` Extend `translation_log_<lang>.json` fields
- [x] `[Codex]` Add `translation_diagnostics_<lang>.json`
- [x] `[Codex]` Extend translation preview API payload

## Frontend

- [x] `[Gemini]` Improve per-language translation status display in episode detail/overlay
- [x] `[Gemini]` Add short error badges for translation failures
- [x] `[Gemini]` Expand preview modal with diagnostics, excerpts, hints, and provider/model metadata

## Verification

- [ ] `[Jules]` Smoke-test quota/auth/rate-limit/timeout failure visibility
- [ ] `[Jules]` Smoke-test content blockers: source leak, CTA leak, channel leak, digits, gibberish
- [ ] `[Jules]` Confirm each error clearly points to provider/config, prompt/profile, or code/system action
- [x] `[Codex]` Update automated translation tests for deterministic gating
- [x] `[Codex]` Run targeted backend/frontend verification

## Closeout

- [x] `[Codex]` Update final session notes in `PROJECT_REGISTRY.md`
- [x] `[Codex]` Commit and push documentation unit
- [ ] `[Codex]` Commit and push backend/runtime unit
- [ ] `[Codex]` Commit and push frontend/tests unit
