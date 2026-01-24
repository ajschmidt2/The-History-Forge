# utils.py
import os
import json
import re
import time
import random
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple, Union

from PIL import Image


# ----------------------------
# Secrets
# ----------------------------
def _get_secret(name: str, default: str = "") -> str:
    """
    Get from Streamlit secrets if available; otherwise from environment variables.
    Never raises at import time.
    """
    try:
        import streamlit as st  # type: ignore
        if hasattr(st, "secrets") and name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        pass
    return os.getenv(name, os.getenv(name.upper(), default))


# ----------------------------
# OpenAI client (text)
# ----------------------------
def _openai_client():
    key = _get_secret("openai_api_key", "").strip()
    if not key:
        return None
    try:
        from openai import OpenAI  # openai>=1.x
        return OpenAI(api_key=key)
    except Exception:
        return None


# ----------------------------
# Gemini client (images)
# ----------------------------
def _gemini_client() -> Tuple[Optional[str], Any]:
    """
    Returns (provider_name, client_obj).
    Prefers google-genai.
    """
    key = _get_secret("gemini_api_key", "").strip()
    if not key:
        return None, None

    # Preferred: google-genai
    try:
        from google import genai  # type: ignore
        client = genai.Client(api_key=key)
        return "google-genai", client
    except Exception:
        pass

    # Fallback: google-generativeai (older)
    try:
        import google.generativeai as genai_old  # type: ignore
        genai_old.configure(api_key=key)
        return "google-generativeai", genai_old
    except Exception:
        pass

    return None, None


# ----------------------------
# Script generation
# ----------------------------
def generate_script(topic: str, length: str, tone: str) -> str:
    topic = (topic or "").strip()
    if not topic:
        return "Please enter a topic."

    client = _openai_client()
    if client is None:
        return (
            f"[Missing openai_api_key] Placeholder script for: {topic}\n\n"
            "Add `openai_api_key` in Streamlit Secrets to enable real script generation."
        )

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
        return f"Script generation failed: {type(e).__name__}: {e}"


# ----------------------------
# Scene splitting
# ----------------------------
def split_script_into_scenes(script: str, max_scenes: int = 8) -> List[Dict[str, Any]]:
    script = (script or "").strip()
    if not script:
        return []

    client = _openai_client()
    if client is None:
        paras = [p.strip() for p in re.split(r"\n\s*\n", script) if p.strip()]
        paras = paras[:max_scenes] if paras else [script[:600]]
        return [
            {
                "title": f"Scene {i}",
                "text": p,
                "visual_intent": f"Create a cinematic historical visual matching this excerpt: {p[:180]}...",
            }
            for i, p in enumerate(paras, start=1)
        ]

    system = "You are a video director breaking narration into visual scenes. Return ONLY valid JSON."
    payload = {
        "task": "Split the script into visual scenes.",
        "max_scenes": max_scenes,
        "return": {"format": "json", "field": "scenes"},
        "scene_schema": {"title": "string", "text": "1–3 sentences", "visual_intent": "one sentence"},
        "script": script,
    }

    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            temperature=0.3,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(payload)},
            ],
        )
        data = json.loads(resp.choices[0].message.content)
        scenes = data.get("scenes", [])
        if not isinstance(scenes, list):
            scenes = []

        out: List[Dict[str, Any]] = []
        for i, sc in enumerate(scenes[:max_scenes], start=1):
            title = str(sc.get("title", f"Scene {i}"))
            text = str(sc.get("text", "")).strip()
            vi = str(sc.get("visual_intent", "")).strip() or f"Create a cinematic historical visual matching this excerpt: {text[:180]}..."
            out.append({"title": title, "text": text, "visual_intent": vi})
        return out

    except Exception:
        paras = [p.strip() for p in re.split(r"\n\s*\n", script) if p.strip()]
        paras = paras[:max_scenes] if paras else [script[:600]]
        return [
            {
                "title": f"Scene {i}",
                "text": p,
                "visual_intent": f"Create a cinematic historical visual matching this excerpt: {p[:180]}...",
            }
            for i, p in enumerate(paras, start=1)
        ]


