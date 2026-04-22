"""Tests unitarios para BancoChileScraper usando HTML fixture guardado."""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from src.models.schemas import CardTier, CreditCard, Currency
from src.scrapers.banco_chile import BancoChileScraper

FIXTURE = Path(__file__).parent.parent / "fixtures" / "banco_chile_tarjetas.html"


@pytest.fixture
def fixture_html() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_list_product_urls_targets_public_cards_page(tmp_path: Path) -> None:
    scraper = BancoChileScraper(data_dir=tmp_path)

    urls = scraper.list_product_urls()

    assert urls == ["https://www.bancochile.cl/personas/tarjetas-de-credito"]


def test_parse_products_extracts_all_cards_from_fixture(tmp_path: Path, fixture_html: str) -> None:
    scraper = BancoChileScraper(data_dir=tmp_path)
    url = "https://www.bancochile.cl/personas/tarjetas-de-credito"

    cards = scraper.parse_products(fixture_html, url)

    assert len(cards) == 3
    assert all(isinstance(c, CreditCard) for c in cards)

    by_id = {c.product_id: c for c in cards}
    assert set(by_id) == {
        "banco_chile_visa-classic",
        "banco_chile_visa-gold",
        "banco_chile_mastercard-signature",
    }

    gold = by_id["banco_chile_visa-gold"]
    assert gold.bank_name == "Banco de Chile"
    assert gold.product_name == "Visa Gold Banco de Chile"
    assert gold.tier is CardTier.GOLD
    assert gold.currency is Currency.CLP
    assert gold.min_income_required == Decimal("900000")
    assert gold.annual_fee == Decimal("75000")
    assert gold.interest_rate_annual == pytest.approx(0.33)
    assert gold.credit_limit_min == Decimal("800000")
    assert gold.credit_limit_max == Decimal("7000000")
    assert gold.rewards_program is True
    assert gold.cashback_rate == pytest.approx(0.015)
    assert gold.international is True
    assert gold.source_url == url


def test_parse_products_returns_empty_when_no_card_markers(tmp_path: Path) -> None:
    scraper = BancoChileScraper(data_dir=tmp_path)

    cards = scraper.parse_products("<html><body><p>Sin tarjetas.</p></body></html>", "https://x/y")

    assert cards == []


def test_parse_products_raises_on_malformed_row(tmp_path: Path) -> None:
    scraper = BancoChileScraper(data_dir=tmp_path)
    html = (
        '<div data-product="credit-card" data-slug="broken" data-name="Bad"'
        ' data-min-income="notanumber" data-annual-fee="0" data-tea="0.3"'
        ' data-limit-min="0" data-limit-max="0"></div>'
    )

    with pytest.raises(ValueError, match="fila de tarjeta inválida"):
        scraper.parse_products(html, "https://www.bancochile.cl/")


def test_scrape_end_to_end_uses_fixture_html(
    tmp_path: Path, fixture_html: str, mocker: Any
) -> None:
    scraper = BancoChileScraper(data_dir=tmp_path)
    mocker.patch.object(scraper, "_fetch_html", return_value=fixture_html)
    mocker.patch.object(scraper, "is_allowed_by_robots", return_value=True)
    mocker.patch("src.scrapers.base.time.sleep")

    cards = scraper.scrape()

    assert len(cards) == 3
    processed = tmp_path / "processed" / "banco_de_chile.json"
    assert processed.exists()
    raw_files = list((tmp_path / "raw" / "banco_de_chile").iterdir())
    assert len(raw_files) == 1
    assert raw_files[0].read_text(encoding="utf-8") == fixture_html
