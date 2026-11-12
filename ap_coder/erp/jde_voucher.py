"""One JD Edwards E1 voucher, worked out once from an invoice (``_prepare``): its amounts in cents, its tax area and
explanation code, its payment terms code and due date (``terms_code``, ``sent_due``), its G/L distribution lines with
their accounts, and why it can't go (its problems, each with a check code). The Z-files, the keying sheet, the review
screen, the checks over a batch and the dry run all read this one voucher (``_vouchers``: each invoice's, prepared
once). How the distribution balances under each tax explanation code: ``jde.py``'s first lines.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from types import SimpleNamespace
from typing import Any

from ..coding.line_tax import exempt_of, legacy_basis
from ..coding.tax import CANADIAN_TAX_TYPES, OUTSIDE_CANADA, PLACE_OF_SUPPLY, PROVINCES
from ..coding.terms import terms_key
from .jde_accounts import VendorLookup, account_for, ani, resolve_an8
from .jde_formats import E1_YEARS, NO_DECIMAL_CURRENCIES, _date, _negative, _norm, _units
from .jde_settings import AMOUNT_GROSS, PST_PROVINCES, JdeSettings, TaxArea, gl_date_of, period_problem

# The two reasons an invoice is held back for its supplier number: the Exports page links them to Settings → JD
# Edwards E1 → Supplier address numbers (``an8_problem``).
NO_AN8 = "no JDE supplier number (AN8)"
BAD_AN8 = "is not a JDE address number"

EXEMPT_STATUSES = ("exempt", "zero_rated")  # a line's tax_status that E1 takes as exempt (D-64)

# Which taxes E1 itself posts (so they are NOT in the distribution), by tax explanation code.
RECOVERABLE_BY_CODE: dict[str, frozenset[str]] = {
    "V": frozenset({"GST", "HST", "QST", "PST"}),
    "C": frozenset({"GST", "HST"}),
    "B": frozenset({"GST", "HST"}),
    "E": frozenset(),
    "": frozenset(),
}

# Where a voucher's terms code comes from (``terms_code``); a credit memo is sent without one (B-896).
TERMS_TABLE, TERMS_DEFAULT, TERMS_CREDIT = "table", "default", "credit"
PTC_WIDTH = 3  # VLPTC


@dataclass
class _Voucher:
    inv_id: int
    final: dict[str, Any]
    problems: list[str]
    codes: list[tuple[str, int | None]] = field(default_factory=list)  # each problem's check code and invoice line
    currency: str = "CAD"
    foreign: bool = False
    decimals: int = 2
    province: str = ""
    tax_area: str = ""
    tax_code: str = ""
    gross: int = 0  # cents
    stam: int = 0
    atxa: int = 0
    recoverable: int = 0
    charged: list[dict[str, Any]] = field(default_factory=list)  # Canadian tax lines with an amount
    recoverable_types: frozenset[str] = frozenset()
    # The taxes the code would have E1 post that the Tax setup keeps in the expense (D-210): not on the pay item.
    expensed: frozenset[str] = frozenset()
    entries: list[dict[str, Any]] = field(default_factory=list)  # distribution lines (cents in "cents")
    po_matched: bool = False
    # The GST/HST/QST on meals not claimable (D-90): in the expense lines, so E1 must not post it as recoverable.
    meals: int = 0
    gl_moved: dt.date | None = None  # a G/L date in a closed period, moved to ``gl_day`` (D-182)
    gl_day: dt.date | None = None

    def add(self, problem: str, code: str = "JDE_VOUCHER", line: int | None = None) -> None:
        """A problem that holds the voucher back, with its check code and the invoice line it is on."""
        self.problems.append(problem)
        self.codes.append((code, line))


def _place_of_supply(final: dict[str, Any]) -> str:
    for key in ("ship_to_province", PLACE_OF_SUPPLY, "supplier_province"):  # printed, worked out, the vendor's
        prov = _norm(final.get(key))
        if prov in PROVINCES:
            return prov
    if OUTSIDE_CANADA in (_norm(final.get("ship_to_province")), _norm(final.get("supplier_province"))):
        return OUTSIDE_CANADA
    return ""


def _distribution(final: dict[str, Any]) -> list[dict[str, Any]]:
    # Not stored (an old invoice): rebuilt with the default tax treatments (``exports.distribution``); recoverable
    # taxes are dropped from the F0911Z1 lines anyway, so their GL accounts do not matter here.
    from .exports import distribution

    return distribution(final)


def _is_recoverable_entry(e: dict[str, Any]) -> bool:
    return e.get("kind") == "tax" and abs(float(e.get("non_recoverable_tax") or 0)) < 0.005


def _entry_tax(e: dict[str, Any]) -> str:
    """The tax of a tax distribution line ("HST"), from its description ("HST ON 13% (recoverable)")."""
    words = str(e.get("description") or "").split()
    return words[0].upper() if words else ""


def _recovered_types(dist: list[dict[str, Any]], final: dict[str, Any]) -> set[str]:
    """The taxes the G/L distribution recovers, as the Tax setup said when the invoice was coded: each has its own
    recoverable tax line, or, all of it on a meal with nothing left to claim (a cent), is kept on the meals lines."""
    found = {_entry_tax(e) for e in dist if _is_recoverable_entry(e)}
    tax_lines = final.get("tax_lines") or []
    for e in dist:
        for position in e.get("meals_tax_lines") or {}:
            if str(position).isdigit() and int(position) < len(tax_lines):
                found.add(str(tax_lines[int(position)].get("tax_type") or ""))
    return found


def sent_due(final: dict[str, Any], invoice: dict[str, Any] | None = None) -> dt.date | None:
    """The due date a voucher sends (VLDDJ, B-924): printed on the invoice, or worked out from the terms printed; None
    when there are none (or none understood), so E1 works it out from the supplier's own terms instead of being told
    AP Coder's default days to pay. A credit memo: its invoice date, as E1 needs one."""
    from ..coding.terms import payment

    invoice_date = _date(final.get("invoice_date"))
    if _negative(final.get("grand_total")):
        return invoice_date
    printed = _date(final.get("due_date"))
    if printed is not None:
        return printed
    if payment(final).source != "terms":  # no terms printed, or not understood: the due date would be a guess
        return None
    return _date((invoice or {}).get("due_date")) or payment(final).due


