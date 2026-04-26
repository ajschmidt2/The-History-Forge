from __future__ import annotations

import base64
import os
from io import BytesIO
from typing import Any, List, Optional, Sequence


_PLACEHOLDER_VALUES = {
    "paste_key_here", "your_api_key_here", "replace_me", "none", "null", "",
    "aiza...", "your-api-key", "your_key_here",
}


def _normalize_secret(value: str) -> str:
    cleaned = str(value or "").strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"\"", "'"}:
        cleaned = cleaned[1:-1].strip()
    if cleaned.lower() in _PLACEHOLDER_VALUES:
        return ""
    return cleaned


from src.config import get_secret as _config_get_secret
from src.providers.gemini_provider import (
    GeminiEmptyResponseError,
    GeminiMissingKeyError,
    GeminiModelError,
    GeminiQuotaError,
    generate_images as _gemini_generate_images,
    get_gemini_api_key as _provider_get_gemini_api_key,
    get_image_model as _provider_get_image_model,
)


def _get_secret(name: str, default: str = "") -> str:
    return _normalize_secret(_config_get_secret(name, default) or "")


def _resolve_api_key() -> str:
    # Check os.environ directly first (e.g. GitHub Actions secrets injected as env vars).
    for env_name in (
        "GEMINI_API_KEY",
        "GOOGLE_AI_STUDIO_API_KEY",
        "GOOGLE_API_KEY",
        "gemini_api_key",
        "google_ai_studio_api_key",
        "google_api_key",
    ):
        value = _normalize_secret(os.environ.get(env_name, ""))
        if value:
            return value

    # Fall back to the full secrets resolver (covers Streamlit secrets, aliases, etc.).
    for key_name in (
        "GEMINI_API_KEY",
        "GOOGLE_AI_STUDIO_API_KEY",
        "GOOGLE_API_KEY",
        "gemini_api_key",
        "google_ai_studio_api_key",
        "google_api_key",
    ):
        value = _get_secret(key_name, "")
        if value:
            return _normalize_secret(str(value))

    return ""


def validate_gemini_api_key(*, required: bool = True) -> str:
    try:
        api_key = _provider_get_gemini_api_key(required=required)
    except GeminiMissingKeyError as exc:
        raise RuntimeError(str(exc)) from exc
    if api_key:
        os.environ["GOOGLE_AI_STUDIO_API_KEY"] = api_key
    return api_key


def _resolve_model() -> str:
    return _provider_get_image_model()


def _is_gemini_image_model(model: str) -> bool:
    normalized = (model or "").lower().strip()
    return normalized.startswith("gemini-") or normalized.startswith("models/gemini-")


def _maybe_decode_bytes(value: Any) -> Optional[bytes]:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, str):
        normalized = value.strip()
        if "," in normalized and normalized.startswith("data:"):
            normalized = normalized.split(",", 1)[1]

        # Try strict decode first, then progressively more permissive formats
        # to cover SDK variations (whitespace, missing padding, urlsafe chars).
        try:
            return base64.b64decode(normalized, validate=True)
        except Exception:
            pass

        try:
            padded = normalized + ("=" * (-len(normalized) % 4))
            return base64.b64decode(padded)
        except Exception:
            pass

        try:
            padded = normalized + ("=" * (-len(normalized) % 4))
            return base64.urlsafe_b64decode(padded)
        except Exception:
            return None
    return None


def _image_to_png_bytes(image: Any) -> Optional[bytes]:
    if image is None:
        return None
    if isinstance(image, (bytes, bytearray, str)):
        return _maybe_decode_bytes(image)
    if isinstance(image, dict):
        for key in (
            "image_bytes",
            "bytes",
            "data",
            "inline_data",
            "b64_json",
            "b64",
            "encoded_image",
        ):
            raw = _maybe_decode_bytes(image.get(key))
            if raw:
                return raw
        nested = image.get("image")
        if nested is not None:
            return _image_to_png_bytes(nested)
    if hasattr(image, "image_bytes"):
        data = getattr(image, "image_bytes")
        raw = _maybe_decode_bytes(data)
        if raw:
            return raw
    for key in ("bytes", "data", "inline_data", "b64_json", "b64", "encoded_image"):
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
    if hasattr(image, "to_bytes"):
        data = image.to_bytes()
        if isinstance(data, (bytes, bytearray)):
            return bytes(data)
    return None


