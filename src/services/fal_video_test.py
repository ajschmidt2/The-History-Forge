from __future__ import annotations

import base64
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests

from src.config import get_fal_key

MIN_VIDEO_BYTES = 100_000
DEFAULT_OUTPUT_DIR = Path("data/fal_video_tests")
DEFAULT_FAL_VIDEO_MODEL = "fal-ai/wan/v2.2-5b/image-to-video"
WORKING_TEST_MODEL_SLUG = DEFAULT_FAL_VIDEO_MODEL
INVALID_MODEL_HELP = "Use a full fal model slug, e.g. fal-ai/wan/v2.2-5b/image-to-video"
logger = logging.getLogger(__name__)


def get_fal_key_status() -> dict[str, Any]:
    """Return non-sensitive fal key status for UI diagnostics."""
    try:
        key = get_fal_key()
        return {
            "ok": bool(key),
            "configured": bool(key),
            "key_length": len(key),
            "key_prefix": key[:4] if key else "",
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "configured": False,
            "key_length": 0,
            "key_prefix": "",
            "error": str(exc),
        }


def _sanitize_url(url: str) -> str:
    parsed = urlsplit(url)
    redacted_qs = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if k.lower() not in {"token", "signature", "sig", "expires", "x-amz-signature", "x-amz-security-token"}
    ]
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, urlencode(redacted_qs), ""))


def _sanitize_obj(obj: Any) -> Any:
    if isinstance(obj, str):
        if obj.startswith(("http://", "https://")):
            return _sanitize_url(obj)
        return obj
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for key, value in obj.items():
            lower_key = str(key).lower()
            if any(secret_key in lower_key for secret_key in ("authorization", "api_key", "key", "token", "secret", "signature")):
                out[str(key)] = "<redacted>"
            else:
                out[str(key)] = _sanitize_obj(value)
        return out
    if isinstance(obj, list):
        return [_sanitize_obj(item) for item in obj[:50]]
    return obj


def validate_fal_model_slug(model: Any) -> tuple[bool, str]:
    """Validate a fal model slug and return (ok, normalized_model_or_error)."""
    model_clean = str(model or "").strip()
    if not model_clean:
        return False, INVALID_MODEL_HELP
    if not model_clean.startswith("fal-ai/"):
        return False, INVALID_MODEL_HELP
    remainder = model_clean[len("fal-ai/"):]
    if "/" not in remainder:
        return False, INVALID_MODEL_HELP
    return True, model_clean


def normalize_image_input(image_source: Any) -> str:
    """Normalize uploaded/url/path input to a fal-compatible image string."""
    if image_source is None:
        return ""

    if isinstance(image_source, str):
        value = image_source.strip()
        if not value:
            return ""
        if value.startswith(("http://", "https://", "data:")):
            return value
        candidate = Path(value)
        if candidate.exists() and candidate.is_file():
            mime = "image/png" if candidate.suffix.lower() == ".png" else "image/jpeg"
            encoded = base64.b64encode(candidate.read_bytes()).decode("utf-8")
            return f"data:{mime};base64,{encoded}"
        return value

    if isinstance(image_source, dict):
        for key in ("url", "image_url", "path", "data_uri", "data"):
            value = image_source.get(key)
            if isinstance(value, str) and value.strip():
                return normalize_image_input(value)

    # Streamlit UploadedFile-like object
    if hasattr(image_source, "getvalue") and callable(image_source.getvalue):
        raw = image_source.getvalue()
        if isinstance(raw, (bytes, bytearray)) and raw:
            name = str(getattr(image_source, "name", "")).lower()
            if name.endswith(".png"):
                mime = "image/png"
            elif name.endswith(".webp"):
                mime = "image/webp"
            else:
                mime = "image/jpeg"
            encoded = base64.b64encode(bytes(raw)).decode("utf-8")
            return f"data:{mime};base64,{encoded}"

    if isinstance(image_source, (bytes, bytearray)) and image_source:
        encoded = base64.b64encode(bytes(image_source)).decode("utf-8")
        return f"data:image/jpeg;base64,{encoded}"

    return ""


def _looks_like_video_url(url: str) -> bool:
    lower = url.lower()
    return any(token in lower for token in (".mp4", ".webm", ".mov", ".m4v", "video", "download"))


