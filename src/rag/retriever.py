"""Retrieval híbrido sobre el catálogo de productos: BM25 + embeddings con RRF.

El retriever expone tres capas — léxico (``BM25Retriever``), semántico
(``SemanticRetriever``) y la fusión (``HybridRetriever``) — para que las evals
puedan benchmarkear cada una por separado contra la versión combinada.

La fusión usa Reciprocal Rank Fusion (Cormack et al., 2009): en vez de mezclar
puntajes en escalas distintas, se suma ``1/(k + rank)`` por documento — es
robusto a la calibración de cada retriever y es el baseline estándar.
"""

from __future__ import annotations

import logging
import math
import re
import unicodedata
from collections.abc import Sequence
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field
from rank_bm25 import BM25Okapi

logger = logging.getLogger(__name__)

DEFAULT_EMBED_MODEL = "gemini-embedding-001"
DEFAULT_TOP_K = 10
DEFAULT_RRF_K = 60
"""Constante ``k`` de RRF. 60 es el valor canónico de Cormack et al. (2009)."""


RetrievalMethod = Literal["bm25", "semantic", "hybrid"]


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class Document(BaseModel):
    """Documento indexable. ``text`` es el contenido sobre el que se hace match."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    doc_id: str = Field(..., min_length=1, description="Id estable usado al rankear.")
    text: str = Field(..., min_length=1)
    metadata: dict[str, str] = Field(default_factory=dict)


class RetrievalResult(BaseModel):
    """Hit individual de un retriever, con el método que lo produjo."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    doc_id: str = Field(..., min_length=1)
    score: float = Field(..., description="Score crudo del retriever; escala depende del método.")
    rank: int = Field(..., ge=1, description="Posición 1-indexada en el ranking.")
    method: RetrievalMethod


# ---------------------------------------------------------------------------
# Tokenización ES (BM25)
# ---------------------------------------------------------------------------


_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def tokenize_es(text: str) -> list[str]:
    """Tokeniza para BM25 en español: lowercase, plega tildes y separa por no-palabra.

    El plegado de tildes evita que ``crédito`` y ``credito`` se traten como
    términos distintos — los usuarios suelen omitir tildes al escribir queries.
    """
    nfkd = unicodedata.normalize("NFKD", text.lower())
    folded = "".join(ch for ch in nfkd if not unicodedata.combining(ch))
    return _TOKEN_RE.findall(folded)


# ---------------------------------------------------------------------------
# Embeddings
# ---------------------------------------------------------------------------


class EmbeddingClient(Protocol):
    """Subset minimo del cliente de embeddings que usa el retriever."""

    def embed(
        self,
        texts: list[str],
        model: str | None = None,
        input_type: str | None = None,
    ) -> Any: ...


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        raise ValueError(f"dim mismatch: {len(a)} vs {len(b)}")
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


# ---------------------------------------------------------------------------
# Retrievers
# ---------------------------------------------------------------------------


class BM25Retriever:
    """Retriever léxico sobre los textos de los documentos.

    Usa ``BM25Okapi`` con la tokenización ES por defecto. Los documentos se
    indexan en el constructor; reindexar requiere instanciar de nuevo.
    """

    def __init__(self, documents: Sequence[Document]) -> None:
        if not documents:
            raise ValueError("BM25Retriever necesita al menos un documento")
        self._documents: list[Document] = list(documents)
        tokenized = [tokenize_es(d.text) for d in self._documents]
        self._index = BM25Okapi(tokenized)

    @property
    def documents(self) -> list[Document]:
        """Documentos indexados, en el orden de inserción."""
        return list(self._documents)

    def search(self, query: str, top_k: int = DEFAULT_TOP_K) -> list[RetrievalResult]:
        """Devuelve los ``top_k`` documentos más relevantes para ``query``."""
        if top_k < 1:
            raise ValueError(f"top_k debe ser >= 1, recibí {top_k}")
        tokens = tokenize_es(query)
        if not tokens:
            return []
        scores = self._index.get_scores(tokens)
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return [
            RetrievalResult(
                doc_id=self._documents[i].doc_id,
                score=max(0.0, float(scores[i])),
                rank=rank,
                method="bm25",
            )
            for rank, i in enumerate(order[:top_k], start=1)
        ]


