# Performance Optimization Plans

> Start here: `PERFORMANCE_CHECKLIST.md` — master tracker for all findings
> Completed plan files live in `../completed/`

## Documents

| File | What | Status |
|------|------|--------|
| `PERFORMANCE_AUDIT.md` | Full audit — 17 findings with root cause, impact, line refs | Reference |
| `PERFORMANCE_CHECKLIST.md` | Master checklist tracking all findings | Active |
| `../completed/ASSEMBLY_LIGHTWEIGH_PLAN.md` | Assembly upload pagination + chunked upload + incremental DOM | Done |
| `../completed/PERF_PLAN_SMART_REFRESH.md` | Smart polling: fingerprint guard + route-aware fetch + slower intervals | Done |
| `PERF_PLAN_VIDEO_ENCODING.md` | Eliminate redundant re-encoding: stream-copy concat + fast presets | Ready |

## Priority Order

1. ~~Assembly upload~~ (done, moved to `../completed/`)
2. ~~Smart Refresh~~ (done, moved to `../completed/`)
3. **Video Re-encoding** (B1) — CRITICAL, backend only, 4 small file edits
4. Remaining HIGH/MEDIUM/LOW — plans to be designed as needed