def terms_code(final: dict[str, Any], settings: JdeSettings) -> tuple[str, str, str]:
    """(VLPTC, the meaning of the printed terms, where the code comes from) of a voucher (D-91): the code of the
    printed terms' meaning in the table ("30 days" and "N30" are "Net 30"), else the default code, else blank (E1
    takes the supplier's own terms). A credit memo: blank, as its due date is not sent: a code can carry a discount
    (2/10 Net 30) that E1 would work out on the credit (B-896; to confirm with the JDE team, D-91)."""
    key = terms_key(str(final.get("payment_terms") or ""))
    if _negative(final.get("grand_total")):
        return "", key, TERMS_CREDIT
    code = settings.terms_codes.get(key, "").strip() if key else ""
    if code:
        return code, key, TERMS_TABLE
    default = settings.payment_terms_code.strip()
    return (default, key, TERMS_DEFAULT) if default else ("", key, "")


def terms_unmapped(final: dict[str, Any], settings: JdeSettings) -> str:
    """The meaning of the printed terms when the terms table is in use and has no code for them, nor a default: ""
    otherwise (no table: E1 takes the supplier's own terms, as before D-91; terms not understood: TERMS_NOT_FOUND)."""
    code, key, source = terms_code(final, settings)
    return key if settings.terms_codes and key and not code and source != TERMS_CREDIT else ""


def an8_problem(reason: str) -> bool:
    """Whether a reason ``validate`` gives is about the supplier's address number (set in Settings → JD Edwards E1)."""
    return reason.startswith(NO_AN8) or BAD_AN8 in reason


def _tax_area_code(final: dict[str, Any], settings: JdeSettings) -> tuple[list[dict[str, Any]], str, str, str]:
    """The Canadian tax lines charged, the province of supply, and the JDE tax area and explanation code for it."""
    charged = [
        t for t in final.get("tax_lines") or []
        if t.get("tax_type") in CANADIAN_TAX_TYPES and abs(float(t.get("tax_amount") or 0)) > 0.004
    ]  # fmt: skip
    types = {t["tax_type"] for t in charged}
    province = _place_of_supply(final)
    if settings.tax_on_gl:  # the tax is on its own G/L lines: no tax area, nothing for E1 to work out (D-93)
        return charged, province, "", settings.gl_tax_code
    row = settings.tax_map.get(province) or TaxArea("", "")
    area, code = row.tax_area.strip(), row.code.strip().upper()
    if charged and province in ("", OUTSIDE_CANADA):
        pass
    elif not charged and province != OUTSIDE_CANADA:
        code = settings.exempt_code.strip().upper()  # nothing charged: exempt (keeps the area)
    elif province in PST_PROVINCES and types & {"GST", "HST"} and "PST" not in types:
        # PST to self-assess: E1 accrues it (B) on the whole voucher, so only when every line owes it; on lines all
        # exempt from it there is none, and E1 handles no tax (the GST goes on its own G/L line, B-922). A mix is
        # held back (``PST_MIX``).
        owes = pst_owed(final)
        code = (
            settings.exempt_code.strip().upper() if owes == NONE_OWED else settings.self_assessed_code.strip().upper()
        )
    return charged, province, area, code


ALL_OWED, NONE_OWED, SOME_OWED = "all", "none", "some"


