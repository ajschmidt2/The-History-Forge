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
import subprocess
import time
import requests
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from typing import Optional, Any

from src.config import get_fal_key, get_openai_config, get_secret
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

SUPPORTED_PROVIDERS = ("falai", "veo", "sora")
SUPPORTED_ASPECT_RATIOS = {"16:9", "9:16", "1:1"}
MIN_VIDEO_BYTES = 1024
FAL_VIDEO_MODELS = {
    "text": "fal-ai/wan/v2.2-5b/text-to-video",
    "image": "fal-ai/wan/v2.2-5b/image-to-video",
}
FAL_RESPONSE_CANDIDATE_KEYS = {
    "video",
    "videos",
    "url",
    "mp4",
    "output",
    "outputs",
    "data",
    "result",
    "artifacts",
}


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
# fal.ai: text-to-video and image-to-video via Wan 2.2
# ---------------------------------------------------------------------------

def _ensure_fal_key() -> None:
    """Populate canonical FAL_KEY env var so fal_client can authenticate."""
    get_fal_key()


def _simplify_motion_prompt(prompt: str, aspect_ratio: str, duration_seconds: int) -> str:
    clean = " ".join((prompt or "").split())
    if not clean:
        return f"Historical cinematic shot, aspect ratio {aspect_ratio}, duration {duration_seconds}s."
    first_sentence = re.split(r"[.!?]", clean, maxsplit=1)[0].strip()
    return f"{first_sentence}. Cinematic camera movement. aspect ratio {aspect_ratio}. duration {duration_seconds}s."


def _sanitize_url(url: str) -> str:
    parsed = urlsplit(url)
    filtered = [(k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True) if k.lower() not in {"token", "signature", "sig", "expires", "x-amz-signature"}]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(filtered), ""))


def _sanitize_for_debug(value: object) -> object:
    if isinstance(value, str):
        if value.startswith("http://") or value.startswith("https://"):
            return _sanitize_url(value)
        if "key" in value.lower() or "token" in value.lower():
            return "<redacted>"
        return value
    if isinstance(value, dict):
        out: dict[str, object] = {}
        for k, v in value.items():
            lk = str(k).lower()
            if any(secret in lk for secret in ("key", "token", "secret", "authorization", "signature")):
                out[str(k)] = "<redacted>"
            else:
                out[str(k)] = _sanitize_for_debug(v)
        return out
    if isinstance(value, list):
        return [_sanitize_for_debug(v) for v in value[:20]]
    return value


def _looks_like_video_url(url: str) -> bool:
    lower = url.lower()
    return any(ext in lower for ext in (".mp4", ".webm", ".mov", ".m4v", ".m3u8", "video"))


def _short_repr(value: object, max_len: int = 280) -> str:
    preview = repr(value)
    return preview if len(preview) <= max_len else f"{preview[:max_len]}..."


def extract_video_url(response: object) -> str | None:
    """Extract first usable remote video URL from structured provider responses."""
    return _extract_first_video_url(response)


def _extract_first_video_url(obj: object) -> str | None:
    if obj is None:
        return None
    if isinstance(obj, str):
        if obj.startswith(("http://", "https://")) and _looks_like_video_url(obj):
            return obj
        return None
    if isinstance(obj, dict):
        url_value = obj.get("url")
        content_type = str(obj.get("content_type") or "").lower()
        if isinstance(url_value, str) and url_value.startswith(("http://", "https://")):
            if content_type.startswith("video/") or _looks_like_video_url(url_value):
                return url_value
        for key in ("video_url", "url", "mp4", "href", "download_url"):
            value = obj.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")) and _looks_like_video_url(value):
                return value
        for key in FAL_RESPONSE_CANDIDATE_KEYS:
            if key in obj:
                found = _extract_first_video_url(obj.get(key))
                if found:
                    return found
        for value in obj.values():
            found = _extract_first_video_url(value)
            if found:
                return found
        return None
    if isinstance(obj, list):
        for item in obj:
            found = _extract_first_video_url(item)
            if found:
                return found
    return None


def _download_file(url: str, output_path: Path) -> tuple[bool, str]:
    try:
        with requests.get(url, timeout=180, stream=True) as resp:
            resp.raise_for_status()
            content_type = str(resp.headers.get("Content-Type", "")).lower()
            if content_type and "video" not in content_type and "octet-stream" not in content_type:
                return False, f"download returned non-video content-type={content_type}"
            with output_path.open("wb") as handle:
                for chunk in resp.iter_content(chunk_size=1024 * 128):
                    if chunk:
                        handle.write(chunk)
    except Exception as exc:  # noqa: BLE001
        return False, f"download failed ({type(exc).__name__}: {str(exc)[:160]})"
    return True, ""