def _extract_images(result: Any) -> List[bytes]:
    images: List[bytes] = []

    generated_images = getattr(result, "generated_images", None)
    if generated_images is not None:
        for item in generated_images:
            if item is None:
                continue
            if isinstance(item, dict):
                raw = _image_to_png_bytes(item.get("image")) or _image_to_png_bytes(item)
                if raw:
                    images.append(raw)
                continue
            img = getattr(item, "image", None)
            raw = _image_to_png_bytes(img) or _image_to_png_bytes(item)
            if raw:
                images.append(raw)

    if images:
        return images

    alt_images = getattr(result, "images", None)
    if alt_images:
        for item in alt_images:
            raw = _image_to_png_bytes(item)
            if raw:
                images.append(raw)

    if images:
        return images

    candidates = getattr(result, "candidates", None)
    if candidates:
        for candidate in candidates:
            content = getattr(candidate, "content", None)
            parts = getattr(content, "parts", None)
            if not parts:
                continue
            for part in parts:
                inline_data = getattr(part, "inline_data", None)
                raw = _image_to_png_bytes(inline_data) or _image_to_png_bytes(part)
                if raw:
                    images.append(raw)

    return images


def _sequence_length(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, Sequence):
        return len(value)
    try:
        return len(value)
    except Exception:
        return None


def _is_likely_filtered_or_empty(result: Any) -> bool:
    generated_images = getattr(result, "generated_images", None)
    generated_len = _sequence_length(generated_images)

    result_dict = getattr(result, "__dict__", {})
    response_keys = set(result_dict.keys()) if isinstance(result_dict, dict) else set()

    has_generated_images_field = hasattr(result, "generated_images") or "generated_images" in response_keys
    has_safety_field = (
        hasattr(result, "positive_prompt_safety_attributes")
        or "positive_prompt_safety_attributes" in response_keys
    )
    has_candidates_field = hasattr(result, "candidates") or "candidates" in response_keys

    # If image metadata exists but no decodable bytes, this is usually
    # a filtered/empty response rather than a parser bug.
    if has_generated_images_field or has_candidates_field:
        return True

    # Also treat explicit safety metadata as a likely filtered response.
    if has_safety_field and generated_len in (None, 0):
        return True

    return False


def _describe_empty_result(result: Any) -> str:
    keys = list(getattr(result, "__dict__", {}).keys())
    details: List[str] = [f"Response keys: {keys}"]

    generated_images = getattr(result, "generated_images", None)
    if generated_images is not None:
        try:
            details.append(f"generated_images length: {len(generated_images)}")
        except Exception:
            details.append("generated_images present but length unavailable")

    candidates = getattr(result, "candidates", None)
    if candidates is not None:
        try:
            details.append(f"candidates length: {len(candidates)}")
        except Exception:
            details.append("candidates present but length unavailable")

    safety = getattr(result, "positive_prompt_safety_attributes", None)
    if safety is not None:
        details.append("positive_prompt_safety_attributes present")

    return "; ".join(details)


def _model_not_found_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "not_found" in msg
        or "is not found" in msg
        or "not supported for predict" in msg
        or "404" in msg
    )


def _is_invalid_api_key_error(exc: Exception) -> bool:
    msg = str(exc).lower()
    return any(
        token in msg
        for token in (
            "api_key_invalid",
            "api key not valid",
            "invalid api key",
            "invalid_argument",
        )
    ) and "api key" in msg


