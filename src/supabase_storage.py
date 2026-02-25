"""Supabase cloud storage helpers.

Provides a thin wrapper around the Supabase Python client for uploading
stories (project metadata), images, and voiceover audio files.  All
functions degrade gracefully when Supabase credentials are not configured
— callers receive ``None`` / ``False`` instead of exceptions, and local
SQLite storage continues to work as a fallback.

Buckets expected in Supabase Storage
-------------------------------------
  history-forge-images   — generated scene images
  history-forge-audio    — voiceover / music files
  history-forge-videos   — rendered video exports

Tables expected in Supabase Database
--------------------------------------
  projects   — project metadata (see SUPABASE_SETUP.md)
  assets     — per-project asset records
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from src.config import get_secret

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_PLACEHOLDER_URLS = {"", "https://xxxxxxxxxxxx.supabase.co"}
_PLACEHOLDER_KEYS = {"", "your-anon-public-key", "your-anon-key-here"}

# Module-level cached client (one per Python process / Streamlit session).
_client = None


def _get_credentials() -> tuple[str, str]:
    url = get_secret("SUPABASE_URL").strip()
    key = get_secret("SUPABASE_KEY").strip()
    return url, key


def is_configured() -> bool:
    """Return True when valid (non-placeholder) Supabase credentials exist."""
    url, key = _get_credentials()
    return (
        bool(url)
        and url not in _PLACEHOLDER_URLS
        and bool(key)
        and key not in _PLACEHOLDER_KEYS
    )


def get_client():
    """Return a cached Supabase client, or None if not configured."""
    global _client
    if _client is not None:
        return _client
    if not is_configured():
        return None
    url, key = _get_credentials()
    try:
        from supabase import create_client  # type: ignore

        _client = create_client(url, key)
        return _client
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Database operations
# ---------------------------------------------------------------------------

def upsert_project(project_id: str, title: str) -> bool:
    """Upsert a project record into Supabase.  Returns True on success."""
    sb = get_client()
    if sb is None:
        return False
    try:
        sb.table("projects").upsert(
            {"id": project_id, "title": title},
            on_conflict="id",
        ).execute()
        return True
    except Exception:
        return False


def record_asset(
    project_id: str,
    asset_type: str,
    filename: str,
    url: str,
) -> bool:
    """Insert an asset record into Supabase.  Returns True on success."""
    sb = get_client()
    if sb is None:
        return False
    try:
        sb.table("assets").upsert(
            {
                "project_id": project_id,
                "asset_type": asset_type,
                "filename": filename,
                "url": url,
            },
            on_conflict="project_id,asset_type,filename",
        ).execute()
        return True
    except Exception:
        return False


def list_projects() -> list[dict]:
    """Return all projects from Supabase, newest first."""
    sb = get_client()
    if sb is None:
        return []
    try:
        resp = sb.table("projects").select("*").order("created_at", desc=True).execute()
        return resp.data or []
    except Exception:
        return []


# ---------------------------------------------------------------------------
# Storage (file upload) operations
# ---------------------------------------------------------------------------

def _upload_bytes(
    bucket: str,
    storage_path: str,
    data: bytes,
    content_type: str,
) -> Optional[str]:
    """Upload *data* to a Supabase Storage bucket and return the public URL."""
    sb = get_client()
    if sb is None:
        return None
    try:
        sb.storage.from_(bucket).upload(
            storage_path,
            data,
            {"content-type": content_type, "upsert": "true"},
        )
        return sb.storage.from_(bucket).get_public_url(storage_path)
    except Exception:
        return None


def upload_image(project_id: str, filename: str, image_path: Path) -> Optional[str]:
    """Upload a scene image to ``history-forge-images`` and return the public URL.

    Returns None if Supabase is not configured or the upload fails.
    """
    if not image_path.exists():
        return None
    data = image_path.read_bytes()
    ext = image_path.suffix.lower()
    content_type = "image/png" if ext == ".png" else "image/jpeg"
    storage_path = f"{project_id}/images/{filename}"
    url = _upload_bytes("history-forge-images", storage_path, data, content_type)
    if url:
        record_asset(project_id, "image", filename, url)
    return url


def upload_audio(project_id: str, filename: str, audio_path: Path) -> Optional[str]:
    """Upload a voiceover / music file to ``history-forge-audio`` and return the public URL.

    Returns None if Supabase is not configured or the upload fails.
    """
    if not audio_path.exists():
        return None
    data = audio_path.read_bytes()
    ext = audio_path.suffix.lower()
    content_type = "audio/mpeg" if ext == ".mp3" else "audio/wav"
    storage_path = f"{project_id}/audio/{filename}"
    url = _upload_bytes("history-forge-audio", storage_path, data, content_type)
    if url:
        record_asset(project_id, "audio", filename, url)
    return url


def upload_video(project_id: str, filename: str, video_path: Path) -> Optional[str]:
    """Upload a rendered video to ``history-forge-videos`` and return the public URL.

    Returns None if Supabase is not configured or the upload fails.
    """
    if not video_path.exists():
        return None
    data = video_path.read_bytes()
    storage_path = f"{project_id}/videos/{filename}"
    url = _upload_bytes("history-forge-videos", storage_path, data, "video/mp4")
    if url:
        record_asset(project_id, "video", filename, url)
    return url


def sync_project_assets(project_id: str, project_dir: Path) -> dict[str, list[str]]:
    """Upload all local assets for *project_id* to Supabase.

    Scans the project directory for images, audio, and video files and
    uploads any that have not yet been pushed.  Returns a dict mapping
    asset type to list of uploaded public URLs.
    """
    results: dict[str, list[str]] = {"image": [], "audio": [], "video": []}

    images_dir = project_dir / "assets" / "images"
    audio_dir = project_dir / "assets" / "audio"
    renders_dir = project_dir / "renders"

    if images_dir.exists():
        for f in sorted(images_dir.iterdir()):
            if f.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}:
                url = upload_image(project_id, f.name, f)
                if url:
                    results["image"].append(url)

    if audio_dir.exists():
        for f in sorted(audio_dir.iterdir()):
            if f.suffix.lower() in {".mp3", ".wav", ".ogg", ".m4a"}:
                url = upload_audio(project_id, f.name, f)
                if url:
                    results["audio"].append(url)

    if renders_dir.exists():
        for f in sorted(renders_dir.iterdir()):
            if f.suffix.lower() in {".mp4", ".mov", ".webm"}:
                url = upload_video(project_id, f.name, f)
                if url:
                    results["video"].append(url)

    return results
