"""Harness de evals end-to-end sobre ``evals/test_cases.jsonl``.

Mide tres dimensiones por caso y agrega un reporte Markdown:

1. **Intent accuracy** — clasifica la query con ``ProfileAnalyst`` y compara el
   ``intent`` inferido contra ``expected_intent``.
2. **Recall@3 del retrieval** — indexa el corpus compartido con
   ``retrieval_eval`` en un ``HybridRetriever`` y mide qué fracción de
   ``expected_doc_ids`` aparece en los top-3.
3. **Rubric-based scoring** — un juez LLM (``gemini-2.5-flash``) puntúa los
   top-3 recuperados contra los ``rubric_criteria`` del caso. El juez devuelve
   structured output (``RubricScore``) con score 1..5 por criterio y overall.

Uso::

    uv run python -m evals.run_evals                      # corre las tres dims
    uv run python -m evals.run_evals --skip-rubric         # sólo intent + recall
    uv run python -m evals.run_evals --output report.md    # ruta de salida
    uv run python -m evals.run_evals --limit 5             # primeros N casos

Requiere ``GEMINI_API_KEY``. Las dimensiones deshabilitadas no exigen llamadas
al proveedor.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any, cast, get_args

from pydantic import BaseModel, ConfigDict, Field
from src.agents.base import BaseAgent, StructuredLLMClient, StructuredOutputError
from src.agents.profile_analyst import ProfileAnalyst
from src.llm.gemini import GeminiEmbeddingClient
from src.models.schemas import Intent, UserProfile
from src.rag.retriever import DEFAULT_EMBED_MODEL, HybridRetriever, RetrievalResult

from evals.retrieval_eval import CORPUS

logger = logging.getLogger(__name__)

DEFAULT_TEST_CASES = Path(__file__).parent / "test_cases.jsonl"
DEFAULT_OUTPUT = Path(__file__).parent / "reports" / "eval_report.md"
JUDGE_MODEL = os.getenv("FINSAGE_EVAL_JUDGE_MODEL", "gemini-2.5-flash")
DEFAULT_RECALL_K = 3
_INTENT_VALUES: tuple[str, ...] = get_args(Intent)


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class TestCase(BaseModel):
    """Caso leído desde ``evals/test_cases.jsonl``."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(..., min_length=1)
    query: str = Field(..., min_length=3)
    expected_intent: Intent
    expected_doc_ids: list[str] = Field(default_factory=list)
    rubric_criteria: list[str] = Field(default_factory=list)


class CriterionScore(BaseModel):
    """Score y justificación por criterio individual de la rúbrica."""

    model_config = ConfigDict(extra="forbid")

    criterion: str = Field(..., min_length=1)
    score: int = Field(..., ge=1, le=5, description="1=no cumple, 5=cumple plenamente.")
    justification: str = Field(..., min_length=1)


class RubricScore(BaseModel):
    """Devuelto por el juez: scores por criterio + score global 1..5."""

    model_config = ConfigDict(extra="forbid")

    criterion_scores: list[CriterionScore] = Field(..., min_length=1)
    overall_score: int = Field(..., ge=1, le=5)
    overall_justification: str = Field(..., min_length=1)


@dataclass
class CaseResult:
    """Resultado de las tres dimensiones para un caso (None = dimensión no corrida)."""

    case: TestCase
    predicted_intent: Intent | None = None
    intent_ok: bool | None = None
    retrieved_doc_ids: list[str] = field(default_factory=list)
    recall_at_k: float | None = None
    rubric: RubricScore | None = None
    errors: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# RubricJudge
# ---------------------------------------------------------------------------


def _load_judge_prompt() -> str:
    return (Path(__file__).parent / "prompts" / "rubric_judge.md").read_text(encoding="utf-8")


class RubricJudge(BaseAgent):
    """Juez LLM que puntúa los top-k retrieval hits contra una rúbrica."""

    def __init__(self, *, client: StructuredLLMClient | None = None, model: str = JUDGE_MODEL) -> None:
        super().__init__(
            model=model,
            system_prompt=_load_judge_prompt(),
            client=client,
            temperature=0.0,
            max_tokens=2048,
            agent_name="RubricJudge",
        )

    def score(
        self,
        *,
        query: str,
        criteria: Sequence[str],
        hits: Sequence[RetrievalResult],
        doc_texts: dict[str, str],
    ) -> RubricScore:
        """Puntúa los ``hits`` contra ``criteria`` devolviendo un ``RubricScore`` validado."""
        payload: dict[str, Any] = {
            "query": query,
            "rubric_criteria": list(criteria),
            "retrieved_documents": [
                {
                    "doc_id": hit.doc_id,
                    "rank": hit.rank,
                    "text": doc_texts.get(hit.doc_id, ""),
                }
                for hit in hits
            ],
        }
        user_message = json.dumps(payload, ensure_ascii=False)
        return self.call(
            messages=[{"role": "user", "content": user_message}],
            response_model=RubricScore,
        )