def pst_owed(final: dict[str, Any]) -> str:
    """Whether the PST not charged is owed on every line of the invoice (ALL_OWED), on none (NONE_OWED: each exempt,
    zero-rated or out of scope) or on some only (SOME_OWED). Lines with no amount, and tips, don't count."""
    from ..coding.line_tax import NOT_DUE, tax_state
    from ..coding.tax import is_tip

    items = [li for li in final.get("line_items") or [] if isinstance(li, dict) and abs(_units(li.get("amount")))]
    items = [li for li in items if not is_tip(SimpleNamespace(description=li.get("description")))] or items
    if not items:
        return ALL_OWED
    basis = legacy_basis(items, final.get("tax_lines") or [])
    exempt = [tax_state(li, "PST", basis) == NOT_DUE for li in items]
    return NONE_OWED if all(exempt) else SOME_OWED if any(exempt) else ALL_OWED


def accrues_use_tax(final: dict[str, Any], settings: JdeSettings) -> bool:
    """True when this invoice goes to JD Edwards with the self-assessed code (B) on a tax area that holds the PST: E1
    accrues the PST (use tax) itself, so the Self-assessment report must not have it booked again (B-716, B-722).
    Code B on an area that does not hold it accrues nothing: the report lists it (``self_assessed_pst``, B-2343)."""
    return _code_b(final, settings) is True


def self_assessed_pst(final: dict[str, Any], settings: JdeSettings) -> bool:
    """True when this invoice goes with the self-assessed code (B) on a tax area that holds no PST (the prototype's
    GST-only area, D-210): JD Edwards accrues none, the PST is AP's to self-assess (B-2343)."""
    return _code_b(final, settings) is False


def _code_b(final: dict[str, Any], settings: JdeSettings) -> bool | None:
    """With the self-assessed code (B), whether its tax area holds the PST; None: not code B."""
    if settings.tax_on_gl:  # no code B: the Self-assessment report has the PST (D-93)
        return None
    _charged, province, area, code = _tax_area_code(final, settings)
    if not code or code != settings.self_assessed_code.strip().upper():
        return None
    return _holds_pst(province, area, settings)


def _holds_pst(province: str, area: str, settings: JdeSettings) -> bool:
    """Whether a PST province's tax area holds its PST: an area that province's supplies alone go to. One shared with
    other provinces (the prototype's GST-only area for AB, BC, MB, SK...) holds only the tax they have in common, the
    GST: their PSTs differ, Alberta has none (D-227)."""
    return bool(area) and all(row.tax_area.strip() != area for p, row in settings.tax_map.items() if p != province)


PO_WIDTH = 8  # VLPO


def po_not_sent(final: dict[str, Any], settings: JdeSettings) -> bool:
    """Whether the PO number is left out of the voucher's PO reference because VLPO can't hold it (D-87): longer than
    8 characters, sent as a reference only (not a PO-matched voucher, where it holds the voucher back)."""
    po = str(final.get("po_number") or "").strip()
    return len(po) > PO_WIDTH and settings.send_po_reference and not settings.po_matched


# Why a voucher with meals on it is held back (D-90): E1 would claim the full ITC on them while AP Coder expenses half
# of it, so the GL and the ITC report would disagree.
MEALS_GROSS = (
    "meals and entertainment: with the gross amount only, JD Edwards works out the tax itself and would claim all of "
    "it, not half: switch to sending tax amounts in Settings → JD Edwards E1 (Amounts)"
)
MEALS_PO = (
    "meals and entertainment on a PO-matched voucher: JD Edwards distributes it from the receipts and would claim all "
    "the tax on the meals, not half: send it without the PO match, or post the half by hand in JD Edwards"
)
# With the tax coded to G/L accounts (D-93), a PO-matched voucher has no G/L lines of ours to put the tax on.
GL_PO = (
    "tax on a PO-matched voucher: JD Edwards distributes it from the receipts, and with the tax coded to G/L accounts "
    "it posts no tax of its own, so the tax would go nowhere: send it without the PO match"
)
# Code B accrues the PST on the whole voucher (B-922).
PST_MIX = (
    "PST is owed on some lines and not on others (exempt): with code B JD Edwards would accrue PST on every line. "
    "Check the lines' tax codes, or send it with the tax coded to G/L accounts and book the PST from the "
    "Self-assessment report"
)


def _area_types(province: str, code: str, on: dt.date | None) -> list[str]:
    """The taxes E1 works out of a gross amount for a tax area (gross mode): the province's VAT (HST; else GST, and QST
    in Quebec) and, with code C, its PST; code B accrues the PST on top (not in the gross); E or blank: none."""
    if code not in ("V", "C", "B") or province not in PROVINCES:
        return []
    rates = _rates()
    levied = [t for t in ("HST", "GST", "QST", "PST") if rates.rate_for(t, province, on) is not None]
    if "HST" in levied:
        return ["HST"]
    return [t for t in levied if t != "PST" or code in ("V", "C")]


