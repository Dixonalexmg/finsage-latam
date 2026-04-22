"""Experto en tarjetas de crédito: rankea el top-3 para el perfil del usuario."""

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
from src.models.schemas import CreditCard, FinancialProduct


def _load_system_prompt() -> str:
    return (
        resources.files("src.agents.prompts")
        .joinpath("credit_card_expert.md")
        .read_text(encoding="utf-8")
    )


class CreditCardExpert(ProductExpert):
    """Sub-agente de dominio que recomienda tarjetas de crédito.

    Acepta un mapping ``product_id → CreditCard``. Se provee también como
    ``Iterable[CreditCard]`` por conveniencia de instancias del scraper.
    """

    DEFAULT_MODEL = "gemini-2.5-flash-lite"

    def __init__(
        self,
        *,
        retriever: Retriever,
        cards: Mapping[str, CreditCard] | Iterable[CreditCard],
        model: str = DEFAULT_MODEL,
        client: StructuredLLMClient | None = None,
        temperature: float = DEFAULT_TEMPERATURE,
        top_n: int = DEFAULT_TOP_N,
        candidate_pool: int = DEFAULT_CANDIDATE_POOL,
    ) -> None:
        products: dict[str, FinancialProduct] = _normalize_cards(cards)
        super().__init__(
            retriever=retriever,
            products=products,
            system_prompt=_load_system_prompt(),
            agent_name="CreditCardExpert",
            product_type_label="tarjeta de crédito",
            model=model,
            client=client,
            temperature=temperature,
            top_n=top_n,
            candidate_pool=candidate_pool,
        )


def _normalize_cards(
    cards: Mapping[str, CreditCard] | Iterable[CreditCard],
) -> dict[str, FinancialProduct]:
    if isinstance(cards, Mapping):
        return dict(cards)
    out: dict[str, FinancialProduct] = {}
    for card in cards:
        out[card.product_id] = card
    return out
