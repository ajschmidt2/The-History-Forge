"""
Historical image search — Wikimedia Commons, Library of Congress, Unsplash.

Usage:
    from src.research.image_search import search_image_for_scene, ImageResult

    result = search_image_for_scene(
        scene_title="Battle of Thermopylae",
        scene_description="Spartan warriors defend a narrow pass against the Persian army",
        topic="Ancient Greece",
        cache_dir=Path("data/projects/my-project/assets/images"),
    )
    if result:
        # result.local_path contains the downloaded PNG/JPEG path
        # result.source_url is the original page URL for attribution
        pass
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import quote_plus, urljoin

import requests
from PIL import Image

logger = logging.getLogger(__name__)

USER_AGENT = "TheHistoryForge/1.0 (historical-education; +https://github.com/local)"

# How long to wait between outbound requests (seconds) — be polite to APIs
_REQUEST_DELAY = 0.5

# Minimum acceptable image dimensions
_MIN_WIDTH = 300
_MIN_HEIGHT = 300

# Max file size for downloaded images (bytes)
_MAX_IMAGE_BYTES = 20 * 1024 * 1024  # 20 MB

_GENERIC_MEDIA_WORDS = {
    "history", "historical", "photo", "photograph", "image", "portrait", "people", "person", "woman",
    "man", "group", "crowd", "war", "world", "documentary", "scene", "figure", "story", "event",
}


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class ImageResult:
    """A successfully found and downloaded historical image."""

    local_path: Path
    """Absolute path to the locally cached/saved image file."""

    source_url: str
    """Page URL of the source (for attribution)."""

    image_url: str
    """Direct URL to the image file that was downloaded."""

    title: str
    """Human-readable title from the source platform."""

    provider: str
    """Which provider found this image: 'wikimedia', 'loc', or 'unsplash'."""

    license: str = ""
    """License string if known (e.g. 'CC BY-SA 4.0', 'Public Domain')."""

    match_score: float = 0.0
    """Estimated relevance score between 0 and 1 for this scene."""

    verification_notes: str = ""
    """Short note describing why this result was selected."""


def _keyword_terms(*parts: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z'-]{2,}", " ".join(str(part or "") for part in parts))
    seen: set[str] = set()
    selected: list[str] = []
    for word in words:
        lowered = word.lower()
        if lowered in seen or lowered in _GENERIC_MEDIA_WORDS:
            continue
        seen.add(lowered)
        selected.append(word)
    return selected


def _score_candidate_metadata(
    candidate: dict,
    *,
    scene_title: str,
    scene_description: str,
    topic: str,
    era: str,
    query: str,
    verification_level: str,
) -> tuple[float, str]:
    haystack = " ".join(
        [
            str(candidate.get("title", "") or ""),
            str(candidate.get("page_url", "") or ""),
            str(candidate.get("image_url", "") or ""),
        ]
    ).lower()
    if not haystack:
        return 0.0, "candidate_has_no_metadata"

    strong_terms = _keyword_terms(scene_title, scene_description, topic, era, query)
    if not strong_terms:
        strong_terms = _keyword_terms(query, scene_title)
    if not strong_terms:
        return 0.0, "no_strong_terms"

    matches: list[str] = []
    for term in strong_terms:
        lowered = term.lower()
        if lowered in haystack:
            matches.append(term)

    overlap = len(matches) / max(1, min(len(strong_terms), 6))
    phrase_bonus = 0.0
    for phrase in (query, scene_title, topic):
        cleaned = re.sub(r"\s+", " ", str(phrase or "")).strip().lower()
        if cleaned and len(cleaned) >= 8 and cleaned in haystack:
            phrase_bonus = max(phrase_bonus, 0.25)

    provider = str(candidate.get("provider", "") or "").lower()
    provider_bonus = 0.08 if provider in {"wikimedia", "loc"} else 0.02
    penalty = 0.0
    if verification_level == "strict" and len(matches) < 2:
        penalty += 0.2
    if "unsplash.com" in haystack and verification_level == "strict":
        penalty += 0.08

    score = max(0.0, min(1.0, overlap + phrase_bonus + provider_bonus - penalty))
    note = f"matched_terms={matches[:4]}" if matches else "metadata_match_weak"
    return score, note


# ---------------------------------------------------------------------------
# Query builder
# ---------------------------------------------------------------------------

def _build_queries(
    scene_title: str,
    scene_description: str,
    topic: str,
    era: str = "",
    query_hints: list[str] | None = None,
) -> list[str]:
    """
    Return a prioritised list of search queries to try, from most specific
    to most general.  We try specific first so we get the closest match.
    """
    parts: list[str] = []

    if query_hints:
        for hint in query_hints:
            cleaned_hint = re.sub(r"\s+", " ", str(hint or "")).strip()
            if cleaned_hint:
                parts.append(cleaned_hint)

    topic_clean = re.sub(r"\s+", " ", str(topic or "")).strip()
    generic_topic = any(
        token in topic_clean.lower()
        for token in (
            "history figure",
            "history event",
            "history story",
            "not well known",
            "little known",
            "unknown figure",
            "unknown story",
            "legendary",
        )
    )

    # 1. Topic + era (most context-specific)
    if topic_clean and era and not generic_topic:
        parts.append(f"{topic_clean} {era} historical")

    # 2. Scene title + topic
    if scene_title and topic_clean and not generic_topic:
        cleaned = re.sub(r"\s+", " ", scene_title).strip()
        parts.append(f"{cleaned} {topic_clean}")

    # 3. Scene title alone
    if scene_title:
        parts.append(re.sub(r"\s+", " ", scene_title).strip())

    # 4. Topic alone (broad fallback)
    if topic_clean and not generic_topic:
        parts.append(topic_clean)

    # 5. First noun phrase from description as last resort
    if scene_description:
        # grab first ~5 words
        words = scene_description.split()[:5]
        if words:
            parts.append(" ".join(words))

    # De-duplicate while preserving order
    seen: set[str] = set()
    result: list[str] = []
    for q in parts:
        q = q.strip()
        if q and q.lower() not in seen:
            seen.add(q.lower())
            result.append(q)
    return result


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

_session: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    global _session
    if _session is None:
        _session = requests.Session()
        _session.headers.update({"User-Agent": USER_AGENT})
    return _session


def _get_json(url: str, params: Optional[dict] = None, timeout: int = 10) -> Optional[dict]:
    """GET a URL and return parsed JSON, or None on any error."""
    try:
        r = _get_session().get(url, params=params, timeout=timeout)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        logger.debug("GET %s failed: %s", url, exc)
        return None


def _download_image_bytes(url: str, timeout: int = 20) -> Optional[bytes]:
    """Download raw bytes from a direct image URL.  Returns None on failure."""
    try:
        r = _get_session().get(url, timeout=timeout, stream=True)
        r.raise_for_status()
        chunks = []
        total = 0
        for chunk in r.iter_content(chunk_size=65536):
            total += len(chunk)
            if total > _MAX_IMAGE_BYTES:
                logger.debug("Image too large at %s", url)
                return None
            chunks.append(chunk)
        return b"".join(chunks)
    except Exception as exc:
        logger.debug("Download failed %s: %s", url, exc)
        return None


def _normalize_image_bytes(raw: bytes) -> Optional[bytes]:
    """
    Validate and normalise to PNG.  Returns None if the image is too small
    or not a recognised image format.
    """
    if not raw:
        return None
    try:
        with Image.open(io.BytesIO(raw)) as img:
            w, h = img.size
            if w < _MIN_WIDTH or h < _MIN_HEIGHT:
                return None
            fmt = (img.format or "").upper()
            if fmt not in {"PNG", "JPEG", "JPG", "WEBP", "GIF", "BMP", "TIFF"}:
                return None
            mode = "RGBA" if fmt == "PNG" else "RGB"
            out = io.BytesIO()
            img.convert(mode).save(out, format="PNG", optimize=True)
            return out.getvalue()
    except Exception:
        return None


def _save_image(image_bytes: bytes, dest: Path) -> bool:
    """Write image bytes to dest.  Returns True on success."""
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(image_bytes)
        return True
    except OSError as exc:
        logger.debug("Could not save image to %s: %s", dest, exc)
        return False


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _query_hash(query: str, provider: str) -> str:
    h = hashlib.sha256(f"{provider}:{query.strip().lower()}".encode()).hexdigest()[:12]
    return h


def _read_cache(cache_dir: Path, cache_key: str) -> Optional[dict]:
    p = cache_dir / "search_cache" / f"{cache_key}.json"
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None


def _write_cache(cache_dir: Path, cache_key: str, data: dict) -> None:
    p = cache_dir / "search_cache" / f"{cache_key}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Wikimedia Commons
# ---------------------------------------------------------------------------

_WIKIMEDIA_API = "https://commons.wikimedia.org/w/api.php"
_WIKIMEDIA_PAGE = "https://commons.wikimedia.org/wiki/File:"


def _wikimedia_search(query: str, limit: int = 5) -> list[dict]:
    """
    Search Wikimedia Commons for images matching *query*.
    Returns a list of dicts with keys: title, image_url, page_url, license.
    """
    params = {
        "action": "query",
        "list": "search",
        "srsearch": f"{query} filetype:bitmap",
        "srnamespace": "6",  # File namespace
        "srlimit": limit,
        "format": "json",
    }
    data = _get_json(_WIKIMEDIA_API, params=params)
    if not data:
        return []

    titles = [item["title"] for item in data.get("query", {}).get("search", [])]
    if not titles:
        return []

    # Fetch image info for each title
    info_params = {
        "action": "query",
        "titles": "|".join(titles),
        "prop": "imageinfo",
        "iiprop": "url|extmetadata",
        "format": "json",
    }
    info = _get_json(_WIKIMEDIA_API, params=info_params)
    if not info:
        return []

    results = []
    for page in info.get("query", {}).get("pages", {}).values():
        ii_list = page.get("imageinfo", [])
        if not ii_list:
            continue
        ii = ii_list[0]
        img_url = ii.get("url", "")
        if not img_url:
            continue

        # Skip SVG / PDF
        low = img_url.lower()
        if any(low.endswith(ext) for ext in (".svg", ".pdf", ".ogg", ".ogv", ".webm")):
            continue

        meta = ii.get("extmetadata", {})
        license_str = (
            meta.get("LicenseShortName", {}).get("value", "")
            or meta.get("License", {}).get("value", "")
        )
        title = page.get("title", "").replace("File:", "").strip()
        page_url = _WIKIMEDIA_PAGE + quote_plus(title.replace(" ", "_"))

        results.append({
            "title": title,
            "image_url": img_url,
            "page_url": page_url,
            "license": license_str,
        })

    return results


# ---------------------------------------------------------------------------
# Library of Congress
# ---------------------------------------------------------------------------

_LOC_SEARCH = "https://www.loc.gov/search/"
_LOC_API = "https://www.loc.gov/photos/"


def _loc_search(query: str, limit: int = 5) -> list[dict]:
    """
    Search the Library of Congress photo collection.
    Returns list of dicts with keys: title, image_url, page_url, license.
    """
    params = {
        "q": query,
        "fo": "json",
        "c": limit,
        "at": "results",
    }
    data = _get_json(_LOC_API, params=params)
    if not data:
        return []

    results_raw = data.get("results", [])
    results = []
    for item in results_raw[:limit]:
        # Try to get a usable image URL
        image_url = ""
        # Prefer medium-sized jpeg from the image_url list
        for candidate in item.get("image_url", []):
            c_low = candidate.lower()
            if c_low.endswith(".jpg") or c_low.endswith(".jpeg"):
                image_url = candidate
                break
        if not image_url:
            # Try online_format based links
            links = item.get("links", {})
            for link_type in ("fulltext", "related"):
                v = links.get(link_type, "")
                if isinstance(v, str) and v.startswith("http"):
                    break

        if not image_url:
            continue

        title = item.get("title", "Library of Congress photo")
        page_url = item.get("url", "") or item.get("id", "")
        if page_url and not page_url.startswith("http"):
            page_url = "https://www.loc.gov" + page_url

        results.append({
            "title": title,
            "image_url": image_url,
            "page_url": page_url,
            "license": "Public Domain",  # LOC digitized photos are generally PD
        })

    return results


# ---------------------------------------------------------------------------
# Unsplash
# ---------------------------------------------------------------------------

_UNSPLASH_SEARCH = "https://api.unsplash.com/search/photos"


def _unsplash_search(query: str, access_key: str, limit: int = 5) -> list[dict]:
    """
    Search Unsplash for photos matching *query*.
    Requires a valid UNSPLASH_ACCESS_KEY.
    Returns list of dicts with keys: title, image_url, page_url, license.
    """
    if not access_key:
        return []
    params = {
        "query": query,
        "per_page": limit,
        "orientation": "portrait",
    }
    headers = {"Authorization": f"Client-ID {access_key}"}
    try:
        r = _get_session().get(_UNSPLASH_SEARCH, params=params, headers=headers, timeout=10)
        r.raise_for_status()
        data = r.json()
    except Exception as exc:
        logger.debug("Unsplash search failed: %s", exc)
        return []

    results = []
    for photo in data.get("results", [])[:limit]:
        urls = photo.get("urls", {})
        image_url = urls.get("regular") or urls.get("full") or urls.get("small", "")
        if not image_url:
            continue
        user = photo.get("user", {})
        attribution = user.get("name", "Unsplash photographer")
        page_url = photo.get("links", {}).get("html", "https://unsplash.com")
        alt = photo.get("alt_description") or photo.get("description") or query
        results.append({
            "title": f"{alt} (photo by {attribution})",
            "image_url": image_url,
            "page_url": page_url,
            "license": "Unsplash License",
        })

    return results


# ---------------------------------------------------------------------------
# Main per-scene search entry point
# ---------------------------------------------------------------------------

def search_image_for_scene(
    scene_title: str,
    scene_description: str = "",
    topic: str = "",
    era: str = "",
    scene_index: int = 0,
    cache_dir: Optional[Path] = None,
    unsplash_access_key: str = "",
    providers: tuple[str, ...] = ("wikimedia", "loc", "unsplash"),
    prefer_portrait: bool = True,
    query_hints: list[str] | None = None,
    verification_level: str = "standard",
) -> Optional[ImageResult]:
    """
    Search for a real historical image relevant to a scene.

    Tries each *provider* in order until a usable image is found and
    successfully downloaded.  The image is saved to *cache_dir* if given,
    otherwise to ``data/image_search_cache``.

    Args:
        scene_title: Short title of the scene (used as primary search term).
        scene_description: Narration text / longer description for fallback queries.
        topic: Overall topic / subject of the video (e.g. "Julius Caesar").
        era: Historical era string (e.g. "Roman Republic, 1st century BC").
        scene_index: Zero-based scene index, used for the output filename.
        cache_dir: Directory to save the downloaded image in.
        unsplash_access_key: Unsplash API key (required for Unsplash provider).
        providers: Ordered tuple of providers to try.
        prefer_portrait: Whether to prefer portrait-orientation images (9:16 videos).

    Returns:
        An :class:`ImageResult` if an image was found and downloaded, else None.
    """
    if cache_dir is None:
        cache_dir = Path("data/image_search_cache")

    queries = _build_queries(scene_title, scene_description, topic, era, query_hints=query_hints)
    if not queries:
        return None

    provider_fns = {
        "wikimedia": lambda q: _wikimedia_search(q, limit=5),
        "loc": lambda q: _loc_search(q, limit=5),
        "unsplash": lambda q: _unsplash_search(q, unsplash_access_key, limit=5),
    }

    ranked_candidates: list[tuple[float, int, int, str, str, dict, str]] = []
    min_score = 0.0 if verification_level == "off" else (0.3 if verification_level == "standard" else 0.48)

    for provider_index, provider in enumerate(providers):
        fn = provider_fns.get(provider)
        if fn is None:
            continue
        if provider == "unsplash" and not unsplash_access_key:
            continue

        for query_index, query in enumerate(queries):
            cache_key = _query_hash(query, provider)
            cached = _read_cache(cache_dir, cache_key)
            if cached:
                candidates = cached.get("candidates", [])
            else:
                time.sleep(_REQUEST_DELAY)
                try:
                    candidates = fn(query)
                except Exception as exc:
                    logger.debug("Provider %s query %r failed: %s", provider, query, exc)
                    candidates = []
                _write_cache(cache_dir, cache_key, {"candidates": candidates})

            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                candidate = dict(candidate)
                candidate["provider"] = provider
                score, note = _score_candidate_metadata(
                    candidate,
                    scene_title=scene_title,
                    scene_description=scene_description,
                    topic=topic,
                    era=era,
                    query=query,
                    verification_level=verification_level,
                )
                if score < min_score:
                    continue
                img_url = candidate.get("image_url", "")
                if not img_url:
                    continue
                ranked_candidates.append((score, -provider_index, -query_index, provider, query, candidate, note))

    ranked_candidates.sort(reverse=True)
    for score, _provider_order, _query_order, provider, query, candidate, note in ranked_candidates[:8]:
        img_url = str(candidate.get("image_url", "") or "").strip()
        if not img_url:
            continue

        raw = _download_image_bytes(img_url)
        if not raw:
            continue

        normalized = _normalize_image_bytes(raw)
        if not normalized:
            continue

        dest_name = f"search_s{scene_index:02d}_{provider}.png"
        dest = cache_dir / dest_name
        if not _save_image(normalized, dest):
            continue

        return ImageResult(
            local_path=dest,
            source_url=str(candidate.get("page_url", img_url) or img_url),
            image_url=img_url,
            title=str(candidate.get("title", query) or query),
            provider=provider,
            license=str(candidate.get("license", "") or ""),
            match_score=score,
            verification_notes=note,
        )

    return None


def search_images_for_scenes(
    scenes: list[dict],
    topic: str = "",
    era: str = "",
    cache_dir: Optional[Path] = None,
    unsplash_access_key: str = "",
    providers: tuple[str, ...] = ("wikimedia", "loc", "unsplash"),
    verification_level: str = "standard",
) -> dict[int, ImageResult]:
    """
    Batch search for all scenes.

    Args:
        scenes: List of dicts with at least ``index``, ``title``, and
                optionally ``description``/``narration`` keys.
        topic: Overall video topic.
        era: Historical era string.
        cache_dir: Shared directory for downloaded images.
        unsplash_access_key: Unsplash API key.
        providers: Ordered list of providers to try per scene.

    Returns:
        Dict mapping scene index → :class:`ImageResult` for scenes where
        an image was found.  Scenes with no result are absent from the dict.
    """
    results: dict[int, ImageResult] = {}
    for scene in scenes:
        idx = int(scene.get("index", 0))
        title = str(scene.get("title", "") or "")
        desc = str(scene.get("description", "") or scene.get("narration", "") or "")
        result = search_image_for_scene(
            scene_title=title,
            scene_description=desc,
            topic=topic,
            era=era,
            scene_index=idx,
            cache_dir=cache_dir,
            unsplash_access_key=unsplash_access_key,
            providers=providers,
            query_hints=scene.get("query_hints") if isinstance(scene.get("query_hints"), list) else None,
            verification_level=verification_level,
        )
        if result:
            results[idx] = result

    return results
