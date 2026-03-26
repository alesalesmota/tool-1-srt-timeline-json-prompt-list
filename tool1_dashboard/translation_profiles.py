from __future__ import annotations

from copy import deepcopy
import re
from typing import Any

TRANSLATION_PROFILE_PROVIDER_CATALOG = (
    {
        "id": "openai",
        "label": "OpenAI API",
        "mode": "api",
        "placeholder": False,
        "description": "Live API-backed translation profile with model discovery.",
        "runnable": True,
        "editable": True,
    },
    {
        "id": "codex_cli",
        "label": "Codex CLI",
        "mode": "cli",
        "placeholder": True,
        "description": "Preview-only placeholder. CLI translation is not wired up yet.",
        "runnable": False,
        "editable": False,
    },
    {
        "id": "claude_cli",
        "label": "Claude Code CLI",
        "mode": "cli",
        "placeholder": True,
        "description": "Preview-only placeholder. CLI translation is not wired up yet.",
        "runnable": False,
        "editable": False,
    },
)

RUNNABLE_TRANSLATION_PROFILE_PROVIDERS = {
    spec["id"]
    for spec in TRANSLATION_PROFILE_PROVIDER_CATALOG
    if spec.get("runnable")
}

LEGACY_TRANSLATION_PROVIDER_LABELS = {
    "anthropic": "Anthropic API (legacy)",
    "gemini": "Gemini API (legacy)",
    "deepl": "DeepL API (legacy)",
}

OPENAI_MODEL_HINTS: dict[str, dict[str, Any]] = {
    "gpt-5.4": {
        "label": "GPT-5.4",
        "capability_label": "Best quality",
        "best_for": "Highest-quality translation and difficult wording.",
        "price_score": 5,
        "speed_score": 3,
        "priority": 30,
    },
    "gpt-5.4-mini": {
        "label": "GPT-5.4 Mini",
        "capability_label": "Balanced",
        "best_for": "Best default for translation quality, speed, and cost.",
        "price_score": 3,
        "speed_score": 4,
        "priority": 10,
    },
    "gpt-5.4-nano": {
        "label": "GPT-5.4 Nano",
        "capability_label": "Budget",
        "best_for": "Cheap high-volume runs where quality can be lighter.",
        "price_score": 1,
        "speed_score": 5,
        "priority": 20,
    },
    "gpt-4.1": {
        "label": "GPT-4.1",
        "capability_label": "Reliable",
        "best_for": "Stable general-purpose translation.",
        "price_score": 4,
        "speed_score": 3,
        "priority": 40,
    },
    "gpt-4.1-mini": {
        "label": "GPT-4.1 Mini",
        "capability_label": "Fast mini",
        "best_for": "Faster low-cost translation with good quality.",
        "price_score": 2,
        "speed_score": 4,
        "priority": 25,
    },
    "gpt-4.1-nano": {
        "label": "GPT-4.1 Nano",
        "capability_label": "Cheap",
        "best_for": "Lowest-cost simple text translation.",
        "price_score": 1,
        "speed_score": 5,
        "priority": 35,
    },
    "gpt-4o": {
        "label": "GPT-4o",
        "capability_label": "General-purpose",
        "best_for": "Solid multilingual translation and broad availability.",
        "price_score": 4,
        "speed_score": 4,
        "priority": 45,
    },
    "gpt-4o-mini": {
        "label": "GPT-4o Mini",
        "capability_label": "Affordable",
        "best_for": "Good low-cost default when GPT-5 models are unavailable.",
        "price_score": 2,
        "speed_score": 5,
        "priority": 15,
    },
}

OPENAI_MODEL_RECOMMENDATION_ORDER = (
    "gpt-5.4-mini",
    "gpt-4o-mini",
    "gpt-4.1-mini",
    "gpt-5.4",
    "gpt-4o",
    "gpt-4.1",
    "gpt-5.4-nano",
    "gpt-4.1-nano",
)

_OPENAI_MODEL_EXCLUDE_FRAGMENTS = (
    "audio",
    "transcribe",
    "tts",
    "realtime",
    "image",
    "embedding",
    "moderation",
    "whisper",
    "dall-e",
    "search",
    "computer-use",
)