_RATES: dict[str, Any] = {}


def _rates() -> Any:
    """The tax rate table (``data/canada_tax_rates.csv``, or the data folder's), read once per file change."""
    from ..coding.tax import TaxRateTable, rates_path

    path = rates_path()
    try:
        stamp = f"{path}:{path.stat().st_mtime_ns}"
    except OSError:
        stamp = str(path)
    if _RATES.get("stamp") != stamp:
        _RATES.update(stamp=stamp, table=TaxRateTable.load(path))
    return _RATES["table"]


def _gross_problems(v: _Voucher, final: dict[str, Any]) -> list[str]:
    """Why a voucher sent with the gross amount only would have E1 work out another tax than the invoice charges
    (B-921): a line exempt or zero-rated (E1 taxes the whole gross), or the tax charged not the area's rate on the
    gross."""
    on = _date(final.get("invoice_date"))
    types = _area_types(v.province, v.tax_code, on)
    if not types:
        return []
    if v.expensed:  # E1 would work the recovered tax out of an amount that holds the other (D-210)
        left = sorted(v.expensed)
        return [
            f"the {' and '.join(left)} {'is' if len(left) == 1 else 'are'} left in the expense (GL accounts & tax): "
            "with the gross amount only JD Edwards works the tax out of the whole amount, that tax included. Code "
            "the tax to G/L accounts or send tax amounts (Amounts): set it in Settings → JD Edwards E1, or ask "
            "whoever sets up AP Coder"
        ]
    items = final.get("line_items") or []
    basis = legacy_basis(items, final.get("tax_lines") or [])
    kinds = sorted({t["tax_type"] for t in v.charged})
    for li in items:
        if not isinstance(li, dict) or not abs(_units(li.get("amount"))) or li.get("taxes_applied"):
            continue
        if li.get("tax_status") in EXEMPT_STATUSES or (
            kinds and not li.get("tax_status") and set(kinds) <= set(exempt_of(li, basis))
        ):
            return [
                f"line {li.get('line_number') or '?'} carries no tax (exempt or zero-rated): with the gross amount "
                "only JD Edwards would tax it too. Code the tax to G/L accounts or send tax amounts (Amounts): set "
                "it in Settings → JD Edwards E1, or ask whoever sets up AP Coder"
            ]
    rates = _rates()
    rate = Decimal(str(round(sum(rates.rate_for(t, v.province, on) or 0.0 for t in types), 6)))
    charged = sum(_units(t.get("tax_amount"), v.decimals) for t in v.charged)
    works_out = int((Decimal(v.gross) * rate / (1 + rate)).quantize(Decimal(1), ROUND_HALF_UP))
    if abs(works_out - charged) <= 10 ** (2 - v.decimals) * (len(v.charged) + 1):  # a cent a tax line, rounding
        return []
    return [
        f"with the gross amount only, JD Edwards works out {float(rate) * 100:.4g}% tax for "
        f"{v.tax_area or v.province} ({works_out / 100:,.2f}), but the invoice charges {charged / 100:,.2f}. Check "
        "the tax, or ask the pilot tester to code the tax to G/L accounts or send tax amounts (Settings → JD Edwards "
        "E1, Amounts)"
    ]


