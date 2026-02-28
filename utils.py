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

from image_gen import generate_imagen_images
from src.lib.openai_config import DEFAULT_OPENAI_MODEL, resolve_openai_config

# ----------------------------
# Secrets
# ----------------------------

# Regex to detect common API-key placeholder patterns beyond the exact-match set below.
# Catches variants like PASTE_KEHERE (typo), PASTE_API_KEY, ADD_KEY_HERE, YOUR_API_KEY, etc.
_PLACEHOLDER_RE = re.compile(
    r"^paste"                  # PASTE_KEY_HERE, PASTE_KEHERE, PASTEKEHERE, PASTE_API_KEY …
    r"|[_\-\s]here$"           # ADD_KEY_HERE, YOUR_TOKEN_HERE, INSERT_SECRET_HERE …
    r"|^your[_\-\s]"           # YOUR_API_KEY, YOUR_KEY, YOUR_TOKEN …
    r"|^(replace[\-_]?me|fixme|todo|changeme)$",
    re.IGNORECASE,
)


def _normalize_secret(value: str) -> str:
    cleaned = str(value or "").strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"\"", "'"}:
        cleaned = cleaned[1:-1].strip()
    lowered = cleaned.lower()
    if lowered in {"paste_key_here", "your_api_key_here", "replace_me", "none", "null"}:
        return ""
    if _PLACEHOLDER_RE.search(cleaned):
        return ""
    return cleaned




def _secret_from_mapping(mapping: Any, path: tuple[str, ...]) -> str:
    current = mapping
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return ""
        current = current[key]
    return _normalize_secret(str(current))


def _find_secret_in_mapping(mapping: Any, key_aliases: set[str]) -> str:
    """Depth-first search for a non-placeholder secret under any matching alias."""
    if isinstance(mapping, Mapping):
        for key, value in mapping.items():
            key_name = str(key).strip().lower()
            if key_name in key_aliases and not isinstance(value, Mapping):
                normalized = _normalize_secret(str(value))
                if normalized:
                    return normalized
            nested = _find_secret_in_mapping(value, key_aliases)
            if nested:
                return nested
    return ""

def _get_secret(name: str, default: str = "") -> str:
    candidates = [
        name,
        name.lower(),
        name.upper(),
        "OPENAI_API_KEY" if "openai" in name.lower() else "",
        "openai_api_key" if "openai" in name.lower() else "",
        "OPENAI_KEY" if "openai" in name.lower() else "",
        "openai_key" if "openai" in name.lower() else "",
        "api_key" if "openai" in name.lower() else "",
    ]
    candidates = [c for c in candidates if c]

    try:
        import streamlit as st  # type: ignore

        if hasattr(st, "secrets"):
            for key in candidates:
                if key in st.secrets:
                    value = _normalize_secret(str(st.secrets[key]))
                    if value:
                        if key.upper().startswith("OPENAI") or "openai" in key.lower() or key.lower() == "api_key":
                            os.environ.setdefault("OPENAI_API_KEY", value)
                            os.environ.setdefault("openai_api_key", value)
                        return value

            if "openai" in name.lower():
                nested_paths = [
                    ("openai", "api_key"),
                    ("openai", "OPENAI_API_KEY"),
                    ("OPENAI", "api_key"),
                    ("OPENAI", "API_KEY"),
                    ("providers", "openai", "api_key"),
                ]
                for path in nested_paths:
                    value = _secret_from_mapping(st.secrets, path)
                    if value:
                        os.environ.setdefault("OPENAI_API_KEY", value)
                        os.environ.setdefault("openai_api_key", value)
                        return value

                recursive_value = _find_secret_in_mapping(
                    st.secrets,
                    {
                        "openai_api_key",
                        "openai_key",
                        "openai",
                        "api_key",
                        "apikey",
                        "openaiapikey",
                        "openai-api-key",
                    },
                )
                if recursive_value:
                    os.environ.setdefault("OPENAI_API_KEY", recursive_value)
                    os.environ.setdefault("openai_api_key", recursive_value)
                    return recursive_value

            if "elevenlabs" in name.lower():
                nested_paths = [
                    ("elevenlabs", "api_key"),
                    ("elevenlabs", "ELEVENLABS_API_KEY"),
                    ("ELEVENLABS", "api_key"),
                    ("ELEVENLABS", "API_KEY"),
                ]
                for path in nested_paths:
                    value = _secret_from_mapping(st.secrets, path)
                    if value:
                        return value
    except Exception:
        pass

    for key in candidates:
        value = _normalize_secret(os.getenv(key, ""))
        if value:
            return value

    return _normalize_secret(default)


