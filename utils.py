import os
import re
import json
import time
import random
from collections.abc import Mapping
from dataclasses import dataclass, asdict, field
from typing import Any, Dict, List, Optional, Tuple
from io import BytesIO
from pathlib import Path
from uuid import uuid4

import requests
from PIL import Image

from image_gen import generate_imagen_images, generate_scene_image_bytes
from src.config import get_secret as config_get_secret
from src.lib.openai_config import DEFAULT_OPENAI_MODEL, resolve_openai_config

# ----------------------------
# Secrets
# ----------------------------

def _get_secret(name: str, default: str = "") -> str:
    return str(config_get_secret(name, default) or "")


def get_secret(name: str, default: str = "") -> str:
    return _get_secret(name, default)


def get_openai_text_model(default: str = DEFAULT_OPENAI_MODEL) -> str:
    """Resolve and validate the OpenAI model ID from config.

    Checks Streamlit session state first (set via the sidebar model selector),
    then falls back to the configured secret/environment value.
    """
    try:
        import streamlit as st
        session_model = st.session_state.get("openai_model", "").strip()
        if session_model:
            return session_model
    except Exception:
        pass
    cfg = resolve_openai_config(get_secret=_get_secret)
    model = cfg.model.strip() or default
    return model


# ----------------------------
# Clients
# ----------------------------
def _openai_client():
    cfg = resolve_openai_config(get_secret=_get_secret)
    key = cfg.api_key

    os.environ.setdefault("OPENAI_API_KEY", key)
    os.environ.setdefault("openai_api_key", key)
    os.environ.setdefault("OPENAI_MODEL", cfg.model)
    os.environ.setdefault("openai_model", cfg.model)

    from openai import OpenAI  # openai>=1.x
    return OpenAI(api_key=key)


def _is_model_access_error(exc: Exception) -> bool:
    detail = str(exc).lower()
    return (
        "does not have access to model" in detail
        or "model_not_found" in detail
        or "the model requested is not available" in detail
    )


def openai_chat_completion(client, **kwargs):
    """Call OpenAI chat completions and retry once with the default model on access errors."""
    requested_model = str(kwargs.get("model") or get_openai_text_model()).strip()
    payload = {**kwargs, "model": requested_model}
    try:
        return client.chat.completions.create(**payload)
    except Exception as exc:  # noqa: BLE001 - API surface differs by SDK versions
        try:
            from openai import APIError
        except ImportError:
            raise
        if isinstance(exc, APIError) and _is_model_access_error(exc) and requested_model != DEFAULT_OPENAI_MODEL:
            retry_payload = {**payload, "model": DEFAULT_OPENAI_MODEL}
            return client.chat.completions.create(**retry_payload)
        raise


def _reraise_api_errors(exc: Exception) -> None:
    """Re-raise OpenAI API errors so the UI layer can surface them with actionable guidance.

    All HTTP-level API errors (auth, quota, model-not-found, bad-request) and connection
    errors require user action and must reach the UI so openai_error_message() can display
    helpful guidance.  Non-API failures (e.g. JSON parsing errors) are intentionally
    swallowed so callers can return a graceful placeholder.
    """
    try:
        from openai import APIConnectionError, APIError
    except ImportError:
        return
    if isinstance(exc, (APIError, APIConnectionError)):
        raise exc from exc


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
    scene_id: str = field(default_factory=lambda: uuid4().hex)
    image_prompt: str = ""
    image_bytes: Optional[bytes] = None  # PNG bytes (streamlit-safe)
    image_variations: List[Optional[bytes]] = field(default_factory=list)
    primary_image_index: int = 0
    status: str = "active"
    image_error: str = ""
    estimated_duration_sec: float = 0.0
    video_path: Optional[str] = None   # local path to an AI-generated video clip
    video_url: Optional[str] = None    # cloud/public URL of the AI-generated video clip
    video_object_path: Optional[str] = None  # object path in generated-videos bucket
    video_loop: bool = False
    video_muted: bool = True
    video_volume: float = 0.0

    # B-roll fields – free stock video assigned to this scene
    broll_query: str = ""
    broll_provider: str = ""
    broll_source_url: str = ""
    broll_page_url: str = ""
    broll_local_path: str = ""
    broll_duration_sec: float = 0.0
    broll_orientation: str = ""
    use_broll: bool = False
    scene_summary: str = ""
    scene_intent: str = ""
    source_confidence: str = "medium"
    video_prompt: str = ""
    negative_prompt: str = ""
    continuity_notes: str = ""
    prompt_spec: Dict[str, Any] = field(default_factory=dict)
    video_prompt_spec: Dict[str, Any] = field(default_factory=dict)
    prompt_scores: Dict[str, float] = field(default_factory=dict)
    # Global visual context extracted once from the full script (FIX 2)
    visual_context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["image_bytes"] = bool(self.image_bytes)
        d["image_variations"] = [bool(b) for b in self.image_variations]
        d["primary_image_index"] = self.primary_image_index
        return d


