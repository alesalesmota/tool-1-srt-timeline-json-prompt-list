from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_ROOT = PROJECT_ROOT / "output"
UI_DIR = PROJECT_ROOT / "srt_chunker" / "ui"

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = int(os.environ.get("SRT_CHUNKER_PORT", "8020"))