def _two_tax_areas(v: _Voucher, final: dict[str, Any], settings: JdeSettings) -> str:
    """Why a voucher whose recovered GST/HST is charged at more than one rate is held back ("" otherwise): its lines
    were supplied in two provinces (the GST on some and the HST on others, or the HST at 13% and at 15%), a pay item
    has one tax area, and AP Coder makes one pay item. The reason spells out the pay items to key by hand, each with
    its tax area, taxable and tax amounts, and its lines where the lines' tax codes or amounts tell (B-2152): the
    figures are ``taxmath.invoice_taxes``'s ``areas``, as the review screen's Tax check has them (B-2227).

    Told from the tax lines' types and rates alone. Not seen: two provinces of one federal rate (Alberta and British
    Columbia or Quebec: the same GST, the provincial tax on some lines only, as on an invoice with exempt lines)."""

    def rate_of(t: dict[str, Any]) -> float:
        return round(float(t.get("rate") or 0), 5)

    federal = [t for t in v.charged if t["tax_type"] in ("GST", "HST") and t["tax_type"] in v.recoverable_types]
    rated = sorted({rate_of(t) for t in federal if t["tax_type"] == "HST"} - {0}) or [0.0]
    kinds = [("GST", 0.05)] if any(t["tax_type"] == "GST" for t in federal) else []
    kinds += [("HST", rate) for rate in rated if any(t["tax_type"] == "HST" for t in federal)]
    if len(kinds) < 2:
        return ""
    on = _date(final.get("invoice_date"))
    areas = _federal_taxes(final)
    labels, items = [], []
    for n, (kind, rate) in enumerate(kinds, 1):
        # (an HST line whose rate was not read goes with the first rate; the GST has one)
        mine = [t for t in federal if t["tax_type"] == kind and (kind == "GST" or (rate_of(t) or rated[0]) == rate)]
        province = next((p for p in (_norm(t.get("province")) for t in mine) if p in PROVINCES), "")
        if not province and kind in _area_types(v.province, "V", on):
            province = v.province
        row = settings.tax_map.get(province) or TaxArea("", "")
        label = f"{kind} {rate * 100:.6g}%" if rate else kind
        theirs = [a for a in areas if a.tax_type == kind and (kind == "GST" or (round(a.rate, 5) or rated[0]) == rate)]
        numbers = sorted({x for a in theirs for x in a.line_numbers})
        taxable, tax = (sum(_units(getattr(a, key), v.decimals) for a in theirs) for key in ("taxable", "tax"))
        labels.append(label)
        items.append(
            f"Pay item {n:03d}: tax area {row.tax_area.strip() or 'the one of the province it was supplied in'}, "
            f"code {row.code.strip().upper() or 'V'}, taxable amount {taxable / 100:,.2f}, tax amount "
            f"{tax / 100:,.2f} ({label}{': ' + _lines_said([x for x in numbers if x]) if any(numbers) else ''})"
        )
    count = {2: "two", 3: "three"}.get(len(kinds), str(len(kinds)))
    return (f"{' and '.join(labels)} are {'both' if len(kinds) == 2 else 'all'} charged (lines supplied in {count} "
            f"provinces): a pay item has one tax area, so this voucher is {count} pay items, to key by hand with the "
            f"G/L lines as coded. {'. '.join(items)}")  # fmt: skip


def _federal_taxes(final: dict[str, Any]) -> tuple[Any, ...]:
    """The GST and each province's HST the invoice charges, with the lines that carry each
    (``taxmath.invoice_taxes``'s ``areas``)."""
    from ..coding.tax import TaxSetup
    from ..coding.taxmath import invoice_taxes
    from ..core.schema import InvoiceCoding

    coding = InvoiceCoding.model_validate({k: x for k, x in final.items() if k != "gl_distribution"})
    return invoice_taxes(coding, TaxSetup(_rates(), {})).areas


def _prepare(inv: dict[str, Any], settings: JdeSettings, an8: str, on: dt.date | None = None) -> _Voucher:
    """The voucher of ``inv`` for ``an8``, checked; ``on``: the batch's day (else today), its G/L date's when that
    is the export day, in the open period or not (B-2489)."""
    final = inv.get("final_output") or {}
    v = _Voucher(int(inv.get("id") or 0), final, [])
    add = v.add
    po = _check_header(v, final, settings, an8, on)
    charged = _check_tax_area(v, final, settings)
    on_gl = settings.tax_on_gl
    dist = _distribution(final)
    recoverable = _tax_amounts(v, final, inv, settings, charged, dist)
    if two := _two_tax_areas(v, final, settings):  # a pay item has one tax area: held, the two spelled out
        add(two, "JDE_TAX_AREA")

    # Meals (D-90): half the tax on a meals line is not claimable, and is in its expense line, not E1's to post.
    meals = sum(_units(e.get("meals_tax"), v.decimals) for e in dist if e.get("kind") == "expense")
    if v.recoverable_types and meals:
        v.meals = meals
        v.recoverable -= meals
        v.stam -= meals
    v.po_matched = bool(settings.po_matched and po)
    if meals and v.po_matched:  # no G/L lines of ours: E1 distributes from the receipts (B-865)
        add(MEALS_PO, "JDE_MEALS_PO")
    elif meals and settings.amount_mode == AMOUNT_GROSS:  # E1 works out the tax and claims all of it (B-866)
        add(MEALS_GROSS, "JDE_MEALS_GROSS")
    if on_gl and v.po_matched and charged:  # no G/L lines of ours, and no tax E1 would post either (D-93)
        add(GL_PO, "JDE_GL_PO")
    if settings.amount_mode == AMOUNT_GROSS and charged and not meals:
        for problem in _gross_problems(v, final):  # E1 would work the tax out other than charged (B-921)
            add(problem, "JDE_GROSS_TAX")
    if v.po_matched:
        items = final.get("line_items") or []
        lines = sum(_units(li.get("amount")) for li in items)
        if not items:
            add("PO-matched voucher without invoice lines to match", "JDE_UNBALANCED")
        elif abs(lines - _units(final.get("subtotal"))) > len(items):
            add(
                f"the lines add up to {lines / 100:,.2f}, not the subtotal {float(final.get('subtotal') or 0):,.2f}",
                "SUBTOTAL_MISMATCH",
            )
        return v

    _check_distribution(v, final, settings, dist, recoverable)
    return v


