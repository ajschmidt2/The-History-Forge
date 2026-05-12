"""Llama-assisted scene media planning for historical videos.

This module decides whether a scene is best represented by:
  - a real historical image search result
  - real stock B-roll
  - an AI-generated image

The router prefers Ollama/Llama for this utility work and falls back to the
existing remote providers when Ollama is unavailable.
"""

from __future__ import annotations

import json
import re
from typing import Any

from src.ai.provider_router import get_router

_ASSET_REAL_IMAGE = "real_image"
_ASSET_BROLL = "broll"
_ASSET_AI_IMAGE = "ai_image"
_VALID_ASSET_TYPES = {_ASSET_REAL_IMAGE, _ASSET_BROLL, _ASSET_AI_IMAGE}

_PHOTO_ERA_HINTS = (
    "photograph",
    "photo",
    "newspaper",
    "poster",
    "newsreel",
    "document",
    "archive",
    "portrait",
    "city street",
    "factory",
    "president",
    "world war",
    "194",
    "195",
    "196",
    "197",
    "198",
)

_MOTION_HINTS = (
    "march",
    "marching",
    "sailing",
    "storm",
    "crowd",
    "street",
    "waves",
    "ship",
    "ocean",
    "smoke",
    "horse",
    "battlefield",
    "parade",
    "train",
    "travel",
    "walking",
    "running",
    "workers",
)

_PRE_PHOTO_HINTS = (
    "ancient",
    "roman",
    "medieval",
    "viking",
    "bronze age",
    "stone age",
    "empire",
    "kingdom",
    "pharaoh",
    "spartan",
    "samurai",
)


def _keywords(text: str, limit: int = 6) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z'-]{2,}", str(text or ""))
    seen: set[str] = set()
    picked: list[str] = []
    for word in words:
        lowered = word.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        picked.append(word)
        if len(picked) >= limit:
            break
    return picked


def _clean_query_list(values: Any, fallback: list[str]) -> list[str]:
    if not isinstance(values, list):
        return fallback
    cleaned: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        lowered = text.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        cleaned.append(text)
    return cleaned or fallback


def _heuristic_asset_type(scene: Any, topic: str) -> str:
    haystack = " ".join(
        [
            str(getattr(scene, "title", "") or ""),
            str(getattr(scene, "script_excerpt", "") or ""),
            str(getattr(scene, "visual_intent", "") or ""),
            str(topic or ""),
        ]
    ).lower()
    if any(token in haystack for token in _PHOTO_ERA_HINTS):
        return _ASSET_REAL_IMAGE
    if any(token in haystack for token in _MOTION_HINTS):
        return _ASSET_BROLL
    if any(token in haystack for token in _PRE_PHOTO_HINTS):
        return _ASSET_AI_IMAGE
    return _ASSET_REAL_IMAGE if re.search(r"\b(18|19|20)\d{2}\b", haystack) else _ASSET_AI_IMAGE


def _fallback_plan_for_scene(scene: Any, topic: str, era: str) -> dict[str, Any]:
    scene_title = str(getattr(scene, "title", "") or "").strip()
    excerpt = str(getattr(scene, "script_excerpt", "") or "").strip()
    visual_intent = str(getattr(scene, "visual_intent", "") or "").strip()
    primary_asset = _heuristic_asset_type(scene, topic)
    search_seed = scene_title or topic or "historical scene"
    search_keywords = _keywords(" ".join([scene_title, excerpt, topic, era]), limit=6)
    joined_keywords = " ".join(search_keywords[:5]).strip() or search_seed
    return {
        "scene_index": int(getattr(scene, "index", 0) or 0),
        "primary_asset": primary_asset,
        "real_image_search_terms": [
            " ".join(part for part in [scene_title, topic] if part).strip() or search_seed,
            " ".join(part for part in [topic, era, "historical"] if part).strip() or search_seed,
        ],
        "broll_query": joined_keywords,
        "notes": visual_intent or excerpt or search_seed,
    }


