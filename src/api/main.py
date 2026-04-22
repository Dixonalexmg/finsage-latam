"""FastAPI entrypoint for the FinSage demo."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Annotated

import logfire
import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from starlette.responses import Response
from starlette.types import Message

from src.agents.orchestrator import Orchestrator
from src.models.schemas import Intent, Recommendation, UserProfile
from src.runtime import (
    FinSageRuntime,
    RuntimeConfigurationError,
    RuntimeInitializationError,
    build_runtime,
)

logger = logging.getLogger(__name__)

APP_NAME = "finsage-api"
APP_VERSION = "0.1.0"
DEFAULT_MAX_REQUEST_SIZE_BYTES = 16 * 1024
_RECOMMENDATION_ENV_VARS = ("GEMINI_API_KEY",)


class RecommendRequest(BaseModel):
    """Request body for ``POST /recommend``."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(..., min_length=1, max_length=4000, description="Consulta del usuario.")


class RecommendResponse(BaseModel):
    """Structured response for recommendation requests."""

    model_config = ConfigDict(extra="forbid")

    intent: Intent
    profile: UserProfile | None = None
    recommendations: list[Recommendation] = Field(default_factory=list)
    response_markdown: str = Field(..., description="Markdown render del orchestrator.")
    latency_ms: float = Field(..., ge=0)


class HealthResponse(BaseModel):
    """Healthcheck enriquecido para demos publicas y readiness manual."""

    model_config = ConfigDict(extra="forbid")

    status: str
    service: str
    version: str
    recommendations_ready: bool
    missing_env: list[str] = Field(default_factory=list)
    cors_allowlist_configured: bool
    request_size_limit_bytes: int = Field(..., gt=0)
    logfire_mode: str
    logfire_detail: str


class IntentBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid")

    credit_card: int = 0
    personal_loan: int = 0
    comparison: int = 0
    unknown: int = 0


class MetricsSnapshot(BaseModel):
    """In-process snapshot consumed by the Streamlit sidebar."""

    model_config = ConfigDict(extra="forbid")

    total_queries: int
    successful_queries: int
    failed_queries: int
    total_recommendations: int
    avg_latency_ms: float
    p95_latency_ms: float
    intents: IntentBreakdown
    uptime_seconds: float


_LATENCY_WINDOW = 200


@dataclass
class _MetricsState:
    """In-memory counters for the current API process."""

    total_queries: int = 0
    successful_queries: int = 0
    failed_queries: int = 0
    total_recommendations: int = 0
    intent_counts: dict[str, int] = field(default_factory=dict)
    latencies_ms: list[float] = field(default_factory=list)
    started_at: float = field(default_factory=time.monotonic)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def record(self, *, latency_ms: float, intent: str | None, n_recommendations: int) -> None:
        with self.lock:
            self.total_queries += 1
            self.successful_queries += 1
            self.total_recommendations += n_recommendations
            self.latencies_ms.append(latency_ms)
            if len(self.latencies_ms) > _LATENCY_WINDOW:
                del self.latencies_ms[: len(self.latencies_ms) - _LATENCY_WINDOW]
            if intent is not None:
                self.intent_counts[intent] = self.intent_counts.get(intent, 0) + 1

    def record_failure(self) -> None:
        with self.lock:
            self.total_queries += 1
            self.failed_queries += 1

    def snapshot(self) -> MetricsSnapshot:
        with self.lock:
            latencies = sorted(self.latencies_ms)
            avg = sum(latencies) / len(latencies) if latencies else 0.0
            p95 = latencies[int(0.95 * (len(latencies) - 1))] if latencies else 0.0
            intents = IntentBreakdown(
                credit_card=self.intent_counts.get("credit_card", 0),
                personal_loan=self.intent_counts.get("personal_loan", 0),
                comparison=self.intent_counts.get("comparison", 0),
                unknown=self.intent_counts.get("unknown", 0),
            )
            return MetricsSnapshot(
                total_queries=self.total_queries,
                successful_queries=self.successful_queries,
                failed_queries=self.failed_queries,
                total_recommendations=self.total_recommendations,
                avg_latency_ms=avg,
                p95_latency_ms=p95,
                intents=intents,
                uptime_seconds=time.monotonic() - self.started_at,
            )


@dataclass(frozen=True)
class _LogfireStatus:
    mode: str
    detail: str


