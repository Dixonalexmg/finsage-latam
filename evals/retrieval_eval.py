"""Benchmark de retrieval hibrido (BM25 + Gemini embeddings con RRF) vs semantico.

Corpus y queries son inline para mantener el harness self-contained. El target
es validar que la fusion gana sobre semantico puro en queries con jerga
financiera especifica.

Uso::

    uv run python -m evals.retrieval_eval
    uv run python -m evals.retrieval_eval --top-k 5

Requiere ``GEMINI_API_KEY`` en el entorno.
"""

from __future__ import annotations

import argparse
import logging
import math
import os
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from src.llm.gemini import GeminiEmbeddingClient
from src.rag.retriever import (
    DEFAULT_EMBED_MODEL,
    Document,
    HybridRetriever,
    RetrievalResult,
    SemanticRetriever,
)

logger = logging.getLogger(__name__)

CORPUS: list[Document] = [
    Document(
        doc_id="banco_chile_cuenta_corriente_premium",
        text=(
            "Cuenta corriente Banco de Chile Premium. Sin mantencion el primer ano. "
            "Incluye chequera y linea de sobregiro hasta 3 ingresos. Renta liquida "
            "minima requerida 1.500.000 CLP."
        ),
    ),
    Document(
        doc_id="banco_chile_tarjeta_visa_signature",
        text=(
            "Tarjeta de credito Visa Signature de Banco de Chile. Tasa anual 32%. "
            "Programa de millas LATAM Pass. Sin comision por compras en el extranjero. "
            "Renta minima 2.500.000 CLP. Cupo maximo hasta 25.000.000 CLP."
        ),
    ),
    Document(
        doc_id="bci_credito_consumo_express",
        text=(
            "Credito de consumo Express BCI. Hasta 30 millones de pesos. Plazos de 6 a "
            "60 meses. CAE desde 22%. Aprobacion en linea para clientes con renta "
            "demostrable mayor a 800.000 CLP mensuales."
        ),
    ),
    Document(
        doc_id="santander_tarjeta_black",
        text=(
            "Tarjeta Black de Banco Santander. Programa de cashback 2% en supermercados "
            "y 1% en otras compras. Comision anual 350.000 CLP. Renta minima exigida "
            "3.000.000 CLP. Sala VIP en aeropuertos incluida."
        ),
    ),
    Document(
        doc_id="bci_hipotecario_uf",
        text=(
            "Credito hipotecario BCI en UF. Financia hasta el 80% del valor de tasacion. "
            "Plazos de 8 a 30 anos. Tasa fija desde 4,2% anual. Seguro de desgravamen e "
            "incendio incluidos. Para vivienda principal o segunda vivienda."
        ),
    ),
    Document(
        doc_id="banco_estado_cuentarut",
        text=(
            "CuentaRUT BancoEstado. Sin costo de apertura ni mantencion. Cualquier "
            "persona con cedula chilena puede abrirla. Tarjeta de debito Visa con "
            "cupo de giro diario de 200.000 CLP."
        ),
    ),
    Document(
        doc_id="scotiabank_tarjeta_amex_gold",
        text=(
            "Tarjeta American Express Gold de Scotiabank. Acumula puntos Membership "
            "Rewards en todas las compras. Comision anual 280.000 CLP. Acceso a sala "
            "Centurion en Santiago. Renta liquida minima 2.000.000 CLP."
        ),
    ),
    Document(
        doc_id="itau_credito_automotriz",
        text=(
            "Credito automotriz Itau para vehiculos nuevos y usados. Financia hasta el "
            "90% del valor del auto. Plazos de 12 a 60 meses. CAE desde 18%. Requiere "
            "renta demostrable y antiguedad laboral minima de 6 meses."
        ),
    ),
    Document(
        doc_id="falabella_cmr_classic",
        text=(
            "Tarjeta CMR Falabella Classic. Cupo desde 200.000 CLP. Compras en cuotas "
            "sin interes en tiendas Falabella, Sodimac y Tottus. Sin renta minima "
            "estricta, evaluacion caso a caso."
        ),
    ),
    Document(
        doc_id="banco_chile_deposito_a_plazo",
        text=(
            "Deposito a plazo Banco de Chile en pesos. Tasa fija conocida al inicio. "
            "Plazos desde 30 dias hasta 1 ano. Monto minimo 500.000 CLP. Renovacion "
            "automatica opcional."
        ),
    ),
    Document(
        doc_id="bci_inversion_fondos_mutuos",
        text=(
            "Fondos mutuos BCI. Categorias de renta fija, renta variable y mixtos. "
            "Inversion inicial desde 50.000 CLP. Sin comision de entrada. Ideal para "
            "diversificar ahorros con perfil de riesgo moderado."
        ),
    ),
    Document(
        doc_id="santander_credito_hipotecario_pesos",
        text=(
            "Credito hipotecario Santander en pesos chilenos. Tasa nominal 6,8% anual. "
            "Financia hasta el 80%. Sin UF - cuota fija conocida en CLP. Plazos hasta "
            "20 anos."
        ),
    ),
]


@dataclass(frozen=True)
class TestCase:
    query: str
    relevant: frozenset[str]