# ---------------------------------------------------------------------------
# Dimensiones
# ---------------------------------------------------------------------------


def evaluate_intent(case: TestCase, analyst: ProfileAnalyst) -> tuple[Intent, bool]:
    """Clasifica ``case.query`` con ``ProfileAnalyst`` y compara con ``expected_intent``."""
    profile: UserProfile = analyst.extract_profile([{"role": "user", "content": case.query}])
    return profile.intent, profile.intent == case.expected_intent


def evaluate_recall(
    case: TestCase,
    retriever: HybridRetriever,
    *,
    k: int,
) -> tuple[list[RetrievalResult], float | None]:
    """Recall@k: fracción de ``expected_doc_ids`` presente en los top-k del retriever.

    Devuelve ``(hits, None)`` si ``expected_doc_ids`` está vacío — el caso no
    contribuye a la métrica agregada porque "recall sobre conjunto vacío" no
    está definido; el rubric judge puede igualmente correr sobre los hits.
    """
    hits = retriever.search(case.query, top_k=k)
    if not case.expected_doc_ids:
        return hits, None
    retrieved = {h.doc_id for h in hits}
    expected = set(case.expected_doc_ids)
    recall = len(retrieved & expected) / len(expected)
    return hits, recall


def evaluate_rubric(
    case: TestCase,
    judge: RubricJudge,
    hits: Sequence[RetrievalResult],
    doc_texts: dict[str, str],
) -> RubricScore:
    """Llama al juez y devuelve el ``RubricScore`` validado."""
    return judge.score(
        query=case.query,
        criteria=case.rubric_criteria,
        hits=hits,
        doc_texts=doc_texts,
    )


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


def load_test_cases(path: Path) -> list[TestCase]:
    """Lee ``test_cases.jsonl`` validando cada línea contra ``TestCase``."""
    cases: list[TestCase] = []
    for line_num, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as err:
            raise ValueError(f"JSON inválido en {path}:{line_num}: {err}") from err
        cases.append(TestCase.model_validate(obj))
    if not cases:
        raise ValueError(f"{path} no contiene casos válidos")
    return cases


@dataclass(frozen=True)
class RunConfig:
    """Flags de ejecución del harness."""

    run_intent: bool
    run_retrieval: bool
    run_rubric: bool
    top_k: int
    limit: int | None


def run_case(
    case: TestCase,
    *,
    cfg: RunConfig,
    analyst: ProfileAnalyst | None,
    retriever: HybridRetriever | None,
    judge: RubricJudge | None,
    doc_texts: dict[str, str],
) -> CaseResult:
    """Ejecuta las dimensiones habilitadas para un caso y agrega errores sin abortar."""
    result = CaseResult(case=case)

    if cfg.run_intent and analyst is not None:
        try:
            predicted, ok = evaluate_intent(case, analyst)
            result.predicted_intent = predicted
            result.intent_ok = ok
        except (StructuredOutputError, Exception) as err:
            result.errors.append(f"intent: {err}")

    hits: list[RetrievalResult] = []
    if cfg.run_retrieval and retriever is not None:
        try:
            hits, recall = evaluate_recall(case, retriever, k=cfg.top_k)
            result.retrieved_doc_ids = [h.doc_id for h in hits]
            result.recall_at_k = recall
        except Exception as err:
            result.errors.append(f"retrieval: {err}")

    if cfg.run_rubric and judge is not None and hits:
        if not case.rubric_criteria:
            logger.info("caso %s sin rubric_criteria — salteando juez", case.id)
        else:
            try:
                result.rubric = evaluate_rubric(case, judge, hits, doc_texts)
            except (StructuredOutputError, Exception) as err:
                result.errors.append(f"rubric: {err}")

    return result


def _build_retriever(embedding_client: GeminiEmbeddingClient) -> HybridRetriever:
    return HybridRetriever(
        CORPUS,
        embedding_client=embedding_client,
        embedding_model=DEFAULT_EMBED_MODEL,
    )


def _doc_text_index() -> dict[str, str]:
    return {doc.doc_id: doc.text for doc in CORPUS}


# ---------------------------------------------------------------------------
# Agregación
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Aggregate:
    """Métricas agregadas sobre todos los casos."""

    total: int
    intent_accuracy: float | None
    intent_by_expected: dict[str, tuple[int, float]]
    mean_recall: float | None
    recall_cases_counted: int
    mean_rubric_overall: float | None
    rubric_by_criterion_mean: float | None
    rubric_cases_counted: int
    failures: int


