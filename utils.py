import os
import re
import json
import time
import random
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple
from io import BytesIO

from PIL import Image


# ----------------------------
# Secrets
# ----------------------------
def _get_secret(name: str, default: str = "") -> str:
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

    # Fallback: older SDK
    try:
        import google.generativeai as genai_old  # type: ignore
        genai_old.configure(api_key=key)
        return "google-generativeai", genai_old
    except Exception:
        pass

    return None, None


# ----------------------------
# Scene dataclass
# ----------------------------
@dataclass
class Scene:
    index: int
    title: str
    script_excerpt: str
    visual_intent: str
    image_prompt: str = ""
    image_bytes: Optional[bytes] = None  # PNG bytes (streamlit-safe)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        # Don’t embed raw bytes in JSON export (keep it lightweight)
        d["image_bytes"] = bool(self.image_bytes)
        return d


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

    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        temperature=0.7,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return resp.choices[0].message.content.strip()


# ----------------------------
# Scene splitting
# ----------------------------
def split_script_into_scenes(script: str, max_scenes: int = 8) -> List[Scene]:
    script = (script or "").strip()
    if not script:
        return []

    client = _openai_client()
    if client is None:
        # fallback: paragraph split
        paras = [p.strip() for p in re.split(r"\n\s*\n", script) if p.strip()]
        paras = paras[:max_scenes] if paras else [script[:600]]
        out = []
        for i, p in enumerate(paras, start=1):
            out.append(
                Scene(
                    index=i,
                    title=f"Scene {i}",
                    script_excerpt=p,
                    visual_intent=f"Create a cinematic historical visual matching this excerpt: {p[:180]}...",
                )
            )
        return out

    payload = {
        "task": "Split narration into scenes for visuals.",
        "max_scenes": max_scenes,
        "return": {"format": "json", "field": "scenes"},
        "scene_schema": {"title": "string", "text": "1–3 sentences", "visual_intent": "one sentence"},
        "script": script,
    }

    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        temperature=0.3,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": "Return ONLY valid JSON."},
            {"role": "user", "content": json.dumps(payload)},
        ],
    )
    data = json.loads(resp.choices[0].message.content)
    raw = data.get("scenes", [])
    if not isinstance(raw, list):
        raw = []

    scenes: List[Scene] = []
    for i, sc in enumerate(raw[:max_scenes], start=1):
        title = str(sc.get("title", f"Scene {i}")).strip() or f"Scene {i}"
        text = str(sc.get("text", "")).strip()
        vi = str(sc.get("visual_intent", "")).strip()

        if not text:
            # safe fallback so you never get NoneType errors
            text = script[:240].strip()

        if not vi:
            vi = f"Create a cinematic historical visual matching this excerpt: {text[:180]}..."

        scenes.append(Scene(index=i, title=title, script_excerpt=text, visual_intent=vi))

    return scenes


# ----------------------------
# Prompt generation for scenes
# ----------------------------
def generate_prompts_for_scenes(scenes: List[Scene], tone: str, style: str = "photorealistic cinematic") -> List[Scene]:
    if not scenes:
        return scenes

    client = _openai_client()
    if client is None:
        for s in scenes:
            s.image_prompt = (
                f"{style}. {tone} tone. {s.visual_intent}\n"
                f"Scene excerpt: {s.script_excerpt}\n"
                "No text, no captions, no watermarks. High detail."
            )
        return scenes

    packed = [
        {"index": s.index, "title": s.title, "text": s.script_excerpt, "visual_intent": s.visual_intent}
        for s in scenes
    ]
    payload = {
        "tone": tone,
        "style": style,
        "task": "Write one image prompt per scene.",
        "output": {"format": "json", "field": "prompts"},
        "scenes": packed,
    }

    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        temperature=0.6,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": "Return ONLY valid JSON."},
            {"role": "user", "content": json.dumps(payload)},
        ],
    )
    data = json.loads(resp.choices[0].message.content)
    prompts = data.get("prompts", [])
    if not isinstance(prompts, list):
        prompts = []

    for i, s in enumerate(scenes):
        p = str(prompts[i]).strip() if i < len(prompts) else ""
        if not p:
            p = (
                f"{style}. {tone} tone. {s.visual_intent}\n"
                f"Scene excerpt: {s.script_excerpt}\n"
                "No text, no captions, no watermarks. High detail."
            )
        s.image_prompt = p

    return scenes


