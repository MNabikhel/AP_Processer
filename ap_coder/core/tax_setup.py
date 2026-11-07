"""The company's tax setup and the official rate table (D-192): each tax type's treatment and GL account (Settings,
``tax_gl_mapping.csv``), where goods are delivered to us, the meals accounts, and the rates in force by province and
date (``canada_tax_rates.csv``, your copy in the data folder first); with the names of the taxes and the provinces the
invoice schema uses. Reference data the store keeps and builds (``reference_data.py``, ``Store.reference_data``); the
tax checks that read it are ``coding/tax.py``, which keeps every name here as its own too."""

from __future__ import annotations

import datetime as dt
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

from .csvio import parse_date, read_csv_rows

log = logging.getLogger(__name__)

PROVINCES = ("AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT")
PROVINCE_NAMES = {
    "AB": "Alberta",
    "BC": "British Columbia",
    "MB": "Manitoba",
    "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador",
    "NS": "Nova Scotia",
    "NT": "Northwest Territories",
    "NU": "Nunavut",
    "ON": "Ontario",
    "PE": "Prince Edward Island",
    "QC": "Quebec",
    "SK": "Saskatchewan",
    "YT": "Yukon",
}
OUTSIDE_CANADA = "OUTSIDE_CANADA"

# OTHER = a non-Canadian tax (US sales tax, VAT...): no Canadian checks, expensed by default.
TAX_TYPES = ("GST", "HST", "PST", "QST", "OTHER")
CANADIAN_TAX_TYPES = ("GST", "HST", "PST", "QST")

RECOVERABLE = "recoverable"
EXPENSE_TO_LINE = "expense_to_line"
EXPENSE_SEPARATE = "expense_separate"
TREATMENTS = {
    RECOVERABLE: "Recoverable: post to its own GL (ITC / ITR receivable)",
    EXPENSE_TO_LINE: "Not recoverable: add to each expense line's GL",
    EXPENSE_SEPARATE: "Not recoverable: post to its own expense GL",
}
DEFAULT_TREATMENTS = {
    "GST": RECOVERABLE, "HST": RECOVERABLE, "QST": RECOVERABLE, "PST": EXPENSE_TO_LINE, "OTHER": EXPENSE_TO_LINE,
}  # fmt: skip

# The key of a stored invoice (``pipeline.finalise_coding``) that holds the place of supply worked out when no ship-to
# is printed: kept beside ``ship_to_province`` (what is printed or set by the reviewer), never written into it.
PLACE_OF_SUPPLY = "place_of_supply"

DEFAULT_RATES_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "canada_tax_rates.csv"
RATES_FILE = "canada_tax_rates.csv"


def rates_path() -> Path:
    """Your copy of the rate table in the data folder if there is one (it survives updates), else the
    bundled one."""
    from .paths import private_dir

    own = private_dir() / RATES_FILE
    return own if own.is_file() else DEFAULT_RATES_PATH


# --- Rates ------------------------------------------------------------------------


@dataclass(frozen=True)
class TaxRate:
    tax_type: str
    province: str  # "" = applies Canada-wide (GST)
    rate: float
    effective_from: dt.date
    effective_to: dt.date | None


