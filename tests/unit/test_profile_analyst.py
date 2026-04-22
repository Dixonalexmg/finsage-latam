"""Tests unitarios para ProfileAnalyst con cliente Gemini mockeado."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from src.agents.profile_analyst import ProfileAnalyst
from src.models.schemas import Currency, RiskProfile, UserProfile


def test_extract_profile_returns_validated_user_profile(mocker: Any) -> None:
    client = mocker.MagicMock()
    client.generate_json.return_value = """
    {
      "monthly_income": "1500000",
      "monthly_expenses": "800000",
      "existing_debt": "200000",
      "currency": "CLP",
      "age": 32,
      "credit_score": 720,
      "risk_profile": "moderate",
      "stated_goal": "Quiero una tarjeta con cashback en supermercados",
      "intent": "credit_card"
    }
    """

    analyst = ProfileAnalyst(client=client, temperature=0.5)
    profile = analyst.extract_profile(
        [
            {"role": "user", "content": "Gano 1.5M CLP, gasto 800k. Quiero tarjeta con cashback."},
        ]
    )

    assert isinstance(profile, UserProfile)
    assert profile.monthly_income == Decimal("1500000")
    assert profile.disposable_income == Decimal("700000")
    assert profile.intent == "credit_card"
    assert profile.currency is Currency.CLP
    assert profile.risk_profile is RiskProfile.MODERATE

    kwargs = client.generate_json.call_args.kwargs
    assert kwargs["model"] == ProfileAnalyst.DEFAULT_MODEL
    assert "ProfileAnalyst" in kwargs["system_prompt"]
    assert kwargs["temperature"] == 0.5


def test_extract_profile_coerces_none_strings_for_missing_financial_fields(mocker: Any) -> None:
    client = mocker.MagicMock()
    client.generate_json.return_value = """
    {
      "monthly_income": "None",
      "monthly_expenses": "None",
      "existing_debt": "None",
      "currency": "CLP",
      "age": "None",
      "credit_score": "None",
      "risk_profile": "moderate",
      "stated_goal": "Que es mejor una tarjeta o un credito personal?",
      "intent": "comparison"
    }
    """

    analyst = ProfileAnalyst(client=client, temperature=0.5)
    profile = analyst.extract_profile(
        [
            {"role": "user", "content": "Que es mejor una tarjeta o un credito personal?"},
        ]
    )

    assert profile.monthly_income == Decimal("0")
    assert profile.monthly_expenses == Decimal("0")
    assert profile.existing_debt == Decimal("0")
    assert profile.age is None
    assert profile.credit_score is None
    assert profile.intent == "comparison"
