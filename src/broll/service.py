"""High-level B-roll service functions used by the UI and automation pipeline."""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import requests

from .config import broll_provider_status
from .models import BrollResult
from .providers import BrollProviderError, search_pexels_videos, search_pixabay_videos

logger = logging.getLogger(__name__)

_DOWNLOAD_TIMEOUT = 120
_SEARCH_CACHE_TTL_SECONDS = 600.0
_SEARCH_CACHE: dict[str, tuple[float, list[BrollResult]]] = {}
_LAST_SEARCH_ERRORS: list[str] = []


def _cache_key(query: str, aspect_ratio: str, per_page: int, priority: list[str]) -> str:
    return f"{query.strip().lower()}::{aspect_ratio}::{per_page}::{','.join(priority)}"


def _cache_get(key: str) -> list[BrollResult] | None:
    payload = _SEARCH_CACHE.get(key)
    if payload is None:
        return None
    ts, results = payload
    if time.monotonic() - ts > _SEARCH_CACHE_TTL_SECONDS:
        _SEARCH_CACHE.pop(key, None)
        return None
    return results


def _cache_set(key: str, results: list[BrollResult]) -> None:
    _SEARCH_CACHE[key] = (time.monotonic(), results)


def get_last_search_errors() -> list[str]:
    return list(_LAST_SEARCH_ERRORS)


_STOP_WORDS = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "been", "but", "by",
    "do", "for", "from", "had", "has", "have", "he", "her", "him",
    "his", "how", "i", "in", "into", "is", "it", "its", "just", "me",
    "my", "no", "not", "of", "on", "or", "our", "out", "s", "she",
    "so", "than", "that", "the", "their", "them", "then", "there",
    "they", "this", "to", "up", "us", "was", "we", "were", "what",
    "when", "which", "who", "will", "with", "you", "your",
})


def _extract_keywords(text: str, max_keywords: int = 5) -> list[str]:
    words = re.findall(r"[a-zA-Z]{3,}", str(text or "").lower())
    seen: set[str] = set()
    keywords: list[str] = []
    for word in words:
        if word not in _STOP_WORDS and word not in seen:
            seen.add(word)
            keywords.append(word)
        if len(keywords) >= max_keywords:
            break
    return keywords


def generate_broll_query_for_scene(scene: Any) -> str:
    existing = str(getattr(scene, "broll_query", "") or "").strip()
    if existing:
        return existing

    visual = str(getattr(scene, "visual_intent", "") or "").strip()
    if visual:
        keywords = _extract_keywords(visual, max_keywords=5)
        if keywords:
            return " ".join(keywords)

    excerpt = str(getattr(scene, "script_excerpt", "") or "").strip()
    keywords = _extract_keywords(excerpt, max_keywords=4)
    return " ".join(keywords) if keywords else "historical documentary"


def search_broll(
    query: str,
    aspect_ratio: str,
    provider_priority: list[str] | None = None,
    per_page: int = 5,
) -> list[BrollResult]:
    global _LAST_SEARCH_ERRORS
    _LAST_SEARCH_ERRORS = []

    safe_query = str(query or "").strip()
    if not safe_query:
        _LAST_SEARCH_ERRORS.append("No B-roll query provided.")
        return []

    priority = [str(p).lower() for p in (provider_priority or ["pexels", "pixabay"])]
    key = _cache_key(safe_query, aspect_ratio, per_page, priority)
    cached = _cache_get(key)
    if cached is not None:
        return cached

    status = broll_provider_status()
    provider_map = {
        "pexels": search_pexels_videos,
        "pixabay": search_pixabay_videos,
    }

    for provider_name in priority:
        fn = provider_map.get(provider_name)
        if fn is None:
            _LAST_SEARCH_ERRORS.append(f"Unknown B-roll provider: {provider_name}.")
            continue
        if not status.get(provider_name, False):
            _LAST_SEARCH_ERRORS.append(
                "Pexels API key not found in Streamlit secrets." if provider_name == "pexels" else "Pixabay API key not found in Streamlit secrets."
            )
            continue

        try:
            results = fn(safe_query, aspect_ratio=aspect_ratio, per_page=per_page)
        except BrollProviderError as exc:
            _LAST_SEARCH_ERRORS.append(str(exc))
            continue
        except Exception as exc:
            _LAST_SEARCH_ERRORS.append(f"{provider_name.title()} search failed: {exc}")
            continue

        if results:
            _cache_set(key, results)
            return results
        _LAST_SEARCH_ERRORS.append(f"No {provider_name.title()} results found for this scene.")

    return []


def search_broll_for_scene(
    scene: Any,
    aspect_ratio: str = "16:9",
    per_page: int = 5,
    provider_priority: list[str] | None = None,
) -> list[BrollResult]:
    query = generate_broll_query_for_scene(scene)
    if not query:
        return []
    return search_broll(query, aspect_ratio=aspect_ratio, per_page=per_page, provider_priority=provider_priority)


def download_broll_asset(project_id: str, scene_index: int, result: BrollResult) -> Path:
    broll_dir = Path("data/projects") / str(project_id) / "assets" / "broll"
    broll_dir.mkdir(parents=True, exist_ok=True)

    safe_provider = re.sub(r"[^a-z0-9_-]", "", str(result.provider or "unknown").lower()) or "unknown"
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "", str(result.id or "clip")) or "clip"
    dest = broll_dir / f"s{scene_index:02d}_{safe_provider}_{safe_id}.mp4"

    if dest.exists() and dest.stat().st_size > 0:
        result.local_path = str(dest.resolve())
        return dest.resolve()

    video_url = str(result.video_url or "").strip()
    if not video_url:
        raise RuntimeError("BrollResult has no video_url to download.")

    try:
        with requests.get(video_url, stream=True, timeout=_DOWNLOAD_TIMEOUT) as resp:
            resp.raise_for_status()
            with dest.open("wb") as fh:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    if chunk:
                        fh.write(chunk)
    except requests.RequestException as exc:
        if dest.exists():
            dest.unlink(missing_ok=True)
        raise RuntimeError(f"Failed to download B-roll clip: {exc}") from exc

    if not dest.exists() or dest.stat().st_size <= 0:
        dest.unlink(missing_ok=True)
        raise RuntimeError("B-roll download failed: file is empty.")

    metadata = {
        "provider": result.provider,
        "id": result.id,
        "source_url": result.video_url,
        "page_url": result.page_url,
        "attribution_text": result.attribution_text,
        "license_note": result.license_note,
    }
    meta_path = dest.with_suffix(".json")
    meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")

    result.local_path = str(dest.resolve())
    return dest.resolve()


def assign_broll_to_scene(scene: Any, result: BrollResult, local_path: Path | str) -> None:
    scene.broll_query = generate_broll_query_for_scene(scene)
    scene.broll_provider = result.provider
    scene.broll_source_url = result.video_url
    scene.broll_page_url = result.page_url
    scene.broll_local_path = str(local_path)
    scene.broll_duration_sec = result.duration_sec
    scene.broll_orientation = result.orientation
    scene.use_broll = True


def clear_broll_from_scene(scene: Any) -> None:
    scene.broll_query = ""
    scene.broll_provider = ""
    scene.broll_source_url = ""
    scene.broll_page_url = ""
    scene.broll_local_path = ""
    scene.broll_duration_sec = 0.0
    scene.broll_orientation = ""
    scene.use_broll = False
