from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

from openai import AsyncOpenAI

from .config import Settings


class EmbeddingProvider(Protocol):
    async def embed(self, texts: list[str]) -> list[list[float]]: ...


def canonical_entity(text: str) -> str:
    tokens = re.findall(r"[a-z0-9]+", text.lower().replace("_", " "))
    aliases = {"rollout": "launch", "release": "launch"}
    # Role/suffix words do not establish subject identity. In particular,
    # sharing only "status" must not merge project health with deployment state.
    ignored = {"date", "project", "the", "status", "owner", "risk", "deadline", "target"}
    return " ".join(sorted({aliases.get(token, token) for token in tokens if token not in ignored}))


def entities_equivalent(left: str, right: str) -> bool:
    if left.strip().lower() == right.strip().lower():
        return True
    a, b = set(canonical_entity(left).split()), set(canonical_entity(right).split())
    return bool(a and b and (a == b or len(a & b) / min(len(a), len(b)) >= 0.75))


class LocalEmbeddingProvider:
    def __init__(self, dimensions: int = 64):
        self.dimensions = dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._one(text) for text in texts]

    def _one(self, text: str) -> list[float]:
        normalized = canonical_entity(text)
        features = normalized.split() + [normalized[i:i + 3] for i in range(max(0, len(normalized) - 2))]
        vector = [0.0] * self.dimensions
        for feature in features:
            digest = hashlib.sha256(feature.encode()).digest()
            vector[int.from_bytes(digest[:2], "big") % self.dimensions] += 1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]


class OpenAIEmbeddingProvider:
    def __init__(self, settings: Settings):
        client_options = {
            "api_key": settings.openai_api_key,
            "max_retries": settings.openai_max_retries,
            "timeout": settings.openai_timeout_seconds,
        }
        client_options["base_url"] = settings.openai_base_url or "https://api.openai.com/v1"
        self.client = AsyncOpenAI(**client_options)
        self.model = settings.openai_embedding_model
        self.dimensions = settings.embedding_dimensions

    async def embed(self, texts: list[str]) -> list[list[float]]:
        response = await self.client.embeddings.create(
            model=self.model, input=texts, dimensions=self.dimensions
        )
        return [item.embedding for item in response.data]


def build_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "openai":
        if not settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required for the OpenAI embedding provider")
        return OpenAIEmbeddingProvider(settings)
    if settings.embedding_provider != "local":
        raise ValueError("EMBEDDING_PROVIDER must be 'local' or 'openai'")
    return LocalEmbeddingProvider(settings.embedding_dimensions)
