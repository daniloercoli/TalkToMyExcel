from __future__ import annotations

from .base import EmbeddingProvider


class LocalEmbedding(EmbeddingProvider):
    def __init__(self, model: str):
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model, device="cpu")

    def encode_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self.model.encode(texts, normalize_embeddings=True, show_progress_bar=False).tolist()

    def encode_query(self, text: str) -> list[float]:
        return self.model.encode(text, normalize_embeddings=True, show_progress_bar=False).tolist()
