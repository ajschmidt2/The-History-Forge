"""Clip thumbnail extraction helpers.

Extracts a single preview frame from an MP4 clip (at ~0.5 s) using FFmpeg,
uploads the PNG to Supabase Storage, and returns a cacheable public URL.

Usage::

    from src.video.clip_thumbnail import get_clip_thumbnail_url
    thumb_url = get_clip_thumbnail_url(clip_url, project_id, clip_filename)
"""
from __future__ import annotations

import logging
import subprocess
import tempfile
from pathlib import Path
from typing import Optional
from urllib.request import urlopen

log = logging.getLogger(__name__)


def _run_ffmpeg_extract(input_path: str, output_path: str, seek_sec: float = 0.5) -> bool:
    """Extract a single frame from *input_path* at *seek_sec* seconds."""
    cmd = [
        "ffmpeg", "-y",
        "-ss", f"{seek_sec:.2f}",
        "-i", input_path,
        "-frames:v", "1",
        "-q:v", "2",
        output_path,
    ]
    try:
        result = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            timeout=30,
        )
        return result.returncode == 0 and Path(output_path).exists()
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        log.warning("[clip_thumbnail] FFmpeg extract failed: %s", exc)
        return False


def get_clip_thumbnail_url(
    clip_url: str,
    project_id: str,
    clip_filename: str,
) -> Optional[str]:
    """Return a public URL for a thumbnail image extracted from *clip_url*.

    Steps:
    1. Download the clip (if it's a remote URL) to a temp file.
    2. Extract a frame at 0.5 s with FFmpeg.
    3. Upload the PNG to ``history-forge-images/<project_id>/thumbnails/``.
    4. Return the public URL.

    Returns ``None`` when any step fails, so callers can fall back gracefully.
    """
    import src.supabase_storage as _sb

    if not clip_url or not project_id or not clip_filename:
        return None

    # ── Check if already uploaded ──────────────────────────────────────────
    thumb_name = Path(clip_filename).stem + "_thumb.png"
    storage_path = f"{project_id}/thumbnails/{thumb_name}"
    existing_url = _sb.get_public_storage_url("history-forge-images", storage_path)
    # Supabase always returns a URL even for missing objects, so we cannot check
    # existence cheaply.  We'll just re-extract if the caller needs it.

    # ── Download clip to temp file (if remote) ─────────────────────────────
    with tempfile.TemporaryDirectory(prefix="hf_thumb_") as tmp_dir:
        tmp = Path(tmp_dir)

        clip_url_str = str(clip_url or "").strip()
        if clip_url_str.startswith(("http://", "https://")):
            local_clip = tmp / "clip.mp4"
            try:
                with urlopen(clip_url_str) as resp:
                    local_clip.write_bytes(resp.read())
            except Exception as exc:
                log.warning("[clip_thumbnail] failed to download clip %r: %s", clip_url_str, exc)
                return None
            input_path = str(local_clip)
        elif Path(clip_url_str).exists():
            input_path = clip_url_str
        else:
            log.warning("[clip_thumbnail] clip not found locally: %r", clip_url_str)
            return None

        thumb_path = tmp / thumb_name
        ok = _run_ffmpeg_extract(input_path, str(thumb_path))
        if not ok or not thumb_path.exists():
            log.warning("[clip_thumbnail] frame extraction failed for %r", clip_filename)
            return None

        # ── Upload thumbnail ───────────────────────────────────────────────
        try:
            thumb_bytes = thumb_path.read_bytes()
        except OSError as exc:
            log.warning("[clip_thumbnail] could not read thumb file: %s", exc)
            return None

        url = _sb._upload_bytes(
            "history-forge-images",
            storage_path,
            thumb_bytes,
            "image/png",
        )
        if url:
            log.info("[clip_thumbnail] uploaded thumbnail for %r → %s", clip_filename, url)
        return url