def extract_video_url(obj: Any) -> str | None:
    """Recursively extract a likely video URL from structured responses."""
    if obj is None:
        return None

    if isinstance(obj, str):
        return obj if obj.startswith(("http://", "https://")) and _looks_like_video_url(obj) else None

    if isinstance(obj, dict):
        url = obj.get("url")
        content_type = str(obj.get("content_type") or "").lower()
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            if content_type.startswith("video/") or _looks_like_video_url(url):
                return url

        for key in (
            "video_url",
            "mp4",
            "download_url",
            "href",
            "asset_url",
            "file_url",
            "uri",
        ):
            value = obj.get(key)
            if isinstance(value, str) and value.startswith(("http://", "https://")) and _looks_like_video_url(value):
                return value

        for key in ("video", "videos", "output", "outputs", "result", "data", "artifacts", "media"):
            if key in obj:
                found = extract_video_url(obj.get(key))
                if found:
                    return found

        for value in obj.values():
            found = extract_video_url(value)
            if found:
                return found

    if isinstance(obj, list):
        for item in obj:
            found = extract_video_url(item)
            if found:
                return found

    return None


def extract_error_message(obj: Any) -> str | None:
    if obj is None:
        return None

    if isinstance(obj, str):
        candidate = obj.strip()
        if candidate and ("error" in candidate.lower() or "failed" in candidate.lower()):
            return candidate[:500]
        return None

    if isinstance(obj, dict):
        for key in ("error", "message", "detail", "details", "reason", "status_message"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:500]
            nested = extract_error_message(value)
            if nested:
                return nested
        for value in obj.values():
            nested = extract_error_message(value)
            if nested:
                return nested

    if isinstance(obj, list):
        for item in obj:
            nested = extract_error_message(item)
            if nested:
                return nested

    return None


def download_video(url: str, output_path: str | Path) -> bool:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with requests.get(url, timeout=180, stream=True) as resp:
            resp.raise_for_status()
            content_type = str(resp.headers.get("Content-Type", "")).lower()
            if content_type and "video" not in content_type and "octet-stream" not in content_type:
                return False
            with path.open("wb") as handle:
                for chunk in resp.iter_content(chunk_size=1024 * 128):
                    if chunk:
                        handle.write(chunk)
    except Exception:  # noqa: BLE001
        return False
    return is_valid_video_file(path)


def is_valid_video_file(path: str | Path) -> bool:
    candidate = Path(path)
    return candidate.exists() and candidate.is_file() and candidate.stat().st_size >= MIN_VIDEO_BYTES


def _slugify(value: str, *, default: str = "item") -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", (value or "").strip()).strip("-._")
    return cleaned[:80] or default


def _write_debug_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_sanitize_obj(payload), indent=2, ensure_ascii=False), encoding="utf-8")


def run_fal_video_test(
    model: str,
    prompt: str,
    image_source: Any,
    duration: int | None = None,
    aspect_ratio: str | None = None,
) -> dict[str, Any]:
    """Run a standalone fal.ai image-to-video test using queue-backed subscribe."""
    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    model_clean = str(model or DEFAULT_FAL_VIDEO_MODEL).strip()
    model_ok, model_validation_detail = validate_fal_model_slug(model_clean)
    if model_ok:
        model_clean = model_validation_detail

    out_dir = DEFAULT_OUTPUT_DIR / f"{timestamp}_{_slugify(model_clean)}"
    out_dir.mkdir(parents=True, exist_ok=True)

    debug_request_path = out_dir / "request_debug.json"
    debug_response_path = out_dir / "response_debug.json"
    output_path = out_dir / "output.mp4"

    result: dict[str, Any] = {
        "ok": False,
        "response_type": "none",
        "response_keys": [],
        "video_url": "",
        "output_path": str(output_path),
        "error": "",
        "debug_request_path": str(debug_request_path),
        "debug_response_path": str(debug_response_path),
    }

    if not model_ok:
        result["error"] = INVALID_MODEL_HELP
        return result

    return generate_fal_image_to_video(
        model=model_clean,
        prompt=prompt,
        image_source=image_source,
        output_path=output_path,
        duration=duration,
        aspect_ratio=aspect_ratio,
        debug_request_path=debug_request_path,
        debug_response_path=debug_response_path,
        result_seed=result,
    )


