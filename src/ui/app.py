"""App Streamlit para la demo publica de FinSage LATAM."""

from __future__ import annotations

import os
import time
from typing import Any, TypedDict

import httpx
import streamlit as st

DEFAULT_API_URL = os.getenv("FINSAGE_API_URL", "http://127.0.0.1:8000")
REQUEST_TIMEOUT_SECONDS = 60.0
METRICS_TIMEOUT_SECONDS = 3.0


class ChatTurn(TypedDict):
    """Turno visible en el chat."""

    role: str
    content: str
    payload: dict[str, Any] | None


def _post_recommend(api_url: str, query: str) -> dict[str, Any]:
    """Postea una consulta al backend y devuelve el JSON parseado."""
    response = httpx.post(
        f"{api_url.rstrip('/')}/recommend",
        json={"query": query},
        timeout=REQUEST_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise ValueError(f"respuesta inesperada del backend: {type(data).__name__}")
    return data


def _fetch_metrics(api_url: str) -> dict[str, Any] | None:
    """Lee el snapshot de /metrics o None si la API no responde."""
    try:
        response = httpx.get(
            f"{api_url.rstrip('/')}/metrics",
            timeout=METRICS_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _render_sidebar(api_url: str, *, container: Any | None = None) -> None:
    """Renderiza el sidebar de telemetria."""
    sidebar = container or st.sidebar

    sidebar.header("FinSage · Telemetria")
    sidebar.caption(
        "Metricas leidas desde /metrics de la API. "
        "El backend emite los mismos contadores como spans cuando hay token."
    )

    metrics = _fetch_metrics(api_url)
    if metrics is None:
        sidebar.error("API no alcanzable")
        return

    col_a, col_b = sidebar.columns(2)
    col_a.metric("Queries", metrics.get("total_queries", 0))
    col_b.metric("Recomendaciones", metrics.get("total_recommendations", 0))

    col_c, col_d = sidebar.columns(2)
    col_c.metric("Lat. media (ms)", f"{metrics.get('avg_latency_ms', 0.0):.0f}")
    col_d.metric("Lat. p95 (ms)", f"{metrics.get('p95_latency_ms', 0.0):.0f}")

    col_e, col_f = sidebar.columns(2)
    col_e.metric("Exito", metrics.get("successful_queries", 0))
    col_f.metric("Fallos", metrics.get("failed_queries", 0))

    intents = metrics.get("intents") or {}
    if any(intents.values()):
        sidebar.subheader("Intents")
        sidebar.bar_chart(intents)

    uptime = metrics.get("uptime_seconds", 0.0)
    sidebar.caption(f"Uptime: {uptime / 60:.1f} min · refresco: {time.strftime('%H:%M:%S')}")


def _render_history() -> None:
    for turn in st.session_state.history:
        with st.chat_message(turn["role"]):
            st.markdown(turn["content"])
            payload = turn.get("payload")
            if payload and payload.get("recommendations"):
                with st.expander("Ver JSON estructurado"):
                    st.json(payload)


def _safe_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text or "sin detalle"
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])
    return str(body)


def main() -> None:
    st.set_page_config(page_title="FinSage LATAM", page_icon="💸", layout="wide")
    st.title("FinSage LATAM")
    st.caption("Asesor financiero agentico · Chile v1.0")

    if "history" not in st.session_state:
        st.session_state.history = []
    if "api_url" not in st.session_state:
        st.session_state.api_url = DEFAULT_API_URL

    api_url = st.sidebar.text_input(
        "URL de la API",
        value=st.session_state.api_url,
        key="api_url_input",
    )
    api_url = api_url or st.session_state.api_url
    st.session_state.api_url = api_url

    _render_sidebar(api_url)
    _render_history()

    prompt = st.chat_input("Cuentame tu situacion financiera y que buscas...")
    if not prompt:
        return

    st.session_state.history.append({"role": "user", "content": prompt, "payload": None})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Consultando agentes..."):
            try:
                payload = _post_recommend(api_url, prompt)
            except httpx.HTTPStatusError as exc:
                detail = _safe_detail(exc.response)
                error_md = f"**Error {exc.response.status_code}** - {detail}"
                st.error(error_md)
                st.session_state.history.append(
                    {"role": "assistant", "content": error_md, "payload": None}
                )
                st.rerun()
            except httpx.HTTPError as exc:
                error_md = f"**No pude alcanzar la API** ({exc.__class__.__name__}): {exc}"
                st.error(error_md)
                st.session_state.history.append(
                    {"role": "assistant", "content": error_md, "payload": None}
                )
                st.rerun()

        markdown = payload.get("response_markdown") or "_Sin respuesta del orchestrator._"
        st.markdown(markdown)
        if payload.get("recommendations"):
            with st.expander("Ver JSON estructurado"):
                st.json(payload)
        st.session_state.history.append(
            {"role": "assistant", "content": markdown, "payload": payload}
        )
        st.rerun()


if __name__ == "__main__":
    main()
