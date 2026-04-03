"""Per-voice XTTS tuning presets and normalization helpers."""

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

DEFAULT_VOICE_TTS_PRESET = "natural_stable"

VOICE_TTS_PRESETS: dict[str, dict[str, float | int]] = {
    "natural_stable": {
        "temperature": 0.55,
        "top_p": 0.75,
        "top_k": 30,
        "speed": 1.0,
        "chunk_max_chars": 180,
        "silence_gap_seconds": 0.12,
    },
    "balanced": {
        "temperature": 0.65,
        "top_p": 0.82,
        "top_k": 40,
        "speed": 1.0,
        "chunk_max_chars": 200,
        "silence_gap_seconds": 0.15,
    },
    "expressive": {
        "temperature": 0.75,
        "top_p": 0.88,
        "top_k": 50,
        "speed": 1.0,
        "chunk_max_chars": 220,
        "silence_gap_seconds": 0.15,
    },
}

VOICE_TTS_LIMITS: dict[str, dict[str, float | int]] = {
    "temperature": {"min": 0.35, "max": 0.85},
    "top_p": {"min": 0.60, "max": 0.95},
    "top_k": {"min": 10, "max": 80},
    "speed": {"min": 0.96, "max": 1.05},
    "chunk_max_chars": {"min": 120, "max": 250},
    "silence_gap_seconds": {"min": 0.00, "max": 0.25},
}


def _coerce_float(
    value: Any,
    *,
    default: float,
    minimum: float,
    maximum: float,
) -> float:
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        resolved = default
    return max(minimum, min(maximum, resolved))


def _coerce_int(
    value: Any,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    try:
        resolved = int(float(value))
    except (TypeError, ValueError):
        resolved = default
    return max(minimum, min(maximum, resolved))


def normalize_voice_tts_config(raw_config: Any = None) -> dict[str, Any]:
    parsed = raw_config
    if isinstance(parsed, str):
        try:
            parsed = json.loads(parsed) if parsed.strip() else {}
        except json.JSONDecodeError:
            parsed = {}
    if not isinstance(parsed, dict):
        parsed = {}

    preset = str(parsed.get("preset") or DEFAULT_VOICE_TTS_PRESET).strip().lower()
    if preset not in VOICE_TTS_PRESETS:
        preset = DEFAULT_VOICE_TTS_PRESET

    resolved = deepcopy(VOICE_TTS_PRESETS[preset])
    resolved["preset"] = preset
    resolved["temperature"] = _coerce_float(
        parsed.get("temperature"),
        default=float(resolved["temperature"]),
        minimum=float(VOICE_TTS_LIMITS["temperature"]["min"]),
        maximum=float(VOICE_TTS_LIMITS["temperature"]["max"]),
    )
    resolved["top_p"] = _coerce_float(
        parsed.get("top_p"),
        default=float(resolved["top_p"]),
        minimum=float(VOICE_TTS_LIMITS["top_p"]["min"]),
        maximum=float(VOICE_TTS_LIMITS["top_p"]["max"]),
    )
    resolved["top_k"] = _coerce_int(
        parsed.get("top_k"),
        default=int(resolved["top_k"]),
        minimum=int(VOICE_TTS_LIMITS["top_k"]["min"]),
        maximum=int(VOICE_TTS_LIMITS["top_k"]["max"]),
    )
    resolved["speed"] = _coerce_float(
        parsed.get("speed"),
        default=float(resolved["speed"]),
        minimum=float(VOICE_TTS_LIMITS["speed"]["min"]),
        maximum=float(VOICE_TTS_LIMITS["speed"]["max"]),
    )
    resolved["chunk_max_chars"] = _coerce_int(
        parsed.get("chunk_max_chars"),
        default=int(resolved["chunk_max_chars"]),
        minimum=int(VOICE_TTS_LIMITS["chunk_max_chars"]["min"]),
        maximum=int(VOICE_TTS_LIMITS["chunk_max_chars"]["max"]),
    )
    resolved["silence_gap_seconds"] = _coerce_float(
        parsed.get("silence_gap_seconds"),
        default=float(resolved["silence_gap_seconds"]),
        minimum=float(VOICE_TTS_LIMITS["silence_gap_seconds"]["min"]),
        maximum=float(VOICE_TTS_LIMITS["silence_gap_seconds"]["max"]),
    )
    return resolved


def serialize_voice_tts_config(raw_config: Any = None) -> str:
    return json.dumps(
        normalize_voice_tts_config(raw_config),
        ensure_ascii=False,
        sort_keys=True,
    )


def chunk_text_for_voice_tts(text: str, raw_config: Any = None) -> list[str]:
    from .chunker import chunk_text_for_tts

    config = normalize_voice_tts_config(raw_config)
    return [
        chunk.text
        for chunk in chunk_text_for_tts(
            text,
            max_chars=int(config["chunk_max_chars"]),
        )
    ]


def build_xtts_inference_kwargs(raw_config: Any = None) -> dict[str, Any]:
    config = normalize_voice_tts_config(raw_config)
    return {
        "temperature": float(config["temperature"]),
        "top_p": float(config["top_p"]),
        "top_k": int(config["top_k"]),
        "speed": float(config["speed"]),
        "do_sample": True,
        "num_beams": 1,
        "enable_text_splitting": False,
    }


def voice_tts_presets_payload() -> dict[str, dict[str, Any]]:
    return {
        name: normalize_voice_tts_config({"preset": name})
        for name in VOICE_TTS_PRESETS
    }


def voice_tts_limits_payload() -> dict[str, dict[str, float | int]]:
    return deepcopy(VOICE_TTS_LIMITS)
