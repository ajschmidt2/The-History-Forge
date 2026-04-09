from __future__ import annotations

"""Google Gemini Veo Lite image-to-video helper.

Known limitations for ``veo-3.1-lite-generate-preview``:
- No 4K output support.
- No video-extension support.
- Preview model behavior/shape may change.
"""

import base64
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

from src.config import get_secret

logger = logging.getLogger(__name__)

DEFAULT_GOOGLE_VIDEO_MODEL = "veo-3.1-lite-generate-preview"
DEFAULT_OUTPUT_DIR = Path("data/google_veo_video_tests")
MIN_VIDEO_BYTES = 100_000
_SUPPORTED_ASPECT_RATIOS = {"16:9", "9:16", "1:1"}


def _sanitize_obj(obj: Any) -> Any:
    if isinstance(obj, str):
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
    return str(
        get_secret("GEMINI_API_KEY")
        or get_secret("GOOGLE_API_KEY")
        or get_secret("google_ai_studio_api_key")
        or ""
    ).strip()


def google_veo_lite_configured() -> bool:
    return bool(get_gemini_api_key())


def normalize_image_input_for_google(image_source: Any) -> dict[str, str]:
    """Normalize uploaded/url/path input to Google inline/image-url payload."""
    if image_source is None:
        return {"image_url": "", "image_mime_type": ""}

    if isinstance(image_source, str):
        value = image_source.strip()
        if not value:
            return {"image_url": "", "image_mime_type": ""}
        if value.startswith(("http://", "https://")):
            return {"image_url": value, "image_mime_type": ""}
        candidate = Path(value)
        if candidate.exists() and candidate.is_file():
            suffix = candidate.suffix.lower()
            mime = "image/png" if suffix == ".png" else "image/webp" if suffix == ".webp" else "image/jpeg"
            encoded = base64.b64encode(candidate.read_bytes()).decode("utf-8")
            return {"inline_data": encoded, "image_mime_type": mime}
        if value.startswith("data:"):
            return {"inline_data": value, "image_mime_type": ""}
        return {"image_url": value, "image_mime_type": ""}

    if isinstance(image_source, dict):
        for key in ("url", "image_url", "path", "data_uri", "data"):
            raw = image_source.get(key)
            if isinstance(raw, str) and raw.strip():
                return normalize_image_input_for_google(raw)

    if hasattr(image_source, "getvalue") and callable(image_source.getvalue):
        raw = image_source.getvalue()
        if isinstance(raw, (bytes, bytearray)) and raw:
            name = str(getattr(image_source, "name", "")).lower()
            mime = "image/png" if name.endswith(".png") else "image/webp" if name.endswith(".webp") else "image/jpeg"
            return {"inline_data": base64.b64encode(bytes(raw)).decode("utf-8"), "image_mime_type": mime}

    if isinstance(image_source, (bytes, bytearray)) and image_source:
        return {"inline_data": base64.b64encode(bytes(image_source)).decode("utf-8"), "image_mime_type": "image/jpeg"}

    return {"image_url": "", "image_mime_type": ""}


