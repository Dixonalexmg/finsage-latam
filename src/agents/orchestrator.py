"""Orchestrator principal que coordina agentes via LangGraph."""

from __future__ import annotations

import logging
import re
from decimal import Decimal
from typing import Any, Protocol, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from src.agents.base import ChatMessage, StructuredLLMClient
from src.agents.profile_analyst import ProfileAnalyst
from src.models.schemas import Intent, Recommendation, UserProfile

logger = logging.getLogger(__name__)


class Expert(Protocol):
    """Contrato minimo que deben cumplir los sub-agentes de dominio."""

    def recommend(self, profile: UserProfile) -> list[Recommendation]:
        """Devuelve una lista ranqueada de recomendaciones para ``profile``."""
        ...


class OrchestratorState(TypedDict, total=False):
    """Estado compartido entre nodos del grafo."""

    query: str
    conversation: list[ChatMessage]
    profile: UserProfile
    intent: Intent
    recommendations: list[Recommendation]
    clarification_questions: list[str]
    final_response: str


class Orchestrator:
    """Coordina el pipeline ``extract_profile -> classify_intent -> route -> compose``."""

    def __init__(
        self,
        *,
        profile_analyst: ProfileAnalyst | None = None,
        experts: dict[Intent, Expert] | None = None,
        client: StructuredLLMClient | None = None,
    ) -> None:
        self._profile_analyst = profile_analyst or ProfileAnalyst(client=client)
        self._experts: dict[Intent, Expert] = dict(experts or {})
        self._graph: CompiledStateGraph[OrchestratorState, Any, Any, Any] = self._build_graph()

    @property
    def graph(self) -> CompiledStateGraph[OrchestratorState, Any, Any, Any]:
        """Grafo compilado util para inspeccion y tests."""
        return self._graph

    def run(self, query: str) -> OrchestratorState:
        """Ejecuta el pipeline completo para una consulta del usuario."""
        if not query.strip():
            raise ValueError("query no puede ser vacio")
        initial: OrchestratorState = {
            "query": query,
            "conversation": [{"role": "user", "content": query}],
            "intent": "unknown",
            "recommendations": [],
        }
        final = self._graph.invoke(initial)
        return cast(OrchestratorState, final)

    def _build_graph(self) -> CompiledStateGraph[OrchestratorState, Any, Any, Any]:
        graph: StateGraph[OrchestratorState, Any, Any, Any] = StateGraph(OrchestratorState)
        graph.add_node("extract_profile", self._extract_profile)
        graph.add_node("classify_intent", self._classify_intent)
        graph.add_node("route_to_expert", self._route_to_expert)
        graph.add_node("compose_response", self._compose_response)
        graph.add_edge(START, "extract_profile")
        graph.add_edge("extract_profile", "classify_intent")
        graph.add_edge("classify_intent", "route_to_expert")
        graph.add_edge("route_to_expert", "compose_response")
        graph.add_edge("compose_response", END)
        return graph.compile()

    def _extract_profile(self, state: OrchestratorState) -> dict[str, Any]:
        conversation = state.get("conversation") or [{"role": "user", "content": state["query"]}]
        profile = self._profile_analyst.extract_profile(conversation)
        logger.info("profile extracted: intent=%s risk=%s", profile.intent, profile.risk_profile)
        return {"profile": profile}

    def _classify_intent(self, state: OrchestratorState) -> dict[str, Any]:
        profile = state.get("profile")
        if profile is None:
            raise RuntimeError("classify_intent corrio antes que extract_profile")
        intent: Intent = profile.intent
        logger.info("intent classified as %s", intent)
        return {"intent": intent}

    def _route_to_expert(self, state: OrchestratorState) -> dict[str, Any]:
        profile = state.get("profile")
        intent = state.get("intent", "unknown")
        query = state.get("query", "")
        if profile is None:
            raise RuntimeError("route_to_expert corrio sin perfil en el estado")

        clarification_questions = _build_clarification_questions(
            profile=profile,
            intent=intent,
            query=query,
        )
        if clarification_questions:
            logger.info(
                "faltan datos para responder con precision: intent=%s questions=%d",
                intent,
                len(clarification_questions),
            )
            return {
                "recommendations": [],
                "clarification_questions": clarification_questions,
            }

        expert = self._experts.get(intent)
        if expert is None:
            logger.warning("no hay experto registrado para intent=%s", intent)
            return {
                "recommendations": [],
                "clarification_questions": _fallback_questions_for_unwired_intent(
                    intent=intent,
                    query=query,
                ),
            }

        recommendations = expert.recommend(profile)
        logger.info("experto %s devolvio %d recomendaciones", intent, len(recommendations))
        return {"recommendations": recommendations}

    def _compose_response(self, state: OrchestratorState) -> dict[str, Any]:
        profile = state.get("profile")
        recommendations = state.get("recommendations") or []
        intent = state.get("intent", "unknown")
        response = _render_response(
            profile=profile,
            intent=intent,
            recommendations=recommendations,
            query=state.get("query", ""),
            clarification_questions=state.get("clarification_questions") or [],
        )
        return {"final_response": response}


