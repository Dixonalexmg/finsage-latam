"""Schemas Pydantic compartidos: perfil de usuario, productos, recomendaciones y trazas.

Contrato de datos central de FinSage. Toda salida estructurada de un LLM debe
corresponder a uno de estos modelos — ver `CLAUDE.md` §Orchestrator.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class Currency(StrEnum):
    """Monedas soportadas. v1.0 opera sobre Chile (CLP principal, UF para créditos)."""

    CLP = "CLP"
    UF = "UF"
    USD = "USD"


class RiskProfile(StrEnum):
    """Perfil de riesgo inferido por `ProfileAnalyst`."""

    CONSERVATIVE = "conservative"
    MODERATE = "moderate"
    AGGRESSIVE = "aggressive"


class CardTier(StrEnum):
    """Segmento comercial de una tarjeta de crédito."""

    CLASSIC = "classic"
    GOLD = "gold"
    PLATINUM = "platinum"
    SIGNATURE = "signature"
    BLACK = "black"


Intent = Literal["credit_card", "personal_loan", "comparison", "unknown"]


# ---------------------------------------------------------------------------
# Productos financieros
# ---------------------------------------------------------------------------


class _BaseProduct(BaseModel):
    """Campos comunes a todo producto financiero ingerido desde un banco."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    product_id: str = Field(..., min_length=1, description="Id estable `{bank}_{slug}`.")
    bank_name: str = Field(..., min_length=1)
    product_name: str = Field(..., min_length=1)
    currency: Currency = Currency.CLP
    source_url: str = Field(..., description="URL pública — evidencia auditable.")
    scraped_at: datetime = Field(..., description="Timestamp de la última ingesta.")
    min_income_required: Decimal = Field(..., ge=0, description="Renta líquida mensual mínima.")

    @field_validator("source_url")
    @classmethod
    def _must_be_http(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("source_url must be an http(s) URL")
        return v


class CreditCard(_BaseProduct):
    """Tarjeta de crédito bancaria ofrecida al público general."""

    product_type: Literal["credit_card"] = "credit_card"
    annual_fee: Decimal = Field(..., ge=0, description="Comisión anual de administración.")
    interest_rate_annual: float = Field(..., ge=0, le=1, description="TEA en decimal (0.35 = 35%).")
    credit_limit_min: Decimal = Field(..., ge=0)
    credit_limit_max: Decimal = Field(..., ge=0)
    tier: CardTier = CardTier.CLASSIC
    rewards_program: bool = False
    cashback_rate: float | None = Field(
        default=None, ge=0, le=1, description="Fracción devuelta en compras elegibles."
    )
    international: bool = True

    @model_validator(mode="after")
    def _check_limits(self) -> CreditCard:
        if self.credit_limit_max < self.credit_limit_min:
            raise ValueError("credit_limit_max must be >= credit_limit_min")
        return self


class PersonalLoan(_BaseProduct):
    """Préstamo personal de consumo, no garantizado."""

    product_type: Literal["personal_loan"] = "personal_loan"
    amount_min: Decimal = Field(..., ge=0)
    amount_max: Decimal = Field(..., ge=0)
    term_months_min: int = Field(..., ge=1, le=120)
    term_months_max: int = Field(..., ge=1, le=120)
    interest_rate_annual: float = Field(..., ge=0, le=1, description="Tasa nominal anual.")
    cae: float = Field(..., ge=0, le=1, description="Costo Anual Equivalente informado al cliente.")

    @model_validator(mode="after")
    def _check_ranges(self) -> PersonalLoan:
        if self.amount_max < self.amount_min:
            raise ValueError("amount_max must be >= amount_min")
        if self.term_months_max < self.term_months_min:
            raise ValueError("term_months_max must be >= term_months_min")
        if self.cae < self.interest_rate_annual:
            raise ValueError("cae must be >= interest_rate_annual (CAE incluye costos extra)")
        return self


FinancialProduct = Annotated[
    CreditCard | PersonalLoan,
    Field(discriminator="product_type"),
]
"""Union discriminada por `product_type`. Usar como anotación en schemas que la contengan."""


# ---------------------------------------------------------------------------
# Perfil del usuario
# ---------------------------------------------------------------------------


class UserProfile(BaseModel):
    """Perfil financiero extraído por `ProfileAnalyst` a partir de la conversación.

    No contiene PII: nombres, RUT, email, etc. no se persisten (ver `CLAUDE.md`).
    """

    model_config = ConfigDict(extra="forbid")

    monthly_income: Decimal = Field(..., ge=0, description="Ingreso líquido mensual declarado.")
    monthly_expenses: Decimal = Field(..., ge=0, description="Gasto fijo mensual estimado.")
    existing_debt: Decimal = Field(default=Decimal(0), ge=0, description="Deuda vigente total.")
    currency: Currency = Currency.CLP
    age: int | None = Field(default=None, ge=18, le=100)
    credit_score: int | None = Field(
        default=None, ge=300, le=850, description="Opcional; rango FICO estándar."
    )
    risk_profile: RiskProfile = RiskProfile.MODERATE
    stated_goal: str = Field(
        ..., min_length=3, description="Objetivo en lenguaje natural del usuario."
    )
    intent: Intent = "unknown"

    @field_validator("monthly_income", "monthly_expenses", "existing_debt", mode="before")
    @classmethod
    def _coerce_optional_decimal_strings(cls, value: object) -> object:
        if value is None:
            return Decimal(0)
        if isinstance(value, str) and value.strip().lower() in {"", "none", "null", "n/a"}:
            return Decimal(0)
        return value

    @field_validator("age", "credit_score", mode="before")
    @classmethod
    def _coerce_optional_int_strings(cls, value: object) -> object:
        if value is None:
            return None
        if isinstance(value, str) and value.strip().lower() in {"", "none", "null", "n/a"}:
            return None
        return value

    @model_validator(mode="after")
    def _expenses_sane(self) -> UserProfile:
        if self.monthly_income > 0 and self.monthly_expenses > self.monthly_income * Decimal(3):
            raise ValueError("monthly_expenses implausibly high vs monthly_income")
        return self

    @property
    def disposable_income(self) -> Decimal:
        """Ingreso disponible mensual (ingreso - gastos)."""
        return self.monthly_income - self.monthly_expenses


# ---------------------------------------------------------------------------
# Razonamiento y recomendaciones
# ---------------------------------------------------------------------------


class ReasoningStep(BaseModel):
    """Paso individual del razonamiento del agente."""

    model_config = ConfigDict(extra="forbid")

    step: int = Field(..., ge=1, description="Posición 1-indexada en la traza.")
    description: str = Field(..., min_length=1, description="Qué se evaluó en este paso.")
    evidence: list[str] = Field(
        default_factory=list, description="`product_id`s o datos citados como soporte."
    )


class ReasoningTrace(BaseModel):
    """Traza auditable que justifica una recomendación.

    Todo `Recommendation` devuelto por el sistema DEBE incluir una traza — es la
    contrapartida de "decisiones con razonamiento auditable" del proyecto.
    """

    model_config = ConfigDict(extra="forbid")

    agent_name: str = Field(..., min_length=1, description="Ej: 'CardAdvisor', 'LoanAdvisor'.")
    model: str = Field(
        ...,
        min_length=1,
        description="Modelo LLM usado, ej: 'gemini-2.5-flash-lite'.",
    )
    steps: list[ReasoningStep] = Field(..., min_length=1)
    considered_products: list[str] = Field(
        default_factory=list, description="`product_id`s evaluados (ganadores o no)."
    )
    rejected_products: dict[str, str] = Field(
        default_factory=dict, description="Mapa `product_id` → motivo de descarte."
    )
    final_conclusion: str = Field(..., min_length=1)

    @field_validator("steps")
    @classmethod
    def _steps_ordered(cls, v: list[ReasoningStep]) -> list[ReasoningStep]:
        for expected, actual in enumerate(v, start=1):
            if actual.step != expected:
                raise ValueError(
                    f"steps must be 1-indexed and contiguous; expected {expected}, got {actual.step}"
                )
        return v


class Recommendation(BaseModel):
    """Recomendación final devuelta al usuario para una query."""

    model_config = ConfigDict(extra="forbid")

    product: FinancialProduct = Field(..., description="Producto recomendado.")
    match_score: float = Field(
        ..., ge=0, le=1, description="Score 0..1 de ajuste al perfil del usuario."
    )
    rank: int = Field(..., ge=1, description="Posición en el ranking (1 = mejor match).")
    why_this_fits: str = Field(
        ..., min_length=10, description="Explicación concisa orientada al usuario final."
    )
    caveats: list[str] = Field(
        default_factory=list, description="Advertencias, letra chica, condiciones a comunicar."
    )
    reasoning_trace: ReasoningTrace = Field(..., description="Traza auditable (obligatoria).")
