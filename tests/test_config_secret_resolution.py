import src.config as config


def test_get_secret_resolves_openai_key_alias_from_env(monkeypatch):
    monkeypatch.delenv("openai_api_key", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_KEY", "sk-proj-from-openai-key")

    assert config.get_secret("openai_api_key", "") == "sk-proj-from-openai-key"


def test_get_secret_resolves_generic_api_key_alias_from_env(monkeypatch):
    monkeypatch.delenv("openai_api_key", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("api_key", "sk-proj-from-generic-alias")

    assert config.get_secret("openai_api_key", "") == "sk-proj-from-generic-alias"
