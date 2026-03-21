# Quebrador de SRT

Local web app for taking one `.srt` file and breaking it into chunk files.

## What it does

The app:

- uploads one `.srt`
- keeps subtitle cues intact
- groups cues into chunks based on your limits
- exports:
  - chunked `.srt` files
  - chunked `.txt` files
  - `manifest.json`
  - `chunks.zip`

Each run is saved under `output/<run-id>/`.

## Chunk rules

You can split by any combination of:

- max words per chunk
- max subtitle blocks per chunk
- max characters per chunk
- max duration in seconds per chunk

Setting a rule to `0` disables that rule.

## What is included

- `srt_chunker/` - parser, chunking logic, API, and browser UI
- `run_srt_chunker.py` - starts the local web app
- `Run Quebrador de SRT.bat` - simple Windows launcher
- `Install Quebrador de SRT.bat` - basic local setup
- `tests/` - tests for parsing, chunking, and API behavior

## Quick start

1. Run `Install Quebrador de SRT.bat`
2. Run `Run Quebrador de SRT.bat`
3. Upload an `.srt`
4. Choose your chunk limits
5. Download the zip or individual chunk files
