"""
src/video/ai_video_clips.py

Animates the first and middle generated scene images into true AI video clips
using Google Veo 2 image-to-video via the veo-image-to-video Supabase Edge Function.

Runs automatically as the ai_video_clips step in the automation workflow.
The existing veo-generate Edge Function is not touched.
"""

import base64
import json
import logging
import requests
from pathlib import Path

from src.config import get_secret

logger = logging.getLogger(__name__)

POLL_TIMEOUT_SEC = 300


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


def _call_veo_image_to_video(
    image_path: Path,
    prompt: str,
    aspect_ratio: str = "9:16",
    duration_seconds: int = 5,
) -> bytes | None:
    """
    Send an image to the veo-image-to-video Edge Function.
    Returns raw video bytes on success, None on failure.
    """
    supabase_url = get_secret("SUPABASE_URL")
    supabase_key = get_secret("SUPABASE_KEY") or get_secret("SUPABASE_ANON_KEY")

    if not supabase_url or not supabase_key:
        logger.warning("ai_video_clips: SUPABASE_URL or SUPABASE_KEY missing")
        return None

    # Base64-encode the image
    image_bytes = image_path.read_bytes()
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    mime_type = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"

    endpoint = f"{supabase_url}/functions/v1/veo-image-to-video"
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

    try:
        resp = requests.post(endpoint, json=payload, headers=headers, timeout=360)
        resp.raise_for_status()
        data = resp.json()

        if "error" in data:
            logger.warning(f"ai_video_clips: Veo error: {data['error']}")
            return None

        # Response contains base64-encoded video bytes
        video_b64 = data.get("video_base64")
        if not video_b64:
            logger.warning("ai_video_clips: no video_base64 in response")
            return None

        return base64.b64decode(video_b64)

    except Exception as e:
        logger.warning(f"ai_video_clips: request failed: {e}")
        return None


def generate_ai_video_clips(
    project_id: str,
    tmp_dir: Path,
    aspect_ratio: str = "9:16",
    duration_seconds: int = 5,
) -> tuple:
    """
    Main entry point called by the automation step runner.
    Returns (opening_clip_path | None, mid_clip_path | None).
    """
    images = _find_scene_images(project_id)
    if not images:
        logger.info("ai_video_clips: no scene images found, skipping")
        return None, None

    prompts = _find_scene_prompts(project_id)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    def _get_prompt(idx: int) -> str:
        raw = prompts[idx] if idx < len(prompts) else ""
        return _build_motion_prompt(raw) if raw else (
            "Animate this historical scene with natural cinematic motion, "
            "dramatic documentary atmosphere, slow deliberate movement."
        )

    results = []
    for label, img_idx, out_name in [
        ("opening", 0, "ai_clip_opening.mp4"),
        ("mid",     len(images) // 2, "ai_clip_mid.mp4"),
    ]:
        image = images[img_idx]
        prompt = _get_prompt(img_idx)
        out_path = tmp_dir / out_name

        logger.info(f"ai_video_clips: generating {label} clip from {image.name}")
        video_bytes = _call_veo_image_to_video(image, prompt, aspect_ratio, duration_seconds)

        if video_bytes:
            out_path.write_bytes(video_bytes)
            logger.info(f"ai_video_clips: {label} clip saved ({len(video_bytes):,} bytes)")
            results.append(out_path)
        else:
            logger.warning(f"ai_video_clips: {label} clip failed, skipping")
            results.append(None)

    return results[0], results[1]