_metrics = _MetricsState()
_orchestrator: Orchestrator | None = None
_runtime: FinSageRuntime | None = None
_runtime_lock = threading.RLock()
_logfire_status = _LogfireStatus(
    mode="manual_spans_only",
    detail="Logfire pendiente de configuracion durante el arranque.",
)


def get_runtime() -> FinSageRuntime:
    """Builds the real FinSage runtime once and reuses it."""
    global _runtime
    if _runtime is None:
        with _runtime_lock:
            if _runtime is None:
                _runtime = build_runtime()
    return _runtime


def get_orchestrator() -> Orchestrator:
    """Dependency injection hook used by FastAPI and tests."""
    global _orchestrator
    if _orchestrator is None:
        with _runtime_lock:
            if _orchestrator is None:
                _orchestrator = get_runtime().orchestrator
    return _orchestrator


def get_orchestrator_dependency() -> Orchestrator:
    """Wrapper FastAPI que convierte errores de runtime en respuestas HTTP claras."""
    try:
        return get_orchestrator()
    except RuntimeConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except RuntimeInitializationError as exc:
        logger.exception("runtime real no pudo inicializarse durante la resolucion de dependencias")
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        logger.exception("fallo resolviendo el orchestrator real")
        provider_detail = _provider_error_detail(exc)
        if provider_detail is not None:
            raise HTTPException(status_code=503, detail=provider_detail) from exc
        raise HTTPException(status_code=503, detail="orchestrator unavailable") from exc


def set_orchestrator(orchestrator: Orchestrator | None) -> None:
    """Injects a prebuilt orchestrator for tests or custom startup."""
    global _orchestrator, _runtime
    with _runtime_lock:
        _orchestrator = orchestrator
        _runtime = None


def set_runtime(runtime: FinSageRuntime | None) -> None:
    """Injects a full runtime for tests or custom startup."""
    global _runtime, _orchestrator
    with _runtime_lock:
        _runtime = runtime
        _orchestrator = runtime.orchestrator if runtime is not None else None


def _get_cors_origins() -> list[str]:
    raw_origins = os.getenv("FINSAGE_CORS_ALLOW_ORIGINS", "")
    return [origin.strip() for origin in raw_origins.split(",") if origin.strip()]


def _get_missing_recommendation_env() -> list[str]:
    return [name for name in _RECOMMENDATION_ENV_VARS if not os.getenv(name)]


def _get_request_size_limit_bytes() -> int:
    raw_value = os.getenv("FINSAGE_MAX_REQUEST_SIZE_BYTES", "").strip()
    if not raw_value:
        return DEFAULT_MAX_REQUEST_SIZE_BYTES
    try:
        value = int(raw_value)
    except ValueError:
        logger.warning(
            "FINSAGE_MAX_REQUEST_SIZE_BYTES=%r invalido; uso default=%s",
            raw_value,
            DEFAULT_MAX_REQUEST_SIZE_BYTES,
        )
        return DEFAULT_MAX_REQUEST_SIZE_BYTES
    if value <= 0:
        logger.warning(
            "FINSAGE_MAX_REQUEST_SIZE_BYTES=%s invalido; uso default=%s",
            value,
            DEFAULT_MAX_REQUEST_SIZE_BYTES,
        )
        return DEFAULT_MAX_REQUEST_SIZE_BYTES
    return value


def _provider_error_detail(exc: Exception) -> str | None:
    message = str(exc).lower()
    if "resource_exhausted" in message or "quota" in message or "rate limit exceeded" in message:
        return (
            "Gemini API agoto la cuota del free tier o esta rate-limited para este proyecto. "
            "Espera a que la cuota se recupere o reduce el trafico de la demo."
        )
    if "api key not valid" in message or "invalid api key" in message:
        return "Gemini API rechazo la credencial. Verifica GEMINI_API_KEY."
    return None


def _configure_logfire(app: FastAPI) -> None:
    """Configures Logfire in if-token-present mode."""
    global _logfire_status
    logfire.configure(
        service_name="finsage-api",
        send_to_logfire="if-token-present",
        console=False,
    )
    try:
        logfire.instrument_fastapi(app, capture_headers=False)
    except (RuntimeError, ImportError) as exc:
        _logfire_status = _LogfireStatus(
            mode="manual_spans_only",
            detail=(
                "Auto-instrumentacion FastAPI no disponible; "
                f"continuo con spans manuales ({exc.__class__.__name__})."
            ),
        )
        logger.warning("logfire.instrument_fastapi no disponible (%s); continuo sin auto-instrument", exc)
    else:
        _logfire_status = _LogfireStatus(
            mode="fastapi_auto_instrumentation",
            detail="Auto-instrumentacion FastAPI habilitada; el envio sigue siendo if-token-present.",
        )


