import base64
import os
from io import BytesIO
from typing import Any, List, Optional, Sequence

from google import genai
import streamlit as st


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


def _get_secret(name: str, default: str = "") -> str:
    if hasattr(st, "secrets") and name in st.secrets:
        return _normalize_secret(str(st.secrets.get(name, default)))
    return _normalize_secret(os.getenv(name, default))


def _resolve_api_key() -> str:
    env_keys = (
        "GEMINI_API_KEY",
        "GOOGLE_AI_STUDIO_API_KEY",
        "GOOGLE_API_KEY",
        "gemini_api_key",
        "google_ai_studio_api_key",
        "google_api_key",
    )
    for key_name in env_keys:
        value = os.getenv(key_name, "")
        if value:
            return _normalize_secret(str(value))

    secret_keys = (
        "GEMINI_API_KEY",
        "GOOGLE_AI_STUDIO_API_KEY",
        "GOOGLE_API_KEY",
        "gemini_api_key",
        "google_ai_studio_api_key",
        "google_api_key",
    )
    for key_name in secret_keys:
        value = _get_secret(key_name, "")
        if value:
            return _normalize_secret(str(value))

    return ""


def validate_gemini_api_key() -> str:
    api_key = _resolve_api_key()

    # Keep validation permissive to avoid rejecting valid keys from older/newer formats.
    if not api_key:
        raise RuntimeError(
            "Invalid GOOGLE_AI_STUDIO_API_KEY. Generate a valid Google AI Studio API key "
            "and set it in Streamlit secrets as GEMINI_API_KEY (or GOOGLE_AI_STUDIO_API_KEY)."
        )

    # Ensure both env names are populated for downstream SDKs.
    os.environ["GEMINI_API_KEY"] = api_key
    os.environ["GOOGLE_AI_STUDIO_API_KEY"] = api_key

    return api_key


def _resolve_model() -> str:
    return (
        _get_secret("GOOGLE_AI_STUDIO_IMAGE_MODEL", "")
        or _get_secret("IMAGEN_MODEL", "")
        or _get_secret("imagen_model", "")
        or "gemini-2.5-flash-image"
    ).strip()


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


def _generate_images_with_model(client: genai.Client, model: str, prompt: str, config: dict[str, Any]) -> Any:
    if _is_gemini_image_model(model):
        generation_config: dict[str, Any] = {
            "response_modalities": ["IMAGE"],
            "candidate_count": max(1, int(config.get("number_of_images", 1))),
        }
        return client.models.generate_content(
            model=model,
            contents=prompt,
            config=generation_config,
        )

    try:
        return client.models.generate_images(
            model=model,
            prompt=prompt,
            config={**config, "person_generation": "ALLOW_ALL"},
        )
    except ValueError as exc:
        if "PersonGeneration.ALLOW_ALL" not in str(exc):
            raise
        return client.models.generate_images(
            model=model,
            prompt=prompt,
            config=config,
        )


def generate_imagen_images(
    prompt: str,
    number_of_images: int = 1,
    aspect_ratio: str = "16:9",
) -> List[bytes]:
    api_key = validate_gemini_api_key()

    client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})
    model = _resolve_model()

    config = {
        "number_of_images": max(1, int(number_of_images)),
        "output_mime_type": "image/png",
        "aspect_ratio": aspect_ratio,
    }

    last_error: Optional[Exception] = None
    for candidate_model in _candidate_models(model):
        try:
            result = _generate_images_with_model(
                client=client,
                model=candidate_model,
                prompt=prompt,
                config=config,
            )
        except Exception as exc:  # noqa: BLE001 - bubble non-model errors after fallback attempts
            last_error = exc
            if _is_invalid_api_key_error(exc):
                raise RuntimeError(
                    "invalid google_ai_studio_api_key: API key not valid for generativelanguage.googleapis.com"
                ) from exc
            if _model_not_found_error(exc):
                continue
            raise

        images = _extract_images(result)
        if images:
            return images

        if _is_likely_filtered_or_empty(result):
            # Return an empty list so callers can handle prompt-level filtering
            # gracefully without treating it as a transport/parsing exception.
            return []

        raise RuntimeError(
            "No images returned from image generation response. This can happen when the prompt "
            "is blocked by safety filters or when the SDK response payload shape changes. "
            f"{_describe_empty_result(result)}"
        )

    if last_error is not None:
        raise RuntimeError(
            "Image model was unavailable for this API version. "
            f"Tried models: {', '.join(_candidate_models(model))}. Last error: {last_error}"
        ) from last_error

    raise RuntimeError("Image generation failed before receiving a response.")
