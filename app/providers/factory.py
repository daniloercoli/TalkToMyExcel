from __future__ import annotations

import json
from pathlib import Path

from app.config import Config
from app.providers.local_embedding import LocalEmbedding
from app.providers.openai_compatible import OpenAICompatibleEmbedding, OpenAICompatibleLLM


class ProviderCatalog:
    def __init__(self, settings: dict | None = None):
        self.settings = settings or load_settings()
        self.defaults = json.loads(Path(Config.DEFAULT_PROVIDERS_FILE).read_text(encoding="utf-8"))

    def llm_providers(self) -> list[dict]:
        return [*self.defaults.get("llm", []), *self.settings.get("custom_llm_providers", [])]

    def embedding_providers(self) -> list[dict]:
        return [*self.defaults.get("embedding", []), *self.settings.get("custom_embedding_providers", [])]

    def selected_llm(self) -> tuple[dict, str]:
        provider_id = self.settings["chat"]["provider"]
        model = self.settings["chat"]["model"]
        provider = self._find(self.llm_providers(), provider_id)
        return provider, model or provider.get("default_model")

    def selected_embedding(self) -> tuple[dict, str]:
        provider_id = self.settings["embedding"]["provider"]
        model = self.settings["embedding"]["model"]
        provider = self._find(self.embedding_providers(), provider_id)
        return provider, model or provider.get("default_model")

    @staticmethod
    def _find(providers: list[dict], provider_id: str) -> dict:
        for provider in providers:
            if provider.get("id") == provider_id:
                return provider
        raise ValueError(f"Unknown provider: {provider_id}")


def default_settings() -> dict:
    return {
        "chat": {"provider": "regolo", "model": "gpt-oss-120b", "temperature": 0.2},
        "embedding": {"provider": "regolo", "model": "Qwen3-Embedding-8B"},
        "custom_llm_providers": [],
        "custom_embedding_providers": [],
    }


def load_settings() -> dict:
    if not Config.SETTINGS_FILE.exists():
        save_settings(default_settings())
    settings = json.loads(Config.SETTINGS_FILE.read_text(encoding="utf-8"))
    merged = default_settings()
    for key, value in settings.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def save_settings(settings: dict) -> None:
    Config.SETTINGS_FILE.parent.mkdir(parents=True, exist_ok=True)
    Config.SETTINGS_FILE.write_text(json.dumps(settings, indent=2, sort_keys=True), encoding="utf-8")


def get_llm_provider(settings: dict | None = None) -> tuple[OpenAICompatibleLLM, str]:
    catalog = ProviderCatalog(settings)
    provider, model = catalog.selected_llm()
    if provider.get("type") != "openai_compatible":
        raise ValueError("Only OpenAI-compatible chat providers are supported")
    return OpenAICompatibleLLM(provider), model


def get_embedding_provider(settings: dict | None = None):
    catalog = ProviderCatalog(settings)
    provider, model = catalog.selected_embedding()
    if provider.get("type") == "local":
        return LocalEmbedding(model), model
    return OpenAICompatibleEmbedding(provider, model), model
