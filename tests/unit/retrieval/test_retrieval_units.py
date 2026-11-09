import io
import json

import httpx
import pytest

from backend.app.core.config.settings import Settings
from backend.app.core.exceptions.exceptions import RetrievalError
from backend.app.knowledge.documents.parsers import UnsupportedDocumentError, extract_text
from backend.app.retrieval.chunking.chunker import chunk_text
from backend.app.retrieval.embeddings.embedders import (
    HashingEmbedder,
    HttpEmbedder,
    build_embedder,
    cosine,
)
from backend.app.retrieval.hybrid.ranking import bm25_scores, cap_per_group, reciprocal_rank_fusion
from backend.app.retrieval.reranking.reranker import HttpReranker

# ------------------------------------------------------------------ parsing


def test_markdown_and_text_are_normalized() -> None:
    assert extract_text("a.md", b"# Title\r\n\r\n\r\n\r\nBody   text\x00") == "# Title\n\nBody text"


def test_html_keeps_headings_and_drops_scripts() -> None:
    html = b"<html><head><style>x{}</style></head><body><h2>Scope</h2><p>Hello</p><script>evil()</script></body></html>"

    text = extract_text("page.html", html)

    assert "## Scope" in text and "Hello" in text
    assert "evil" not in text and "x{}" not in text


def test_docx_headings_become_markdown() -> None:
    from docx import Document

    doc = Document()
    doc.add_heading("Runbook", level=1)
    doc.add_paragraph("Restart the worker.")
    buffer = io.BytesIO()
    doc.save(buffer)

    text = extract_text("runbook.docx", buffer.getvalue())

    assert text.startswith("# Runbook")
    assert "Restart the worker." in text


@pytest.mark.parametrize(
    ("filename", "data", "match"),
    [
        ("virus.exe", b"MZ", "Unsupported file type"),
        ("empty.txt", b"   \n  ", "No text could be extracted"),
        ("broken.pdf", b"not a pdf", "could not be read"),
        ("broken.docx", b"not a zip", "could not be read"),
    ],
)
def test_bad_files_are_rejected(filename, data, match) -> None:
    with pytest.raises(UnsupportedDocumentError, match=match):
        extract_text(filename, data)


# ------------------------------------------------------------------ chunking


def test_chunks_follow_heading_paths_and_size_limit() -> None:
    body = " ".join(f"Sentence {i} about paging the on-call engineer." for i in range(60))
    text = f"# Policy\nIntro.\n\n## Escalation\n{body}\n\n## Contacts\nCall the lead."

    chunks = chunk_text(text, size=300, overlap=50)

    assert chunks[0].heading == "Policy"
    assert {c.heading for c in chunks} == {"Policy", "Policy > Escalation", "Policy > Contacts"}
    assert all(len(c.text) <= 300 for c in chunks)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))


def test_consecutive_chunks_overlap() -> None:
    text = " ".join(f"word{i}" for i in range(400))

    chunks = chunk_text(text, size=200, overlap=40)

    assert len(chunks) > 2
    tail_words = chunks[0].text.split()[-3:]
    assert all(word in chunks[1].text for word in tail_words)


def test_overlap_must_be_smaller_than_size() -> None:
    with pytest.raises(ValueError):
        chunk_text("x", size=100, overlap=100)


# ------------------------------------------------------------------ ranking


def test_bm25_ranks_matching_documents_first() -> None:
    docs = [
        "vendor timeout escalation",
        "lunch menu",
        "escalation of vendor incidents within one day",
    ]

    scores = bm25_scores("vendor escalation", docs)

    assert scores[1] == 0
    assert scores[0] > 0 and scores[2] > 0


def test_reciprocal_rank_fusion_rewards_agreement() -> None:
    fused = reciprocal_rank_fusion([["a", "b", "c"], ["b", "c", "d"]])

    assert [key for key, _ in fused][:2] == ["b", "c"]


def test_cap_per_group_keeps_order() -> None:
    assert cap_per_group(
        ["a1", "a2", "b1", "a3"], {"a1": "A", "a2": "A", "b1": "B", "a3": "A"}, 2
    ) == [
        "a1",
        "a2",
        "b1",
    ]


# ------------------------------------------------------------------ embedders / reranker


async def test_hashing_embedder_is_deterministic_and_lexical() -> None:
    embedder = HashingEmbedder(256)
    a, b, c = await embedder.embed(
        ["vendor gateway timeouts", "timeouts at the vendor gateway", "holiday calendar"]
    )

    assert a == (await embedder.embed(["vendor gateway timeouts"]))[0]
    assert abs(sum(v * v for v in a) - 1) < 1e-9
    assert cosine(a, b) > cosine(a, c)
    assert embedder.model == "hashing-256"


@pytest.mark.parametrize(
    ("provider", "path", "response"),
    [
        ("ollama", "/api/embed", {"embeddings": [[3.0, 4.0], [1.0, 0.0]]}),
        ("openai_compatible", "/v1/embeddings",
         {"data": [{"index": 1, "embedding": [1.0, 0.0]}, {"index": 0, "embedding": [3.0, 4.0]}]}),
    ],
)  # fmt: skip
async def test_http_embedder_providers(provider, path, response) -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=response)

    embedder = HttpEmbedder(
        provider=provider, base_url="http://emb", model="bge-m3", timeout=5,
        transport=httpx.MockTransport(handler),
    )  # fmt: skip
    vectors = await embedder.embed(["a", "b"])

    assert seen["path"] == path
    assert seen["body"] == {"model": "bge-m3", "input": ["a", "b"]}
    assert vectors == [[0.6, 0.8], [1.0, 0.0]]  # normalized, in input order


async def test_http_embedder_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"embeddings": [[1.0]]})  # one vector for two texts

    embedder = HttpEmbedder(
        provider="ollama",
        base_url="http://emb",
        model="m",
        timeout=5,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(RetrievalError, match="wrong number"):
        await embedder.embed(["a", "b"])


def test_build_embedder_modes() -> None:
    def make(**kw) -> Settings:
        return Settings(_env_file=None, **kw)

    assert isinstance(build_embedder(make(embedding_model_name="")), HashingEmbedder)
    assert build_embedder(make(embedding_provider="none")) is None
    ollama = build_embedder(
        make(embedding_model_name="nomic-embed-text", llm_base_url="http://o:11434")
    )
    assert isinstance(ollama, HttpEmbedder) and ollama.provider == "ollama"
    with pytest.raises(ValueError, match="EMBEDDING_MODEL_NAME"):
        build_embedder(make(embedding_provider="openai_compatible", embedding_model_name=""))


async def test_reranker_orders_and_degrades_gracefully() -> None:
    good = HttpReranker(
        "http://tei",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200, json=[{"index": 1, "score": 0.9}, {"index": 0, "score": 0.1}]
            )
        ),
    )
    down = HttpReranker("http://tei", transport=httpx.MockTransport(lambda r: httpx.Response(503)))

    assert await good.rerank("q", ["a", "b"]) == [1, 0]
    assert await down.rerank("q", ["a", "b"]) is None
