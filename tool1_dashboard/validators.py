from __future__ import annotations

import math
import re
from typing import Any

from .srt_chunker.models import SubtitleCue

ALLOWED_ASSET_TYPES = {"image", "video"}
SCENE_SOFT_MAX_DURATION_SECONDS = 18.0
SCENE_TARGET_DURATION_SECONDS = 14.0
SCENE_MIN_DURATION_SECONDS = 4.0
MANDATORY_PROMPT_RULES = (
    "no text",
    "no subtitles",
    "no captions",
    "no logos",
    "no watermark",
    "no split-screen",
    "no collage",
    "no panels",
    "no white borders or margins",
)
PROMPT_BANNED_PATTERNS = (
    re.compile(r"\bsame\b", re.IGNORECASE),
    re.compile(r"\bas before\b", re.IGNORECASE),
    re.compile(r"\bprevious scene\b", re.IGNORECASE),
    re.compile(r"\bsee above\b", re.IGNORECASE),
    re.compile(r"\bscene_id\b", re.IGNORECASE),
    re.compile(r"\basset_type\b", re.IGNORECASE),
    re.compile(r"[{}]"),
)


def scene_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "scenes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "start": {"type": "number"},
                        "end": {"type": "number"},
                        "duration": {"type": "number"},
                        "text": {"type": "string"},
                        "visual_intent": {"type": "string"},
                        "notes": {"type": "string"},
                    },
                    "required": ["start", "end", "duration", "text", "visual_intent", "notes"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["scenes"],
        "additionalProperties": False,
    }


def visual_bible_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "world_style": {
                "type": "object",
                "properties": {
                    "setting": {"type": "string"},
                    "look": {"type": "string"},
                    "palette": {"type": "string"},
                    "lighting": {"type": "string"},
                    "camera_language": {"type": "string"},
                    "negative_rules": {"type": "string"},
                },
                "required": ["setting", "look", "palette", "lighting", "camera_language", "negative_rules"],
                "additionalProperties": False,
            },
            "characters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "character_id": {"type": "string"},
                        "label": {"type": "string"},
                        "visual_description": {"type": "string"},
                        "wardrobe": {"type": "string"},
                        "demeanor": {"type": "string"},
                        "usage_notes": {"type": "string"},
                    },
                    "required": [
                        "character_id",
                        "label",
                        "visual_description",
                        "wardrobe",
                        "demeanor",
                        "usage_notes",
                    ],
                    "additionalProperties": False,
                },
            },
            "continuity_rules": {
                "type": "array",
                "items": {"type": "string"},
            },
            "environment_rules": {
                "type": "array",
                "items": {"type": "string"},
            },
        },
        "required": ["world_style", "characters", "continuity_rules", "environment_rules"],
        "additionalProperties": False,
    }


def video_prompt_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "prompts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "scene_id": {"type": "string"},
                        "subject": {"type": "string"},
                        "setting": {"type": "string"},
                        "action": {"type": "string"},
                        "camera": {"type": "string"},
                        "look": {"type": "string"},
                        "lighting": {"type": "string"},
                        "rules": {"type": "string"},
                        "character_refs": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "prompt": {"type": "string"},
                    },
                    "required": [
                        "scene_id",
                        "subject",
                        "setting",
                        "action",
                        "camera",
                        "look",
                        "lighting",
                        "rules",
                        "character_refs",
                        "prompt",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["prompts"],
        "additionalProperties": False,
    }