def _is_valid_video_file(path: Path) -> bool:
    return path.exists() and path.is_file() and path.stat().st_size >= MIN_VIDEO_BYTES


def is_valid_video_file(path: Path) -> bool:
    return _is_valid_video_file(path)


def extract_error_message(response: object) -> str | None:
    if response is None:
        return "provider returned empty response"
    if isinstance(response, dict):
        for key in ("error", "message", "detail", "details", "reason"):
            value = response.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, dict):
                nested = extract_error_message(value)
                if nested:
                    return nested
        for value in response.values():
            nested = extract_error_message(value)
            if nested:
                return nested
    if isinstance(response, list):
        for item in response:
            nested = extract_error_message(item)
            if nested:
                return nested
    return None


def save_sanitized_debug_json(path: Path, response: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    sanitized = _sanitize_for_debug(response)
    payload: dict[str, Any] = {
        "response_type": type(response).__name__,
        "top_level_keys": list(response.keys()) if isinstance(response, dict) else [],
        "preview": _short_repr(sanitized),
        "extracted_error_message": extract_error_message(response),
        "response": sanitized,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def normalize_image_input(path_or_url: Optional[str], fal_client: object | None = None) -> tuple[Optional[str], str]:
    value = str(path_or_url or "").strip()
    if not value:
        return None, "none"
    if value.startswith(("http://", "https://")):
        return value, "http_url"
    if value.startswith("data:"):
        return value, "data_uri"
    candidate = Path(value)
    if candidate.exists() and candidate.is_file():
        if fal_client is not None and hasattr(fal_client, "upload_file"):
            uploaded = fal_client.upload_file(str(candidate))
            if isinstance(uploaded, str) and uploaded.startswith(("http://", "https://")):
                return uploaded, "local_path_uploaded_url"
        mime = "image/png" if candidate.suffix.lower() == ".png" else "image/jpeg"
        encoded = base64.b64encode(candidate.read_bytes()).decode("utf-8")
        return f"data:{mime};base64,{encoded}", "local_path_data_uri"
    return value, "local_path_invalid"


def write_video_artifact(response: object, output_path: Path) -> tuple[bool, str]:
    if response is None:
        return False, "provider returned empty response"
    extracted_error = extract_error_message(response)
    if extracted_error:
        return False, f"provider returned validation error: {extracted_error}"
    if isinstance(response, (bytes, bytearray)):
        output_path.write_bytes(bytes(response))
        return (True, "") if _is_valid_video_file(output_path) else (False, "provider returned byte payload but output file is too small")
    if isinstance(response, str) and response.startswith(("http://", "https://")):
        ok, reason = _download_file(response, output_path)
        if ok and _is_valid_video_file(output_path):
            return True, ""
        return False, reason or "provider returned URL but download failed"
    url = _extract_first_video_url(response)
    if url:
        ok, reason = _download_file(url, output_path)
        if ok and _is_valid_video_file(output_path):
            return True, ""
        return False, reason or "provider returned URL but download failed"
    if isinstance(response, dict):
        return False, "provider returned dict without video artifact"
    if isinstance(response, list):
        return False, "provider returned list without a video URL"
    return False, f"provider returned unsupported response type={type(response).__name__}"


def _poll_fal_job_if_needed(fal_client, model_name: str, response: object, *, workflow_logger: logging.Logger) -> object:
    if not isinstance(response, dict):
        return response
    request_id = str(response.get("request_id") or response.get("job_id") or response.get("id") or "").strip()
    if not request_id:
        return response
    queue = getattr(fal_client, "queue", None)
    queue_status = getattr(queue, "status", None) if queue is not None else None
    queue_result = getattr(queue, "result", None) if queue is not None else None
    status_fn = getattr(fal_client, "status", None)
    result_fn = getattr(fal_client, "result", None)
    if not callable(status_fn) and not callable(queue_status):
        return response
    current: object = response
    for attempt in range(30):
        current_url = _extract_first_video_url(current)
        if current_url:
            return current
        if callable(queue_status):
            status_payload = queue_status(model_name, request_id)
        else:
            status_payload = status_fn(model_name, request_id)
        current = status_payload if status_payload is not None else current
        status = str((status_payload or {}).get("status") or "").lower().strip() if isinstance(status_payload, dict) else ""
        if status in {"failed", "error", "canceled", "cancelled"}:
            err = extract_error_message(status_payload) or f"status={status}"
            raise RuntimeError(f"provider returned validation error: {err}")
        if status in {"completed", "succeeded"} and (callable(queue_result) or callable(result_fn)):
            result_payload = queue_result(model_name, request_id) if callable(queue_result) else result_fn(model_name, request_id)
            if result_payload is not None:
                return result_payload
        if isinstance(status_payload, dict) and any(k in status_payload for k in ("output", "result", "data", "video", "outputs")):
            nested_url = _extract_first_video_url(status_payload)
            if nested_url:
                return status_payload
        workflow_logger.info("ai_video_clips [falai]: polling request_id=%s attempt=%d/30 status=%s", request_id, attempt + 1, status or "unknown")
        time.sleep(4)
    raise TimeoutError("provider job timed out")


def maybe_download_file(url: str, output_path: Path) -> bool:
    ok, _ = _download_file(url, output_path)
    return ok


def _validate_fal_inputs(prompt: str, image_path: Optional[Path], aspect_ratio: str, duration_seconds: int) -> None:
    if not str(prompt or "").strip():
        raise ValueError("Prompt cannot be empty.")
    if aspect_ratio not in SUPPORTED_ASPECT_RATIOS:
        raise ValueError(f"Unsupported aspect ratio '{aspect_ratio}'.")
    if duration_seconds < 1 or duration_seconds > 12:
        raise ValueError("Duration must be between 1 and 12 seconds.")
    if image_path is not None and not image_path.exists():
        raise ValueError(f"Image path does not exist: {image_path}")


def _generate_falai_video_clip(
    *,
    prompt: str,
    output_path: Path,
    clip_label: str,
    project_id: str,
    aspect_ratio: str = "9:16",
    duration_seconds: int = 5,
    image_path: Optional[Path] = None,
    workflow_logger: logging.Logger | None = None,
) -> tuple[bool, str]:
    try:
        import fal_client  # type: ignore
    except ImportError:
        raise RuntimeError(
            "ai_video_clips [falai]: fal-client is not installed. Run: pip install fal-client"
        )
    _validate_fal_inputs(prompt, image_path, aspect_ratio, duration_seconds)
    _ensure_fal_key()
    wlog = workflow_logger or logger
    model_name = FAL_VIDEO_MODELS["image"] if image_path else FAL_VIDEO_MODELS["text"]
    wlog.info("FAL_VIDEO_PATCH_V2 active model=%s", model_name)

    image_url = None
    image_input_type = "none"
    if image_path is not None:
        image_url, image_input_type = normalize_image_input(str(image_path), fal_client)
        if image_input_type == "local_path_invalid":
            last_err = "provider returned local-file input error"
            save_sanitized_debug_json(
                Path("data/projects") / project_id / "debug" / f"fal_video_{clip_label}.json",
                {"error": last_err, "image_input": str(image_path)},
            )
            return False, last_err

    last_error = "provider returned empty response"
    debug_path = Path("data/projects") / project_id / "debug" / f"fal_video_{clip_label}.json"
    if clip_label == "opening":
        try:
            probe_args = {"prompt": "Simple cinematic motion.", "aspect_ratio": aspect_ratio, "num_frames": 24}
            if image_url:
                probe_args["image_url"] = image_url
            probe_response = fal_client.subscribe(FAL_VIDEO_MODELS["image"] if image_url else FAL_VIDEO_MODELS["text"], arguments=probe_args)
            wlog.info(
                "ai_video_clips [falai]: fallback_probe response_type=%s keys=%s",
                type(probe_response).__name__,
                list(probe_response.keys())[:12] if isinstance(probe_response, dict) else [],
            )
        except Exception as exc:  # noqa: BLE001
            wlog.warning("ai_video_clips [falai]: fallback_probe failed reason=%s", str(exc)[:180])
    for attempt in range(2):
        prompt_value = _build_motion_prompt(prompt) if attempt == 0 else _simplify_motion_prompt(prompt, aspect_ratio, duration_seconds)
        args = {
            "prompt": prompt_value,
            "aspect_ratio": aspect_ratio,
            "num_frames": max(24, min(120, int(duration_seconds * 16))),
            "sample_steps": 30,
            "sample_guide_scale": 6.0,
        }
        if image_url:
            args["image_url"] = image_url
        request_log = {
            "model": model_name,
            "clip": clip_label,
            "payload_keys": sorted(list(args.keys())),
            "prompt_length": len(prompt_value),
            "image_input_type": image_input_type,
        }
        wlog.info("ai_video_clips [falai]: request=%s", _sanitize_for_debug(request_log))
        started = time.monotonic()
        try:
            response = fal_client.subscribe(model_name, arguments=args)
            response = _poll_fal_job_if_needed(fal_client, model_name, response, workflow_logger=wlog)
            response_type = type(response).__name__
            keys = list(response.keys()) if isinstance(response, dict) else []
            payload_keys = list(_sanitize_for_debug(args).keys())
            wlog.info(
                "ai_video_clips [falai]: clip=%s model=%s attempt=%d elapsed=%.2fs response_type=%s keys=%s payload_keys=%s",
                clip_label, model_name, attempt + 1, time.monotonic() - started, response_type, keys[:15], payload_keys,
            )
            if isinstance(response, dict):
                wlog.info("ai_video_clips [falai]: clip=%s dict_top_level_keys=%s", clip_label, keys[:20])
            save_sanitized_debug_json(
                debug_path,
                {
                    "attempt": attempt + 1,
                    "model": model_name,
                    "clip": clip_label,
                    "request": request_log,
                    "arguments": args,
                    "response_type": response_type,
                    "response": response,
                },
            )
            ok, reason = write_video_artifact(response, output_path)
            if ok:
                if not _is_valid_video_file(output_path):
                    last_error = "video artifact written but file failed validation"
                    wlog.warning(
                        "ai_video_clips [falai]: clip=%s attempt=%d failed reason=%s",
                        clip_label,
                        attempt + 1,
                        last_error,
                    )
                    continue
                return True, ""
            last_error = reason
            if "local path" in (last_error or "").lower():
                last_error = "provider returned local-file input error"
        except TimeoutError:
            last_error = "provider job timed out"
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)[:180]
            if "local" in msg.lower() and "path" in msg.lower():
                last_error = "provider returned local-file input error"
            elif "validation" in msg.lower():
                last_error = f"provider returned validation error: {msg}"
            else:
                last_error = f"{type(exc).__name__}: {msg}"
        save_sanitized_debug_json(
            debug_path,
            {
                "attempt": attempt + 1,
                "model": model_name,
                "clip": clip_label,
                "request": request_log,
                "error": last_error,
            },
        )
        wlog.warning("ai_video_clips [falai]: clip=%s attempt=%d failed reason=%s", clip_label, attempt + 1, last_error)
    return False, last_error


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
    return _call_sora(prompt, aspect_ratio, duration_seconds)


# ---------------------------------------------------------------------------
# Orientation normalization
# ---------------------------------------------------------------------------

def _normalize_clip_orientation(src: Path, width: int, height: int) -> None:
    """Re-encode clip in-place to exact dimensions, stripping rotation metadata.

    Sora (and some other generators) occasionally embed a rotation tag that
    causes the clip to appear sideways when composited. ffmpeg auto-rotates on
    read by default, so applying scale+crop after decoding always produces the
    correct pixel layout. We strip all container metadata on write so the tag
    cannot affect downstream players or the ffmpeg concat step.
    """
    tmp = src.with_suffix(".norm.mp4")
    cmd = [
        "ffmpeg", "-y",
        "-i", str(src),
        "-vf", (
            f"scale={width}:{height}:force_original_aspect_ratio=increase,"
            f"crop={width}:{height},"
            "setsar=1"
        ),
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-an",
        "-map_metadata", "-1",
        str(tmp),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
        tmp.replace(src)
    except subprocess.CalledProcessError as exc:
        logger.warning(
            f"ai_video_clips: orientation normalization failed for {src.name} — "
            f"{exc.stderr.decode(errors='replace')[-300:]}"
        )
        if tmp.exists():
            tmp.unlink()


def _clip_manifest_path(project_id: str) -> Path:
    return Path("data/projects") / project_id / "clip_manifest.json"


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def generate_ai_video_clips(
    project_id: str,
    tmp_dir: Path,
    aspect_ratio: str = "9:16",
    duration_seconds: int = 5,
    provider: str = "falai",
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
    provider = (provider or "falai").strip().lower()
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
        return None, None, None, None

    if not prompts and provider in ("sora", "falai"):
        _wlog.warning(
            "ai_video_clips [%s]: no scene prompts found for project %s, skipping",
            provider, project_id,
        )
        return None, None, None, None

    tmp_dir.mkdir(parents=True, exist_ok=True)
    _wlog.info("ai_video_clips project=%s provider=%s images=%d prompts=%d", project_id, provider, len(images), len(prompts))
    size_str = _SORA_SIZE_MAP.get(aspect_ratio, _SORA_SIZE_MAP["9:16"])
    _clip_w, _clip_h = (int(v) for v in size_str.split("x"))

    def _get_prompt(idx: int) -> str:
        raw = prompts[idx] if idx < len(prompts) else ""
        return _build_motion_prompt(raw) if raw else (
            "Animate this historical scene with natural cinematic motion, "
            "dramatic documentary atmosphere, slow deliberate movement."
        )

    def _get_image(idx: int) -> Optional[Path]:
        return images[idx] if idx < len(images) else None

    # Define clips: (label, image_index, prompt_index, output_filename)
    # 1 at the opening, then 3 spread evenly at 1/4, 1/2, 3/4 of the image set.
    num_images = max(len(images), 1)
    clip_targets = [
        ("opening", 0,                       0,                       "ai_clip_opening.mp4"),
        ("q2",      num_images // 4,         num_images // 4,         "ai_clip_q2.mp4"),
        ("q3",      num_images // 2,         num_images // 2,         "ai_clip_q3.mp4"),
        ("q4",      3 * num_images // 4,     3 * num_images // 4,     "ai_clip_q4.mp4"),
    ]

    results = []
    failures = []
    manifest: dict[str, object] = {
        "project_id": project_id,
        "provider": provider,
        "aspect_ratio": aspect_ratio,
        "duration_seconds": duration_seconds,
        "clips": [],
    }

    for label, img_idx, prompt_idx, out_name in clip_targets:
        image = _get_image(img_idx)
        prompt = _get_prompt(prompt_idx)
        out_path = tmp_dir / out_name

        _wlog.info(
            "ai_video_clips [%s]: generating %s clip image=%s prompt_idx=%d prompt_len=%d",
            provider, label, image.name if image else "none", prompt_idx, len(prompt),
        )

        reason = ""
        start = time.monotonic()
        try:
            if provider == "falai":
                ok, reason = _generate_falai_video_clip(
                    prompt=prompt,
                    image_path=image,
                    aspect_ratio=aspect_ratio,
                    duration_seconds=duration_seconds,
                    output_path=out_path,
                    clip_label=label,
                    project_id=project_id,
                    workflow_logger=_wlog,
                )
                video_bytes = out_path.read_bytes() if ok and out_path.exists() else None
            elif provider == "veo":
                if image is None:
                    _wlog.warning("ai_video_clips [veo]: no image for %s clip, skipping", label)
                    results.append(None)
                    failures.append(label)
                    manifest["clips"].append({"label": label, "status": "failed", "reason": "missing source image"})
                    continue
                video_bytes = _call_veo_image_to_video(
                    image, prompt, aspect_ratio, duration_seconds
                )
            else:  # sora
                video_bytes = _call_sora(prompt, aspect_ratio, duration_seconds, wlog=_wlog)

        except RuntimeError:
            # Config / deployment error — re-raise so the pipeline surfaces it
            raise

        elapsed = time.monotonic() - start
        if video_bytes:
            if provider != "falai":
                out_path.write_bytes(video_bytes)
            _normalize_clip_orientation(out_path, _clip_w, _clip_h)
            _wlog.info(
                "ai_video_clips [%s]: %s clip saved path=%s bytes=%d elapsed=%.2fs",
                provider, label, out_path, len(video_bytes), elapsed,
            )
            results.append(out_path)
            manifest["clips"].append({"label": label, "status": "success", "path": str(out_path), "bytes": len(video_bytes), "elapsed_seconds": round(elapsed, 2)})
            if callable(clip_done_callback):
                try:
                    clip_done_callback(label, True, len(results), len(clip_targets))
                except Exception:  # noqa: BLE001
                    pass
        else:
            if not reason:
                reason = "provider returned empty response"
            _wlog.warning("ai_video_clips [%s]: %s clip failed reason=%s elapsed=%.2fs", provider, label, reason, elapsed)
            results.append(None)
            failures.append(label)
            manifest["clips"].append({"label": label, "status": "failed", "reason": reason, "elapsed_seconds": round(elapsed, 2)})
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
            "ai_video_clips [%s]: ALL clips failed for project %s — check fal video response parsing, payload shape, async polling, or model compatibility",
            provider, project_id,
        )
    _clip_manifest_path(project_id).write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    return results[0], results[1], results[2], results[3]