_OPENAI_TEXT_MODEL_RE = re.compile(r"^(gpt-|chatgpt-|o1|o3|o4)", re.IGNORECASE)


def translation_profile_provider_spec(provider: str | None) -> dict[str, Any]:
    normalized = str(provider or "").strip()
    for spec in TRANSLATION_PROFILE_PROVIDER_CATALOG:
        if spec["id"] == normalized:
            return deepcopy(spec)
    label = LEGACY_TRANSLATION_PROVIDER_LABELS.get(normalized)
    if not label:
        label = f"Legacy: {normalized}" if normalized else "Unknown provider"
    return {
        "id": normalized or "unknown",
        "label": label,
        "mode": "legacy",
        "placeholder": False,
        "description": "Legacy translation profile retained for compatibility.",
        "runnable": normalized in RUNNABLE_TRANSLATION_PROFILE_PROVIDERS,
        "editable": normalized in RUNNABLE_TRANSLATION_PROFILE_PROVIDERS,
    }


def mask_secret(secret: str | None) -> str:
    value = str(secret or "").strip()
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    prefix = value[:4]
    suffix = value[-4:]
    return f"{prefix}{'*' * max(len(value) - 8, 4)}{suffix}"


def sanitize_translation_profile(profile: dict[str, Any]) -> dict[str, Any]:
    payload = dict(profile)
    provider_spec = translation_profile_provider_spec(payload.get("provider"))
    api_key = str(payload.pop("api_key_ref", "") or "")
    payload["provider_label"] = provider_spec["label"]
    payload["provider_mode"] = provider_spec["mode"]
    payload["provider_placeholder"] = bool(provider_spec.get("placeholder"))
    payload["provider_runnable"] = bool(provider_spec.get("runnable"))
    payload["provider_editable"] = bool(provider_spec.get("editable"))
    payload["provider_description"] = provider_spec.get("description", "")
    payload["has_api_key"] = bool(api_key)
    payload["api_key_masked"] = mask_secret(api_key)
    return payload


def is_runnable_translation_profile_provider(provider: str | None) -> bool:
    return str(provider or "").strip() in RUNNABLE_TRANSLATION_PROFILE_PROVIDERS


def is_openai_text_model(model_id: str | None) -> bool:
    candidate = str(model_id or "").strip()
    if not candidate:
        return False
    lowered = candidate.lower()
    if lowered.startswith("ft:") or ":ft-" in lowered:
        return False
    if any(fragment in lowered for fragment in _OPENAI_MODEL_EXCLUDE_FRAGMENTS):
        return False
    return bool(_OPENAI_TEXT_MODEL_RE.match(candidate))


def _humanize_model_label(model_id: str) -> str:
    parts = re.split(r"[-_]", model_id)
    return " ".join(part.upper() if part.isdigit() else part.capitalize() for part in parts if part)


def normalize_openai_model(model: dict[str, Any]) -> dict[str, Any] | None:
    model_id = str(model.get("id") or "").strip()
    if not is_openai_text_model(model_id):
        return None
    hint = OPENAI_MODEL_HINTS.get(model_id, {})
    return {
        "id": model_id,
        "label": hint.get("label") or _humanize_model_label(model_id),
        "capability_label": hint.get("capability_label") or "General text",
        "best_for": hint.get("best_for") or "General-purpose text translation.",
        "price_score": int(hint.get("price_score", 3)),
        "speed_score": int(hint.get("speed_score", 3)),
        "priority": int(hint.get("priority", 999)),
        "owned_by": str(model.get("owned_by") or ""),
        "created": model.get("created"),
    }


def sort_openai_models(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        models,
        key=lambda item: (
            int(item.get("priority", 999)),
            int(item.get("price_score", 3)),
            -int(item.get("speed_score", 3)),
            str(item.get("label") or item.get("id") or "").lower(),
        ),
    )


def recommended_openai_model(models: list[dict[str, Any]]) -> str:
    available = {str(model.get("id") or "").strip() for model in models}
    for candidate in OPENAI_MODEL_RECOMMENDATION_ORDER:
        if candidate in available:
            return candidate
    return str(models[0].get("id") or "").strip() if models else ""