# ----------------------------
# Prompt generation
# ----------------------------
def generate_prompts(
    scenes: Union[List[Dict[str, Any]], List[Any]],
    tone: str,
    style: str = "photorealistic cinematic",
) -> List[str]:
    if not scenes:
        return []

    client = _openai_client()
    if client is None:
        prompts: List[str] = []
        for sc in scenes:
            if isinstance(sc, dict):
                vi = sc.get("visual_intent", "")
                text = sc.get("text", "")
            else:
                vi, text = "", str(sc)
            prompts.append(
                f"{style}. {tone} tone. {vi}\n"
                f"Scene excerpt: {text}\n"
                "No text, no captions, no watermarks. High detail."
            )
        return prompts

    system = (
        "You are an expert image prompt engineer for historical visuals. "
        "Make prompts highly specific: subject, setting, era, lighting, camera feel, atmosphere. "
        "No text overlays. No captions. No watermarks. Return ONLY valid JSON."
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

    payload = {
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
                {"role": "user", "content": json.dumps(payload)},
            ],
        )
        data = json.loads(resp.choices[0].message.content)
        prompts = data.get("prompts", [])
        if not isinstance(prompts, list):
            prompts = []
        while len(prompts) < len(scenes):
            prompts.append("")
        return [str(p).strip() for p in prompts[: len(scenes)]]
    except Exception:
        # fallback
        prompts: List[str] = []
        for sc in scenes:
            if isinstance(sc, dict):
                vi = sc.get("visual_intent", "")
                text = sc.get("text", "")
            else:
                vi, text = "", str(sc)
            prompts.append(
                f"{style}. {tone} tone. {vi}\n"
                f"Scene excerpt: {text}\n"
                "No text, no captions, no watermarks. High detail."
            )
        return prompts


# ----------------------------
# Image generation (Gemini)
# ----------------------------
def _sleep_backoff(attempt: int) -> None:
    # Exponential backoff with jitter
    time.sleep(min(20.0, (2 ** attempt)) + random.random())


def _is_retryable(err: Exception) -> bool:
    msg = str(err).lower()
    return any(k in msg for k in ["429", "too many requests", "quota", "rate limit", "503", "temporarily", "timeout"])


def generate_images_for_scenes(
    scenes: List[Dict[str, Any]],
    aspect_ratio: str = "16:9",
    model_name: str = "gemini-2.5-flash-preview-image",  # <-- matches your screenshot
    **kwargs,
) -> List[Optional[Image.Image]]:
    """
    Generates one PIL image per scene using google-genai.
    Forces response_modalities=["IMAGE"] so you actually get image bytes.
    """
    provider, client = _gemini_client()
    if client is None:
        return [None for _ in scenes]

    print(f"[Gemini provider] {provider} model={model_name}")

    images: List[Optional[Image.Image]] = []

    for sc in scenes:
        base_prompt = (sc.get("prompt") or sc.get("visual_intent") or sc.get("text") or "").strip()
        if not base_prompt:
            base_prompt = "A cinematic historical scene."

        prompt = (
            f"{base_prompt}\n\n"
            f"Framing: compose for aspect ratio {aspect_ratio}. "
            "No text, no captions, no watermarks."
        )

        img: Optional[Image.Image] = None

        # retry for transient failures / occasional 429 bursts
        last_err: Optional[Exception] = None
        for attempt in range(4):
            try:
                if provider == "google-genai":
                    from google.genai import types  # type: ignore

                    resp = client.models.generate_content(
                        model=model_name,
                        contents=[prompt],
                        config=types.GenerateContentConfig(
                            response_modalities=["IMAGE"],  # critical
                        ),
                    )

                    # Extract inline_data bytes
                    for cand in getattr(resp, "candidates", []) or []:
                        content = getattr(cand, "content", None)
                        parts = getattr(content, "parts", []) if content else []
                        for part in parts:
                            inline = getattr(part, "inline_data", None)
                            if inline and getattr(inline, "data", None):
                                img = Image.open(BytesIO(inline.data)).convert("RGB")
                                break
                        if img is not None:
                            break

                    # If still no image, print parts types to logs for debugging
                    if img is None:
                        try:
                            parts_info = []
                            for cand in getattr(resp, "candidates", []) or []:
                                content = getattr(cand, "content", None)
                                parts = getattr(content, "parts", []) if content else []
                                parts_info.append([type(p).__name__ for p in parts])
                            print(f"[Gemini returned no image parts] model={model_name} parts={parts_info}")
                        except Exception as e:
                            print(f"[Gemini debug failed] {e}")

                    # If Gemini returned no bytes, treat as non-retryable unless you want 1 retry
                    # (we’ll allow one retry because sometimes the first attempt returns text)
                    if img is None:
                        raise RuntimeError("No inline image bytes returned (no inline_data.data)")

                else:
                    # Older SDK fallback (less reliable for images)
                    resp = client.GenerativeModel(model_name).generate_content(prompt)
                    candidates = getattr(resp, "candidates", None)
                    if candidates:
                        parts = candidates[0].content.parts
                        for part in parts:
                            inline = getattr(part, "inline_data", None)
                            if inline and getattr(inline, "data", None):
                                img = Image.open(BytesIO(inline.data)).convert("RGB")
                                break
                    if img is None:
                        raise RuntimeError("No inline image bytes returned (older SDK path)")

                # success
                break

            except Exception as e:
                last_err = e
                print(f"[Gemini image gen failed] attempt={attempt+1} err={type(e).__name__}: {e}")
                if _is_retryable(e) and attempt < 3:
                    _sleep_backoff(attempt)
                    continue
                # also retry once for "no image bytes" because sometimes first response is text
                if "no inline image bytes" in str(e).lower() and attempt < 1:
                    _sleep_backoff(attempt)
                    continue
                break

        images.append(img)

    return images