_INTENT_HEADLINES: dict[Intent, str] = {
    "credit_card": "Tarjetas de credito sugeridas",
    "personal_loan": "Creditos de consumo sugeridos",
    "comparison": "Comparacion de productos",
    "unknown": "Productos financieros sugeridos",
}


def _render_response(
    *,
    profile: UserProfile | None,
    intent: Intent,
    recommendations: list[Recommendation],
    query: str,
    clarification_questions: list[str],
) -> str:
    """Formatea la respuesta final en Markdown con fallback de aclaracion."""
    lines: list[str] = [f"## {_INTENT_HEADLINES[intent]}"]

    if profile is not None:
        lines.append("")
        if clarification_questions:
            lines.append("**Contexto disponible**")
            lines.append(f"- Intent inferido: {intent}")
            lines.append(f"- Objetivo declarado: {profile.stated_goal}")
        else:
            lines.append("**Perfil detectado**")
            lines.append(f"- Ingreso liquido mensual: {_fmt_money(profile.monthly_income)}")
            lines.append(f"- Gasto mensual: {_fmt_money(profile.monthly_expenses)}")
            lines.append(f"- Ingreso disponible: {_fmt_money(profile.disposable_income)}")
            lines.append(f"- Objetivo declarado: {profile.stated_goal}")

    if not recommendations:
        lines.append("")
        if clarification_questions:
            lines.extend(_render_clarification_block(intent=intent, query=query))
            lines.append("")
            lines.append("**Para darte una mejor respuesta, necesito:**")
            lines.extend(f"- {question}" for question in clarification_questions)
        else:
            lines.append(
                "> Esta consulta requiere una ruta de comparacion mas especifica o mas contexto "
                "para responder con confianza."
            )
        return "\n".join(lines)

    for rec in sorted(recommendations, key=lambda recommendation: recommendation.rank):
        lines.append("")
        lines.append(f"### {rec.rank}. {rec.product.product_name} - {rec.product.bank_name}")
        lines.append(f"_Match score:_ {rec.match_score:.2f}")
        lines.append("")
        lines.append(rec.why_this_fits)
        if rec.caveats:
            lines.append("")
            lines.append("**Consideraciones**")
            lines.extend(f"- {caveat}" for caveat in rec.caveats)
        lines.append("")
        lines.append(f"<details><summary>Razonamiento ({rec.reasoning_trace.agent_name})</summary>")
        lines.append("")
        for step in rec.reasoning_trace.steps:
            lines.append(f"{step.step}. {step.description}")
        lines.append("")
        lines.append(f"_Conclusion:_ {rec.reasoning_trace.final_conclusion}")
        lines.append("</details>")

    return "\n".join(lines)


