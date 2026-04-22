"""Carga y materializa el catalogo seed de FinSage.

El catalogo versionado vive en ``data/seed`` para que el proyecto tenga una
fuente de verdad reproducible en portfolio y CI, aun cuando los scrapers no se
ejecuten en cada entorno. En runtime el seed se hidrata a modelos Pydantic y se
persiste en DuckDB para alimentar el retriever real.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import duckdb

from src.models.schemas import CreditCard, PersonalLoan
from src.rag.retriever import Document

DEFAULT_SEED_DIR = Path("data") / "seed"
DEFAULT_DUCKDB_PATH = Path("data") / "catalog.duckdb"


@dataclass(frozen=True)
class CatalogSnapshot:
    """Catalogo cargado y validado desde seed o DuckDB."""

    cards: list[CreditCard]
    loans: list[PersonalLoan]


def load_seed_catalog(seed_dir: Path | str = DEFAULT_SEED_DIR) -> CatalogSnapshot:
    """Lee ``data/seed`` y valida el catalogo contra los schemas del dominio."""
    root = Path(seed_dir)
    cards = _load_model_list(root / "credit_cards.json", CreditCard)
    loans = _load_model_list(root / "personal_loans.json", PersonalLoan)
    if not cards and not loans:
        raise ValueError(f"{root} no contiene productos seed")
    return CatalogSnapshot(cards=cards, loans=loans)


def materialize_catalog(snapshot: CatalogSnapshot, db_path: Path | str = DEFAULT_DUCKDB_PATH) -> Path:
    """Persiste el catalogo seed en DuckDB para inspeccion y reuso local."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(str(path))
    try:
        _write_credit_cards(con, snapshot.cards)
        _write_personal_loans(con, snapshot.loans)
    finally:
        con.close()
    return path


def load_catalog_from_duckdb(db_path: Path | str = DEFAULT_DUCKDB_PATH) -> CatalogSnapshot:
    """Carga el catalogo ya materializado desde DuckDB y lo revalida."""
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"DuckDB catalog no existe: {path}")

    con = duckdb.connect(str(path), read_only=True)
    try:
        card_rows = con.execute("SELECT * FROM credit_cards ORDER BY product_id").fetchall()
        loan_rows = con.execute("SELECT * FROM personal_loans ORDER BY product_id").fetchall()
    finally:
        con.close()

    cards = [CreditCard.model_validate(_credit_card_dict(row)) for row in card_rows]
    loans = [PersonalLoan.model_validate(_personal_loan_dict(row)) for row in loan_rows]
    return CatalogSnapshot(cards=cards, loans=loans)


def build_credit_card_documents(cards: list[CreditCard]) -> list[Document]:
    """Proyecta tarjetas a documentos indexables por el retriever."""
    return [
        Document(
            doc_id=card.product_id,
            text=_render_card_text(card),
            metadata={"product_type": "credit_card", "bank_name": card.bank_name},
        )
        for card in cards
    ]


def build_loan_documents(loans: list[PersonalLoan]) -> list[Document]:
    """Proyecta prestamos a documentos indexables por el retriever."""
    return [
        Document(
            doc_id=loan.product_id,
            text=_render_loan_text(loan),
            metadata={"product_type": "personal_loan", "bank_name": loan.bank_name},
        )
        for loan in loans
    ]


def _load_model_list(path: Path, model: type[CreditCard] | type[PersonalLoan]) -> list[Any]:
    if not path.exists():
        raise FileNotFoundError(f"Seed file no existe: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path} debe contener una lista JSON")
    return [model.model_validate(item) for item in payload]


