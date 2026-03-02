import src.config as config


def test_resolve_openai_key_reads_openai_api_key_env(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("openai_api_key", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-proj-from-openai-api-key")

    assert config.resolve_openai_key() == "sk-proj-from-openai-api-key"


def test_resolve_openai_key_reads_lowercase_env(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("openai_api_key", raising=False)
    monkeypatch.setenv("openai_api_key", "sk-proj-from-lowercase-key")

    assert config.resolve_openai_key() == "sk-proj-from-lowercase-key"


def test_get_secret_no_longer_uses_unscoped_aliases(monkeypatch):
    monkeypatch.delenv("openai_api_key", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_KEY", "sk-proj-from-openai-key")
    monkeypatch.setenv("api_key", "sk-proj-from-generic-alias")

    assert config.get_secret("openai_api_key", "") == ""