TEST_CASES: list[TestCase] = [
    TestCase(
        query="quiero una tarjeta de credito con cashback en supermercado",
        relevant=frozenset({"santander_tarjeta_black"}),
    ),
    TestCase(
        query="necesito un credito para comprar un auto usado",
        relevant=frozenset({"itau_credito_automotriz"}),
    ),
    TestCase(
        query="cuenta sin mantencion para un trabajador con sueldo bajo",
        relevant=frozenset({"banco_estado_cuentarut"}),
    ),
    TestCase(
        query="hipotecario en UF para primera vivienda",
        relevant=frozenset({"bci_hipotecario_uf"}),
    ),
    TestCase(
        query="hipotecario en pesos cuota fija sin UF",
        relevant=frozenset({"santander_credito_hipotecario_pesos"}),
    ),
    TestCase(
        query="credito de consumo rapido aprobacion en linea",
        relevant=frozenset({"bci_credito_consumo_express"}),
    ),
    TestCase(
        query="tarjeta amex gold con sala vip",
        relevant=frozenset({"scotiabank_tarjeta_amex_gold"}),
    ),
    TestCase(
        query="tarjeta para acumular millas LATAM Pass",
        relevant=frozenset({"banco_chile_tarjeta_visa_signature"}),
    ),
    TestCase(
        query="donde invertir 100 mil pesos a corto plazo",
        relevant=frozenset({"bci_inversion_fondos_mutuos", "banco_chile_deposito_a_plazo"}),
    ),
    TestCase(
        query="tarjeta para comprar en falabella en cuotas sin interes",
        relevant=frozenset({"falabella_cmr_classic"}),
    ),
    TestCase(
        query="cuenta corriente con linea de sobregiro y chequera",
        relevant=frozenset({"banco_chile_cuenta_corriente_premium"}),
    ),
    TestCase(
        query="opciones de inversion conservadora para diversificar ahorros",
        relevant=frozenset({"bci_inversion_fondos_mutuos", "banco_chile_deposito_a_plazo"}),
    ),
]


def recall_at_k(hits: Sequence[RetrievalResult], relevant: frozenset[str], k: int) -> float:
    if not relevant:
        return 0.0
    top = {h.doc_id for h in hits[:k]}
    return len(top & relevant) / len(relevant)


def mrr_at_k(hits: Sequence[RetrievalResult], relevant: frozenset[str], k: int) -> float:
    for hit in hits[:k]:
        if hit.doc_id in relevant:
            return 1.0 / hit.rank
    return 0.0


def ndcg_at_k(hits: Sequence[RetrievalResult], relevant: frozenset[str], k: int) -> float:
    if not relevant:
        return 0.0
    dcg = sum(1.0 / math.log2(hit.rank + 1) for hit in hits[:k] if hit.doc_id in relevant)
    ideal_hits = min(len(relevant), k)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return dcg / idcg if idcg > 0 else 0.0


@dataclass
class AggregateMetrics:
    name: str
    recall: float
    mrr: float
    ndcg: float

    def as_row(self) -> str:
        return f"{self.name:<10} {self.recall:>8.3f} {self.mrr:>8.3f} {self.ndcg:>8.3f}"


def _evaluate(
    name: str,
    search: Callable[[str], list[RetrievalResult]],
    cases: Sequence[TestCase],
    k: int,
) -> AggregateMetrics:
    recalls: list[float] = []
    mrrs: list[float] = []
    ndcgs: list[float] = []
    for case in cases:
        hits = search(case.query)
        recalls.append(recall_at_k(hits, case.relevant, k))
        mrrs.append(mrr_at_k(hits, case.relevant, k))
        ndcgs.append(ndcg_at_k(hits, case.relevant, k))
    n = len(cases)
    return AggregateMetrics(
        name=name,
        recall=sum(recalls) / n,
        mrr=sum(mrrs) / n,
        ndcg=sum(ndcgs) / n,
    )


def run_benchmark(*, top_k: int, model: str) -> tuple[AggregateMetrics, AggregateMetrics]:
    client = GeminiEmbeddingClient()
    semantic_only = SemanticRetriever(CORPUS, client=client, model=model)
    hybrid = HybridRetriever(CORPUS, embedding_client=client, embedding_model=model)
    sem_metrics = _evaluate(
        "semantic", lambda q: semantic_only.search(q, top_k=top_k), TEST_CASES, top_k
    )
    hyb_metrics = _evaluate("hybrid", lambda q: hybrid.search(q, top_k=top_k), TEST_CASES, top_k)
    return sem_metrics, hyb_metrics


def _print_report(sem: AggregateMetrics, hyb: AggregateMetrics, *, top_k: int) -> None:
    header = f"{'method':<10} {'recall':>8} {'MRR':>8} {'NDCG':>8}"
    bar = "-" * len(header)
    sys.stdout.write(f"\nRetrieval benchmark @ top_k={top_k} sobre {len(TEST_CASES)} casos\n")
    sys.stdout.write(bar + "\n")
    sys.stdout.write(header + "\n")
    sys.stdout.write(bar + "\n")
    sys.stdout.write(sem.as_row() + "\n")
    sys.stdout.write(hyb.as_row() + "\n")
    sys.stdout.write(bar + "\n")
    sys.stdout.write(
        f"{'delta hibrido':<10} {hyb.recall - sem.recall:>+8.3f} "
        f"{hyb.mrr - sem.mrr:>+8.3f} {hyb.ndcg - sem.ndcg:>+8.3f}\n"
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top-k", type=int, default=5, help="Cuantos hits evaluar.")
    parser.add_argument(
        "--model", default=DEFAULT_EMBED_MODEL, help="Modelo de embeddings de Gemini."
    )
    args = parser.parse_args(argv)

    if not os.getenv("GEMINI_API_KEY"):
        sys.stderr.write("GEMINI_API_KEY no esta seteada - abortando.\n")
        return 2

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    sem, hyb = run_benchmark(top_k=args.top_k, model=args.model)
    _print_report(sem, hyb, top_k=args.top_k)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
