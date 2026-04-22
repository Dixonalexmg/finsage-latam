"""Tests unitarios para BaseAgent con cliente Gemini mockeado."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, Field
from src.agents.base import BaseAgent, StructuredOutputError
from src.models.schemas import UserProfile


class _Answer(BaseModel):
    """Schema simple para verificar el round-trip de structured output."""

    label: str = Field(..., min_length=1)
    confidence: float = Field(..., ge=0, le=1)


def test_call_returns_validated_pydantic_instance(mocker: Any) -> None:
    client = mocker.MagicMock()
    client.generate_json.return_value = '{"label": "credit_card", "confidence": 0.91}'

    agent = BaseAgent(
        model="gemini-2.5-flash-lite",
        system_prompt="responde en JSON",
        client=client,
    )

    result = agent.call(
        messages=[{"role": "user", "content": "clasifica esto"}],
        response_model=_Answer,
    )

    assert isinstance(result, _Answer)
    assert result.label == "credit_card"
    assert result.confidence == pytest.approx(0.91)

    kwargs = client.generate_json.call_args.kwargs
    assert kwargs["model"] == "gemini-2.5-flash-lite"
    assert kwargs["json_schema"]["type"] == "object"
    assert "propertyOrdering" in kwargs["json_schema"]
    assert kwargs["system_prompt"] == "responde en JSON"


def test_call_raises_when_model_returns_invalid_json(mocker: Any) -> None:
    client = mocker.MagicMock()
    client.generate_json.return_value = "no soy json"

    agent = BaseAgent(
        model="gemini-2.5-flash-lite",
        system_prompt="usa JSON",
        client=client,
    )

    with pytest.raises(StructuredOutputError, match="JSON valido"):
        agent.call(
            messages=[{"role": "user", "content": "hola"}],
            response_model=_Answer,
        )


def test_call_mentions_truncation_when_json_is_cut_off(mocker: Any) -> None:
    client = mocker.MagicMock()
    client.generate_json.return_value = '{"label": "credit_card", "confidence": '

    agent = BaseAgent(
        model="gemini-2.5-flash-lite",
        system_prompt="usa JSON",
        client=client,
    )

    with pytest.raises(StructuredOutputError, match="salida truncada"):
        agent.call(
            messages=[{"role": "user", "content": "hola"}],
            response_model=_Answer,
        )


def test_schema_normalization_preserves_properties_for_gemini() -> None:
    schema = BaseAgent._build_response_schema(UserProfile)

    assert schema["type"] == "object"
    assert "monthly_income" in schema["properties"]
    assert "monthly_expenses" in schema["properties"]
    assert "stated_goal" in schema["properties"]
    assert schema["propertyOrdering"][:3] == [
        "monthly_income",
        "monthly_expenses",
        "existing_debt",
    ]
