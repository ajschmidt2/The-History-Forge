import base64
import io
import os
from typing import List, Optional, Sequence


def _get_api_key() -> Optional[str]:
    try:
        import streamlit as st  # type: ignore
        return st.secrets.get("GEMINI_API_KEY")
    except Exception:
        return os.getenv("GEMINI_API_KEY")


def _decode_base64_bytes(raw: str) -> Optional[bytes]:
    try:
        return base64.b64decode(raw)
    except Exception:
        return None


def _bytes_from_image_obj(image_obj: object, output_mime_type: str) -> Optional[bytes]:
    if image_obj is None:
        return None
    if hasattr(image_obj, "save"):
        buf = io.BytesIO()
        fmt = "PNG" if output_mime_type == "image/png" else "JPEG"
        try:
            image_obj.save(buf, format=fmt)
            return buf.getvalue()
        except Exception:
            return None
    return None


def _bytes_from_inline_part(part: object) -> Optional[bytes]:
    inline_data = None
    if isinstance(part, dict):
        inline_data = part.get("inlineData") or part.get("inline_data")
    else:
        inline_data = getattr(part, "inline_data", None) or getattr(part, "inlineData", None)
    if not inline_data:
        return None
    data = inline_data.get("data") if isinstance(inline_data, dict) else getattr(inline_data, "data", None)
    if isinstance(data, str):
        return _decode_base64_bytes(data)
    return None


def _extract_images_from_generated(
    generated_images: Sequence[object],
    output_mime_type: str,
) -> List[bytes]:
    images: List[bytes] = []
    for img in generated_images:
        raw = getattr(img, "image_bytes", None)
        if raw is None and isinstance(img, dict):
            raw = img.get("image_bytes")
        if isinstance(raw, str):
            raw = _decode_base64_bytes(raw)
        if raw:
            images.append(raw)
            continue
        raw = _bytes_from_image_obj(getattr(img, "image", None), output_mime_type)
        if raw:
            images.append(raw)
    return images


def _extract_images_from_candidates(
    candidates: Sequence[object],
    output_mime_type: str,
) -> List[bytes]:
    images: List[bytes] = []
    for candidate in candidates:
        content = getattr(candidate, "content", None)
        if isinstance(candidate, dict):
            content = candidate.get("content") or content
        parts = getattr(content, "parts", None)
        if isinstance(content, dict):
            parts = content.get("parts") or parts
        if not parts:
            continue
        for part in parts:
            raw = _bytes_from_inline_part(part)
            if raw:
                images.append(raw)
                continue
            image_obj = None
            if isinstance(part, dict):
                image_obj = part.get("image")
            else:
                image_obj = getattr(part, "image", None)
            raw = _bytes_from_image_obj(image_obj, output_mime_type)
            if raw:
                images.append(raw)
    return images


def generate_imagen_images(
    prompt: str,
    *,
    number_of_images: int = 1,
    aspect_ratio: str = "16:9",
) -> List[bytes]:
    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError(
            "Missing GEMINI_API_KEY. Add it to Streamlit Secrets or environment variables."
        )

    from google import genai  # type: ignore

    client = genai.Client(api_key=api_key)
    model = os.getenv("GOOGLE_AI_STUDIO_IMAGE_MODEL", "models/imagen-4.0-generate-001")

    output_mime_type = "image/jpeg"

    resp = client.models.generate_images(
        model=model,
        prompt=prompt,
        config={
            "number_of_images": number_of_images,
            "aspect_ratio": aspect_ratio,
            "output_mime_type": output_mime_type,
        },
    )

    images: List[bytes] = []
    generated_images = getattr(resp, "generated_images", None) or []
    if isinstance(resp, dict):
        generated_images = resp.get("generated_images") or generated_images
    images.extend(_extract_images_from_generated(generated_images, output_mime_type))

    if not images:
        candidates = getattr(resp, "candidates", None) or []
        if isinstance(resp, dict):
            candidates = resp.get("candidates") or candidates
        images.extend(_extract_images_from_candidates(candidates, output_mime_type))

    if not images:
        raise RuntimeError(
            "No images returned from Imagen response (unexpected response shape). "
            "Verify the model supports image output and inspect the raw response."
        )

    return images
