"""Provider implementations for free B-roll video search.

Supported providers
-------------------
* Pexels  – https://www.pexels.com/api/  (200 req/hr, 20 000 req/month, free)
* Pixabay – https://pixabay.com/api/docs/ (100 req/60s, responses must be cached 24 h)

Environment / Streamlit secrets
--------------------------------
``PEXELS_API_KEY``   – Pexels API key
``PIXABAY_API_KEY``  – Pixabay API key

Neither key is required; if a key is absent the provider is silently skipped
and the other is tried instead.
"""

from __future__ import annotations

import json
import logging
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

import requests

from src.config.secrets import get_secret
from .models import BrollResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Simple in-process request-level cache (keyed by provider + query + orientation).
# This prevents burning API quota on repeated identical searches within the same
# Streamlit session.  Cache entries expire after CACHE_TTL_SECONDS.
# ---------------------------------------------------------------------------
_CACHE: dict[str, tuple[float, list[BrollResult]]] = {}
CACHE_TTL_SECONDS: float = 86_400.0  # 24 hours (Pixabay requirement)
_REQUEST_TIMEOUT = 10  # seconds per HTTP call


def _cache_get(key: str) -> list[BrollResult] | None:
    entry = _CACHE.get(key)
    if entry is None:
        return None
    ts, results = entry
    if time.monotonic() - ts > CACHE_TTL_SECONDS:
        del _CACHE[key]
        return None
    return results


def _cache_set(key: str, results: list[BrollResult]) -> None:
    _CACHE[key] = (time.monotonic(), results)


# ---------------------------------------------------------------------------
# Orientation helpers
# ---------------------------------------------------------------------------

def _aspect_ratio_to_pexels_orientation(aspect_ratio: str) -> str:
    """Map a History Forge aspect ratio string to a Pexels orientation filter."""
    return "portrait" if str(aspect_ratio or "16:9").strip() == "9:16" else "landscape"


def _aspect_ratio_to_pixabay_orientation(aspect_ratio: str) -> str:
    """Map a History Forge aspect ratio string to a Pixabay video_type-compatible orientation."""
    # Pixabay does not have an orientation filter on the video endpoint; we do
    # client-side filtering by comparing width/height in the response.
    return "vertical" if str(aspect_ratio or "16:9").strip() == "9:16" else "horizontal"


def _clip_orientation(width: int, height: int) -> str:
    """Derive 'vertical' or 'horizontal' from clip dimensions."""
    return "vertical" if int(height or 0) > int(width or 0) else "horizontal"


# ---------------------------------------------------------------------------
# Pexels
# ---------------------------------------------------------------------------

_PEXELS_VIDEO_SEARCH_URL = "https://api.pexels.com/videos/search"


def _best_pexels_file(video_files: list[dict]) -> dict:
    """Pick the highest-quality file whose width is ≤ 1920 pixels."""
    candidates = [f for f in video_files if isinstance(f, dict) and f.get("link")]
    candidates.sort(key=lambda f: int(f.get("width", 0) or 0), reverse=True)
    for f in candidates:
        if int(f.get("width", 0) or 0) <= 1920:
            return f
    return candidates[0] if candidates else {}


