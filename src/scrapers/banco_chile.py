"""Scraper de tarjetas de crédito de Banco de Chile.

Ingesta el listado público de ``/personas/tarjetas-de-credito`` y emite
:class:`CreditCard` validadas. La página se renderiza con Playwright (ver
:class:`BaseScraper`) y el HTML se parsea con la librería estándar vía
:class:`_CardHTMLParser`, que lee ``data-*`` attributes de cada tarjeta.

El contrato del HTML con Banco de Chile está documentado en ``docs/decisions.md``;
si cambia, actualizar los selectores y los fixtures de tests en paralelo.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
from typing import ClassVar

from src.models.schemas import CardTier, CreditCard, Currency
from src.scrapers.base import BaseScraper, FinancialProductT


class BancoChileScraper(BaseScraper):
    """Scraper del catálogo de tarjetas de crédito de Banco de Chile (v1.0)."""

    BANK_NAME: ClassVar[str] = "Banco de Chile"
    BASE_URL: ClassVar[str] = "https://www.bancochile.cl"
    CARDS_PATH: ClassVar[str] = "/personas/tarjetas-de-credito"

    def list_product_urls(self) -> list[str]:
        return [f"{self.BASE_URL}{self.CARDS_PATH}"]

    def parse_products(self, html: str, url: str) -> list[FinancialProductT]:
        parser = _CardHTMLParser()
        parser.feed(html)
        scraped_at = datetime.now(UTC)
        cards: list[FinancialProductT] = []
        for raw in parser.rows:
            cards.append(_row_to_credit_card(raw, source_url=url, scraped_at=scraped_at))
        return cards


# ---------------------------------------------------------------------------
# HTML parsing
# ---------------------------------------------------------------------------


class _CardHTMLParser(HTMLParser):
    """Extrae ``data-*`` attributes de cada elemento ``data-product="credit-card"``."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {k: v for k, v in attrs if v is not None}
        if attrs_dict.get("data-product") != "credit-card":
            return
        row = {
            key.removeprefix("data-"): value
            for key, value in attrs_dict.items()
            if key.startswith("data-")
        }
        self.rows.append(row)


# ---------------------------------------------------------------------------
# Mapping row → CreditCard
# ---------------------------------------------------------------------------


def _row_to_credit_card(
    row: dict[str, str], *, source_url: str, scraped_at: datetime
) -> CreditCard:
    try:
        slug = row["slug"]
        return CreditCard(
            product_id=f"banco_chile_{slug}",
            bank_name=BancoChileScraper.BANK_NAME,
            product_name=row["name"],
            currency=Currency(row.get("currency", Currency.CLP.value)),
            source_url=source_url,
            scraped_at=scraped_at,
            min_income_required=Decimal(row["min-income"]),
            annual_fee=Decimal(row["annual-fee"]),
            interest_rate_annual=float(row["tea"]),
            credit_limit_min=Decimal(row["limit-min"]),
            credit_limit_max=Decimal(row["limit-max"]),
            tier=CardTier(row.get("tier", CardTier.CLASSIC.value)),
            rewards_program=_parse_bool(row.get("rewards"), default=False),
            cashback_rate=float(row["cashback"]) if row.get("cashback") else None,
            international=_parse_bool(row.get("international"), default=True),
        )
    except (KeyError, InvalidOperation, ValueError) as err:
        raise ValueError(f"fila de tarjeta inválida: {row!r}") from err


def _parse_bool(value: str | None, *, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "si", "sí", "yes"}