def image_prompt_output_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "prompts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "scene_id": {"type": "string"},
                        "subject": {"type": "string"},
                        "setting": {"type": "string"},
                        "composition": {"type": "string"},
                        "look": {"type": "string"},
                        "lighting": {"type": "string"},
                        "rules": {"type": "string"},
                        "character_refs": {
                            "type": "array",
                            "items": {"type": "string"},
                        },
                        "prompt": {"type": "string"},
                    },
                    "required": [
                        "scene_id",
                        "subject",
                        "setting",
                        "composition",
                        "look",
                        "lighting",
                        "rules",
                        "character_refs",
                        "prompt",
                    ],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["prompts"],
        "additionalProperties": False,
    }


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _sanitize_prompt_value(value: Any) -> str:
    cleaned = _clean_text(value).replace(";", ",")
    cleaned = re.sub(r"\bas before\b", "with consistent continuity", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bprevious scene\b", "this moment", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bsee above\b", "within this frame", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bthe same\b", "the shared", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bsame\b", "shared", cleaned, flags=re.IGNORECASE)
    return re.sub(r"\s*,\s*", ", ", cleaned).strip(" ,;")


def _merge_prompt_rules(value: Any) -> str:
    fragments: list[str] = []
    sources = value if isinstance(value, list) else [value]
    for source in sources:
        for part in re.split(r"[,;\n]+", str(source or "")):
            cleaned = _sanitize_prompt_value(part)
            if cleaned:
                fragments.append(cleaned)

    merged: list[str] = []
    seen: set[str] = set()
    for rule in [*fragments, *MANDATORY_PROMPT_RULES]:
        cleaned = _sanitize_prompt_value(rule)
        key = cleaned.lower()
        if not cleaned or key in seen:
            continue
        seen.add(key)
        merged.append(cleaned)
    return ", ".join(merged)


def _clean_lines(value: Any) -> list[str]:
    if isinstance(value, list):
        candidates = value
    else:
        candidates = str(value or "").splitlines()
    cleaned = [_clean_text(item) for item in candidates]
    return [item for item in cleaned if item]


