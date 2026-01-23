"""Utilities for the History Video Generator.

This module keeps model/provider logic out of the Streamlit UI.

Providers
---------
Text (scripts + planning): OpenAI via `openai` SDK (v1+).
Images: Google Gen AI SDK (`google-genai`) using Gemini native image models.

Keys are loaded from Streamlit secrets:
  - openai_api_key
  - gemini_api_key
Optional overrides:
  - openai_model
  - gemini_image_model

Notes
-----
* If a provider fails (missing key, model unavailable, network, etc.) the
  functions return reasonable placeholders so the app still "works".
* Gemini/Imagen-generated images include a SynthID watermark per Google docs.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

import streamlit as st


DEFAULT_NUM_SCENES = 6
DEFAULT_ASPECT_RATIO = "16:9"


# ----------------------------
# Data model
# ----------------------------


@dataclass
class SceneArtifact:
    idx: int
    title: str
    script_excerpt: str
    visual_intent: str
    image_prompt: str
    image: Optional[object] = None  # PIL.Image when available


# ----------------------------
# OpenAI helpers (text)
# ----------------------------


def _openai_client():
    try:
        from openai import OpenAI

        api_key = st.secrets.get("openai_api_key")
        if not api_key:
            raise RuntimeError("Missing openai_api_key")
        return OpenAI(api_key=api_key)
    except Exception as e:
        raise RuntimeError(f"OpenAI client init failed: {e}")


def _openai_model(default: str = "gpt-4.1-mini") -> str:
    return str(st.secrets.get("openai_model", default))


def generate_script(topic: str, length_label: str, tone: str, strict_accuracy: bool = True) -> str:
    """Generate a narration script (no stage directions)."""

    length_map = {
        "Short (~60 seconds)": 180,
        "Standard (8–10 minutes)": 1500,
        "Long (20–30 minutes)": 3500,
    }
    target_words = int(length_map.get(length_label, 1500))

    accuracy_clause = (
        "Be careful with specifics: do not invent quotes, dates, or names. "
        "If uncertain, use cautious phrasing. "
        if strict_accuracy
        else ""
    )

    system = (
        "You are a top-tier YouTube history documentary scriptwriter. "
        "Write for spoken narration with vivid but historically grounded storytelling. "
        "Do not include stage directions, camera cues, or on-screen text. "
        "Do not mention that you are an AI. "
        + accuracy_clause
    )

    user = (
        f"Topic: {topic}\n"
        f"Approx length: {target_words} words\n"
        f"Tone: {tone}\n\n"
        "Structure requirements:\n"
        "- Strong hook (2–4 sentences)\n"
        "- Clear context/setup\n"
        "- A coherent narrative arc (chronological where appropriate)\n"
        "- A satisfying close\n"
        "- End with a short call-to-action to subscribe\n\n"
        "Style rules:\n"
        "- Narrator-only\n"
        "- Short paragraphs for pacing\n"
        "- A few rhetorical questions\n"
        "- Avoid filler\n"
    )

    try:
        client = _openai_client()
        model = _openai_model()
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.7,
        )
        return (resp.choices[0].message.content or "").strip()
    except Exception as e:
        return _placeholder_script(topic=topic, tone=tone, target_words=target_words, err=str(e))


def refine_script(script: str, instruction: str, tone: str) -> str:
    """Refine the full script by instruction."""
    system = (
        "You are an expert editor for YouTube narration scripts. "
        "Apply the user's instruction while preserving factual integrity and the overall structure. "
        "Return only the revised script text." 
    )
    user = (
        f"Tone: {tone}\n"
        f"Instruction: {instruction.strip()}\n\n"
        "SCRIPT:\n" + script.strip()
    )
    try:
        client = _openai_client()
        model = _openai_model()
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.5,
        )
        return (resp.choices[0].message.content or "").strip() or script
    except Exception:
        return script


# ----------------------------
# Scene planning + prompt refinement
# ----------------------------


def plan_scenes(
    *,
    script: str,
    topic: str,
    tone: str,
    visual_style: str,
    aspect_ratio: str,
    num_scenes: int,
    strict_accuracy: bool,
    no_people: bool,
) -> List[SceneArtifact]:
    """Plan scenes and prompts as structured JSON."""

    num_scenes = max(3, min(int(num_scenes), 20))
    aspect_ratio = aspect_ratio or DEFAULT_ASPECT_RATIO

    accuracy_rules = (
        "- Do not invent specific named individuals unless clearly present in the script.\n"
        "- Avoid anachronisms (modern items, clothing, architecture, tech).\n"
        if strict_accuracy
        else ""
    )

    people_rule = "- Do NOT include people (no faces, no crowds).\n" if no_people else ""

    schema = {
        "scenes": [
            {
                "idx": 1,
                "title": "",
                "script_excerpt": "",
                "visual_intent": "",
                "image_prompt": "",
            }
        ]
    }

    system = (
        "You are a creative director planning visuals for a YouTube history video. "
        "Return VALID JSON only (no markdown)."
    )

    user = (
        f"Topic: {topic}\n"
        f"Tone: {tone}\n"
        f"Visual style: {visual_style}\n"
        f"Aspect ratio: {aspect_ratio}\n"
        f"Number of scenes: {num_scenes}\n\n"
        "Create a scene plan that matches the narration arc end-to-end.\n\n"
        "Rules:\n"
        "- Each scene excerpt should be 1–3 sentences pulled or paraphrased from the script\n"
        "- Visual intent should describe what the viewer sees\n"
        "- Image prompt must be detailed: subject, setting, era cues, lighting, mood, composition\n"
        "- No on-image text\n"
        f"- Strict aspect ratio: {aspect_ratio}\n"
        + accuracy_rules
        + people_rule
        + "\nReturn JSON with this shape:\n"
        + json.dumps(schema)
        + "\n\nSCRIPT:\n"
        + script.strip()
    )

    try:
        client = _openai_client()
        model = _openai_model()
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.4,
            response_format={"type": "json_object"},
        )
        payload = json.loads(resp.choices[0].message.content)
        scenes_raw = payload.get("scenes", [])
        scenes: List[SceneArtifact] = []
        for i, s in enumerate(scenes_raw[:num_scenes], start=1):
            scenes.append(
                SceneArtifact(
                    idx=int(s.get("idx", i)),
                    title=str(s.get("title", f"Scene {i}")).strip() or f"Scene {i}",
                    script_excerpt=str(s.get("script_excerpt", "")).strip(),
                    visual_intent=str(s.get("visual_intent", "")).strip(),
                    image_prompt=_normalize_prompt(str(s.get("image_prompt", "")).strip(), aspect_ratio),
                    image=None,
                )
            )
        return scenes or _fallback_scenes(script=script, num_scenes=num_scenes, tone=tone, aspect_ratio=aspect_ratio)
    except Exception:
        return _fallback_scenes(script=script, num_scenes=num_scenes, tone=tone, aspect_ratio=aspect_ratio)


def refine_scene_prompt(
    *,
    scene: SceneArtifact,
    instruction: str,
    tone: str,
    visual_style: str,
    aspect_ratio: str,
    strict_accuracy: bool,
    no_people: bool,
) -> SceneArtifact:
    """Refine a single scene's prompt and keep everything else."""

    accuracy = "Avoid anachronisms and invented specifics." if strict_accuracy else ""
    people_rule = "No people." if no_people else ""

    system = (
        "You are refining an image-generation prompt for a history documentary. "
        "Keep it consistent with the scene and follow the instruction. "
        "Return ONLY the updated prompt text." 
    )
    user = (
        f"Tone: {tone}\nStyle: {visual_style}\nAspect ratio: {aspect_ratio}\n"
        f"Scene title: {scene.title}\nScene intent: {scene.visual_intent}\n"
        f"Accuracy: {accuracy}\nPeople: {people_rule}\n\n"
        f"Instruction: {instruction.strip()}\n\n"
        "CURRENT PROMPT:\n" + scene.image_prompt.strip()
    )

    try:
        client = _openai_client()
        model = _openai_model()
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.4,
        )
        new_prompt = (resp.choices[0].message.content or "").strip()
        if not new_prompt:
            return scene
        return SceneArtifact(
            idx=scene.idx,
            title=scene.title,
            script_excerpt=scene.script_excerpt,
            visual_intent=scene.visual_intent,
            image_prompt=_normalize_prompt(new_prompt, aspect_ratio),
            image=scene.image,
        )
    except Exception:
        return scene


