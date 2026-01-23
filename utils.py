import os
import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union
from io import BytesIO

from PIL import Image

# ----------------------------
# Secret handling (Streamlit Cloud friendly)
# ----------------------------
def _get_secret(name: str, default: str = "") -> str:
    """
    Get a secret from Streamlit secrets if available; otherwise from env vars.
    Never raises at import time.
    """
    # Try Streamlit secrets (only if streamlit is installed and running)
    try:
        import streamlit as st  # type: ignore
        if hasattr(st, "secrets") and name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass

    # Fallback to environment variables
    return os.getenv(name.upper(), os.getenv(name, default))


# ----------------------------
# OpenAI (text) - lazy init
# ----------------------------
def _openai_client():
    """
    Returns an OpenAI client instance using openai>=1.x if available.
    Lazily created so utils.py doesn't fail import when keys are missing.
    """
    api_key = _get_secret("openai_api_key", "")
    if not api_key:
        return None

    try:
        from openai import OpenAI  # openai>=1.x
        return OpenAI(api_key=api_key)
    except Exception:
        return None


# ----------------------------
# Gemini (images) - lazy init
# Supports both google-genai and google-generativeai
# ----------------------------
def _gemini_client():
    api_key = _get_secret("gemini_api_key", "")
    if not api_key:
        return None, None

    # Preferred: google-genai (newer)
    try:
        from google import genai  # type: ignore
        client = genai.Client(api_key=api_key)
        return ("google-genai", client)
    except Exception:
        pass

    # Fallback: google-generativeai (older)
    try:
        import google.generativeai as genai_old  # type: ignore
        genai_old.configure(api_key=api_key)
        return ("google-generativeai", genai_old)
    except Exception:
        pass

    return None, None


# ----------------------------
# Core: Script generation
# ----------------------------
def generate_script(topic: str, length: str, tone: str) -> str:
    """
    Generate a YouTube-style history narration script.

    If OpenAI key isn't set, returns a placeholder script so the app still runs.
    """
    topic = (topic or "").strip()
    if not topic:
        return "Please enter a topic."

    client = _openai_client()
    if client is None:
        # Safe fallback
        return (
            f"[Missing OpenAI key] Draft script placeholder for: {topic}\n\n"
            "Add `openai_api_key` in Streamlit Cloud → Secrets to enable real script generation."
        )

    # Map length to target words
    target_words = {
        "Short (~60 seconds)": 150,
        "8–10 minutes": 1300,
        "20–30 minutes": 3500,
    }.get(length, 1300)

    system = (
        "You are a YouTube history scriptwriter. Write engaging, accurate narration. "
        "Use a strong hook, clear storytelling, and natural pacing. Avoid stage directions. "
        "End with a quick call-to-action to subscribe."
    )

    user = (
        f"Topic: {topic}\n"
        f"Tone: {tone}\n"
        f"Target length: ~{target_words} words\n\n"
        "Write a single continuous narration script with:\n"
        "1) Hook (1–3 sentences)\n"
        "2) Main story (well-structured paragraphs)\n"
        "3) Ending CTA (1–2 sentences)\n"
        "No headings. No bullet lists."
    )

    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            temperature=0.7,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return resp.choices[0].message.content.strip()
    except Exception as e:
        return f"Script generation failed: {e}"


# ----------------------------
# Scene splitting
# ----------------------------
def split_script_into_scenes(script: str, max_scenes: int = 8) -> List[Dict[str, Any]]:
    """
    Split the narration script into a list of scenes.

    Returns a list of dicts with at least:
      - text (excerpt)
      - visual_intent (best-effort)
      - title (best-effort)

    If OpenAI key isn't set, uses a simple paragraph-based splitter.
    """
    script = (script or "").strip()
    if not script:
        return []

    client = _openai_client()
    if client is None:
        # Fallback: split by paragraphs
        paras = [p.strip() for p in re.split(r"\n\s*\n", script) if p.strip()]
        paras = paras[:max_scenes] if paras else [script[:600]]
        scenes = []
        for i, p in enumerate(paras, start=1):
            scenes.append(
                {
                    "title": f"Scene {i}",
                    "text": p,
                    "visual_intent": f"Create a cinematic historical visual matching this excerpt: {p[:160]}...",
                }
            )
        return scenes

    system = (
        "You are a video director breaking narration into visual scenes. "
        "Return ONLY valid JSON."
    )

    user = {
        "task": "Split the script into scenes for visuals.",
        "max_scenes": max_scenes,
        "requirements": {
            "return_format": "json",
            "scene_fields": ["title", "text", "visual_intent"],
            "text_excerpt_length": "1–3 sentences",
            "visual_intent": "One sentence describing what the image should depict."
        },
        "script": script,
    }

    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            temperature=0.3,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user)},
            ],
        )
        data = json.loads(resp.choices[0].message.content)
        raw = data.get("scenes", data if isinstance(data, list) else [])
        if isinstance(raw, dict) and "scenes" in raw:
            raw = raw["scenes"]
        if not isinstance(raw, list):
            return []

        out = []
        for i, sc in enumerate(raw[:max_scenes], start=1):
            title = str(sc.get("title", f"Scene {i}"))
            text = str(sc.get("text", "")).strip()
            vi = str(sc.get("visual_intent", "")).strip()
            if not vi:
                vi = f"Create a cinematic historical visual matching this excerpt: {text[:160]}..."
            out.append({"title": title, "text": text, "visual_intent": vi})
        return out

    except Exception:
        # Fallback if JSON parsing fails
        paras = [p.strip() for p in re.split(r"\n\s*\n", script) if p.strip()]
        paras = paras[:max_scenes] if paras else [script[:600]]
        return [
            {
                "title": f"Scene {i}",
                "text": p,
                "visual_intent": f"Create a cinematic historical visual matching this excerpt: {p[:160]}...",
            }
            for i, p in enumerate(paras, start=1)
        ]


