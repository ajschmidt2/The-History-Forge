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
  SUPABASE_VIDEO_BUCKET  — AI-generated videos (Veo / Sora)

Tables expected in Supabase Database
--------------------------------------
  projects   — project metadata (see SUPABASE_SETUP.md)
  assets     — per-project asset records
"""
from __future__ import annotations

import os
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Optional

from src.config import get_secret, get_supabase_config
from src.constants import SUPABASE_VIDEO_BUCKET

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_PLACEHOLDER_URLS = {"", "https://xxxxxxxxxxxx.supabase.co"}
_PLACEHOLDER_KEYS = {"", "your-anon-public-key", "your-anon-key-here"}

# Module-level cached client (one per Python process / Streamlit session).
_client = None


def _get_credentials() -> tuple[str, str]:
    cfg = get_supabase_config()
    url = str(cfg.get("url") or "").strip()
    key = str(cfg.get("key") or "").strip()
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


def create_video_job(
    *,
    openai_video_id: str,
    prompt: str,
    status: str,
    user_id: Optional[str] = None,
    bucket: str = SUPABASE_VIDEO_BUCKET,
) -> Optional[dict[str, Any]]:
    """Insert a row into ``video_jobs`` and return the inserted row.

    Returns ``None`` when Supabase is not configured or insertion fails.
    """
    sb = get_client()
    if sb is None:
        return None

    payload: dict[str, Any] = {
        "openai_video_id": openai_video_id,
        "prompt": prompt,
        "status": status,
        "bucket": bucket,
    }
    if user_id:
        payload["user_id"] = user_id

    try:
        resp = sb.table("video_jobs").insert(payload).execute()
        rows = resp.data or []
        return rows[0] if rows else None
    except Exception:
        return None


def get_video_job(job_id: str) -> Optional[dict[str, Any]]:
    """Return a ``video_jobs`` row by UUID string."""
    sb = get_client()
    if sb is None:
        return None
    try:
        resp = sb.table("video_jobs").select("*").eq("id", job_id).limit(1).execute()
        rows = resp.data or []
        return rows[0] if rows else None
    except Exception:
        return None


def update_video_job(job_id: str, updates: dict[str, Any]) -> Optional[dict[str, Any]]:
    """Update ``video_jobs`` row and return the updated payload."""
    sb = get_client()
    if sb is None:
        return None
    if not updates:
        return get_video_job(job_id)
    updates = dict(updates)
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    try:
        resp = sb.table("video_jobs").update(updates).eq("id", job_id).execute()
        rows = resp.data or []
        if rows:
            return rows[0]
    except Exception:
        return None
    return get_video_job(job_id)


def upload_video_bytes(
    *,
    bucket: str,
    storage_path: str,
    video_bytes: bytes,
    content_type: str = "video/mp4",
) -> Optional[str]:
    """Upload MP4 bytes to Supabase Storage and return public URL if available."""
    if not video_bytes:
        return None
    return _upload_bytes(bucket, storage_path, video_bytes, content_type)


def get_public_storage_url(bucket: str, storage_path: str) -> Optional[str]:
    """Return the public URL for a storage object path when available."""
    sb = get_client()
    if sb is None:
        return None
    try:
        return sb.storage.from_(bucket).get_public_url(storage_path)
    except Exception:
        return None


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


def _list_storage_objects(bucket: str, prefix: str) -> list[dict]:
    """Return object metadata under *prefix* for a bucket."""
    sb = get_client()
    if sb is None:
        return []

    normalized_prefix = str(prefix or "").strip("/")
    try:
        listing = sb.storage.from_(bucket).list(normalized_prefix)
    except Exception:
        return []

    if isinstance(listing, list):
        return [item for item in listing if isinstance(item, dict)]
    return []


def _download_storage_object(bucket: str, storage_path: str) -> Optional[bytes]:
    """Download a file from Supabase Storage and return bytes."""
    sb = get_client()
    if sb is None:
        return None
    try:
        payload = sb.storage.from_(bucket).download(storage_path)
    except Exception:
        return None
    if isinstance(payload, bytes):
        return payload
    return None


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


def upload_script(project_id: str, script_text: str, filename: str = "script.txt") -> Optional[str]:
    """Upload a script as plain text to ``history-forge-scripts`` and return the public URL.

    Returns None if Supabase is not configured or the upload fails.
    """
    if not script_text:
        return None
    data = script_text.encode("utf-8")
    storage_path = f"{project_id}/scripts/{filename}"
    url = _upload_bytes("history-forge-scripts", storage_path, data, "text/plain")
    if url:
        record_asset(project_id, "script", filename, url)
    return url


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




def upload_music(project_id: str, filename: str, music_path: Path) -> Optional[str]:
    """Upload a project music file and return the public URL."""
    if not music_path.exists():
        return None
    data = music_path.read_bytes()
    ext = music_path.suffix.lower()
    content_type = "audio/mpeg" if ext == ".mp3" else "audio/wav"
    storage_path = f"{project_id}/music/{filename}"
    url = _upload_bytes("history-forge-audio", storage_path, data, content_type)
    if url:
        record_asset(project_id, "music", filename, url)
    return url


def upload_shared_music(filename: str, music_path: Path) -> Optional[str]:
    """Upload a shared music-library track and return the public URL."""
    if not music_path.exists():
        return None
    data = music_path.read_bytes()
    ext = music_path.suffix.lower()
    content_type = "audio/mpeg" if ext == ".mp3" else "audio/wav"
    storage_path = f"music-library/{filename}"
    return _upload_bytes("history-forge-audio", storage_path, data, content_type)

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


def upload_generated_video(
    project_id: str,
    filename: str,
    video_bytes: bytes,
    prompt: str = "",
    provider: str = "",
) -> Optional[str]:
    """Upload an AI-generated video to the configured video bucket and return the public URL.

    Also records the asset in the ``assets`` table with asset_type
    ``generated_video`` and stores *prompt* / *provider* in the filename
    field as metadata (callers may pass a descriptive filename).

    Returns None if Supabase is not configured or the upload fails.
    """
    if not video_bytes:
        return None
    bucket = SUPABASE_VIDEO_BUCKET
    storage_path = f"{project_id}/{bucket}/{filename}"

    sb = get_client()
    if sb is None:
        return None

    try:
        sb.storage.from_(bucket).upload(
            storage_path,
            video_bytes,
            {"content-type": "video/mp4", "upsert": True},
        )
        public_url = sb.storage.from_(bucket).get_public_url(storage_path)
    except Exception:
        return None

    if public_url:
        record_asset(project_id, "generated_video", filename, public_url)
    return public_url


def record_generated_video_asset(*, project_id: str, public_url: str, prompt: str, provider: str) -> None:
    """Record a generated-video asset row using an existing public URL (no upload)."""
    sb = get_client()
    if sb is None:
        return

    safe_provider = str(provider or "unknown").strip().lower() or "unknown"
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    metadata_prompt = " ".join(str(prompt or "").split())[:80]
    safe_prompt = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in metadata_prompt).strip("_")
    suffix = f"_{safe_prompt}" if safe_prompt else ""
    filename = f"{safe_provider}_{stamp}{suffix}.mp4"

    payload = {
        "project_id": project_id,
        "asset_type": "generated_video",
        "filename": filename,
        "url": public_url,
        "provider": safe_provider,
        "prompt": prompt,
    }

    try:
        sb.table("assets").insert(payload).execute()
        return
    except Exception:
        pass

    # Fallback for schemas that do not yet include prompt/provider columns.
    record_asset(project_id, "generated_video", filename, public_url)


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


def pull_project_assets(project_id: str, project_dir: Path) -> dict[str, int]:
    """Download project assets from Supabase Storage.

    Returns counts of newly downloaded files keyed by asset type.
    """
    results = {"image": 0, "audio": 0, "video": 0, "music": 0, "generated_video": 0}
    if not project_id:
        return results

    targets = [
        ("image", "history-forge-images", f"{project_id}/images", project_dir / "assets" / "images"),
        ("audio", "history-forge-audio", f"{project_id}/audio", project_dir / "assets" / "audio"),
        ("video", "history-forge-videos", f"{project_id}/videos", project_dir / "assets" / "videos"),
        ("music", "history-forge-audio", f"{project_id}/music", project_dir / "assets" / "music"),
        ("generated_video", SUPABASE_VIDEO_BUCKET, f"{project_id}/{SUPABASE_VIDEO_BUCKET}", project_dir / "assets" / "videos"),
        ("generated_video", SUPABASE_VIDEO_BUCKET, f"{project_id}", project_dir / "assets" / "videos"),
    ]

    for asset_type, bucket, prefix, local_dir in targets:
        objects = _list_storage_objects(bucket, prefix)
        if not objects:
            continue
        local_dir.mkdir(parents=True, exist_ok=True)
        for obj in objects:
            name = str(obj.get("name") or "").strip()
            if not name or "/" in name:
                continue
            destination = local_dir / name
            if destination.exists() and destination.stat().st_size > 0:
                continue
            remote_path = f"{prefix}/{name}"
            payload = _download_storage_object(bucket, remote_path)
            if not payload:
                continue
            try:
                destination.write_bytes(payload)
            except OSError:
                continue
            results[asset_type] += 1

    shared_music_dir = project_dir.parent.parent / "music_library"
    shared_prefix = "music-library"
    objects = _list_storage_objects("history-forge-audio", shared_prefix)
    if objects:
        shared_music_dir.mkdir(parents=True, exist_ok=True)
        for obj in objects:
            name = str(obj.get("name") or "").strip()
            if not name or "/" in name:
                continue
            destination = shared_music_dir / name
            if destination.exists() and destination.stat().st_size > 0:
                continue
            payload = _download_storage_object("history-forge-audio", f"{shared_prefix}/{name}")
            if not payload:
                continue
            try:
                destination.write_bytes(payload)
            except OSError:
                continue

    return results


def list_all_bucket_videos(bucket: str = SUPABASE_VIDEO_BUCKET, limit: int = 100) -> list[dict[str, str]]:
    """List ALL video files in *bucket* by recursively scanning every subfolder.

    Unlike :func:`list_generated_videos`, this is **not** filtered by
    ``project_id``, so it surfaces every video that exists in the bucket
    (e.g. files stored under an ``anon/`` prefix or at the bucket root).
    """
    if not is_configured():
        return []

    rows: list[dict[str, str]] = []
    seen: set[str] = set()

    _VIDEO_EXTS = {".mp4", ".mov", ".webm", ".mkv"}

    def _collect(prefix: str, depth: int) -> None:
        if depth > 4 or len(rows) >= limit:
            return
        for obj in _list_storage_objects(bucket, prefix):
            name = str(obj.get("name") or "").strip()
            if not name:
                continue
            storage_path = f"{prefix}/{name}".lstrip("/") if prefix else name
            # Supabase returns id=None for folder placeholders
            if obj.get("id") is None:
                _collect(storage_path, depth + 1)
            else:
                if not any(name.lower().endswith(ext) for ext in _VIDEO_EXTS):
                    continue
                if storage_path in seen:
                    continue
                seen.add(storage_path)
                url = get_public_storage_url(bucket, storage_path)
                if not url:
                    continue
                rows.append(
                    {
                        "filename": name,
                        "url": url,
                        "object_path": storage_path,
                        "created_at": str(obj.get("created_at") or ""),
                    }
                )

    _collect("", 0)
    rows.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return rows[:limit]


# ---------------------------------------------------------------------------
# Effects-clip assignment helpers
# ---------------------------------------------------------------------------

def list_effects_clips(project_id: str) -> list[dict[str, str]]:
    """List rendered effects clips from the ``history-forge-videos`` bucket.

    Returns a list of dicts with keys: ``filename``, ``url``, ``storage_path``,
    ``created_at``.  Returns an empty list when Supabase is not configured or
    no clips are found.
    """
    if not project_id or not is_configured():
        return []

    prefix = f"{project_id}/effects_clips"
    rows: list[dict[str, str]] = []
    for obj in _list_storage_objects("history-forge-videos", prefix):
        name = str(obj.get("name") or "").strip()
        if not name or "/" in name:
            continue
        if not name.lower().endswith(".mp4"):
            continue
        storage_path = f"{prefix}/{name}"
        url = get_public_storage_url("history-forge-videos", storage_path)
        if not url:
            continue
        rows.append(
            {
                "filename": name,
                "url": url,
                "storage_path": storage_path,
                "created_at": str(obj.get("created_at") or ""),
            }
        )
    rows.sort(key=lambda r: r.get("created_at", ""), reverse=True)
    return rows


def save_clip_assignment(project_id: str, scene_num: int, clip_storage_path: str, clip_url: str) -> bool:
    """Persist a scene→clip assignment in the ``assets`` table.

    Uses ``asset_type="clip_assignment"`` and ``filename="s{scene_num:02d}"``
    as the unique key so each scene can hold at most one assignment.

    Returns True on success.
    """
    import logging as _log
    _logger = _log.getLogger(__name__)

    if not clip_url:
        _logger.warning("[supabase] save_clip_assignment: clip_url is empty for scene %d", scene_num)
        return False

    sb = get_client()
    if sb is None:
        _logger.warning("[supabase] save_clip_assignment: Supabase client unavailable for scene %d", scene_num)
        return False

    # Ensure the project row exists before writing to assets (FK constraint).
    try:
        sb.table("projects").upsert(
            {"id": project_id, "title": project_id},
            on_conflict="id",
        ).execute()
    except Exception as _proj_exc:
        _logger.warning("[supabase] save_clip_assignment: could not upsert project %r: %s", project_id, _proj_exc)

    try:
        sb.table("assets").upsert(
            {
                "project_id": project_id,
                "asset_type": "clip_assignment",
                "filename": f"s{scene_num:02d}",
                "url": clip_url,
            },
            on_conflict="project_id,asset_type,filename",
        ).execute()
        return True
    except Exception as exc:
        _logger.warning(
            "[supabase] save_clip_assignment failed for scene %d (project=%r): %s",
            scene_num, project_id, exc,
        )
        return False


def load_clip_assignments(project_id: str) -> dict[int, dict[str, str]]:
    """Return all clip assignments for *project_id*.

    Returns ``{scene_num: {"url": ..., "storage_path": ...}, ...}``.
    """
    sb = get_client()
    if sb is None:
        return {}
    if not project_id:
        return {}
    try:
        resp = (
            sb.table("assets")
            .select("filename, url")
            .eq("project_id", project_id)
            .eq("asset_type", "clip_assignment")
            .execute()
        )
    except Exception:
        return {}

    assignments: dict[int, dict[str, str]] = {}
    for row in resp.data or []:
        fname = str(row.get("filename") or "").strip()
        url = str(row.get("url") or "").strip()
        if not fname.startswith("s") or not url:
            continue
        try:
            scene_num = int(fname[1:])
        except ValueError:
            continue
        # Derive the actual clip filename from the URL so callers can resolve
        # the local file (e.g. "s01_effects.mp4"). The `filename` column stores
        # the scene key ("s01"), not the clip filename, so we extract it from
        # the storage URL path instead.
        clip_filename = Path(url).name if url else fname
        assignments[scene_num] = {"url": url, "filename": clip_filename}
    return assignments


def remove_clip_assignment(project_id: str, scene_num: int) -> bool:
    """Remove the clip assignment for *scene_num* in *project_id*.

    Returns True on success.
    """
    sb = get_client()
    if sb is None:
        return False
    try:
        sb.table("assets").delete().eq("project_id", project_id).eq(
            "asset_type", "clip_assignment"
        ).eq("filename", f"s{scene_num:02d}").execute()
        return True
    except Exception:
        return False


def list_generated_videos(project_id: str, limit: int = 25) -> list[dict[str, str]]:
    """List AI generated videos from the configured generated-videos bucket."""
    if not project_id:
        return []

    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    prefixes = [f"{project_id}/{SUPABASE_VIDEO_BUCKET}", f"{project_id}"]
    for prefix in prefixes:
        for obj in _list_storage_objects(SUPABASE_VIDEO_BUCKET, prefix):
            name = str(obj.get("name") or "").strip()
            if not name or "/" in name:
                continue
            storage_path = f"{prefix}/{name}"
            if storage_path in seen:
                continue
            seen.add(storage_path)
            url = get_public_storage_url(SUPABASE_VIDEO_BUCKET, storage_path)
            if not url:
                continue
            rows.append({"filename": name, "url": url, "object_path": storage_path, "created_at": str(obj.get("created_at") or "")})

    rows.sort(key=lambda item: item.get("created_at", ""), reverse=True)
    return rows[: max(1, int(limit or 25))]
