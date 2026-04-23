"""App Streamlit para la demo publica de FinSage LATAM."""

from __future__ import annotations

import os
import re
import time
from typing import Any, TypedDict, cast

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


class ExamplePrompt(TypedDict):
    """Prompt sugerido para acelerar la demo."""

    label: str
    query: str


EXAMPLE_PROMPTS: tuple[ExamplePrompt, ...] = (
    {
        "label": "Cashback diario",
        "query": (
            "Gano 1.800.000 CLP, gasto 850.000 CLP y quiero una tarjeta con cashback "
            "para supermercado y compras del dia a dia. Me importa que la comision anual "
            "no sea tan alta."
        ),
    },
    {
        "label": "Prestamo concreto",
        "query": (
            "Gano 1.400.000 CLP, gasto 700.000 CLP y necesito un prestamo de 6 millones "
            "a 36 meses para consolidar deudas."
        ),
    },
    {
        "label": "Comparacion ambigua",
        "query": "Que es mejor una tarjeta o un credito personal?",
    },
)

RECRUITER_DEMO: ExamplePrompt = {
    "label": "Consulta recruiter",
    "query": (
        "Gano 1.800.000 CLP, gasto 850.000 CLP y quiero una tarjeta con cashback "
        "para supermercado y compras del dia a dia. Me importa que la comision anual "
        "no sea tan alta."
    ),
}


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


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


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _metric_card(label: str, value: str, caption: str) -> str:
    return f"""
    <div class="telemetry-card">
        <span>{label}</span>
        <strong>{value}</strong>
        <small>{caption}</small>
    </div>
    """


def _format_clp(value: Any) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "-"
    return f"${amount:,.0f} CLP"


def _format_decimal_rate(value: Any) -> str:
    try:
        amount = float(value)
    except (TypeError, ValueError):
        return "-"
    return f"{amount * 100:.1f}%"


