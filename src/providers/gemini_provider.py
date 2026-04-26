from __future__ import annotations

"""Central Gemini Developer API provider using the Google GenAI SDK."""

import base64
import os
import re
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Sequence

from src.config import get_secret

DEFAULT_GEMINI_TEXT_MODEL = "gemini-2.5-flash"
DEFAULT_GEMINI_FAST_MODEL = "gemini-2.5-flash"
DEFAULT_GEMINI_IMAGE_MODEL = "gemini-2.5-flash-image"
DEFAULT_GEMINI_VIDEO_MODEL = "veo-3.1-lite-generate-preview"
VEO3_ALLOWED_DURATIONS_SECONDS = (4, 6, 8)


class GeminiProviderError(RuntimeError):
    """Base error for user-facing Gemini provider failures."""


class GeminiMissingKeyError(GeminiProviderError):
    """Raised when no Gemini Developer API key is configured."""


class GeminiQuotaError(GeminiProviderError):
    """Raised for rate-limit or quota failures."""


class GeminiModelError(GeminiProviderError):
    """Raised when the configured model name is invalid or unavailable."""


class GeminiEmptyResponseError(GeminiProviderError):
    """Raised when Gemini returns no usable content."""


@dataclass(frozen=True)
class GeminiVideoResult:
    ok: bool
    model: str
    output_path: str
    response_type: str = "generate_videos"
    video_url: str = ""
    error: str = ""
    duration_seconds: float | None = None


_PLACEHOLDER_VALUES = {
    "",
    "none",
    "null",
    "paste_key_here",
    "your_api_key_here",
    "replace_me",
    "your-api-key",
    "your_key_here",
    "aiza...",
}


def _clean(value: object) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        text = text[1:-1].strip()
    return "" if text.lower() in _PLACEHOLDER_VALUES else text


def _secret(*names: str, default: str = "") -> str:
    for name in names:
        value = _clean(os.getenv(name, ""))
        if value:
            return value
    for name in names:
        value = _clean(get_secret(name, ""))
        if value:
            return value
    return _clean(default)


def get_gemini_api_key(*, required: bool = True) -> str:
    api_key = _secret(
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
        "GOOGLE_AI_STUDIO_API_KEY",
        "gemini_api_key",
        "google_ai_studio_api_key",
    )
    if not api_key:
        if not required:
            return ""
        raise GeminiMissingKeyError(
            "Missing Gemini API key. Set GEMINI_API_KEY from Google AI Studio in Streamlit secrets or environment variables."
        )
    os.environ["GEMINI_API_KEY"] = api_key
    return api_key


def get_text_model() -> str:
    return _secret("GEMINI_MODEL_TEXT", default=DEFAULT_GEMINI_TEXT_MODEL)


def get_fast_model() -> str:
    return _secret("GEMINI_MODEL_FAST", default=DEFAULT_GEMINI_FAST_MODEL)


def get_image_model() -> str:
    return _secret(
        "GEMINI_IMAGE_MODEL",
        "GOOGLE_AI_STUDIO_IMAGE_MODEL",
        "IMAGEN_MODEL",
        "imagen_model",
        default=DEFAULT_GEMINI_IMAGE_MODEL,
    )


def get_video_model() -> str:
    return _secret("GEMINI_VIDEO_MODEL", "HF_GOOGLE_VIDEO_MODEL", default=DEFAULT_GEMINI_VIDEO_MODEL)


def normalize_veo_duration_seconds(duration_seconds: object, model: str | None = None) -> int:
    """Return a duration accepted by the target Veo model.

    Veo 3/3.1 models accept only 4, 6, or 8 seconds. Veo 2 accepts 5-8
    seconds. Keeping this normalization central prevents UI defaults from
    producing late API failures.
    """
    try:
        requested = int(round(float(duration_seconds)))
    except (TypeError, ValueError):
        requested = 8
    model_id = str(model or "").strip().lower()
    if "veo-2" in model_id:
        return max(5, min(8, requested))
    allowed = VEO3_ALLOWED_DURATIONS_SECONDS
    return min(allowed, key=lambda value: (abs(value - requested), -value))


def get_client(*, api_version: str = "v1beta"):
    from google import genai

    get_gemini_api_key(required=True)
    return genai.Client(api_key=os.getenv("GEMINI_API_KEY"), http_options={"api_version": api_version})


