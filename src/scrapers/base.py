"""``BaseScraper``: contrato común para scrapers de productos financieros con Playwright.

Todo scraper por banco hereda de ``BaseScraper`` y expone ``list_product_urls`` y
``parse_products``. La base se encarga de:

* validar ``robots.txt`` antes de cada fetch,
* aplicar un rate limit (por defecto 1 req/s) entre peticiones,
* renderizar la página con Playwright (requiere JS, ver ``CLAUDE.md`` §Stack),
* persistir el HTML crudo en ``data/raw/{slug}/`` para re-procesamiento offline,
* serializar los productos validados en ``data/processed/{slug}.json``.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

from src.models.schemas import CreditCard, PersonalLoan

logger = logging.getLogger(__name__)

FinancialProductT = CreditCard | PersonalLoan


class RobotsDisallowedError(RuntimeError):
    """``robots.txt`` prohíbe acceder a la URL con el user-agent configurado."""


class BaseScraper(ABC):
    """Base para scrapers que ingestan productos financieros desde un banco público.

    Subclases definen :data:`BANK_NAME` y :data:`BASE_URL`, e implementan
    :meth:`list_product_urls` y :meth:`parse_products`. La base orquesta el ciclo
    completo (``robots.txt`` → rate limit → fetch → save raw → parse → save processed)
    vía :meth:`scrape`.
    """

    BANK_NAME: ClassVar[str] = ""
    BASE_URL: ClassVar[str] = ""
    USER_AGENT: ClassVar[str] = "FinSageBot/0.1 (+https://github.com/finsage-latam)"
    RATE_LIMIT_SECONDS: ClassVar[float] = 1.0

    def __init__(self, data_dir: Path | str | None = None) -> None:
        if not self.BANK_NAME or not self.BASE_URL:
            raise ValueError(
                f"{type(self).__name__} debe definir BANK_NAME y BASE_URL como ClassVars"
            )
        root = Path(data_dir) if data_dir else Path("data")
        self.data_dir = root
        self.raw_dir = root / "raw" / self.slug()
        self.processed_dir = root / "processed"
        self._last_fetch_ts: float = -math.inf
        self._robots: RobotFileParser | None = None

    @classmethod
    def slug(cls) -> str:
        """Identificador estable del banco, derivado de :data:`BANK_NAME`."""
        return re.sub(r"[^a-z0-9]+", "_", cls.BANK_NAME.lower()).strip("_")

    # ------------------------------------------------------------------
    # Contrato a implementar por subclases
    # ------------------------------------------------------------------

    @abstractmethod
    def list_product_urls(self) -> list[str]:
        """URLs públicas del banco a scrapear (listados de tarjetas, préstamos, etc.)."""

    @abstractmethod
    def parse_products(self, html: str, url: str) -> list[FinancialProductT]:
        """Extrae productos validados desde el HTML renderizado de ``url``."""

    # ------------------------------------------------------------------
    # Flujo principal
    # ------------------------------------------------------------------

    def scrape(self) -> list[FinancialProductT]:
        """Orquesta el ciclo completo y devuelve los productos parseados."""
        products: list[FinancialProductT] = []
        for url in self.list_product_urls():
            html = self.fetch(url)
            self.save_raw(url, html)
            parsed = self.parse_products(html, url)
            logger.info("parsed %d productos desde %s", len(parsed), url)
            products.extend(parsed)
        if products:
            self.save_processed(products)
        return products

    def fetch(self, url: str) -> str:
        """Valida ``robots.txt``, aplica rate limit y renderiza la URL con Playwright."""
        if not self.is_allowed_by_robots(url):
            raise RobotsDisallowedError(
                f"robots.txt de {self.BANK_NAME} prohíbe {url} para {self.USER_AGENT}"
            )
        self._apply_rate_limit()
        return self._fetch_html(url)

    # ------------------------------------------------------------------
    # robots.txt
    # ------------------------------------------------------------------

    def is_allowed_by_robots(self, url: str) -> bool:
        """Devuelve ``True`` si ``robots.txt`` permite el acceso con :data:`USER_AGENT`."""
        robots = self._ensure_robots_loaded()
        return robots.can_fetch(self.USER_AGENT, url)

    def _ensure_robots_loaded(self) -> RobotFileParser:
        if self._robots is not None:
            return self._robots
        parser = RobotFileParser()
        robots_url = urljoin(self.BASE_URL, "/robots.txt")
        parser.set_url(robots_url)
        try:
            parser.read()
        except (OSError, ValueError) as err:
            logger.warning("no se pudo leer %s (%s); asumiendo permitido", robots_url, err)
            parser.parse([])
        self._robots = parser
        return parser

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    def _apply_rate_limit(self) -> None:
        now = time.monotonic()
        wait = self.RATE_LIMIT_SECONDS - (now - self._last_fetch_ts)
        if wait > 0:
            time.sleep(wait)
            now += wait
        self._last_fetch_ts = now

    # ------------------------------------------------------------------
    # Fetch (Playwright) — aislado para permitir mocking en tests
    # ------------------------------------------------------------------

    def _fetch_html(self, url: str) -> str:
        """Renderiza ``url`` con Playwright Chromium y devuelve el HTML final."""
        # Import local: Playwright es pesado y no debe cargarse en tests unitarios.
        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            try:
                context = browser.new_context(user_agent=self.USER_AGENT)
                page = context.new_page()
                page.goto(url, wait_until="domcontentloaded")
                return page.content()
            finally:
                browser.close()

    # ------------------------------------------------------------------
    # Persistencia
    # ------------------------------------------------------------------

    def save_raw(self, url: str, html: str) -> Path:
        """Persiste el HTML crudo en ``data/raw/{slug}/`` con timestamp UTC."""
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        path = self.raw_dir / self._raw_filename(url)
        path.write_text(html, encoding="utf-8")
        return path

    def save_processed(self, products: Sequence[FinancialProductT]) -> Path:
        """Serializa ``products`` validados a ``data/processed/{slug}.json``."""
        self.processed_dir.mkdir(parents=True, exist_ok=True)
        path = self.processed_dir / f"{self.slug()}.json"
        payload = [p.model_dump(mode="json") for p in products]
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        return path

    @staticmethod
    def _raw_filename(url: str) -> str:
        parsed = urlparse(url)
        slug = re.sub(r"[^a-zA-Z0-9]+", "_", parsed.path).strip("_") or "index"
        ts = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        return f"{slug}_{ts}.html"
