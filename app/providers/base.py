from __future__ import annotations

from abc import ABC, abstractmethod


class LLMProvider(ABC):
    @abstractmethod
    def generate(
        self,
        system: str,
        user: str,
        model: str,
        temperature: float = 0.2,
        messages: list[dict] | None = None,
    ) -> str:
        raise NotImplementedError


class EmbeddingProvider(ABC):
    @abstractmethod
    def encode_documents(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError

    @abstractmethod
    def encode_query(self, text: str) -> list[float]:
        raise NotImplementedError
