"""Creator Studio translation module."""

try:
    from .adapter import TranslationAdapter, TranslationError
    from .service import ChunkResult, TranslationResult, TranslationService
except ModuleNotFoundError as exc:
    if exc.name not in {"httpx", "openai"}:
        raise
    TranslationAdapter = None
    TranslationError = None
    ChunkResult = None
    TranslationResult = None
    TranslationService = None

from .chunker import TranslationChunk, build_scene_aware_chunks, build_text_chunks
from .prompts import build_translation_prompt

__all__ = [
    "TranslationAdapter",
    "TranslationError",
    "TranslationChunk",
    "TranslationResult",
    "TranslationService",
    "ChunkResult",
    "build_scene_aware_chunks",
    "build_text_chunks",
    "build_translation_prompt",
]
