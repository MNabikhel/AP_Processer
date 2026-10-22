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
import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.csvio import parse_date, read_csv_rows

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
EXEMPT, ZERO_RATED = "exempt", "zero_rated"  # a line's tax_status (line_tax.py, D-64)
_GST_NUMBER = re.compile(r"^\d{9}RT\d{4}$")
_QST_NUMBER = re.compile(r"^\d{10}TQ\d{4}$")

DEFAULT_RATES_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "canada_tax_rates.csv"
RATES_FILE = "canada_tax_rates.csv"


def rates_path() -> Path:
    """Your copy of the rate table in the data folder if there is one (it survives updates), else the
    bundled one."""
    from ..core.paths import private_dir

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


# Where a place of supply came from (``supply_source``), as the review screen says it.
SUPPLY_PRINTED, SUPPLY_SITE, SUPPLY_OURS, SUPPLY_VENDOR = "printed", "site", "ours", "vendor"
# The key of a stored invoice (``pipeline.finalise_coding``) that holds the place of supply worked out when no ship-to
# is printed: kept beside ``ship_to_province`` (what is printed or set by the reviewer), never written into it.
PLACE_OF_SUPPLY = "place_of_supply"


def stored_supply(doc: dict[str, Any]) -> str:
    """The place of supply of a stored invoice: the ship-to printed, else the one worked out when it was coded, else
    the vendor's province."""
    return str(doc.get("ship_to_province") or doc.get(PLACE_OF_SUPPLY) or doc.get("supplier_province") or "")


def supply_source(coding: Any, setup: TaxSetup | None = None) -> tuple[str, str]:
    """(province, where it came from) of the place of supply, the province whose sales tax applies.

    Goods are taxed where they are delivered to the buyer, services (generally) where the buyer is (Excise Tax Act,
    Schedule IX; CRA GST/HST Memoranda 3-3-3 and 3-3-6; the BC, Saskatchewan and Manitoba PST acts follow the same
    idea). Neither the vendor's province nor where the goods were shipped from decides it: an Ontario vendor shipping
    from BC to our site in Alberta charges GST only. So, in order: the ship-to read on the invoice (or set by the
    reviewer); the province of the cost center the lines are coded to (one site, one province); our default ship-to
    province; and only when none is known, the vendor's province (often the same, never sure). A cost center not in
    the site list (or none) is one of our home sites, in our province: a site's province decides only when every
    line goes to it."""
    printed = getattr(coding, "ship_to_province", "") or ""
    if printed in PROVINCES:
        return printed, SUPPLY_PRINTED
    if setup is not None:
        # A cost center whatever its case or spaces: "cc400=BC" typed in Settings is cost center CC400.
        listed = {str(code).strip().upper(): province for code, province in setup.site_provinces.items()}
        sites = {listed.get(str(getattr(li, "predicted_cost_center", "") or "").strip().upper()) or setup.our_province
                 for li in getattr(coding, "line_items", []) or []} - {None, ""}  # fmt: skip
        if len(sites) == 1 and (site := next(iter(sites))) in PROVINCES and site != setup.our_province:
            return site, SUPPLY_SITE
        if setup.our_province in PROVINCES:
            return setup.our_province, SUPPLY_OURS
    supplier = getattr(coding, "supplier_province", "") or ""
    if supplier in PROVINCES:
        return supplier, SUPPLY_VENDOR
    return "", ""


