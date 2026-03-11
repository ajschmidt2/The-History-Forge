import src.config as config
import src.config.secrets as secrets


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


def test_get_secret_reads_broll_keys_from_nested_api_keys_section(monkeypatch):
    monkeypatch.setattr(
        secrets,
        "_safe_streamlit_secrets",
        lambda: {"api_keys": {"pexels_api_key": "pexels-nested", "pixabay": "pixabay-nested"}},
    )

    assert config.get_secret("PEXELS_API_KEY") == "pexels-nested"
    assert config.get_secret("PIXABAY_API_KEY") == "pixabay-nested"


def test_get_secret_reads_non_mapping_streamlit_secret_container(monkeypatch):
    class SecretNode:
        def __init__(self, data):
            self._data = data

        def __getitem__(self, key):
            value = self._data[key]
            if isinstance(value, dict):
                return SecretNode(value)
            return value

    monkeypatch.setattr(
        secrets,
        "_safe_streamlit_secrets",
        lambda: SecretNode({"broll": {"pixabay_api_key": "pixabay-from-secret-node"}}),
    )

    assert config.get_secret("PIXABAY_API_KEY") == "pixabay-from-secret-node"