def _check_header(v: _Voucher, final: dict[str, Any], settings: JdeSettings, an8: str,
                  on: dt.date | None = None) -> str:  # fmt: skip
    """The supplier number, invoice number, PO number, dates and currency; the PO number."""
    add = v.add
    if not an8:
        add(f"{NO_AN8}: set the vendor's ERP ID in the vendor master, or in Settings → JD Edwards", "JDE_NO_AN8")
    elif not an8.isdigit() or len(an8) > 8:
        add(f"supplier number {an8!r} {BAD_AN8} (digits only, up to 8)", "JDE_BAD_AN8")
    number = str(final.get("invoice_number") or "").strip()
    if not number:
        add("no invoice number (VLVINV)", "MISSING_INVOICE_NUMBER")
    elif len(number) > 25:
        add(f"invoice number {number!r} is longer than 25 characters (VLVINV)", "JDE_INVOICE_NUMBER_LONG")
    po = str(final.get("po_number") or "").strip()
    # A PO-matched voucher is matched to E1's order by its number: one longer than VLPO holds can't be. As a reference
    # only, it is left out of VLPO and kept in AP Coder (``po_not_sent``, D-87).
    if po and len(po) > PO_WIDTH and settings.po_matched:
        add(f"PO number {po!r} is longer than 8 characters (VLPO)", "JDE_PO_LONG")
    invoice_date = _date(final.get("invoice_date"))
    if invoice_date is None:
        add("no invoice date", "INVOICE_DATE_INVALID")
    elif invoice_date.year not in E1_YEARS:
        add(f"invoice date {invoice_date} is outside what JD Edwards dates hold (1900 to 2899)", "JDE_DATE_RANGE")
    else:  # the open period (D-182): the batch's day (``on``, else today) for a G/L date of the export day
        day, v.gl_moved = gl_date_of(final, settings, on)
        if why := period_problem(day, settings):
            add(why, "JDE_PERIOD_CLOSED")
        elif v.gl_moved:
            v.gl_day = day

    v.currency = _norm(final.get("currency")) or settings.domestic_currency.upper()
    allowed = {_norm(c) for c in settings.currencies} | {_norm(settings.domestic_currency)}
    if v.currency not in allowed:
        add(f"currency {v.currency} is not set up for JD Edwards (Settings → JD Edwards → currencies)", "JDE_CURRENCY")
    v.foreign = v.currency != _norm(settings.domestic_currency)
    v.decimals = NO_DECIMAL_CURRENCIES.get(v.currency, 2)
    return po


def _check_tax_area(v: _Voucher, final: dict[str, Any], settings: JdeSettings) -> list[dict[str, Any]]:
    """The tax area and explanation code (``v``'s), and the Canadian tax lines charged."""
    add = v.add
    # Tax area and explanation code from the province of supply.
    # With the tax coded to G/L accounts (D-93) there is no tax area: none of these is asked.
    charged, v.province, v.tax_area, v.tax_code = _tax_area_code(final, settings)
    on_gl = settings.tax_on_gl
    if on_gl:
        pass
    elif charged and v.province in ("", OUTSIDE_CANADA):
        add("province of supply unknown: no JDE tax area for the Canadian tax charged", "JDE_TAX_AREA")
    elif v.tax_code not in RECOVERABLE_BY_CODE:
        add(
            f"tax explanation code {v.tax_code!r} is not one AP Coder can balance (V, C, B, E or blank)", "JDE_TAX_AREA"
        )
    elif charged and v.tax_code in ("V", "C", "B") and not v.tax_area:
        add(f"no JDE tax area for {v.province} (Settings → JD Edwards → tax areas)", "JDE_TAX_AREA")
    elif v.tax_code == settings.self_assessed_code.strip().upper() and pst_owed(final) == SOME_OWED:
        add(PST_MIX, "JDE_TAX_AREA")
    return charged