def _normalize_plan(raw_plan: dict[str, Any], scene: Any, topic: str, era: str) -> dict[str, Any]:
    fallback = _fallback_plan_for_scene(scene, topic, era)
    primary_asset = str(raw_plan.get("primary_asset", "") or "").strip().lower()
    if primary_asset not in _VALID_ASSET_TYPES:
        primary_asset = fallback["primary_asset"]
    real_image_search_terms = _clean_query_list(
        raw_plan.get("real_image_search_terms"),
        fallback["real_image_search_terms"],
    )
    broll_query = str(raw_plan.get("broll_query", "") or "").strip() or fallback["broll_query"]
    notes = str(raw_plan.get("notes", "") or "").strip() or fallback["notes"]
    return {
        "scene_index": int(getattr(scene, "index", 0) or 0),
        "primary_asset": primary_asset,
        "real_image_search_terms": real_image_search_terms,
        "broll_query": broll_query,
        "notes": notes,
    }


def _fallback_batch_plan(scenes: list[Any], topic: str, era: str) -> dict[int, dict[str, Any]]:
    return {
        int(getattr(scene, "index", 0) or 0): _fallback_plan_for_scene(scene, topic, era)
        for scene in scenes
        if int(getattr(scene, "index", 0) or 0) > 0
    }


def plan_media_for_scenes(
    scenes: list[Any],
    *,
    topic: str = "",
    era: str = "",
    aspect_ratio: str = "9:16",
) -> dict[int, dict[str, Any]]:
    """Return a per-scene media plan using Ollama/Llama first, heuristics second."""
    ordered_scenes = sorted(
        [scene for scene in scenes if int(getattr(scene, "index", 0) or 0) > 0],
        key=lambda item: int(getattr(item, "index", 0) or 0),
    )
    if not ordered_scenes:
        return {}

    scene_lines: list[str] = []
    for scene in ordered_scenes:
        scene_lines.append(
            "\n".join(
                [
                    f"Scene {int(scene.index)}",
                    f"Title: {str(getattr(scene, 'title', '') or '').strip()}",
                    f"Excerpt: {str(getattr(scene, 'script_excerpt', '') or '').strip()}",
                    f"Visual intent: {str(getattr(scene, 'visual_intent', '') or '').strip()}",
                ]
            )
        )

    prompt = (
        "You are planning visuals for a historical documentary video.\n"
        "Choose the best primary asset for each scene.\n"
        "Rules:\n"
        "- Use 'real_image' when a scene likely has real archival photos, documents, maps, posters, portraits, or engravings.\n"
        "- Use 'broll' when the scene benefits most from motion, atmosphere, crowds, streets, travel, oceans, smoke, or generalized activity.\n"
        "- Use 'ai_image' when the scene is ancient, speculative, or unlikely to have trustworthy real visuals.\n"
        "- Prefer historical accuracy and specificity over generic stock aesthetics.\n"
        "- Return strict JSON only.\n\n"
        f"Topic: {topic}\n"
        f"Era: {era}\n"
        f"Aspect ratio: {aspect_ratio}\n\n"
        "Return an object with a 'plans' array. Each plan must include:\n"
        "scene_index, primary_asset, real_image_search_terms, broll_query, notes.\n\n"
        "Scenes:\n"
        f"{chr(10).join(scene_lines)}"
    )
    system = (
        "You are a careful history-video visual producer. "
        "Pick between real archival imagery, stock B-roll motion, and AI illustration. "
        "Return valid JSON only."
    )

    try:
        raw = get_router().generate_structured(prompt, system=system, task_type="metadata")
        parsed = json.loads(raw)
        plans = parsed.get("plans", []) if isinstance(parsed, dict) else []
        by_index: dict[int, dict[str, Any]] = {}
        for scene in ordered_scenes:
            scene_idx = int(getattr(scene, "index", 0) or 0)
            raw_plan = next(
                (
                    item for item in plans
                    if isinstance(item, dict) and int(item.get("scene_index", 0) or 0) == scene_idx
                ),
                {},
            )
            by_index[scene_idx] = _normalize_plan(raw_plan, scene, topic, era)
        return by_index
    except Exception:
        return _fallback_batch_plan(ordered_scenes, topic, era)
