from __future__ import annotations

"""Gemini Developer API Veo helper.

The public provider key remains ``google_veo_lite`` for saved-setting
compatibility, but generation now uses the Google GenAI SDK with
``GEMINI_API_KEY``. No Google Cloud project, location, endpoint, or service
account configuration is required for this generative path.
"""

import base64
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from src.config import get_secret
from src.providers.gemini_provider import (
    DEFAULT_GEMINI_VIDEO_MODEL,
    GeminiMissingKeyError,
    GeminiModelError,
    GeminiProviderError,
    GeminiQuotaError,
    generate_video_from_image,
    get_gemini_api_key as _provider_get_gemini_api_key,
)
from src.video.utils import get_media_duration

logger = logging.getLogger(__name__)

DEFAULT_GOOGLE_VIDEO_MODEL = DEFAULT_GEMINI_VIDEO_MODEL
GOOGLE_VIDEO_MODEL_FALLBACKS = (
    "veo-3.1-fast-generate-preview",
    "veo-3.0-fast-generate-001",
    "veo-2.0-generate-001",
)
DEFAULT_OUTPUT_DIR = Path("data/google_veo_video_tests")
MIN_VIDEO_BYTES = 100_000
_SUPPORTED_ASPECT_RATIOS = {"16:9", "9:16", "1:1"}


def _sanitize_obj(obj: Any) -> Any:
    if isinstance(obj, str):
        if len(obj) > 500:
            return f"<redacted-large-string:{len(obj)} chars>"
        return obj
    if isinstance(obj, dict):
        redacted: dict[str, Any] = {}
        for key, value in obj.items():
            key_lower = str(key).lower()
            if any(tok in key_lower for tok in ("key", "token", "authorization", "secret", "signature")):
                redacted[str(key)] = "<redacted>"
            else:
                redacted[str(key)] = _sanitize_obj(value)
        return redacted
    if isinstance(obj, list):
        return [_sanitize_obj(item) for item in obj[:50]]
    return obj


def _write_debug_json(path: Path | None, payload: Any) -> str:
    if path is None:
        return ""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_sanitize_obj(payload), indent=2, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _slugify(value: str, *, default: str = "item") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(value or "").strip()).strip("-._")
    return cleaned[:80] or default


def get_gemini_api_key() -> str:
    """Resolve Gemini API key from secrets/env without exposing it in logs."""
    try:
        return _provider_get_gemini_api_key(required=False)
    except Exception:
        return ""


def google_veo_lite_configured() -> bool:
    return bool(get_gemini_api_key())


def normalize_image_input_for_google(image_source: Any) -> dict[str, str]:
    """Normalize uploaded/url/path input to a debug-friendly shape."""
    if image_source is None:
        return {"inline_data": "", "image_mime_type": ""}
    if hasattr(image_source, "getvalue") and callable(getattr(image_source, "getvalue", None)):
        raw = image_source.getvalue()
        if isinstance(raw, (bytes, bytearray)) and raw:
            name = str(getattr(image_source, "name", "")).lower()
            mime = "image/png" if name.endswith(".png") else "image/webp" if name.endswith(".webp") else "image/jpeg"
            return {"inline_data": base64.b64encode(bytes(raw)).decode("utf-8"), "image_mime_type": mime}
    if isinstance(image_source, str):
        value = image_source.strip()
        if not value:
            return {"inline_data": "", "image_mime_type": ""}
        if value.startswith(("http://", "https://")):
            return {"image_url": value, "image_mime_type": ""}
        candidate = Path(value)
        if candidate.exists() and candidate.is_file():
            suffix = candidate.suffix.lower()
            mime = "image/png" if suffix == ".png" else "image/webp" if suffix == ".webp" else "image/jpeg"
            return {"inline_data": base64.b64encode(candidate.read_bytes()).decode("utf-8"), "image_mime_type": mime}
        if value.startswith("data:"):
            return {"inline_data": value, "image_mime_type": ""}
    if isinstance(image_source, dict):
        for key in ("url", "image_url", "path", "data_uri", "data"):
            raw = image_source.get(key)
            if isinstance(raw, str) and raw.strip():
                return normalize_image_input_for_google(raw)
    if isinstance(image_source, (bytes, bytearray)) and image_source:
        return {"inline_data": base64.b64encode(bytes(image_source)).decode("utf-8"), "image_mime_type": "image/jpeg"}
    return {"inline_data": "", "image_mime_type": ""}


def _friendly_error(exc: Exception) -> str:
    if isinstance(exc, GeminiMissingKeyError):
        return "GEMINI_API_KEY is not configured. Create a Google AI Studio key and set GEMINI_API_KEY."
    if isinstance(exc, GeminiQuotaError):
        return "Gemini quota or rate limit reached. Retry later or choose a cheaper/faster model."
    if isinstance(exc, GeminiModelError):
        return f"Gemini video model is unavailable or invalid: {exc}"
    if isinstance(exc, GeminiProviderError):
        return str(exc)
    return f"Gemini video generation failed: {exc}"


