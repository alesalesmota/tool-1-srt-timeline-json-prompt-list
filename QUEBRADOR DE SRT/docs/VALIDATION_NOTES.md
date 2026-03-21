# Validation Notes

Date: `2026-03-20`

## Intended app

- Input: one `.srt` file
- Output: chunked `.srt` files, chunked `.txt` files, `manifest.json`, and `chunks.zip`

## What will be verified

- SRT parsing
- Chunk splitting rules
- API upload and result shape

## Verified

- `python -m unittest discover -s tests -v`
  - Result: `8 tests passed`
- Direct service run with a sample SRT
  - Result: generated `2` chunks
  - Result: `chunks.zip` was written successfully

## Current behavior

- Upload one `.srt`
- Split by configurable limits:
  - words
  - subtitle blocks
  - characters
  - duration
- Export:
  - `chunks/chunk-001.srt`
  - `chunks/chunk-001.txt`
  - `manifest.json`
  - `chunks.zip`
