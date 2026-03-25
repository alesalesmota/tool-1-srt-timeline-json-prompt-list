"""Audio utilities for TTS: WAV merge and silence generation.

Uses only the standard-library ``wave`` module — no torch dependency.
"""

from __future__ import annotations

import struct
import wave
from pathlib import Path

from .constants import AUDIO_SAMPLE_RATE, SILENCE_GAP_SECONDS, WAV_MERGE_READ_FRAMES


def generate_silence_wav(
    path: str | Path,
    duration_seconds: float = SILENCE_GAP_SECONDS,
    sample_rate: int = AUDIO_SAMPLE_RATE,
) -> None:
    """Write a mono 16-bit PCM silence WAV file to *path*."""
    num_samples = int(sample_rate * duration_seconds)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{num_samples}h", *([0] * num_samples)))


def merge_wav_chunks_streaming(
    chunk_paths: list[str | Path],
    final_path: str | Path,
) -> None:
    """Stream-merge a list of WAV chunks into a single output file.

    All chunks must share the same channel count, sample width, and frame rate.
    """
    if not chunk_paths:
        # Write a minimal valid WAV with a single silent sample.
        generate_silence_wav(str(final_path), duration_seconds=0.001)
        return

    first = str(chunk_paths[0])
    with wave.open(first, "rb") as ref:
        n_channels = ref.getnchannels()
        sample_width = ref.getsampwidth()
        frame_rate = ref.getframerate()
        comp_type = ref.getcomptype()
        comp_name = ref.getcompname()

    with wave.open(str(final_path), "wb") as out:
        out.setnchannels(n_channels)
        out.setsampwidth(sample_width)
        out.setframerate(frame_rate)
        out.setcomptype(comp_type, comp_name)

        for chunk_path in chunk_paths:
            with wave.open(str(chunk_path), "rb") as chunk:
                if (
                    chunk.getnchannels() != n_channels
                    or chunk.getsampwidth() != sample_width
                    or chunk.getframerate() != frame_rate
                    or chunk.getcomptype() != comp_type
                ):
                    raise ValueError(
                        f"Incompatible WAV chunk while merging: {chunk_path}"
                    )
                while True:
                    frames = chunk.readframes(WAV_MERGE_READ_FRAMES)
                    if not frames:
                        break
                    out.writeframesraw(frames)

        # Flush final frame count.
        out.writeframes(b"")
