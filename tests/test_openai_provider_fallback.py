from __future__ import annotations

from src.ai.providers.openai_provider import OpenAIProvider


class _DummyMessage:
    def __init__(self, content: str) -> None:
        self.content = content


class _DummyChoice:
    def __init__(self, content: str) -> None:
        self.message = _DummyMessage(content)


class _DummyResponse:
    def __init__(self, content: str) -> None:
        self.choices = [_DummyChoice(content)]


def test_generate_text_falls_back_when_primary_model_unavailable(monkeypatch):
    provider = OpenAIProvider(
        api_key="test-key",
        text_model="gpt-4o",
        fast_model="gpt-4o-mini",
        tts_model="gpt-4o-mini-tts",
        tts_voice="alloy",
    )

    class _ChatCompletions:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def create(self, **kwargs):
            self.calls.append(kwargs["model"])
            if kwargs["model"] == "gpt-4o":
                raise Exception(
                    "Error code: 403 - {'error': {'message': 'Project does not have access to model `gpt-4o`', 'type': 'invalid_request_error', 'code': 'model_not_found'}}"
                )
            return _DummyResponse("fallback worked")

    completions = _ChatCompletions()
    client = type(
        "_Client",
        (),
        {"chat": type("_Chat", (), {"completions": completions})()},
    )()
    monkeypatch.setattr(provider, "_client", lambda: client)

    result = provider.generate_text("hello world", model="gpt-4o")

    assert result == "fallback worked"
    assert completions.calls[:2] == ["gpt-4o", "gpt-4o-mini"]


def test_generate_text_raises_non_model_errors(monkeypatch):
    provider = OpenAIProvider(
        api_key="test-key",
        text_model="gpt-4o",
        fast_model="gpt-4o-mini",
        tts_model="gpt-4o-mini-tts",
        tts_voice="alloy",
    )

    class _ChatCompletions:
        def create(self, **kwargs):
            raise Exception("network exploded")

    client = type(
        "_Client",
        (),
        {"chat": type("_Chat", (), {"completions": _ChatCompletions()})()},
    )()
    monkeypatch.setattr(provider, "_client", lambda: client)

    try:
        provider.generate_text("hello world", model="gpt-4o")
    except Exception as exc:  # noqa: BLE001 - intentional for behavior check
        assert str(exc) == "network exploded"
    else:
        raise AssertionError("Expected exception was not raised")
