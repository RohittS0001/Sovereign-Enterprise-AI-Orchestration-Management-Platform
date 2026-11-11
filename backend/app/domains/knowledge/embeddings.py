"""Text embedders: local model servers, plus an offline hashing embedder."""

import hashlib
import math
import re
from typing import Protocol

import httpx

from backend.app.core.config import Settings
from backend.app.core.errors import RetrievalError

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


class Embedder(Protocol):
    model: str  # stored with every vector; only same-model vectors are compared

    async def embed(self, texts: list[str]) -> list[list[float]]: ...

    async def close(self) -> None: ...


def _normalize(vector: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector)) or 1.0
    return [v / norm for v in vector]


class HashingEmbedder:
    """Feature-hashed unigrams + bigrams. No model needed; captures lexical overlap only.

    Good enough for tests, demos and air-gapped trials; use a real embedding model
    (e.g. ``nomic-embed-text`` or ``bge-m3`` on Ollama) for semantic search.
    """

    def __init__(self, dimensions: int = 384) -> None:
        self.dimensions = dimensions
        self.model = f"hashing-{dimensions}"

    def _vector(self, text: str) -> list[float]:
        vector = [0.0] * self.dimensions
        tokens = tokenize(text)
        for feature in tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:], strict=False)]:
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "little") % self.dimensions
            vector[index] += 1.0 if digest[4] & 1 else -1.0
        return _normalize(vector)

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    async def close(self) -> None:
        return None


class HttpEmbedder:
    """Ollama ``/api/embed`` or OpenAI-compatible ``/v1/embeddings``."""

    def __init__(
        self,
        *,
        provider: str,
        base_url: str,
        model: str,
        timeout: float,
        api_key: str = "",
        batch_size: int = 32,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.provider = provider
        self.model = model
        self.batch_size = batch_size
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"), timeout=timeout, headers=headers, transport=transport
        )

    async def _batch(self, texts: list[str]) -> list[list[float]]:
        if self.provider == "ollama":
            path, payload = "/api/embed", {"model": self.model, "input": texts}
        else:
            path, payload = "/v1/embeddings", {"model": self.model, "input": texts}
        try:
            response = await self._client.post(path, json=payload)
        except httpx.RequestError as exc:
            raise RetrievalError(f"Embedding server unreachable: {exc.__class__.__name__}") from exc
        if response.is_error:
            raise RetrievalError(
                f"Embedding server returned {response.status_code}: {response.text[:200]}"
            )
        body = response.json()
        try:
            if self.provider == "ollama":
                vectors = body["embeddings"]
            else:
                vectors = [
                    item["embedding"] for item in sorted(body["data"], key=lambda d: d["index"])
                ]
        except (KeyError, TypeError) as exc:
            raise RetrievalError("Embedding server returned an unexpected payload") from exc
        if len(vectors) != len(texts):
            raise RetrievalError("Embedding server returned the wrong number of vectors")
        return [_normalize([float(v) for v in vector]) for vector in vectors]

    async def embed(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            vectors.extend(await self._batch(texts[start : start + self.batch_size]))
        return vectors

    async def close(self) -> None:
        await self._client.aclose()


class RedactingEmbedder:
    """Redacts secrets and personal data (the guardrails used for model observations)
    before texts leave for an embedding server. Stored chunk text is unchanged; only what
    is transmitted, and therefore embedded, is redacted."""

    def __init__(self, inner: Embedder, *, include_contact_info: bool = False) -> None:
        self.inner = inner
        self.include_contact_info = include_contact_info

    async def embed(self, texts: list[str]) -> list[list[float]]:
        from backend.app.domains.governance.guardrails.redaction import redact_text

        cleaned = [
            redact_text(t, include_contact_info=self.include_contact_info).text for t in texts
        ]
        return await self.inner.embed(cleaned)

    async def close(self) -> None:
        await self.inner.close()


def build_embedder(settings: Settings) -> Embedder | None:
    provider = settings.embedding_provider
    if provider == "none":
        return None
    if provider == "auto":
        if not settings.embedding_model_name:
            return HashingEmbedder(settings.embedding_dimensions)
        provider = settings.llm_provider
    if provider == "hashing":
        return HashingEmbedder(settings.embedding_dimensions)
    if not settings.embedding_model_name:
        raise ValueError("EMBEDDING_MODEL_NAME is required for this embedding provider")
    embedder: Embedder = HttpEmbedder(
        provider=provider,
        base_url=settings.embedding_base_url or settings.llm_base_url,
        model=settings.embedding_model_name,
        timeout=settings.llm_timeout_seconds,
        api_key=settings.llm_api_key.get_secret_value(),
    )
    if settings.embedding_redacts:  # documents and queries are both embedded through here
        embedder = RedactingEmbedder(
            embedder, include_contact_info=settings.guardrails_redact_contact_info
        )
    return embedder


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two already-normalized vectors."""
    return sum(x * y for x, y in zip(a, b, strict=False))
