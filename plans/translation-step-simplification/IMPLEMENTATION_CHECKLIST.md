# Translation Step Simplification Checklist
> Track progress for the 2026-04-10 translation simplification effort.

## Documentation

- [x] `[Codex]` Create `plans/translation-step-simplification/IMPLEMENTATION_PLAN.md`
- [x] `[Codex]` Create `plans/translation-step-simplification/IMPLEMENTATION_CHECKLIST.md`
- [x] `[Codex]` Create `plans/translation-step-simplification/AGENT_EXECUTION_GUIDE.md`
- [x] `[Codex]` Add root pointers in `IMPLEMENTATION_PLAN.md` and `IMPLEMENTATION_CHECKLIST.md`
- [x] `[Codex]` Update `PROJECT_REGISTRY.md` with the new translation decision and session notes

## Backend Runtime

- [ ] `[Codex]` Add backend feature flag for optional AI review, default disabled
- [ ] `[Codex]` Disable reviewer calls in the default translation runtime path
- [ ] `[Codex]` Remove automatic AI repair from the default translation runtime path
- [ ] `[Codex]` Preserve readable-script and spoken-script outputs
- [ ] `[Codex]` Preserve retry-from-translation compatibility

## Deterministic Gate

- [ ] `[Codex]` Keep existing pipeline-breaking translation blockers
- [ ] `[Codex]` Add digits-present blocker
- [ ] `[Codex]` Add gibberish-output blocker
- [ ] `[Codex]` Add categorized diagnostics and next-action hints
- [ ] `[Codex]` Add offending excerpts when available

## Prompt and Diagnostics

- [ ] `[Codex]` Update translation prompt with spelled-out-number rule
- [ ] `[Codex]` Extend `translation_log_<lang>.json` fields
- [ ] `[Codex]` Add `translation_diagnostics_<lang>.json`
- [ ] `[Codex]` Extend translation preview API payload

## Frontend

- [ ] `[Gemini]` Improve per-language translation status display in episode detail/overlay
- [ ] `[Gemini]` Add short error badges for translation failures
- [ ] `[Gemini]` Expand preview modal with diagnostics, excerpts, hints, and provider/model metadata

## Verification

- [ ] `[Jules]` Smoke-test quota/auth/rate-limit/timeout failure visibility
- [ ] `[Jules]` Smoke-test content blockers: source leak, CTA leak, channel leak, digits, gibberish
- [ ] `[Jules]` Confirm each error clearly points to provider/config, prompt/profile, or code/system action
- [ ] `[Codex]` Update automated translation tests for deterministic gating
- [ ] `[Codex]` Run targeted backend/frontend verification

## Closeout

- [ ] `[Codex]` Update final session notes in `PROJECT_REGISTRY.md`
- [ ] `[Codex]` Commit and push documentation unit
- [ ] `[Codex]` Commit and push backend/runtime unit
- [ ] `[Codex]` Commit and push frontend/tests unit
