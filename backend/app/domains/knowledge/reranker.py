"""Optional cross-encoder reranking via a local text-embeddings-inference server.

TEI (e.g. serving ``BAAI/bge-reranker-v2-m3``) exposes ``POST /rerank`` with
``{"query", "texts"}`` and returns ``[{"index", "score"}]``. Failures never break a
search: the fused ranking is kept.
"""

import httpx

from backend.app.core.logging import get_logger

logger = get_logger(__name__)


class HttpReranker:
    def __init__(
        self, url: str, *, timeout: float = 15.0, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self._client = httpx.AsyncClient(
            base_url=url.rstrip("/"), timeout=timeout, transport=transport
        )

    async def rerank(self, query: str, texts: list[str]) -> list[int] | None:
        """Return text indexes best-first, or None if the reranker is unavailable."""
        try:
            response = await self._client.post(
                "/rerank", json={"query": query, "texts": texts, "truncate": True}
            )
            response.raise_for_status()
            ranked = sorted(response.json(), key=lambda r: r["score"], reverse=True)
            return [int(r["index"]) for r in ranked if 0 <= int(r["index"]) < len(texts)]
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            logger.warning("Reranker unavailable; keeping fused ranking", exc_info=True)
            return None

    async def close(self) -> None:
        await self._client.aclose()
