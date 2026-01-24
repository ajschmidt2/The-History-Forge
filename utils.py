import os
import re
import json
import time
import random
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Tuple
from io import BytesIO

import requests
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


def get_secret(name: str, default: str = "") -> str:
    return _get_secret(name, default)


# ----------------------------
# Clients
# ----------------------------
def _openai_client():
    key = _get_secret("openai_api_key", "").strip()
    if not key:
        return None
    from openai import OpenAI  # openai>=1.x
    return OpenAI(api_key=key)


def _gemini_client() -> Tuple[Optional[str], Any]:
    key = _get_secret("gemini_api_key", "").strip()
    if not key:
        return None, None

    try:
        from google import genai  # type: ignore
        client = genai.Client(api_key=key)
        return "google-genai", client
    except Exception:
        pass

    try:
        import google.generativeai as genai_old  # type: ignore
        genai_old.configure(api_key=key)
        return "google-generativeai", genai_old
    except Exception:
        pass

    return None, None


def _elevenlabs_api_key() -> str:
    return _get_secret("elevenlabs_api_key", "").strip()


# ----------------------------
# Data model
# ----------------------------
@dataclass
class Scene:
    index: int
    title: str
    script_excerpt: str
    visual_intent: str
    image_prompt: str = ""
    image_bytes: Optional[bytes] = None  # PNG bytes (streamlit-safe)
    image_variations: List[Optional[bytes]] = field(default_factory=list)
    primary_image_index: int = 0
    supabase_id: Optional[str] = None
    status: str = "active"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["image_bytes"] = bool(self.image_bytes)
        d["image_variations"] = [bool(b) for b in self.image_variations]
        d["primary_image_index"] = self.primary_image_index
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
# Deterministic fallback chunking (ENFORCES N scenes)
# ----------------------------
def _fallback_chunk_scenes(script: str, target_n: int) -> List[Scene]:
    script = (script or "").strip()
    if not script:
        return []

    # Split by paragraphs first
    paras = [p.strip() for p in re.split(r"\n\s*\n", script) if p.strip()]
    if not paras:
        paras = [script]

    # If we already have enough paragraphs, take first N
    if len(paras) >= target_n:
        use = paras[:target_n]
    else:
        # Otherwise, chunk the whole script into roughly N equal character chunks
        # (more stable than asking the model again)
        joined = "\n\n".join(paras)
        L = len(joined)
        step = max(200, L // target_n)  # minimum chunk size
        use = []
        start = 0
        while start < L and len(use) < target_n:
            end = min(L, start + step)
            # try to break at sentence boundary near end
            window = joined[start:end]
            m = re.search(r"(.+?[.!?])\s", window[::-1])
            # m is not super useful reversed; keep simple:
            use.append(joined[start:end].strip())
            start = end

        # If still short, pad with last chunk
        while len(use) < target_n:
            use.append(use[-1])

    scenes = []
    for i, txt in enumerate(use, start=1):
        txt2 = txt.strip()
        if not txt2:
            txt2 = script[:240].strip()
        scenes.append(
            Scene(
                index=i,
                title=f"Scene {i}",
                script_excerpt=txt2,
                visual_intent=f"Create a strong historical visual that matches this excerpt: {txt2[:180]}...",
            )
        )
    return scenes


# ----------------------------
# Scene splitting (LLM + ENFORCE exact N)
# ----------------------------
def split_script_into_scenes(script: str, max_scenes: int = 8) -> List[Scene]:
    script = (script or "").strip()
    if not script:
        return []

    max_scenes = min(max_scenes, 75)

    # 1) Start with deterministic fallback that ALWAYS produces exactly N
    fallback = _fallback_chunk_scenes(script, max_scenes)

    client = _openai_client()
    if client is None:
        return fallback

    payload = {
        "task": "Split narration into scenes for visuals.",
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
                {"role": "system", "content": "Return ONLY valid JSON."},
                {"role": "user", "content": json.dumps(payload)},
            ],
        )

        data = json.loads(resp.choices[0].message.content)
        raw = data.get("scenes", [])
        if not isinstance(raw, list):
            raw = []

        # 2) Overlay model results onto fallback (keeping exact length)
        for i in range(min(len(raw), max_scenes)):
            sc = raw[i] if isinstance(raw[i], dict) else {}
            title = str(sc.get("title", fallback[i].title)).strip() or fallback[i].title
            text = str(sc.get("text", fallback[i].script_excerpt)).strip() or fallback[i].script_excerpt
            vi = str(sc.get("visual_intent", fallback[i].visual_intent)).strip() or fallback[i].visual_intent

            fallback[i] = Scene(
                index=i + 1,
                title=title,
                script_excerpt=text,
                visual_intent=vi,
                image_prompt=fallback[i].image_prompt,
                image_bytes=fallback[i].image_bytes,
            )

    except Exception:
        # If OpenAI fails or returns weird JSON, we still return fallback
        pass

    # 3) Guarantee numbering + exact length
    fallback = fallback[:max_scenes]
    for i, s in enumerate(fallback, start=1):
        s.index = i
        if not s.title:
            s.title = f"Scene {i}"
        if not s.script_excerpt:
            s.script_excerpt = script[:240].strip()
        if not s.visual_intent:
            s.visual_intent = f"Create a strong historical visual matching: {s.script_excerpt[:180]}..."

    return fallback

