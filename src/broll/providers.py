from __future__ import annotations

import logging
from typing import Any

import requests

from .config import get_pexels_api_key, get_pixabay_api_key
from .models import BrollResult

logger = logging.getLogger(__name__)
_REQUEST_TIMEOUT = 12

_PEXELS_VIDEO_SEARCH_URL = "https://api.pexels.com/videos/search"
_PIXABAY_VIDEO_SEARCH_URL = "https://pixabay.com/api/videos/"


class BrollProviderError(RuntimeError):
    pass


def _aspect_ratio_to_pexels_orientation(aspect_ratio: str) -> str:
    ratio = str(aspect_ratio or "16:9").strip()
    if ratio == "9:16":
        return "portrait"
    if ratio == "1:1":
        return "square"
    return "landscape"


def _is_vertical(width: int, height: int) -> bool:
    return int(height or 0) > int(width or 0)


def _orientation_matches(aspect_ratio: str, width: int, height: int) -> bool:
    ratio = str(aspect_ratio or "16:9").strip()
    if ratio == "9:16":
        return _is_vertical(width, height)
    return int(width or 0) >= int(height or 0)


def _best_pexels_file(video_files: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = []
    for file_info in video_files:
        if not isinstance(file_info, dict):
            continue
        if file_info.get("file_type") != "video/mp4":
            continue
        if not file_info.get("link"):
            continue
        candidates.append(file_info)
    if not candidates:
        return {}
    candidates.sort(key=lambda f: abs(int(f.get("width", 0) or 0) - 1280))
    return candidates[0]


def _best_pixabay_video(videos: dict[str, Any]) -> dict[str, Any]:
    for size in ("medium", "small", "tiny", "large"):
        candidate = videos.get(size, {}) if isinstance(videos, dict) else {}
        if isinstance(candidate, dict) and candidate.get("url"):
            return candidate
    return {}


def _http_error_message(provider: str, status_code: int) -> str:
    if status_code in (401, 403):
        return f"{provider} search failed: unauthorized."
    if status_code == 429:
        return f"{provider} search failed: rate limit reached."
    return f"{provider} search failed: HTTP {status_code}."


def search_pexels_videos(query: str, aspect_ratio: str, per_page: int = 5) -> list[BrollResult]:
    api_key = get_pexels_api_key().strip()
    if not api_key:
        raise BrollProviderError("Pexels API key not found in Streamlit secrets.")

    safe_query = str(query or "").strip()
    if not safe_query:
        return []

    params = {
        "query": safe_query,
        "per_page": max(1, min(int(per_page), 80)),
        "orientation": _aspect_ratio_to_pexels_orientation(aspect_ratio),
        "size": "medium",
    }
    headers = {"Authorization": api_key}

    logger.info("Pexels search query=%r orientation=%s per_page=%s", safe_query, params["orientation"], params["per_page"])
    try:
        response = requests.get(_PEXELS_VIDEO_SEARCH_URL, params=params, headers=headers, timeout=_REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise BrollProviderError(f"Pexels search failed: {exc}") from exc

    if response.status_code != 200:
        raise BrollProviderError(_http_error_message("Pexels", response.status_code))

    try:
        payload = response.json()
        videos = payload.get("videos", [])
    except Exception as exc:
        raise BrollProviderError("Pexels search failed: malformed response.") from exc

    results: list[BrollResult] = []
    for video in videos:
        if not isinstance(video, dict):
            continue
        selected = _best_pexels_file(video.get("video_files", []))
        if not selected:
            continue

        width = int(selected.get("width", video.get("width", 0)) or 0)
        height = int(selected.get("height", video.get("height", 0)) or 0)
        orientation = "vertical" if _is_vertical(width, height) else "horizontal"
        pictures = video.get("video_pictures", [])
        preview = pictures[0].get("picture", "") if pictures and isinstance(pictures[0], dict) else ""
        user = video.get("user", {}) if isinstance(video.get("user", {}), dict) else {}

        results.append(BrollResult(
            provider="pexels",
            id=str(video.get("id", "")),
            title=str(video.get("url", "") or f"Pexels {video.get('id', '')}"),
            duration_sec=float(video.get("duration", 0) or 0),
            width=width,
            height=height,
            orientation=orientation,
            preview_image_url=str(preview or ""),
            video_url=str(selected.get("link", "") or ""),
            page_url=str(video.get("url", "") or ""),
            attribution_text=(f"Video by {user.get('name', '')} on Pexels" if user.get("name") else "Video on Pexels"),
            license_note="Pexels License – free for commercial and personal use.",
        ))

    return results


def search_pixabay_videos(query: str, aspect_ratio: str, per_page: int = 5) -> list[BrollResult]:
    api_key = get_pixabay_api_key().strip()
    if not api_key:
        raise BrollProviderError("Pixabay API key not found in Streamlit secrets.")

    safe_query = str(query or "").strip()
    if not safe_query:
        return []

    params = {
        "key": api_key,
        "q": safe_query,
        "per_page": max(3, min(int(per_page * 3), 200)),
        "safesearch": "true",
    }

    logger.info("Pixabay search query=%r per_page=%s", safe_query, params["per_page"])
    try:
        response = requests.get(_PIXABAY_VIDEO_SEARCH_URL, params=params, timeout=_REQUEST_TIMEOUT)
    except requests.RequestException as exc:
        raise BrollProviderError(f"Pixabay search failed: {exc}") from exc

    if response.status_code != 200:
        raise BrollProviderError(_http_error_message("Pixabay", response.status_code))

    try:
        payload = response.json()
        hits = payload.get("hits", [])
    except Exception as exc:
        raise BrollProviderError("Pixabay search failed: malformed response.") from exc

    results: list[BrollResult] = []
    fallback: list[BrollResult] = []
    for hit in hits:
        if not isinstance(hit, dict):
            continue

        selected = _best_pixabay_video(hit.get("videos", {}))
        if not selected:
            continue
        width = int(selected.get("width", 0) or 0)
        height = int(selected.get("height", 0) or 0)
        orientation = "vertical" if _is_vertical(width, height) else "horizontal"
        item = BrollResult(
            provider="pixabay",
            id=str(hit.get("id", "")),
            title=str(hit.get("tags", "") or f"Pixabay {hit.get('id', '')}"),
            duration_sec=float(hit.get("duration", 0) or 0),
            width=width,
            height=height,
            orientation=orientation,
            preview_image_url=str(hit.get("videos", {}).get("tiny", {}).get("thumbnail", "") or ""),
            video_url=str(selected.get("url", "") or ""),
            page_url=str(hit.get("pageURL", "") or ""),
            attribution_text=(f"Video by {hit.get('user', '')} on Pixabay" if hit.get("user") else "Video on Pixabay"),
            license_note="Pixabay Content License – free for commercial and personal use.",
        )
        fallback.append(item)
        if _orientation_matches(aspect_ratio, width, height):
            results.append(item)
        if len(results) >= per_page:
            break

    if results:
        return results[:per_page]
    return fallback[:per_page]
