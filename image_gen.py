import base64
import os
from io import BytesIO
from typing import Any, List, Optional, Sequence

from google import genai


def _get_secret(name: str, default: str = "") -> str:
    try:
        import streamlit as st  # type: ignore

        if hasattr(st, "secrets"):
            candidates = {name, name.lower(), name.upper()}
            for key in candidates:
                if key in st.secrets:
                    return str(st.secrets[key])
    except Exception:
        pass
    return os.getenv(name, os.getenv(name.upper(), default))


def _resolve_api_key() -> str:
    return (
        _get_secret("GOOGLE_AI_STUDIO_API_KEY", "")
        or _get_secret("GEMINI_API_KEY", "")
        or _get_secret("google_ai_studio_api_key", "")
        or _get_secret("gemini_api_key", "")
    ).strip()


def _resolve_model() -> str:
    return (
        _get_secret("GOOGLE_AI_STUDIO_IMAGE_MODEL", "")
        or _get_secret("IMAGEN_MODEL", "")
        or _get_secret("imagen_model", "")
        or "models/imagen-3.0-generate-001"
    ).strip()


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

    # If Imagen returned generated_images metadata but no decodable bytes,
    # treat it as an empty/filtered prompt result (not parser failure).
    if has_generated_images_field:
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

    safety = getattr(result, "positive_prompt_safety_attributes", None)
    if safety is not None:
        details.append("positive_prompt_safety_attributes present")

    return "; ".join(details)


def generate_imagen_images(
    prompt: str,
    number_of_images: int = 1,
    aspect_ratio: str = "16:9",
) -> List[bytes]:
    api_key = _resolve_api_key()
    if not api_key:
        raise RuntimeError("missing google_ai_studio_api_key")

    client = genai.Client(api_key=api_key, http_options={"api_version": "v1beta"})
    model = _resolve_model()

    config = {
        "number_of_images": max(1, int(number_of_images)),
        "output_mime_type": "image/png",
        "aspect_ratio": aspect_ratio,
    }

    try:
        result = client.models.generate_images(
            model=model,
            prompt=prompt,
            config={**config, "person_generation": "ALLOW_ALL"},
        )
    except ValueError as exc:
        if "PersonGeneration.ALLOW_ALL" not in str(exc):
            raise
        result = client.models.generate_images(
            model=model,
            prompt=prompt,
            config=config,
        )

    images = _extract_images(result)
    if images:
        return images

    if _is_likely_filtered_or_empty(result):
        # Return an empty list so callers can handle prompt-level filtering
        # gracefully without treating it as a transport/parsing exception.
        return []

    raise RuntimeError(
        "No images returned from Imagen response. This can happen when the prompt "
        "is blocked by safety filters or when the SDK response payload shape changes. "
        f"{_describe_empty_result(result)}"
    )
