"""Tests del retriever híbrido: BM25, semántico (Voyage mockeado) y RRF."""

from __future__ import annotations

from typing import Any

import pytest
from src.rag.retriever import (
    BM25Retriever,
    Document,
    HybridRetriever,
    RetrievalResult,
    SemanticRetriever,
    reciprocal_rank_fusion,
    tokenize_es,
)

DOCS: list[Document] = [
    Document(doc_id="d1", text="Tarjeta de crédito Visa con cashback en supermercados"),
    Document(doc_id="d2", text="Crédito de consumo BCI con CAE bajo y aprobación rápida"),
    Document(doc_id="d3", text="Cuenta corriente sin mantención del Banco Estado"),
    Document(doc_id="d4", text="Hipotecario en UF para vivienda nueva, plazo 20 años"),
]


# ---------------------------------------------------------------------------
# tokenize_es
# ---------------------------------------------------------------------------


def test_tokenize_es_lowercases_and_folds_accents() -> None:
    assert tokenize_es("CRÉDITO Préstamo Ñoño") == ["credito", "prestamo", "nono"]


def test_tokenize_es_splits_on_punctuation() -> None:
    assert tokenize_es("hola, mundo! financiero.") == ["hola", "mundo", "financiero"]


def test_tokenize_es_empty() -> None:
    assert tokenize_es("   ") == []


# ---------------------------------------------------------------------------
# BM25Retriever
# ---------------------------------------------------------------------------


def test_bm25_finds_lexical_match_first() -> None:
    retriever = BM25Retriever(DOCS)
    hits = retriever.search("hipotecario UF", top_k=3)
    assert hits[0].doc_id == "d4"
    assert hits[0].rank == 1
    assert hits[0].method == "bm25"


def test_bm25_accent_folding_works() -> None:
    retriever = BM25Retriever(DOCS)
    # "aprobacion" sin tilde debe matchear "aprobación" — sólo aparece en d2,
    # así que un IDF positivo lo deja primero con score > 0.
    hits = retriever.search("aprobacion rapida", top_k=4)
    assert hits[0].doc_id == "d2"
    assert hits[0].score > 0


def test_bm25_empty_query_returns_empty() -> None:
    retriever = BM25Retriever(DOCS)
    assert retriever.search("", top_k=5) == []
    assert retriever.search("   ", top_k=5) == []


def test_bm25_requires_documents() -> None:
    with pytest.raises(ValueError, match="al menos un documento"):
        BM25Retriever([])


def test_bm25_top_k_validation() -> None:
    retriever = BM25Retriever(DOCS)
    with pytest.raises(ValueError, match="top_k"):
        retriever.search("crédito", top_k=0)


def test_bm25_ranks_are_contiguous() -> None:
    retriever = BM25Retriever(DOCS)
    hits = retriever.search("crédito tarjeta", top_k=4)
    assert [h.rank for h in hits] == list(range(1, len(hits) + 1))


# ---------------------------------------------------------------------------
# SemanticRetriever — Voyage mockeado
# ---------------------------------------------------------------------------


class _FakeEmbedResponse:
    """Imita ``EmbeddingsObject`` de voyageai exponiendo solo ``.embeddings``."""

    def __init__(self, embeddings: list[list[float]]) -> None:
        self.embeddings = embeddings


class _FakeVoyageClient:
    """Cliente fake determinístico — los embeddings se pre-arman por texto."""

    def __init__(self, mapping: dict[str, list[float]]) -> None:
        self._mapping = mapping
        self.embed_calls: list[tuple[tuple[str, ...], str | None, str | None]] = []

    def embed(
        self,
        texts: list[str],
        model: str | None = None,
        input_type: str | None = None,
    ) -> _FakeEmbedResponse:
        self.embed_calls.append((tuple(texts), model, input_type))
        try:
            embs = [self._mapping[t] for t in texts]
        except KeyError as e:
            raise AssertionError(f"texto sin embedding fake: {e!r}") from None
        return _FakeEmbedResponse(embs)


def _build_fake_client() -> _FakeVoyageClient:
    # Embeddings 3D ortogonales por dominio: tarjetas / créditos / cuentas / hipotecario
    return _FakeVoyageClient(
        {
            DOCS[0].text: [1.0, 0.0, 0.0],
            DOCS[1].text: [0.0, 1.0, 0.0],
            DOCS[2].text: [0.0, 0.0, 1.0],
            DOCS[3].text: [0.7, 0.0, 0.7],
            "tarjeta para pagar el supermercado": [1.0, 0.1, 0.0],
            "préstamo personal urgente": [0.0, 1.0, 0.1],
            "casa propia con crédito": [0.6, 0.1, 0.6],
        }
    )


def test_semantic_returns_closest_by_cosine() -> None:
    client = _build_fake_client()
    retriever = SemanticRetriever(DOCS, client=client, model="voyage-3-lite")
    hits = retriever.search("tarjeta para pagar el supermercado", top_k=2)
    assert hits[0].doc_id == "d1"
    assert hits[0].method == "semantic"
    assert 0.0 < hits[0].score <= 1.0


