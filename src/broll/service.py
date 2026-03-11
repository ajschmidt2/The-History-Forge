"""High-level B-roll service functions used by the UI and automation pipeline.

These helpers abstract the per-scene lifecycle:
  1. Generate a search query from scene metadata
  2. Search providers for matching clips
  3. Download the selected clip to a canonical local path
  4. Assign the clip to the scene object

The functions are intentionally stateless and side-effect-free except for
``download_broll_asset``, which writes to disk.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

import requests

from .models import BrollResult
from .providers import search_broll

logger = logging.getLogger(__name__)

_DOWNLOAD_TIMEOUT = 120  # seconds


# ---------------------------------------------------------------------------
# Query generation
# ---------------------------------------------------------------------------

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
    """Extract the most meaningful keywords from a text string."""
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
    """Derive a B-roll search query from scene metadata.

    Priority order for the query source:
    1. ``scene.broll_query`` if already set (manual override)
    2. ``scene.visual_intent`` (describes what should be shown)
    3. ``scene.script_excerpt`` (narration text, used as fallback)

    Returns
    -------
    str
        A concise natural-language search query suitable for stock video APIs.
    """
    # Use existing manual override
    existing = str(getattr(scene, "broll_query", "") or "").strip()
    if existing:
        return existing

    # Prefer visual_intent as it describes what should appear on screen
    visual = str(getattr(scene, "visual_intent", "") or "").strip()
    if visual:
        keywords = _extract_keywords(visual, max_keywords=5)
        if keywords:
            return " ".join(keywords)

    # Fall back to script excerpt
    excerpt = str(getattr(scene, "script_excerpt", "") or "").strip()
    keywords = _extract_keywords(excerpt, max_keywords=4)
    return " ".join(keywords) if keywords else "historical documentary"


def search_broll_for_scene(
    scene: Any,
    aspect_ratio: str = "16:9",
    per_page: int = 5,
    provider_priority: list[str] | None = None,
) -> list[BrollResult]:
    """Search for B-roll clips matching a scene's visual intent.

    Parameters
    ----------
    scene:
        A Scene-like object with at least ``visual_intent`` and
        ``script_excerpt`` attributes.
    aspect_ratio:
        Target aspect ratio (``"9:16"`` or ``"16:9"``).
    per_page:
        Maximum number of results.
    provider_priority:
        Provider search order; defaults to ``["pexels", "pixabay"]``.

    Returns
    -------
    list[BrollResult]
    """
    query = generate_broll_query_for_scene(scene)
    if not query:
        return []
    return search_broll(
        query,
        aspect_ratio=aspect_ratio,
        per_page=per_page,
        provider_priority=provider_priority,
    )


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------

def download_broll_asset(
    project_id: str,
    scene_index: int,
    result: BrollResult,
) -> Path:
    """Download a B-roll clip to the canonical local path for a scene.

    The clip is stored at:
        ``data/projects/<project_id>/assets/broll/s<NN>_broll.mp4``

    If the file is already present (same URL cached locally on the result
    object), the download is skipped.

    Parameters
    ----------
    project_id:
        Active project identifier.
    scene_index:
        1-based scene index used to form the canonical filename.
    result:
        The BrollResult whose ``video_url`` will be downloaded.

    Returns
    -------
    Path
        Absolute path to the downloaded clip.

    Raises
    ------
    RuntimeError
        If the download fails for any reason.
    """
    broll_dir = Path("data/projects") / str(project_id) / "assets" / "broll"
    broll_dir.mkdir(parents=True, exist_ok=True)

    # Canonical naming: s01_broll.mp4, s02_broll.mp4, ...
    # The "s<NN>" prefix is required by the render pipeline's scene-ID assertions.
    dest = broll_dir / f"s{scene_index:02d}_broll.mp4"

    if dest.exists() and dest.stat().st_size > 0:
        # Already downloaded; trust that it's correct
        result.local_path = str(dest.resolve())
        return dest.resolve()

    video_url = str(result.video_url or "").strip()
    if not video_url:
        raise RuntimeError("BrollResult has no video_url to download.")

    logger.info(
        "Downloading B-roll for scene %02d from %s (%s) ...",
        scene_index,
        result.provider,
        video_url[:80],
    )

    try:
        with requests.get(video_url, stream=True, timeout=_DOWNLOAD_TIMEOUT) as resp:
            resp.raise_for_status()
            with dest.open("wb") as fh:
                for chunk in resp.iter_content(chunk_size=1 << 20):  # 1 MiB chunks
                    fh.write(chunk)
    except requests.exceptions.RequestException as exc:
        if dest.exists():
            dest.unlink(missing_ok=True)
        raise RuntimeError(f"Failed to download B-roll clip: {exc}") from exc

    result.local_path = str(dest.resolve())
    logger.info("B-roll scene_%02d saved → %s", scene_index, dest)
    return dest.resolve()


# ---------------------------------------------------------------------------
# Scene assignment
# ---------------------------------------------------------------------------

def assign_broll_to_scene(
    scene: Any,
    result: BrollResult,
    local_path: Path | str,
) -> None:
    """Write all B-roll fields onto a Scene object.

    This mutates *scene* in-place so the data is immediately available in
    session state and will be persisted on the next ``save_scenes()`` call.

    Parameters
    ----------
    scene:
        A mutable Scene-like object.
    result:
        The BrollResult that was selected.
    local_path:
        Path to the downloaded clip (as returned by ``download_broll_asset``).
    """
    scene.broll_query = generate_broll_query_for_scene(scene)
    scene.broll_provider = result.provider
    scene.broll_source_url = result.video_url
    scene.broll_page_url = result.page_url
    scene.broll_local_path = str(local_path)
    scene.broll_duration_sec = result.duration_sec
    scene.broll_orientation = result.orientation
    scene.use_broll = True


def clear_broll_from_scene(scene: Any) -> None:
    """Remove all B-roll assignments from a Scene object."""
    scene.broll_query = ""
    scene.broll_provider = ""
    scene.broll_source_url = ""
    scene.broll_page_url = ""
    scene.broll_local_path = ""
    scene.broll_duration_sec = 0.0
    scene.broll_orientation = ""
    scene.use_broll = False
