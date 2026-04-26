from __future__ import annotations

import json
import logging

import requests

_LOG = logging.getLogger(__name__)

_OLLAMA_CHAT_TIMEOUT = 180
_OLLAMA_EMBED_TIMEOUT = 60


class OllamaUnavailableError(RuntimeError):
    """Raised when the Ollama server cannot be reached."""


class OllamaProvider:
    def __init__(self, base_url: str, text_model: str, json_model: str, embed_model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.text_model = text_model
        self.json_model = json_model
        self.embed_model = embed_model

    def chat(self, prompt: str, *, system: str | None = None, model: str | None = None) -> str:
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": model or self.text_model,
            "messages": messages,
            "stream": False,
        }
        try:
            r = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=_OLLAMA_CHAT_TIMEOUT,
            )
            r.raise_for_status()
        except requests.exceptions.ConnectionError as exc:
            raise OllamaUnavailableError(f"Cannot reach Ollama at {self.base_url}: {exc}") from exc
        except requests.exceptions.Timeout as exc:
            raise OllamaUnavailableError(f"Ollama request timed out after {_OLLAMA_CHAT_TIMEOUT}s: {exc}") from exc

        data = r.json()
        content = data.get("message", {}).get("content", "")
        return str(content).strip()

    def structured(self, prompt: str, *, schema: dict | None = None, system: str | None = None, model: str | None = None) -> str:
        """Return a JSON string. Pass schema for structured-output mode, or omit for plain json format."""
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        payload: dict = {
            "model": model or self.json_model,
            "messages": messages,
            "stream": False,
            "format": schema if schema else "json",
        }
        try:
            r = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=_OLLAMA_CHAT_TIMEOUT,
            )
            r.raise_for_status()
        except requests.exceptions.ConnectionError as exc:
            raise OllamaUnavailableError(f"Cannot reach Ollama at {self.base_url}: {exc}") from exc
        except requests.exceptions.Timeout as exc:
            raise OllamaUnavailableError(f"Ollama structured request timed out: {exc}") from exc

        data = r.json()
        content = data.get("message", {}).get("content", "")
        return str(content).strip()

    def embeddings(self, texts: list[str], *, model: str | None = None) -> list[list[float]]:
        payload = {
            "model": model or self.embed_model,
            "input": texts,
        }
        try:
            r = requests.post(
                f"{self.base_url}/api/embed",
                json=payload,
                timeout=_OLLAMA_EMBED_TIMEOUT,
            )
            r.raise_for_status()
        except requests.exceptions.ConnectionError as exc:
            raise OllamaUnavailableError(f"Cannot reach Ollama at {self.base_url}: {exc}") from exc

        data = r.json()
        return data.get("embeddings", [])

    def is_available(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/api/tags", timeout=5)
            return r.status_code == 200
        except Exception:
            return False
