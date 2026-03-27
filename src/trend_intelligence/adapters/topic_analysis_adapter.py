from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from src.config import get_openai_config, get_secret
from src.trend_intelligence.adapters.interfaces import TopicAnalysisAdapter
from src.trend_intelligence.adapters.schemas import TopicAnalysis, VideoResult
from src.lib.openai_config import DEFAULT_OPENAI_MODEL
from utils import openai_chat_completion

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TopicAnalysisPrompt:
    system: str
    user: str


class DeterministicTopicAnalysisAdapter(TopicAnalysisAdapter):
    """Stable fallback adapter used when no provider is configured or provider calls fail."""

    source_name = "deterministic_topic_analysis"

    def analyze_topic(self, topic: str, videos: list[VideoResult], *, brand_focus: str = "all") -> TopicAnalysis:
        video_count = len(videos)
        top_channel = videos[0].channel_title if videos else "N/A"
        avg_views = int(sum(video.view_count for video in videos) / video_count) if video_count else 0

        return TopicAnalysis(
            topic=topic,
            explanation=(
                f"{topic} is trending due to sustained discovery signals and {video_count} related uploads. "
                f"Observed top-channel pattern: {top_channel}. "
                f"Average sampled views: {avg_views:,}."
            ),
            angles=(
                f"What most people miss about {topic}",
                f"The chain reaction that made {topic} inevitable",
                f"How {topic} still impacts current geopolitics",
            ),
            hooks=(
                f"You were probably taught {topic} backwards.",
                f"This one decision changed {topic} forever.",
                f"The hidden catalyst behind {topic} nobody mentions.",
            ),
            thumbnail_ideas=(
                f"Split timeline graphic showing before/after {topic}",
                f"Historic map + bold text: '{topic}: WHY NOW?'",
                "Portrait close-up + turning-point date in red",
            ),
            source=self.source_name,
        )


class OpenAITopicAnalysisAdapter(TopicAnalysisAdapter):
    """Provider-backed analysis adapter with deterministic fallback."""

    source_name = "openai_topic_analysis"

    def __init__(
        self,
        *,
        fallback: TopicAnalysisAdapter | None = None,
        model: str | None = None,
    ) -> None:
        self.fallback = fallback or DeterministicTopicAnalysisAdapter()
        configured_model = get_secret("OPENAI_MODEL") or get_secret("openai_model")
        self.model = (model or configured_model or DEFAULT_OPENAI_MODEL).strip()
        self._api_key = str(get_openai_config().get("api_key") or "").strip()

    def analyze_topic(self, topic: str, videos: list[VideoResult], *, brand_focus: str = "all") -> TopicAnalysis:
        if not self._api_key:
            return self.fallback.analyze_topic(topic, videos, brand_focus=brand_focus)

        try:
            from openai import OpenAI

            client = OpenAI(api_key=self._api_key)
            prompt = _build_prompt(topic, videos, brand_focus=brand_focus)
            response = openai_chat_completion(
                client,
                model=self.model,
                temperature=0.25,
                response_format={"type": "json_object"},
                messages=[
                    {"role": "system", "content": prompt.system},
                    {"role": "user", "content": prompt.user},
                ],
            )
            raw = (response.choices[0].message.content or "").strip()
            parsed = _parse_response(raw)
            return TopicAnalysis(
                topic=topic,
                explanation=parsed["why_trending"],
                angles=parsed["content_angles"],
                hooks=parsed["opening_hooks"],
                thumbnail_ideas=parsed["thumbnail_ideas"],
                source=self.source_name,
            )
        except Exception:
            logger.exception("Topic analysis provider failed. Falling back to deterministic analysis.")
            return self.fallback.analyze_topic(topic, videos, brand_focus=brand_focus)


def _build_prompt(topic: str, videos: list[VideoResult], *, brand_focus: str = "all") -> TopicAnalysisPrompt:
    condensed_videos = [
        {
            "title": video.title,
            "channel": video.channel_title,
            "views": video.view_count,
            "likes": video.like_count,
            "comments": video.comment_count,
            "duration_minutes": video.duration_minutes,
            "published_at": video.published_at,
        }
        for video in videos[:8]
    ]

    focus_instruction = (
        f"The creator's current content focus is: {brand_focus}. "
        "Tailor all angles, hooks, and thumbnail ideas specifically to that focus area."
        if brand_focus.lower() != "all"
        else "The creator covers all areas of history."
    )

    system = (
        "You are a trend intelligence strategist for history-focused video channels. "
        "Return strict JSON only and never include markdown."
    )
    user = (
        "Analyze why this history topic may be trending and produce creator-ready ideation.\n"
        f"Topic: {topic}\n"
        f"Creator focus: {focus_instruction}\n"
        f"Sampled videos (JSON): {json.dumps(condensed_videos, ensure_ascii=False)}\n\n"
        "Return JSON with exactly this shape:\n"
        "{\n"
        '  "why_trending": "string",\n'
        '  "content_angles": ["string", "string", "string"],\n'
        '  "opening_hooks": ["string", "string", "string"],\n'
        '  "thumbnail_ideas": ["string", "string", "string"]\n'
        "}\n"
        "Rules:\n"
        "- Keep each item concise and specific to this topic and the creator focus.\n"
        "- No numbering in strings.\n"
        "- Avoid generic advice and avoid policy/safety disclaimers.\n"
    )
    return TopicAnalysisPrompt(system=system, user=user)


def _parse_response(raw: str) -> dict[str, str | tuple[str, ...]]:
    payload = json.loads(raw)

    why_trending = _clean_string(payload.get("why_trending"))
    content_angles = _clean_items(payload.get("content_angles"))
    opening_hooks = _clean_items(payload.get("opening_hooks"))
    thumbnail_ideas = _clean_items(payload.get("thumbnail_ideas"))

    if not why_trending or not content_angles or not opening_hooks or not thumbnail_ideas:
        raise ValueError("Topic analysis response missing required fields.")

    return {
        "why_trending": why_trending,
        "content_angles": content_angles,
        "opening_hooks": opening_hooks,
        "thumbnail_ideas": thumbnail_ideas,
    }


def _clean_string(value: object) -> str:
    return str(value or "").strip()


def _clean_items(items: object) -> tuple[str, str, str]:
    if not isinstance(items, list):
        return tuple()
    cleaned = [str(item or "").strip() for item in items if str(item or "").strip()]
    padded = cleaned[:3]
    if len(padded) != 3:
        return tuple()
    return (padded[0], padded[1], padded[2])