# ----------------------------
# Image generation (one scene at a time)
# ----------------------------
def _sleep_backoff(attempt: int) -> None:
    time.sleep(min(20.0, (2 ** attempt)) + random.random())


def _is_retryable(err: Exception) -> bool:
    msg = str(err).lower()
    return any(k in msg for k in ["429", "too many requests", "quota", "rate limit", "503", "temporarily", "timeout"])


def generate_image_for_scene(
    scene: Scene,
    aspect_ratio: str = "16:9",
    model_name: str = "gemini-2.5-flash-preview-image",  # matches what you showed
) -> Scene:
    provider, client = _gemini_client()
    if client is None:
        return scene

    print(f"[Gemini provider] {provider} model={model_name}")

    prompt = (scene.image_prompt or scene.visual_intent or scene.script_excerpt or "").strip()
    if not prompt:
        prompt = "A cinematic historical scene."

    prompt = (
        f"{prompt}\n\n"
        f"Framing: compose for aspect ratio {aspect_ratio}. "
        "No text, no captions, no watermarks."
    )

    png_bytes: Optional[bytes] = None
    last_err: Optional[Exception] = None

    for attempt in range(4):
        try:
            if provider == "google-genai":
                from google.genai import types  # type: ignore

                resp = client.models.generate_content(
                    model=model_name,
                    contents=[prompt],
                    config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
                )

                # Extract inline bytes
                for cand in getattr(resp, "candidates", []) or []:
                    content = getattr(cand, "content", None)
                    parts = getattr(content, "parts", []) if content else []
                    for part in parts:
                        inline = getattr(part, "inline_data", None)
                        if inline and getattr(inline, "data", None):
                            raw = inline.data
                            # Normalize to PNG bytes so Streamlit can display reliably
                            img = Image.open(BytesIO(raw)).convert("RGB")
                            out = BytesIO()
                            img.save(out, format="PNG")
                            png_bytes = out.getvalue()
                            break
                    if png_bytes:
                        break

                if not png_bytes:
                    print("[Gemini returned no image bytes] No inline_data.data in response")
                    raise RuntimeError("No image bytes returned")

            else:
                # Older SDK fallback (less reliable)
                resp = client.GenerativeModel(model_name).generate_content(prompt)
                candidates = getattr(resp, "candidates", None)
                if candidates:
                    parts = candidates[0].content.parts
                    for part in parts:
                        inline = getattr(part, "inline_data", None)
                        if inline and getattr(inline, "data", None):
                            raw = inline.data
                            img = Image.open(BytesIO(raw)).convert("RGB")
                            out = BytesIO()
                            img.save(out, format="PNG")
                            png_bytes = out.getvalue()
                            break

                if not png_bytes:
                    print("[Gemini returned no image bytes] Older SDK path returned no bytes")
                    raise RuntimeError("No image bytes returned (older SDK path)")

            break  # success

        except Exception as e:
            last_err = e
            print(f"[Gemini image gen failed] attempt={attempt+1} {type(e).__name__}: {e}")
            if _is_retryable(e) and attempt < 3:
                _sleep_backoff(attempt)
                continue
            # Also retry once if response came back text-only (no bytes)
            if "no image bytes" in str(e).lower() and attempt < 1:
                _sleep_backoff(attempt)
                continue
            break

    # Write result back to scene
    scene.image_bytes = png_bytes
    return scene
