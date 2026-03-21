# Validation Notes

## Automated Checks

- `python -m compileall tool1_dashboard run_tool1_dashboard.py`
- `python -m unittest discover -s tests -v`

## Current Result

- backend modules compile
- API smoke checks pass
- unit and integration tests pass

## Remaining Manual Validation

- run the dashboard against real `codex` CLI outputs
- run the dashboard against real `claude` CLI outputs
- run one full audio + script job with local MFA/WhisperX available
