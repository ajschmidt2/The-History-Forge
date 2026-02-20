from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from html import unescape
from pathlib import Path
from typing import Iterable
from urllib.parse import urlparse

import requests

CACHE_DIR = Path("data/research_cache")
USER_AGENT = "TheHistoryForgeResearch/1.0 (+https://example.local)"


@dataclass
class Source:
    title: str
    url: str
    snippet: str


def _topic_hash(topic: str) -> str:
    return hashlib.sha256(topic.strip().lower().encode("utf-8")).hexdigest()[:16]


def _cache_path(topic: str) -> Path:
    return CACHE_DIR / f"{_topic_hash(topic)}.json"


def _clean_text(value: str) -> str:
    text = unescape(value or "")
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _allowed_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _extract_search_results(html: str, max_results: int) -> list[Source]:
    # DuckDuckGo HTML result blocks
    pattern = re.compile(
        r'<a[^>]*class="result__a"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>.*?'
        r'<a[^>]*class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
        flags=re.DOTALL,
    )

    results: list[Source] = []
    for match in pattern.finditer(html):
        href = unescape(match.group("href") or "").strip()
        if not _allowed_url(href):
            continue
        title = _clean_text(match.group("title") or "")
        snippet = _clean_text(match.group("snippet") or "")
        if not title:
            continue
        results.append(Source(title=title, url=href, snippet=snippet))
        if len(results) >= max_results:
            break
    return results


def _load_cache(topic: str) -> list[Source] | None:
    path = _cache_path(topic)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, list):
        return None

    sources: list[Source] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title", "") or "").strip()
        url = str(item.get("url", "") or "").strip()
        snippet = str(item.get("snippet", "") or "").strip()
        if title and _allowed_url(url):
            sources.append(Source(title=title, url=url, snippet=snippet))
    return sources or None


def _save_cache(topic: str, sources: Iterable[Source]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = _cache_path(topic)
    serializable = [asdict(source) for source in sources]
    path.write_text(json.dumps(serializable, indent=2), encoding="utf-8")


def search_topic(topic: str, max_results: int = 6) -> list[Source]:
    normalized_topic = (topic or "").strip()
    if not normalized_topic:
        return []

    max_results = max(3, min(int(max_results), 8))
    cached = _load_cache(normalized_topic)
    if cached:
        return cached[:max_results]

    response = requests.get(
        "https://duckduckgo.com/html/",
        params={"q": normalized_topic},
        timeout=20,
        headers={"User-Agent": USER_AGENT},
    )
    response.raise_for_status()
    sources = _extract_search_results(response.text, max_results=max_results)
    if sources:
        _save_cache(normalized_topic, sources)
    return sources


def summarize_sources(topic: str, sources: list[Source], *, max_facts: int = 12) -> str:
    normalized_topic = (topic or "").strip() or "Topic"
    safe_sources = [source for source in sources if source.title and _allowed_url(source.url)]
    if not safe_sources:
        return ""

    max_facts = max(8, min(int(max_facts), 15))
    facts: list[str] = []
    timeline: list[str] = []
    seen_fact_keys: set[str] = set()

    year_pattern = re.compile(r"\b(1[0-9]{3}|20[0-9]{2})\b")

    for idx, source in enumerate(safe_sources, start=1):
        snippet = (source.snippet or "").strip()
        if not snippet:
            continue

        cleaned = re.sub(r"\s+", " ", snippet)
        fact_key = cleaned.lower()[:120]
        if fact_key in seen_fact_keys:
            continue
        seen_fact_keys.add(fact_key)
        facts.append(f"- {cleaned} [{idx}]")

        year_match = year_pattern.search(cleaned)
        if year_match and len(timeline) < 10:
            timeline.append(f"- {year_match.group(1)} — {cleaned} [{idx}]")

        if len(facts) >= max_facts:
            break

    if not facts:
        facts = [f"- Limited extractable web snippets were found for {normalized_topic}; verify with primary sources."]

    if not timeline:
        timeline = [
            "- Timeline extraction unavailable from snippets; verify dates from listed sources.",
        ]

    people_places = [
        "- People/Places mentioned in snippets should be verified in the source pages.",
    ]
    suggested_angles = [
        "1. Explain what changed over time and why it mattered.",
        "2. Contrast common myths vs. source-backed evidence.",
        "3. Focus on under-discussed actors and locations in the story.",
    ]
    risky_claims = [
        "- Any causal claim not directly supported by at least one listed source.",
        "- Numeric figures and casualty counts that vary across outlets.",
        "- Attributed quotes unless confirmed in primary records.",
    ]

    source_lines = [f"[{i}] {source.title} — {source.url}" for i, source in enumerate(safe_sources, start=1)]

    return "\n".join(
        [
            f"# Research Brief: {normalized_topic}",
            "",
            "## Key Facts",
            *facts,
            "",
            "## Timeline",
            *timeline[:10],
            "",
            "## Key People and Places",
            *people_places,
            "",
            "## Suggested Angles",
            *suggested_angles,
            "",
            "## Risky Claims / Uncertain Areas",
            *risky_claims,
            "",
            "## Sources",
            *source_lines,
        ]
    )