def submit_google_veo_job(
    *,
    api_key: str,
    model: str,
    prompt: str,
    image_payload: dict[str, str],
    aspect_ratio: str,
    duration_seconds: int,
    request_timeout_s: int = 180,
) -> dict[str, Any]:
    """Submit a Veo-lite generation request using defensive endpoint probing."""
    if not api_key:
        return {"ok": False, "error": "GEMINI_API_KEY is not configured."}

    parts: list[dict[str, Any]] = [{"text": str(prompt or "").strip()}]
    inline_data = str(image_payload.get("inline_data") or "").strip()
    image_url = str(image_payload.get("image_url") or "").strip()
    mime_type = str(image_payload.get("image_mime_type") or "image/jpeg").strip() or "image/jpeg"

    if inline_data:
        if inline_data.startswith("data:"):
            parts.append({"inlineData": {"data": inline_data.split(",", 1)[-1], "mimeType": mime_type}})
        else:
            parts.append({"inlineData": {"data": inline_data, "mimeType": mime_type}})
    elif image_url:
        parts.append({"fileData": {"fileUri": image_url, "mimeType": mime_type}})

    generation_config = {
        "responseModalities": ["VIDEO", "AUDIO"],
        "videoConfig": {
            "aspectRatio": aspect_ratio,
            "durationSeconds": int(duration_seconds),
        },
    }

    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": generation_config,
    }

    endpoint_paths = [
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateVideos",
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:predictLongRunning",
    ]
    last_error = "unknown request error"
    for endpoint in endpoint_paths:
        try:
            resp = requests.post(endpoint, params={"key": api_key}, json=payload, timeout=request_timeout_s)
            if resp.ok:
                parsed = resp.json() if resp.content else {}
                return {"ok": True, "response": parsed, "status_code": resp.status_code, "endpoint": endpoint, "request_payload": payload}
            last_error = f"HTTP {resp.status_code}: {resp.text[:500]}"
            if resp.status_code in {400, 404}:
                continue
            return {
                "ok": False,
                "error": last_error,
                "status_code": resp.status_code,
                "endpoint": endpoint,
                "request_payload": payload,
                "raw_text": resp.text[:1000],
            }
        except requests.RequestException as exc:
            last_error = str(exc)
    return {"ok": False, "error": last_error, "request_payload": payload}


def poll_google_veo_job_if_needed(
    *,
    api_key: str,
    submit_response: dict[str, Any],
    poll_interval_s: float = 5.0,
    max_polls: int = 90,
) -> dict[str, Any]:
    """Poll long-running operation responses when needed."""
    if not submit_response.get("ok"):
        return submit_response

    response = submit_response.get("response") or {}
    if not isinstance(response, dict):
        return {"ok": False, "error": "Unexpected Google response shape.", "response": response}

    if response.get("done") is True:
        return {"ok": True, "response": response, "response_type": "operation_done"}

    op_name = str(response.get("name") or "").strip()
    if not op_name:
        return {"ok": True, "response": response, "response_type": "inline_result"}

    operation_url = f"https://generativelanguage.googleapis.com/v1beta/{op_name.lstrip('/')}"
    for _ in range(max_polls):
        try:
            poll_resp = requests.get(operation_url, params={"key": api_key}, timeout=60)
            if not poll_resp.ok:
                return {"ok": False, "error": f"Polling failed HTTP {poll_resp.status_code}: {poll_resp.text[:500]}", "response_type": "operation_poll_error"}
            payload = poll_resp.json()
            if bool(payload.get("done")):
                return {"ok": True, "response": payload, "response_type": "operation_polled"}
        except requests.RequestException as exc:
            return {"ok": False, "error": f"Polling request failed: {exc}", "response_type": "operation_poll_error"}

        import time
        time.sleep(max(0.5, float(poll_interval_s)))

    return {"ok": False, "error": "Google Veo operation timed out while polling.", "response_type": "operation_timeout"}


def extract_google_video_artifact(response_payload: dict[str, Any]) -> dict[str, str]:
    """Extract likely output video URL from a flexible preview-model payload."""

    def _walk(node: Any) -> str:
        if isinstance(node, dict):
            for key in ("video_uri", "videoUrl", "video_url", "uri", "url", "downloadUrl", "download_url", "fileUri", "file_uri"):
                value = node.get(key)
                if isinstance(value, str) and value.startswith(("http://", "https://")) and ("mp4" in value.lower() or "video" in value.lower()):
                    return value
            for value in node.values():
                found = _walk(value)
                if found:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = _walk(item)
                if found:
                    return found
        return ""

    payload = response_payload or {}
    if not isinstance(payload, dict):
        return {"video_url": "", "response_type": "unknown"}

    source = payload.get("response") if isinstance(payload.get("response"), dict) else payload
    video_url = _walk(source)
    if video_url:
        return {"video_url": video_url, "response_type": str(payload.get("response_type") or "google_operation")}

    error_node = source.get("error") if isinstance(source, dict) else None
    error_msg = ""
    if isinstance(error_node, dict):
        error_msg = str(error_node.get("message") or error_node.get("status") or "")

    return {
        "video_url": "",
        "response_type": str(payload.get("response_type") or "google_operation"),
        "error": error_msg[:500],
    }


