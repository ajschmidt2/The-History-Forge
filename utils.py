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
def _normalize_secret(value: str) -> str:
    cleaned = str(value or "").strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in {"\"", "'"}:
        cleaned = cleaned[1:-1].strip()
    lowered = cleaned.lower()
    if lowered in {"paste_key_here", "your_api_key_here", "replace_me", "none", "null"}:
        return ""
    return cleaned


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
    except Exception:
        pass

    for key in candidates:
        value = _normalize_secret(os.getenv(key, ""))
        if value:
            return value

    return _normalize_secret(default)


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
    estimated_duration_sec: float = 0.0

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

    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        temperature=0.2,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
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
            model="gpt-4.1-mini",
            temperature=0.4,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    except Exception:
        return _default_outline(topic_clean)

    raw = resp.choices[0].message.content.strip()
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = None
    return _normalize_outline_payload(parsed, topic_clean)


def generate_script_from_outline(outline: dict[str, Any], tone: str, reading_level: str, pacing: str) -> str:
    normalized_outline = _normalize_outline_payload(outline, str(outline.get("hook", "History topic")) if isinstance(outline, dict) else "History topic")

    client = _openai_client()
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
        "Write one continuous script with no headings or bullet points. "
        "Cover each beat in order, include natural transitions, and end with the CTA."
    )

    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            temperature=0.6,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    except Exception:
        beat_titles = ", ".join([beat.get("title", "Beat") for beat in normalized_outline.get("beats", [])])
        return (
            "[OpenAI request failed] Placeholder script from outline.\n\n"
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
) -> str:
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
        "\nWrite a single continuous narration script with:\n"
        "1) Hook (1–3 sentences)\n"
        "2) Main story (well-structured paragraphs)\n"
        "3) Ending CTA (1–2 sentences)\n"
        "No headings. No bullet lists."
        f"{brief_block}"
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

    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            temperature=0.6,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
    except Exception:
        beat_titles = ", ".join([beat.get("title", "Beat") for beat in normalized_outline.get("beats", [])])
        return (
            "[OpenAI request failed] Placeholder script from outline.\n\n"
            f"Hook: {normalized_outline['hook']}\n"
            f"Context: {normalized_outline['context']}\n"
            f"Beats: {beat_titles}\n"
            f"Twist: {normalized_outline['twist_or_insight']}\n"
            f"Modern relevance: {normalized_outline['modern_relevance']}\n"
            f"CTA: {normalized_outline['cta']}"
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
    resp = client.chat.completions.create(
        model="gpt-4.1-mini",
        temperature=0.7,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
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
        return None, "No image returned for this prompt (possibly safety-filtered)."
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


def split_script_into_scenes(script: str, max_scenes: int = 8, outline: dict[str, Any] | None = None, wpm: int = 160) -> List[Scene]:
    script = (script or "").strip()
    if not script:
        return []

    target = max(1, min(int(max_scenes or 8), 75))
    beats = _outline_beats(outline)

    if beats:
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", script) if s.strip()]
        base_units = sentences if len(sentences) >= target else [p.strip() for p in re.split(r"\n\s*\n+", script) if p.strip()]
        if not base_units:
            base_units = [script]
        groups = _split_into_groups(base_units, target)

        scenes: list[Scene] = []
        for i in range(target):
            beat = beats[i] if i < len(beats) else {}
            excerpt = " ".join(groups[i]).strip() if i < len(groups) else ""
            excerpt = excerpt or script[:280].strip()
            beat_text = " ".join(beat.get("bullets", []))
            keyword_source = f"{beat.get('title', '')} {beat_text} {excerpt}"
            scenes.append(
                Scene(
                    index=i + 1,
                    title=str(beat.get("title", "") or f"Scene {i+1}"),
                    script_excerpt=excerpt,
                    visual_intent=_extract_visual_keywords(keyword_source),
                    estimated_duration_sec=_estimate_duration_sec(excerpt, wpm),
                )
            )
        return scenes

    chunks = _split_by_headings_paragraphs(script, target)
    scenes: list[Scene] = []
    for i, txt in enumerate(chunks[:target], start=1):
        excerpt = txt.strip() or script[:280].strip()
        scenes.append(
            Scene(
                index=i,
                title=f"Scene {i}",
                script_excerpt=excerpt,
                visual_intent=_extract_visual_keywords(excerpt),
                estimated_duration_sec=_estimate_duration_sec(excerpt, wpm),
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