def test_semantic_uses_input_type_query_vs_document() -> None:
    client = _build_fake_client()
    SemanticRetriever(DOCS, client=client, model="voyage-3-lite").search(
        "préstamo personal urgente", top_k=1
    )
    # Primer call indexa documentos, segundo embeddea la query
    assert client.embed_calls[0][2] == "document"
    assert client.embed_calls[1][2] == "query"


def test_semantic_empty_query() -> None:
    client = _build_fake_client()
    retriever = SemanticRetriever(DOCS, client=client, model="voyage-3-lite")
    assert retriever.search("", top_k=3) == []


def test_semantic_requires_documents() -> None:
    client = _FakeVoyageClient({})
    with pytest.raises(ValueError, match="al menos un documento"):
        SemanticRetriever([], client=client)


# ---------------------------------------------------------------------------
# Reciprocal Rank Fusion
# ---------------------------------------------------------------------------


def _hits(method: str, *doc_ids: str) -> list[RetrievalResult]:
    return [
        RetrievalResult(doc_id=doc_id, score=1.0 / rank, rank=rank, method=method)  # type: ignore[arg-type]
        for rank, doc_id in enumerate(doc_ids, start=1)
    ]


def test_rrf_promotes_documents_in_both_rankings() -> None:
    bm25 = _hits("bm25", "a", "b", "c")
    semantic = _hits("semantic", "b", "a", "d")
    fused = reciprocal_rank_fusion([bm25, semantic], k=60, top_k=4)
    # 'a' (1+2) y 'b' (2+1) ambos suman lo mismo; ambos deben quedar arriba de 'c' y 'd'
    top_two = {fused[0].doc_id, fused[1].doc_id}
    assert top_two == {"a", "b"}
    bottom_two = {fused[2].doc_id, fused[3].doc_id}
    assert bottom_two == {"c", "d"}


def test_rrf_score_formula() -> None:
    bm25 = _hits("bm25", "a")
    semantic = _hits("semantic", "a")
    fused = reciprocal_rank_fusion([bm25, semantic], k=60, top_k=1)
    # 'a' aparece en rank=1 en ambos: 2 * 1/(60+1)
    assert fused[0].score == pytest.approx(2.0 / 61.0)
    assert fused[0].method == "hybrid"
    assert fused[0].rank == 1


def test_rrf_handles_disjoint_rankings() -> None:
    bm25 = _hits("bm25", "a", "b")
    semantic = _hits("semantic", "c", "d")
    fused = reciprocal_rank_fusion([bm25, semantic], k=60, top_k=4)
    # Todos aparecen una vez; los rank=1 de cada lista (a, c) ganan a los rank=2 (b, d)
    assert {fused[0].doc_id, fused[1].doc_id} == {"a", "c"}
    assert {fused[2].doc_id, fused[3].doc_id} == {"b", "d"}


def test_rrf_validates_args() -> None:
    with pytest.raises(ValueError, match="k de RRF"):
        reciprocal_rank_fusion([], k=0)
    with pytest.raises(ValueError, match="top_k"):
        reciprocal_rank_fusion([], k=60, top_k=0)


def test_rrf_rejects_duplicate_ranks_in_input() -> None:
    bad = [
        RetrievalResult(doc_id="a", score=0.5, rank=1, method="bm25"),
        RetrievalResult(doc_id="b", score=0.4, rank=1, method="bm25"),
    ]
    with pytest.raises(ValueError, match="duplica el rank"):
        reciprocal_rank_fusion([bad], k=60, top_k=2)


# ---------------------------------------------------------------------------
# HybridRetriever (integra BM25 + semántico mockeado)
# ---------------------------------------------------------------------------


def test_hybrid_uses_both_signals(mocker: Any) -> None:
    client = _build_fake_client()
    retriever = HybridRetriever(
        DOCS, embedding_client=client, embedding_model="voyage-3-lite", rrf_k=60
    )
    spy_bm25 = mocker.spy(retriever.bm25, "search")
    spy_sem = mocker.spy(retriever.semantic, "search")
    hits = retriever.search("tarjeta para pagar el supermercado", top_k=2, candidate_pool=4)
    assert spy_bm25.called
    assert spy_sem.called
    assert hits[0].method == "hybrid"
    assert hits[0].doc_id == "d1"


def test_hybrid_default_candidate_pool() -> None:
    client = _build_fake_client()
    retriever = HybridRetriever(DOCS, embedding_client=client)
    hits = retriever.search("casa propia con crédito", top_k=3)
    assert len(hits) <= 3
    assert all(h.method == "hybrid" for h in hits)
    assert [h.rank for h in hits] == list(range(1, len(hits) + 1))


def test_hybrid_top_k_validation() -> None:
    client = _build_fake_client()
    retriever = HybridRetriever(DOCS, embedding_client=client)
    with pytest.raises(ValueError, match="top_k"):
        retriever.search("crédito", top_k=0)
