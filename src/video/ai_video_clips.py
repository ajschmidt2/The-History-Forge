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
import re
import requests
from pathlib import Path
from typing import Optional

from src.config import get_secret, get_openai_config
from src.ai_video_generation import (
    sora_configured,
    veo_configured,
    _SORA_SIZE_MAP,
    create_video,
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


def _extract_prompt_str(raw: object) -> str:
    """Extract a plain prompt string from a raw scenes.json value.

    The image_prompt field may be:
    - A bare string: returned as-is
    - A dict: ``raw["prompt"]`` returned
    - A stringified dict with trailing metadata, e.g.
      ``"{'scene': 1, 'prompt': 'Wide shot of...'}\n\nVisual intent: ..."``
      → the 'prompt' value is extracted via regex
    """
    if not raw:
        return ""
    if isinstance(raw, dict):
        return str(raw.get("prompt") or "")
    s = str(raw).strip()
    if s.startswith("{"):
        # Try regex to extract 'prompt': '...' or "prompt": "..." value
        # Handles trailing metadata after the closing brace
        m = re.search(r"""['"]prompt['"]\s*:\s*['"](.+?)['"]\s*\}""", s, re.DOTALL)
        if m:
            return m.group(1).strip()
    return s


def _find_scene_prompts(project_id: str) -> list[str]:
    """Return image prompts in scene order from scenes.json."""
    scenes_path = Path("data/projects") / project_id / "scenes.json"
    if not scenes_path.exists():
        return []
    try:
        scenes = json.loads(scenes_path.read_text())
        return [
            _extract_prompt_str(s.get("image_prompt") or s.get("prompt") or "")
            for s in scenes
        ]
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
# Sora: moderation-safe prompt sanitization
# ---------------------------------------------------------------------------

# Pairs of (regex_pattern, replacement) applied when Sora blocks a prompt.
# Primary target: real person names and phrases that trigger moderation.
_SORA_SANITIZATIONS: list[tuple[str, str]] = [
    # Real person name patterns — replace mid-sentence Title Case name pairs
    # with generic historical descriptions.
    (r'\b([A-Z][a-z]{1,12})\s+([A-Z][a-z]{1,12})\b', 'a historical figure'),
    # Sensitive descriptors that can trigger moderation on people
    (r'\bglamorous\b', 'distinguished'),
    (r'\bseductive\b', 'compelling'),
    (r'\bsensual\b', 'graceful'),
    (r'\bnaked\b', 'unadorned'),
    (r'\bnude\b', 'unadorned'),
    # Violence / dark content
    (r'\bkill(?:ing|ed|s)?\b', 'historical event'),
    (r'\bmurder(?:ing|ed|s)?\b', 'historical event'),
    (r'\bdeath\b', 'loss'),
    (r'\bblood(?:y|ied)?\b', 'aftermath'),
    (r'\bwar\b', 'conflict era'),
    (r'\bbattle\b', 'historical confrontation'),
    (r'\bweapon(?:s)?\b', 'period equipment'),
    (r'\bgun(?:s|fire)?\b', 'period implement'),
    (r'\bexplosion(?:s)?\b', 'dramatic event'),
]


def _sanitize_prompt_for_sora(prompt: str) -> str:
    """Apply moderation-safe substitutions to a Sora prompt.

    Called when a job returns ``moderation_blocked``.  Removes real person
    names (Title Case pairs) and other known trigger phrases.

    The name-detection pattern (first entry) is case-SENSITIVE so it only
    matches actual Title Case names like "Hedy Lamarr", not lowercase phrases.
    All other patterns are applied case-insensitively.
    """
    result = prompt
    name_pattern, name_replacement = _SORA_SANITIZATIONS[0]
    result = re.sub(name_pattern, name_replacement, result)  # case-sensitive
    for pattern, replacement in _SORA_SANITIZATIONS[1:]:
        result = re.sub(pattern, replacement, result, flags=re.IGNORECASE)
    return result


# ---------------------------------------------------------------------------
# Sora: text-to-video using proven create_video() + poll_video() path
# ---------------------------------------------------------------------------

def _call_sora(
    prompt: str,
    aspect_ratio: str = "9:16",
    duration_seconds: int = 5,
    wlog: logging.Logger | None = None,
) -> bytes | None:
    """
    Generate a video from a text prompt using OpenAI Sora via create_video().
    Uses the same proven path as the manual Sora UI flow.
    Returns raw MP4 bytes on success, None on non-fatal failure.
    Raises RuntimeError on configuration errors (missing key, 401, 403).
    """
    _log = wlog or logger

    if not sora_configured():
        raise RuntimeError(
            "ai_video_clips [sora]: OpenAI API key is not configured. "
            "Set openai_api_key in .streamlit/secrets.toml."
        )

    # Snap to nearest supported Sora duration (4, 8, 12)
    seconds = min([4, 8, 12], key=lambda x: abs(x - duration_seconds))
    size = _SORA_SIZE_MAP.get(aspect_ratio, _SORA_SIZE_MAP["16:9"])

    _log.info(
        "ai_video_clips [sora]: submitting text-to-video job size=%s seconds=%d prompt_len=%d",
        size, seconds, len(prompt),
    )

    try:
        job = create_video(prompt, model="sora-2", seconds=seconds, size=size)
    except (RuntimeError, PermissionError, ValueError) as exc:
        raise RuntimeError(f"ai_video_clips [sora]: job creation failed — {exc}") from exc
    except Exception as exc:  # noqa: BLE001
        _log.error("ai_video_clips [sora]: unexpected error creating job — %s", exc)
        return None

    job_id = str(job.get("id") or "").strip()
    if not job_id:
        _log.error("ai_video_clips [sora]: no job ID in response: %s", str(job)[:300])
        return None

    _log.info("ai_video_clips [sora]: job submitted id=%s polling...", job_id)

    try:
        final_job = poll_video(job_id, timeout_s=_MAX_POLLS * _POLL_INTERVAL_S)
    except TimeoutError:
        _log.error("ai_video_clips [sora]: job %s timed out after %ds", job_id, _MAX_POLLS * _POLL_INTERVAL_S)
        return None
    except RuntimeError as exc:
        err_str = str(exc)
        if "moderation_blocked" in err_str:
            # Sora blocked the prompt — sanitize and retry once
            sanitized = _sanitize_prompt_for_sora(prompt)
            if sanitized != prompt:
                _log.warning(
                    "ai_video_clips [sora]: moderation_blocked — retrying with sanitized prompt"
                )
                try:
                    retry_job = create_video(sanitized, model="sora-2", seconds=seconds, size=size)
                    retry_id = str(retry_job.get("id") or "").strip()
                    if retry_id:
                        _log.info("ai_video_clips [sora]: sanitized retry job id=%s", retry_id)
                        retry_final = poll_video(retry_id, timeout_s=_MAX_POLLS * _POLL_INTERVAL_S)
                        if str(retry_final.get("status", "")).lower() == "completed":
                            video_bytes = get_video_content(retry_id)
                            _log.info("ai_video_clips [sora]: sanitized retry succeeded bytes=%d", len(video_bytes))
                            return video_bytes
                except Exception as retry_exc:  # noqa: BLE001
                    _log.error("ai_video_clips [sora]: sanitized retry failed — %s", retry_exc)
            else:
                _log.warning("ai_video_clips [sora]: moderation_blocked but sanitization had no effect")
        else:
            _log.error("ai_video_clips [sora]: job %s failed — %s", job_id, exc)
        return None

    status = str(final_job.get("status", "")).lower().strip()
    if status != "completed":
        _log.error("ai_video_clips [sora]: job %s ended with status=%s", job_id, status)
        return None

    _log.info("ai_video_clips [sora]: job %s complete, downloading...", job_id)

    try:
        video_bytes = get_video_content(job_id)
    except Exception as exc:  # noqa: BLE001
        _log.error("ai_video_clips [sora]: failed to download content for %s — %s", job_id, exc)
        return None

    _log.info("ai_video_clips [sora]: received %d bytes for job %s", len(video_bytes), job_id)
    return video_bytes


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

    _wlog = workflow_logger or logger

    images = _find_scene_images(project_id)
    prompts = _find_scene_prompts(project_id)

    if not images and provider == "veo":
        _wlog.warning("ai_video_clips [veo]: no scene images found for project %s, skipping", project_id)
        return None, None

    if not prompts and provider == "sora":
        _wlog.warning("ai_video_clips [sora]: no scene prompts found for project %s, skipping", project_id)
        return None, None

    tmp_dir.mkdir(parents=True, exist_ok=True)
    _wlog.info("ai_video_clips project=%s provider=%s images=%d prompts=%d", project_id, provider, len(images), len(prompts))

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

        _wlog.info(
            "ai_video_clips [%s]: generating %s clip image=%s prompt_idx=%d prompt_len=%d",
            provider, label, image.name if image else "none", prompt_idx, len(prompt),
        )

        try:
            if provider == "veo":
                if image is None:
                    _wlog.warning("ai_video_clips [veo]: no image for %s clip, skipping", label)
                    results.append(None)
                    failures.append(label)
                    continue
                video_bytes = _call_veo_image_to_video(
                    image, prompt, aspect_ratio, duration_seconds
                )
            else:  # sora
                video_bytes = _call_sora(prompt, aspect_ratio, duration_seconds, wlog=_wlog)

        except RuntimeError:
            # Config / deployment error — re-raise so the pipeline surfaces it
            raise

        if video_bytes:
            out_path.write_bytes(video_bytes)
            _wlog.info(
                "ai_video_clips [%s]: %s clip saved path=%s bytes=%d",
                provider, label, out_path, len(video_bytes),
            )
            results.append(out_path)
            if callable(clip_done_callback):
                try:
                    clip_done_callback(label, True, len(results), len(clip_targets))
                except Exception:  # noqa: BLE001
                    pass
        else:
            _wlog.warning("ai_video_clips [%s]: %s clip returned no bytes", provider, label)
            results.append(None)
            failures.append(label)
            if callable(clip_done_callback):
                try:
                    clip_done_callback(label, False, len(results), len(clip_targets))
                except Exception:  # noqa: BLE001
                    pass

    if failures:
        _wlog.warning(
            "ai_video_clips [%s]: %d/%d clips failed: %s",
            provider, len(failures), len(results), failures,
        )
    if all(r is None for r in results):
        _wlog.error(
            "ai_video_clips [%s]: ALL clips failed for project %s — check credentials",
            provider, project_id,
        )

    return results[0], results[1]
