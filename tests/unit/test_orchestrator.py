"""Tests unitarios del orchestrator y su render determinista."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from src.agents.orchestrator import Orchestrator, _fmt_money, _render_response
from src.models.schemas import (
    CardTier,
    CreditCard,
    ReasoningStep,
    ReasoningTrace,
    Recommendation,
    RiskProfile,
    UserProfile,
)


def _profile(*, intent: str = "credit_card", goal: str = "Quiero cashback") -> UserProfile:
    return UserProfile(
        monthly_income=Decimal("1800000"),
        monthly_expenses=Decimal("700000"),
        existing_debt=Decimal("100000"),
        risk_profile=RiskProfile.MODERATE,
        stated_goal=goal,
        intent=intent,  # type: ignore[arg-type]
    )


def _card(product_id: str = "card_gold") -> CreditCard:
    return CreditCard(
        product_id=product_id,
        bank_name="Banco Test",
        product_name="Tarjeta Dorada",
        source_url="https://example.com/card",
        scraped_at=datetime(2026, 4, 1, tzinfo=UTC),
        min_income_required=Decimal("900000"),
        annual_fee=Decimal("60000"),
        interest_rate_annual=0.31,
        credit_limit_min=Decimal("800000"),
        credit_limit_max=Decimal("7000000"),
        tier=CardTier.GOLD,
        rewards_program=True,
        cashback_rate=0.02,
        international=True,
    )


def _recommendation(product_id: str = "card_gold", rank: int = 1) -> Recommendation:
    return Recommendation(
        product=_card(product_id),
        match_score=0.91,
        rank=rank,
        why_this_fits="Te conviene por su cashback y una renta minima compatible con tu perfil.",
        caveats=["La comision anual no es cero."],
        reasoning_trace=ReasoningTrace(
            agent_name="CreditCardExpert",
            model="claude-3-7-sonnet-latest",
            steps=[
                ReasoningStep(
                    step=1,
                    description="Valide elegibilidad por renta y objetivo.",
                    evidence=[product_id],
                ),
                ReasoningStep(
                    step=2,
                    description="Compare costos y beneficios contra el pool.",
                    evidence=[product_id],
                ),
            ],
            considered_products=[product_id],
            rejected_products={"other_card": "cashback inferior"},
            final_conclusion="Es la mejor combinacion de beneficios y elegibilidad.",
        ),
    )


class _FakeProfileAnalyst:
    def __init__(self, profile: UserProfile) -> None:
        self.profile = profile
        self.calls: list[list[dict[str, Any]]] = []

    def extract_profile(self, conversation: list[dict[str, Any]]) -> UserProfile:
        self.calls.append(conversation)
        return self.profile


class _FakeExpert:
    def __init__(self, recommendations: list[Recommendation]) -> None:
        self.recommendations = recommendations
        self.calls: list[UserProfile] = []

    def recommend(self, profile: UserProfile) -> list[Recommendation]:
        self.calls.append(profile)
        return list(self.recommendations)


def test_orchestrator_run_routes_to_registered_expert_and_renders_markdown() -> None:
    profile = _profile()
    analyst = _FakeProfileAnalyst(profile)
    expert = _FakeExpert([_recommendation()])
    orchestrator = Orchestrator(
        profile_analyst=analyst,
        experts={"credit_card": expert},
    )

    state = orchestrator.run("Necesito una tarjeta con cashback")

    assert orchestrator.graph is not None
    assert analyst.calls[0] == [{"role": "user", "content": "Necesito una tarjeta con cashback"}]
    assert expert.calls == [profile]
    assert state["intent"] == "credit_card"
    assert len(state["recommendations"]) == 1
    assert "Tarjetas de cr" in state["final_response"]
    assert "Tarjeta Dorada" in state["final_response"]
    assert "Razonamiento (CreditCardExpert)" in state["final_response"]


def test_orchestrator_returns_empty_recommendations_when_intent_has_no_expert() -> None:
    profile = _profile(intent="personal_loan", goal="Quiero un prestamo")
    analyst = _FakeProfileAnalyst(profile)
    orchestrator = Orchestrator(profile_analyst=analyst, experts={})

    state = orchestrator.run("Necesito un prestamo para ordenar mis deudas")

    assert state["intent"] == "personal_loan"
    assert state["recommendations"] == []
    assert "Para darte una mejor respuesta" in state["final_response"]
    assert "Cuanto dinero necesitas pedir" in state["final_response"]


def test_orchestrator_comparison_without_context_asks_for_key_data() -> None:
    profile = UserProfile(
        monthly_income=Decimal("0"),
        monthly_expenses=Decimal("0"),
        existing_debt=Decimal("0"),
        risk_profile=RiskProfile.MODERATE,
        stated_goal="comparar",
        intent="comparison",
    )
    analyst = _FakeProfileAnalyst(profile)
    orchestrator = Orchestrator(profile_analyst=analyst, experts={})

    state = orchestrator.run("Que es mejor una tarjeta o un credito personal?")

    assert state["intent"] == "comparison"
    assert state["recommendations"] == []
    assert "Contexto disponible" in state["final_response"]
    assert "Perfil detectado" not in state["final_response"]
    assert "Ingreso liquido mensual: $0" not in state["final_response"]
    assert "Una tarjeta conviene mas" in state["final_response"]
    assert "Tu ingreso liquido mensual aproximado en CLP." in state["final_response"]
    assert "Que criterio pesa mas para ti" in state["final_response"]


def test_orchestrator_personal_loan_with_amount_term_and_purpose_does_not_reask_purpose() -> None:
    profile = UserProfile(
        monthly_income=Decimal("1400000"),
        monthly_expenses=Decimal("700000"),
        existing_debt=Decimal("0"),
        risk_profile=RiskProfile.MODERATE,
        stated_goal="consolidar deudas",
        intent="personal_loan",
    )
    analyst = _FakeProfileAnalyst(profile)
    orchestrator = Orchestrator(profile_analyst=analyst, experts={})

    state = orchestrator.run(
        "Gano 1.400.000 CLP, gasto 700.000 CLP y necesito un prestamo de 6 millones "
        "a 36 meses para consolidar deudas."
    )

    assert state["intent"] == "personal_loan"
    assert "Si el credito es para consolidar deudas" not in state["final_response"]


def test_orchestrator_comparison_with_tarjeta_y_prestamo_uses_general_rule_block() -> None:
    profile = UserProfile(
        monthly_income=Decimal("0"),
        monthly_expenses=Decimal("0"),
        existing_debt=Decimal("0"),
        risk_profile=RiskProfile.MODERATE,
        stated_goal="financiar una compra grande",
        intent="comparison",
    )
    analyst = _FakeProfileAnalyst(profile)
    orchestrator = Orchestrator(profile_analyst=analyst, experts={})

    state = orchestrator.run(
        "Quiero algo para financiar una compra grande, pero no se si me conviene "
        "una tarjeta en cuotas o un prestamo. Serian como 3 millones y podria "
        "pagarlo en 24 meses."
    )

    assert "Una tarjeta conviene mas" in state["final_response"]
    assert "Un credito personal conviene mas" in state["final_response"]


def test_orchestrator_rejects_blank_query() -> None:
    orchestrator = Orchestrator(profile_analyst=_FakeProfileAnalyst(_profile()))

    with pytest.raises(ValueError, match="query no puede ser vac"):
        orchestrator.run("   ")


def test_classify_intent_requires_profile_in_state() -> None:
    orchestrator = Orchestrator(profile_analyst=_FakeProfileAnalyst(_profile()))

    with pytest.raises(RuntimeError, match="classify_intent"):
        orchestrator._classify_intent({})


def test_route_to_expert_requires_profile_in_state() -> None:
    orchestrator = Orchestrator(profile_analyst=_FakeProfileAnalyst(_profile()))

    with pytest.raises(RuntimeError, match="route_to_expert"):
        orchestrator._route_to_expert({"intent": "credit_card"})


def test_render_response_handles_profile_caveats_and_rank_sorting() -> None:
    profile = _profile()
    lower = _recommendation("card_b", rank=2)
    higher = _recommendation("card_a", rank=1)

    rendered = _render_response(
        profile=profile,
        intent="credit_card",
        recommendations=[lower, higher],
        query="Quiero cashback",
        clarification_questions=[],
    )

    assert rendered.index("### 1.") < rendered.index("### 2.")
    assert "Consideraciones" in rendered
    assert "Ingreso disponible" in rendered
    assert "Conclusi" in rendered


def test_fmt_money_rounds_to_integer_string() -> None:
    assert _fmt_money(Decimal("1234567.89")) == "$1,234,568"