def generate_fal_video_from_image(
    *,
    model: str,
    prompt: str,
    image_source: Any,
    output_path: str | Path,
    duration: int | None = None,
    aspect_ratio: str | None = None,
    debug_request_path: str | Path | None = None,
    debug_response_path: str | Path | None = None,
    result_seed: dict[str, Any] | None = None,
    fail_loud_missing_video_artifact: bool = False,
) -> dict[str, Any]:
    """Canonical fal image-to-video helper used by standalone tests and automation."""
    output = Path(output_path)
    result: dict[str, Any] = {
        "ok": False,
        "response_type": "none",
        "response_keys": [],
        "video_url": "",
        "output_path": str(output),
        "error": "",
        "debug_request_path": str(debug_request_path or ""),
        "debug_response_path": str(debug_response_path or ""),
    }
    if result_seed:
        result.update(result_seed)
        result["output_path"] = str(output)
        result["debug_request_path"] = str(debug_request_path or result.get("debug_request_path") or "")
        result["debug_response_path"] = str(debug_response_path or result.get("debug_response_path") or "")

    model_ok, model_validation_detail = validate_fal_model_slug(model)
    if not model_ok:
        result["error"] = model_validation_detail
        return result
    model_clean = model_validation_detail

    try:
        import fal_client  # type: ignore
    except ImportError:
        result["error"] = "fal-client is not installed. Run: pip install fal-client"
        return result

    try:
        key = get_fal_key()
        os.environ["FAL_KEY"] = key
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"fal key not configured: {exc}"
        return result

    normalized_image = normalize_image_input(image_source)
    if not normalized_image:
        result["error"] = "image input is required (upload, URL, data URI, or local file path)"
        return result

    args: dict[str, Any] = {
        "prompt": (prompt or "").strip(),
        "image_url": normalized_image,
    }
    if duration is not None:
        args["duration"] = int(duration)
    if aspect_ratio:
        args["aspect_ratio"] = str(aspect_ratio).strip()
    image_preview = f"{normalized_image[:96]}..." if len(normalized_image) > 96 else normalized_image
    logger.info("FAL_SHARED_HELPER model_slug=%s", model_clean)
    logger.info("FAL_SHARED_HELPER payload_keys=%s", list(args.keys()))
    logger.info("FAL_SHARED_HELPER image_input_preview=%s", image_preview)

    if debug_request_path:
        _write_debug_json(
            Path(debug_request_path),
            {
                "model": model_clean,
                "arguments": args,
                "image_input_type": "data_uri" if normalized_image.startswith("data:") else "url_or_path",
            },
        )

    try:
        response = fal_client.subscribe(model_clean, arguments=args)
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"subscribe failed: {type(exc).__name__}: {str(exc)[:300]}"
        return result

    if debug_response_path:
        _write_debug_json(Path(debug_response_path), response)

    result["response_type"] = type(response).__name__
    if isinstance(response, dict):
        result["response_keys"] = list(response.keys())
    logger.info("FAL_SHARED_HELPER response_type=%s", result["response_type"])
    logger.info("FAL_SHARED_HELPER response_keys=%s", result.get("response_keys", []))

    response_has_video_key = isinstance(response, dict) and "video" in response
    video_url = extract_video_url(response)
    result["video_url"] = _sanitize_url(video_url) if isinstance(video_url, str) else ""
    logger.info("FAL_SHARED_HELPER extracted_video_url=%s", result["video_url"])

    if not video_url:
        if fail_loud_missing_video_artifact and not response_has_video_key:
            raise RuntimeError("Workflow fal response missing video artifact")
        result["error"] = extract_error_message(response) or "no video URL found in structured response"
        return result

    if not download_video(video_url, output):
        result["error"] = "video URL found but download/validation failed"
        return result

    result["ok"] = True
    logger.info("FAL_SHARED_HELPER output_path=%s", str(output))
    return result


def generate_fal_image_to_video(**kwargs: Any) -> dict[str, Any]:
    """Backward-compatible alias for the canonical helper."""
    return generate_fal_video_from_image(**kwargs)
