"""Canadian sales tax: GST, HST, PST (incl. Manitoba RST) and QST.

Three responsibilities, all deterministic (the LLM only reads what is printed):

* **Rates**: an effective-dated rate table (``data/canada_tax_rates.csv``)
  that finance can edit when a province changes its rate.
* **Checks**: tax amount = taxable amount x rate, rate matches the official
  rate for the province on the invoice date, the tax types charged match the
  province of supply (HST vs GST+PST vs GST+QST), QST is charged on the
  pre-GST amount, tax lines add up, and the supplier's GST/HST and QST
  registration numbers are present (needed to claim input tax credits).
* **GL distribution**: each tax type is posted according to a user-defined
  treatment: recoverable taxes (GST/HST ITCs, QST ITRs) go to their own GL
  account, while non-recoverable PST is added to the cost of the expense
  lines it applies to, or to its own expense account.
"""

from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .csvio import parse_date, read_csv_rows

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


def province_label(code: str, unknown: str = "Province unknown") -> str:
    """Display name for a province code, including outside-Canada suppliers."""
    if code == OUTSIDE_CANADA:
        return "Outside Canada"
    return PROVINCE_NAMES.get(code, unknown) if code else unknown


# OTHER = a non-Canadian tax (US sales tax, VAT...): no Canadian checks, expensed by default.
TAX_TYPES = ("GST", "HST", "PST", "QST", "OTHER")
CANADIAN_TAX_TYPES = ("GST", "HST", "PST", "QST")

# Sales taxes normally charged on a taxable supply made in each province.
REGIME: dict[str, frozenset[str]] = {
    **{p: frozenset({"HST"}) for p in ("ON", "NB", "NL", "NS", "PE")},
    **{p: frozenset({"GST", "PST"}) for p in ("BC", "SK", "MB")},
    "QC": frozenset({"GST", "QST"}),
    **{p: frozenset({"GST"}) for p in ("AB", "NT", "NU", "YT")},
}

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

# Currencies with no cents: amounts are whole units (an ERP rejects or rounds 0.50 yen).
ZERO_DECIMAL_CURRENCIES = frozenset({"JPY", "KRW", "CLP", "ISK"})

ERROR, WARNING = "error", "warning"
_GST_NUMBER = re.compile(r"^\d{9}RT\d{4}$")
_QST_NUMBER = re.compile(r"^\d{10}TQ\d{4}$")

DEFAULT_RATES_PATH = Path(__file__).resolve().parent.parent / "data" / "canada_tax_rates.csv"
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
            try:
                rate = float((row.get("rate") or "").strip().rstrip("%").replace(",", "."))
            except ValueError:
                raise ValueError(f"{where}: rate {row.get('rate')!r} is not a number") from None
            end = (row.get("effective_to") or "").strip()
            rows.append(
                TaxRate(
                    tax_type=tax_type,
                    province=(row.get("province") or "").strip().upper(),
                    rate=rate / 100 if rate >= 1 else rate,
                    effective_from=parse_date(row.get("effective_from") or "", where),
                    effective_to=parse_date(end, where) if end else None,
                )
            )
        return cls(tuple(rows))

    def rate_for(self, tax_type: str, province: str, on: dt.date | None = None) -> float | None:
        """Official rate for a tax type in a province on a date (None if not levied there)."""
        on = on or dt.date.today()
        for r in self.rates:
            if r.tax_type != tax_type or (r.province and r.province != province):
                continue
            if r.effective_from <= on and (r.effective_to is None or on <= r.effective_to):
                return r.rate
        return None

    def to_markdown(self, on: dt.date | None = None) -> str:
        on = on or dt.date.today()
        lines = ["| tax_type | province | rate |", "| --- | --- | --- |"]
        for r in self.rates:
            if r.effective_from <= on and (r.effective_to is None or on <= r.effective_to):
                lines.append(f"| {r.tax_type} | {r.province or 'all'} | {r.rate:.5g} |")
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


# --- Helpers ---------------------------------------------------------------------------------


def normalise_registration(number: str) -> str:
    return re.sub(r"[\s\-]", "", number or "").upper()


def valid_gst_number(number: str) -> bool:
    return bool(_GST_NUMBER.match(normalise_registration(number)))


