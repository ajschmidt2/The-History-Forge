from utils import generate_outline


REQUIRED_KEYS = {
    "hook",
    "context",
    "beats",
    "twist_or_insight",
    "modern_relevance",
    "cta",
}


def test_generate_outline_returns_required_schema() -> None:
    outline = generate_outline(
        topic="The Fall of Constantinople",
        research_brief="",
        tone="Documentary",
        length="8–10 minutes",
        audience="General audience",
        angle="Military innovation",
    )

    assert isinstance(outline, dict)
    assert REQUIRED_KEYS.issubset(outline.keys())
    assert isinstance(outline["beats"], list)
    assert len(outline["beats"]) >= 1

    first = outline["beats"][0]
    assert isinstance(first, dict)
    assert isinstance(first.get("title"), str)
    assert first.get("title")
    assert isinstance(first.get("bullets"), list)
    assert len(first["bullets"]) >= 1
