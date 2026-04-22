"""Base comun para expertos de producto.

Un experto de producto:

1. Construye una query a partir del ``UserProfile``.
2. Llama al retriever para obtener el pool de candidatos.
3. Pasa perfil + candidatos al LLM, que devuelve un ranking estructurado.
4. Hidrata cada borrador en un ``Recommendation`` completo, inyectando
   metadatos de traza server-side.

La separacion entre ``RecommendationDraft`` y ``Recommendation`` evita que el
modelo tenga que serializar el producto completo o campos de auditoria que el
backend conoce de forma deterministica.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Mapping, Sequence
from decimal import Decimal
from typing import Annotated, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from src.agents.base import BaseAgent, StructuredLLMClient
from src.models.schemas import (
    FinancialProduct,
    ReasoningStep,
    ReasoningTrace,
    Recommendation,
    UserProfile,
)
from src.rag.retriever import RetrievalResult

logger = logging.getLogger(__name__)

DEFAULT_MODEL = os.getenv("FINSAGE_EXPERT_MODEL", "gemini-2.5-flash-lite")
DEFAULT_TEMPERATURE = 0.2
DEFAULT_TOP_N = 3
DEFAULT_CANDIDATE_POOL = 8
DEFAULT_MAX_TOKENS = 4096

MAX_REASONING_STEPS = 3
MAX_EVIDENCE_PER_STEP = 3
MAX_CONSIDERED_PRODUCTS = 8
MAX_CAVEATS = 2

ShortText = Annotated[str, Field(min_length=1, max_length=140)]
EvidenceText = Annotated[str, Field(min_length=1, max_length=80)]
ExplanationText = Annotated[str, Field(min_length=10, max_length=240)]
ConclusionText = Annotated[str, Field(min_length=1, max_length=220)]
RejectReasonText = Annotated[str, Field(min_length=1, max_length=120)]


class Retriever(Protocol):
    """Subset minimo del retriever que usa el experto."""

    def search(self, query: str, top_k: int) -> list[RetrievalResult]: ...


class ReasoningStepDraft(BaseModel):
    """Paso breve del razonamiento emitido por el LLM."""

    model_config = ConfigDict(extra="forbid")

    step: int = Field(..., ge=1, description="Posicion 1-indexada en la traza.")
    description: ShortText = Field(..., description="Resumen breve del criterio evaluado.")
    evidence: list[EvidenceText] = Field(
        default_factory=list,
        max_length=MAX_EVIDENCE_PER_STEP,
        description="Evidencia concreta y breve: product_ids, tasas o umbrales.",
    )


class ReasoningTraceDraft(BaseModel):
    """Traza emitida por el LLM, sin ``agent_name`` ni ``model``."""

    model_config = ConfigDict(extra="forbid")

    steps: list[ReasoningStepDraft] = Field(..., min_length=1, max_length=MAX_REASONING_STEPS)
    considered_products: list[str] = Field(
        default_factory=list,
        max_length=MAX_CONSIDERED_PRODUCTS,
        description="`product_id`s evaluados (ganadores o no).",
    )
    rejected_products: dict[str, RejectReasonText] = Field(
        default_factory=dict,
        description="Mapa `product_id` -> motivo de descarte.",
    )
    final_conclusion: ConclusionText = Field(...)


class RecommendationDraft(BaseModel):
    """Recomendacion individual emitida por el LLM; referencia al producto por id."""

    model_config = ConfigDict(extra="forbid")

    product_id: str = Field(
        ...,
        min_length=1,
        description="Debe aparecer en la lista de candidatos del mensaje del usuario.",
    )
    rank: int = Field(..., ge=1, le=3, description="1 = mejor match.")
    match_score: float = Field(..., ge=0, le=1)
    why_this_fits: ExplanationText = Field(
        ...,
        description="Orientado al usuario final, breve y concreto.",
    )
    caveats: list[ShortText] = Field(default_factory=list, max_length=MAX_CAVEATS)
    reasoning_trace: ReasoningTraceDraft


class ExpertRanking(BaseModel):
    """Top-N ranqueado producido por el experto."""

    model_config = ConfigDict(extra="forbid")

    recommendations: list[RecommendationDraft] = Field(..., min_length=1, max_length=3)


class ProductExpert(BaseAgent):
    """Pipeline generico ``profile -> retrieve -> LLM rank -> hydrate``."""

    def __init__(
        self,
        *,
        retriever: Retriever,
        products: Mapping[str, FinancialProduct],
        system_prompt: str,
        agent_name: str,
        product_type_label: str,
        model: str = DEFAULT_MODEL,
        client: StructuredLLMClient | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
        top_n: int = DEFAULT_TOP_N,
        candidate_pool: int = DEFAULT_CANDIDATE_POOL,
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> None:
        if top_n < 1:
            raise ValueError(f"top_n debe ser >= 1, recibi {top_n}")
        if candidate_pool < top_n:
            raise ValueError(f"candidate_pool ({candidate_pool}) debe ser >= top_n ({top_n})")
        super().__init__(
            model=model,
            system_prompt=system_prompt,
            client=client,
            temperature=temperature,
            max_tokens=max_tokens,
            agent_name=agent_name,
        )
        self._retriever = retriever
        self._products: dict[str, FinancialProduct] = dict(products)
        self._product_type_label = product_type_label
        self._top_n = top_n
        self._candidate_pool = candidate_pool

    def recommend(self, profile: UserProfile) -> list[Recommendation]:
        """Devuelve hasta ``top_n`` recomendaciones ordenadas por ``rank``."""
        query = self._build_query(profile)
        hits = self._retriever.search(query, top_k=self._candidate_pool)
        candidates = self._gather_candidates(hits)
        if not candidates:
            logger.warning(
                "%s: retriever no devolvio candidatos conocidos para la query", self.agent_name
            )
            return []

        user_message = self._render_user_message(profile, candidates)
        ranking = self.call(
            messages=[{"role": "user", "content": user_message}],
            response_model=ExpertRanking,
        )
        return self._hydrate(ranking)

    def _build_query(self, profile: UserProfile) -> str:
        """Templetiza una query lexico-semantica a partir del perfil."""
        return (
            f"{self._product_type_label} para usuario con ingreso mensual "
            f"{profile.monthly_income} {profile.currency.value}, "
            f"ingreso disponible {profile.disposable_income}, perfil de riesgo "
            f"{profile.risk_profile.value}. Objetivo: {profile.stated_goal}"
        )

    def _gather_candidates(self, hits: Sequence[RetrievalResult]) -> list[FinancialProduct]:
        seen: set[str] = set()
        ordered: list[FinancialProduct] = []
        for hit in hits:
            if hit.doc_id in seen:
                continue
            product = self._products.get(hit.doc_id)
            if product is None:
                continue
            seen.add(hit.doc_id)
            ordered.append(product)
        return ordered

    def _render_user_message(
        self, profile: UserProfile, candidates: Sequence[FinancialProduct]
    ) -> str:
        payload = {
            "profile": profile.model_dump(mode="json"),
            "candidates": [self._serialize_candidate(product) for product in candidates],
            "instructions": {
                "top_n": min(self._top_n, len(candidates)),
                "note": (
                    "Selecciona unicamente product_ids presentes en 'candidates'. "
                    "Ranquea 1 = mejor ajuste. Devuelve JSON compacto y auditable."
                ),
                "response_constraints": {
                    "why_this_fits_max_sentences": 2,
                    "caveats_max_items": MAX_CAVEATS,
                    "reasoning_steps_max_items": MAX_REASONING_STEPS,
                    "reasoning_step_evidence_max_items": MAX_EVIDENCE_PER_STEP,
                    "reasoning_style": (
                        "Usa frases cortas. No repitas tablas completas ni listados largos."
                    ),
                },
            },
        }
        return json.dumps(payload, ensure_ascii=False, default=str)

    def _serialize_candidate(self, product: FinancialProduct) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "product_id": product.product_id,
            "product_type": product.product_type,
            "bank_name": product.bank_name,
            "product_name": product.product_name,
            "currency": product.currency.value,
            "min_income_required": _decimal_to_str(product.min_income_required),
            "source_url": product.source_url,
        }
        if product.product_type == "credit_card":
            payload.update(
                {
                    "annual_fee": _decimal_to_str(product.annual_fee),
                    "interest_rate_annual": product.interest_rate_annual,
                    "credit_limit_min": _decimal_to_str(product.credit_limit_min),
                    "credit_limit_max": _decimal_to_str(product.credit_limit_max),
                    "tier": product.tier.value,
                    "rewards_program": product.rewards_program,
                    "cashback_rate": product.cashback_rate,
                    "international": product.international,
                }
            )
        else:
            payload.update(
                {
                    "amount_min": _decimal_to_str(product.amount_min),
                    "amount_max": _decimal_to_str(product.amount_max),
                    "term_months_min": product.term_months_min,
                    "term_months_max": product.term_months_max,
                    "interest_rate_annual": product.interest_rate_annual,
                    "cae": product.cae,
                }
            )
        return payload

    def _hydrate(self, ranking: ExpertRanking) -> list[Recommendation]:
        out: list[Recommendation] = []
        for draft in ranking.recommendations:
            product = self._products.get(draft.product_id)
            if product is None:
                logger.warning(
                    "%s: LLM devolvio product_id desconocido %r y fue descartado",
                    self.agent_name,
                    draft.product_id,
                )
                continue
            trace = ReasoningTrace(
                agent_name=self.agent_name,
                model=self.model,
                steps=[
                    ReasoningStep(
                        step=step.step,
                        description=step.description,
                        evidence=list(step.evidence),
                    )
                    for step in draft.reasoning_trace.steps
                ],
                considered_products=draft.reasoning_trace.considered_products,
                rejected_products=draft.reasoning_trace.rejected_products,
                final_conclusion=draft.reasoning_trace.final_conclusion,
            )
            out.append(
                Recommendation(
                    product=product,
                    match_score=draft.match_score,
                    rank=draft.rank,
                    why_this_fits=draft.why_this_fits,
                    caveats=list(draft.caveats),
                    reasoning_trace=trace,
                )
            )
        out.sort(key=lambda recommendation: recommendation.rank)
        return out[: self._top_n]


def _decimal_to_str(value: Decimal) -> str:
    normalized = value.normalize()
    return format(normalized, "f")