def _classify_error(exc: Exception) -> GeminiProviderError:
    msg = str(exc)
    low = msg.lower()
    if "api_key_invalid" in low or "api key not valid" in low or "invalid api key" in low:
        return GeminiMissingKeyError("Invalid GEMINI_API_KEY. Create a new key in Google AI Studio and update your secrets.")
    if any(token in low for token in ("quota", "rate limit", "rate_limit", "resource_exhausted", "429")):
        return GeminiQuotaError("Gemini quota or rate limit reached. Retry later or use a lower-cost/faster model.")
    if any(token in low for token in ("not found", "not_found", "model", "404")):
        return GeminiModelError(f"Gemini model is unavailable or invalid: {msg}")
    if any(token in low for token in ("deadline", "timeout", "timed out", "connection", "temporarily unavailable", "503")):
        return GeminiProviderError(f"Gemini API/network failure: {msg}")
    return GeminiProviderError(f"Gemini API failure: {msg}")


def _extract_text(response: Any) -> str:
    direct = _clean(getattr(response, "text", ""))
    if direct:
        return direct
    parts_text: list[str] = []
    for part in getattr(response, "parts", None) or []:
        text = _clean(getattr(part, "text", ""))
        if text:
            parts_text.append(text)
    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            text = _clean(getattr(part, "text", ""))
            if text:
                parts_text.append(text)
    return "\n".join(parts_text).strip()


def generate_text(
    prompt: str | Sequence[Any],
    *,
    model: str | None = None,
    system_instruction: str | None = None,
    temperature: float | None = None,
    max_output_tokens: int | None = None,
) -> str:
    contents = prompt
    config: dict[str, Any] = {}
    if system_instruction:
        config["system_instruction"] = system_instruction
    if temperature is not None:
        config["temperature"] = float(temperature)
    if max_output_tokens is not None:
        config["max_output_tokens"] = int(max_output_tokens)
    try:
        client = get_client()
        response = client.models.generate_content(
            model=model or get_text_model(),
            contents=contents,
            config=config or None,
        )
    except GeminiProviderError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise _classify_error(exc) from exc

    text = _extract_text(response)
    if not text:
        raise GeminiEmptyResponseError("Gemini returned an empty text response.")
    return text


def generate_fast_text(prompt: str | Sequence[Any], **kwargs: Any) -> str:
    return generate_text(prompt, model=kwargs.pop("model", None) or get_fast_model(), **kwargs)


def _maybe_decode_bytes(value: Any) -> bytes | None:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, str):
        normalized = value.strip()
        if "," in normalized and normalized.startswith("data:"):
            normalized = normalized.split(",", 1)[1]
        for decoder in (base64.b64decode, base64.urlsafe_b64decode):
            try:
                padded = normalized + ("=" * (-len(normalized) % 4))
                return decoder(padded)
            except Exception:
                pass
    return None


def _image_to_png_bytes(image: Any) -> bytes | None:
    if image is None:
        return None
    if isinstance(image, (bytes, bytearray, str)):
        return _maybe_decode_bytes(image)
    if isinstance(image, dict):
        for key in ("image_bytes", "bytes", "data", "inline_data", "b64_json", "b64", "encoded_image"):
            raw = _maybe_decode_bytes(image.get(key))
            if raw:
                return raw
        return _image_to_png_bytes(image.get("image"))
    if hasattr(image, "as_image"):
        try:
            return _image_to_png_bytes(image.as_image())
        except Exception:
            pass
    for key in ("image_bytes", "bytes", "data", "inline_data", "b64_json", "b64", "encoded_image"):
        if hasattr(image, key):
            raw = _maybe_decode_bytes(getattr(image, key))
            if raw:
                return raw
    if hasattr(image, "image"):
        raw = _image_to_png_bytes(getattr(image, "image"))
        if raw:
            return raw
    if hasattr(image, "save"):
        buf = BytesIO()
        image.save(buf, format="PNG")
        return buf.getvalue()
    return None


def _extract_images(response: Any) -> list[bytes]:
    images: list[bytes] = []
    for attr in ("generated_images", "images"):
        for item in getattr(response, attr, None) or []:
            raw = _image_to_png_bytes(getattr(item, "image", None)) or _image_to_png_bytes(item)
            if raw:
                images.append(raw)
    if images:
        return images

    for part in getattr(response, "parts", None) or []:
        raw = _image_to_png_bytes(getattr(part, "inline_data", None)) or _image_to_png_bytes(part)
        if raw:
            images.append(raw)
    if images:
        return images

    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        for part in getattr(content, "parts", None) or []:
            raw = _image_to_png_bytes(getattr(part, "inline_data", None)) or _image_to_png_bytes(part)
            if raw:
                images.append(raw)
    return images


def _is_gemini_image_model(model: str) -> bool:
    normalized = str(model or "").strip().lower()
    return normalized.startswith("gemini-") or normalized.startswith("models/gemini-")


