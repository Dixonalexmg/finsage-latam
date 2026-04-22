"""Tests del entrypoint FastAPI y sus helpers de runtime."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from fastapi.testclient import TestClient
from src.api import main as api_main
from src.catalog import CatalogSnapshot
from src.models.schemas import (
    CardTier,
    CreditCard,
    ReasoningStep,
    ReasoningTrace,
    Recommendation,
    RiskProfile,
    UserProfile,
)
from src.runtime import FinSageRuntime, RuntimeConfigurationError, RuntimeInitializationError


def _profile() -> UserProfile:
    return UserProfile(
        monthly_income=Decimal("1500000"),
        monthly_expenses=Decimal("650000"),
        existing_debt=Decimal("0"),
        risk_profile=RiskProfile.MODERATE,
        stated_goal="Quiero cashback para compras del dia a dia",
        intent="credit_card",
    )


def _card() -> CreditCard:
    return CreditCard(
        product_id="card_gold",
        bank_name="Banco Test",
        product_name="Tarjeta Gold",
        source_url="https://example.com/card",
        scraped_at=datetime(2026, 4, 1, tzinfo=UTC),
        min_income_required=Decimal("500000"),
        annual_fee=Decimal("45000"),
        interest_rate_annual=0.29,
        credit_limit_min=Decimal("500000"),
        credit_limit_max=Decimal("5000000"),
        tier=CardTier.GOLD,
        rewards_program=True,
        cashback_rate=0.02,
        international=True,
    )


def _recommendation() -> Recommendation:
    return Recommendation(
        product=_card(),
        match_score=0.88,
        rank=1,
        why_this_fits="Combina cashback y un costo razonable para tu nivel de ingresos.",
        caveats=["La comision anual aplica desde el segundo ano."],
        reasoning_trace=ReasoningTrace(
            agent_name="CreditCardExpert",
            model="gemini-2.5-flash-lite",
            steps=[
                ReasoningStep(
                    step=1,
                    description="Valide la elegibilidad por renta.",
                    evidence=["card_gold"],
                )
            ],
            considered_products=["card_gold"],
            rejected_products={},
            final_conclusion="Es la opcion con mejor ajuste para el objetivo declarado.",
        ),
    )


class _FakeOrchestrator:
    def __init__(self, result: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[str] = []

    def run(self, query: str) -> dict[str, Any]:
        self.calls.append(query)
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


@pytest.fixture
def client() -> TestClient:
    api_main._metrics = api_main._MetricsState()
    api_main.set_runtime(None)
    api_main.set_orchestrator(None)
    app = api_main.create_app()
    with TestClient(app) as test_client:
        yield test_client
    api_main._metrics = api_main._MetricsState()
    api_main.set_runtime(None)
    api_main.set_orchestrator(None)


def test_get_runtime_builds_once_and_get_orchestrator_reuses_it(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_orchestrator = _FakeOrchestrator(result={"intent": "unknown", "recommendations": []})
    fake_runtime = FinSageRuntime(
        orchestrator=fake_orchestrator,  # type: ignore[arg-type]
        catalog=CatalogSnapshot(cards=[], loans=[]),
    )
    calls: list[str] = []

    def _build_runtime() -> FinSageRuntime:
        calls.append("build")
        return fake_runtime

    api_main.set_runtime(None)
    api_main.set_orchestrator(None)
    monkeypatch.setattr(api_main, "build_runtime", _build_runtime)

    runtime_a = api_main.get_runtime()
    runtime_b = api_main.get_runtime()
    orchestrator = api_main.get_orchestrator()

    assert runtime_a is runtime_b
    assert orchestrator is fake_orchestrator
    assert calls == ["build"]


def test_set_runtime_updates_orchestrator_cache() -> None:
    fake_orchestrator = _FakeOrchestrator(result={"intent": "unknown", "recommendations": []})
    runtime = FinSageRuntime(
        orchestrator=fake_orchestrator,  # type: ignore[arg-type]
        catalog=CatalogSnapshot(cards=[], loans=[]),
    )

    api_main.set_runtime(runtime)

    assert api_main.get_runtime() is runtime
    assert api_main.get_orchestrator() is fake_orchestrator


def test_health_endpoint_returns_ok(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["service"] == "finsage-api"
    assert payload["version"] == "0.1.0"
    assert payload["recommendations_ready"] is False
    assert payload["missing_env"] == ["GEMINI_API_KEY"]
    assert payload["request_size_limit_bytes"] == api_main.DEFAULT_MAX_REQUEST_SIZE_BYTES
    assert "logfire_mode" in payload
    assert "logfire_detail" in payload


def test_health_reports_ready_when_required_env_is_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "test-gemini")
    app = api_main.create_app()

    with TestClient(app) as test_client:
        response = test_client.get("/health")

    payload = response.json()
    assert payload["recommendations_ready"] is True
    assert payload["missing_env"] == []


def test_request_size_limit_returns_413(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINSAGE_MAX_REQUEST_SIZE_BYTES", "32")
    app = api_main.create_app()

    with TestClient(app) as test_client:
        response = test_client.post("/recommend", json={"query": "x" * 64})

    assert response.status_code == 413
    assert "request body too large" in response.json()["detail"]


def test_invalid_request_size_limit_falls_back_to_default(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FINSAGE_MAX_REQUEST_SIZE_BYTES", "invalido")

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["request_size_limit_bytes"] == api_main.DEFAULT_MAX_REQUEST_SIZE_BYTES


def test_cors_headers_are_added_when_allowlist_is_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINSAGE_CORS_ALLOW_ORIGINS", "http://localhost:8501")
    app = api_main.create_app()

    with TestClient(app) as test_client:
        response = test_client.options(
            "/health",
            headers={
                "Origin": "http://localhost:8501",
                "Access-Control-Request-Method": "GET",
            },
        )

    monkeypatch.delenv("FINSAGE_CORS_ALLOW_ORIGINS", raising=False)
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:8501"


def test_recommend_endpoint_returns_payload_and_updates_metrics(client: TestClient) -> None:
    profile = _profile()
    recommendation = _recommendation()
    fake = _FakeOrchestrator(
        result={
            "intent": "credit_card",
            "profile": profile,
            "recommendations": [recommendation],
            "final_response": "## Tarjetas de credito sugeridas\n\n### 1. Tarjeta Gold",
        }
    )
    api_main.set_orchestrator(fake)  # type: ignore[arg-type]

    response = client.post("/recommend", json={"query": "Quiero una tarjeta con cashback"})

    assert response.status_code == 200
    payload = response.json()
    assert payload["intent"] == "credit_card"
    assert payload["profile"]["stated_goal"] == profile.stated_goal
    assert payload["recommendations"][0]["product"]["product_id"] == "card_gold"
    assert payload["response_markdown"].startswith("## Tarjetas")
    assert payload["latency_ms"] >= 0
    assert fake.calls == ["Quiero una tarjeta con cashback"]

    metrics = client.get("/metrics").json()
    assert metrics["total_queries"] == 1
    assert metrics["successful_queries"] == 1
    assert metrics["failed_queries"] == 0
    assert metrics["total_recommendations"] == 1
    assert metrics["intents"]["credit_card"] == 1


def test_recommend_request_schema_validation_returns_422(client: TestClient) -> None:
    api_main.set_orchestrator(_FakeOrchestrator(result={"intent": "unknown", "recommendations": []}))  # type: ignore[arg-type]

    response = client.post("/recommend", json={"query": ""})

    assert response.status_code == 422


def test_recommend_maps_orchestrator_value_error_to_422(client: TestClient) -> None:
    api_main.set_orchestrator(_FakeOrchestrator(error=ValueError("query no puede ser vacia")))  # type: ignore[arg-type]

    response = client.post("/recommend", json={"query": "hola"})

    assert response.status_code == 422
    assert response.json()["detail"] == "query no puede ser vacia"
    metrics = client.get("/metrics").json()
    assert metrics["failed_queries"] == 1


def test_recommend_maps_runtime_configuration_error_to_503(client: TestClient) -> None:
    api_main.set_orchestrator(
        _FakeOrchestrator(
            error=RuntimeConfigurationError("falta GEMINI_API_KEY")
        )  # type: ignore[arg-type]
    )

    response = client.post("/recommend", json={"query": "hola"})

    assert response.status_code == 503
    assert "GEMINI_API_KEY" in response.json()["detail"]


def test_recommend_returns_503_when_dependency_runtime_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    api_main.set_runtime(None)
    api_main.set_orchestrator(None)
    app = api_main.create_app()

    with TestClient(app) as test_client:
        response = test_client.post("/recommend", json={"query": "hola"})

    assert response.status_code == 503
    assert "GEMINI_API_KEY" in response.json()["detail"]


def test_recommend_maps_runtime_initialization_error_to_503(client: TestClient) -> None:
    api_main.set_orchestrator(
        _FakeOrchestrator(
            error=RuntimeInitializationError("No pude construir el runtime real")
        )  # type: ignore[arg-type]
    )

    response = client.post("/recommend", json={"query": "hola"})

    assert response.status_code == 503
    assert "runtime real" in response.json()["detail"]


def test_recommend_maps_unexpected_error_to_generic_503(client: TestClient) -> None:
    api_main.set_orchestrator(_FakeOrchestrator(error=RuntimeError("boom")))  # type: ignore[arg-type]

    response = client.post("/recommend", json={"query": "hola"})

    assert response.status_code == 503
    assert response.json()["detail"] == "orchestrator unavailable"


def test_recommend_surfaces_provider_credit_error(client: TestClient) -> None:
    api_main.set_orchestrator(
        _FakeOrchestrator(
            error=RuntimeError(
                "Gemini API error 429 (RESOURCE_EXHAUSTED): free tier quota exhausted"
            )
        )  # type: ignore[arg-type]
    )

    response = client.post("/recommend", json={"query": "hola"})

    assert response.status_code == 503
    assert "free tier" in response.json()["detail"]


def test_metrics_state_computes_p95_and_trims_window() -> None:
    metrics = api_main._MetricsState()
    values = [float(i) for i in range(205)]
    for value in values:
        metrics.record(latency_ms=value, intent="credit_card", n_recommendations=1)
    metrics.record_failure()

    snapshot = metrics.snapshot()
    retained = values[-200:]

    assert len(metrics.latencies_ms) == 200
    assert snapshot.total_queries == 206
    assert snapshot.successful_queries == 205
    assert snapshot.failed_queries == 1
    assert snapshot.avg_latency_ms == pytest.approx(sum(retained) / len(retained))
    assert snapshot.p95_latency_ms == retained[int(0.95 * (len(retained) - 1))]


def test_main_uses_env_and_starts_uvicorn(monkeypatch: pytest.MonkeyPatch) -> None:
    called: dict[str, Any] = {}

    def _fake_run(app_path: str, *, host: str, port: int, reload: bool) -> None:
        called.update({"app_path": app_path, "host": host, "port": port, "reload": reload})

    monkeypatch.setenv("FINSAGE_API_HOST", "0.0.0.0")
    monkeypatch.setenv("FINSAGE_API_PORT", "9000")
    monkeypatch.setattr(api_main.uvicorn, "run", _fake_run)

    api_main.main()

    assert called == {
        "app_path": "src.api.main:app",
        "host": "0.0.0.0",
        "port": 9000,
        "reload": False,
    }