@dataclass(frozen=True)
class TaxRateTable:
    rates: tuple[TaxRate, ...]

    @classmethod
    def load(cls, path: str | Path | None = None) -> TaxRateTable:
        path = rates_path() if path is None else path
        rows = []
        for n, row in enumerate(read_csv_rows(path), start=2):  # row 1 is the header
            where = f"{path} row {n}"
            tax_type = (row.get("tax_type") or "").strip().upper()
            if tax_type not in TAX_TYPES:
                raise ValueError(f"{where}: unknown tax_type {tax_type!r}")
            text = (row.get("rate") or "").strip()
            try:
                rate = float(text.rstrip("%").strip().replace(",", "."))
                if not math.isfinite(rate):  # float() reads "nan" and "inf": not a rate either
                    raise ValueError(text)
            except ValueError:
                raise ValueError(f"{where}: rate {row.get('rate')!r} is not a number") from None
            # "0.5%" is half a percent: a rate written with % is always a percentage; without it, 5 means 5%.
            percent = text.endswith("%") or rate >= 1
            end = (row.get("effective_to") or "").strip()
            rows.append(
                TaxRate(
                    tax_type=tax_type,
                    province=(row.get("province") or "").strip().upper(),
                    rate=rate / 100 if percent else rate,
                    effective_from=parse_date(row.get("effective_from") or "", where),
                    effective_to=parse_date(end, where) if end else None,
                )
            )
        table = cls(tuple(rows))
        for message in table.overlaps():
            log.warning("%s: %s", path, message)
        return table

    def _in_force(self, tax_type: str, province: str, on: dt.date) -> TaxRate | None:
        """The row in force: of the rows covering the date, the one that took effect last. File order never
        decides, so a new rate appended with the old row still open (no effective_to) wins from its date."""
        rows = [
            r for r in self.rates
            if r.tax_type == tax_type and (not r.province or r.province == province)
            and r.effective_from <= on and (r.effective_to is None or on <= r.effective_to)
        ]  # fmt: skip
        return max(rows, key=lambda r: (r.effective_from, bool(r.province)), default=None)

    def rate_for(self, tax_type: str, province: str, on: dt.date | None = None) -> float | None:
        """Official rate for a tax type in a province on a date (None if not levied there)."""
        row = self._in_force(tax_type, province, on or dt.date.today())
        return row.rate if row else None

    def overlaps(self) -> list[str]:
        """Rows of the same tax and province whose date ranges overlap (an old row left open when a new rate was
        added): the latest one wins, but the table should be tidied."""
        found = []
        for i, a in enumerate(self.rates):
            for b in self.rates[i + 1 :]:
                if (a.tax_type, a.province) != (b.tax_type, b.province):
                    continue
                a_end, b_end = a.effective_to or dt.date.max, b.effective_to or dt.date.max
                if a.effective_from <= b_end and b.effective_from <= a_end:
                    found.append(
                        f"{a.tax_type} {a.province or 'all'}: rows from {a.effective_from} and {b.effective_from} "
                        "overlap; the later one is used from its date (set effective_to on the older row)"
                    )
        return found

    def in_force(self, on: dt.date | None = None) -> list[TaxRate]:
        """The rows in force on a date, one per tax and province (the one ``rate_for`` uses), in file order."""
        on = on or dt.date.today()
        return [r for r in self.rates if r is self._in_force(r.tax_type, r.province, on)]

    def to_markdown(self, on: dt.date | None = None) -> str:
        lines = ["| tax_type | province | rate |", "| --- | --- | --- |"]
        lines += [f"| {r.tax_type} | {r.province or 'all'} | {r.rate:.5g} |" for r in self.in_force(on)]
        return "\n".join(lines)


# --- GL treatment per tax type --------------------------------------------------------


@dataclass(frozen=True)
class TaxTreatment:
    tax_type: str
    treatment: str
    gl_code: str = ""

    @property
    def needs_gl(self) -> bool:
        return self.treatment in (RECOVERABLE, EXPENSE_SEPARATE)


@dataclass
class TaxSetup:
    rates: TaxRateTable
    treatments: dict[str, TaxTreatment] = field(default_factory=dict)
    # Where goods and services are delivered to us (Settings → Review): the place of supply of an invoice that
    # prints no ship-to, by cost center (sites in several provinces) or the one default.
    our_province: str = ""
    site_provinces: dict[str, str] = field(default_factory=dict)
    # GL accounts marked "Meals & entertainment (50% tax credit)" (GL accounts page, D-90): half the GST/HST and QST
    # on a line coded to one is not claimable, and is expensed with the line.
    meals_gl_codes: frozenset[str] = frozenset()

    @classmethod
    def default(cls) -> TaxSetup:
        return cls(TaxRateTable.load(), {})

    def treatment(self, tax_type: str) -> TaxTreatment:
        return self.treatments.get(tax_type) or TaxTreatment(tax_type, DEFAULT_TREATMENTS[tax_type])

    def tax_gl_codes(self) -> set[str]:
        return {t.gl_code for t in self.treatments.values() if t.gl_code and t.needs_gl}

    def to_prompt_context(self) -> str:
        lines = ["| tax_type | treatment | GL account |", "| --- | --- | --- |"]
        for tax_type in TAX_TYPES:
            t = self.treatment(tax_type)
            lines.append(f"| {tax_type} | {t.treatment} | {t.gl_code if t.needs_gl else '(expense line GL)'} |")
        return "\n".join(lines)


def load_tax_mapping(path: str | Path) -> dict[str, TaxTreatment]:
    """CSV with columns tax_type, treatment, gl_code."""
    mapping = {}
    for row in read_csv_rows(path):
        tax_type = row["tax_type"].strip().upper()
        treatment = row["treatment"].strip().lower()
        if tax_type not in TAX_TYPES:
            raise ValueError(f"{path}: unknown tax_type {tax_type!r}")
        if treatment not in TREATMENTS:
            raise ValueError(f"{path}: treatment must be one of {', '.join(TREATMENTS)}")
        mapping[tax_type] = TaxTreatment(tax_type, treatment, (row.get("gl_code") or "").strip())
    return mapping