class SemanticRetriever:
    """Retriever denso usando embeddings externos.

    Embeddea el corpus al construir y mantiene la matriz en memoria — el target
    son ~10³ productos del catálogo, no escala a millones.
    """

    def __init__(
        self,
        documents: Sequence[Document],
        *,
        client: EmbeddingClient,
        model: str = DEFAULT_EMBED_MODEL,
    ) -> None:
        if not documents:
            raise ValueError("SemanticRetriever necesita al menos un documento")
        self._documents: list[Document] = list(documents)
        self._model = model
        self._client: EmbeddingClient = client
        response = self._client.embed(
            [d.text for d in self._documents],
            model=model,
            input_type="document",
        )
        self._embeddings: list[list[float]] = [list(map(float, e)) for e in response.embeddings]
        if len(self._embeddings) != len(self._documents):
            raise RuntimeError(
                f"El proveedor de embeddings devolvio {len(self._embeddings)} embeddings para "
                f"{len(self._documents)} documentos"
            )

    @property
    def documents(self) -> list[Document]:
        """Documentos indexados, en el orden de inserción."""
        return list(self._documents)

    def search(self, query: str, top_k: int = DEFAULT_TOP_K) -> list[RetrievalResult]:
        """Devuelve los ``top_k`` documentos más cercanos en coseno a ``query``."""
        if top_k < 1:
            raise ValueError(f"top_k debe ser >= 1, recibí {top_k}")
        if not query.strip():
            return []
        response = self._client.embed([query], model=self._model, input_type="query")
        q_emb = list(response.embeddings[0])
        scored = [(i, _cosine(q_emb, e)) for i, e in enumerate(self._embeddings)]
        scored.sort(key=lambda t: t[1], reverse=True)
        return [
            RetrievalResult(
                doc_id=self._documents[i].doc_id,
                score=float(score),
                rank=rank,
                method="semantic",
            )
            for rank, (i, score) in enumerate(scored[:top_k], start=1)
        ]


# ---------------------------------------------------------------------------
# Reciprocal Rank Fusion
# ---------------------------------------------------------------------------


def reciprocal_rank_fusion(
    rankings: Sequence[Sequence[RetrievalResult]],
    *,
    k: int = DEFAULT_RRF_K,
    top_k: int = DEFAULT_TOP_K,
) -> list[RetrievalResult]:
    """Fusiona varios rankings vía Reciprocal Rank Fusion.

    Score fusionado por documento: ``sum(1 / (k + rank_in_ranking))`` sobre todos
    los rankings donde aparece. Insensible a la escala de los scores originales,
    por eso es robusto al combinar BM25 (no acotado) con coseno (en [-1, 1]).
    """
    if k < 1:
        raise ValueError(f"k de RRF debe ser >= 1, recibí {k}")
    if top_k < 1:
        raise ValueError(f"top_k debe ser >= 1, recibí {top_k}")

    fused: dict[str, float] = {}
    for ranking in rankings:
        seen_ranks: set[int] = set()
        for hit in ranking:
            if hit.rank in seen_ranks:
                raise ValueError(f"ranking duplica el rank {hit.rank}")
            seen_ranks.add(hit.rank)
            fused[hit.doc_id] = fused.get(hit.doc_id, 0.0) + 1.0 / (k + hit.rank)

    ordered = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
    return [
        RetrievalResult(doc_id=doc_id, score=score, rank=rank, method="hybrid")
        for rank, (doc_id, score) in enumerate(ordered[:top_k], start=1)
    ]


class HybridRetriever:
    """Combina BM25 y semántico vía RRF, exponiendo una API ``search`` única.

    ``candidate_pool`` controla cuántos hits pide a cada retriever antes de
    fusionar — un pool más grande mejora recall del híbrido a costo de latencia
    semántica (BM25 es prácticamente gratis).
    """

    def __init__(
        self,
        documents: Sequence[Document],
        *,
        embedding_client: EmbeddingClient,
        embedding_model: str = DEFAULT_EMBED_MODEL,
        rrf_k: int = DEFAULT_RRF_K,
    ) -> None:
        self._bm25 = BM25Retriever(documents)
        self._semantic = SemanticRetriever(
            documents, client=embedding_client, model=embedding_model
        )
        self._rrf_k = rrf_k

    @property
    def bm25(self) -> BM25Retriever:
        """Retriever léxico subyacente (útil para evals que comparan por capa)."""
        return self._bm25

    @property
    def semantic(self) -> SemanticRetriever:
        """Retriever denso subyacente (útil para evals que comparan por capa)."""
        return self._semantic

    def search(
        self,
        query: str,
        top_k: int = DEFAULT_TOP_K,
        *,
        candidate_pool: int | None = None,
    ) -> list[RetrievalResult]:
        """Recupera ``top_k`` documentos fusionando BM25 + semántico con RRF."""
        if top_k < 1:
            raise ValueError(f"top_k debe ser >= 1, recibí {top_k}")
        pool = candidate_pool if candidate_pool is not None else max(top_k * 4, 20)
        bm25_hits = self._bm25.search(query, top_k=pool)
        sem_hits = self._semantic.search(query, top_k=pool)
        return reciprocal_rank_fusion([bm25_hits, sem_hits], k=self._rrf_k, top_k=top_k)
