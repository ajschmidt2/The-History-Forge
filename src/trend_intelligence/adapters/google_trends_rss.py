from __future__ import annotations

import xml.etree.ElementTree as ET

import requests

from src.config import get_secret
from src.trend_intelligence.adapters.base import TrendSourceAdapter
from src.trend_intelligence.models import TrendSignal


class GoogleTrendsRssAdapter(TrendSourceAdapter):
    source_name = "google_trends_rss"

    def __init__(self, geo: str | None = None) -> None:
        self.geo = geo or get_secret("GOOGLE_TRENDS_GEO", "US")

    def fetch_topics(self, *, limit: int) -> list[TrendSignal]:
        url = f"https://trends.google.com/trending/rss?geo={self.geo}"
        response = requests.get(url, timeout=15)
        response.raise_for_status()

        root = ET.fromstring(response.text)
        channel = root.find("channel")
        if channel is None:
            return []

        topics: list[TrendSignal] = []
        for item in channel.findall("item")[:limit]:
            title = (item.findtext("title") or "").strip()
            if not title:
                continue
            approx_traffic = ""
            for child in item:
                if child.tag.endswith("approx_traffic"):
                    approx_traffic = (child.text or "").strip()
                    break
            momentum = 0.65
            if "K+" in approx_traffic:
                momentum = 0.75
            if "M+" in approx_traffic:
                momentum = 0.9
            topics.append(
                TrendSignal(
                    topic=title,
                    source=self.source_name,
                    momentum=momentum,
                    reason=f"Google Trends traffic signal: {approx_traffic or 'rising query'}",
                    raw={"approx_traffic": approx_traffic},
                )
            )
        return topics
