"""Tests para CreditCardExpert y LoanExpert con retriever y Gemini mockeados."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from src.agents.credit_card_expert import CreditCardExpert
from src.agents.loan_expert import LoanExpert
from src.agents.product_expert import ProductExpert
from src.models.schemas import (
    CardTier,
    CreditCard,
    PersonalLoan,
    Recommendation,
    RiskProfile,
    UserProfile,
)
from src.rag.retriever import RetrievalResult

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _card(
    product_id: str,
    *,
    annual_fee: str = "0",
    tea: float = 0.30,
    min_income: str = "500000",
    cashback: float | None = None,
    tier: CardTier = CardTier.CLASSIC,
) -> CreditCard:
    return CreditCard(
        product_id=product_id,
        bank_name="Banco Test",
        product_name=f"Tarjeta {product_id}",
        source_url="https://example.cl/tarjetas",
        scraped_at=datetime(2026, 4, 1, tzinfo=UTC),
        min_income_required=Decimal(min_income),
        annual_fee=Decimal(annual_fee),
        interest_rate_annual=tea,
        credit_limit_min=Decimal("500000"),
        credit_limit_max=Decimal("3000000"),
        tier=tier,
        rewards_program=cashback is not None,
        cashback_rate=cashback,
        international=True,
    )


def _loan(
    product_id: str,
    *,
    cae: float = 0.25,
    tea: float = 0.22,
    min_income: str = "500000",
    amount_min: str = "500000",
    amount_max: str = "20000000",
) -> PersonalLoan:
    return PersonalLoan(
        product_id=product_id,
        bank_name="Banco Test",
        product_name=f"Préstamo {product_id}",
        source_url="https://example.cl/prestamos",
        scraped_at=datetime(2026, 4, 1, tzinfo=UTC),
        min_income_required=Decimal(min_income),
        amount_min=Decimal(amount_min),
        amount_max=Decimal(amount_max),
        term_months_min=6,
        term_months_max=60,
        interest_rate_annual=tea,
        cae=cae,
    )


def _profile(*, intent: str = "credit_card", goal: str = "Quiero cashback") -> UserProfile:
    return UserProfile(
        monthly_income=Decimal("1500000"),
        monthly_expenses=Decimal("700000"),
        existing_debt=Decimal("0"),
        risk_profile=RiskProfile.MODERATE,
        stated_goal=goal,
        intent=intent,  # type: ignore[arg-type]
    )


class _FakeRetriever:
    """Retriever fake que devuelve un ranking fijo por doc_id."""

    def __init__(self, ordered_doc_ids: list[str]) -> None:
        self._ordered = ordered_doc_ids
        self.calls: list[tuple[str, int]] = []

    def search(self, query: str, top_k: int) -> list[RetrievalResult]:
        self.calls.append((query, top_k))
        return [
            RetrievalResult(doc_id=doc_id, score=1.0 / (i + 1), rank=i + 1, method="hybrid")
            for i, doc_id in enumerate(self._ordered[:top_k])
        ]


def _make_draft(
    product_id: str,
    *,
    rank: int,
    match_score: float,
    considered: list[str],
    rejected: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "product_id": product_id,
        "rank": rank,
        "match_score": match_score,
        "why_this_fits": f"Se ajusta a tu objetivo por {product_id}.",
        "caveats": [],
        "reasoning_trace": {
            "steps": [
                {"step": 1, "description": f"Evalué {product_id}", "evidence": [product_id]},
                {"step": 2, "description": "Comparé vs resto del pool", "evidence": considered},
            ],
            "considered_products": considered,
            "rejected_products": rejected or {},
            "final_conclusion": f"{product_id} rank={rank} es razonable para el perfil.",
        },
    }


# ---------------------------------------------------------------------------
# CreditCardExpert
# ---------------------------------------------------------------------------


def test_credit_card_expert_returns_ranked_recommendations(mocker: Any) -> None:
    cards = [
        _card("card_a", cashback=0.03),
        _card("card_b", cashback=0.01),
        _card("card_c"),
    ]
    retriever = _FakeRetriever(["card_a", "card_b", "card_c"])
    client = mocker.MagicMock()
    client.generate_json.return_value = json.dumps(
        {
            "recommendations": [
                _make_draft(
                    "card_a", rank=1, match_score=0.9, considered=["card_a", "card_b", "card_c"]
                ),
                _make_draft(
                    "card_b", rank=2, match_score=0.7, considered=["card_a", "card_b", "card_c"]
                ),
                _make_draft(
                    "card_c", rank=3, match_score=0.5, considered=["card_a", "card_b", "card_c"]
                ),
            ]
        }
    )

    expert = CreditCardExpert(retriever=retriever, cards=cards, client=client)
    recs = expert.recommend(_profile())

    assert len(recs) == 3
    assert [r.rank for r in recs] == [1, 2, 3]
    assert [r.product.product_id for r in recs] == ["card_a", "card_b", "card_c"]
    assert all(isinstance(r, Recommendation) for r in recs)
    assert recs[0].reasoning_trace.agent_name == "CreditCardExpert"
    assert recs[0].reasoning_trace.model == CreditCardExpert.DEFAULT_MODEL
    # considered_products viajó desde el LLM
    assert set(recs[0].reasoning_trace.considered_products) == {"card_a", "card_b", "card_c"}

    # Retriever recibió una query con la etiqueta del tipo de producto y el objetivo.
    query, _ = retriever.calls[0]
    assert "tarjeta de crédito" in query
    assert "cashback" in query.lower()


def test_credit_card_expert_drops_hallucinated_product_ids(mocker: Any) -> None:
    cards = [_card("card_real")]
    retriever = _FakeRetriever(["card_real"])
    client = mocker.MagicMock()
    client.generate_json.return_value = json.dumps(
        {
            "recommendations": [
                _make_draft("card_real", rank=1, match_score=0.8, considered=["card_real"]),
                _make_draft("card_fake", rank=2, match_score=0.6, considered=["card_real"]),
            ]
        }
    )

    expert = CreditCardExpert(retriever=retriever, cards=cards, client=client)
    recs = expert.recommend(_profile())

    # El product_id inventado se descarta silenciosamente.
    assert [r.product.product_id for r in recs] == ["card_real"]


def test_expert_returns_empty_when_retriever_has_no_known_products(mocker: Any) -> None:
    cards = [_card("card_a")]
    # retriever devuelve un doc_id que no está en el catálogo del experto
    retriever = _FakeRetriever(["unknown_card"])
    client = mocker.MagicMock()

    expert = CreditCardExpert(retriever=retriever, cards=cards, client=client)
    recs = expert.recommend(_profile())

    assert recs == []
    # No debe haber llamado al LLM si no hay candidatos hidratables
    client.generate_json.assert_not_called()


def test_expert_sends_compact_candidate_payload_to_llm(mocker: Any) -> None:
    cards = [_card("card_a", annual_fee="50000", cashback=0.03)]
    retriever = _FakeRetriever(["card_a"])
    client = mocker.MagicMock()
    client.generate_json.return_value = json.dumps(
        {
            "recommendations": [
                _make_draft("card_a", rank=1, match_score=0.91, considered=["card_a"])
            ]
        }
    )

    expert = CreditCardExpert(retriever=retriever, cards=cards, client=client)
    expert.recommend(_profile())

    message_payload = json.loads(client.generate_json.call_args.kwargs["messages"][0]["content"])
    candidate = message_payload["candidates"][0]

    assert candidate["product_id"] == "card_a"
    assert candidate["annual_fee"] == "50000"
    assert "scraped_at" not in candidate
    assert "response_constraints" in message_payload["instructions"]


# ---------------------------------------------------------------------------
# LoanExpert
# ---------------------------------------------------------------------------


def test_loan_expert_returns_ranked_recommendations(mocker: Any) -> None:
    loans = [
        _loan("loan_x", cae=0.20, tea=0.18),
        _loan("loan_y", cae=0.28, tea=0.24),
        _loan("loan_z", cae=0.35, tea=0.30),
    ]
    retriever = _FakeRetriever(["loan_x", "loan_y", "loan_z"])
    client = mocker.MagicMock()
    client.generate_json.return_value = json.dumps(
        {
            "recommendations": [
                _make_draft(
                    "loan_x",
                    rank=1,
                    match_score=0.95,
                    considered=["loan_x", "loan_y", "loan_z"],
                    rejected={"loan_z": "CAE alto"},
                ),
                _make_draft(
                    "loan_y",
                    rank=2,
                    match_score=0.7,
                    considered=["loan_x", "loan_y", "loan_z"],
                ),
            ]
        }
    )

    expert = LoanExpert(
        retriever=retriever,
        loans=loans,
        client=client,
    )
    recs = expert.recommend(_profile(intent="personal_loan", goal="Necesito 5M para auto"))

    assert [r.rank for r in recs] == [1, 2]
    assert recs[0].product.product_id == "loan_x"
    assert recs[0].reasoning_trace.agent_name == "LoanExpert"
    assert recs[0].reasoning_trace.rejected_products == {"loan_z": "CAE alto"}

    query, _ = retriever.calls[0]
    assert "préstamo" in query.lower() or "prestamo" in query.lower()


# ---------------------------------------------------------------------------
# Validación de construcción
# ---------------------------------------------------------------------------


def test_product_expert_rejects_top_n_larger_than_pool() -> None:
    with pytest.raises(ValueError, match="candidate_pool"):
        ProductExpert(
            retriever=_FakeRetriever([]),
            products={},
            system_prompt="x",
            agent_name="X",
            product_type_label="x",
            top_n=5,
            candidate_pool=3,
        )


def test_product_expert_rejects_non_positive_top_n() -> None:
    with pytest.raises(ValueError, match="top_n"):
        ProductExpert(
            retriever=_FakeRetriever([]),
            products={},
            system_prompt="x",
            agent_name="X",
            product_type_label="x",
            top_n=0,
        )
