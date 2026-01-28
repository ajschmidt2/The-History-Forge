import base64
import os
from typing import List, Optional


def _get_api_key() -> Optional[str]:
    try:
        import streamlit as st  # type: ignore
        return st.secrets.get("GOOGLE_AI_STUDIO_API_KEY")
    except Exception:
        return os.getenv("GOOGLE_AI_STUDIO_API_KEY")


def generate_imagen_images(
    prompt: str,
    *,
    number_of_images: int = 1,
    aspect_ratio: str = "16:9",
) -> List[bytes]:
    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError(
            "Missing GOOGLE_AI_STUDIO_API_KEY. Add it to Streamlit Secrets or environment variables."
        )

    from google import genai  # type: ignore

    client = genai.Client(api_key=api_key)
    model = os.getenv("GOOGLE_AI_STUDIO_IMAGE_MODEL", "models/imagen-4.0-generate-001")

    resp = client.models.generate_images(
        model=model,
        prompt=prompt,
        config={
            "number_of_images": number_of_images,
            "aspect_ratio": aspect_ratio,
            "output_mime_type": "image/png",
        },
    )

    images: List[bytes] = []
    for img in getattr(resp, "generated_images", []) or []:
        raw = getattr(img, "image_bytes", None)
        if raw is None and isinstance(img, dict):
            raw = img.get("image_bytes")
        if isinstance(raw, str):
            try:
                raw = base64.b64decode(raw)
            except Exception:
                raw = None
        if raw:
            images.append(raw)

    if not images:
        raise RuntimeError("No images returned from Imagen response (unexpected response shape).")

    return images
