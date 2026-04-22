"""Tests unitarios del catalogo seed y del runtime real."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from src.catalog import (
    build_credit_card_documents,
    build_loan_documents,
    load_catalog_from_duckdb,
    load_seed_catalog,
    materialize_catalog,
)
from src.runtime import RuntimeConfigurationError, RuntimeSettings, build_runtime

SEED_DIR = Path("data") / "seed"


class _FakeEmbeddingResponse:
    def __init__(self, embeddings: list[list[float]]) -> None:
        self.embeddings = embeddings


class _FakeEmbeddingClient:
    """Cliente fake para embeddings; genera vectores deterministas y baratos."""

    def embed(
        self,
        texts: list[str],
        model: str | None = None,
        input_type: str | None = None,
    ) -> _FakeEmbeddingResponse:
        _ = model
        scale = 1.0 if input_type == "document" else 2.0
        embeddings = [
            [float((len(text) % 17) + 1) * scale, float((sum(map(ord, text)) % 31) + 1) * scale]
            for text in texts
        ]
        return _FakeEmbeddingResponse(embeddings)


def test_load_seed_catalog_returns_real_seed_snapshot() -> None:
    snapshot = load_seed_catalog(SEED_DIR)

    assert len(snapshot.cards) >= 6
    assert len(snapshot.loans) >= 6
    assert {card.product_id for card in snapshot.cards} >= {
        "banco_chile_visa_gold",
        "scotiabank_visa_signature",
    }
    assert {loan.product_id for loan in snapshot.loans} >= {
        "bci_credito_consumo",
        "bancoestado_credito_consumo",
    }


def test_materialize_catalog_round_trips_through_duckdb(tmp_path: Path) -> None:
    snapshot = load_seed_catalog(SEED_DIR)
    db_path = tmp_path / "catalog.duckdb"

    materialize_catalog(snapshot, db_path)
    restored = load_catalog_from_duckdb(db_path)

    assert [card.product_id for card in restored.cards] == sorted(
        card.product_id for card in snapshot.cards
    )
    assert [loan.product_id for loan in restored.loans] == sorted(
        loan.product_id for loan in snapshot.loans
    )


def test_catalog_builds_indexable_documents() -> None:
    snapshot = load_seed_catalog(SEED_DIR)

    card_docs = build_credit_card_documents(snapshot.cards)
    loan_docs = build_loan_documents(snapshot.loans)

    assert card_docs[0].metadata["product_type"] == "credit_card"
    assert "Renta minima" in card_docs[0].text
    assert loan_docs[0].metadata["product_type"] == "personal_loan"
    assert "CAE" in loan_docs[0].text


def test_build_runtime_requires_both_external_keys(tmp_path: Path) -> None:
    settings = RuntimeSettings(
        seed_dir=SEED_DIR,
        duckdb_path=tmp_path / "catalog.duckdb",
        gemini_api_key=None,
    )

    with pytest.raises(RuntimeConfigurationError, match="GEMINI_API_KEY"):
        build_runtime(settings=settings)


def test_build_runtime_wires_seed_catalog_and_experts(tmp_path: Path, mocker: Any) -> None:
    settings = RuntimeSettings(
        seed_dir=SEED_DIR,
        duckdb_path=tmp_path / "catalog.duckdb",
        gemini_api_key="test-gemini",
    )
    llm_client = mocker.MagicMock()

    runtime = build_runtime(
        settings=settings,
        llm_client=llm_client,
        embedding_client=_FakeEmbeddingClient(),
    )

    assert runtime.available_intents == ("credit_card", "personal_loan")
    assert len(runtime.catalog.cards) >= 6
    assert len(runtime.catalog.loans) >= 6