# ----------------------------
# Script generation
# ----------------------------
def generate_research_brief(topic: str, tone: str, length: str, audience: str, angle: str) -> str:
    topic = (topic or "").strip()
    if not topic:
        return "Please enter a topic."

    tone_clean = (tone or "Documentary").strip() or "Documentary"
    length_clean = (length or "8–10 minutes").strip() or "8–10 minutes"
    audience_clean = (audience or "General audience").strip() or "General audience"
    angle_clean = (angle or "Balanced overview").strip() or "Balanced overview"

    client = _openai_client()
    if client is None:
        return (
            f"# Research Brief: {topic}\n\n"
            "## Key Facts\n"
            "- [Missing openai_api_key] Add `openai_api_key` to generate AI research briefs.\n"
            "- Placeholder fact set is shown to preserve output format.\n"
            f"- Topic focus: {topic}.\n"
            f"- Tone target: {tone_clean}.\n"
            f"- Audience target: {audience_clean}.\n"
            f"- Story angle: {angle_clean}.\n"
            "- Verify names, dates, and primary-source claims before publishing.\n"
            "- Confirm modern historian consensus where interpretations differ.\n"
            "- Mark disputed casualty numbers and uncertain statistics.\n"
            "- Avoid unsourced quotes in final script.\n\n"
            "## Timeline\n"
            "- c. [date] — Early context event relevant to the topic.\n"
            "- c. [date] — Key turning point.\n"
            "- c. [date] — Consequence or expansion phase.\n"
            "- c. [date] — Major conflict or transition.\n"
            "- c. [date] — Legacy milestone.\n\n"
            "## Key People and Places\n"
            "- People: [Person 1], [Person 2], [Person 3].\n"
            "- Places: [Place 1], [Place 2], [Place 3].\n\n"
            "## Suggested Angles\n"
            "1. The hidden turning point and why it mattered.\n"
            "2. The human story behind policy and power.\n"
            "3. What modern audiences misunderstand about this topic.\n\n"
            "## Risky Claims / Uncertain Areas\n"
            "- Claims with missing primary-source citations.\n"
            "- Conflicting date ranges across references.\n"
            "- National narratives that may introduce bias.\n"
            "- Attribution of motives stated as fact without documentation."
        )

    system = (
        "You are a meticulous history research assistant for documentary scripting. "
        "Return concise, factual notes and flag uncertainty clearly."
    )
    user = (
        "Create a research brief with deterministic markdown headings and structure.\n"
        f"Topic: {topic}\n"
        f"Tone: {tone_clean}\n"
        f"Video length target: {length_clean}\n"
        f"Audience: {audience_clean}\n"
        f"Preferred angle: {angle_clean}\n\n"
        "Output format requirements (must follow exactly):\n"
        "# Research Brief: <topic>\n"
        "## Key Facts\n"
        "- 10 to 15 bullet points\n"
        "## Timeline\n"
        "- 5 to 10 dated events when applicable (use c. for approximate dates)\n"
        "## Key People and Places\n"
        "- Bulleted list of notable people and places\n"
        "## Suggested Angles\n"
        "1. Option one\n2. Option two\n3. Option three\n"
        "## Risky Claims / Uncertain Areas\n"
        "- Bulleted list of claims requiring verification\n"
        "Do not add any other headings or sections."
    )

    try:
        resp = openai_chat_completion(client, 
            model=get_openai_text_model(),
            temperature=0.2,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    except Exception as exc:
        _reraise_api_errors(exc)
        exc_detail = f"{type(exc).__name__}: {exc}"
        return (
            f"# Research Brief: {topic}\n\n"
            "## Key Facts\n"
            f"- [OpenAI request failed — {exc_detail}] Unable to generate research brief.\n"
            f"- Topic focus: {topic}.\n\n"
            "## Timeline\n"
            "- [Request failed — no timeline available]\n\n"
            "## Key People and Places\n"
            "- [Request failed — no data available]\n\n"
            "## Suggested Angles\n"
            "1. [Request failed]\n\n"
            "## Risky Claims / Uncertain Areas\n"
            "- [Request failed — verify all claims before publishing]"
        )
    return resp.choices[0].message.content.strip()



def _default_outline(topic: str) -> dict[str, Any]:
    topic_clean = (topic or "History topic").strip() or "History topic"
    return {
        "hook": f"Open with a surprising truth about {topic_clean}.",
        "context": f"Set the historical stage so viewers understand the stakes behind {topic_clean}.",
        "beats": [
            {
                "title": "Origins",
                "bullets": [
                    "Introduce the early conditions and major forces at play.",
                    "Name the first major decision or event that changes momentum.",
                ],
            },
            {
                "title": "Escalation",
                "bullets": [
                    "Show how conflict or pressure grows over time.",
                    "Connect at least one key person or place to the turning point.",
                ],
            },
            {
                "title": "Consequences",
                "bullets": [
                    "Describe immediate outcomes for institutions and everyday people.",
                    "Highlight one long-term effect that still matters now.",
                ],
            },
        ],
        "twist_or_insight": "Reveal a lesser-known interpretation or misunderstood fact.",
        "modern_relevance": "Explain how this history still shapes current politics, culture, or strategy.",
        "cta": "Close by inviting viewers to subscribe for more deep history stories.",
    }


def _normalize_outline_payload(payload: object, topic: str) -> dict[str, Any]:
    fallback = _default_outline(topic)
    if not isinstance(payload, dict):
        return fallback

    hook = str(payload.get("hook", fallback["hook"]) or fallback["hook"])
    context = str(payload.get("context", fallback["context"]) or fallback["context"])
    twist = str(payload.get("twist_or_insight", fallback["twist_or_insight"]) or fallback["twist_or_insight"])
    relevance = str(payload.get("modern_relevance", fallback["modern_relevance"]) or fallback["modern_relevance"])
    cta = str(payload.get("cta", fallback["cta"]) or fallback["cta"])

    beats_raw = payload.get("beats", [])
    beats: list[dict[str, Any]] = []
    if isinstance(beats_raw, list):
        for beat in beats_raw[:8]:
            if not isinstance(beat, dict):
                continue
            title = str(beat.get("title", "") or "").strip()
            bullets_raw = beat.get("bullets", [])
            bullets = [str(item).strip() for item in bullets_raw if str(item).strip()] if isinstance(bullets_raw, list) else []
            bullets = bullets[:4]
            if title and bullets:
                beats.append({"title": title, "bullets": bullets})

    if not beats:
        beats = fallback["beats"]

    return {
        "hook": hook,
        "context": context,
        "beats": beats,
        "twist_or_insight": twist,
        "modern_relevance": relevance,
        "cta": cta,
    }


def generate_outline(
    topic: str,
    research_brief: str,
    tone: str,
    length: str,
    audience: str,
    angle: str,
) -> dict[str, Any]:
    topic_clean = (topic or "").strip()
    if not topic_clean:
        return _default_outline("History topic")

    client = _openai_client()
    if client is None:
        return _default_outline(topic_clean)

    brief_text = (research_brief or "").strip()
    brief_block = f"\n\nResearch brief context:\n{brief_text}" if brief_text else ""

    system = (
        "You are a history documentary story editor. Build concise, coherent beat maps before scriptwriting."
    )
    user = (
        f"Topic: {topic_clean}\n"
        f"Tone: {(tone or 'Documentary').strip()}\n"
        f"Length target: {(length or '8–10 minutes').strip()}\n"
        f"Audience: {(audience or 'General audience').strip()}\n"
        f"Angle: {(angle or 'Balanced overview').strip()}\n"
        "\nReturn strict JSON with keys: hook, context, beats, twist_or_insight, modern_relevance, cta.\n"
        "- beats must be an array with 3 to 8 beat objects\n"
        "- each beat object must have: title, bullets\n"
        "- bullets must contain 2 to 4 concise strings\n"
        "No markdown. No extra keys."
        f"{brief_block}"
    )

    try:
        resp = openai_chat_completion(client, 
            model=get_openai_text_model(),
            temperature=0.4,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    except Exception as exc:
        _reraise_api_errors(exc)
        return _default_outline(topic_clean)

    raw = resp.choices[0].message.content.strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    return _normalize_outline_payload(parsed, topic_clean)


def generate_script_from_outline(outline: dict[str, Any], tone: str, reading_level: str, pacing: str, desired_scenes: int = 8) -> str:
    normalized_outline = _normalize_outline_payload(outline, str(outline.get("hook", "History topic")) if isinstance(outline, dict) else "History topic")

    client = _openai_client()
    target_scenes = max(3, min(int(desired_scenes or 8), 75))
    if client is None:
        beat_titles = ", ".join([beat.get("title", "Beat") for beat in normalized_outline.get("beats", [])])
        return (
            f"[Missing openai_api_key] Placeholder script from outline.\n\n"
            f"Hook: {normalized_outline['hook']}\n"
            f"Context: {normalized_outline['context']}\n"
            f"Beats: {beat_titles}\n"
            f"Twist: {normalized_outline['twist_or_insight']}\n"
            f"Modern relevance: {normalized_outline['modern_relevance']}\n"
            f"CTA: {normalized_outline['cta']}"
        )

    system = (
        "You are a YouTube history scriptwriter. Convert an outline into a smooth, engaging narration. "
        "Use natural transitions between beats and preserve factual caution."
    )
    user = (
        f"Tone: {(tone or 'Documentary').strip()}\n"
        f"Reading level: {(reading_level or 'General').strip()}\n"
        f"Pacing: {(pacing or 'Balanced').strip()}\n\n"
        f"Outline JSON:\n{json.dumps(normalized_outline, indent=2)}\n\n"
        "Write scene-delimited output so parsing is deterministic. "
        f"Output exactly {target_scenes} scenes total.\n"
        "Cover each beat in order with natural transitions and end with the CTA.\n"
        "Format every scene exactly as:\n"
        "SCENE 01 | <title>\n"
        "NARRATION: <text>\n"
        "VISUAL INTENT: <text>\n"
        "END SCENE 01\n"
        "---SCENE_BREAK---\n"
        "Use incrementing scene numbers and place ---SCENE_BREAK--- only between scenes."
    )

    try:
        resp = openai_chat_completion(client, 
            model=get_openai_text_model(),
            temperature=0.6,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    except Exception as exc:
        _reraise_api_errors(exc)
        exc_detail = f"{type(exc).__name__}: {exc}"
        beat_titles = ", ".join([beat.get("title", "Beat") for beat in normalized_outline.get("beats", [])])
        return (
            f"[OpenAI request failed — {exc_detail}] Placeholder script from outline.\n\n"
            f"Hook: {normalized_outline['hook']}\n"
            f"Context: {normalized_outline['context']}\n"
            f"Beats: {beat_titles}\n"
            f"Twist: {normalized_outline['twist_or_insight']}\n"
            f"Modern relevance: {normalized_outline['modern_relevance']}\n"
            f"CTA: {normalized_outline['cta']}"
        )

    return resp.choices[0].message.content.strip()

def generate_script(
    topic: str,
    length: str,
    tone: str,
    audience: str = "",
    angle: str = "",
    research_brief: str = "",
    desired_scenes: int = 8,
) -> str:
    topic = (topic or "").strip()
    if not topic:
        return "Please enter a topic."

    client = _openai_client()
    target_scenes = max(3, min(int(desired_scenes or 8), 75))
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

    brief_text = (research_brief or "").strip()
    brief_block = f"\n\nResearch brief (use this as source context):\n{brief_text}" if brief_text else ""
    audience_block = f"Audience: {(audience or 'General audience').strip()}\n"
    angle_block = f"Story angle: {(angle or 'Balanced overview').strip()}\n"

    user = (
        f"Topic: {topic}\n"
        f"Tone: {tone}\n"
        f"Target length: ~{target_words} words\n"
        f"{audience_block}"
        f"{angle_block}"
        "\nWrite scene-delimited output so parsing is deterministic.\n"
        f"Output exactly {target_scenes} scenes total.\n"
        "Use this exact structure for every scene:\n"
        "SCENE 01 | <title>\n"
        "NARRATION: <narration text>\n"
        "VISUAL INTENT: <historical visual guidance>\n"
        "END SCENE 01\n"
        "---SCENE_BREAK---\n"
        "Repeat with incrementing scene numbers and keep ---SCENE_BREAK--- between scenes only.\n"
        "Include hook, main story progression, and final CTA across the sequence.\n"
        "No markdown code fences. No bullet lists outside VISUAL INTENT prose."
        f"{brief_block}"
    )

    try:
        resp = openai_chat_completion(client, 
            model=get_openai_text_model(),
            temperature=0.7,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    except Exception as exc:
        _reraise_api_errors(exc)
        exc_detail = f"{type(exc).__name__}: {exc}"
        return (
            f"[OpenAI request failed — {exc_detail}] Unable to generate script for: {topic}\n\n"
            "Check the error detail above, then try again."
        )
    return resp.choices[0].message.content.strip()


def generate_short_script(
    topic: str,
    *,
    tone: str = "Documentary",
    reading_level: str = "General",
    direction: str = "",
) -> str:
    """Generate a narration-first script tuned for ~60-second YouTube history shorts."""
    topic = (topic or "").strip()
    if not topic:
        return "Please enter a topic."

    client = _openai_client()
    if client is None:
        return (
            f"[Missing openai_api_key] Placeholder short-form script for: {topic}\n\n"
            "Add `openai_api_key` in Streamlit Secrets to enable real short script generation."
        )

    direction_block = f"Direction/angle: {direction.strip()}\n" if (direction or "").strip() else ""
    system = (
        "You are a YouTube history short-form narration writer. "
        "Write compelling, historically grounded scripts that sound natural when spoken aloud."
    )
    user = (
        "Write a 60-second YouTube history narration script.\n"
        f"Topic: {topic}\n"
        f"Tone: {(tone or 'Documentary').strip()}\n"
        f"Reading level: {(reading_level or 'General').strip()}\n"
        f"{direction_block}"
        "Requirements:\n"
        "- About 130 to 170 spoken words.\n"
        "- Strong hook in the first 1-2 lines.\n"
        "- Clear middle progression.\n"
        "- Strong memorable closing line.\n"
        "- Concise, vivid, engaging language with short-form retention pacing.\n"
        "- No markdown, no bullets, no scene labels, no production notes, no visual instructions.\n"
        "Output only the final narration script text."
    )

    try:
        resp = openai_chat_completion(
            client,
            model=get_openai_text_model(),
            temperature=0.7,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    except Exception as exc:
        _reraise_api_errors(exc)
        exc_detail = f"{type(exc).__name__}: {exc}"
        return (
            f"[OpenAI request failed — {exc_detail}] Unable to generate short script for: {topic}\n\n"
            "Check the error detail above, then try again."
        )

    return resp.choices[0].message.content.strip()


def edit_script_with_direction(script: str, direction: str) -> str:
    """Revise an existing script according to a plain-English direction.

    Examples of *direction*: "make it shorter", "add more humor",
    "make the tone more dramatic", "simplify for a younger audience".
    """
    script = (script or "").strip()
    direction = (direction or "").strip()
    if not script:
        return script
    if not direction:
        return script

    client = _openai_client()
    if client is None:
        return script

    system = (
        "You are an expert YouTube history scriptwriter and editor. "
        "You will receive a script and a direction for how to revise it. "
        "Apply the direction faithfully while preserving the topic, key facts, and overall structure. "
        "Return only the revised script text — no commentary, no markdown fences."
    )
    user = (
        f"Direction: {direction}\n\n"
        f"Script to revise:\n{script}"
    )

    try:
        resp = openai_chat_completion(client, 
            model=get_openai_text_model(),
            temperature=0.7,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    except Exception as exc:
        _reraise_api_errors(exc)
        return script

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
    try:
        resp = openai_chat_completion(client, 
            model=get_openai_text_model(),
            temperature=1.0,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    except Exception as exc:
        _reraise_api_errors(exc)
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

    try:
        resp = openai_chat_completion(client, 
            model=get_openai_text_model(),
            temperature=0.6,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    except Exception as exc:
        _reraise_api_errors(exc)
        exc_detail = f"{type(exc).__name__}: {exc}"
        return (
            f"[OpenAI request failed — {exc_detail}] Unable to rewrite description."
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
    try:
        resp = openai_chat_completion(client, 
            model=get_openai_text_model(),
            temperature=0.7,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    except Exception as exc:
        _reraise_api_errors(exc)
        base = topic or "Untitled History Story"
        return [
            f"{base}: The Forgotten Turning Point",
            f"The Hidden Truth Behind {base}",
            f"{base} in 10 Minutes",
            f"{base}: What Really Happened",
            f"The Untold Story of {base}",
        ][:count]
    raw = resp.choices[0].message.content.strip()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()][:count]
    except json.JSONDecodeError:
        pass
    return [line.strip("- ").strip() for line in raw.splitlines() if line.strip()][:count]


def generate_video_description(
    topic: str,
    title: str,
    script: str,
    direction: str,
    hashtag_count: int = 8,
) -> str:
    topic = (topic or "").strip()
    title = (title or "").strip()
    script = (script or "").strip()
    direction = (direction or "").strip()
    hashtag_count = max(3, min(int(hashtag_count), 15))

    client = _openai_client()
    if client is None:
        base = title or topic or "This history story"
        return (
            f"{base} changed the course of history in ways most people never hear about. "
            "In this episode, we break down the turning points, key figures, and the real stakes behind the event.\n\n"
            f"#{(topic or 'history').replace(' ', '')} #History #Documentary #Storytelling #WorldHistory"
        )

    system = (
        "You are a YouTube metadata writer for history channels. "
        "Write clear descriptions optimized for watch-time and discovery while staying factual."
    )
    user = (
        f"Topic: {topic or 'History documentary'}\n"
        f"Video title: {title or 'Untitled'}\n"
        f"Creator direction: {direction or 'No extra direction provided.'}\n"
        f"Script excerpt:\n{script[:2500]}\n\n"
        "Write a YouTube description with: \n"
        "1) A strong 2-3 sentence hook paragraph\n"
        "2) A short context paragraph\n"
        "3) A one-line call to action\n"
        f"4) Exactly {hashtag_count} relevant hashtags at the end\n"
        "Return plain text only."
    )
    try:
        resp = openai_chat_completion(client, 
            model=get_openai_text_model(),
            temperature=0.7,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    except Exception as exc:
        _reraise_api_errors(exc)
        base = title or topic or "This history story"
        return (
            f"{base} changed the course of history in ways most people never hear about. "
            "In this episode, we break down the turning points, key figures, and the real stakes behind the event.\n\n"
            f"#{(topic or 'history').replace(' ', '')} #History #Documentary #Storytelling #WorldHistory"
        )
    return resp.choices[0].message.content.strip()


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
    try:
        resp = openai_chat_completion(client, 
            model=get_openai_text_model(),
            temperature=0.7,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    except Exception as exc:
        _reraise_api_errors(exc)
        base = title or topic or "Epic historical moment"
        return (
            f"{base}, {style} lighting, dramatic composition, high contrast, sharp focus, "
            "no text, no watermark, YouTube thumbnail style, 16:9."
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
        return None, "No image returned for this prompt (possibly safety-filtered)."
    return images[0], ""


# ----------------------------
# Deterministic fallback chunking (ENFORCES N scenes)
# ----------------------------


def _normalize_script_text(script: str) -> str:
    cleaned = (script or "").replace("\r\n", "\n").replace("\r", "\n")
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    cleaned = cleaned.strip()
    cleaned = re.split(r"(?im)^\s*##\s*notes\s+to\s+verify\b", cleaned, maxsplit=1)[0].strip()
    return cleaned




def _extract_numbered_scene_lines(script: str) -> list[str]:
    """Extract scene-like lines formatted as `01: ...`, `01 - ...`, or `1) ...`."""
    if not script:
        return []

    lines = [line.strip() for line in script.replace("\r\n", "\n").split("\n") if line.strip()]
    extracted: list[str] = []
    for line in lines:
        match = re.match(r"^\s*(?:scene\s*)?(\d{1,3})\s*(?:[:.)\-–—]|\s+-\s+)\s*(.+)$", line, flags=re.IGNORECASE)
        if not match:
            continue
        content = re.sub(r"\s+", " ", match.group(2)).strip(" -–—:	")
        if content:
            extracted.append(content)

    # Keep only meaningful candidates and de-duplicate exact adjacent repeats.
    cleaned: list[str] = []
    for candidate in extracted:
        if len(candidate.split()) < 3:
            continue
        if cleaned and cleaned[-1].casefold() == candidate.casefold():
            continue
        cleaned.append(candidate)
    return cleaned

def _split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text.strip()) if s.strip()]


def _count_words(text: str) -> int:
    return len(re.findall(r"\b\w+\b", text or ""))


def _scene_title_from_text(scene_text: str, index: int) -> str:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'\-]*", scene_text or "")
    take = words[:6] if len(words) >= 6 else words[:3]
    snippet = " ".join(take).strip() or "Scene"
    return f"{index:02d} — {snippet}"


def _make_atomic_beats(script: str, paragraph_word_threshold: int = 120) -> list[str]:
    normalized = _normalize_script_text(script)
    if not normalized:
        return []

    paragraphs = [p.strip() for p in re.split(r"\n\n+", normalized) if p.strip()]
    beats: list[str] = []
    for paragraph in paragraphs:
        if len(paragraph.split()) <= paragraph_word_threshold:
            beats.append(paragraph)
            continue

        sentences = _split_sentences(paragraph)
        if not sentences:
            beats.append(paragraph)
            continue

        i = 0
        while i < len(sentences):
            chunk = [sentences[i]]
            i += 1
            while i < len(sentences) and len(chunk) < 3:
                if len(" ".join(chunk).split()) >= 90:
                    break
                chunk.append(sentences[i])
                i += 1
            beats.append(" ".join(chunk).strip())

    return [b for b in beats if b.strip()]


def _pack_beats_into_scene_strings(beats: list[str], target_scenes: int) -> list[str]:
    if target_scenes <= 0:
        return []
    if not beats:
        return ["Scene content unavailable"] * target_scenes

    words_per_beat = [_count_words(b) for b in beats]
    total_words = sum(words_per_beat)
    target_words = max(1.0, total_words / float(target_scenes))
    min_words = max(40, int(target_words * 0.55))
    max_words = max(min_words + 1, int(target_words * 1.65))

    scenes: list[list[str]] = []
    current: list[str] = []
    current_words = 0

    for beat, beat_words in zip(beats, words_per_beat):
        should_break = (
            current
            and current_words >= min_words
            and (current_words + beat_words) > max_words
            and len(scenes) < target_scenes - 1
        )
        if should_break:
            scenes.append(current)
            current = []
            current_words = 0

        current.append(beat)
        current_words += beat_words

    if current:
        scenes.append(current)

    return ["\n\n".join(group).strip() for group in scenes]


def _split_scene_text_midpoint(scene_text: str) -> tuple[str, str]:
    parts = [p.strip() for p in re.split(r"\n\n+", scene_text) if p.strip()]
    if len(parts) >= 2:
        total = sum(len(p.split()) for p in parts)
        running = 0
        best_idx = 1
        best_delta = float("inf")
        for idx in range(1, len(parts)):
            running += len(parts[idx - 1].split())
            delta = abs((total / 2.0) - running)
            if delta < best_delta:
                best_delta = delta
                best_idx = idx
        left = "\n\n".join(parts[:best_idx]).strip()
        right = "\n\n".join(parts[best_idx:]).strip()
        if left and right:
            return left, right

    sentences = _split_sentences(scene_text)
    if len(sentences) >= 2:
        total = sum(len(s.split()) for s in sentences)
        running = 0
        best_idx = 1
        best_delta = float("inf")
        for idx in range(1, len(sentences)):
            running += len(sentences[idx - 1].split())
            delta = abs((total / 2.0) - running)
            if delta < best_delta:
                best_delta = delta
                best_idx = idx
        left = " ".join(sentences[:best_idx]).strip()
        right = " ".join(sentences[best_idx:]).strip()
        if left and right:
            return left, right

    words = scene_text.split()
    if len(words) <= 1:
        return scene_text.strip(), ""
    mid = len(words) // 2
    left = " ".join(words[:mid]).strip()
    right = " ".join(words[mid:]).strip()
    return left, right


def split_script_into_scene_strings(
    script: str,
    target_scenes: int,
    return_debug: bool = False,
) -> list[str] | tuple[list[str], dict[str, Any]]:
    target_scenes = max(1, int(target_scenes or 1))
    normalized = _normalize_script_text(script)
    if not normalized:
        out = ["Scene content unavailable"] * target_scenes
        debug = {"word_counts": [_count_words(v) for v in out], "merges": 0, "splits": 0}
        return (out, debug) if return_debug else out

    beats = _make_atomic_beats(normalized)
    scenes = _pack_beats_into_scene_strings(beats, target_scenes)

    merges = 0
    splits = 0

    while len(scenes) > target_scenes:
        smallest_idx = min(range(len(scenes)), key=lambda i: _count_words(scenes[i]))
        if smallest_idx == 0:
            neighbor_idx = 1
        elif smallest_idx == len(scenes) - 1:
            neighbor_idx = len(scenes) - 2
        else:
            left_words = _count_words(scenes[smallest_idx - 1])
            right_words = _count_words(scenes[smallest_idx + 1])
            neighbor_idx = smallest_idx - 1 if left_words <= right_words else smallest_idx + 1

        left_i, right_i = sorted([smallest_idx, neighbor_idx])
        scenes[left_i] = "\n\n".join([scenes[left_i], scenes[right_i]]).strip()
        del scenes[right_i]
        merges += 1

    while len(scenes) < target_scenes:
        largest_idx = max(range(len(scenes)), key=lambda i: _count_words(scenes[i]))
        left, right = _split_scene_text_midpoint(scenes[largest_idx])
        if not right.strip():
            scenes.insert(largest_idx + 1, "")
        else:
            scenes[largest_idx] = left
            scenes.insert(largest_idx + 1, right)
        splits += 1

    for idx, scene in enumerate(scenes):
        if scene.strip():
            continue
        donor_idx = idx - 1 if idx > 0 else (idx + 1 if idx + 1 < len(scenes) else None)
        moved = False
        if donor_idx is not None and scenes[donor_idx].strip():
            donor_sentences = _split_sentences(scenes[donor_idx])
            if len(donor_sentences) >= 2:
                scenes[idx] = donor_sentences[-1].strip()
                scenes[donor_idx] = " ".join(donor_sentences[:-1]).strip()
                moved = True
        if not moved:
            scenes[idx] = normalized[:120].strip() or "Scene content unavailable"

    word_counts = [_count_words(scene) for scene in scenes]
    debug = {"word_counts": word_counts, "merges": merges, "splits": splits}
    return (scenes, debug) if return_debug else scenes


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
                title=_scene_title_from_text(txt2, i),
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
# Scene splitting (beat-aware + deterministic duration estimates)
# ----------------------------
_STOPWORDS = {
    "the", "and", "that", "with", "from", "this", "into", "about", "after", "before", "their", "there",
    "were", "have", "has", "had", "been", "being", "they", "them", "than", "then", "when", "where",
    "while", "which", "whose", "what", "your", "you", "our", "for", "are", "was", "will", "would",
    "could", "should", "over", "under", "between", "through", "across", "during", "because", "very",
}


def _estimate_duration_sec(text: str, wpm: int) -> float:
    words = len((text or "").split())
    rate = max(90, min(int(wpm or 160), 240))
    seconds = (words / rate) * 60.0 if words else 0.0
    return round(max(2.0, seconds), 1)


def _extract_visual_keywords(text: str, min_items: int = 5, max_items: int = 10) -> str:
    tokens = re.findall(r"[A-Za-z][A-Za-z\-']+", (text or "").lower())
    ranked: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if len(token) < 4 or token in _STOPWORDS:
            continue
        if token in seen:
            continue
        seen.add(token)
        ranked.append(token)
        if len(ranked) >= max_items:
            break
    if len(ranked) < min_items:
        defaults = ["era", "location", "architecture", "wardrobe", "atmosphere", "props", "lighting"]
        for item in defaults:
            if item not in seen:
                ranked.append(item)
            if len(ranked) >= min_items:
                break
    return ", ".join(ranked[:max_items])


def _split_by_headings_paragraphs(script: str, target_n: int) -> list[str]:
    chunks = [c.strip() for c in re.split(r"\n\s*\n+", script) if c.strip()]
    if not chunks:
        chunks = [script.strip()]

    grouped: list[str] = []
    for chunk in chunks:
        if len(chunk.split()) > 180:
            sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", chunk) if s.strip()]
            grouped.extend(sentences if sentences else [chunk])
        else:
            grouped.append(chunk)

    if len(grouped) >= target_n:
        return [" ".join(group).strip() for group in _split_into_groups(grouped, target_n)]

    words = script.split()
    if not words:
        return []
    return [" ".join(group).strip() for group in _split_into_groups(words, target_n)]


def _outline_beats(outline: object) -> list[dict[str, Any]]:
    if not isinstance(outline, dict):
        return []
    beats = outline.get("beats", [])
    if not isinstance(beats, list):
        return []
    clean: list[dict[str, Any]] = []
    for beat in beats:
        if not isinstance(beat, dict):
            continue
        title = str(beat.get("title", "") or "").strip()
        bullets_raw = beat.get("bullets", [])
        bullets = [str(b).strip() for b in bullets_raw if str(b).strip()] if isinstance(bullets_raw, list) else []
        if title:
            clean.append({"title": title, "bullets": bullets[:4]})
    return clean


def _scene_chunks_from_script(script: str) -> list[str]:
    text = (script or "").strip()
    if not text:
        return []

    # 1) Explicit delimiter
    if "---SCENE_BREAK---" in text:
        return [c.strip() for c in text.split("---SCENE_BREAK---") if c.strip()]

    # 2) SCENE heading boundaries (keep heading with each chunk)
    scene_heading = re.compile(r"(?im)^\s*SCENE\s+\d+\b.*$")
    if scene_heading.search(text):
        parts = re.split(r"(?im)^(?=\s*SCENE\s+\d+\b)", text)
        return [p.strip() for p in parts if p.strip()]

    # 3) Paragraph boundaries
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n+", text) if p.strip()]
    if len(paragraphs) > 1:
        return paragraphs

    # 4) Sentence-window fallback (3-sentence windows)
    sentences = [seg.strip() for seg in re.split(r"(?<=[.!?])\s+", text) if seg.strip()]
    if not sentences:
        return [text]

    chunks: list[str] = []
    window: list[str] = []
    for sentence in sentences:
        window.append(sentence)
        if len(window) >= 3:
            chunks.append(" ".join(window).strip())
            window = []
    if window:
        chunks.append(" ".join(window).strip())
    return chunks




def _rebalance_chunks_to_target(chunks: list[str], target: int) -> list[str]:
    cleaned = [c.strip() for c in chunks if str(c or "").strip()]
    if not cleaned:
        return []

    while len(cleaned) > target:
        smallest_idx = min(range(len(cleaned)), key=lambda i: _count_words(cleaned[i]))
        if smallest_idx == 0:
            neighbor_idx = 1
        elif smallest_idx == len(cleaned) - 1:
            neighbor_idx = len(cleaned) - 2
        else:
            left_words = _count_words(cleaned[smallest_idx - 1])
            right_words = _count_words(cleaned[smallest_idx + 1])
            neighbor_idx = smallest_idx - 1 if left_words <= right_words else smallest_idx + 1
        left_i, right_i = sorted([smallest_idx, neighbor_idx])
        cleaned[left_i] = "\n\n".join([cleaned[left_i], cleaned[right_i]]).strip()
        del cleaned[right_i]

    while len(cleaned) < target:
        largest_idx = max(range(len(cleaned)), key=lambda i: _count_words(cleaned[i]))
        left, right = _split_scene_text_midpoint(cleaned[largest_idx])
        if not right.strip() or left.strip() == cleaned[largest_idx].strip():
            break
        cleaned[largest_idx] = left.strip()
        cleaned.insert(largest_idx + 1, right.strip())

    return [c for c in cleaned if c.strip()]


def split_script_into_scenes(script: str, max_scenes: int = 8, outline: dict[str, Any] | None = None, wpm: int = 160) -> List[Scene]:
    text = (script or "").strip()
    if not text:
        return []

    target = max(1, min(int(max_scenes or 8), 75))
    chunks = _scene_chunks_from_script(text)

    # De-dupe repeated chunks to avoid duplicate scene cards.
    deduped: list[str] = []
    seen: set[str] = set()
    for chunk in chunks:
        normalized = re.sub(r"\s+", " ", chunk).strip().lower()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(chunk.strip())

    if not deduped:
        deduped = [text]

    chunk_pool = _rebalance_chunks_to_target(deduped, target)
    if not chunk_pool:
        chunk_pool = [text]

    # Fallback to robust exact-count splitter if rebalancing misses the target.
    if len(chunk_pool) != target:
        chunk_pool = split_script_into_scene_strings(text, target)

    # Hard guarantee: always honor the requested scene count.
    # Some content patterns can still drift after heuristics/rebalancing (e.g.,
    # heavily duplicated chunks, very short scripts, or malformed delimiters).
    if len(chunk_pool) < target:
        filler = split_script_into_scene_strings(text, target)
        chunk_pool.extend(filler[len(chunk_pool):])
    if len(chunk_pool) > target:
        chunk_pool = chunk_pool[:target]

    beats = _outline_beats(outline)
    scenes: list[Scene] = []
    for i, chunk in enumerate(chunk_pool[:target], start=1):
        beat = beats[i - 1] if i - 1 < len(beats) else {}
        beat_text = " ".join(beat.get("bullets", [])) if isinstance(beat, dict) else ""
        keyword_source = f"{beat.get('title', '') if isinstance(beat, dict) else ''} {beat_text} {chunk}"
        title = str(beat.get("title", "") or "").strip() if isinstance(beat, dict) else ""
        scenes.append(
            Scene(
                index=i,
                title=title or _scene_title_from_text(chunk, i),
                script_excerpt=chunk,
                visual_intent=_extract_visual_keywords(keyword_source),
                estimated_duration_sec=_estimate_duration_sec(chunk, wpm),
            )
        )

    return scenes

# ----------------------------
# Prompt generation (ENFORCE one prompt per scene)
# ----------------------------
_DEFAULT_NEGATIVE_CUES = [
    "no modern clothing",
    "no modern weapons",
    "no text overlays",
    "no duplicated limbs",
    "no floating objects",
    "no futuristic architecture unless explicitly requested",
    "no random smiling at camera unless appropriate",
    "no fantasy elements unless script calls for speculation",
]
_FORBIDDEN_GENERIC_PHRASES = (
    "epic scene",
    "dramatic history",
    "cinematic historical moment",
)


def _scene_anchor_keywords(text: str, limit: int = 8) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z\-']{2,}", text or "")
    stop_words = {
        "the", "and", "for", "with", "that", "from", "this", "were", "their", "they", "into", "while", "over",
        "have", "has", "had", "about", "during", "after", "before", "when", "where", "what", "which", "would",
        "could", "should", "through", "across", "between", "there", "these", "those", "his", "her", "its", "our",
        "your", "than", "then", "them", "been", "being", "also", "still", "very", "more", "most", "many",
    }
    ranked: list[str] = []
    seen: set[str] = set()
    for word in words:
        token = word.lower()
        if token in stop_words or token in seen:
            continue
        seen.add(token)
        ranked.append(word)
        if len(ranked) >= limit:
            break
    return ranked


def _classify_scene_intent(excerpt: str) -> str:
    text = (excerpt or "").lower()
    if any(k in text for k in ("close-up", "artifact", "inscription", "coin", "map", "detail")):
        return "object/detail shot"
    if any(k in text for k in ("revealed", "discovered", "uncovered", "opened", "found")):
        return "discovery/reveal"
    if any(k in text for k in ("battle", "march", "attack", "charging", "fleeing", "burning")):
        return "action moment"
    if any(k in text for k in ("aftermath", "ruins", "debris", "smoke", "silent")):
        return "aftermath"
    if any(k in text for k in ("city", "valley", "harbor", "palace", "temple", "fortress", "landscape")):
        return "location establishing shot"
    if any(k in text for k in ("he", "she", "leader", "official", "queen", "king", "commander")):
        return "character portrait"
    return "mystery/speculation reconstruction"


def _infer_time_period(excerpt: str, title: str) -> tuple[str, str]:
    text = f"{title} {excerpt}"
    year_match = re.search(r"\b(1[0-9]{3}|20[0-9]{2}|[5-9][0-9]{2})\b", text)
    if year_match:
        year = year_match.group(1)
        return f"around {year} CE", "high"
    lower = text.lower()
    if "roman" in lower:
        return "Roman era (approximately 1st century BCE to 4th century CE)", "medium"
    if "medieval" in lower:
        return "Medieval period (approximately 5th to 15th century)", "medium"
    if "victorian" in lower:
        return "Victorian period (19th century)", "medium"
    if "bronze age" in lower:
        return "Bronze Age (approximately 3300 to 1200 BCE)", "medium"
    return "historical period unspecified; use plausible reconstruction", "low"


def _infer_location(excerpt: str) -> str:
    phrases = re.findall(r"\b(?:in|at|near|inside)\s+([A-Z][A-Za-z\-]*(?:\s+[A-Z][A-Za-z\-]*){0,3})", excerpt or "")
    if phrases:
        return phrases[0]
    return "location inferred from the narration context"


def _clean_generic_phrases(text: str) -> str:
    out = text
    for phrase in _FORBIDDEN_GENERIC_PHRASES:
        out = re.sub(re.escape(phrase), "", out, flags=re.IGNORECASE)
    return re.sub(r"\s{2,}", " ", out).strip(" ,.")


def _prompt_scores(spec: dict[str, Any], image_prompt: str, video_prompt: str) -> dict[str, float]:
    excerpt = str(spec.get("script_excerpt", "")).lower()
    keywords = [k.lower() for k in spec.get("anchor_keywords", []) if str(k).strip()]
    image_lower = image_prompt.lower()
    video_lower = video_prompt.lower()
    keyword_hits = sum(1 for k in keywords if k and (k in image_lower or k in video_lower))
    script_alignment = min(5.0, 1.0 + keyword_hits * 0.7)
    historical_signals = sum(
        1 for v in (
            spec.get("time_period", ""),
            spec.get("setting/location", ""),
            spec.get("wardrobe_or_architecture_details", ""),
            spec.get("historical_context", ""),
        )
        if str(v).strip()
    )
    historical_specificity = min(5.0, historical_signals * 1.2)
    visual_clarity = 5.0 if all(str(spec.get(k, "")).strip() for k in ("primary_subject", "visible_action", "camera_framing")) else 3.0
    action_clarity = min(5.0, 1.0 + sum(1 for k in ("opening frame description", "subject motion", "camera motion", "ending frame description") if str(spec.get("video_spec", {}).get(k, "")).strip()))
    if excerpt and not any(tok in image_lower for tok in keywords[:2]):
        script_alignment = max(0.0, script_alignment - 1.0)
    return {
        "script_alignment": round(script_alignment, 2),
        "historical_specificity": round(historical_specificity, 2),
        "visual_clarity": round(visual_clarity, 2),
        "action_clarity_for_video": round(action_clarity, 2),
    }


def _build_scene_prompt_spec(scene: Scene, continuity_ctx: dict[str, str]) -> dict[str, Any]:
    excerpt = str(scene.script_excerpt or "").strip()
    anchor_keywords = _scene_anchor_keywords(excerpt)
    intent = _classify_scene_intent(excerpt)
    time_period, confidence = _infer_time_period(excerpt, scene.title)
    location = _infer_location(excerpt) or continuity_ctx.get("location_family", "")
    lower_excerpt = excerpt.lower()
    role_candidates = [
        "engineer", "official", "soldier", "queen", "king", "scribe", "merchant", "artisan", "commander", "priest",
        "worker", "guard", "scholar", "leader", "ruler",
    ]
    primary_subject = next((r for r in role_candidates if r in lower_excerpt), "")
    if not primary_subject:
        primary_subject = anchor_keywords[0] if anchor_keywords else (scene.title or "historical subject")
    secondary_subjects = anchor_keywords[1:4]
    visible_action = _clean_generic_phrases(
        f"{primary_subject} {('and ' + ', '.join(secondary_subjects)) if secondary_subjects else ''} during {anchor_keywords[0] if anchor_keywords else 'the narrated event'}."
    )
    one_sentence_summary = _clean_generic_phrases(
        f"{scene.title or 'Scene'} shows {visible_action} in {location} during {time_period}."
    )
    historical_context = _clean_generic_phrases(
        f"Ground visuals in {time_period}; avoid modern props and use plausible regional materials tied to {location}."
    )
    wardrobe = continuity_ctx.get("costume_family", "") or "period-appropriate clothing, tools, and architecture matching the era"
    composition = "clear subject hierarchy, leading lines toward the primary subject, foreground-midground-background depth"
    if intent == "location establishing shot":
        composition = "wide depth, horizon-led composition, architecture scale emphasized, small human figures for scale"
    elif intent == "object/detail shot":
        composition = "tight framing, texture emphasis, shallow depth of field around key object"

    cinematic_moment = _clean_generic_phrases(
        f"{primary_subject} {('interacting with ' + secondary_subjects[0]) if secondary_subjects else 'at a decisive story beat'} in {location}"
    )
    spec = {
        "scene_id": scene.scene_id or f"scene-{scene.index}",
        "script_excerpt": excerpt,
        "one_sentence_scene_summary": one_sentence_summary,
        "primary_subject": primary_subject,
        "secondary_subjects": secondary_subjects,
        "setting/location": location,
        "time_period": time_period,
        "historical_context": historical_context,
        "visible_action": visible_action,
        "emotional_tone": "somber and investigative" if "aftermath" in intent else "focused documentary tension",
        "camera_framing": "medium shot, eye-level, 35mm lens equivalent",
        "composition_notes": composition,
        "lighting": continuity_ctx.get("lighting_direction", "directional natural light with era-appropriate practical sources"),
        "important_objects": anchor_keywords[2:7],
        "wardrobe_or_architecture_details": wardrobe,
        "exclusions / negative prompt cues": list(_DEFAULT_NEGATIVE_CUES),
        "continuity_notes": (
            f"location family: {continuity_ctx.get('location_family', location)}; "
            f"costume family: {wardrobe}; "
            f"time-of-day logic: {continuity_ctx.get('time_logic', 'maintain adjacent-scene consistency')}"
        ),
        "source_confidence": confidence,
        "scene_intent": intent,
        "moment_selection": cinematic_moment,
        "anchor_keywords": anchor_keywords,
    }
    return spec


def _build_image_prompt(spec: dict[str, Any], style_phrase: str, tone: str) -> str:
    _style = spec.get("visual_style", "") or style_phrase
    _palette = spec.get("color_palette", "")
    _palette_clause = f"Color palette: {_palette}. " if _palette else ""
    return _clean_generic_phrases(
        f"{_style}; {tone}. "
        f"Frozen moment: {spec.get('moment_selection', '')}. "
        f"Primary subject: {spec.get('primary_subject', '')}. "
        f"Secondary subjects: {', '.join(spec.get('secondary_subjects', [])) or 'none'}. "
        f"Setting: {spec.get('setting/location', '')}, {spec.get('time_period', '')}. "
        f"Visible action: {spec.get('visible_action', '')}. "
        f"Camera framing: {spec.get('camera_framing', '')}. "
        f"Composition: {spec.get('composition_notes', '')}. "
        f"Lighting: {spec.get('lighting', '')}. "
        f"{_palette_clause}"
        f"Historical grounding: {spec.get('historical_context', '')}; {spec.get('wardrobe_or_architecture_details', '')}. "
        f"Important objects: {', '.join(spec.get('important_objects', [])) or 'none'}. "
        f"Script anchor keywords: {', '.join(spec.get('anchor_keywords', []))}. "
        "Photoreal detail, subject priority, no text overlays."
    )


def _build_video_prompt(spec: dict[str, Any], style_phrase: str, tone: str) -> tuple[str, dict[str, Any]]:
    _style = spec.get("visual_style", "") or style_phrase
    _palette = spec.get("color_palette", "")
    _palette_clause = f"Color palette: {_palette}. " if _palette else ""
    opening = _clean_generic_phrases(
        f"Opening frame: {spec.get('primary_subject', '')} in {spec.get('setting/location', '')}, {spec.get('time_period', '')}, {spec.get('camera_framing', '')}"
    )
    subject_motion = _clean_generic_phrases(
        f"Subject motion: {spec.get('primary_subject', '')} performs {spec.get('visible_action', '')} with stable body proportions and identity."
    )
    # Per-clip camera motion is injected at generation time (ai_video_clips.py);
    # this default covers scenes rendered as still-image video.
    camera_motion = spec.get("camera_motion_override") or "Camera motion: slow dolly-in with subtle lateral drift, no abrupt cuts."
    environment_motion = "Environment motion: smoke, dust, cloth, firelight, and ambient particles move naturally with consistent wind direction."
    ending = _clean_generic_phrases(
        f"Ending frame: same subject, same location, same lighting direction, action resolves into {spec.get('emotional_tone', 'documentary tension')}."
    )
    continuity_lock = {
        "same clothing": True,
        "same location": True,
        "same lighting direction": True,
        "no jump cuts": True,
        "no new subjects appearing unexpectedly": True,
    }
    video_spec = {
        "opening frame description": opening,
        "subject motion": subject_motion,
        "camera motion": camera_motion,
        "environment motion": environment_motion,
        "ending frame description": ending,
        "continuity lock": continuity_lock,
    }
    prompt = (
        f"{_style}; {tone}. 5-second continuous historical shot. "
        f"{_palette_clause}"
        f"{opening}. {subject_motion}. {camera_motion}. {environment_motion}. {ending}. "
        f"Keep subject stable and temporally continuous from first second to last second. "
        f"Script anchor keywords: {', '.join(spec.get('anchor_keywords', []))}."
    )
    return _clean_generic_phrases(prompt), video_spec


def extract_visual_context(full_script: str) -> dict:
    """Extract persistent visual context from the full script via a single LLM call.

    Returns a dict with keys:
      time_period, location, clothing_style, visual_atmosphere  (original)
      character_name, character_appearance, visual_style, color_palette  (new)
    Falls back to empty strings for any key that is missing or on any failure.
    """
    _fallback = {
        "time_period": "", "location": "", "clothing_style": "", "visual_atmosphere": "",
        "character_name": "", "character_appearance": "", "visual_style": "", "color_palette": "",
    }
    client = _openai_client()
    if not client or not (full_script or "").strip():
        return _fallback.copy()

    prompt_text = (
        "You are a cinematic art director for historical documentary shorts. "
        "Read this script and extract the visual DNA that must stay consistent across ALL scenes.\n\n"
        f"Script:\n{full_script.strip()}\n\n"
        "Return ONLY a JSON object with these exact keys:\n"
        "{\n"
        '  "time_period": "specific era and century, e.g. Ancient Rome, 1st century AD",\n'
        '  "location": "primary geographic setting, e.g. the Roman Forum, ancient Rome",\n'
        '  "clothing_style": "era-accurate clothing description, e.g. Roman togas and military tunics in red and white",\n'
        '  "visual_atmosphere": "lighting and mood that defines the whole piece, e.g. golden hour Mediterranean sunlight, dusty and dramatic",\n'
        '  "character_name": "main subject, e.g. Julius Caesar, Joan of Arc",\n'
        '  "character_appearance": "specific physical description — hair, build, distinguishing features",\n'
        '  "visual_style": "cinematic rendering style, e.g. cinematic oil painting style, dramatic chiaroscuro",\n'
        '  "color_palette": "dominant colors, e.g. warm golds, deep crimson, stone gray"\n'
        "}\n"
        "Return only JSON, no other text. Be specific — these values are injected verbatim into image and video prompts."
    )
    try:
        import json as _json
        resp = openai_chat_completion(
            client,
            messages=[{"role": "user", "content": prompt_text}],
            temperature=0.2,
            max_tokens=400,
        )
        raw = (resp.choices[0].message.content or "").strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        result = _json.loads(raw.strip())
        # Ensure all expected keys exist, filling blanks for any new keys the
        # model omitted and preserving backwards compatibility for callers that
        # only read the original four keys.
        for _k, _v in _fallback.items():
            result.setdefault(_k, _v)
        return result
    except Exception:
        return _fallback.copy()


def generate_prompts_for_scenes(
    scenes: List[Scene],
    tone: str,
    style: str = "Photorealistic cinematic",
    characters: Optional[List[dict]] = None,
    objects: Optional[List[dict]] = None,
    visual_context: Optional[dict] = None,
) -> List[Scene]:
    if not scenes:
        return scenes
    style_phrase = style.strip() or "Photorealistic cinematic"

    # Build subject consistency block from defined characters and objects
    valid_chars = [
        c for c in (characters or [])
        if str(c.get("name", "")).strip() and str(c.get("description", "")).strip()
    ][:5]
    valid_objs = [
        o for o in (objects or [])
        if str(o.get("name", "")).strip() and str(o.get("description", "")).strip()
    ][:14]

    consistency_lines: List[str] = []
    if valid_chars or valid_objs:
        consistency_lines.append(
            "SUBJECT CONSISTENCY — when any of these characters or objects appear in the scene, "
            "reproduce their visual description verbatim:"
        )
        for c in valid_chars:
            consistency_lines.append(f"  Character '{c['name'].strip()}': {c['description'].strip()}")
        for o in valid_objs:
            consistency_lines.append(f"  Object '{o['name'].strip()}': {o['description'].strip()}")
    consistency_block = "\n".join(consistency_lines)

    continuity_ctx: dict[str, str] = {
        "location_family": "",
        "costume_family": "",
        "time_logic": "preserve neighboring scene chronology",
        "lighting_direction": "key light from frame-left",
    }
    score_threshold = 3.5

    # Build a reusable context prefix from global visual_context
    _vc = visual_context or {}
    _vc_prefix = ""
    _vc_all_keys = ("time_period", "location", "clothing_style", "visual_atmosphere",
                    "character_name", "character_appearance", "visual_style", "color_palette")
    if _vc and any(_vc.get(k, "") for k in _vc_all_keys):
        _vc_parts = []
        if _vc.get("time_period"):
            _vc_parts.append(f"Era: {_vc['time_period']}")
        if _vc.get("location"):
            _vc_parts.append(f"Setting: {_vc['location']}")
        if _vc.get("clothing_style"):
            _vc_parts.append(f"Clothing: {_vc['clothing_style']}")
        if _vc.get("visual_atmosphere"):
            _vc_parts.append(f"Atmosphere: {_vc['visual_atmosphere']}")
        if _vc.get("character_name"):
            _vc_parts.append(f"Subject: {_vc['character_name']}")
        if _vc.get("character_appearance"):
            _vc_parts.append(f"Appearance: {_vc['character_appearance']}")
        if _vc.get("visual_style"):
            _vc_parts.append(f"Style: {_vc['visual_style']}")
        if _vc.get("color_palette"):
            _vc_parts.append(f"Palette: {_vc['color_palette']}")
        _vc_prefix = "Global visual context — " + ". ".join(_vc_parts) + "."

    for s in scenes:
        spec = _build_scene_prompt_spec(s, continuity_ctx)
        # Inject global visual context into spec fields so prompts carry era DNA
        if _vc:
            if _vc.get("time_period") and not str(spec.get("time_period", "")).strip():
                spec["time_period"] = _vc["time_period"]
            if _vc.get("location") and not str(spec.get("setting/location", "")).strip():
                spec["setting/location"] = _vc["location"]
            if _vc.get("clothing_style"):
                existing = str(spec.get("wardrobe_or_architecture_details", "") or "")
                spec["wardrobe_or_architecture_details"] = (
                    f"{_vc['clothing_style']}; {existing}".strip("; ") if existing else _vc["clothing_style"]
                )
            if _vc.get("visual_atmosphere"):
                existing_lighting = str(spec.get("lighting", "") or "")
                spec["lighting"] = (
                    f"{existing_lighting}; atmosphere: {_vc['visual_atmosphere']}".strip("; ")
                    if existing_lighting
                    else _vc["visual_atmosphere"]
                )
            # Inject new enriched fields
            if _vc.get("character_name") and not str(spec.get("primary_subject", "")).strip():
                spec["primary_subject"] = _vc["character_name"]
            if _vc.get("character_appearance"):
                existing_wardrobe = str(spec.get("wardrobe_or_architecture_details", "") or "")
                appearance_note = f"appearance: {_vc['character_appearance']}"
                if appearance_note not in existing_wardrobe:
                    spec["wardrobe_or_architecture_details"] = (
                        f"{existing_wardrobe}; {appearance_note}".strip("; ") if existing_wardrobe else appearance_note
                    )
            if _vc.get("visual_style") and not str(spec.get("visual_style", "")).strip():
                spec["visual_style"] = _vc["visual_style"]
            if _vc.get("color_palette"):
                spec["color_palette"] = _vc["color_palette"]
            if _vc_prefix:
                spec["global_visual_context"] = _vc_prefix
        # Store visual_context on the scene for later use in image/video generation
        s.visual_context = dict(_vc) if _vc else {}
        if valid_chars or valid_objs:
            details = []
            for c in valid_chars:
                if c["name"].strip().lower() in str(s.script_excerpt).lower():
                    details.append(f"{c['name'].strip()}: {c['description'].strip()}")
            for o in valid_objs:
                if o["name"].strip().lower() in str(s.script_excerpt).lower():
                    details.append(f"{o['name'].strip()}: {o['description'].strip()}")
            if details:
                spec["wardrobe_or_architecture_details"] = (
                    f"{spec.get('wardrobe_or_architecture_details', '')}; continuity descriptors: {'; '.join(details)}"
                ).strip("; ")

        image_prompt = _build_image_prompt(spec, style_phrase=style_phrase, tone=tone)
        video_prompt, video_spec = _build_video_prompt(spec, style_phrase=style_phrase, tone=tone)
        spec["video_spec"] = video_spec
        scores = _prompt_scores(spec, image_prompt, video_prompt)
        if any(value < score_threshold for value in scores.values()):
            spec["historical_context"] = (
                f"{spec.get('historical_context', '')}. Plausible reconstruction only where evidence is uncertain."
            ).strip()
            image_prompt = _build_image_prompt(spec, style_phrase=style_phrase, tone=tone)
            video_prompt, video_spec = _build_video_prompt(spec, style_phrase=style_phrase, tone=tone)
            spec["video_spec"] = video_spec
            scores = _prompt_scores(spec, image_prompt, video_prompt)

        s.image_prompt = image_prompt
        s.video_prompt = video_prompt
        s.negative_prompt = ", ".join(_DEFAULT_NEGATIVE_CUES)
        s.scene_summary = str(spec.get("one_sentence_scene_summary", "") or "")
        s.continuity_notes = str(spec.get("continuity_notes", "") or "")
        s.scene_intent = str(spec.get("scene_intent", "") or "")
        s.source_confidence = str(spec.get("source_confidence", "medium") or "medium")
        s.prompt_spec = spec
        s.video_prompt_spec = video_spec
        s.prompt_scores = scores

        continuity_ctx["location_family"] = str(spec.get("setting/location", "") or continuity_ctx["location_family"])
        continuity_ctx["costume_family"] = str(spec.get("wardrobe_or_architecture_details", "") or continuity_ctx["costume_family"])

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
# Prompt safety sanitization
# ----------------------------
import re as _re

# Ordered list of (regex_pattern, safe_replacement).
# Phrase-level patterns come first so they take priority over word-level ones.
_SAFETY_SANITIZATIONS: list[tuple[str, str]] = [
    # ── Phrases ──────────────────────────────────────────────────────────────
    (r'loading\s+\w+\s+into\s+[a-z\s]*car', 'near a parked vehicle'),
    (r'concentration\s+camps?', 'wartime detention sites'),
    (r'death\s+camps?', 'wartime sites'),
    (r'storm\s*troopers?', 'wartime guards'),
    # ── Named figures / groups ────────────────────────────────────────────────
    (r'\bnazis?\b', 'occupying'),
    (r'\bhitler\b', 'wartime leader'),
    (r'\bgestapo\b', 'secret police'),
    (r'\b(?<!\w)ss(?!\w)\b', 'wartime guards'),   # lone "SS"
    # ── Violence / harm ───────────────────────────────────────────────────────
    (r'\btor?tur(?:e|ing|ed|ous|er)?\b', 'ordeal'),
    (r'\babduct(?:ion|ing|ed|s)?\b', 'mysterious disappearance'),
    (r'\bkidnap(?:ping|ped|per|s)?\b', 'disappearance'),
    (r'\bkill(?:ing|ings|ed|er|ers|s)?\b', 'tragedy'),
    (r'\bmurder(?:ing|ed|er|ers|ous|s)?\b', 'tragedy'),
    (r'\bblood(?:y|ied|shed|bath|stained)?\b', 'aftermath'),
    (r'\bviolen(?:ce|t|tly)\b', 'conflict'),
    (r'\bbrut(?:al|ality|ally|ish)\b', 'harsh'),
    (r'\bdying\b', 'fading'),
    (r'\bdead(?:ly|pan)?\b', 'lost'),
    (r'\bdeath(?:s)?\b', 'loss'),
    (r'\bexecut(?:ion|ions|ed|ing|ioner)\b', 'historical moment'),
    (r'\bhanging\b', 'historical scene'),
    (r'\bbeaten?\b', 'weary'),
    (r'\bbeating(?:s)?\b', 'hardship'),
    (r'\bbruis(?:ed|es|ing)\b', 'tired'),
    (r'\bwound(?:ed|s|ing)?\b', 'fallen figure'),
    (r'\bstrang(?:le|led|ling)\b', 'struggle'),
    (r'\bchok(?:e|ing|ed)\b', 'struggle'),
    (r'\bgun(?:s|fire|shot|shots|man|men)?\b', 'period implement'),
    (r'\bweapon(?:s|ry|ized)?\b', 'period equipment'),
    (r'\brifle(?:s)?\b', 'period equipment'),
    (r'\bpistol(?:s)?\b', 'period implement'),
    (r'\bshoot(?:ing|ings)?\b', 'historical event'),
    (r'\bshot\b', 'historical scene'),
    (r'\bbomb(?:ing|ings|ed|s|er|ers)?\b', 'wartime event'),
    (r'\bexplosion(?:s)?\b', 'dramatic event'),
    (r'\binterrogat(?:ion|ions|ing|ed|or)\b', 'questioning'),
    (r'\bthreat(?:ening|ened|s)?\b', 'tension'),
    (r'\bterror(?:ism|ist|ists|izing)?\b', 'wartime fear'),
    # ── Sensitive groups ──────────────────────────────────────────────────────
    (r'\bchildren\b', 'young figures'),
    (r'\bchild\b', 'young figure'),
    (r'\bkids?\b', 'young figures'),
    (r'\bjuvenile(?:s)?\b', 'young person'),
    (r'\bbab(?:y|ies)\b', 'small figure'),
    (r'\binfant(?:s)?\b', 'small figure'),
    (r'\btoddler(?:s)?\b', 'small figure'),
    # ── Other sensitive topics ────────────────────────────────────────────────
    (r'\bholocaust\b', 'wartime tragedy'),
    (r'\bghetto(?:s)?\b', 'wartime district'),
    (r'\bgenoci(?:de|dal)\b', 'historical tragedy'),
    (r'\bprisoner(?:s)?\b', 'captive figure'),
    (r'\bprison(?:s|er)?\b', 'wartime facility'),
    (r'\bsuspicion\b', 'uncertainty'),
    (r'\bfear(?:ful|fully)?\b', 'unease'),
    (r'\bsinister\b', 'mysterious'),
]


def _sanitize_prompt_for_safety(prompt: str) -> str:
    """Replace known Imagen safety-filter triggers with neutral historical equivalents."""
    result = prompt
    for pattern, replacement in _SAFETY_SANITIZATIONS:
        result = _re.sub(pattern, replacement, result, flags=_re.IGNORECASE)
    return result


# ----------------------------
# Image generation (one scene)
# ----------------------------
def generate_image_for_scene(
    scene: Scene,
    aspect_ratio: str = "16:9",
    visual_style: str = "Photorealistic cinematic",
    visual_anchor: str = "",
    provider: str = "falai",
) -> Scene:
    base = (scene.image_prompt or "").strip()
    if not base:
        base = "Create a cinematic historical visual."

    context = (
        f"Visual intent: {scene.visual_intent}\n"
        f"Scene excerpt: {scene.script_excerpt}"
    ).strip()

    _anchor_line = f"Visual setting anchor: {visual_anchor}\n" if visual_anchor else ""

    # Build global visual context block from scene.visual_context (FIX 2)
    _vc = getattr(scene, "visual_context", None) or {}
    _context_block = ""
    if _vc and any(_vc.get(k, "") for k in ("time_period", "location", "clothing_style", "visual_atmosphere")):
        _context_block = (
            f"Time period: {_vc.get('time_period', '')}. "
            f"Location: {_vc.get('location', '')}. "
            f"Clothing: {_vc.get('clothing_style', '')}. "
            f"Atmosphere: {_vc.get('visual_atmosphere', '')}.\n"
        )
        print(f"[visual_context] scene={scene.index} context_block={_context_block[:120]!r}")

    prompt = (
        f"Style: {visual_style}. Unified cinematic color grade and period-accurate historical atmosphere.\n"
        f"{_anchor_line}"
        f"{_context_block}"
        f"STRICT RULE: Absolutely no text, letters, words, numbers, captions, subtitles, watermarks, logos, "
        f"labels, signs with readable text, or writing of any kind anywhere in the image.\n"
        f"{base}\n\n"
        f"{context}\n"
        f"Compose for {aspect_ratio}. Painterly, consistent tonal palette matching the historical era. "
        f"No text or writing of any kind."
    )

    png_bytes: Optional[bytes] = None
    last_error: Optional[str] = None
    scene.image_error = ""

    for attempt in range(4):
        try:
            raw_images = generate_scene_image_bytes(
                prompt,
                number_of_images=1,
                aspect_ratio=aspect_ratio,
                provider=provider,
            )
            raw = raw_images[0] if raw_images else None
            if not raw:
                raise RuntimeError(
                    "Image provider returned no image bytes for this prompt (likely safety-filtered)."
                )

            img = Image.open(BytesIO(raw)).convert("RGB")
            img = _crop_to_aspect(img, aspect_ratio)

            out = BytesIO()
            img.save(out, format="PNG")
            png_bytes = out.getvalue()
            break
        except Exception as e:
            err_text = str(e)
            if "missing gemini api key" in err_text.lower():
                last_error = (
                    "Missing Gemini API key. Set GEMINI_API_KEY in .streamlit/secrets.toml"
                )
            elif "invalid google_ai_studio_api_key" in err_text.lower() or "api key not valid" in err_text.lower():
                last_error = (
                    "Invalid GOOGLE_AI_STUDIO_API_KEY. Generate a valid Google AI Studio API key and set it in "
                    "`.streamlit/secrets.toml` as `GEMINI_API_KEY` (or `GOOGLE_AI_STUDIO_API_KEY`)."
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

    # ── Safety-filter retry ───────────────────────────────────────────────────
    # If the prompt was blocked by Imagen's content policy, sanitize trigger
    # terms and try once more before giving up.
    if not png_bytes and last_error and "safety-filtered" in last_error.lower():
        sanitized = _sanitize_prompt_for_safety(prompt)
        if sanitized != prompt:
            print(f"[Imagen] Safety-filter detected — retrying with sanitized prompt (scene {scene.index})")
            try:
                raw_images = generate_scene_image_bytes(
                    sanitized,
                    number_of_images=1,
                    aspect_ratio=aspect_ratio,
                    provider=provider,
                )
                raw = raw_images[0] if raw_images else None
                if raw:
                    img = Image.open(BytesIO(raw)).convert("RGB")
                    img = _crop_to_aspect(img, aspect_ratio)
                    out = BytesIO()
                    img.save(out, format="PNG")
                    png_bytes = out.getvalue()
                    last_error = None
                    scene.image_error = ""
                    print(f"[Imagen] Safety-filter retry succeeded (scene {scene.index})")
                else:
                    print(f"[Imagen] Safety-filter retry also blocked (scene {scene.index})")
            except Exception as e:
                print(f"[Imagen] Safety-filter retry failed (scene {scene.index}): {e}")

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