def get_secret(name: str, default: str = "") -> str:
    return _get_secret(name, default)


def get_openai_text_model(default: str = DEFAULT_OPENAI_MODEL) -> str:
    """Resolve and validate the OpenAI model ID from config."""
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
        resp = client.chat.completions.create(
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
        resp = client.chat.completions.create(
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
        resp = client.chat.completions.create(
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
        resp = client.chat.completions.create(
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
        resp = client.chat.completions.create(
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
        resp = client.chat.completions.create(
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
        resp = client.chat.completions.create(
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
        resp = client.chat.completions.create(
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
        resp = client.chat.completions.create(
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
        resp = client.chat.completions.create(
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
def generate_prompts_for_scenes(
    scenes: List[Scene],
    tone: str,
    style: str = "Photorealistic cinematic",
    characters: Optional[List[dict]] = None,
    objects: Optional[List[dict]] = None,
) -> List[Scene]:
    if not scenes:
        return scenes

    client = _openai_client()
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

    # fallback (no OpenAI) — still ensures prompts exist
    if client is None:
        for s in scenes:
            parts = [
                f"Style: {style_phrase}. Tone: {tone}.",
                s.visual_intent,
                f"Scene excerpt: {s.script_excerpt}",
                "No text overlays, captions, logos, or watermarks. High detail.",
            ]
            if consistency_block:
                parts.append(consistency_block)
            s.image_prompt = "\n".join(parts)
        return scenes

    packed = [
        {"index": s.index, "title": s.title, "text": s.script_excerpt, "visual_intent": s.visual_intent}
        for s in scenes
    ]
    constraints = [
        "No text overlays, captions, logos, or watermarks.",
        "Be specific about subject, setting, era cues, lighting, mood, camera feel.",
        "Explicitly state the era/time period and setting grounded in the excerpt.",
        "Match the selected style strongly.",
    ]
    payload: dict = {
        "tone": tone,
        "style": style_phrase,
        "task": (
            "Write one image prompt per scene. Return exactly one prompt per scene in the same order. "
            "Each prompt must name the time period and location inferred from the excerpt, plus concrete "
            "setting details (architecture, clothing, props) that match the story."
        ),
        "output": {"format": "json", "field": "prompts"},
        "scenes": packed,
        "constraints": constraints,
    }
    if valid_chars or valid_objs:
        payload["subject_consistency"] = {
            "characters": [
                {"name": c["name"].strip(), "description": c["description"].strip()} for c in valid_chars
            ],
            "objects": [
                {"name": o["name"].strip(), "description": o["description"].strip()} for o in valid_objs
            ],
        }
        constraints.append(
            "For any character or object listed in subject_consistency that appears in the scene, "
            "include their exact visual description in the prompt."
        )

    prompts: List[str] = []
    try:
        resp = client.chat.completions.create(
            model=get_openai_text_model(),
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
        context_parts = [
            f"Visual intent: {s.visual_intent}",
            f"Scene excerpt: {s.script_excerpt}",
            "Include the time period and location inferred from the excerpt. "
            "Call out architecture, clothing, and props that fit the era. "
            "No text overlays, captions, logos, or watermarks. High detail.",
        ]
        if consistency_block:
            context_parts.append(consistency_block)
        context = "\n".join(context_parts)
        if not p:
            p = f"Style: {style_phrase}. Tone: {tone}.\n{context}"
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
                raise RuntimeError(
                    "Imagen returned no image bytes for this prompt (likely safety-filtered)."
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