# ----------------------------
# Prompt generation (ENFORCE one prompt per scene)
# ----------------------------
def generate_prompts_for_scenes(
    scenes: List[Scene],
    tone: str,
    style: str = "Photorealistic cinematic",
) -> List[Scene]:
    if not scenes:
        return scenes

    client = _openai_client()
    style_phrase = style.strip() or "Photorealistic cinematic"

    # fallback (no OpenAI) — still ensures prompts exist
    if client is None:
        for s in scenes:
            s.image_prompt = (
                f"Style: {style_phrase}. Tone: {tone}.\n"
                f"{s.visual_intent}\n"
                f"Scene excerpt: {s.script_excerpt}\n"
                "No text overlays, captions, logos, or watermarks. High detail."
            )
        return scenes

    packed = [
        {"index": s.index, "title": s.title, "text": s.script_excerpt, "visual_intent": s.visual_intent}
        for s in scenes
    ]
    payload = {
        "tone": tone,
        "style": style_phrase,
        "task": "Write one image prompt per scene. Return exactly one prompt per scene in the same order.",
        "output": {"format": "json", "field": "prompts"},
        "scenes": packed,
        "constraints": [
            "No text overlays, captions, logos, or watermarks.",
            "Be specific about subject, setting, era cues, lighting, mood, camera feel.",
            "Match the selected style strongly."
        ],
    }

    prompts: List[str] = []
    try:
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
        raw = data.get("prompts", [])
        if isinstance(raw, list):
            prompts = [str(x).strip() for x in raw]
    except Exception:
        prompts = []

    # ✅ ENFORCE count == scenes count
    while len(prompts) < len(scenes):
        prompts.append("")
    prompts = prompts[:len(scenes)]

    for i, s in enumerate(scenes):
        p = prompts[i].strip()
        if not p:
            p = (
                f"Style: {style_phrase}. Tone: {tone}.\n"
                f"{s.visual_intent}\n"
                f"Scene excerpt: {s.script_excerpt}\n"
                "No text overlays, captions, logos, or watermarks. High detail."
            )
        s.image_prompt = p

    return scenes


# ----------------------------
# Aspect ratio enforcement (guaranteed)
# ----------------------------
def _crop_to_aspect(img: Image.Image, aspect_ratio: str) -> Image.Image:
    ar_map = {"16:9": (16, 9), "9:16": (9, 16), "1:1": (1, 1)}
    w, h = img.size
    a, b = ar_map.get(aspect_ratio, (16, 9))
    target = a / b
    current = w / h

    if abs(current - target) < 0.01:
        return img

    if current > target:
        new_w = int(h * target)
        left = (w - new_w) // 2
        return img.crop((left, 0, left + new_w, h))
    else:
        new_h = int(w / target)
        top = (h - new_h) // 2
        return img.crop((0, top, w, top + new_h))


# ----------------------------
# Robust extraction of image bytes from google-genai responses
# ----------------------------
def _extract_image_bytes_google_genai(resp: Any) -> Optional[bytes]:
    # Try object path: resp.candidates[].content.parts[].inline_data.data
    try:
        for cand in getattr(resp, "candidates", []) or []:
            content = getattr(cand, "content", None)
            parts = getattr(content, "parts", []) if content else []
            for part in parts:
                inline = getattr(part, "inline_data", None)
                data = getattr(inline, "data", None) if inline else None
                if data:
                    return data
    except Exception:
        pass

    # Try dict path (SDK sometimes provides to_dict-like objects)
    try:
        if hasattr(resp, "to_dict"):
            d = resp.to_dict()
        elif isinstance(resp, dict):
            d = resp
        else:
            d = None

        if d:
            cands = d.get("candidates", [])
            for cand in cands:
                content = cand.get("content", {})
                parts = content.get("parts", [])
                for part in parts:
                    inline = part.get("inline_data") or part.get("inlineData") or {}
                    data = inline.get("data")
                    if data:
                        return data
    except Exception:
        pass

    return None