def search_pexels_videos(
    query: str,
    aspect_ratio: str = "16:9",
    per_page: int = 5,
) -> list[BrollResult]:
    """Search Pexels for free stock video clips matching *query*.

    Parameters
    ----------
    query:
        Natural-language search string (e.g. ``"ancient Rome soldiers"``)
    aspect_ratio:
        ``"9:16"`` for vertical/portrait or ``"16:9"`` for horizontal/landscape.
    per_page:
        Maximum number of results to return (1–80).

    Returns
    -------
    list[BrollResult]
        Normalised results, empty list on any error.
    """
    api_key = get_secret("PEXELS_API_KEY", "").strip()
    if not api_key:
        logger.debug("Pexels search skipped: PEXELS_API_KEY not configured.")
        return []

    safe_query = str(query or "").strip()
    if not safe_query:
        return []

    orientation = _aspect_ratio_to_pexels_orientation(aspect_ratio)
    cache_key = f"pexels::{safe_query}::{orientation}::{per_page}"
    cached = _cache_get(cache_key)
    if cached is not None:
        logger.debug("Pexels cache hit for %r", safe_query)
        return cached

    params: dict[str, Any] = {
        "query": safe_query,
        "orientation": orientation,
        "per_page": min(max(1, int(per_page)), 80),
        "size": "medium",
    }
    headers = {"Authorization": api_key}

    try:
        response = requests.get(
            _PEXELS_VIDEO_SEARCH_URL,
            params=params,
            headers=headers,
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as exc:
        logger.warning("Pexels API request failed: %s", exc)
        return []
    except (ValueError, KeyError) as exc:
        logger.warning("Pexels API response parse error: %s", exc)
        return []

    results: list[BrollResult] = []
    for video in data.get("videos", []):
        try:
            vid_id = str(video.get("id", ""))
            duration = float(video.get("duration", 0) or 0)
            vid_files = video.get("video_files", [])
            best_file = _best_pexels_file(vid_files)
            if not best_file.get("link"):
                continue

            w = int(best_file.get("width", video.get("width", 0)) or 0)
            h = int(best_file.get("height", video.get("height", 0)) or 0)
            clip_orient = _clip_orientation(w, h)

            # Use the first picture as preview thumbnail
            preview_url = ""
            pictures = video.get("video_pictures", [])
            if pictures and isinstance(pictures[0], dict):
                preview_url = str(pictures[0].get("picture", "") or "")

            photographer = str(video.get("user", {}).get("name", "") or "")
            page_url = str(video.get("url", "") or "")

            results.append(BrollResult(
                provider="pexels",
                id=vid_id,
                title=f"Pexels #{vid_id}",
                duration_sec=duration,
                width=w,
                height=h,
                orientation=clip_orient,
                preview_image_url=preview_url,
                video_url=str(best_file["link"]),
                page_url=page_url,
                attribution_text=f"Video by {photographer} on Pexels" if photographer else "Pexels video",
                license_note="Pexels License – free for commercial and personal use, no attribution required.",
            ))
        except Exception as exc:
            logger.debug("Skipping Pexels video entry due to error: %s", exc)
            continue

    _cache_set(cache_key, results)
    logger.info("Pexels returned %d results for %r", len(results), safe_query)
    return results


# ---------------------------------------------------------------------------
# Pixabay
# ---------------------------------------------------------------------------

_PIXABAY_VIDEO_SEARCH_URL = "https://pixabay.com/api/videos/"


def _best_pixabay_file(hits_entry: dict) -> dict:
    """Return the best video file dict from a Pixabay video hit.

    Pixabay returns a ``videos`` key with sizes: large, medium, small, tiny.
    We prefer the largest size that is ≤ 1920px wide.
    """
    videos = hits_entry.get("videos", {})
    preference = ["large", "medium", "small", "tiny"]
    for size in preference:
        file_info = videos.get(size, {})
        if isinstance(file_info, dict) and file_info.get("url"):
            w = int(file_info.get("width", 0) or 0)
            if w <= 1920:
                return file_info
    # If nothing ≤ 1920, just return the first available size
    for size in preference:
        file_info = videos.get(size, {})
        if isinstance(file_info, dict) and file_info.get("url"):
            return file_info
    return {}


def search_pixabay_videos(
    query: str,
    aspect_ratio: str = "16:9",
    per_page: int = 5,
) -> list[BrollResult]:
    """Search Pixabay for free stock video clips matching *query*.

    Parameters
    ----------
    query:
        Natural-language search string.
    aspect_ratio:
        ``"9:16"`` or ``"16:9"``.
    per_page:
        Maximum number of results (3–200).

    Returns
    -------
    list[BrollResult]
        Normalised results, empty list on any error.
    """
    api_key = get_secret("PIXABAY_API_KEY", "").strip()
    if not api_key:
        logger.debug("Pixabay search skipped: PIXABAY_API_KEY not configured.")
        return []

    safe_query = str(query or "").strip()
    if not safe_query:
        return []

    want_orientation = _aspect_ratio_to_pixabay_orientation(aspect_ratio)
    cache_key = f"pixabay::{safe_query}::{want_orientation}::{per_page}"
    cached = _cache_get(cache_key)
    if cached is not None:
        logger.debug("Pixabay cache hit for %r", safe_query)
        return cached

    params: dict[str, Any] = {
        "key": api_key,
        "q": safe_query,
        "video_type": "all",
        "per_page": min(max(3, int(per_page * 3)), 200),  # over-fetch to allow orientation filter
        "safesearch": "true",
        "lang": "en",
    }

    try:
        response = requests.get(
            _PIXABAY_VIDEO_SEARCH_URL,
            params=params,
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
    except requests.exceptions.RequestException as exc:
        logger.warning("Pixabay API request failed: %s", exc)
        return []
    except (ValueError, KeyError) as exc:
        logger.warning("Pixabay API response parse error: %s", exc)
        return []

    results: list[BrollResult] = []
    for hit in data.get("hits", []):
        try:
            vid_id = str(hit.get("id", ""))
            duration = float(hit.get("duration", 0) or 0)
            tags = str(hit.get("tags", "") or "")
            page_url = str(hit.get("pageURL", "") or "")
            preview_url = str(hit.get("userImageURL", "") or "")
            user = str(hit.get("user", "") or "")

            best_file = _best_pixabay_file(hit)
            if not best_file.get("url"):
                continue

            w = int(best_file.get("width", 0) or 0)
            h = int(best_file.get("height", 0) or 0)
            clip_orient = _clip_orientation(w, h)

            # Client-side orientation filter
            if clip_orient != want_orientation:
                continue

            results.append(BrollResult(
                provider="pixabay",
                id=vid_id,
                title=tags or f"Pixabay #{vid_id}",
                duration_sec=duration,
                width=w,
                height=h,
                orientation=clip_orient,
                preview_image_url=preview_url,
                video_url=str(best_file["url"]),
                page_url=page_url,
                attribution_text=f"Video by {user} on Pixabay" if user else "Pixabay video",
                license_note="Pixabay Content License – free for commercial and personal use.",
            ))

            if len(results) >= per_page:
                break

        except Exception as exc:
            logger.debug("Skipping Pixabay hit due to error: %s", exc)
            continue

    _cache_set(cache_key, results)
    logger.info("Pixabay returned %d results (orientation=%s) for %r", len(results), want_orientation, safe_query)
    return results


# ---------------------------------------------------------------------------
# Unified search entry point
# ---------------------------------------------------------------------------

_DEFAULT_PROVIDER_PRIORITY = ["pexels", "pixabay"]


def search_broll(
    query: str,
    aspect_ratio: str = "16:9",
    per_page: int = 5,
    provider_priority: list[str] | None = None,
) -> list[BrollResult]:
    """Search for free B-roll video clips across configured providers.

    Tries each provider in *provider_priority* order and returns the first
    non-empty result list.  Falls back to the remaining providers if the
    preferred one returns nothing or is unconfigured.

    Parameters
    ----------
    query:
        Natural-language search string.
    aspect_ratio:
        ``"9:16"`` or ``"16:9"``.
    per_page:
        Maximum results per provider.
    provider_priority:
        Ordered list of provider names to try.  Defaults to
        ``["pexels", "pixabay"]``.

    Returns
    -------
    list[BrollResult]
        Combined results from the first successful provider, or empty list
        if all providers fail.
    """
    priority = [str(p).lower() for p in (provider_priority or _DEFAULT_PROVIDER_PRIORITY)]
    safe_query = str(query or "").strip()
    if not safe_query:
        return []

    _provider_fns = {
        "pexels": search_pexels_videos,
        "pixabay": search_pixabay_videos,
    }

    all_results: list[BrollResult] = []
    for provider_name in priority:
        fn = _provider_fns.get(provider_name)
        if fn is None:
            logger.warning("Unknown B-roll provider %r – skipping.", provider_name)
            continue
        try:
            results = fn(safe_query, aspect_ratio=aspect_ratio, per_page=per_page)
            if results:
                all_results.extend(results)
                if len(all_results) >= per_page:
                    break
        except Exception as exc:
            logger.warning("Provider %r failed: %s", provider_name, exc)
            continue

    return all_results[:per_page]