def _clean_character_refs(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    refs = [_clean_text(item) for item in value]
    return [item for item in refs if item]


def _as_float(value: Any, field_name: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid numeric field '{field_name}'.") from exc


def _normalize_asset_type(value: Any) -> str:
    normalized = _clean_text(value).lower()
    if normalized not in ALLOWED_ASSET_TYPES:
        raise ValueError("asset_type must be image or video.")
    return normalized


def _is_gap_placeholder(
    *,
    start: float,
    end: float,
    requested_duration: float,
    text: str,
    notes: str,
) -> bool:
    if text:
        return False
    if end > start and requested_duration > 0:
        return False
    note_lower = notes.lower()
    return "skip" in note_lower or "gap" in note_lower or "absorbed" in note_lower


def normalize_scene_payload(payload: dict[str, Any], source_chunk_id: int) -> tuple[list[dict[str, Any]], list[str]]:
    scenes = payload.get("scenes")
    if not isinstance(scenes, list):
        raise ValueError("Scene output must contain a scenes array.")
    normalized: list[dict[str, Any]] = []
    warnings: list[str] = []
    for position, scene in enumerate(scenes, start=1):
        if not isinstance(scene, dict):
            raise ValueError(f"Scene {position} is not an object.")
        start = round(_as_float(scene.get("start"), "start"), 3)
        end = round(_as_float(scene.get("end"), "end"), 3)
        text = _clean_text(scene.get("text"))
        notes = _clean_text(scene.get("notes"))
        requested_duration = round(_as_float(scene.get("duration", end - start), "duration"), 3)
        if _is_gap_placeholder(
            start=start,
            end=end,
            requested_duration=requested_duration,
            text=text,
            notes=notes,
        ):
            warnings.append(
                f"Chunk {source_chunk_id} scene {position} was a zero-length gap marker and was discarded."
            )
            continue
        if end <= start:
            raise ValueError(f"Scene {position} ends before it starts.")
        if not text:
            raise ValueError(f"Scene {position} has no text.")
        actual_duration = round(end - start, 3)
        if math.fabs(actual_duration - requested_duration) > 0.35:
            warnings.append(
                f"Chunk {source_chunk_id} scene {position} had an incorrect duration and was normalized."
            )
        normalized.append(
            {
                "start": start,
                "end": end,
                "duration": actual_duration,
                "text": text,
                "asset_type": "image",
                "visual_intent": _clean_text(scene.get("visual_intent")) or None,
                "notes": notes or None,
                "_source_chunk_id": source_chunk_id,
            }
        )
    return normalized, warnings


def apply_default_asset_types(
    scenes: list[dict[str, Any]],
    leading_video_scene_count: int,
) -> list[dict[str, Any]]:
    video_count = max(0, int(leading_video_scene_count))
    assigned: list[dict[str, Any]] = []
    for index, scene in enumerate(scenes, start=1):
        updated = dict(scene)
        updated["asset_type"] = "video" if index <= video_count else "image"
        assigned.append(updated)
    return assigned


def normalize_visual_bible(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    world_style_raw = payload.get("world_style") if isinstance(payload.get("world_style"), dict) else {}
    world_style = {
        "setting": _clean_text(world_style_raw.get("setting")),
        "look": _clean_text(world_style_raw.get("look")),
        "palette": _clean_text(world_style_raw.get("palette")),
        "lighting": _clean_text(world_style_raw.get("lighting")),
        "camera_language": _clean_text(world_style_raw.get("camera_language")),
        "negative_rules": _clean_text(world_style_raw.get("negative_rules")),
    }
    characters: list[dict[str, Any]] = []
    for index, raw_character in enumerate(payload.get("characters") or [], start=1):
        if not isinstance(raw_character, dict):
            continue
        characters.append(
            {
                "character_id": _clean_text(raw_character.get("character_id")) or f"character_{index:03d}",
                "label": _clean_text(raw_character.get("label")),
                "visual_description": _clean_text(raw_character.get("visual_description")),
                "wardrobe": _clean_text(raw_character.get("wardrobe")),
                "demeanor": _clean_text(raw_character.get("demeanor")),
                "usage_notes": _clean_text(raw_character.get("usage_notes")),
            }
        )

    continuity_rules = _clean_lines(payload.get("continuity_rules"))
    environment_rules = _clean_lines(payload.get("environment_rules"))

    errors: list[str] = []
    warnings: list[str] = []
    for key, value in world_style.items():
        if not value:
            errors.append(f"Visual bible is missing world_style.{key}.")
    if not characters:
        warnings.append("Visual bible did not define any recurring characters.")
    for index, character in enumerate(characters, start=1):
        if not character["label"]:
            errors.append(f"Character {index} is missing a label.")
        if not character["visual_description"]:
            errors.append(f"Character {index} is missing a visual_description.")
    if not continuity_rules:
        errors.append("Visual bible must include at least one continuity rule.")
    if not environment_rules:
        errors.append("Visual bible must include at least one environment rule.")

    normalized = {
        "world_style": world_style,
        "characters": characters,
        "continuity_rules": continuity_rules,
        "environment_rules": environment_rules,
    }
    return normalized, {
        "status": "valid" if not errors else "invalid",
        "errors": errors,
        "warnings": warnings,
        "character_count": len(characters),
    }


def _scene_duplicate_key(scene: dict[str, Any]) -> tuple[float, float, str]:
    return (
        round(float(scene["start"]), 1),
        round(float(scene["end"]), 1),
        _clean_text(scene["text"]).lower(),
    )


def _scene_midpoint(scene: dict[str, Any]) -> float:
    return (float(scene["start"]) + float(scene["end"])) / 2.0


def _chunk_ownership_window(
    chunk: dict[str, Any],
    *,
    position: int,
    total_chunks: int,
    overlap_seconds: float,
) -> tuple[float, float]:
    start = float(chunk["start_seconds"])
    end = float(chunk["end_seconds"])
    if overlap_seconds > 0:
        if position > 0:
            start += overlap_seconds / 2.0
        if position < total_chunks - 1:
            end -= overlap_seconds / 2.0
    if end <= start:
        return float(chunk["start_seconds"]), float(chunk["end_seconds"])
    return round(start, 3), round(end, 3)


def _select_owned_scenes(
    scene_groups: list[list[dict[str, Any]]],
    chunk_metadata: list[dict[str, Any]] | None,
    *,
    overlap_seconds: float,
) -> tuple[list[dict[str, Any]], int]:
    if not chunk_metadata or len(chunk_metadata) != len(scene_groups):
        return [scene for group in scene_groups for scene in group], 0

    selected: list[dict[str, Any]] = []
    dropped = 0
    total_chunks = len(chunk_metadata)
    for position, group in enumerate(scene_groups):
        if not group:
            continue
        group_sorted = sorted(group, key=lambda item: (float(item["start"]), float(item["end"])))
        window_start, window_end = _chunk_ownership_window(
            chunk_metadata[position],
            position=position,
            total_chunks=total_chunks,
            overlap_seconds=overlap_seconds,
        )
        owned = [
            scene
            for scene in group_sorted
            if window_start <= _scene_midpoint(scene) <= window_end
        ]
        if not owned:
            center = (window_start + window_end) / 2.0
            owned = [
                min(
                    group_sorted,
                    key=lambda scene: abs(_scene_midpoint(scene) - center),
                )
            ]
        dropped += max(0, len(group_sorted) - len(owned))
        selected.extend(owned)
    return selected, dropped


def _dedupe_scenes(scenes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    merged: list[dict[str, Any]] = []
    deduped = 0
    seen_keys: set[tuple[float, float, str]] = set()
    for scene in scenes:
        key = _scene_duplicate_key(scene)
        if key in seen_keys:
            deduped += 1
            continue
        seen_keys.add(key)
        merged.append(scene)
    return merged, deduped


def _scene_cues(scene: dict[str, Any], cues: list[SubtitleCue]) -> list[SubtitleCue]:
    start_ms = int(round(float(scene["start"]) * 1000))
    end_ms = int(round(float(scene["end"]) * 1000))
    return [
        cue
        for cue in cues
        if cue.start_ms < end_ms and cue.end_ms > start_ms
    ]


def _ends_with_split_punctuation(text: str) -> bool:
    return _clean_text(text).endswith((".", "!", "?", ":", ";"))


def _build_scene_from_cues(
    base_scene: dict[str, Any],
    cues: list[SubtitleCue],
) -> dict[str, Any]:
    start = round(cues[0].start_ms / 1000.0, 3)
    end = round(cues[-1].end_ms / 1000.0, 3)
    return {
        "start": start,
        "end": end,
        "duration": round(end - start, 3),
        "text": _clean_text(" ".join(cue.text.replace("\n", " ") for cue in cues)),
        "asset_type": base_scene.get("asset_type", "image"),
        "visual_intent": base_scene.get("visual_intent"),
        "notes": base_scene.get("notes"),
        "_source_chunk_id": base_scene.get("_source_chunk_id"),
    }


def _split_scene_by_cues(
    scene: dict[str, Any],
    cues: list[SubtitleCue],
    *,
    max_duration: float,
    target_duration: float,
    min_duration: float,
) -> list[dict[str, Any]]:
    overlapping_cues = _scene_cues(scene, cues)
    if len(overlapping_cues) < 2:
        return [scene]

    result: list[dict[str, Any]] = []
    cursor = 0
    while cursor < len(overlapping_cues):
        start_time = overlapping_cues[cursor].start_ms / 1000.0
        limit = cursor
        while limit + 1 < len(overlapping_cues):
            candidate_end = overlapping_cues[limit + 1].end_ms / 1000.0
            if candidate_end - start_time > max_duration:
                break
            limit += 1

        best_index: int | None = None
        best_score: float | None = None
        for candidate in range(cursor, limit + 1):
            candidate_end = overlapping_cues[candidate].end_ms / 1000.0
            duration = candidate_end - start_time
            if duration < min_duration and candidate < limit:
                continue
            score = abs(target_duration - duration)
            if not _ends_with_split_punctuation(overlapping_cues[candidate].text):
                score += 0.75
            if best_score is None or score < best_score:
                best_index = candidate
                best_score = score

        if best_index is None:
            best_index = limit

        result.append(_build_scene_from_cues(scene, overlapping_cues[cursor : best_index + 1]))
        cursor = best_index + 1

    return result or [scene]


def _split_long_scenes(
    scenes: list[dict[str, Any]],
    cues: list[SubtitleCue] | None,
    *,
    max_duration: float,
    target_duration: float,
    min_duration: float,
) -> tuple[list[dict[str, Any]], int]:
    if not cues:
        return scenes, 0

    split_scenes: list[dict[str, Any]] = []
    inserted = 0
    for scene in scenes:
        if float(scene["duration"]) <= max_duration:
            split_scenes.append(scene)
            continue
        fragments = _split_scene_by_cues(
            scene,
            cues,
            max_duration=max_duration,
            target_duration=target_duration,
            min_duration=min_duration,
        )
        split_scenes.extend(fragments)
        inserted += max(0, len(fragments) - 1)
    return split_scenes, inserted


def _resolve_scene_overlaps(
    scenes: list[dict[str, Any]],
    *,
    min_duration: float,
) -> tuple[list[dict[str, Any]], int]:
    if not scenes:
        return [], 0

    ordered = [dict(scene) for scene in sorted(scenes, key=lambda item: (float(item["start"]), float(item["end"])))]
    resolved: list[dict[str, Any]] = [ordered[0]]
    adjusted = 0

    for scene in ordered[1:]:
        current = dict(scene)
        previous = resolved[-1]
        overlap = round(float(previous["end"]) - float(current["start"]), 3)
        if overlap > 0.25:
            if float(previous["end"]) <= float(current["end"]):
                new_start = round(float(previous["end"]), 3)
                if float(current["end"]) - new_start >= min_duration:
                    current["start"] = new_start
                    current["duration"] = round(float(current["end"]) - float(current["start"]), 3)
                    adjusted += 1
                else:
                    adjusted += 1
                    continue
            else:
                new_end = round(float(current["start"]), 3)
                if new_end - float(previous["start"]) >= min_duration:
                    previous["end"] = new_end
                    previous["duration"] = round(float(previous["end"]) - float(previous["start"]), 3)
                    adjusted += 1
                else:
                    resolved[-1] = current
                    adjusted += 1
                    continue
        resolved.append(current)

    return resolved, adjusted


def merge_scene_chunks(
    scene_groups: list[list[dict[str, Any]]],
    *,
    chunk_metadata: list[dict[str, Any]] | None = None,
    overlap_seconds: float = 0.0,
    cues: list[SubtitleCue] | None = None,
    max_duration: float = SCENE_SOFT_MAX_DURATION_SECONDS,
    target_duration: float = SCENE_TARGET_DURATION_SECONDS,
    min_duration: float = SCENE_MIN_DURATION_SECONDS,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    selected, ownership_dropped = _select_owned_scenes(
        scene_groups,
        chunk_metadata,
        overlap_seconds=overlap_seconds,
    )
    selected.sort(key=lambda item: (float(item["start"]), float(item["end"]), _clean_text(item["text"]).lower()))
    merged, deduped = _dedupe_scenes(selected)
    merged, split_insertions = _split_long_scenes(
        merged,
        cues,
        max_duration=max_duration,
        target_duration=target_duration,
        min_duration=min_duration,
    )
    merged, overlap_adjustments = _resolve_scene_overlaps(merged, min_duration=min_duration)

    finalized: list[dict[str, Any]] = []
    for index, scene in enumerate(merged, start=1):
        finalized.append(
            {
                "scene_id": f"scene_{index:03d}",
                "start": round(float(scene["start"]), 3),
                "end": round(float(scene["end"]), 3),
                "duration": round(float(scene["duration"]), 3),
                "text": scene["text"],
                "asset_type": scene.get("asset_type") if scene.get("asset_type") in ALLOWED_ASSET_TYPES else "image",
                **({"visual_intent": scene["visual_intent"]} if scene.get("visual_intent") else {}),
                **({"notes": scene["notes"]} if scene.get("notes") else {}),
            }
        )
    report = validate_timeline(finalized)
    report["deduped_duplicates"] = deduped
    report["ownership_dropped"] = ownership_dropped
    report["split_insertions"] = split_insertions
    report["overlap_adjustments"] = overlap_adjustments
    return finalized, report


def validate_timeline(scenes: list[dict[str, Any]]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    previous_end = None
    for index, scene in enumerate(scenes, start=1):
        start = float(scene["start"])
        end = float(scene["end"])
        duration = float(scene["duration"])
        if end <= start:
            errors.append(f"Scene {index} ends before it starts.")
        if abs((end - start) - duration) > 0.35:
            errors.append(f"Scene {index} duration does not match start/end.")
        if scene.get("asset_type") not in ALLOWED_ASSET_TYPES:
            errors.append(f"Scene {index} has an invalid asset_type.")
        if previous_end is not None and start < (previous_end - 0.25):
            warnings.append(f"Scene {index} overlaps the previous scene.")
        if duration < 1.0:
            warnings.append(f"Scene {index} is very short.")
        if duration > 18.0:
            warnings.append(f"Scene {index} is unusually long.")
        previous_end = end
    return {
        "status": "valid" if not errors else "invalid",
        "errors": errors,
        "warnings": warnings,
        "scene_count": len(scenes),
        "total_duration": round(float(scenes[-1]["end"]) - float(scenes[0]["start"]), 3) if scenes else 0.0,
    }


def _sentence_fragment(value: Any) -> str:
    cleaned = _sanitize_prompt_value(value)
    if not cleaned:
        return ""
    normalized = cleaned[0].upper() + cleaned[1:] if cleaned else ""
    return normalized.rstrip(" .,:;") + "."


def _compose_prompt_text(asset_type: str, prompt_entry: dict[str, Any]) -> str:
    subject = _sanitize_prompt_value(prompt_entry.get("subject"))
    setting = _sanitize_prompt_value(prompt_entry.get("setting"))
    action = _sanitize_prompt_value(prompt_entry.get("action"))
    composition = _sanitize_prompt_value(prompt_entry.get("composition"))

    primary_parts = [part for part in (subject, f"in {setting}" if subject and setting else setting) if part]
    primary = " ".join(primary_parts).strip()
    if asset_type == "video" and action:
        primary = f"{primary}, {action}" if primary else action
    if asset_type == "image" and composition:
        primary = f"{primary}, {composition}" if primary else composition

    fragments = [primary]
    if asset_type == "video":
        fragments.append(prompt_entry.get("camera"))
    fragments.extend(
        [
            prompt_entry.get("look"),
            prompt_entry.get("lighting"),
            _merge_prompt_rules(prompt_entry.get("rules")),
        ]
    )
    rendered = [_sentence_fragment(fragment) for fragment in fragments if _sanitize_prompt_value(fragment)]
    return " ".join(rendered).strip()


def _has_structured_prompt_fields(asset_type: str, prompt_entry: dict[str, Any]) -> bool:
    required_fields = (
        ("subject", "setting", "action", "camera", "look", "lighting")
        if asset_type == "video"
        else ("subject", "setting", "composition", "look", "lighting")
    )
    return all(_clean_text(prompt_entry.get(field_name)) for field_name in required_fields)


def _validate_prompt_line(prompt_text: str, asset_type: str) -> list[str]:
    errors: list[str] = []
    if not prompt_text:
        return ["Prompt is empty."]
    if "\n" in prompt_text or "\r" in prompt_text:
        errors.append("Prompt must be a single line.")
    if len(_clean_text(prompt_text).split()) < 6:
        errors.append("Prompt is too short to be useful.")
    for pattern in PROMPT_BANNED_PATTERNS:
        if pattern.search(prompt_text):
            errors.append(f"Prompt contains banned content matching {pattern.pattern}.")
    return errors


def normalize_prompt_payloads(
    scenes: list[dict[str, Any]],
    payloads: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    prompt_entries: list[dict[str, Any]] = []
    for payload in payloads:
        prompts = payload.get("prompts")
        if not isinstance(prompts, list):
            raise ValueError("Prompt output must contain a prompts array.")
        for item in prompts:
            if isinstance(item, str):
                prompt_entries.append({"scene_id": None, "prompt": item.strip()})
                continue
            if isinstance(item, dict):
                prompt_entries.append(
                    {
                        "scene_id": _clean_text(item.get("scene_id")) or None,
                        "prompt": _clean_text(item.get("prompt")),
                        "subject": _clean_text(item.get("subject")),
                        "setting": _clean_text(item.get("setting")),
                        "action": _clean_text(item.get("action")),
                        "camera": _clean_text(item.get("camera")),
                        "composition": _clean_text(item.get("composition")),
                        "look": _clean_text(item.get("look")),
                        "lighting": _clean_text(item.get("lighting")),
                        "rules": _clean_text(item.get("rules")),
                        "character_refs": _clean_character_refs(item.get("character_refs")),
                    }
                )
                continue
            raise ValueError("Each prompt must be a string or an object with a prompt field.")

    errors: list[str] = []
    warnings: list[str] = []
    if len(prompt_entries) != len(scenes):
        errors.append(
            f"Prompt count mismatch: expected {len(scenes)}, received {len(prompt_entries)}."
        )

    normalized_entries: list[dict[str, Any]] = []
    for index, scene in enumerate(scenes):
        if index >= len(prompt_entries):
            break
        prompt_entry = prompt_entries[index]
        if _has_structured_prompt_fields(scene["asset_type"], prompt_entry):
            prompt_text = _compose_prompt_text(scene["asset_type"], prompt_entry)
        else:
            prompt_text = _clean_text(prompt_entry.get("prompt"))
        scene_id = prompt_entry.get("scene_id")
        if scene_id and scene_id != scene["scene_id"]:
            warnings.append(
                f"Prompt {index + 1} referenced {scene_id}, expected {scene['scene_id']}."
            )
        line_errors = _validate_prompt_line(prompt_text, scene["asset_type"])
        errors.extend(f"Prompt {index + 1}: {message}" for message in line_errors)

        normalized_entry = {
            "scene_id": scene["scene_id"],
            "asset_type": scene["asset_type"],
            "prompt": prompt_text,
            "subject": prompt_entry.get("subject") or None,
            "setting": prompt_entry.get("setting") or None,
            "look": prompt_entry.get("look") or None,
            "lighting": prompt_entry.get("lighting") or None,
            "rules": prompt_entry.get("rules") or None,
            "character_refs": prompt_entry.get("character_refs") or [],
        }
        if scene["asset_type"] == "video":
            normalized_entry["action"] = prompt_entry.get("action") or None
            normalized_entry["camera"] = prompt_entry.get("camera") or None
        else:
            normalized_entry["composition"] = prompt_entry.get("composition") or None
        normalized_entries.append(normalized_entry)

    return normalized_entries, {
        "status": "valid" if not errors else "invalid",
        "errors": errors,
        "warnings": warnings,
        "scene_count": len(scenes),
        "prompt_count": len(prompt_entries),
    }


def validate_prompt_payloads(
    scenes: list[dict[str, Any]],
    payloads: list[dict[str, Any]],
) -> tuple[list[str], dict[str, Any]]:
    normalized_entries, report = normalize_prompt_payloads(scenes, payloads)
    final_lines = [_clean_text(entry.get("prompt")) for entry in normalized_entries]
    return final_lines, report