def _candidate_models(primary_model: str) -> list[str]:
    candidates = [primary_model, *GOOGLE_VIDEO_MODEL_FALLBACKS]
    seen: set[str] = set()
    ordered: list[str] = []
    for model in candidates:
        value = str(model or "").strip()
        if value and value not in seen:
            ordered.append(value)
            seen.add(value)
    return ordered


def generate_google_veo_lite_video(
    *,
    prompt: str,
    image_source: Any,
    aspect_ratio: str = "9:16",
    duration_seconds: int = 5,
    output_path: str | Path,
    debug_dir: str | Path | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Generate image-to-video with Gemini Developer API + Google GenAI SDK."""
    configured_model = str(model or get_secret("GEMINI_VIDEO_MODEL", get_secret("HF_GOOGLE_VIDEO_MODEL", DEFAULT_GOOGLE_VIDEO_MODEL)) or DEFAULT_GOOGLE_VIDEO_MODEL)
    result: dict[str, Any] = {
        "ok": False,
        "provider": "google_veo_lite",
        "model": configured_model,
        "response_type": "generate_videos",
        "video_url": "",
        "output_path": str(output_path),
        "error": "",
    }

    prompt_clean = str(prompt or "").strip()
    if not prompt_clean:
        result["error"] = "Prompt cannot be empty."
        return result
    if aspect_ratio not in _SUPPORTED_ASPECT_RATIOS:
        result["error"] = f"Unsupported aspect ratio '{aspect_ratio}'."
        return result
    if duration_seconds < 1 or duration_seconds > 12:
        result["error"] = "Duration must be between 1 and 12 seconds."
        return result
    if not get_gemini_api_key():
        result["error"] = "GEMINI_API_KEY is not configured."
        return result

    image_payload = normalize_image_input_for_google(image_source)
    if not image_payload.get("inline_data") and not image_payload.get("image_url"):
        result["error"] = "Image input is required for Google Veo Lite image-to-video generation."
        return result

    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    stem = _slugify(Path(str(output_path)).stem or "google_veo")
    debug_base = Path(debug_dir) if debug_dir else None
    debug_request_path = debug_base / f"google_veo_request_{stem}_{ts}.json" if debug_base else None
    debug_response_path = debug_base / f"google_veo_response_{stem}_{ts}.json" if debug_base else None

    attempts: list[dict[str, Any]] = []
    for candidate_model in _candidate_models(configured_model):
        candidate_aspect_ratio = "16:9" if candidate_model == "veo-2.0-generate-001" and aspect_ratio == "9:16" else aspect_ratio
        result["model"] = candidate_model
        attempts.append({"model": candidate_model, "aspect_ratio": candidate_aspect_ratio, "ok": False, "error": ""})
        _write_debug_json(
            debug_request_path,
            {
                "provider": "google_veo_lite",
                "auth": "GEMINI_API_KEY",
                "model": candidate_model,
                "model_attempts": attempts,
                "aspect_ratio": candidate_aspect_ratio,
                "requested_aspect_ratio": aspect_ratio,
                "duration_seconds": int(duration_seconds),
                "prompt_length": len(prompt_clean),
                "image_payload": image_payload,
            },
        )
        try:
            video_result = generate_video_from_image(
                prompt=prompt_clean,
                image_source=image_source,
                aspect_ratio=candidate_aspect_ratio,
                duration_seconds=int(duration_seconds),
                output_path=output_path,
                model=candidate_model,
            )
        except Exception as exc:  # noqa: BLE001
            error = _friendly_error(exc)
            attempts[-1]["error"] = error[:500]
            _write_debug_json(debug_response_path, {"ok": False, "attempts": attempts, "error": error})
            if isinstance(exc, GeminiModelError):
                continue
            result["error"] = error
            result["debug_request_path"] = str(debug_request_path) if debug_request_path else ""
            result["debug_response_path"] = str(debug_response_path) if debug_response_path else ""
            return result

        attempts[-1]["ok"] = bool(video_result.ok)
        attempts[-1]["error"] = str(video_result.error or "")[:500]
        if video_result.ok:
            path = Path(output_path)
            if not path.exists() or path.stat().st_size < MIN_VIDEO_BYTES:
                result["error"] = f"Gemini video appears invalid or too small ({path.stat().st_size if path.exists() else 0} bytes)."
            else:
                result["ok"] = True
                result["video_url"] = str(path)
                result["duration_seconds"] = get_media_duration(path)
                result["debug_request_path"] = str(debug_request_path) if debug_request_path else ""
                result["debug_response_path"] = str(debug_response_path) if debug_response_path else ""
                _write_debug_json(debug_response_path, {"ok": True, "attempts": attempts, "output_path": str(path), "duration_seconds": result["duration_seconds"]})
                return result
        else:
            result["error"] = video_result.error or "Gemini video generation failed."

    result["debug_request_path"] = str(debug_request_path) if debug_request_path else ""
    result["debug_response_path"] = str(debug_response_path) if debug_response_path else ""
    _write_debug_json(debug_response_path, {"ok": False, "attempts": attempts, "error": result["error"]})
    return result
