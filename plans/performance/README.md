# Performance Optimization Plans

> Start here: `PERFORMANCE_CHECKLIST.md` — master tracker for all findings
> Completed plan files live in `../completed/`

## Documents

| File | What | Status |
|------|------|--------|
| `PERFORMANCE_AUDIT.md` | Full audit — 17 findings with root cause, impact, line refs | Reference |
| `PERFORMANCE_CHECKLIST.md` | Master checklist tracking all findings | Active |
| `../completed/ASSEMBLY_LIGHTWEIGH_PLAN.md` | Assembly upload pagination + chunked upload + incremental DOM | Done |
| `../completed/PERF_PLAN_HIGH_FINDINGS.md` | F4 elapsed timer + F5 SSE log growth + F6 SSE leaks + B2 ffprobe caching | Done |
| `../completed/PERF_PLAN_MEDIUM_FINDINGS.md` | F7 assembly cache LRU + B3 worker idle backoff + B4 parallel translation | Done |
| `../completed/PERF_PLAN_SMART_REFRESH.md` | Smart polling: fingerprint guard + route-aware fetch + slower intervals | Done |
| `../completed/PERF_PLAN_VIDEO_ENCODING.md` | Eliminate redundant re-encoding: stream-copy concat + fast presets | Done |

## Priority Order

1. ~~Assembly upload~~ (done, moved to `../completed/`)
2. ~~Smart Refresh~~ (done, moved to `../completed/`)
3. ~~Video Re-encoding~~ (done, moved to `../completed/`)
4. ~~HIGH + B2/F6 batch~~ (done, moved to `../completed/`)
5. ~~MEDIUM batch~~ (done, moved to `../completed/PERF_PLAN_MEDIUM_FINDINGS.md`)
6. Remaining LOW — plans to be designed as needed (F8, F9, B8); B5/B6/B7 have no actionable quick wins