def _tax_amounts(v: _Voucher, final: dict[str, Any], inv: dict[str, Any], settings: JdeSettings,
                 charged: list[dict[str, Any]], dist: list[dict[str, Any]]) -> list[dict[str, Any]]:  # fmt: skip
    """The gross, the due date, the tax and its taxable amount, and the recoverable tax (``v``'s); the
    recoverable tax lines."""
    add = v.add
    on_gl = settings.tax_on_gl
    # In the currency's own units: whole yen are sent as whole yen, so the voucher balances as E1 reads it.
    v.gross = _units(final.get("grand_total"), v.decimals)
    due = sent_due(final, inv)
    if settings.send_due_date and v.gross >= 0 and due is not None and due.year not in E1_YEARS:
        # (a credit's is not sent)
        add(f"due date {due} is outside what JD Edwards dates hold (1900 to 2899)", "JDE_DATE_RANGE")
    recovers = frozenset() if on_gl else RECOVERABLE_BY_CODE.get(v.tax_code, frozenset())
    # Code V has E1 post every tax on the pay item as recoverable: one the Tax setup keeps in the expense (the QST
    # not recovered, the GST still claimed) is not the pay item's. It stays in the G/L lines, in the non-taxable
    # amount (D-210, B-2086). Codes C and B keep the provincial tax in the tax amount: the area's sales tax.
    if v.tax_code == "V" and dist:
        v.expensed = frozenset(t["tax_type"] for t in charged) & (recovers - _recovered_types(dist, final))
    on_item = [t for t in charged if t["tax_type"] not in v.expensed]
    if v.expensed and not on_item:  # no tax left on the pay item: the exempt code, as when none is charged (B-2150)
        v.tax_code, recovers = settings.exempt_code.strip().upper(), frozenset()
    v.stam = sum(_units(t.get("tax_amount"), v.decimals) for t in on_item)
    # The taxable amount of each tax, a tax printed twice (goods, freight) added up (B-923); GST and QST on the same
    # lines have the same one: the widest.
    bases: dict[str, int] = {}
    for t in on_item:
        bases[t["tax_type"]] = bases.get(t["tax_type"], 0) + _units(t.get("taxable_amount"), v.decimals)
    v.atxa = max(bases.values(), key=abs, default=0)
    v.charged, v.recoverable_types = charged, recovers - v.expensed
    recoverable = [t for t in charged if t["tax_type"] in v.recoverable_types]
    v.recoverable = sum(_units(t.get("tax_amount"), v.decimals) for t in recoverable)
    return recoverable


def _check_distribution(v: _Voucher, final: dict[str, Any], settings: JdeSettings, dist: list[dict[str, Any]],
                        recoverable: list[dict[str, Any]]) -> None:  # fmt: skip
    """The G/L distribution's entries (``v.entries``), and that they balance the gross less the recoverable tax."""
    add = v.add
    if not dist:
        add("no GL distribution to post", "NO_LINE_ITEMS")
    total = sum(_units(e.get("amount")) for e in dist)
    gross_cents = _units(final.get("grand_total"))
    if dist and total != gross_cents:
        add(
            f"the GL distribution adds up to {total / 100:,.2f}, not the invoice total {v.gross / 100:,.2f}",
            "POSTING_UNBALANCED",
        )
    _distribution_entries(v, settings, dist)
    from .exports import entry_lines

    for e, li in zip(v.entries, entry_lines(v.entries, final.get("line_items") or []), strict=True):
        if li is not None and li.get("remark"):  # the invoice line's own remark (``line_remark``, D-213)
            e["remark"] = li["remark"]
        elif li is not None and li.get("remark_from") == "person":  # emptied on purpose: keyed empty (D-229, B-2488)
            e["remark"], e["remark_from"] = "", "person"
    expected = v.gross - v.recoverable
    kept = sum(e["cents"] for e in v.entries)
    kept_cents = sum(_units(e.get("amount")) for e in v.entries)
    if v.decimals < 2 and v.entries and kept != expected:
        # Whole units (yen): a distribution spread in cents (coded before tax was spread in whole units) balances
        # in cents, but its lines each rounded to the yen can be a yen or two off: the largest line takes it.
        if kept_cents == gross_cents - sum(_units(t.get("tax_amount")) for t in recoverable):
            biggest = max(range(len(v.entries)), key=lambda i: abs(v.entries[i]["cents"]))
            v.entries[biggest]["cents"] += expected - kept
            kept = expected
    # A line with no GL account, or a province of supply not read, is said by its own finding and holds the voucher
    # back by itself: the imbalance it causes is no Tax setup problem to send the clerk to (B-812).
    said = {code for code, _ in v.codes} & {"GL_UNASSIGNED", "JDE_TAX_AREA", "TAX_GL_UNMAPPED"}
    if dist and total == gross_cents and kept != expected and not said:
        add(
            f"unbalanced: the distribution is {kept / 100:,.2f} but gross {v.gross / 100:,.2f} minus recoverable "
            f"tax {v.recoverable / 100:,.2f} (code {v.tax_code or 'blank'}) is {expected / 100:,.2f}: check the Tax "
            "setup against the JDE tax explanation code",
            "JDE_UNBALANCED",
        )