def _configure_cors(app: FastAPI) -> None:
    """Configura CORS desde ``FINSAGE_CORS_ALLOW_ORIGINS`` cuando aplica."""
    origins = _get_cors_origins()
    if not origins:
        logger.info("CORS deshabilitado; asumo deploy same-origin para la demo.")
        return
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )
    logger.info("CORS configurado con %s origen(es) permitidos.", len(origins))


def _configure_request_limits(app: FastAPI) -> None:
    """Bloquea payloads demasiado grandes antes de que entren a la capa de agentes."""

    @app.middleware("http")
    async def enforce_request_size(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.method not in {"POST", "PUT", "PATCH"}:
            return await call_next(request)

        max_bytes = _get_request_size_limit_bytes()
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > max_bytes:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": f"request body too large (max {max_bytes} bytes)"},
                    )
            except ValueError:
                logger.warning("Content-Length invalido recibido: %r", content_length)

        body = await request.body()
        if len(body) > max_bytes:
            return JSONResponse(
                status_code=413,
                content={"detail": f"request body too large (max {max_bytes} bytes)"},
            )

        async def receive() -> Message:
            return {"type": "http.request", "body": body, "more_body": False}

        buffered_request = Request(request.scope, receive=receive)
        return await call_next(buffered_request)


def create_app() -> FastAPI:
    """Factory used in tests and alternative startup flows."""
    app = FastAPI(
        title="FinSage LATAM API",
        version=APP_VERSION,
        description="Recomendador agentico de productos financieros (Chile v1.0).",
    )
    _configure_logfire(app)
    _configure_cors(app)
    _configure_request_limits(app)

    @app.get("/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        missing_env = _get_missing_recommendation_env()
        return HealthResponse(
            status="ok",
            service=APP_NAME,
            version=APP_VERSION,
            recommendations_ready=not missing_env,
            missing_env=missing_env,
            cors_allowlist_configured=bool(_get_cors_origins()),
            request_size_limit_bytes=_get_request_size_limit_bytes(),
            logfire_mode=_logfire_status.mode,
            logfire_detail=_logfire_status.detail,
        )

    @app.get("/metrics", response_model=MetricsSnapshot)
    def metrics() -> MetricsSnapshot:
        return _metrics.snapshot()

    @app.post("/recommend", response_model=RecommendResponse)
    def recommend(
        request: RecommendRequest,
        orchestrator: Annotated[Orchestrator, Depends(get_orchestrator_dependency)],
    ) -> RecommendResponse:
        with logfire.span("api.recommend") as span:
            span.set_attribute("query_length", len(request.query))
            start = time.perf_counter()
            try:
                state = orchestrator.run(request.query)
            except ValueError as exc:
                _metrics.record_failure()
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            except RuntimeConfigurationError as exc:
                _metrics.record_failure()
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except RuntimeInitializationError as exc:
                _metrics.record_failure()
                logger.exception("runtime real no pudo inicializarse")
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except Exception as exc:
                _metrics.record_failure()
                logger.exception("orchestrator fallo para query=%r", request.query)
                provider_detail = _provider_error_detail(exc)
                if provider_detail is not None:
                    raise HTTPException(status_code=503, detail=provider_detail) from exc
                raise HTTPException(status_code=503, detail="orchestrator unavailable") from exc

            latency_ms = (time.perf_counter() - start) * 1000.0
            recommendations = state.get("recommendations") or []
            intent: Intent = state.get("intent", "unknown")
            response = RecommendResponse(
                intent=intent,
                profile=state.get("profile"),
                recommendations=recommendations,
                response_markdown=state.get("final_response", ""),
                latency_ms=latency_ms,
            )
            _metrics.record(
                latency_ms=latency_ms,
                intent=intent,
                n_recommendations=len(recommendations),
            )
            span.set_attribute("intent", intent)
            span.set_attribute("recommendations.count", len(recommendations))
            span.set_attribute("latency_ms", latency_ms)
            return response

    return app


app = create_app()


def main() -> None:
    host = os.getenv("FINSAGE_API_HOST", "127.0.0.1")
    port = int(os.getenv("FINSAGE_API_PORT", "8000"))
    uvicorn.run("src.api.main:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
