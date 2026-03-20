"""
src/video/ai_video_clips.py

Animates the first and middle generated scene images into true AI video clips.
Supports two providers:
  - veo:  Google Veo 2 image-to-video via the veo-image-to-video Supabase Edge Function
  - sora: OpenAI Sora text-to-video (with optional image reference fallback)

Provider is selected at call time via the `provider` argument.
"""

import base64
import json
import logging
import requests
from pathlib import Path
from typing import Optional

from src.config import get_secret, get_openai_config
from src.ai_video_generation import (
    sora_configured,
    veo_configured,
    _SORA_SIZE_MAP,
    _SORA_CREATE_URL,
    _sora_headers,
    poll_video,
    get_video_content,
    _MAX_POLLS,
    _POLL_INTERVAL_S,
)

logger = logging.getLogger(__name__)

POLL_TIMEOUT_SEC = 300
SUPPORTED_PROVIDERS = ("veo", "sora")


# ---------------------------------------------------------------------------
# Scene asset helpers
# ---------------------------------------------------------------------------

def _find_scene_images(project_id: str) -> list[Path]:
    """Return sorted generated scene images for this project."""
    base = Path("data/projects") / project_id / "assets" / "images"
    if not base.exists():
        return []
    images = sorted(base.glob("s*.png"))
    if not images:
        images = sorted(base.glob("*.png"))
    return images


def _find_scene_prompts(project_id: str) -> list[str]:
    """Return image prompts in scene order from scenes.json."""
    scenes_path = Path("data/projects") / project_id / "scenes.json"
    if not scenes_path.exists():
        return []
    try:
        scenes = json.loads(scenes_path.read_text())
        return [s.get("image_prompt") or s.get("prompt") or "" for s in scenes]
    except Exception:
        return []


def _build_motion_prompt(image_prompt: str) -> str:
    base = image_prompt.strip().rstrip(".")
    return (
        f"{base}. Animate with natural cinematic motion — elements move realistically, "
        "atmosphere shifts, light and shadow animate across the scene. "
        "Dramatic documentary style, historically immersive, slow deliberate movement."
    )


# ---------------------------------------------------------------------------
# Veo: image-to-video via Supabase Edge Function
# ---------------------------------------------------------------------------

def _call_veo_image_to_video(
    image_path: Path,
    prompt: str,
    aspect_ratio: str = "9:16",
    duration_seconds: int = 5,
) -> bytes | None:
    """
    Send an image to the veo-image-to-video Edge Function.
    Returns raw video bytes on success, None on non-critical failure.
    Raises RuntimeError on configuration or deployment errors.
    """
    supabase_url = get_secret("SUPABASE_URL")
    supabase_key = get_secret("SUPABASE_KEY") or get_secret("SUPABASE_ANON_KEY")

    if not supabase_url or not supabase_key:
        raise RuntimeError(
            "ai_video_clips: SUPABASE_URL and SUPABASE_KEY are required but not configured."
        )

    if not image_path.exists():
        logger.warning(f"ai_video_clips: image not found at {image_path}, skipping clip")
        return None

    image_bytes = image_path.read_bytes()
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    mime_type = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"

    endpoint = f"{supabase_url.rstrip('/')}/functions/v1/veo-image-to-video"
    headers = {
        "Authorization": f"Bearer {supabase_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "prompt": prompt,
        "image_base64": image_b64,
        "image_mime_type": mime_type,
        "aspect_ratio": aspect_ratio,
        "duration_seconds": duration_seconds,
    }

    logger.info(
        f"ai_video_clips [veo]: calling {endpoint} "
        f"(image={image_path.name}, {len(image_bytes):,} bytes)"
    )

    try:
        resp = requests.post(endpoint, json=payload, headers=headers, timeout=360)
    except requests.exceptions.Timeout:
        logger.error("ai_video_clips [veo]: request timed out after 360s")
        return None
    except requests.exceptions.ConnectionError as e:
        logger.error(f"ai_video_clips [veo]: connection error — {e}")
        return None

    if resp.status_code == 401:
        raise RuntimeError(
            "ai_video_clips [veo]: Edge Function returned 401. "
            "Redeploy with: supabase functions deploy veo-image-to-video --no-verify-jwt"
        )
    if resp.status_code == 404:
        raise RuntimeError(
            "ai_video_clips [veo]: Edge Function returned 404. "
            "Deploy veo-image-to-video first — see the video fix implementation brief."
        )
    if not resp.ok:
        logger.error(
            f"ai_video_clips [veo]: HTTP {resp.status_code}: {resp.text[:500]}"
        )
        return None

    try:
        data = resp.json()
    except Exception:
        logger.error(f"ai_video_clips [veo]: could not parse JSON: {resp.text[:200]}")
        return None

    if "error" in data:
        logger.error(f"ai_video_clips [veo]: Veo error: {data['error']}")
        return None

    video_b64 = data.get("video_base64")
    if not video_b64:
        logger.error(
            f"ai_video_clips [veo]: missing video_base64. Keys: {list(data.keys())}"
        )
        return None

    try:
        video_bytes = base64.b64decode(video_b64)
    except Exception as e:
        logger.error(f"ai_video_clips [veo]: failed to decode video_base64: {e}")
        return None

    logger.info(f"ai_video_clips [veo]: received {len(video_bytes):,} bytes")
    return video_bytes