# ----------------------------
# Prompt generation (for images)
# ----------------------------
def generate_prompts(
    scenes: Union[List[Dict[str, Any]], List[Any]],
    tone: str,
    style: str = "photorealistic cinematic",
) -> List[str]:
    """
    Create image prompts aligned to each scene.
    Returns a list of strings (one per scene).
    """
    if not scenes:
        return []

    client = _openai_client()
    # We can still generate usable prompts without OpenAI
    if client is None:
        prompts = []
        for sc in scenes:
            text = sc.get("text", "") if isinstance(sc, dict) else str(sc)
            vi = sc.get("visual_intent", "") if isinstance(sc, dict) else ""
            prompt = (
                f"{style}. {tone} tone. {vi}\n"
                f"Scene excerpt: {text}\n"
                "No text, no captions, no watermarks. High detail."
            )
            prompts.append(prompt)
        return prompts

    system = (
        "You are an expert image prompt engineer for historical visuals. "
        "Make prompts highly specific: subject, setting, era, lighting, camera feel, atmosphere. "
        "No text overlays. No watermarks."
        "Return ONLY valid JSON."
    )

    packed = []
    for i, sc in enumerate(scenes, start=1):
        if isinstance(sc, dict):
            packed.append(
                {
                    "index": i,
                    "title": sc.get("title", f"Scene {i}"),
                    "text": sc.get("text", ""),
                    "visual_intent": sc.get("visual_intent", ""),
                }
            )
        else:
            packed.append({"index": i, "title": f"Scene {i}", "text": str(sc), "visual_intent": ""})

    user = {
        "tone": tone,
        "style": style,
        "task": "Write one image prompt per scene.",
        "output": {"format": "json", "field": "prompts"},
        "scenes": packed,
    }

    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            temperature=0.6,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user)},
            ],
        )
        data = json.loads(resp.choices[0].message.content)
        prompts = data.get("prompts", [])
        if not isinstance(prompts, list):
            prompts = []
        # Ensure length alignment
        while len(prompts) < len(scenes):
            prompts.append("")
        return [str(p).strip() for p in prompts[: len(scenes)]]
    except Exception:
        # fallback
        prompts = []
        for sc in scenes:
            text = sc.get("text", "") if isinstance(sc, dict) else str(sc)
            vi = sc.get("visual_intent", "") if isinstance(sc, dict) else ""
            prompt = (
                f"{style}. {tone} tone. {vi}\n"
                f"Scene excerpt: {text}\n"
                "No text, no captions, no watermarks. High detail."
            )
            prompts.append(prompt)
        return prompts

from io import BytesIO
from PIL import Image

def generate_images_for_scenes(
    scenes,
    aspect_ratio="16:9",
    model_name="gemini-2.5-flash-preview-image",  # <-- matches your screenshot
    **kwargs,
):
    """
    Generates one PIL.Image per scene using google-genai.
    Forces IMAGE response modality so Gemini actually returns bytes.
    """
    provider, client = _gemini_client()
    if client is None:
        return [None for _ in scenes]

    images = []

    for sc in scenes:
        # Build a prompt from whatever fields exist
        prompt = ""
        if isinstance(sc, dict):
            prompt = (sc.get("prompt") or sc.get("visual_intent") or sc.get("text") or "").strip()
        else:
            prompt = str(getattr(sc, "prompt", "") or getattr(sc, "visual_intent", "") or getattr(sc, "text", "") or sc).strip()

        if not prompt:
            prompt = "A cinematic historical scene."

        prompt = (
            f"{prompt}\n\n"
            f"Framing: compose for aspect ratio {aspect_ratio}. "
            "No text, no captions, no watermarks."
        )

        try:
            # Preferred: google-genai
            if provider == "google-genai":
                from google.genai import types

                resp = client.models.generate_content(
                    model=model_name,
                    contents=[prompt],
                    config=types.GenerateContentConfig(
                        response_modalities=["IMAGE"]  # <-- critical
                    ),
                )

                img = None

                # Some responses return inline_data; others return file_data
                for cand in getattr(resp, "candidates", []) or []:
                    content = getattr(cand, "content", None)
                    parts = getattr(content, "parts", []) if content else []
                    for part in parts:
                        inline = getattr(part, "inline_data", None)
                        if inline and getattr(inline, "data", None):
                            img = Image.open(BytesIO(inline.data)).convert("RGB")
                            break

                        file_data = getattr(part, "file_data", None)
                        # file_data usually requires fetching; if present, we’ll just skip gracefully
                    if img is not None:
                        break

                images.append(img)

            else:
                # Fallback older SDK (may not reliably return image bytes)
                resp = client.GenerativeModel(model_name).generate_content(prompt)
                img = None
                candidates = getattr(resp, "candidates", None)
                if candidates:
                    parts = candidates[0].content.parts
                    for part in parts:
                        inline = getattr(part, "inline_data", None)
                        if inline and getattr(inline, "data", None):
                            img = Image.open(BytesIO(inline.data)).convert("RGB")
                            break
                images.append(img)

        except Exception as e:
            # Helpful logging for Streamlit Cloud logs
            print(f"[Gemini image gen failed] model={model_name} err={type(e).__name__}: {e}")
            images.append(None)

    return images