def _candidate_models(primary_model: str) -> list[str]:
    candidates = [
        primary_model,
        "gemini-2.5-flash-image",
        "imagen-3.0-generate-002",
        "imagen-3.0-generate-001",
    ]
    seen: set[str] = set()
    ordered: list[str] = []
    for model in candidates:
        normalized = (model or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        ordered.append(normalized)
    return ordered


def generate_falai_images(
    prompt: str,
    number_of_images: int = 1,
    aspect_ratio: str = "16:9",
) -> List[bytes]:
    """
    Generate images using fal.ai FLUX Dev.

    Model: fal-ai/flux/dev
      - Swap to "fal-ai/flux/schnell" for faster/cheaper generation.
      - Swap to "fal-ai/flux-pro/v1.1" for highest quality.

    Aspect-ratio → pixel mapping mirrors the documentary-style defaults
    used throughout the History Forge pipeline.
    """
    import os as _os

    try:
        import fal_client  # type: ignore
    except ImportError as exc:  # pragma: no cover - surfaced as runtime error
        raise RuntimeError(
            "fal-client is not installed. Run: pip install fal-client"
        ) from exc

    api_key = _get_secret("fal_api_key", "")
    if not api_key:
        raise RuntimeError(
            "fal_api_key not found in secrets. Add it to .streamlit/secrets.toml"
        )

    # fal_client reads FAL_KEY from the environment.
    _os.environ["FAL_KEY"] = api_key

    ar = (aspect_ratio or "16:9").strip()
    if ar == "9:16":
        width, height = 1024, 1792
    elif ar == "1:1":
        width, height = 1024, 1024
    else:  # default 16:9
        width, height = 1792, 1024

    try:
        result = fal_client.subscribe(
            "fal-ai/flux/dev",
            arguments={
                "prompt": prompt,
                "image_size": {"width": width, "height": height},
                "num_inference_steps": 28,
                "guidance_scale": 3.5,
                "num_images": max(1, int(number_of_images)),
                "enable_safety_checker": True,
                "output_format": "jpeg",
            },
        )
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"fal.ai image generation failed: {exc}") from exc

    images: List[bytes] = []
    if result and result.get("images"):
        import requests as _requests
        for item in result["images"]:
            url = item.get("url") if isinstance(item, dict) else None
            if not url:
                continue
            try:
                resp = _requests.get(url, timeout=60)
                resp.raise_for_status()
                images.append(resp.content)
            except Exception:  # noqa: BLE001
                continue
    return images


def generate_scene_image_bytes(
    prompt: str,
    number_of_images: int = 1,
    aspect_ratio: str = "16:9",
    provider: str = "gemini",
) -> List[bytes]:
    """
    Provider-routed scene image generation.

    provider:
      - "gemini" — Google Imagen / Gemini image model (default)
      - "falai"  — fal.ai FLUX Dev fallback
    """
    provider = (provider or "gemini").strip().lower()
    if provider == "gemini":
        return generate_imagen_images(prompt, number_of_images=number_of_images, aspect_ratio=aspect_ratio)
    if provider == "falai":
        return generate_falai_images(prompt, number_of_images=number_of_images, aspect_ratio=aspect_ratio)
    # Unknown provider names use the primary Gemini path.
    return generate_imagen_images(prompt, number_of_images=number_of_images, aspect_ratio=aspect_ratio)


def generate_imagen_images(
    prompt: str,
    number_of_images: int = 1,
    aspect_ratio: str = "16:9",
) -> List[bytes]:
    model = _resolve_model()

    last_error: Optional[Exception] = None
    for candidate_model in _candidate_models(model):
        try:
            return _gemini_generate_images(
                prompt,
                model=candidate_model,
                number_of_images=number_of_images,
                aspect_ratio=aspect_ratio,
            )
        except Exception as exc:  # noqa: BLE001 - bubble non-model errors after fallback attempts
            last_error = exc
            if isinstance(exc, GeminiMissingKeyError) or _is_invalid_api_key_error(exc):
                raise RuntimeError(
                    "invalid google_ai_studio_api_key: API key not valid for generativelanguage.googleapis.com"
                ) from exc
            if isinstance(exc, GeminiModelError) or _model_not_found_error(exc):
                continue
            if isinstance(exc, GeminiEmptyResponseError):
                return []
            if isinstance(exc, GeminiQuotaError):
                raise RuntimeError(str(exc)) from exc
            raise

    if last_error is not None:
        raise RuntimeError(
            "Image model was unavailable for this API version. "
            f"Tried models: {', '.join(_candidate_models(model))}. Last error: {last_error}"
        ) from last_error

    raise RuntimeError("Image generation failed before receiving a response.")