def _escape_html(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _intent_theme(intent: str) -> str:
    themes = {
        "credit_card": "theme-card",
        "personal_loan": "theme-loan",
        "comparison": "theme-compare",
        "unknown": "theme-neutral",
    }
    return themes.get(intent, "theme-neutral")


def _bank_avatar(bank_name: str) -> tuple[str, str]:
    cleaned = " ".join(bank_name.split()).strip()
    initials = "".join(word[0] for word in cleaned.split()[:2]).upper() or "FS"
    palette = (
        ("#ff6a5f", "#ffb36b"),
        ("#0a66ff", "#43b2ff"),
        ("#11a56b", "#7fe3a6"),
        ("#7a5cff", "#b3a4ff"),
        ("#ff4f64", "#ff9a6b"),
    )
    index = sum(ord(char) for char in cleaned) % len(palette)
    start, end = palette[index]
    return initials[:2], f"linear-gradient(135deg, {start}, {end})"


def _score_pct(score: float) -> int:
    score = min(max(score, 0.0), 1.0)
    return round(score * 100)


def _clean_response_markdown(markdown: str) -> str:
    """Limpia etiquetas HTML de detalles y deja una lectura mas pulida."""

    def _replace_summary(match: re.Match[str]) -> str:
        summary = match.group(1).strip()
        return f"\n**{summary}**\n\n"

    cleaned = re.sub(
        r"<details>\s*<summary>(.*?)</summary>\s*",
        _replace_summary,
        markdown,
        flags=re.DOTALL,
    )
    cleaned = re.sub(r"</details>", "", cleaned, flags=re.DOTALL)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _inject_styles() -> None:
    """Inyecta la direccion visual del demo."""
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Manrope:wght@400;600;700;800&family=Space+Grotesk:wght@600;700&display=swap');

        :root {
            --ink: #182033;
            --muted: #667085;
            --line: rgba(24, 32, 51, 0.10);
            --accent: #ff4f64;
            --accent-2: #ffb02e;
            --blue: #0a66ff;
        }

        html, body, [class*="css"] {
            font-family: "Manrope", sans-serif;
        }

        .stApp {
            color: var(--ink);
            background:
                radial-gradient(circle at 16% 8%, rgba(255, 176, 46, 0.22), transparent 28%),
                radial-gradient(circle at 88% 4%, rgba(10, 102, 255, 0.16), transparent 30%),
                linear-gradient(145deg, #fff8ec 0%, #f6f8fb 44%, #eef4ff 100%);
        }

        section[data-testid="stSidebar"],
        button[data-testid="stSidebarCollapsedControl"] {
            display: none !important;
        }

        .block-container {
            padding-top: 2.15rem;
            max-width: 1180px;
        }

        .hero {
            position: relative;
            overflow: hidden;
            padding: 34px;
            border: 1px solid rgba(24, 32, 51, 0.10);
            border-radius: 34px;
            background:
                linear-gradient(135deg, rgba(255,255,255,0.94), rgba(255,255,255,0.72)),
                radial-gradient(circle at 92% 12%, rgba(255, 79, 100, 0.22), transparent 28%);
            box-shadow: 0 24px 80px rgba(24, 32, 51, 0.12);
            margin-bottom: 18px;
            animation: heroRise 0.9s ease-out both;
        }

        .hero:after {
            content: "";
            position: absolute;
            right: -120px;
            top: -120px;
            width: 290px;
            height: 290px;
            border-radius: 999px;
            background: conic-gradient(from 160deg, var(--accent), var(--accent-2), var(--blue), var(--accent));
            filter: blur(4px);
            opacity: 0.24;
            animation: orbFloat 10s ease-in-out infinite alternate;
        }

        .eyebrow {
            display: inline-flex;
            gap: 8px;
            align-items: center;
            padding: 7px 12px;
            border-radius: 999px;
            background: rgba(17, 165, 107, 0.12);
            color: #0b714d;
            font-weight: 800;
            font-size: 0.78rem;
            letter-spacing: 0.04em;
            text-transform: uppercase;
        }

        .hero h1 {
            font-family: "Space Grotesk", sans-serif;
            max-width: 860px;
            margin: 18px 0 12px;
            font-size: clamp(2.6rem, 6vw, 5.5rem);
            line-height: 0.88;
            letter-spacing: -0.075em;
            color: #172036;
        }

        .hero p {
            max-width: 760px;
            color: var(--muted);
            font-size: 1.04rem;
            line-height: 1.7;
            margin-bottom: 0;
        }

        .telemetry-strip {
            padding: 18px;
            margin: 14px 0 18px;
            border: 1px solid rgba(24, 32, 51, 0.10);
            border-radius: 26px;
            background: rgba(255,255,255,0.74);
            box-shadow: 0 14px 44px rgba(24, 32, 51, 0.08);
        }

        .telemetry-strip h3,
        .prompt-panel h3,
        .composer-shell h3,
        .section-shell h3 {
            margin: 0 0 4px;
            font-family: "Space Grotesk", sans-serif;
            letter-spacing: -0.035em;
        }

        .telemetry-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 12px;
            margin-top: 12px;
        }

        .telemetry-card {
            padding: 16px;
            border: 1px solid rgba(24, 32, 51, 0.10);
            border-radius: 22px;
            background: rgba(255,255,255,0.85);
        }

        .telemetry-card span {
            display: block;
            color: var(--muted);
            font-size: 0.72rem;
            font-weight: 800;
            letter-spacing: 0.06em;
            text-transform: uppercase;
        }

        .telemetry-card strong {
            display: block;
            margin-top: 6px;
            font-family: "Space Grotesk", sans-serif;
            font-size: 2rem;
            line-height: 1;
            color: #172036;
        }

        .telemetry-card small {
            display: block;
            margin-top: 6px;
            color: var(--muted);
        }

        .prompt-panel,
        .composer-shell,
        .section-shell {
            padding: 18px;
            border: 1px solid rgba(24, 32, 51, 0.10);
            border-radius: 26px;
            background: rgba(255,255,255,0.74);
            box-shadow: 0 14px 44px rgba(24, 32, 51, 0.08);
            margin-bottom: 18px;
            animation: cardRise 0.55s ease-out both;
        }

        .prompt-panel span,
        .composer-shell span,
        .section-shell span {
            color: var(--muted);
        }

        .profile-grid,
        .recommendation-grid {
            display: grid;
            gap: 14px;
        }

        .profile-grid {
            grid-template-columns: repeat(4, minmax(0, 1fr));
        }

        .recommendation-grid {
            grid-template-columns: repeat(1, minmax(0, 1fr));
            margin-top: 14px;
        }

        .profile-chip {
            padding: 16px;
            border-radius: 20px;
            border: 1px solid rgba(24, 32, 51, 0.08);
            background: rgba(255,255,255,0.84);
            animation: cardRise 0.65s ease-out both;
        }

        .profile-chip span {
            display: block;
            color: var(--muted);
            font-size: 0.78rem;
            font-weight: 800;
            letter-spacing: 0.05em;
            text-transform: uppercase;
        }

        .profile-chip strong {
            display: block;
            margin-top: 8px;
            color: #172036;
            font-size: 1.08rem;
            line-height: 1.35;
        }

        .recommendation-card {
            padding: 22px;
            border-radius: 28px;
            border: 1px solid rgba(24, 32, 51, 0.10);
            background:
                linear-gradient(180deg, rgba(255,255,255,0.94), rgba(255,255,255,0.78)),
                radial-gradient(circle at top right, rgba(10, 102, 255, 0.08), transparent 32%);
            box-shadow: 0 16px 52px rgba(24, 32, 51, 0.08);
            animation: cardRise 0.7s ease-out both;
        }

        .theme-card {
            border-color: rgba(255, 79, 100, 0.16);
            background:
                linear-gradient(180deg, rgba(255,255,255,0.94), rgba(255,245,246,0.84)),
                radial-gradient(circle at top right, rgba(255, 79, 100, 0.10), transparent 32%);
        }

        .theme-loan {
            border-color: rgba(10, 102, 255, 0.16);
            background:
                linear-gradient(180deg, rgba(255,255,255,0.94), rgba(243,248,255,0.84)),
                radial-gradient(circle at top right, rgba(10, 102, 255, 0.10), transparent 32%);
        }

        .theme-compare {
            border-color: rgba(255, 176, 46, 0.18);
            background:
                linear-gradient(180deg, rgba(255,255,255,0.94), rgba(255,250,240,0.84)),
                radial-gradient(circle at top right, rgba(255, 176, 46, 0.12), transparent 32%);
        }

        .theme-neutral {
            border-color: rgba(24, 32, 51, 0.10);
        }

        .rec-topline {
            display: flex;
            justify-content: space-between;
            gap: 12px;
            align-items: flex-start;
            margin-bottom: 12px;
        }

        .rec-header-left {
            display: flex;
            gap: 12px;
            align-items: center;
            flex: 1 1 auto;
        }

        .rec-rank {
            width: 42px;
            height: 42px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border-radius: 999px;
            background: linear-gradient(135deg, var(--accent), #ff8d6f);
            color: white;
            font-weight: 800;
            box-shadow: 0 10px 24px rgba(255, 79, 100, 0.22);
            flex: 0 0 auto;
        }

        .bank-avatar {
            width: 48px;
            height: 48px;
            display: inline-flex;
            align-items: center;
            justify-content: center;
            border-radius: 16px;
            color: #ffffff;
            font-weight: 800;
            letter-spacing: 0.04em;
            box-shadow: 0 10px 24px rgba(24, 32, 51, 0.14);
            flex: 0 0 auto;
        }

        .rec-heading {
            flex: 1 1 auto;
        }

        .rec-heading h4 {
            margin: 0 0 4px;
            font-family: "Space Grotesk", sans-serif;
            font-size: 1.36rem;
            letter-spacing: -0.04em;
            color: #172036;
        }

        .rec-heading p {
            margin: 0;
            color: var(--muted);
        }

        .rec-score {
            display: flex;
            align-items: center;
            gap: 10px;
            padding: 8px 10px;
            border-radius: 18px;
            background: rgba(10, 102, 255, 0.06);
            color: #1148ad;
            font-weight: 800;
            white-space: nowrap;
        }

        .score-ring {
            --score-angle: 180deg;
            width: 52px;
            height: 52px;
            border-radius: 999px;
            background:
                conic-gradient(#0a66ff var(--score-angle), rgba(10, 102, 255, 0.12) 0deg);
            display: inline-flex;
            align-items: center;
            justify-content: center;
            position: relative;
        }

        .score-ring:before {
            content: "";
            position: absolute;
            inset: 6px;
            background: #ffffff;
            border-radius: 999px;
        }

        .score-ring span {
            position: relative;
            z-index: 1;
            font-family: "Space Grotesk", sans-serif;
            font-size: 0.82rem;
            color: #172036;
        }

        .score-copy {
            display: flex;
            flex-direction: column;
            gap: 2px;
        }

        .score-copy strong {
            font-size: 0.96rem;
            color: #172036;
        }

        .score-copy small {
            color: var(--muted);
            font-size: 0.72rem;
        }

        .badge-row {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            margin-bottom: 14px;
        }

        .badge {
            display: inline-flex;
            align-items: center;
            padding: 7px 12px;
            border-radius: 999px;
            background: rgba(24, 32, 51, 0.06);
            color: #314056;
            font-size: 0.82rem;
            font-weight: 700;
        }

        .rec-why {
            color: #243146;
            font-size: 1rem;
            line-height: 1.75;
            margin-bottom: 14px;
        }

        .rec-meta {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 10px;
            margin-bottom: 14px;
        }

        .rec-meta div {
            padding: 12px;
            border-radius: 18px;
            border: 1px solid rgba(24, 32, 51, 0.08);
            background: rgba(255,255,255,0.70);
        }

        .rec-meta span {
            display: block;
            color: var(--muted);
            font-size: 0.76rem;
            text-transform: uppercase;
            font-weight: 800;
            letter-spacing: 0.05em;
        }

        .rec-meta strong {
            display: block;
            margin-top: 6px;
            color: #172036;
            font-size: 1rem;
        }

        .rec-section-title {
            margin: 14px 0 8px;
            color: #172036;
            font-weight: 800;
        }

        .reasoning-list,
        .caveat-list {
            margin: 0;
            padding-left: 18px;
            color: #334155;
        }

        .reasoning-conclusion {
            margin-top: 12px;
            padding: 12px 14px;
            border-left: 4px solid rgba(10, 102, 255, 0.30);
            background: rgba(10, 102, 255, 0.05);
            border-radius: 0 16px 16px 0;
            color: #23324a;
        }

        .recruiter-shell {
            padding: 20px;
            border-radius: 28px;
            border: 1px solid rgba(255, 79, 100, 0.14);
            background:
                linear-gradient(180deg, rgba(255,255,255,0.94), rgba(255,246,247,0.84)),
                radial-gradient(circle at top right, rgba(255, 79, 100, 0.12), transparent 34%);
            box-shadow: 0 16px 52px rgba(24, 32, 51, 0.08);
            margin-bottom: 18px;
            animation: cardRise 0.8s ease-out both;
        }

        .recruiter-shell h3 {
            margin: 0 0 6px;
            font-family: "Space Grotesk", sans-serif;
            letter-spacing: -0.04em;
        }

        .recruiter-shell p,
        .recruiter-shell small {
            color: var(--muted);
        }

        .recruiter-query {
            margin-top: 14px;
            padding: 16px 18px;
            border-radius: 22px;
            border: 1px solid rgba(24, 32, 51, 0.08);
            background: rgba(255,255,255,0.88);
            color: #243146;
            line-height: 1.65;
        }

        .insight-shell {
            padding: 18px 20px;
            border-radius: 26px;
            border: 1px solid rgba(24, 32, 51, 0.10);
            background: rgba(255,255,255,0.80);
            box-shadow: 0 14px 44px rgba(24, 32, 51, 0.08);
            animation: cardRise 0.6s ease-out both;
        }

        .insight-shell h3 {
            margin: 0 0 8px;
            font-family: "Space Grotesk", sans-serif;
            letter-spacing: -0.04em;
        }

        @keyframes heroRise {
            from {
                opacity: 0;
                transform: translateY(18px) scale(0.985);
            }
            to {
                opacity: 1;
                transform: translateY(0) scale(1);
            }
        }

        @keyframes cardRise {
            from {
                opacity: 0;
                transform: translateY(14px);
            }
            to {
                opacity: 1;
                transform: translateY(0);
            }
        }

        @keyframes orbFloat {
            from {
                transform: translate3d(0, 0, 0);
            }
            to {
                transform: translate3d(-22px, 18px, 0);
            }
        }

        div[data-testid="stChatMessage"] {
            border: 1px solid rgba(24, 32, 51, 0.10);
            border-radius: 24px;
            padding: 14px;
            background: rgba(255,255,255,0.72);
            box-shadow: 0 12px 34px rgba(24, 32, 51, 0.06);
            margin-bottom: 12px;
        }

        .stButton > button,
        div[data-testid="stFormSubmitButton"] button {
            border-radius: 999px;
            border: 1px solid rgba(24, 32, 51, 0.10);
            background: #ffffff;
            box-shadow: 0 10px 30px rgba(24, 32, 51, 0.08);
            font-weight: 800;
            color: #182033;
        }

        div[data-testid="stTextArea"] textarea {
            min-height: 126px;
            border-radius: 24px;
            border: 1.6px solid rgba(255, 79, 100, 0.42);
            background: rgba(255,255,255,0.94);
            box-shadow: 0 14px 40px rgba(255, 79, 100, 0.09);
            font-size: 1rem;
            line-height: 1.6;
            padding-top: 18px;
        }

        @media (max-width: 900px) {
            .hero {
                padding: 24px;
            }

            .telemetry-grid {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }

            .profile-grid,
            .rec-meta {
                grid-template-columns: repeat(2, minmax(0, 1fr));
            }
        }

        @media (max-width: 640px) {
            .profile-grid,
            .rec-meta {
                grid-template-columns: 1fr;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _render_hero() -> None:
    st.markdown(
        """
        <section class="hero">
            <div class="eyebrow">Decision engine financiero · Chile v1.0</div>
            <h1>FinSage LATAM convierte una consulta financiera en una recomendacion clara, util y auditable.</h1>
            <p>
                Una demo agéntica construida para portfolio: interpreta contexto, recupera productos,
                enruta a expertos y explica el por que de cada recomendacion sin caer en respuestas vacias.
            </p>
        </section>
        """,
        unsafe_allow_html=True,
    )


def _render_prompt_suggestions() -> None:
    st.markdown(
        """
        <div class="prompt-panel">
            <h3>Casos de prueba sugeridos</h3>
            <span>Usa un caso estructurado o uno ambiguo para ver si el sistema recomienda o pide mejores datos.</span>
        </div>
        """,
        unsafe_allow_html=True,
    )
    cols = st.columns(len(EXAMPLE_PROMPTS))
    for index, (col, example) in enumerate(zip(cols, EXAMPLE_PROMPTS, strict=True)):
        if col.button(example["label"], key=f"example_prompt_{index}", use_container_width=True):
            st.session_state.draft_query = example["query"]
            st.rerun()


def _seed_recruiter_demo() -> None:
    if st.session_state.get("recruiter_seeded"):
        return
    if cast(list[ChatTurn], st.session_state.history):
        st.session_state.recruiter_seeded = True
        return
    if not str(st.session_state.get("draft_query", "")).strip():
        st.session_state.draft_query = RECRUITER_DEMO["query"]
    st.session_state.recruiter_seeded = True


def _render_recruiter_panel() -> None:
    st.markdown(
        f"""
        <section class="recruiter-shell">
            <h3>Modo recruiter</h3>
            <p>
                La consulta demo ya viene cargada para que puedas mostrar el flujo mas convincente
                de la app sin preparar nada antes.
            </p>
            <div class="recruiter-query">{_escape_html(RECRUITER_DEMO["query"])}</div>
            <small>Incluye ingreso, gasto, objetivo y restriccion de costo: suficiente para que el sistema recomiende de inmediato.</small>
        </section>
        """,
        unsafe_allow_html=True,
    )
    if st.button("Recargar consulta demo", key="reload_recruiter_demo", use_container_width=True):
        st.session_state.draft_query = RECRUITER_DEMO["query"]
        st.rerun()


def _render_telemetry(api_url: str) -> None:
    metrics = _fetch_metrics(api_url)
    if metrics is None:
        st.warning("No pude leer la telemetria de la API en este momento.")
        return

    total_queries = _as_int(metrics.get("total_queries", 0))
    total_recommendations = _as_int(metrics.get("total_recommendations", 0))
    avg_latency = _as_float(metrics.get("avg_latency_ms", 0.0))
    p95_latency = _as_float(metrics.get("p95_latency_ms", 0.0))
    successful = _as_int(metrics.get("successful_queries", 0))
    failed = _as_int(metrics.get("failed_queries", 0))
    uptime = _as_float(metrics.get("uptime_seconds", 0.0))

    st.markdown(
        f"""
        <section class="telemetry-strip">
            <h3>Telemetria del demo</h3>
            <span>Visible solo cuando la necesitas. Uptime actual: {uptime / 60:.1f} min · refresco {time.strftime('%H:%M:%S')}</span>
            <div class="telemetry-grid">
                {_metric_card("Queries", str(total_queries), "consultas procesadas")}
                {_metric_card("Recomendaciones", str(total_recommendations), "productos sugeridos")}
                {_metric_card("Latencia media", f"{avg_latency:.0f} ms", f"p95 {p95_latency:.0f} ms")}
                {_metric_card("Exito / fallos", f"{successful} / {failed}", "estado operativo")}
            </div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def _render_profile_summary(payload: dict[str, Any]) -> None:
    profile = payload.get("profile")
    if not isinstance(profile, dict):
        return

    monthly_income = _format_clp(profile.get("monthly_income"))
    monthly_expenses = _format_clp(profile.get("monthly_expenses"))
    available = _format_clp(
        _as_float(profile.get("monthly_income")) - _as_float(profile.get("monthly_expenses"))
    )
    goal = _escape_html(_as_str(profile.get("stated_goal")) or "Sin objetivo declarado")
    intent = _as_str(payload.get("intent"))
    theme = _intent_theme(intent)

    st.markdown(
        f"""
        <section class="section-shell {theme}">
            <h3>Contexto detectado</h3>
            <span>Resumen rapido del caso antes del ranking.</span>
            <div class="profile-grid">
                <div class="profile-chip"><span>Ingreso mensual</span><strong>{monthly_income}</strong></div>
                <div class="profile-chip"><span>Gasto mensual</span><strong>{monthly_expenses}</strong></div>
                <div class="profile-chip"><span>Ingreso disponible</span><strong>{available}</strong></div>
                <div class="profile-chip"><span>Objetivo</span><strong>{goal}</strong></div>
            </div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def _build_badges(product: dict[str, Any]) -> str:
    badges: list[str] = []
    product_type = _as_str(product.get("product_type"))
    if product_type == "credit_card":
        badges.append("Tarjeta")
        tier = _as_str(product.get("tier"))
        if tier:
            badges.append(tier.capitalize())
        cashback_rate = product.get("cashback_rate")
        if cashback_rate not in (None, "", "null"):
            badges.append(f"Cashback {_format_decimal_rate(cashback_rate)}")
    elif product_type == "personal_loan":
        badges.append("Prestamo")
        cae = product.get("cae")
        if cae not in (None, "", "null"):
            badges.append(f"CAE {_format_decimal_rate(cae)}")

    if product.get("international") is True:
        badges.append("Internacional")

    return "".join(
        f'<span class="badge">{_escape_html(badge)}</span>'
        for badge in badges
    )


def _build_meta(product: dict[str, Any]) -> str:
    product_type = _as_str(product.get("product_type"))
    if product_type == "credit_card":
        first_label = "Comision anual"
        first_value = _format_clp(product.get("annual_fee"))
        second_label = "Tasa anual"
        second_value = _format_decimal_rate(product.get("interest_rate_annual"))
        third_label = "Renta minima"
        third_value = _format_clp(product.get("min_income_required"))
    else:
        first_label = "Monto maximo"
        first_value = _format_clp(product.get("amount_max"))
        second_label = "CAE"
        second_value = _format_decimal_rate(product.get("cae"))
        third_label = "Plazo maximo"
        third_value = f"{_as_int(product.get('term_months_max'))} meses"

    items = (
        (first_label, first_value),
        (second_label, second_value),
        (third_label, third_value),
    )
    return "".join(
        f"""
        <div>
            <span>{_escape_html(label)}</span>
            <strong>{_escape_html(value)}</strong>
        </div>
        """
        for label, value in items
    )


def _render_recommendation_card(rec: dict[str, Any], index: int) -> None:
    product = rec.get("product")
    if not isinstance(product, dict):
        return

    title = _escape_html(_as_str(product.get("product_name")) or "Producto sugerido")
    bank = _escape_html(_as_str(product.get("bank_name")) or "Banco")
    why = _escape_html(_as_str(rec.get("why_this_fits")))
    score = _as_float(rec.get("match_score"))
    score_pct = _score_pct(score)
    badges = _build_badges(product)
    meta = _build_meta(product)
    caveats = rec.get("caveats")
    reasoning_trace = rec.get("reasoning_trace")
    product_type = _as_str(product.get("product_type"))
    theme = _intent_theme(product_type)
    initials, avatar_background = _bank_avatar(_as_str(product.get("bank_name")))

    st.markdown(
        f"""
        <section class="recommendation-card {theme}">
            <div class="rec-topline">
                <div class="rec-header-left">
                    <div class="rec-rank">{index}</div>
                    <div class="bank-avatar" style="background:{avatar_background};">{_escape_html(initials)}</div>
                    <div class="rec-heading">
                        <h4>{title}</h4>
                        <p>{bank}</p>
                    </div>
                </div>
                <div class="rec-score">
                    <div class="score-ring" style="--score-angle: {score_pct * 3.6:.0f}deg;">
                        <span>{score_pct}</span>
                    </div>
                    <div class="score-copy">
                        <strong>Match score</strong>
                        <small>{score:.2f} / 1.00</small>
                    </div>
                </div>
            </div>
            <div class="badge-row">{badges}</div>
            <div class="rec-why">{why}</div>
            <div class="rec-meta">{meta}</div>
        </section>
        """,
        unsafe_allow_html=True,
    )

    if isinstance(caveats, list) and caveats:
        st.markdown("**Consideraciones**")
        st.markdown(
            "\n".join(f"- {_as_str(caveat)}" for caveat in caveats if _as_str(caveat).strip())
        )

    if isinstance(reasoning_trace, dict):
        steps = reasoning_trace.get("steps")
        if isinstance(steps, list) and steps:
            st.markdown("**Por que aparece en el ranking**")
            reasoning_lines = []
            for step in steps:
                if not isinstance(step, dict):
                    continue
                description = _as_str(step.get("description")).strip()
                if description:
                    reasoning_lines.append(f"- {description}")
            if reasoning_lines:
                st.markdown("\n".join(reasoning_lines))

        conclusion = _as_str(reasoning_trace.get("final_conclusion")).strip()
        if conclusion:
            st.markdown(
                f'<div class="reasoning-conclusion"><strong>Lectura final:</strong> {_escape_html(conclusion)}</div>',
                unsafe_allow_html=True,
            )


def _render_recommendations(payload: dict[str, Any]) -> None:
    recommendations = payload.get("recommendations")
    if not isinstance(recommendations, list) or not recommendations:
        return

    _render_profile_summary(payload)
    st.markdown(
        """
        <section class="section-shell">
            <h3>Productos sugeridos</h3>
            <span>Ordenados segun ajuste al perfil, objetivo declarado y restricciones detectadas.</span>
        </section>
        """,
        unsafe_allow_html=True,
    )
    for index, rec in enumerate(recommendations, start=1):
        if isinstance(rec, dict):
            _render_recommendation_card(rec, index)


def _render_assistant_content(content: str, payload: dict[str, Any] | None) -> None:
    if payload and payload.get("recommendations"):
        _render_recommendations(payload)
        return
    if payload and isinstance(payload, dict):
        intent = _intent_theme(_as_str(payload.get("intent")))
        st.markdown(
            f'<section class="insight-shell {intent}"><h3>Lectura del caso</h3></section>',
            unsafe_allow_html=True,
        )
    st.markdown(_clean_response_markdown(content))


def _render_history() -> None:
    history = cast(list[ChatTurn], st.session_state.history)
    for turn in history:
        with st.chat_message(turn["role"]):
            _render_assistant_content(turn["content"], turn.get("payload"))


def _render_composer() -> str | None:
    st.markdown(
        """
        <section class="composer-shell">
            <h3>Haz tu consulta</h3>
            <span>Describe ingresos, gastos, objetivo y cualquier restriccion relevante. Mientras mas contexto des, mejor decide.</span>
        </section>
        """,
        unsafe_allow_html=True,
    )

    with st.form("query_form", clear_on_submit=True, border=False):
        st.text_area(
            "Consulta",
            key="draft_query",
            label_visibility="collapsed",
            placeholder=(
                "Ej: Gano 1.500.000 CLP, gasto 700.000 CLP y busco una tarjeta con cashback "
                "para compras del dia a dia, pero sin una comision anual demasiado alta."
            ),
        )
        submit_col, info_col = st.columns([1, 4])
        submitted = submit_col.form_submit_button("Analizar caso", use_container_width=True)
        info_col.caption(
            "FinSage recomienda productos reales o te dice exactamente que informacion falta."
        )

    if not submitted:
        return None

    query = str(st.session_state.get("draft_query", "")).strip()
    if not query:
        st.warning("Escribe una consulta antes de enviarla.")
        return None

    return query


def _safe_detail(response: httpx.Response) -> str:
    try:
        body = response.json()
    except ValueError:
        return response.text or "sin detalle"
    if isinstance(body, dict) and "detail" in body:
        return str(body["detail"])
    return str(body)


def _submit_query(api_url: str, query: str) -> None:
    history = cast(list[ChatTurn], st.session_state.history)
    history.append({"role": "user", "content": query, "payload": None})

    with st.chat_message("user"):
        st.markdown(query)

    with st.chat_message("assistant"):
        with st.spinner("Orquestando perfil, retrieval y expertos financieros..."):
            try:
                payload = _post_recommend(api_url, query)
            except httpx.HTTPStatusError as exc:
                detail = _safe_detail(exc.response)
                error_md = f"**Error {exc.response.status_code}** - {detail}"
                st.error(error_md)
                history.append({"role": "assistant", "content": error_md, "payload": None})
                st.rerun()
            except httpx.HTTPError as exc:
                error_md = f"**No pude alcanzar la API** ({exc.__class__.__name__}): {exc}"
                st.error(error_md)
                history.append({"role": "assistant", "content": error_md, "payload": None})
                st.rerun()

        markdown = payload.get("response_markdown") or "_Sin respuesta del orchestrator._"
        cleaned_markdown = _clean_response_markdown(markdown)
        _render_assistant_content(cleaned_markdown, payload)
        history.append({"role": "assistant", "content": cleaned_markdown, "payload": payload})
        st.rerun()


def main() -> None:
    st.set_page_config(
        page_title="FinSage LATAM",
        page_icon="F",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    _inject_styles()

    if "history" not in st.session_state:
        st.session_state.history = []
    if "api_url" not in st.session_state:
        st.session_state.api_url = DEFAULT_API_URL
    if "show_telemetry" not in st.session_state:
        st.session_state.show_telemetry = False
    if "draft_query" not in st.session_state:
        st.session_state.draft_query = ""
    if "recruiter_seeded" not in st.session_state:
        st.session_state.recruiter_seeded = False

    api_url = cast(str, st.session_state.api_url)
    _seed_recruiter_demo()

    _render_hero()

    top_left, top_right = st.columns([4, 1])
    top_left.caption("Demo publica de asesoria financiera agentica para portfolio.")
    telemetry_label = "Ocultar telemetria" if st.session_state.show_telemetry else "Ver telemetria"
    if top_right.button(telemetry_label, use_container_width=True):
        st.session_state.show_telemetry = not st.session_state.show_telemetry
        st.rerun()

    if st.session_state.show_telemetry:
        _render_telemetry(api_url)

    if not cast(list[ChatTurn], st.session_state.history):
        _render_recruiter_panel()
        _render_prompt_suggestions()

    query = _render_composer()
    _render_history()

    if query:
        _submit_query(api_url, query)


if __name__ == "__main__":
    main()