# ---------------------------------------------------------------------------
# Sora: text-to-video
# ---------------------------------------------------------------------------

def _call_sora_text_to_video(
    prompt: str,
    aspect_ratio: str = "9:16",
    duration_seconds: int = 5,
) -> bytes | None:
    """
    Generate a video from a text prompt using OpenAI Sora.
    Returns raw MP4 bytes on success, None on failure.
    Raises RuntimeError on configuration errors.
    """
    if not sora_configured():
        raise RuntimeError(
            "ai_video_clips [sora]: OpenAI API key is not configured. "
            "Set openai_api_key in .streamlit/secrets.toml."
        )

    # Map duration to nearest supported Sora value (4, 8, 12)
    supported = [4, 8, 12]
    seconds = min(supported, key=lambda x: abs(x - duration_seconds))
    size = _SORA_SIZE_MAP.get(aspect_ratio, _SORA_SIZE_MAP["16:9"])

    payload = {
        "model": "sora-2",
        "prompt": prompt.strip(),
        "seconds": str(seconds),
        "size": size,
    }

    logger.info(f"ai_video_clips [sora]: submitting text-to-video job (size={size}, seconds={seconds})")

    try:
        resp = requests.post(_SORA_CREATE_URL, json=payload, headers=_sora_headers(), timeout=60)
    except requests.exceptions.Timeout:
        logger.error("ai_video_clips [sora]: job submission timed out")
        return None
    except requests.exceptions.ConnectionError as e:
        logger.error(f"ai_video_clips [sora]: connection error — {e}")
        return None

    if resp.status_code == 401:
        raise RuntimeError(
            "ai_video_clips [sora]: OpenAI returned 401. API key is invalid or revoked."
        )
    if resp.status_code == 403:
        raise RuntimeError(
            "ai_video_clips [sora]: OpenAI returned 403. "
            "This key does not have Sora access. Ensure it belongs to a Sora-enabled project."
        )
    if not resp.ok:
        logger.error(f"ai_video_clips [sora]: submit HTTP {resp.status_code}: {resp.text[:500]}")
        return None

    job = resp.json()
    job_id = str(job.get("id") or "").strip()
    if not job_id:
        logger.error(f"ai_video_clips [sora]: no job ID in response: {json.dumps(job)[:300]}")
        return None

    logger.info(f"ai_video_clips [sora]: job submitted — id={job_id}, polling...")

    try:
        final_job = poll_video(job_id, timeout_s=_MAX_POLLS * _POLL_INTERVAL_S)
    except TimeoutError:
        logger.error(f"ai_video_clips [sora]: job {job_id} timed out")
        return None
    except RuntimeError as e:
        logger.error(f"ai_video_clips [sora]: job {job_id} failed — {e}")
        return None

    status = str(final_job.get("status", "")).lower().strip()
    if status != "completed":
        logger.error(f"ai_video_clips [sora]: job {job_id} ended with status={status}")
        return None

    logger.info(f"ai_video_clips [sora]: job {job_id} complete, downloading content...")

    try:
        video_bytes = get_video_content(job_id)
    except Exception as e:
        logger.error(f"ai_video_clips [sora]: failed to download content — {e}")
        return None

    logger.info(f"ai_video_clips [sora]: received {len(video_bytes):,} bytes")
    return video_bytes


