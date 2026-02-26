from __future__ import annotations

import re
from typing import List

from utils import get_secret


def _openai_client():
    import os  # defensive local import to prevent future refactor/import-order regressions

    # get_secret normalises placeholder values (e.g. "PASTE_KEY_HERE") to "".
    # Avoid raw os.getenv fallbacks here: they bypass normalisation and would
    # send placeholder strings to OpenAI, causing a 401 AuthenticationError.
    key = get_secret("openai_api_key", "").strip()
    if not key:
        return None

    os.environ.setdefault("OPENAI_API_KEY", key)
    os.environ.setdefault("openai_api_key", key)

    from openai import OpenAI

    return OpenAI(api_key=key)


def _fallback_tighten(script: str) -> str:
    text = (script or "").strip()
    replacements = {
        " very ": " ",
        " really ": " ",
        " in order to ": " to ",
        " it is important to note that ": " ",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def refine_for_clarity(script: str) -> str:
    base = (script or "").strip()
    if not base:
        return ""

    client = _openai_client()
    if client is None:
        return _fallback_tighten(base)

    prompt = (
        "Improve this documentary script for clarity and consistency. "
        "Ensure setup/payoff links are clear and remove contradictions. "
        "Keep facts and structure intact. Return only revised script.\n\n"
        f"{base}"
    )
    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            temperature=0.2,
            messages=[
                {"role": "system", "content": "You are a script editor focused on clarity and internal consistency."},
                {"role": "user", "content": prompt},
            ],
        )
        return resp.choices[0].message.content.strip()
    except Exception as exc:
        print("OpenAI request failed:", str(exc))
        return _fallback_tighten(base)


def refine_for_retention(script: str) -> str:
    base = (script or "").strip()
    if not base:
        return ""

    client = _openai_client()
    if client is None:
        return _fallback_tighten(base)

    prompt = (
        "Rewrite for audience retention: tighten sentences, remove filler, "
        "and add light curiosity gaps without clickbait. Keep factual meaning. "
        "Return only revised script text.\n\n"
        f"{base}"
    )
    try:
        resp = client.chat.completions.create(
            model="gpt-4.1-mini",
            temperature=0.4,
            messages=[
                {"role": "system", "content": "You are a YouTube script retention editor."},
                {"role": "user", "content": prompt},
            ],
        )
        return resp.choices[0].message.content.strip()
    except Exception as exc:
        print("OpenAI request failed:", str(exc))
        return _fallback_tighten(base)


def _heuristic_uncertain_notes(script: str) -> List[str]:
    notes: List[str] = []
    lines = re.split(r"(?<=[.!?])\s+", script)
    for line in lines:
        lower = line.lower()
        if any(token in lower for token in ["always", "never", "undeniably", "proved", "definitely"]):
            notes.append(f"- Verify strong claim wording: \"{line.strip()}\"")
        if re.search(r"\b\d{3,}\b", line):
            notes.append(f"- Verify numeric/date claim: \"{line.strip()}\"")
    return notes[:8]


def flag_uncertain_claims(script: str, research_brief: str) -> str:
    base = (script or "").strip()
    if not base:
        return ""

    client = _openai_client()
    if client is not None:
        prompt = (
            "You are editing a history documentary script. "
            "Identify any claims that are uncertain or weakly sourced based on the research brief. "
            "Rewrite those sentences in place using softer, hedged language "
            "(e.g. 'reportedly', 'possibly', 'it is believed that', 'accounts suggest'). "
            "Do NOT list revisions, do NOT include section headers, do NOT add commentary or notes. "
            "Return ONLY the complete revised script as clean, flowing narrative prose — "
            "exactly as it should appear to the viewer, with no formatting or meta-text.\n\n"
            f"Research brief:\n{(research_brief or '').strip()}\n\n"
            f"Script:\n{base}"
        )
        try:
            resp = client.chat.completions.create(
                model="gpt-4.1-mini",
                temperature=0.2,
                messages=[
                    {"role": "system", "content": "You are a cautious historical fact-check editor. You apply softened language directly inline and return only the revised script text with no commentary."},
                    {"role": "user", "content": prompt},
                ],
            )
            return resp.choices[0].message.content.strip()
        except Exception as exc:
            print("OpenAI request failed:", str(exc))
            pass

    # Heuristic fallback: apply simple softening substitutions in-place
    uncertain_phrases = {
        r"\bproved\b": "suggested",
        r"\bundeniably\b": "reportedly",
        r"\bdefinitely\b": "possibly",
        r"\balways\b": "often",
        r"\bnever\b": "rarely",
    }
    text = base
    for pattern, replacement in uncertain_phrases.items():
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)
    return text
