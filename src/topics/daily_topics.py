"""Daily short-video topic generation with duplicate protection."""

from __future__ import annotations

import json
import random
from datetime import date
from pathlib import Path

from src.config import get_openai_config

USED_TOPICS_PATH = Path("data/daily_topics_used.json")

CURATED_TOPICS: tuple[str, ...] = (
    "The nurse who smuggled 2,500 children out of the Warsaw Ghetto",
    "The dancing plague of 1518 that terrified an entire city",
    "The bizarre war where an emu population beat Australian soldiers",
    "The forgotten inventor whose safety system saved thousands of miners",
    "How one codebreaker helped shorten World War II in silence",
    "The medieval battle won by weather, mud, and timing",
    "The strange Victorian obsession that created modern forensics",
    "The ancient mechanism that looked like a lost computer",
    "The woman pharaoh history tried to erase",
    "How a failed invention accidentally changed naval warfare",
    "The messenger who ran through battle lines to save an empire",
    "A city that vanished under volcanic ash in a single day",
)


def load_used_topics(path: Path = USED_TOPICS_PATH) -> set[str]:
    if not path.exists():
        return set()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    if not isinstance(payload, list):
        return set()
    return {str(item.get("topic", "") if isinstance(item, dict) else item).strip().lower() for item in payload if str(item).strip()}


def save_used_topic(topic: str, *, run_date: date | None = None, path: Path = USED_TOPICS_PATH) -> None:
    normalized = str(topic or "").strip()
    if not normalized:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, str]] = []
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                rows = [r for r in loaded if isinstance(r, dict)]
        except (OSError, json.JSONDecodeError):
            rows = []
    rows.append({"topic": normalized, "date": (run_date or date.today()).isoformat()})
    path.write_text(json.dumps(rows[-1000:], indent=2), encoding="utf-8")


def _generate_topic_with_openai() -> str:
    config = get_openai_config()
    api_key = str(config.get("api_key") or "").strip()
    model = str(config.get("model") or "gpt-4o-mini").strip()
    if not api_key:
        return ""
    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        resp = client.chat.completions.create(
            model=model,
            temperature=0.8,
            messages=[
                {"role": "system", "content": "You generate short-form, high-retention history video topic ideas."},
                {
                    "role": "user",
                    "content": (
                        "Give one unique topic for a 60-second history short. "
                        "Prefer unsung heroes, bizarre moments, strange inventions, battlefield turning points, "
                        "forgotten figures, or ancient mysteries. "
                        "Return only the topic phrase in one line."
                    ),
                },
            ],
        )
        return str(resp.choices[0].message.content or "").strip()
    except Exception:
        return ""


def generate_daily_topic(*, used_topics: set[str] | None = None) -> str:
    used = used_topics if used_topics is not None else load_used_topics()
    for _ in range(3):
        candidate = _generate_topic_with_openai()
        if candidate and candidate.lower() not in used:
            return candidate

    available = [topic for topic in CURATED_TOPICS if topic.lower() not in used]
    if available:
        return random.choice(available)
    return f"Hidden history mystery #{len(used) + 1}"
