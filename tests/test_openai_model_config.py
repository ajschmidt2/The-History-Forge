import utils


def test_get_openai_text_model_ignores_api_key_like_value(monkeypatch):
    monkeypatch.setattr(utils, "_get_secret", lambda name, default="": "sk-proj-abc123")

    assert utils.get_openai_text_model(default="gpt-4o-mini") == "gpt-4o-mini"


def test_get_openai_text_model_accepts_regular_model_id(monkeypatch):
    monkeypatch.setattr(utils, "_get_secret", lambda name, default="": "gpt-4o-mini")

    assert utils.get_openai_text_model(default="gpt-4o-mini") == "gpt-4o-mini"


def test_get_openai_text_model_falls_back_from_unavailable_model(monkeypatch):
    monkeypatch.setattr(utils, "_get_secret", lambda name, default="": "gpt-4.1-mini")

    assert utils.get_openai_text_model(default="gpt-4o-mini") == "gpt-4o-mini"
