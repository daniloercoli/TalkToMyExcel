from __future__ import annotations

import os

from .base import EmbeddingProvider, LLMProvider


def base_url(config: dict) -> str:
    return str(config.get("base_url") or "").rstrip("/")


def api_key(config: dict) -> str:
    explicit = str(config.get("api_key") or "").strip()
    if explicit:
        return explicit
    env_name = str(config.get("api_key_env") or "").strip()
    return os.getenv(env_name, "") if env_name else ""


def client_key(config: dict) -> str:
    return api_key(config) or "openai-compatible"


class OpenAICompatibleLLM(LLMProvider):
    def __init__(self, config: dict):
        if not base_url(config):
            raise ValueError("Provider base_url is required")
        if config.get("requires_api_key", True) and not api_key(config):
            raise ValueError(f"Missing API key for {config.get('name', 'provider')}")
        from openai import OpenAI

        self.config = config
        self.client = OpenAI(api_key=client_key(config), base_url=base_url(config))

    def generate(self, system: str, user: str, model: str, temperature: float = 0.2) -> str:
        response = self.client.chat.completions.create(
            model=model,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        return response.choices[0].message.content or ""


class OpenAICompatibleEmbedding(EmbeddingProvider):
    def __init__(self, config: dict, model: str):
        if not base_url(config):
            raise ValueError("Embedding provider base_url is required")
        if config.get("requires_api_key", True) and not api_key(config):
            raise ValueError(f"Missing API key for {config.get('name', 'embedding provider')}")
        from openai import OpenAI

        self.model = model
        self.client = OpenAI(api_key=client_key(config), base_url=base_url(config))

    def encode_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        response = self.client.embeddings.create(model=self.model, input=texts)
        return [item.embedding for item in response.data]

    def encode_query(self, text: str) -> list[float]:
        response = self.client.embeddings.create(model=self.model, input=text)
        return response.data[0].embedding