def place_of_supply(coding: Any, setup: TaxSetup | None = None) -> str:
    return supply_source(coding, setup)[0]


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
    prov, source = supply_source(coding, setup)
    # Where the place of supply came from, when no ship-to is printed: the warnings say it was assumed (D-61).
    assumed = {
        SUPPLY_OURS: " (your province in Settings: no ship-to or bill-to printed)",
        SUPPLY_VENDOR: " (only the vendor's province: no ship-to or bill-to printed)",
    }.get(source, "")
    if assumed and (getattr(coding, "ship_to_province", "") or "") == OUTSIDE_CANADA:  # printed, not missing (B-673)
        where = "your province in Settings" if source == SUPPLY_OURS else "only the vendor's province"
        assumed = f" (the ship-to printed is outside Canada: checked against {where})"
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
            f"the tax lines add up to {lines_total:.2f} but the tax total is {coding.tax_total:.2f}",
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
            # The taxable is the lines marked with the tax (it follows them on the review screen) and one line's
            # amount is the difference: the tax charged is right for the other lines, a line's tax code is what to
            # look at (a warning that names it), not the vendor's arithmetic.
            marked = [li for li in coding.line_items if tl.tax_type in li.taxes_applied]
            gap = round((tl.tax_amount - expected) / tl.rate, 2) if tl.rate else 0.0
            suspect = None
            if marked and _close(round(sum(li.amount for li in marked), 2), tl.taxable_amount, tol) and gap:
                suspect = (_gap_line(marked, -gap, tol) if gap < 0 else
                           _gap_line([li for li in coding.line_items if li not in marked], gap, tol))  # fmt: skip
            hint = ""
            if suspect is not None:
                name = tax_name(tl.tax_type, tl.province or prov)
                said = f"line {suspect.line_number} ({_short(suspect.description)}, {suspect.amount:,.2f})"
                hint = f": {said} may be exempt from {name}" if gap < 0 else f": {said} may carry {name} too"
            add(
                WARNING if _close(expected, tl.tax_amount, rounding) or suspect is not None else ERROR,
                "TAX_CALC_MISMATCH",
                f"{label}: {tl.taxable_amount:.2f} x {tl.rate:.5g} = {expected:.2f} "
                f"but invoice charges {tl.tax_amount:.2f}{hint}",
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
            add(WARNING, "TAX_PROVINCE_DIFFERS", f"{label} charged but place of supply is {prov}{assumed}")

    # Which invoice lines carry each tax (their tax code, D-64), and does their sum match the taxable amount? Compared
    # per tax type, because the same tax can be printed more than once (e.g. on goods and on freight).
    for tax_type in sorted(charged):
        name = tax_name(tax_type, prov)
        flagged = [li for li in coding.line_items if tax_type in li.taxes_applied]
        taxable = round(sum(t.taxable_amount for t in tax_lines if t.tax_type == tax_type), 2)
        if not flagged:
            add(WARNING, "TAX_NOT_ALLOCATED",
                f"{name} is charged but no line is marked with it: pick the tax code of the lines it applies "
                "to")  # fmt: skip
            continue
        base = round(sum(li.amount for li in flagged), 2)
        if not _close(base, taxable, tol):
            gap = round(taxable - base, 2)
            # A line marked exempt or zero-rated whose amount makes the tax add up: the vendor taxed it, or it is
            # not exempt after all (EXEMPT_LINE_TAXED says so instead of the bare difference).
            stated = [li for li in coding.line_items if not li.taxes_applied and li.tax_status in (EXEMPT, ZERO_RATED)]
            taxed = _gap_line(stated, gap, tol) if gap > 0 else None
            if taxed is not None:
                what = "exempt" if taxed.tax_status == EXEMPT else "zero-rated"
                add(WARNING, "EXEMPT_LINE_TAXED",
                    f"marked {what}, but the {name} charged adds up only with this line ({taxed.amount:,.2f}): the "
                    f"vendor charged {name} on it, or it is not {what}", taxed.line_number)  # fmt: skip
                continue
            # Name the line whose amount is the difference: marked and maybe exempt, or not marked and maybe taxed.
            suspect = (
                _gap_line(flagged, -gap, tol)
                if gap < 0
                else _gap_line([li for li in coding.line_items if tax_type not in li.taxes_applied], gap, tol)
            )
            hint = ""
            if suspect is not None:
                said = f"line {suspect.line_number} ({_short(suspect.description)}, {suspect.amount:,.2f})"
                hint = f": {said} may be exempt from {name}" if gap < 0 else f": {said} may carry {name} too"
            add(
                WARNING,
                "TAX_BASE_MISMATCH",
                f"lines marked {name} total {base:,.2f} but the {name} is charged on {taxable:,.2f} "
                f"({abs(gap):,.2f} {'more' if gap < 0 else 'less'} on the lines){hint}",
            )

    # Lines marked with a tax the invoice does not charge: exempt (or no tax charged) after all, or a tax to
    # self-assess. Said per line, once per tax; the province's own "PST not charged" is then not said again.
    posted_types = {tl.tax_type for tl in tax_lines if abs(tl.tax_amount) > 0.004}
    not_charged: set[str] = set()
    for li in coding.line_items:
        for tax_type in li.taxes_applied:
            if tax_type in posted_types:
                continue
            not_charged.add(tax_type)
            name = tax_name(tax_type, prov)
            todo = (f"mark it exempt or \"No tax charged\", or self-assess the {name}" if tax_type in ("PST", "QST")
                    else "mark it exempt or \"No tax charged\", or ask the vendor")  # fmt: skip
            add(WARNING, "LINE_TAX_NOT_CHARGED", f"marked {name}, but no {name} is charged: {todo}", li.line_number)

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
        name = PROVINCE_NAMES[prov] + assumed
        # Every line says why it carries no tax (exempt, zero-rated, out of scope): no provincial tax was due either.
        all_stated = bool(coding.line_items) and all(not li.taxes_applied and li.tax_status for li in coding.line_items)
        if not charged:
            add(
                WARNING,
                "NO_TAX_CHARGED",
                f"no sales tax charged on a supply in {name}; "
                + ("every line is marked exempt, zero-rated or out of scope: confirm it" if all_stated
                   else "confirm it is exempt or zero-rated"),
            )  # fmt: skip
        for extra in sorted(charged - expected_types):
            add(
                WARNING,
                "TAX_REGIME_MISMATCH",
                f"{extra} charged but {name} normally charges {' + '.join(sorted(expected_types))}",
            )
        if charged:
            for missing in sorted(expected_types - charged):
                if missing in not_charged or all_stated:
                    continue  # said line by line (LINE_TAX_NOT_CHARGED), or no line is taxable
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
            add(ERROR, "TAX_GL_UNMAPPED", f"no GL account is set for {tax_type}: set it in GL accounts & tax")
        elif t.needs_gl and known_gl_codes is not None and t.gl_code not in known_gl_codes:
            add(
                ERROR,
                "TAX_GL_UNKNOWN",
                f"{tax_type} posts to GL account {t.gl_code}, which is not in your GL accounts: fix it in "
                "GL accounts & tax",
            )
    return findings


def _gap_line(lines: list[Any], gap: float, tol: float) -> Any:
    """The one line whose amount is ``gap`` (within ``tol``), None when none or several are."""
    found = [li for li in lines if abs(li.amount - gap) <= tol + 1e-9 and abs(li.amount) > 0.004]
    return found[0] if len(found) == 1 else None


def _short(text: str, size: int = 32) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= size else text[: size - 1].rstrip() + "…"


def tax_name(tax_type: str, province: str = "") -> str:
    """A tax as said there: PST is RST in Manitoba, OTHER is a foreign tax (``line_tax.tax_name``)."""
    from .line_tax import tax_name as name

    return name(tax_type, province)


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
            # The lines whose tax code carries it; none marked: the lines not marked exempt, zero-rated or out of
            # scope (never spread onto a line that says it carries no tax), else every line.
            targets = (
                [(i, li) for i, li in items if tl.tax_type in li.taxes_applied]
                or [(i, li) for i, li in items if li.taxes_applied or not getattr(li, "tax_status", "")]
                or items
            )
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