def aggregate(results: Sequence[CaseResult]) -> Aggregate:
    """Computa las métricas globales y las rupturas por intent."""
    total = len(results)

    intent_judged = [r for r in results if r.intent_ok is not None]
    intent_accuracy = (
        sum(1 for r in intent_judged if r.intent_ok) / len(intent_judged) if intent_judged else None
    )

    intent_by_expected: dict[str, tuple[int, float]] = {}
    for intent_label in _INTENT_VALUES:
        subset = [r for r in intent_judged if r.case.expected_intent == intent_label]
        if not subset:
            continue
        acc = sum(1 for r in subset if r.intent_ok) / len(subset)
        intent_by_expected[intent_label] = (len(subset), acc)

    recalled = [r for r in results if r.recall_at_k is not None]
    mean_recall = mean(cast(list[float], [r.recall_at_k for r in recalled])) if recalled else None

    rubric_cases = [r for r in results if r.rubric is not None]
    overalls = [cast(RubricScore, r.rubric).overall_score for r in rubric_cases]
    mean_overall = mean(overalls) if overalls else None
    per_criterion = [
        cs.score for r in rubric_cases for cs in cast(RubricScore, r.rubric).criterion_scores
    ]
    mean_criterion = mean(per_criterion) if per_criterion else None

    failures = sum(1 for r in results if r.errors)

    return Aggregate(
        total=total,
        intent_accuracy=intent_accuracy,
        intent_by_expected=intent_by_expected,
        mean_recall=mean_recall,
        recall_cases_counted=len(recalled),
        mean_rubric_overall=mean_overall,
        rubric_by_criterion_mean=mean_criterion,
        rubric_cases_counted=len(rubric_cases),
        failures=failures,
    )


# ---------------------------------------------------------------------------
# Reporte Markdown
# ---------------------------------------------------------------------------


def _fmt_opt(value: float | None, *, digits: int = 3) -> str:
    return f"{value:.{digits}f}" if value is not None else "—"


def _fmt_intent(intent: Intent | None) -> str:
    return intent if intent is not None else "—"


def render_report(
    results: Sequence[CaseResult],
    agg: Aggregate,
    *,
    cfg: RunConfig,
    generated_at: datetime,
) -> str:
    """Genera un reporte Markdown con resumen + detalle por caso."""
    lines: list[str] = []
    lines.append("# FinSage — Reporte de Evals")
    lines.append("")
    lines.append(f"**Generado:** {generated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}  ")
    lines.append(f"**Casos evaluados:** {agg.total}  ")
    lines.append(
        "**Dimensiones:** "
        f"intent={'✅' if cfg.run_intent else '—'}, "
        f"recall@{cfg.top_k}={'✅' if cfg.run_retrieval else '—'}, "
        f"rubric={'✅' if cfg.run_rubric else '—'}  "
    )
    lines.append(
        f"**Modelos:** ProfileAnalyst=`{ProfileAnalyst.DEFAULT_MODEL}`, "
        f"Judge=`{JUDGE_MODEL}`, Embeddings=`{DEFAULT_EMBED_MODEL}`"
    )

    # Resumen
    lines.append("")
    lines.append("## Resumen agregado")
    lines.append("")
    lines.append("| Métrica | Valor | N |")
    lines.append("|---|---|---|")
    lines.append(
        f"| Intent accuracy | {_fmt_opt(agg.intent_accuracy)} | "
        f"{agg.total if agg.intent_accuracy is not None else 0} |"
    )
    lines.append(
        f"| Recall@{cfg.top_k} (media) | {_fmt_opt(agg.mean_recall)} | {agg.recall_cases_counted} |"
    )
    lines.append(
        f"| Rubric overall (media, 1-5) | {_fmt_opt(agg.mean_rubric_overall, digits=2)} "
        f"| {agg.rubric_cases_counted} |"
    )
    lines.append(
        f"| Rubric por criterio (media, 1-5) | "
        f"{_fmt_opt(agg.rubric_by_criterion_mean, digits=2)} | {agg.rubric_cases_counted} |"
    )
    lines.append(f"| Casos con errores | {agg.failures} | {agg.total} |")

    # Accuracy por expected_intent
    if agg.intent_by_expected:
        lines.append("")
        lines.append("## Accuracy por intent esperado")
        lines.append("")
        lines.append("| Intent | N | Accuracy |")
        lines.append("|---|---|---|")
        for intent_label, (n, acc) in agg.intent_by_expected.items():
            lines.append(f"| `{intent_label}` | {n} | {acc:.3f} |")

    # Detalle por caso
    lines.append("")
    lines.append(f"## Detalle por caso (top-{cfg.top_k})")
    lines.append("")
    lines.append("| ID | Expected intent | Predicted | Intent ok | Recall | Rubric | Errores |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in results:
        intent_mark = "✅" if r.intent_ok else ("❌" if r.intent_ok is False else "—")
        rubric_str = f"{r.rubric.overall_score}/5" if r.rubric is not None else "—"
        errors_str = "; ".join(r.errors) if r.errors else ""
        lines.append(
            f"| `{r.case.id}` | `{r.case.expected_intent}` | "
            f"`{_fmt_intent(r.predicted_intent)}` | {intent_mark} | "
            f"{_fmt_opt(r.recall_at_k)} | {rubric_str} | {errors_str} |"
        )

    # Fallos de intent (expandidos)
    mispredicted = [r for r in results if r.intent_ok is False and r.predicted_intent is not None]
    if mispredicted:
        lines.append("")
        lines.append("## Fallos de intent classification")
        lines.append("")
        for r in mispredicted:
            lines.append(
                f"- `{r.case.id}`: esperado `{r.case.expected_intent}` · "
                f"predicho `{r.predicted_intent}` · query: _{r.case.query}_"
            )

    # Detalle de rubric (solo criterios con score bajo)
    low_rubric = [
        (r, cs)
        for r in results
        if r.rubric is not None
        for cs in r.rubric.criterion_scores
        if cs.score <= 2
    ]
    if low_rubric:
        lines.append("")
        lines.append("## Criterios con score bajo (≤ 2)")
        lines.append("")
        for r, cs in low_rubric:
            lines.append(
                f"- `{r.case.id}` · score {cs.score}/5 · _{cs.criterion}_ → {cs.justification}"
            )

    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_TEST_CASES,
        help="Ruta al JSONL de casos (default: evals/test_cases.jsonl).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Ruta del reporte Markdown a generar.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=DEFAULT_RECALL_K,
        help="K para recall@k y para los hits que ve el juez (default: 3).",
    )
    parser.add_argument(
        "--skip-intent", action="store_true", help="No correr intent classification."
    )
    parser.add_argument("--skip-retrieval", action="store_true", help="No correr retrieval.")
    parser.add_argument(
        "--skip-rubric", action="store_true", help="No correr el juez (ahorra llamadas)."
    )
    parser.add_argument("--limit", type=int, default=None, help="Correr sólo los primeros N casos.")
    return parser.parse_args(argv)


