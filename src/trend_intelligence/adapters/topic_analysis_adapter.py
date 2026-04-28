from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from src.config import get_secret
from src.trend_intelligence.adapters.interfaces import TopicAnalysisAdapter
from src.trend_intelligence.adapters.schemas import TopicAnalysis, VideoResult

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class TopicAnalysisPrompt:
    system: str
    user: str


class DeterministicTopicAnalysisAdapter(TopicAnalysisAdapter):
    """Stable fallback adapter used when no provider is configured or provider calls fail."""

    source_name = "deterministic_topic_analysis"

    def analyze_topic(
        self,
        topic: str,
        videos: list[VideoResult],
        *,
        brand_focus: str = "all",
        content_type: str = "both",
    ) -> TopicAnalysis:
        video_count = len(videos)
        top_channel = videos[0].channel_title if videos else "N/A"
        avg_views = int(sum(video.view_count for video in videos) / video_count) if video_count else 0
        angle_mode = "documentary deep dive" if content_type in {"long-form", "both"} else "high-retention short"
        topic_lower = topic.lower()
        if any(term in topic_lower for term in ("mystery", "disappearance", "secret", "what happened", "lost")):
            angles = (
                f"The strongest evidence in the {topic} mystery",
                f"What most retellings of {topic} leave out",
                f"The most credible explanation for {topic}",
            )
            hooks = (
                f"This history mystery still doesn't sit right.",
                f"The evidence behind {topic} is stranger than the legend.",
                f"Most people know the myth, not what likely happened.",
            )
            thumbs = (
                "Key figure or location + a single unsettling clue",
                "Before/after evidence board layout with one bold focal object",
                "Historic photo or map detail with one red circle highlight",
            )
        elif any(term in topic_lower for term in ("forgotten", "hero", "general", "woman", "who was", "life of")):
            angles = (
                f"Why {topic} deserves a second look",
                f"The decision that made {topic} historically important",
                f"How later retellings pushed {topic} into the background",
            )
            hooks = (
                f"History almost erased this person.",
                f"You probably know the event but not the person who shaped it.",
                f"This overlooked figure changed more than most textbooks admit.",
            )
            thumbs = (
                "Single portrait + one bold emotional word",
                "Known famous figure contrasted with the overlooked subject",
                "Historic portrait with strong date or battlefield/map cue",
            )
        else:
            angles = (
                f"What most people miss about {topic}",
                f"The chain reaction that made {topic} inevitable",
                f"Why {topic} still matters more than it seems",
            )
            hooks = (
                f"The real story of {topic} is more dramatic than the headline.",
                f"One decision changed the course of {topic}.",
                f"The part of {topic} that keeps people watching is rarely the beginning.",
            )
            thumbs = (
                "Central figure or location + conflict-driven contrast",
                "Map or artifact close-up with one sharp focal detail",
                "Before/after visual framing that implies consequence",
            )

        return TopicAnalysis(
            topic=topic,
            explanation=(
                f"{topic} is trending due to sustained discovery signals and {video_count} related uploads. "
                f"Observed top-channel pattern: {top_channel}. "
                f"Average sampled views: {avg_views:,}. Recommended packaging bias: {angle_mode}."
            ),
            angles=angles,
            hooks=hooks,
            thumbnail_ideas=thumbs,
            source=self.source_name,
        )


class OpenAITopicAnalysisAdapter(TopicAnalysisAdapter):
    """Provider-backed analysis adapter with deterministic fallback.

    Routes JSON extraction through the provider router (Ollama by default,
    OpenAI on fallback) rather than calling OpenAI directly.
    """

    source_name = "openai_topic_analysis"

    def __init__(
        self,
        *,
        fallback: TopicAnalysisAdapter | None = None,
        model: str | None = None,
    ) -> None:
        self.fallback = fallback or DeterministicTopicAnalysisAdapter()
        self._model_override = model
        self._api_key = str(get_secret("OPENAI_API_KEY", "") or "")
        self._enabled = str(get_secret("TREND_INTELLIGENCE_OPENAI_ENABLED", "") or "").strip().lower() in {"1", "true", "yes", "on"}

    def analyze_topic(
        self,
        topic: str,
        videos: list[VideoResult],
        *,
        brand_focus: str = "all",
        content_type: str = "both",
    ) -> TopicAnalysis:
        if not self._enabled or not str(getattr(self, "_api_key", "") or "").strip():
            return self.fallback.analyze_topic(topic, videos, brand_focus=brand_focus, content_type=content_type)
        try:
            from src.ai.provider_router import get_router
            prompt = _build_prompt(topic, videos, brand_focus=brand_focus, content_type=content_type)
            raw = get_router().generate_structured(prompt.user, system=prompt.system, task_type="json")
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
            return self.fallback.analyze_topic(topic, videos, brand_focus=brand_focus, content_type=content_type)

def _build_prompt(
    topic: str,
    videos: list[VideoResult],
    *,
    brand_focus: str = "all",
    content_type: str = "both",
) -> TopicAnalysisPrompt:
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
    format_instruction = {
        "shorts": "Optimize for YouTube Shorts: immediate hook, 45-70 second payoff, one core reveal.",
        "long-form": "Optimize for long-form YouTube documentaries: curiosity gap opening, sustained structure, retention through escalating reveals.",
        "both": "Optimize for YouTube first and propose ideas that can stretch into either a documentary or a short companion cut.",
    }.get(content_type, "Optimize for YouTube history storytelling.")

    system = (
        "You are a trend intelligence strategist for a cinematic history YouTube channel called History Crossroads. "
        "Return strict JSON only and never include markdown."
    )
    user = (
        "Analyze why this history topic may be trending and produce creator-ready ideation.\n"
        f"Topic: {topic}\n"
        f"Creator focus: {focus_instruction}\n"
        f"Packaging mode: {format_instruction}\n"
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
        "- Favor hooks and thumbnail directions that suit YouTube history audiences: mystery, reversal, forgotten figure, stakes, consequence, evidence, or hidden cause.\n"
        "- Avoid bland school-report phrasing.\n"
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
