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

from image_gen import generate_imagen_images

# ----------------------------
# Secrets
# ----------------------------
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
    status: str = "active"
    image_error: str = ""

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


def generate_lucky_topic() -> str:
    client = _openai_client()
    if client is None:
        return random.choice(
            [
                "The Lost City of Cahokia",
                "The Great Fire of London",
                "The Spy Who Fooled Hitler",
                "The Silk Road's Hidden Empires",
                "The Mystery of the Mary Celeste",
                "The Battle Won by an Eclipse",
            ]
        )

    system = (
        "You are a history curator. Provide a single intriguing, lesser-known history topic "
        "title suitable for a short YouTube documentary."
    )
    user = "Give me one unique historical story idea. Respond with only the title."
    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        temperature=1.0,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return resp.choices[0].message.content.strip().strip('"')


def rewrite_description(script: str, description: str, mode: str = "refresh") -> str:
    script = (script or "").strip()
    description = (description or "").strip()
    if not description:
        return ""

    client = _openai_client()
    if client is None:
        return (
            "[Missing openai_api_key] Unable to rewrite description. "
            "Add `openai_api_key` in Streamlit Secrets to enable AI edits."
        )

    system = (
        "You are a YouTube metadata assistant. Rewrite descriptions clearly and concisely. "
        "Preserve facts from the script and keep it YouTube-ready."
    )
    user = (
        f"Script excerpt:\n{script[:1200]}\n\n"
        f"Current description:\n{description}\n\n"
        f"Rewrite mode: {mode}\n"
        "Return only the rewritten description."
    )

    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        temperature=0.6,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return resp.choices[0].message.content.strip()


def generate_video_titles(topic: str, script: str, count: int = 5) -> List[str]:
    topic = (topic or "").strip()
    script = (script or "").strip()
    count = max(1, min(int(count), 12))

    client = _openai_client()
    if client is None:
        base = topic or "Untitled History Story"
        return [
            f"{base}: The Forgotten Turning Point",
            f"The Hidden Truth Behind {base}",
            f"{base} in 10 Minutes",
            f"{base}: What Really Happened",
            f"The Untold Story of {base}",
        ][:count]

    system = (
        "You are a YouTube title strategist for history documentaries. "
        "Generate compelling, accurate, curiosity-driven titles without clickbait."
    )
    user = (
        f"Topic: {topic or 'History documentary'}\n"
        f"Script excerpt:\n{script[:1200]}\n\n"
        f"Return exactly {count} titles as a JSON array of strings."
    )
    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        temperature=0.7,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    raw = resp.choices[0].message.content.strip()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()][:count]
    except json.JSONDecodeError:
        pass
    return [line.strip("- ").strip() for line in raw.splitlines() if line.strip()][:count]


