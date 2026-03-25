"""Creator Studio TTS module."""

from .audio import generate_silence_wav, merge_wav_chunks_streaming
from .chunker import TTSChunk, chunk_text_for_tts
from .constants import map_language_code
from .manager import TTSManager

__all__ = [
    "TTSChunk",
    "TTSManager",
    "chunk_text_for_tts",
    "generate_silence_wav",
    "map_language_code",
    "merge_wav_chunks_streaming",
]