def gst_check_digit_ok(number: str) -> bool:
    """Whether the 9-digit Business Number of a GST/HST number passes the CRA's check digit (the Luhn formula
    the CRA uses on its Business Numbers). A number that fails it was misread, mistyped or made up."""
    digits = normalise_registration(number)[:9]
    if len(digits) != 9 or not digits.isdigit():
        return False
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch) * (2 if i % 2 else 1)
        total += n - 9 if n > 9 else n
    return total % 10 == 0


def valid_qst_number(number: str) -> bool:
    return bool(_QST_NUMBER.match(normalise_registration(number)))


def regime_on(province: str, on: dt.date | None, rates: TaxRateTable) -> frozenset[str]:
    """The taxes normally charged in a province on a date: ``REGIME``, except where the rate table has HST in
    force in a GST + PST province (British Columbia from 2010-07-01 to 2013-03-31)."""
    usual = REGIME.get(province, frozenset())
    if usual and "HST" not in usual and on is not None and rates.rate_for("HST", province, on) is not None:
        return frozenset({"HST"})
    return usual


def place_of_supply(coding: Any) -> str:
    for prov in (coding.ship_to_province, coding.supplier_province):
        if prov in PROVINCES:
            return prov
    return ""


def _invoice_date(coding: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(coding.invoice_date)
    except (TypeError, ValueError):
        return None


def _close(a: float, b: float, tol: float) -> bool:
    return abs(a - b) <= tol + 1e-9


# --- Checks ----------------------------------------------------------------------------------


@dataclass
class TaxFinding:
    severity: str
    code: str
    message: str
    line_number: int | None = None


def check_taxes(coding: Any, setup: TaxSetup, known_gl_codes: set[str] | None = None) -> list[TaxFinding]:
    findings: list[TaxFinding] = []

    def add(severity: str, code: str, message: str, line: int | None = None) -> None:
        findings.append(TaxFinding(severity, code, message, line))

    on = _invoice_date(coding)
    prov = place_of_supply(coding)
    tax_lines = list(coding.tax_lines)
    # Canadian taxes actually charged (a printed "GST 0.00" on an exempt invoice is not a charge).
    charged = {tl.tax_type for tl in tax_lines if abs(tl.tax_amount) > 0.004 and tl.tax_type != "OTHER"}
    n_items = max(1, len(coding.line_items))
    tol = 0.01 * n_items + 0.01  # per-line rounding on the vendor's side

    # Tax lines add up to the tax total.
    lines_total = round(sum(tl.tax_amount for tl in tax_lines), 2)
    if not _close(lines_total, coding.tax_total, 0.02):
        add(
            ERROR,
            "TAX_LINES_TOTAL_MISMATCH",
            f"tax lines sum to {lines_total:.2f} but tax_total = {coding.tax_total:.2f}",
        )

    # Tax is worked out once on the taxable amount (a cent of rounding), or line by line and added up (each line
    # rounded). An amount neither way gives is a wrong charge: an error, or a warning when it is within what half a
    # cent of rounding per line could add up to (tax rounded per unit, a line marked wrongly), never silently.
    places = currency_places(getattr(coding, "currency", ""))
    unit = 10.0**-places
    rounding = unit * (n_items / 2 + 1)
    for tl in tax_lines:
        label = f"{tl.tax_type}{' ' + tl.province if tl.province else ''}"
        expected = round(tl.taxable_amount * tl.rate, places)
        ways = [expected]
        if sum(1 for t in tax_lines if t.tax_type == tl.tax_type) == 1:
            flagged = [li for li in coding.line_items if tl.tax_type in li.taxes_applied]
            if flagged:
                ways.append(round(sum(round(li.amount * tl.rate, places) for li in flagged), places))
        if not any(_close(way, tl.tax_amount, unit) for way in ways):
            add(
                WARNING if _close(expected, tl.tax_amount, rounding) else ERROR,
                "TAX_CALC_MISMATCH",
                f"{label}: {tl.taxable_amount:.2f} x {tl.rate:.5g} = {expected:.2f} "
                f"but invoice charges {tl.tax_amount:.2f}",
            )

        if tl.tax_type == "OTHER":
            if abs(tl.tax_amount) > 0.004:
                add(
                    WARNING,
                    "TAX_NON_CANADIAN",
                    f"non-Canadian tax of {tl.tax_amount:.2f} charged (e.g. US sales tax); it is not recoverable "
                    "here and is added to the expense lines. Confirm it should have been charged",
                )
            continue
        if abs(tl.tax_amount) <= 0.004:
            continue  # "GST 0.00" / "exempt" printed: nothing charged, nothing to allocate or verify
        rate_province = tl.province or prov
        if tl.tax_type != "GST" and not rate_province:
            add(WARNING, "TAX_PROVINCE_UNKNOWN", f"{tl.tax_type}: province unknown, rate cannot be verified")
        else:
            official = setup.rates.rate_for(tl.tax_type, rate_province, on)
            if official is None:
                where = PROVINCE_NAMES.get(rate_province, rate_province or "this province")
                add(ERROR, "TAX_TYPE_NOT_LEVIED", f"{tl.tax_type} is not levied in {where}")
            elif abs(official - tl.rate) > 1e-6:
                add(
                    WARNING,
                    "TAX_RATE_NONSTANDARD",
                    f"{label} rate {tl.rate:.5g} differs from the official {official:.5g} on {coding.invoice_date}",
                )
        if prov and tl.province and tl.tax_type != "GST" and tl.province != prov:
            add(WARNING, "TAX_PROVINCE_DIFFERS", f"{label} charged but place of supply is {prov}")

    # Which invoice lines carry each tax, and does their sum match the taxable amount? Compared per
    # tax type, because the same tax can be printed more than once (e.g. on goods and on freight).
    for tax_type in sorted(charged):
        flagged = [li for li in coding.line_items if tax_type in li.taxes_applied]
        taxable = round(sum(t.taxable_amount for t in tax_lines if t.tax_type == tax_type), 2)
        if not flagged:
            add(WARNING, "TAX_NOT_ALLOCATED", f"no line is marked as subject to {tax_type}")
            continue
        base = round(sum(li.amount for li in flagged), 2)
        if not _close(base, taxable, tol):
            add(
                WARNING,
                "TAX_BASE_MISMATCH",
                f"lines marked {tax_type} total {base:.2f} but taxable amount is {taxable:.2f}",
            )

    # QST is calculated on the price excluding GST (no tax-on-tax since 2013).
    gst = next((t for t in tax_lines if t.tax_type == "GST"), None)
    qst = next((t for t in tax_lines if t.tax_type == "QST"), None)
    if gst and qst and not _close(qst.taxable_amount, gst.taxable_amount, tol):
        if _close(qst.taxable_amount, gst.taxable_amount + gst.tax_amount, tol):
            add(ERROR, "QST_ON_GST_INCLUSIVE", "QST was calculated on the GST-inclusive amount; it must exclude GST")

    # Do the taxes charged match the province of supply?
    if not prov:
        if charged:
            add(WARNING, "PROVINCE_UNKNOWN", "province of supply unknown; tax regime cannot be verified")
    elif abs(coding.subtotal) > 0.004:  # credit notes (negative) are checked too
        expected_types = regime_on(prov, on, setup.rates)
        name = PROVINCE_NAMES[prov]
        if not charged:
            add(
                WARNING,
                "NO_TAX_CHARGED",
                f"no sales tax charged on a supply in {name}; confirm it is exempt or zero-rated",
            )
        for extra in sorted(charged - expected_types):
            add(
                WARNING,
                "TAX_REGIME_MISMATCH",
                f"{extra} charged but {name} normally charges {' + '.join(sorted(expected_types))}",
            )
        if charged:
            for missing in sorted(expected_types - charged):
                if missing in ("PST", "QST"):
                    add(
                        WARNING,
                        "PROVINCIAL_TAX_NOT_CHARGED",
                        f"no {missing} charged on a supply in {name}; self-assessment may be required",
                    )
                elif missing == "HST" and "GST" not in charged:
                    add(WARNING, "TAX_REGIME_MISMATCH", f"{name} is an HST province but no HST was charged")

    # Registration numbers needed to claim input tax credits.
    if charged & {"GST", "HST"}:
        number = coding.gst_hst_registration_number
        if not number.strip():
            add(WARNING, "GST_HST_NUMBER_MISSING", "supplier GST/HST registration number not found (needed for ITC)")
        elif not valid_gst_number(number):
            add(WARNING, "GST_HST_NUMBER_FORMAT", "GST/HST number is not in the format 123456789RT0001")
        elif not gst_check_digit_ok(number):
            add(WARNING, "GST_NUMBER_CHECK_DIGIT",
                "GST/HST number fails the CRA check digit: misread, mistyped or not a real number")  # fmt: skip
    if "QST" in charged:
        number = coding.qst_registration_number
        if not number.strip():
            add(WARNING, "QST_NUMBER_MISSING", "supplier QST registration number not found (needed for ITR)")
        elif not valid_qst_number(number):
            add(WARNING, "QST_NUMBER_FORMAT", "QST number is not in the format 1234567890TQ0001")

    # Every tax charged must have a GL treatment the user has set up. Non-Canadian tax (OTHER) too: set up to post
    # to its own account, it would otherwise post to a blank GL.
    posted = {tl.tax_type for tl in tax_lines if abs(tl.tax_amount) > 0.004}
    for tax_type in sorted(posted):
        t = setup.treatment(tax_type)
        if t.needs_gl and not t.gl_code:
            add(ERROR, "TAX_GL_UNMAPPED", f"no GL account mapped for {tax_type}; set it in Tax setup")
        elif t.needs_gl and known_gl_codes is not None and t.gl_code not in known_gl_codes:
            add(ERROR, "TAX_GL_UNKNOWN", f"{tax_type} is mapped to GL {t.gl_code}, which is not in the GL accounts")
    return findings


# --- GL distribution --------------------------------------------------------------------------


def currency_places(currency: str | None) -> int:
    """Decimal places of an amount in this currency: 0 for the yen and the like, else 2."""
    return 0 if str(currency or "").strip().upper() in ZERO_DECIMAL_CURRENCIES else 2


def _allocate(total: float, weights: list[float], places: int = 2) -> list[float]:
    """Split ``total`` proportionally to ``weights`` in cents (whole units when ``places`` is 0); the remainder
    goes to the largest weight."""
    if not weights:
        return []
    base = sum(weights)
    if abs(base) < 1e-9:
        shares = [0.0] * len(weights)
        shares[0] = round(total, 2)
        return shares
    shares = [round(total * w / base, places) for w in weights]
    remainder = round(total - sum(shares), 2)  # to the cent: the shares always add up to the total as read
    biggest = max(range(len(weights)), key=lambda i: abs(weights[i]))
    shares[biggest] = round(shares[biggest] + remainder, 2)
    return shares


def build_gl_distribution(coding: Any, setup: TaxSetup) -> list[dict[str, Any]]:
    """Posting lines that add up to the grand total (what the ERP voucher would contain). Tax spread over the
    lines is spread in whole units for a currency without cents (the yen), so each line posts as printed."""
    places = currency_places(getattr(coding, "currency", ""))
    entries: dict[int, dict[str, Any]] = {}  # keyed by position: line numbers may repeat
    for idx, li in enumerate(coding.line_items):
        entries[idx] = {
            "kind": "expense",
            "line_number": li.line_number,
            "gl_code": li.predicted_gl_code,
            "cost_center": li.predicted_cost_center,
            "description": li.description,
            "net_amount": round(li.amount, 2),
            "non_recoverable_tax": 0.0,
            "amount": round(li.amount, 2),
        }
    tax_entries = []
    for tl in coding.tax_lines:
        if abs(tl.tax_amount) < 0.005:
            continue
        t = setup.treatment(tl.tax_type)
        label = f"{tl.tax_type}{' ' + tl.province if tl.province else ''} {tl.rate * 100:.6g}%"  # QST 9.975%
        if t.treatment == EXPENSE_TO_LINE and coding.line_items:
            items = list(enumerate(coding.line_items))
            targets = [(i, li) for i, li in items if tl.tax_type in li.taxes_applied] or items
            shares = _allocate(tl.tax_amount, [li.amount for _, li in targets], places)
            for (idx, _li), share in zip(targets, shares, strict=True):
                e = entries[idx]
                e["non_recoverable_tax"] = round(e["non_recoverable_tax"] + share, 2)
                e["amount"] = round(e["amount"] + share, 2)
            continue
        tax_entries.append(
            {
                "kind": "tax",
                "line_number": None,
                "gl_code": t.gl_code,
                "cost_center": "",
                "description": f"{label} ({'recoverable' if t.treatment == RECOVERABLE else 'non-recoverable'})",
                "net_amount": 0.0,
                "non_recoverable_tax": round(tl.tax_amount, 2) if t.treatment != RECOVERABLE else 0.0,
                "amount": round(tl.tax_amount, 2),
            }
        )
    return [*entries.values(), *tax_entries]
