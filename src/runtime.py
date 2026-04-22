"""Assembly del runtime productivo de FinSage.

Centraliza las dependencias reales de la demo: seed versionado, snapshot en
DuckDB, retrievers hibridos con embeddings de Gemini y expertos de dominio
cableados al orchestrator.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from src.agents.credit_card_expert import CreditCardExpert
from src.agents.loan_expert import LoanExpert
from src.agents.orchestrator import Orchestrator
from src.catalog import (
    DEFAULT_DUCKDB_PATH,
    DEFAULT_SEED_DIR,
    CatalogSnapshot,
    build_credit_card_documents,
    build_loan_documents,
    load_catalog_from_duckdb,
    load_seed_catalog,
    materialize_catalog,
)
from src.llm.gemini import GeminiEmbeddingClient, GeminiStructuredClient
from src.rag.retriever import DEFAULT_EMBED_MODEL, EmbeddingClient, HybridRetriever


class RuntimeConfigurationError(RuntimeError):
    """Falta configuracion esencial para construir el runtime real."""


class RuntimeInitializationError(RuntimeError):
    """Fallo la construccion del runtime real por una dependencia externa."""


@dataclass(frozen=True)
class RuntimeSettings:
    """Configuracion del runtime cargada desde entorno."""

    seed_dir: Path
    duckdb_path: Path
    gemini_api_key: str | None

    @classmethod
    def from_env(cls) -> RuntimeSettings:
        seed_dir = Path(os.getenv("FINSAGE_SEED_DIR", str(DEFAULT_SEED_DIR)))
        duckdb_path = Path(os.getenv("FINSAGE_DUCKDB_PATH", str(DEFAULT_DUCKDB_PATH)))
        return cls(
            seed_dir=seed_dir,
            duckdb_path=duckdb_path,
            gemini_api_key=os.getenv("GEMINI_API_KEY"),
        )


@dataclass(frozen=True)
class FinSageRuntime:
    """Dependencias reales necesarias para atender ``/recommend``."""

    orchestrator: Orchestrator
    catalog: CatalogSnapshot

    @property
    def available_intents(self) -> tuple[str, ...]:
        return ("credit_card", "personal_loan")


def build_runtime(
    *,
    settings: RuntimeSettings | None = None,
    llm_client: GeminiStructuredClient | None = None,
    embedding_client: EmbeddingClient | None = None,
) -> FinSageRuntime:
    """Construye el runtime real con seed, DuckDB, retrievers y expertos."""
    cfg = settings or RuntimeSettings.from_env()
    _validate_settings(cfg)

    try:
        seed_catalog = load_seed_catalog(cfg.seed_dir)
        materialize_catalog(seed_catalog, cfg.duckdb_path)
        catalog = load_catalog_from_duckdb(cfg.duckdb_path)

        shared_llm_client = llm_client or GeminiStructuredClient(api_key=cfg.gemini_api_key)
        embed_client = embedding_client or GeminiEmbeddingClient(api_key=cfg.gemini_api_key)

        card_retriever = HybridRetriever(
            build_credit_card_documents(catalog.cards),
            embedding_client=embed_client,
            embedding_model=DEFAULT_EMBED_MODEL,
        )
        loan_retriever = HybridRetriever(
            build_loan_documents(catalog.loans),
            embedding_client=embed_client,
            embedding_model=DEFAULT_EMBED_MODEL,
        )

        orchestrator = Orchestrator(
            client=shared_llm_client,
            experts={
                "credit_card": CreditCardExpert(
                    retriever=card_retriever,
                    cards=catalog.cards,
                    client=shared_llm_client,
                ),
                "personal_loan": LoanExpert(
                    retriever=loan_retriever,
                    loans=catalog.loans,
                    client=shared_llm_client,
                ),
            },
        )
        return FinSageRuntime(orchestrator=orchestrator, catalog=catalog)
    except RuntimeConfigurationError:
        raise
    except Exception as exc:
        raise RuntimeInitializationError(
            f"No pude construir el runtime real de FinSage: {exc}"
        ) from exc


def _validate_settings(settings: RuntimeSettings) -> None:
    if not settings.gemini_api_key:
        raise RuntimeConfigurationError(
            "FinSage runtime no esta configurado: falta GEMINI_API_KEY. "
            "Configura la clave en tu entorno local antes de llamar /recommend "
            "o agregala como variable del servicio en Railway."
        )
    if not settings.seed_dir.exists():
        raise RuntimeConfigurationError(
            f"No encuentro el catalogo seed en {settings.seed_dir}. Asegura que data/seed este presente."
        )
