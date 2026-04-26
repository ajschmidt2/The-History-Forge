"""AI video generation service - Gemini/Veo video with optional fal.ai fallback.

The primary path uses Gemini Developer API Veo image-to-video. fal.ai remains
available as an optional fallback.

Public API
----------
  generate_video(prompt, provider, project_id, aspect_ratio, save_dir, seconds) -> (str, str | None)
      Returns a tuple of (public_url, local_path).  local_path is None when
      save_dir is not supplied or the write fails.

  VEO_ASPECT_RATIOS   â€” supported aspect ratios for Veo
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Optional

import src.supabase_storage as _sb_store
from src.config import get_secret
from src.services.fal_video_test import DEFAULT_FAL_VIDEO_MODEL, generate_fal_video_from_image
from src.services.google_veo_video import DEFAULT_GOOGLE_VIDEO_MODEL, generate_google_veo_lite_video

# ---------------------------------------------------------------------------
# Aspect-ratio constants exposed to the UI
# ---------------------------------------------------------------------------

VEO_ASPECT_RATIOS: list[str] = ["16:9", "9:16", "1:1"]
"""Aspect ratios supported by Google Veo."""


def veo_configured() -> bool:
    """Legacy Supabase-proxied Veo path has been removed from generative flows."""
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def generate_video(
    prompt: str,
    provider: str,
    project_id: str,
    aspect_ratio: str = "9:16",
    save_dir: Optional[Path | str] = None,
    seconds: int | str = 5,
    image_source: Optional[str | Path] = None,
) -> tuple[str, Optional[str]]:
    """Generate a video from *prompt* using *provider* and return ``(url, local_path)``.

    Parameters
    ----------
    prompt:
        The text description of the video to generate.
    provider:
        ``"google_veo_lite"`` for Gemini/Veo via the Developer API, or
        ``"falai"`` as fallback.
    project_id:
        The active History Forge project ID, used as a storage path prefix in
        Supabase and as the foreign key when recording the asset.
    aspect_ratio:
        Desired aspect ratio string such as ``"16:9"``, ``"9:16"``, or ``"1:1"``.
        Defaults to ``"16:9"`` when the value is unsupported by the chosen provider.
    save_dir:
        Optional directory path. When supplied, provider output is persisted as
        ``{save_dir}/{provider}_{short_id}.mp4``.
    seconds:
        Desired clip length for image-to-video providers.
    image_source:
        Optional source image for image-to-video providers. When omitted, the
        first generated scene image for the project is used if available.

    Returns
    -------
    tuple[str, str | None]
        ``(public_url, local_path)`` where *public_url* is the provider-backed
        URL or local path from the selected provider and *local_path* is the
        absolute path to the locally saved file (``None`` if not saved locally).

    Raises
    ------
    ValueError
        If *provider* is unrecognised or if the required credentials are missing.
    RuntimeError / TimeoutError
        If the provider API call fails or times out.
    """
    provider = (provider or "").strip().lower()
    if provider == "auto":
        provider = str(get_secret("HF_VIDEO_PROVIDER", "google_veo_lite") or "google_veo_lite").strip().lower()

    def _video_output_path(provider_name: str) -> Path:
        short_id = uuid.uuid4().hex[:8]
        target_dir = Path(save_dir) if save_dir is not None else Path("data/projects") / project_id / "assets/videos"
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir / f"{provider_name}_{short_id}.mp4"

    def _default_image_source() -> str:
        if image_source:
            return str(image_source)
        images_dir = Path("data/projects") / project_id / "assets/images"
        if not images_dir.exists():
            return ""
        images = sorted(images_dir.glob("s*.png")) or sorted(images_dir.glob("*.png"))
        return str(images[0]) if images else ""

    if provider == "google_veo_lite":
        dest = _video_output_path(provider)
        duration = max(1, min(12, int(seconds or 5)))
        result = generate_google_veo_lite_video(
            prompt=prompt,
            image_source=_default_image_source(),
            aspect_ratio=aspect_ratio,
            duration_seconds=duration,
            output_path=dest,
            debug_dir=Path("data/projects") / project_id / "debug",
            model=str(get_secret("HF_GOOGLE_VIDEO_MODEL", DEFAULT_GOOGLE_VIDEO_MODEL) or DEFAULT_GOOGLE_VIDEO_MODEL),
        )
        if not result.get("ok"):
            raise RuntimeError(str(result.get("error") or "Gemini video generation failed."))
        local_path = str(dest.resolve()) if dest.exists() else None
        public_url = str(result.get("video_url") or local_path or "")
        _sb_store.record_generated_video_asset(
            project_id=project_id,
            public_url=public_url,
            prompt=prompt,
            provider=provider,
        )
        return public_url, local_path

    if provider == "falai":
        dest = _video_output_path(provider)
        source = _default_image_source()
        if not source:
            raise RuntimeError("fal.ai video generation requires a generated scene image.")
        result = generate_fal_video_from_image(
            model=str(get_secret("fal_video_model", DEFAULT_FAL_VIDEO_MODEL) or DEFAULT_FAL_VIDEO_MODEL),
            prompt=prompt,
            image_source=source,
            output_path=dest,
            duration=max(1, min(12, int(seconds or 5))),
            aspect_ratio=aspect_ratio,
            fail_loud_missing_video_artifact=True,
        )
        if not result.get("ok"):
            raise RuntimeError(str(result.get("error") or "fal.ai video generation failed."))
        local_path = str(dest.resolve()) if dest.exists() else None
        public_url = str(result.get("video_url") or local_path or "")
        _sb_store.record_generated_video_asset(
            project_id=project_id,
            public_url=public_url,
            prompt=prompt,
            provider=provider,
        )
        return public_url, local_path

    raise ValueError(f"Unknown video provider '{provider}'. Use 'google_veo_lite' or 'falai'.")