def download_google_video(video_url: str, output_path: str | Path, *, api_key: str = "") -> tuple[bool, str]:
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    headers: dict[str, str] = {}
    params: dict[str, str] = {}
    if api_key and "generativelanguage.googleapis.com" in video_url and "key=" not in video_url:
        params["key"] = api_key

    try:
        with requests.get(video_url, headers=headers, params=params or None, timeout=300, stream=True) as resp:
            resp.raise_for_status()
            with path.open("wb") as handle:
                for chunk in resp.iter_content(chunk_size=1024 * 128):
                    if chunk:
                        handle.write(chunk)
    except Exception as exc:  # noqa: BLE001
        return False, f"Video download failed: {exc}"

    if not path.exists() or path.stat().st_size < MIN_VIDEO_BYTES:
        return False, f"Downloaded video appears invalid or too small ({path.stat().st_size if path.exists() else 0} bytes)."
    return True, ""


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
    """Generate image-to-video with Gemini Veo 3.1 Lite preview model."""
    result: dict[str, Any] = {
        "ok": False,
        "provider": "google_veo_lite",
        "model": str(model or get_secret("HF_GOOGLE_VIDEO_MODEL", DEFAULT_GOOGLE_VIDEO_MODEL) or DEFAULT_GOOGLE_VIDEO_MODEL),
        "response_type": "",
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

    api_key = get_gemini_api_key()
    if not api_key:
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

    submit = submit_google_veo_job(
        api_key=api_key,
        model=result["model"],
        prompt=prompt_clean,
        image_payload=image_payload,
        aspect_ratio=aspect_ratio,
        duration_seconds=duration_seconds,
    )
    _write_debug_json(debug_request_path, {
        "provider": "google_veo_lite",
        "model": result["model"],
        "aspect_ratio": aspect_ratio,
        "duration_seconds": duration_seconds,
        "prompt_length": len(prompt_clean),
        "submit_request": submit.get("request_payload", {}),
    })

    if not submit.get("ok"):
        result["error"] = str(submit.get("error") or "Failed to submit Google Veo request.")
        result["response_type"] = "submit_error"
        _write_debug_json(debug_response_path, submit)
        result["debug_request_path"] = str(debug_request_path) if debug_request_path else ""
        result["debug_response_path"] = str(debug_response_path) if debug_response_path else ""
        return result

    polled = poll_google_veo_job_if_needed(api_key=api_key, submit_response=submit)
    artifact = extract_google_video_artifact(polled)
    result["response_type"] = str(artifact.get("response_type") or polled.get("response_type") or "unknown")
    result["video_url"] = str(artifact.get("video_url") or "")

    _write_debug_json(debug_response_path, {
        "submit": submit,
        "polled": polled,
        "artifact": artifact,
    })

    if not result["video_url"]:
        result["error"] = str(artifact.get("error") or polled.get("error") or "Google response did not include a downloadable video URL.")[:500]
        result["debug_request_path"] = str(debug_request_path) if debug_request_path else ""
        result["debug_response_path"] = str(debug_response_path) if debug_response_path else ""
        return result

    ok, dl_error = download_google_video(result["video_url"], output_path, api_key=api_key)
    if not ok:
        result["error"] = dl_error
        result["debug_request_path"] = str(debug_request_path) if debug_request_path else ""
        result["debug_response_path"] = str(debug_response_path) if debug_response_path else ""
        return result

    result["ok"] = True
    result["debug_request_path"] = str(debug_request_path) if debug_request_path else ""
    result["debug_response_path"] = str(debug_response_path) if debug_response_path else ""
    return result
