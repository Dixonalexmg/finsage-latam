"""Orchestrator principal que coordina agentes vía LangGraph.

Flujo del grafo (ver ``CLAUDE.md`` §Orchestrator):

```mermaid
flowchart TD
    START([START]) --> extract_profile[extract_profile<br/>ProfileAnalyst → UserProfile]
    extract_profile --> classify_intent[classify_intent<br/>tarjeta / préstamo / comparación]
    classify_intent --> route_to_expert[route_to_expert<br/>dispatch al sub-agente]
    route_to_expert --> compose_response[compose_response<br/>respuesta + reasoning_trace]
    compose_response --> END([END])
```

El grafo es lineal por diseño v1.0: cada nodo produce un delta sobre el estado
que el siguiente consume. ``route_to_expert`` no fork-ea en paralelo — usa un
registro ``Intent → Expert`` y llama al experto correspondiente.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any, Protocol, TypedDict, cast

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from src.agents.base import ChatMessage, StructuredLLMClient
from src.agents.profile_analyst import ProfileAnalyst
from src.models.schemas import Intent, Recommendation, UserProfile

logger = logging.getLogger(__name__)


class Expert(Protocol):
    """Contrato mínimo que deben cumplir los sub-agentes de dominio (CardAdvisor, LoanAdvisor, ...)."""

    def recommend(self, profile: UserProfile) -> list[Recommendation]:
        """Devuelve una lista ranqueada de recomendaciones para ``profile``."""
        ...


class OrchestratorState(TypedDict, total=False):
    """Estado compartido entre nodos del grafo.

    Todos los campos son opcionales porque cada nodo escribe solo su delta; el
    estado se hidrata progresivamente desde ``START`` hasta ``END``.
    """

    query: str
    conversation: list[ChatMessage]
    profile: UserProfile
    intent: Intent
    recommendations: list[Recommendation]
    final_response: str


class Orchestrator:
    """Coordina el pipeline ``extract_profile → classify_intent → route → compose``.

    Los sub-agentes expertos se inyectan vía ``experts`` para permitir registros
    parciales durante el desarrollo (v1.0 aún no wirea ``CardAdvisor`` etc.).
    """

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
        """Grafo compilado (útil para inspección/diagramas en tests y docs)."""
        return self._graph

    def run(self, query: str) -> OrchestratorState:
        """Ejecuta el pipeline completo para una consulta del usuario."""
        if not query.strip():
            raise ValueError("query no puede ser vacío")
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
            raise RuntimeError("classify_intent corrió antes que extract_profile")
        intent: Intent = profile.intent
        logger.info("intent classified as %s", intent)
        return {"intent": intent}

    def _route_to_expert(self, state: OrchestratorState) -> dict[str, Any]:
        profile = state.get("profile")
        intent = state.get("intent", "unknown")
        if profile is None:
            raise RuntimeError("route_to_expert corrió sin perfil en el estado")
        expert = self._experts.get(intent)
        if expert is None:
            logger.warning("no hay experto registrado para intent=%s", intent)
            return {"recommendations": []}
        recommendations = expert.recommend(profile)
        logger.info("experto %s devolvió %d recomendaciones", intent, len(recommendations))
        return {"recommendations": recommendations}

    def _compose_response(self, state: OrchestratorState) -> dict[str, Any]:
        profile = state.get("profile")
        recommendations = state.get("recommendations") or []
        intent = state.get("intent", "unknown")
        response = _render_response(profile=profile, intent=intent, recommendations=recommendations)
        return {"final_response": response}


# ---------------------------------------------------------------------------
# Render determinista de la respuesta final
# ---------------------------------------------------------------------------


_INTENT_HEADLINES: dict[Intent, str] = {
    "credit_card": "Tarjetas de crédito sugeridas",
    "personal_loan": "Créditos de consumo sugeridos",
    "comparison": "Comparación de productos",
    "unknown": "Productos financieros sugeridos",
}


def _render_response(
    *,
    profile: UserProfile | None,
    intent: Intent,
    recommendations: list[Recommendation],
) -> str:
    """Formatea la respuesta final en Markdown con reasoning trace auditable.

    Se mantiene determinista (sin LLM) para que ``compose_response`` sea testable
    sin mocks adicionales; un futuro `ResponseWriter` con structured output puede
    reemplazarlo respetando ``CLAUDE.md`` §Restricciones.
    """
    lines: list[str] = [f"## {_INTENT_HEADLINES[intent]}"]

    if profile is not None:
        lines.append("")
        lines.append("**Perfil detectado**")
        lines.append(f"- Ingreso líquido mensual: {_fmt_money(profile.monthly_income)}")
        lines.append(f"- Gasto mensual: {_fmt_money(profile.monthly_expenses)}")
        lines.append(f"- Ingreso disponible: {_fmt_money(profile.disposable_income)}")
        lines.append(f"- Objetivo declarado: {profile.stated_goal}")

    if not recommendations:
        lines.append("")
        lines.append(
            "> No hay recomendaciones disponibles todavía para este intent. "
            "Registra un experto en ``Orchestrator.experts`` para habilitar esta ruta."
        )
        return "\n".join(lines)

    for rec in sorted(recommendations, key=lambda r: r.rank):
        lines.append("")
        lines.append(f"### {rec.rank}. {rec.product.product_name} — {rec.product.bank_name}")
        lines.append(f"_Match score:_ {rec.match_score:.2f}")
        lines.append("")
        lines.append(rec.why_this_fits)
        if rec.caveats:
            lines.append("")
            lines.append("**Consideraciones**")
            lines.extend(f"- {c}" for c in rec.caveats)
        lines.append("")
        lines.append(f"<details><summary>Razonamiento ({rec.reasoning_trace.agent_name})</summary>")
        lines.append("")
        for step in rec.reasoning_trace.steps:
            lines.append(f"{step.step}. {step.description}")
        lines.append("")
        lines.append(f"_Conclusión:_ {rec.reasoning_trace.final_conclusion}")
        lines.append("</details>")

    return "\n".join(lines)


def _fmt_money(amount: Decimal) -> str:
    return f"${amount:,.0f}"
