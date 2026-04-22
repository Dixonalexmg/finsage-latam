"""Experto en préstamos personales: rankea el top-3 para el perfil del usuario."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from importlib import resources

from src.agents.base import StructuredLLMClient
from src.agents.product_expert import (
    DEFAULT_CANDIDATE_POOL,
    DEFAULT_TEMPERATURE,
    DEFAULT_TOP_N,
    ProductExpert,
    Retriever,
)
from src.models.schemas import FinancialProduct, PersonalLoan


def _load_system_prompt() -> str:
    return (
        resources.files("src.agents.prompts").joinpath("loan_expert.md").read_text(encoding="utf-8")
    )


class LoanExpert(ProductExpert):
    """Sub-agente de dominio que recomienda préstamos personales.

    Acepta un mapping ``product_id → PersonalLoan``. Se provee también como
    ``Iterable[PersonalLoan]`` por conveniencia de instancias del scraper.
    """

    DEFAULT_MODEL = "gemini-2.5-flash-lite"

    def __init__(
        self,
        *,
        retriever: Retriever,
        loans: Mapping[str, PersonalLoan] | Iterable[PersonalLoan],
        model: str = DEFAULT_MODEL,
        client: StructuredLLMClient | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
        top_n: int = DEFAULT_TOP_N,
        candidate_pool: int = DEFAULT_CANDIDATE_POOL,
    ) -> None:
        products: dict[str, FinancialProduct] = _normalize_loans(loans)
        super().__init__(
            retriever=retriever,
            products=products,
            system_prompt=_load_system_prompt(),
            agent_name="LoanExpert",
            product_type_label="préstamo personal de consumo",
            model=model,
            client=client,
            temperature=temperature,
            top_n=top_n,
            candidate_pool=candidate_pool,
        )


def _normalize_loans(
    loans: Mapping[str, PersonalLoan] | Iterable[PersonalLoan],
) -> dict[str, FinancialProduct]:
    if isinstance(loans, Mapping):
        return dict(loans)
    out: dict[str, FinancialProduct] = {}
    for loan in loans:
        out[loan.product_id] = loan
    return out
