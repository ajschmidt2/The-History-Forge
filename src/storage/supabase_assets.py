from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

from src.video.timeline_schema import Timeline

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg"}

# Mapping rules between local-ish timeline paths and storage object paths.
# Override via env vars when your bucket layout differs.
LOCAL_PREFIX_IMAGES = os.getenv("LOCAL_PREFIX_IMAGES", "data/projects/{project}/assets/images/")
LOCAL_PREFIX_AUDIO = os.getenv("LOCAL_PREFIX_AUDIO", "data/projects/{project}/assets/audio/")
STORAGE_PREFIX_IMAGES = os.getenv("STORAGE_PREFIX_IMAGES", "{project}/")
STORAGE_PREFIX_AUDIO = os.getenv("STORAGE_PREFIX_AUDIO", "{project}/")


def _prefix(prefix_template: str, project_slug: str) -> str:
    return prefix_template.format(project=project_slug).replace("\\", "/")


def _sanitize_storage_path(storage_path: str) -> str:
    normalized = storage_path.replace("\\", "/").strip("/")
    parts: list[str] = []
    for part in normalized.split("/"):
        if not part or part in {".", ".."}:
            continue
        parts.append(part)
    if not parts:
        raise ValueError("Resolved storage object path is empty.")
    return "/".join(parts)


def get_supabase_client():
    url = (os.getenv("SUPABASE_URL") or "").strip()
    key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY") or os.getenv("SUPABASE_KEY") or "").strip()
    if not url or not key:
        raise RuntimeError("Supabase credentials are not configured (SUPABASE_URL + key required).")

    from supabase import create_client  # type: ignore

    return create_client(url, key)


def download_storage_object(bucket: str, object_path: str, dest: Path) -> Path:
    dest = dest.resolve()
    dest.parent.mkdir(parents=True, exist_ok=True)

    sb = get_supabase_client()
    payload = sb.storage.from_(bucket).download(object_path)
    if not isinstance(payload, bytes):
        raise RuntimeError(f"Failed to download storage://{bucket}/{object_path} (empty or invalid payload).")

    dest.write_bytes(payload)
    if not dest.exists() or dest.stat().st_size <= 0:
        raise RuntimeError(f"Downloaded file is missing or empty: {dest}")
    return dest


def _default_object_path_mapper(path_str: str, project_slug: str, is_audio: bool) -> str:
    normalized = path_str.replace("\\", "/")
    filename = Path(normalized).name

    if is_audio:
        local_prefix = _prefix(LOCAL_PREFIX_AUDIO, project_slug)
        storage_prefix = _prefix(STORAGE_PREFIX_AUDIO, project_slug)
    else:
        local_prefix = _prefix(LOCAL_PREFIX_IMAGES, project_slug)
        storage_prefix = _prefix(STORAGE_PREFIX_IMAGES, project_slug)

    if local_prefix in normalized:
        relative = normalized.split(local_prefix, 1)[1].lstrip("/")
        mapped = f"{storage_prefix.rstrip('/')}/{relative}" if relative else storage_prefix
        return _sanitize_storage_path(mapped)

    normalized_trimmed = normalized.lstrip("/")
    if normalized_trimmed.startswith(f"{project_slug}/"):
        return _sanitize_storage_path(normalized_trimmed)

    return _sanitize_storage_path(f"{storage_prefix.rstrip('/')}/{filename}")


def ensure_local_asset(
    path_str: str,
    staging_root: Path,
    bucket_images: str,
    bucket_audio: str | None,
    project_slug: str,
    object_path_mapper: Callable[[str, str, bool], str] | None = None,
) -> str:
    candidate = Path(path_str).expanduser()
    if candidate.exists():
        return str(candidate.resolve())

    ext = candidate.suffix.lower()
    is_audio = ext in AUDIO_EXTENSIONS
    if ext in IMAGE_EXTENSIONS:
        bucket = bucket_images
        category = "images"
    elif is_audio:
        bucket = bucket_audio or bucket_images
        category = "audio"
    else:
        bucket = bucket_images
        category = "misc"

    mapper = object_path_mapper or _default_object_path_mapper
    object_path = mapper(path_str, project_slug, is_audio)

    local_rel = Path(*object_path.split("/"))
    if any(part in {"..", ""} for part in local_rel.parts):
        raise ValueError(f"Unsafe storage object path: {object_path}")

    dest = (staging_root / category / local_rel).resolve()
    download_storage_object(bucket, object_path, dest)
    return str(dest)


def stage_timeline_assets(
    timeline: Timeline,
    staging_root: Path,
    project_slug: str,
    bucket_images: str = "images",
    bucket_audio: str | None = None,
) -> Timeline:
    staging_root = staging_root.resolve()
    staging_root.mkdir(parents=True, exist_ok=True)

    for scene in timeline.scenes:
        scene.image_path = ensure_local_asset(
            scene.image_path,
            staging_root=staging_root,
            bucket_images=bucket_images,
            bucket_audio=bucket_audio,
            project_slug=project_slug,
        )

    if timeline.meta.voiceover and timeline.meta.voiceover.path:
        timeline.meta.voiceover.path = ensure_local_asset(
            timeline.meta.voiceover.path,
            staging_root=staging_root,
            bucket_images=bucket_images,
            bucket_audio=bucket_audio,
            project_slug=project_slug,
        )

    if timeline.meta.music and timeline.meta.music.path:
        timeline.meta.music.path = ensure_local_asset(
            timeline.meta.music.path,
            staging_root=staging_root,
            bucket_images=bucket_images,
            bucket_audio=bucket_audio,
            project_slug=project_slug,
        )

    return timeline