def _build_clarification_questions(
    *,
    profile: UserProfile,
    intent: Intent,
    query: str,
) -> list[str]:
    questions: list[str] = []
    normalized_query = query.lower()

    if profile.monthly_income <= 0:
        questions.append("Tu ingreso liquido mensual aproximado en CLP.")
    if profile.monthly_expenses <= 0:
        questions.append("Tus gastos fijos mensuales aproximados en CLP.")

    if intent == "credit_card":
        if not _mentions_card_priority(normalized_query):
            questions.append(
                "Que priorizas en la tarjeta: cashback, millas, cuotas sin interes, "
                "menor comision o compras internacionales."
            )
    elif intent == "personal_loan":
        if not _query_mentions_money_amount(normalized_query):
            questions.append("Cuanto dinero necesitas pedir aproximadamente.")
        if not _query_mentions_term(normalized_query):
            questions.append("A cuantos meses quieres pagar el prestamo.")
        if not _mentions_loan_purpose(normalized_query, profile.stated_goal.lower()):
            questions.append("Si el credito es para consolidar deudas, auto, estudio o libre uso.")
    elif intent == "comparison":
        if not _mentions_specific_products_or_categories(normalized_query):
            questions.append("Que dos alternativas quieres comparar exactamente.")
        questions.append(
            "Que criterio pesa mas para ti: menor costo total, cashback, cuota fija, "
            "monto disponible o flexibilidad."
        )
    else:
        questions.append(
            "Que producto estas buscando exactamente: tarjeta, prestamo personal o comparacion."
        )
        questions.append("Cual es tu objetivo principal con ese producto.")

    return _dedupe_preserve_order(questions)


def _fallback_questions_for_unwired_intent(*, intent: Intent, query: str) -> list[str]:
    normalized_query = query.lower()
    if intent == "comparison":
        questions = [
            "Que dos productos o alternativas quieres comparar exactamente.",
            "Que criterio pesa mas para ti: costo total, cashback, cuota fija, monto o plazo.",
        ]
        if "tarjeta" in normalized_query or "credito" in normalized_query:
            questions.append("Tu ingreso liquido mensual aproximado en CLP.")
            questions.append("Tus gastos fijos mensuales aproximados en CLP.")
        return questions
    if intent == "unknown":
        return [
            "Que producto financiero quieres evaluar exactamente.",
            "Cual es tu objetivo principal con ese producto.",
        ]
    return []


def _render_clarification_block(*, intent: Intent, query: str) -> list[str]:
    normalized_query = query.lower()
    if intent == "comparison" and "tarjeta" in normalized_query and (
        "credito" in normalized_query or "prestamo" in normalized_query
    ):
        return [
            "> Con lo que me diste solo puedo darte una regla general.",
            "> Una tarjeta conviene mas para compras recurrentes y beneficios como cashback o cuotas.",
            "> Un credito personal conviene mas cuando necesitas un monto definido con cuota y plazo fijos.",
        ]
    if intent == "credit_card":
        return [
            "> Puedo orientarte con tarjetas, pero me faltan algunos datos para no recomendarte algo fuera de perfil.",
        ]
    if intent == "personal_loan":
        return [
            "> Puedo orientarte con prestamos, pero me faltan algunos datos clave para estimar elegibilidad y plazo.",
        ]
    return [
        "> Me falta contexto para darte una recomendacion util y no inventar una respuesta poco confiable.",
    ]


def _mentions_card_priority(query: str) -> bool:
    keywords = (
        "cashback",
        "millas",
        "puntos",
        "cuotas",
        "comision",
        "sin costo",
        "viaje",
        "internacional",
        "beneficio",
        "vip",
    )
    return any(keyword in query for keyword in keywords)


def _mentions_specific_products_or_categories(query: str) -> bool:
    keywords = (
        "visa",
        "mastercard",
        "amex",
        "tarjeta",
        "credito personal",
        "prestamo",
        "black",
        "gold",
        "platinum",
        "signature",
    )
    return any(keyword in query for keyword in keywords)


def _query_mentions_money_amount(query: str) -> bool:
    patterns = (
        r"\b\d[\d\.\,]*\s*(clp|mil|millon|millones|lucas|k|uf)\b",
        r"\$\s*\d",
    )
    return any(re.search(pattern, query) for pattern in patterns)


def _query_mentions_term(query: str) -> bool:
    patterns = (
        r"\b\d+\s*(mes|meses|ano|anos|año|años)\b",
        r"\bplazo\b",
        r"\bcuotas\b",
    )
    return any(re.search(pattern, query) for pattern in patterns)


def _mentions_loan_purpose(query: str, stated_goal: str) -> bool:
    haystack = f"{query} {stated_goal}"
    keywords = (
        "consolidar",
        "deuda",
        "deudas",
        "auto",
        "vehiculo",
        "curso",
        "estudio",
        "estudios",
        "libre uso",
        "remodel",
        "compra grande",
    )
    return any(keyword in haystack for keyword in keywords)


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        output.append(item)
    return output


def _fmt_money(amount: Decimal) -> str:
    return f"${amount:,.0f}"
