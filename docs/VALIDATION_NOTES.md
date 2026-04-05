# Validation Notes

## Automated Checks

- `python -m compileall tool1_dashboard run_tool1_dashboard.py`
- `python -m pytest tests -q`

## Current Result

- backend modules compile
- API smoke checks pass
- pytest suite passes (`208` passing tests, `4` passing subtests as of 2026-04-04)

## Remaining Manual Validation

- run the dashboard against real `codex` CLI outputs
- run the dashboard against real `claude` CLI outputs
- run one full audio + script job with local MFA/WhisperX available