def _missing_key_msg(name: str) -> str:
    return (
        f"Falta {name} en el entorno — deshabilitá la dimensión correspondiente o seteá la clave."
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    run_intent = not args.skip_intent
    run_retrieval = not args.skip_retrieval
    # Rubric depende de retrieval para obtener los hits a puntuar.
    run_rubric = (not args.skip_rubric) and run_retrieval

    # Validaciones de entorno antes de cualquier llamada costosa.
    if (run_intent or run_retrieval or run_rubric) and not os.getenv("GEMINI_API_KEY"):
        sys.stderr.write(_missing_key_msg("GEMINI_API_KEY") + "\n")
        return 2

    cfg = RunConfig(
        run_intent=run_intent,
        run_retrieval=run_retrieval,
        run_rubric=run_rubric,
        top_k=args.top_k,
        limit=args.limit,
    )

    cases = load_test_cases(args.cases)
    if cfg.limit is not None:
        cases = cases[: cfg.limit]

    analyst: ProfileAnalyst | None = ProfileAnalyst() if cfg.run_intent else None
    retriever: HybridRetriever | None = None
    judge: RubricJudge | None = None
    if cfg.run_retrieval:
        retriever = _build_retriever(GeminiEmbeddingClient())
    if cfg.run_rubric:
        judge = RubricJudge()

    doc_texts = _doc_text_index()

    results: list[CaseResult] = []
    for i, case in enumerate(cases, start=1):
        logger.info("evaluando caso %d/%d: %s", i, len(cases), case.id)
        result = run_case(
            case,
            cfg=cfg,
            analyst=analyst,
            retriever=retriever,
            judge=judge,
            doc_texts=doc_texts,
        )
        results.append(result)

    agg = aggregate(results)
    report = render_report(results, agg, cfg=cfg, generated_at=datetime.now(tz=UTC))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report, encoding="utf-8")
    logger.info("reporte escrito en %s", args.output)

    # Resumen en stdout para CI.
    sys.stdout.write(f"\nCasos: {agg.total} · errores: {agg.failures}\n")
    if agg.intent_accuracy is not None:
        sys.stdout.write(f"Intent accuracy: {agg.intent_accuracy:.3f}\n")
    if agg.mean_recall is not None:
        sys.stdout.write(
            f"Recall@{cfg.top_k}: {agg.mean_recall:.3f} (n={agg.recall_cases_counted})\n"
        )
    if agg.mean_rubric_overall is not None:
        sys.stdout.write(
            f"Rubric overall: {agg.mean_rubric_overall:.2f}/5 (n={agg.rubric_cases_counted})\n"
        )
    sys.stdout.write(f"Reporte: {args.output}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