# ---------------------------------------------------------------------------
# Sora: image-to-video with text-to-video fallback
# ---------------------------------------------------------------------------

def _call_sora_image_to_video(
    image_path: Path,
    prompt: str,
    aspect_ratio: str = "9:16",
    duration_seconds: int = 5,
) -> bytes | None:
    """
    Attempt Sora image-to-video using input_reference.
    Falls back to text-to-video if the image reference is rejected or fails.
    Returns raw MP4 bytes on success, None on failure.
    """
    if not sora_configured():
        raise RuntimeError(
            "ai_video_clips [sora]: OpenAI API key is not configured."
        )

    supported = [4, 8, 12]
    seconds = min(supported, key=lambda x: abs(x - duration_seconds))
    size = _SORA_SIZE_MAP.get(aspect_ratio, _SORA_SIZE_MAP["16:9"])

    # Try with image reference first
    if image_path.exists():
        logger.info(
            f"ai_video_clips [sora]: attempting image-to-video with {image_path.name}"
        )
        image_bytes = image_path.read_bytes()
        image_b64 = base64.b64encode(image_bytes).decode("utf-8")
        mime_type = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
        image_data_url = f"data:{mime_type};base64,{image_b64}"

        payload = {
            "model": "sora-2",
            "prompt": prompt.strip(),
            "seconds": str(seconds),
            "size": size,
            "input_reference": image_data_url,
        }

        try:
            resp = requests.post(
                _SORA_CREATE_URL, json=payload, headers=_sora_headers(), timeout=60
            )
            if resp.ok:
                job = resp.json()
                job_id = str(job.get("id") or "").strip()
                if job_id:
                    logger.info(
                        f"ai_video_clips [sora]: image-to-video job submitted — id={job_id}"
                    )
                    try:
                        final_job = poll_video(
                            job_id, timeout_s=_MAX_POLLS * _POLL_INTERVAL_S
                        )
                        if str(final_job.get("status", "")).lower() == "completed":
                            video_bytes = get_video_content(job_id)
                            logger.info(
                                f"ai_video_clips [sora]: image-to-video succeeded "
                                f"({len(video_bytes):,} bytes)"
                            )
                            return video_bytes
                    except (TimeoutError, RuntimeError) as e:
                        logger.warning(
                            f"ai_video_clips [sora]: image-to-video job failed ({e}), "
                            "falling back to text-to-video"
                        )
                else:
                    logger.warning(
                        "ai_video_clips [sora]: image-to-video returned no job ID, "
                        "falling back to text-to-video"
                    )
            else:
                logger.warning(
                    f"ai_video_clips [sora]: image-to-video submit returned "
                    f"HTTP {resp.status_code}, falling back to text-to-video"
                )
        except Exception as e:
            logger.warning(
                f"ai_video_clips [sora]: image-to-video attempt raised {e}, "
                "falling back to text-to-video"
            )
    else:
        logger.info(
            f"ai_video_clips [sora]: image not found at {image_path}, "
            "using text-to-video directly"
        )

    # Fallback: text-to-video
    logger.info("ai_video_clips [sora]: running text-to-video fallback")
    return _call_sora_text_to_video(prompt, aspect_ratio, duration_seconds)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_ai_video_clips(
    project_id: str,
    tmp_dir: Path,
    aspect_ratio: str = "9:16",
    duration_seconds: int = 5,
    provider: str = "veo",
    workflow_logger=None,
    clip_done_callback=None,
) -> tuple:
    """
    Main entry point called by the automation step runner.

    Args:
        project_id:       Active History Forge project ID.
        tmp_dir:          Directory to write output MP4 files.
        aspect_ratio:     "9:16", "16:9", or "1:1".
        duration_seconds: Clip length (Sora snaps to 4/8/12; Veo uses as-is).
        provider:         "veo" or "sora". Defaults to "veo".

    Returns:
        (opening_clip_path | None, mid_clip_path | None)

    Raises:
        RuntimeError: If provider credentials are missing or Edge Function is
                      not deployed. Surfaces to the automation UI.
        ValueError:   If an unknown provider is specified.
    """
    provider = (provider or "veo").strip().lower()
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(
            f"ai_video_clips: unknown provider '{provider}'. "
            f"Use one of: {SUPPORTED_PROVIDERS}"
        )

    images = _find_scene_images(project_id)
    prompts = _find_scene_prompts(project_id)

    if not images and provider == "veo":
        logger.info(
            f"ai_video_clips [veo]: no scene images found for project {project_id}, skipping"
        )
        return None, None

    if not prompts and provider == "sora":
        logger.info(
            f"ai_video_clips [sora]: no scene prompts found for project {project_id}, skipping"
        )
        return None, None

    tmp_dir.mkdir(parents=True, exist_ok=True)

    def _get_prompt(idx: int) -> str:
        raw = prompts[idx] if idx < len(prompts) else ""
        return _build_motion_prompt(raw) if raw else (
            "Animate this historical scene with natural cinematic motion, "
            "dramatic documentary atmosphere, slow deliberate movement."
        )

    def _get_image(idx: int) -> Optional[Path]:
        return images[idx] if idx < len(images) else None

    # Define clips: (label, image_index, prompt_index, output_filename)
    num_images = max(len(images), 1)
    clip_targets = [
        ("opening", 0,               0,               "ai_clip_opening.mp4"),
        ("mid",     num_images // 2, num_images // 2, "ai_clip_mid.mp4"),
    ]

    results = []
    failures = []

    for label, img_idx, prompt_idx, out_name in clip_targets:
        image = _get_image(img_idx)
        prompt = _get_prompt(prompt_idx)
        out_path = tmp_dir / out_name

        logger.info(
            f"ai_video_clips [{provider}]: generating {label} clip "
            f"(image={image.name if image else 'none'}, prompt_idx={prompt_idx})"
        )

        try:
            if provider == "veo":
                if image is None:
                    logger.warning(
                        f"ai_video_clips [veo]: no image for {label} clip, skipping"
                    )
                    results.append(None)
                    failures.append(label)
                    continue
                video_bytes = _call_veo_image_to_video(
                    image, prompt, aspect_ratio, duration_seconds
                )
            else:  # sora
                video_bytes = _call_sora_image_to_video(
                    image, prompt, aspect_ratio, duration_seconds
                ) if image else _call_sora_text_to_video(
                    prompt, aspect_ratio, duration_seconds
                )

        except RuntimeError:
            # Config / deployment error — re-raise so the pipeline surfaces it
            raise

        if video_bytes:
            out_path.write_bytes(video_bytes)
            logger.info(
                f"ai_video_clips [{provider}]: {label} clip saved → "
                f"{out_path} ({len(video_bytes):,} bytes)"
            )
            results.append(out_path)
            if callable(clip_done_callback):
                try:
                    clip_done_callback(label, True, len(results), len(clip_targets))
                except Exception:  # noqa: BLE001
                    pass
        else:
            logger.warning(
                f"ai_video_clips [{provider}]: {label} clip returned no bytes"
            )
            results.append(None)
            failures.append(label)
            if callable(clip_done_callback):
                try:
                    clip_done_callback(label, False, len(results), len(clip_targets))
                except Exception:  # noqa: BLE001
                    pass

    if failures:
        logger.warning(
            f"ai_video_clips [{provider}]: {len(failures)}/{len(results)} clips failed: {failures}"
        )
    if all(r is None for r in results):
        logger.error(
            f"ai_video_clips [{provider}]: ALL clips failed for project {project_id}. "
            "Check credentials and provider logs."
        )

    return results[0], results[1]
