"""Tests unitarios para BaseScraper: robots.txt, rate limiting y persistencia."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.robotparser import RobotFileParser

import pytest
from src.models.schemas import CreditCard, Currency
from src.scrapers.base import BaseScraper, FinancialProductT, RobotsDisallowedError


class _DummyScraper(BaseScraper):
    """Scraper de prueba: implementa el contrato mínimo y no hace I/O real."""

    BANK_NAME = "Test Bank"
    BASE_URL = "https://example.test"

    def __init__(self, data_dir: Path, html_by_url: dict[str, str] | None = None) -> None:
        super().__init__(data_dir=data_dir)
        self.fetched_urls: list[str] = []
        self._html_by_url = html_by_url or {}

    def list_product_urls(self) -> list[str]:
        return list(self._html_by_url.keys())

    def parse_products(self, html: str, url: str) -> list[FinancialProductT]:
        if not html.strip():
            return []
        return [_sample_card(url=url)]

    def _fetch_html(self, url: str) -> str:
        self.fetched_urls.append(url)
        return self._html_by_url.get(url, "")


def _sample_card(url: str) -> CreditCard:
    return CreditCard(
        product_id="test_bank_sample",
        bank_name="Test Bank",
        product_name="Sample",
        currency=Currency.CLP,
        source_url=url,
        scraped_at=datetime(2026, 4, 20, 12, 0, tzinfo=UTC),
        min_income_required=Decimal("500000"),
        annual_fee=Decimal("40000"),
        interest_rate_annual=0.33,
        credit_limit_min=Decimal("500000"),
        credit_limit_max=Decimal("5000000"),
    )


def _robots_parser(rules: str) -> RobotFileParser:
    parser = RobotFileParser()
    parser.parse(rules.splitlines())
    return parser


def test_constructor_requires_bank_name_and_base_url() -> None:
    class _Empty(BaseScraper):
        def list_product_urls(self) -> list[str]:
            return []

        def parse_products(self, html: str, url: str) -> list[FinancialProductT]:
            return []

    with pytest.raises(ValueError, match="BANK_NAME"):
        _Empty()


def test_slug_normalizes_bank_name() -> None:
    assert _DummyScraper.slug() == "test_bank"


def test_fetch_blocks_when_robots_disallows(tmp_path: Path) -> None:
    scraper = _DummyScraper(data_dir=tmp_path)
    scraper._robots = _robots_parser("User-agent: *\nDisallow: /cards")

    with pytest.raises(RobotsDisallowedError):
        scraper.fetch("https://example.test/cards")


def test_fetch_allows_when_robots_permits(tmp_path: Path, mocker: Any) -> None:
    scraper = _DummyScraper(
        data_dir=tmp_path,
        html_by_url={"https://example.test/ok": "<html></html>"},
    )
    scraper._robots = _robots_parser("User-agent: *\nAllow: /")
    mocker.patch("time.sleep")

    html = scraper.fetch("https://example.test/ok")

    assert html == "<html></html>"
    assert scraper.fetched_urls == ["https://example.test/ok"]


def test_rate_limit_sleeps_between_fetches(tmp_path: Path, mocker: Any) -> None:
    scraper = _DummyScraper(
        data_dir=tmp_path,
        html_by_url={
            "https://example.test/a": "<html/>",
            "https://example.test/b": "<html/>",
        },
    )
    scraper._robots = _robots_parser("User-agent: *\nAllow: /")
    # Primera llamada: t=0 (sin sleep). Segunda: t=0.2 → debe dormir 0.8s.
    mocker.patch("src.scrapers.base.time.monotonic", side_effect=[0.0, 0.2, 0.2])
    sleep_mock = mocker.patch("src.scrapers.base.time.sleep")

    scraper.fetch("https://example.test/a")
    scraper.fetch("https://example.test/b")

    sleep_mock.assert_called_once()
    (wait,) = sleep_mock.call_args.args
    assert wait == pytest.approx(0.8)


def test_rate_limit_skips_sleep_when_interval_exceeded(tmp_path: Path, mocker: Any) -> None:
    scraper = _DummyScraper(
        data_dir=tmp_path,
        html_by_url={
            "https://example.test/a": "<html/>",
            "https://example.test/b": "<html/>",
        },
    )
    scraper._robots = _robots_parser("User-agent: *\nAllow: /")
    mocker.patch("src.scrapers.base.time.monotonic", side_effect=[0.0, 5.0, 5.0])
    sleep_mock = mocker.patch("src.scrapers.base.time.sleep")

    scraper.fetch("https://example.test/a")
    scraper.fetch("https://example.test/b")

    sleep_mock.assert_not_called()


def test_save_raw_writes_file_with_url_slug(tmp_path: Path) -> None:
    scraper = _DummyScraper(data_dir=tmp_path)

    path = scraper.save_raw("https://example.test/personas/tarjetas", "<html>ok</html>")

    assert path.exists()
    assert path.parent == tmp_path / "raw" / "test_bank"
    assert "personas_tarjetas" in path.name
    assert path.name.endswith(".html")
    assert path.read_text(encoding="utf-8") == "<html>ok</html>"


def test_save_processed_emits_validated_json(tmp_path: Path) -> None:
    scraper = _DummyScraper(data_dir=tmp_path)
    card = _sample_card(url="https://example.test/cards")

    path = scraper.save_processed([card])

    assert path == tmp_path / "processed" / "test_bank.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert len(payload) == 1
    assert payload[0]["product_id"] == "test_bank_sample"
    assert payload[0]["product_type"] == "credit_card"


def test_scrape_full_flow_persists_raw_and_processed(tmp_path: Path, mocker: Any) -> None:
    scraper = _DummyScraper(
        data_dir=tmp_path,
        html_by_url={"https://example.test/cards": "<html>cards</html>"},
    )
    scraper._robots = _robots_parser("User-agent: *\nAllow: /")
    mocker.patch("src.scrapers.base.time.sleep")

    products = scraper.scrape()

    assert len(products) == 1
    raw_files = list((tmp_path / "raw" / "test_bank").iterdir())
    assert len(raw_files) == 1
    processed = tmp_path / "processed" / "test_bank.json"
    assert processed.exists()


def test_scrape_skips_processed_when_no_products(tmp_path: Path, mocker: Any) -> None:
    scraper = _DummyScraper(
        data_dir=tmp_path,
        html_by_url={"https://example.test/empty": "   "},
    )
    scraper._robots = _robots_parser("User-agent: *\nAllow: /")
    mocker.patch("src.scrapers.base.time.sleep")

    products = scraper.scrape()

    assert products == []
    assert not (tmp_path / "processed").exists()


def test_robots_failure_defaults_to_allowed(tmp_path: Path, mocker: Any) -> None:
    scraper = _DummyScraper(data_dir=tmp_path)
    mocker.patch.object(RobotFileParser, "read", side_effect=OSError("dns failure"))

    assert scraper.is_allowed_by_robots("https://example.test/anything") is True
