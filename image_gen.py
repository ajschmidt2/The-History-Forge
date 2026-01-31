import base64
import os
from io import BytesIO
from typing import Any, List, Optional

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
        or "models/imagen-4.0-generate-001"
    ).strip()


def _maybe_decode_bytes(value: Any) -> Optional[bytes]:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value)
    if isinstance(value, str):
        try:
            return base64.b64decode(value, validate=True)
        except Exception:
            return None
    return None


def _image_to_png_bytes(image: Any) -> Optional[bytes]:
    if image is None:
        return None
    if isinstance(image, (bytes, bytearray, str)):
        return _maybe_decode_bytes(image)
    if isinstance(image, dict):
        for key in ("image_bytes", "bytes", "data", "b64_json", "b64"):
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


def generate_imagen_images(
    prompt: str,
    number_of_images: int = 1,
    aspect_ratio: str = "16:9",
) -> List[bytes]:
    api_key = _resolve_api_key()
    if not api_key:
        raise RuntimeError("missing google_ai_studio_api_key")

    client = genai.Client(api_key=api_key)
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
    if not images:
        shape = list(getattr(result, "__dict__", {}).keys())
        raise RuntimeError(
            "No images returned from Imagen response (unexpected response shape). "
            f"Response keys: {shape}"
        )
    return images