def _write_credit_cards(con: Any, cards: list[CreditCard]) -> None:
    con.execute(
        """
        CREATE OR REPLACE TABLE credit_cards (
            product_id TEXT,
            bank_name TEXT,
            product_name TEXT,
            currency TEXT,
            source_url TEXT,
            scraped_at TEXT,
            min_income_required TEXT,
            annual_fee TEXT,
            interest_rate_annual DOUBLE,
            credit_limit_min TEXT,
            credit_limit_max TEXT,
            tier TEXT,
            rewards_program BOOLEAN,
            cashback_rate DOUBLE,
            international BOOLEAN
        )
        """
    )
    rows = [
        (
            card.product_id,
            card.bank_name,
            card.product_name,
            card.currency.value,
            card.source_url,
            card.scraped_at.isoformat(),
            str(card.min_income_required),
            str(card.annual_fee),
            card.interest_rate_annual,
            str(card.credit_limit_min),
            str(card.credit_limit_max),
            card.tier.value,
            card.rewards_program,
            card.cashback_rate,
            card.international,
        )
        for card in cards
    ]
    if rows:
        con.executemany(
            "INSERT INTO credit_cards VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )


def _write_personal_loans(con: Any, loans: list[PersonalLoan]) -> None:
    con.execute(
        """
        CREATE OR REPLACE TABLE personal_loans (
            product_id TEXT,
            bank_name TEXT,
            product_name TEXT,
            currency TEXT,
            source_url TEXT,
            scraped_at TEXT,
            min_income_required TEXT,
            amount_min TEXT,
            amount_max TEXT,
            term_months_min INTEGER,
            term_months_max INTEGER,
            interest_rate_annual DOUBLE,
            cae DOUBLE
        )
        """
    )
    rows = [
        (
            loan.product_id,
            loan.bank_name,
            loan.product_name,
            loan.currency.value,
            loan.source_url,
            loan.scraped_at.isoformat(),
            str(loan.min_income_required),
            str(loan.amount_min),
            str(loan.amount_max),
            loan.term_months_min,
            loan.term_months_max,
            loan.interest_rate_annual,
            loan.cae,
        )
        for loan in loans
    ]
    if rows:
        con.executemany(
            "INSERT INTO personal_loans VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )


def _credit_card_dict(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "product_id": row[0],
        "product_type": "credit_card",
        "bank_name": row[1],
        "product_name": row[2],
        "currency": row[3],
        "source_url": row[4],
        "scraped_at": row[5],
        "min_income_required": row[6],
        "annual_fee": row[7],
        "interest_rate_annual": row[8],
        "credit_limit_min": row[9],
        "credit_limit_max": row[10],
        "tier": row[11],
        "rewards_program": row[12],
        "cashback_rate": row[13],
        "international": row[14],
    }


def _personal_loan_dict(row: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "product_id": row[0],
        "product_type": "personal_loan",
        "bank_name": row[1],
        "product_name": row[2],
        "currency": row[3],
        "source_url": row[4],
        "scraped_at": row[5],
        "min_income_required": row[6],
        "amount_min": row[7],
        "amount_max": row[8],
        "term_months_min": row[9],
        "term_months_max": row[10],
        "interest_rate_annual": row[11],
        "cae": row[12],
    }


def _render_card_text(card: CreditCard) -> str:
    rewards = "con rewards" if card.rewards_program else "sin rewards"
    cashback = (
        f"cashback {card.cashback_rate * 100:.1f}%"
        if card.cashback_rate is not None
        else "sin cashback explicito"
    )
    international = "uso internacional" if card.international else "uso local"
    return (
        f"Tarjeta de credito {card.product_name} de {card.bank_name}. "
        f"Tier {card.tier.value}. Renta minima {card.min_income_required} {card.currency.value}. "
        f"Comision anual {card.annual_fee}. TEA {card.interest_rate_annual * 100:.1f}%. "
        f"Cupo entre {card.credit_limit_min} y {card.credit_limit_max}. "
        f"{rewards}, {cashback}, {international}."
    )


def _render_loan_text(loan: PersonalLoan) -> str:
    return (
        f"Prestamo personal {loan.product_name} de {loan.bank_name}. "
        f"Renta minima {loan.min_income_required} {loan.currency.value}. "
        f"Monto entre {loan.amount_min} y {loan.amount_max}. "
        f"Plazo entre {loan.term_months_min} y {loan.term_months_max} meses. "
        f"Tasa anual {loan.interest_rate_annual * 100:.2f}%. "
        f"CAE {loan.cae * 100:.2f}%."
    )