def _distribution_entries(v: _Voucher, settings: JdeSettings, dist: list[dict[str, Any]]) -> None:
    """Each G/L line with its JD Edwards account (``v.entries``), or the problem that holds it back."""
    add = v.add
    for e in dist:
        if _is_recoverable_entry(e) and v.recoverable_types:
            continue  # E1 posts recoverable tax itself (tax AAIs); with code E (or the tax on G/L) it is ours to post
        where = f"GL {e.get('gl_code') or '(none)'}" + (f" / {e['cost_center']}" if e.get("cost_center") else "")
        if e.get("kind") == "tax":
            where = f"GL {e.get('gl_code') or '(none)'} (the {_entry_tax(e) or 'tax'} tax account)"
        if e.get("kind") == "tax" and not str(e.get("gl_code") or "").strip():
            add(
                f"the {_entry_tax(e) or 'tax'} tax line has no GL account: set it in GL accounts & tax",
                "TAX_GL_UNMAPPED",
            )
            v.entries.append({**e, "cents": _units(e.get("amount"), v.decimals), "account": None})
            continue
        if str(e.get("gl_code") or "").strip().upper() in ("", "UNASSIGNED"):
            add(
                f"a line has no GL account ({e.get('description') or 'no description'}): code it in the review screen",
                "GL_UNASSIGNED",
                e.get("line_number"),
            )
            continue
        account = account_for(e.get("gl_code") or "", e.get("cost_center") or "", settings)
        if account is None:
            add(
                f"{where}{NO_ACCOUNT_SAID}",
                "JDE_NO_ACCOUNT",
                e.get("line_number"),
            )
        elif len(account.bu) > 12 or len(account.obj) > 6 or len(account.sub) > 8:
            add(
                f"{where}: JDE account {ani(account)} is too long (BU 12, object 6, subsidiary 8 characters)",
                "JDE_NO_ACCOUNT",
                e.get("line_number"),
            )
        elif len(account.sbl) > 8 or len(account.sblt) > 1:
            add(
                f"{where}: JDE account {ani(account)} has a subledger too long ({account.sbl or 'blank'}, type "
                f"{account.sblt or 'blank'}: subledger 8, type 1 character)",
                "JDE_NO_ACCOUNT",
                e.get("line_number"),
            )
        v.entries.append({**e, "cents": _units(e.get("amount"), v.decimals), "account": account})


def sent_tax(invoice: dict[str, Any], settings: JdeSettings, an8: str = "") -> float | None:
    """The tax a voucher gives JD Edwards (pilot-score, B-934): with tax amounts, VLSTAM (the claimable tax: half of it
    on meals, D-90); with the tax coded to G/L accounts, the recoverable tax on its own G/L lines; with the gross only
    None (E1 works it out from the tax area)."""
    if settings.amount_mode == AMOUNT_GROSS:
        return None
    v = _prepare(invoice, settings, an8)
    if settings.tax_on_gl:
        cents = sum(e["cents"] for e in v.entries if _is_recoverable_entry(e))
    else:
        cents = v.stam
    return round(cents / 100, 2)


def _vouchers(invoices: list[dict[str, Any]], settings: JdeSettings, vendor_lookup: VendorLookup,
              on: dt.date | None = None) -> list[tuple[str, _Voucher]]:  # fmt: skip
    """Each invoice's (supplier number, voucher), in their order: prepared once for a whole check, export or dry run,
    then handed to ``validate``, ``voucher_rows`` and ``dry_run`` as ``prepared``."""
    out = []
    for inv in invoices:
        an8 = resolve_an8(inv.get("final_output") or {}, vendor_lookup, settings)
        out.append((an8, _prepare(inv, settings, an8, on)))
    return out


NO_ACCOUNT_SAID = " has no JDE account (BU.Object.Subsidiary): add it to the account mapping"


def _lines_said(numbers: Iterable[int]) -> str:
    """The invoice lines a problem is on, in the order given: "line 3", "lines 1, 3"."""
    said = [str(n) for n in numbers]
    return f"line{'s' if len(said) > 1 else ''} {', '.join(said)}"


def _accounts_once(v: _Voucher) -> list[str]:
    """A voucher's problems, an account without a JDE account said once with the lines posting to it ("GL 1500 has no
    JDE account (lines 1, 3): …"), not once a line (B-1301)."""
    lines: dict[str, list[int]] = {}
    for problem, (code, line) in zip(v.problems, v.codes, strict=True):
        if code == "JDE_NO_ACCOUNT":
            lines.setdefault(problem, []).extend([line] if line else [])
    out: list[str] = []
    for problem in dict.fromkeys(v.problems):
        numbers = sorted(set(lines.get(problem) or []))
        if not numbers:
            out.append(problem)
            continue
        said = _lines_said(numbers)
        if problem.endswith(NO_ACCOUNT_SAID):
            out.append(f"{problem.removesuffix(NO_ACCOUNT_SAID)} has no JDE account ({said}): add it to the account "
                       "mapping (BU.Object.Subsidiary)")  # fmt: skip
        else:
            out.append(f"{problem} ({said})")
    return out