def generate_thumbnail_prompt(topic: str, title: str, style: str) -> str:
    topic = (topic or "").strip()
    title = (title or "").strip()
    style = (style or "").strip() or "cinematic"

    client = _openai_client()
    if client is None:
        base = title or topic or "Epic historical moment"
        return (
            f"{base}, {style} lighting, dramatic composition, high contrast, sharp focus, "
            "no text, no watermark, YouTube thumbnail style, 16:9."
        )

    system = (
        "You craft concise image prompts for historical YouTube thumbnails. "
        "Use vivid cinematic descriptors and avoid any on-image text."
    )
    user = (
        f"Topic: {topic}\nTitle: {title}\nStyle: {style}\n\n"
        "Write one short prompt (1-2 sentences) for a 16:9 YouTube thumbnail image. "
        "No text, no logos, no watermarks."
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


def generate_thumbnail_image(prompt: str, aspect_ratio: str = "16:9") -> Tuple[Optional[bytes], str]:
    base = (prompt or "").strip()
    if not base:
        return None, "Enter a thumbnail prompt first."
    try:
        images = generate_imagen_images(base, number_of_images=1, aspect_ratio=aspect_ratio)
    except Exception as exc:  # noqa: BLE001 - surface image generation errors
        return None, str(exc)
    if not images:
        return None, "No image bytes returned."
    return images[0], ""


# ----------------------------
# Deterministic fallback chunking (ENFORCES N scenes)
# ----------------------------
def _split_into_groups(items: List[str], target_n: int) -> List[List[str]]:
    if target_n <= 0:
        return []
    total = len(items)
    if total == 0:
        return [[] for _ in range(target_n)]
    base = total // target_n
    extra = total % target_n
    groups: List[List[str]] = []
    idx = 0
    for i in range(target_n):
        take = base + (1 if i < extra else 0)
        groups.append(items[idx:idx + take])
        idx += take
    return groups


def _fallback_chunk_scenes(script: str, target_n: int) -> List[Scene]:
    script = (script or "").strip()
    if not script or target_n <= 0:
        return []

    # Prefer sentence-based splitting so scenes stay aligned throughout the script.
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", script) if s.strip()]
    use: List[str] = []

    if len(sentences) >= target_n:
        groups = _split_into_groups(sentences, target_n)
        for group in groups:
            use.append(" ".join(group).strip())
    else:
        # If not enough sentences, fall back to word-based splitting.
        words = script.split()
        if not words:
            return []
        word_groups = _split_into_groups(words, target_n)
        for group in word_groups:
            use.append(" ".join(group).strip())

    scenes = []
    for i, txt in enumerate(use, start=1):
        txt2 = txt.strip() or script[:240].strip()
        scenes.append(
            Scene(
                index=i,
                title=f"Scene {i}",
                script_excerpt=txt2,
                visual_intent=(
                    "Create a strong historical visual that matches this excerpt. "
                    "Identify the likely time period, location, and key setting details from the excerpt: "
                    f"{txt2[:180]}..."
                ),
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

        # 2) Only overlay when the model returns a full set to preserve even coverage.
        if len(raw) >= max_scenes:
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
            s.visual_intent = (
                "Create a strong historical visual. Identify the time period and location from the excerpt: "
                f"{s.script_excerpt[:180]}..."
            )

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
        "task": (
            "Write one image prompt per scene. Return exactly one prompt per scene in the same order. "
            "Each prompt must name the time period and location inferred from the excerpt, plus concrete "
            "setting details (architecture, clothing, props) that match the story."
        ),
        "output": {"format": "json", "field": "prompts"},
        "scenes": packed,
        "constraints": [
            "No text overlays, captions, logos, or watermarks.",
            "Be specific about subject, setting, era cues, lighting, mood, camera feel.",
            "Explicitly state the era/time period and setting grounded in the excerpt.",
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
        context = (
            f"Visual intent: {s.visual_intent}\n"
            f"Scene excerpt: {s.script_excerpt}\n"
            "Include the time period and location inferred from the excerpt. "
            "Call out architecture, clothing, and props that fit the era. "
            "No text overlays, captions, logos, or watermarks. High detail."
        )
        if not p:
            p = (
                f"Style: {style_phrase}. Tone: {tone}.\n"
                f"{context}"
            )
        else:
            p = f"{p}\n\n{context}"
        s.image_prompt = p

    return scenes


def generate_visuals_from_script(
    script: str,
    num_images: int,
    tone: str,
    visual_style: str,
    aspect_ratio: str,
    variations_per_scene: int,
    scenes: Optional[List[Scene]] = None,
) -> Tuple[List[Scene], int]:
    if scenes is None:
        scenes = split_script_into_scenes(script, max_scenes=num_images)
        scenes = generate_prompts_for_scenes(scenes, tone=tone, style=visual_style)

    scenes_out: List[Scene] = []
    failed_idxs: List[int] = []

    for scene in scenes:
        variations: List[Optional[bytes]] = []
        for _ in range(max(1, variations_per_scene)):
            updated = generate_image_for_scene(
                scene,
                aspect_ratio=aspect_ratio,
                visual_style=visual_style,
            )
            variations.append(updated.image_bytes)
        scene.image_variations = variations
        scene.primary_image_index = 0
        scene.image_bytes = variations[0] if variations else None
        if any(img is None for img in variations):
            failed_idxs.append(scene.index)
        scenes_out.append(scene)

    if failed_idxs:
        for scene in scenes_out:
            if scene.index not in failed_idxs:
                continue
            updated_variations: List[Optional[bytes]] = []
            for img in scene.image_variations:
                if img:
                    updated_variations.append(img)
                    continue
                updated = generate_image_for_scene(
                    scene,
                    aspect_ratio=aspect_ratio,
                    visual_style=visual_style,
                )
                updated_variations.append(updated.image_bytes)
            scene.image_variations = updated_variations
            primary = updated_variations[scene.primary_image_index] if updated_variations else None
            scene.image_bytes = primary

    failures = sum(1 for scene in scenes_out if any(img is None for img in scene.image_variations))
    return scenes_out, failures


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
) -> Scene:
    base = (scene.image_prompt or "").strip()
    if not base:
        base = "Create a cinematic historical visual."

    context = (
        f"Visual intent: {scene.visual_intent}\n"
        f"Scene excerpt: {scene.script_excerpt}\n"
        "No text overlays, captions, logos, or watermarks."
    ).strip()

    prompt = (
        f"Style: {visual_style}.\n"
        f"{base}\n\n"
        f"{context}\n"
        f"Compose for {aspect_ratio}."
    )

    png_bytes: Optional[bytes] = None
    last_error: Optional[str] = None
    scene.image_error = ""

    for attempt in range(4):
        try:
            raw_images = generate_imagen_images(
                prompt,
                number_of_images=1,
                aspect_ratio=aspect_ratio,
            )
            raw = raw_images[0] if raw_images else None
            if not raw:
                raise RuntimeError("Imagen returned no image bytes.")

            img = Image.open(BytesIO(raw)).convert("RGB")
            img = _crop_to_aspect(img, aspect_ratio)

            out = BytesIO()
            img.save(out, format="PNG")
            png_bytes = out.getvalue()
            break
        except Exception as e:
            err_text = str(e)
            if "missing google_ai_studio_api_key" in err_text.lower():
                last_error = (
                    "Missing GOOGLE_AI_STUDIO_API_KEY. Add it to Streamlit Secrets or environment variables."
                )
            elif _is_retryable(e):
                last_error = (
                    "AI Studio rate limit reached. Retry later or reduce the number of images."
                )
            else:
                last_error = f"{type(e).__name__}: {e}"
            print(f"[Imagen image gen failed] attempt={attempt+1} {last_error}")
            if _is_retryable(e) and attempt < 3:
                _sleep_backoff(attempt)
                continue
            break

    if not png_bytes and last_error:
        print(f"[Imagen image gen final] FAILED: {last_error}")
        scene.image_error = last_error

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