def generate_images(
    prompt: str,
    *,
    model: str | None = None,
    number_of_images: int = 1,
    aspect_ratio: str = "9:16",
) -> list[bytes]:
    model_id = model or get_image_model()
    config = {
        "number_of_images": max(1, int(number_of_images)),
        "output_mime_type": "image/png",
        "aspect_ratio": aspect_ratio,
    }
    try:
        client = get_client()
        if _is_gemini_image_model(model_id):
            response = client.models.generate_content(
                model=model_id,
                contents=prompt,
                config={
                    "response_modalities": ["IMAGE"],
                    "candidate_count": max(1, int(number_of_images)),
                },
            )
        else:
            response = client.models.generate_images(
                model=model_id,
                prompt=prompt,
                config={**config, "person_generation": "ALLOW_ALL"},
            )
    except ValueError as exc:
        if "PersonGeneration.ALLOW_ALL" not in str(exc):
            raise _classify_error(exc) from exc
        client = get_client()
        response = client.models.generate_images(model=model_id, prompt=prompt, config=config)
    except Exception as exc:  # noqa: BLE001
        raise _classify_error(exc) from exc

    images = _extract_images(response)
    if not images:
        raise GeminiEmptyResponseError("Gemini image generation returned no image bytes. The prompt may have been filtered.")
    return images


def image_from_source(image_source: Any):
    from google.genai import types

    if image_source is None:
        return None
    if hasattr(image_source, "getvalue"):
        raw = image_source.getvalue()
        name = str(getattr(image_source, "name", "")).lower()
        mime = "image/png" if name.endswith(".png") else "image/webp" if name.endswith(".webp") else "image/jpeg"
        return types.Image(imageBytes=bytes(raw), mimeType=mime)
    if isinstance(image_source, (bytes, bytearray)):
        return types.Image(imageBytes=bytes(image_source), mimeType="image/jpeg")
    if isinstance(image_source, str):
        value = image_source.strip()
        if value.startswith("data:"):
            match = re.match(r"data:([^;]+);base64,(.+)", value, re.DOTALL)
            if match:
                return types.Image(imageBytes=base64.b64decode(match.group(2)), mimeType=match.group(1))
        path = Path(value)
        if path.exists() and path.is_file():
            mime = "image/png" if path.suffix.lower() == ".png" else "image/webp" if path.suffix.lower() == ".webp" else "image/jpeg"
            return types.Image(imageBytes=path.read_bytes(), mimeType=mime)
    return None


def generate_video_from_image(
    *,
    prompt: str,
    image_source: Any,
    output_path: str | Path,
    model: str | None = None,
    aspect_ratio: str = "9:16",
    duration_seconds: int = 8,
    poll_interval_s: float = 10.0,
    max_polls: int = 90,
) -> GeminiVideoResult:
    from google.genai import types

    model_id = model or get_video_model()
    image = image_from_source(image_source)
    if image is None:
        return GeminiVideoResult(False, model_id, str(output_path), error="Image input is required for Gemini Veo image-to-video generation.")

    try:
        client = get_client()
        effective_duration_seconds = normalize_veo_duration_seconds(duration_seconds, model_id)
        operation = client.models.generate_videos(
            model=model_id,
            prompt=str(prompt or "").strip(),
            image=image,
            config=types.GenerateVideosConfig(
                duration_seconds=effective_duration_seconds,
                aspect_ratio=aspect_ratio,
                number_of_videos=1,
            ),
        )
        for _ in range(max(1, int(max_polls))):
            if bool(getattr(operation, "done", False)):
                break
            time.sleep(max(0.5, float(poll_interval_s)))
            operation = client.operations.get(operation)
    except Exception as exc:  # noqa: BLE001
        raise _classify_error(exc) from exc

    if not bool(getattr(operation, "done", False)):
        return GeminiVideoResult(False, model_id, str(output_path), error="Gemini Veo operation timed out while polling.")

    generated_videos = getattr(getattr(operation, "response", None), "generated_videos", None) or []
    if not generated_videos:
        return GeminiVideoResult(False, model_id, str(output_path), error="Gemini Veo returned no generated videos.")

    video = generated_videos[0]
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        video_file = getattr(video, "video", video)
        data = client.files.download(file=video_file)
        if isinstance(data, (bytes, bytearray)) and data:
            path.write_bytes(bytes(data))
        elif hasattr(video_file, "save"):
            video_file.save(str(path))
        else:
            return GeminiVideoResult(False, model_id, str(path), error="Gemini Veo download returned no video bytes.")
    except Exception as exc:  # noqa: BLE001
        raise _classify_error(exc) from exc

    if not path.exists() or path.stat().st_size <= 0:
        return GeminiVideoResult(False, model_id, str(path), error="Gemini Veo video file was not created.")
    return GeminiVideoResult(True, model_id, str(path), duration_seconds=float(effective_duration_seconds))