def _sleep_backoff(attempt: int) -> None:
    time.sleep(min(20.0, (2 ** attempt)) + random.random())


def _is_retryable(err: Exception) -> bool:
    msg = str(err).lower()
    return any(k in msg for k in ["429", "too many requests", "quota", "rate limit", "503", "temporarily", "timeout"])


# ----------------------------
# Image generation (one scene)
# ----------------------------
def generate_image_for_scene(
    scene: Scene,
    aspect_ratio: str = "16:9",
    visual_style: str = "Photorealistic cinematic",
    model_name: str = "gemini-2.5-flash-image",
) -> Scene:
    provider, client = _gemini_client()
    if client is None:
        return scene

    base = (scene.image_prompt or scene.visual_intent or scene.script_excerpt or "").strip()
    if not base:
        base = "A cinematic historical scene."

    # Keep prompt concise; too long can reduce compliance
    prompt = (
        f"Style: {visual_style}.\n"
        f"{base}\n"
        f"Compose for {aspect_ratio}. No text, logos, captions, or watermarks."
    )

    png_bytes: Optional[bytes] = None
    last_error: Optional[str] = None

    for attempt in range(4):
        try:
            if provider == "google-genai":
                from google.genai import types  # type: ignore

                resp = client.models.generate_content(
                    model=model_name,
                    contents=[prompt],
                    config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
                )

                raw = _extract_image_bytes_google_genai(resp)
                if not raw:
                    raise RuntimeError("Gemini returned no image bytes (no inline_data.data found)")

                img = Image.open(BytesIO(raw)).convert("RGB")
                img = _crop_to_aspect(img, aspect_ratio)

                out = BytesIO()
                img.save(out, format="PNG")
                png_bytes = out.getvalue()
                break

            else:
                # Older SDK fallback (best effort)
                resp = client.GenerativeModel(model_name).generate_content(prompt)
                raw = None
                try:
                    candidates = getattr(resp, "candidates", None)
                    if candidates:
                        parts = candidates[0].content.parts
                        for part in parts:
                            inline = getattr(part, "inline_data", None)
                            if inline and getattr(inline, "data", None):
                                raw = inline.data
                                break
                except Exception:
                    raw = None

                if not raw:
                    raise RuntimeError("Older Gemini SDK returned no image bytes")

                img = Image.open(BytesIO(raw)).convert("RGB")
                img = _crop_to_aspect(img, aspect_ratio)
                out = BytesIO()
                img.save(out, format="PNG")
                png_bytes = out.getvalue()
                break

        except Exception as e:
            last_error = f"{type(e).__name__}: {e}"
            print(f"[Gemini image gen failed] attempt={attempt+1} {last_error}")
            if _is_retryable(e) and attempt < 3:
                _sleep_backoff(attempt)
                continue
            break

    if not png_bytes and last_error:
        print(f"[Gemini image gen final] FAILED: {last_error}")

    scene.image_bytes = png_bytes
    return scene


# ----------------------------
# Voiceover generation (ElevenLabs)
# ----------------------------
def generate_voiceover(
    script: str,
    voice_id: str,
    output_format: str = "mp3",
    model_id: str = "eleven_multilingual_v2",
) -> Tuple[Optional[bytes], Optional[str]]:
    script = (script or "").strip()
    if not script:
        return None, "Script is empty."

    api_key = _elevenlabs_api_key()
    if not api_key:
        return None, "[Missing elevenlabs_api_key] Add it in Streamlit Secrets."

    voice_id = (voice_id or "").strip()
    if not voice_id:
        return None, "Voice ID is required."

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "accept": "audio/mpeg" if output_format == "mp3" else "audio/wav",
        "content-type": "application/json",
    }
    payload = {
        "text": script,
        "model_id": model_id,
    }

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        if resp.status_code >= 400:
            return None, f"ElevenLabs error {resp.status_code}: {resp.text}"
        return resp.content, None
    except Exception as exc:
        return None, f"ElevenLabs request failed: {exc}"