def _normalize_prompt(prompt: str, aspect_ratio: str) -> str:
    if not prompt:
        return ""
    if "aspect ratio" not in prompt.lower() and "ar" not in prompt.lower():
        prompt = prompt.strip() + f". Aspect ratio {aspect_ratio}."
    return re.sub(r"\s+", " ", prompt).strip()


def _fallback_scenes(script: str, num_scenes: int, tone: str, aspect_ratio: str) -> List[SceneArtifact]:
    chunks = _split_into_chunks(script, num_scenes)
    scenes: List[SceneArtifact] = []
    for i, chunk in enumerate(chunks, start=1):
        scenes.append(
            SceneArtifact(
                idx=i,
                title=f"Scene {i}",
                script_excerpt=chunk,
                visual_intent=f"A {tone.lower()} historical visual matching the narration excerpt.",
                image_prompt=_normalize_prompt(
                    f"{tone} {aspect_ratio} scene: {chunk}. Cinematic lighting, era-accurate details, no text.",
                    aspect_ratio,
                ),
                image=None,
            )
        )
    return scenes


def _split_into_chunks(text: str, n: int) -> List[str]:
    paras = [p.strip() for p in re.split(r"\n\n+", text) if p.strip()]
    if len(paras) >= n:
        return paras[:n]
    sentences = re.split(r"(?<=[.!?])\s+", " ".join(paras) if paras else text)
    sentences = [s.strip() for s in sentences if s.strip()]
    if not sentences:
        return [text.strip()]
    chunk_size = max(1, len(sentences) // n)
    out: List[str] = []
    for i in range(0, len(sentences), chunk_size):
        out.append(" ".join(sentences[i : i + chunk_size]).strip())
        if len(out) >= n:
            break
    return out


def _placeholder_script(topic: str, tone: str, target_words: int, err: str) -> str:
    return (
        f"(Placeholder) I couldn't reach OpenAI right now.\n"
        f"Topic: {topic}\nTone: {tone}\nTarget: ~{target_words} words\n\n"
        f"Error: {err}\n\n"
        "HOOK:\n"
        "History isn't just dates—it's decisions, accidents, and moments where everything pivots.\n\n"
        "BODY:\n"
        "(When your API keys are set, the full narration will appear here.)\n\n"
        "CLOSE:\n"
        "If you want more stories like this, subscribe for the next episode."
    )


# ----------------------------
# Image generation (Gemini)
# ----------------------------


def _gemini_client():
    try:
        from google import genai

        api_key = st.secrets.get("gemini_api_key")
        if not api_key:
            raise RuntimeError("Missing gemini_api_key")
        return genai.Client(api_key=api_key)
    except Exception as e:
        raise RuntimeError(f"Gemini client init failed: {e}")


def _gemini_image_model(default: str = "gemini-2.5-flash-image") -> str:
    return str(st.secrets.get("gemini_image_model", default))


from PIL import Image
from io import BytesIO

def generate_images_for_scenes(
    scenes,
    aspect_ratio="16:9",
    model_name="gemini-2.0-flash-image-preview",
):
    """
    Generate a PIL.Image for each scene using Gemini image generation.
    Accepts aspect_ratio (e.g., '16:9', '9:16') so callers can control framing.
    Returns a list of PIL.Image objects (or None if generation fails).
    """
    images = []

    for scene in scenes:
        try:
            # If your scene already has a prompt, just append AR guidance.
            prompt = getattr(scene, "prompt", str(scene))
            prompt = f"{prompt}\n\nFraming: aspect ratio {aspect_ratio}. Compose for this ratio."

            response = genai_client.models.generate_content(
                model=model_name,
                contents=[prompt],
            )

            img = None
            for part in response.candidates[0].content.parts:
                if hasattr(part, "inline_data") and part.inline_data:
                    raw_bytes = part.inline_data.data
                    img = Image.open(BytesIO(raw_bytes)).convert("RGB")
                    break

            images.append(img)

        except Exception as e:
            print(f"Image generation failed: {e}")
            images.append(None)

    return images

def _placeholder_image(text: str):
    from PIL import Image, ImageDraw

    w, h = (1024, 576)
    img = Image.new("RGB", (w, h), color=(245, 245, 245))
    d = ImageDraw.Draw(img)
    d.text((20, 20), str(text)[:280], fill=(60, 60, 60))
    return img


# ----------------------------
# Export
# ----------------------------


def compile_export_bundle(
    *,
    topic: str,
    script: str,
    scenes: List[SceneArtifact],
    meta: Dict[str, Any],
) -> Dict[str, bytes]:
    """Return a dict of file_path -> bytes to be zipped by the UI."""

    safe_topic = _safe_slug(topic or "history-video")
    bundle: Dict[str, bytes] = {}

    bundle[f"{safe_topic}/script.txt"] = script.encode("utf-8")

    scene_rows = []
    for sc in scenes:
        row = asdict(sc)
        row.pop("image", None)
        scene_rows.append(row)

    bundle[f"{safe_topic}/scenes.json"] = json.dumps(
        {"meta": meta, "scenes": scene_rows}, indent=2
    ).encode("utf-8")

    # Images
    for sc in scenes:
        if sc.image is None:
            continue
        try:
            bio = io.BytesIO()
            sc.image.save(bio, format="PNG")
            bio.seek(0)
            bundle[f"{safe_topic}/images/scene_{sc.idx:02d}.png"] = bio.read()
        except Exception:
            continue

    return bundle


def _safe_slug(s: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]+", "_", s.strip())
    return re.sub(r"_+", "_", s).strip("_")[:80] or "history-video"
