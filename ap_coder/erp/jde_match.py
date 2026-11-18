"""A PO-matched voucher as it is keyed in JD Edwards' Voucher Match (P0411, D-264): its rows, each invoice line on the
PO line it bills, and the tax on them (D-265), so that the amounts matched and the tax JD Edwards works out on them
add up to the gross under every tax area and explanation code.

In Voucher Match JD Edwards works the tax out of each row taxed Y (the amount matched, at the row's tax rate/area,
under its explanation code), the way it does from the gross amount: so the amount matched is the line's amount before
tax, and the gross is the amounts matched plus the tax the rows' tax area holds. Under each code, as the G/L-coded
voucher (``jde_voucher``) has it:

* ``V`` (GST, HST, or GST + QST on an area that holds the QST): every tax the area holds is recovered;
* ``C`` (GST + PST on an area that holds the PST): the GST recovered, the PST the area's sales tax, in the expense;
* ``B`` (the PST not charged, self-assessed): the GST recovered, the PST accrued by JD Edwards on top, not in the gross
  (on an area that holds no PST, AP's to book from the Self-assessment report);
* the exempt code (nothing charged on a line, exempt or zero-rated): Tax N.

A tax the invoice charges that the voucher's tax area does not hold (the PST or QST of a province whose area is a
GST-only one shared with others, the prototype's ``CDNGST``) is not JD Edwards' to work out: kept in the expense by the
Tax setup, it goes on a G/L line of its own on the PO line's account, Tax N. A charge the PO has no line for (freight,
a fuel surcharge) is one too when Settings → JD Edwards E1 says so (``JdeSettings.charge_account``). What can't be
keyed so holds the voucher with why, never guessed: a line taxed with part of what its area holds (GST only on a GST +
PST area) and no tax area of its own on its PO line, a tax recovered that JD Edwards would not work out, a tax kept in
the expense that it would recover.

Entry point: ``keyed_match``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..coding.line_tax import tax_name
from .jde_accounts import account_for, ani
from .jde_formats import _date, _units
from .jde_remarks import line_remark
from .jde_settings import JdeSettings
from .jde_voucher import (
    RECOVERABLE_BY_CODE,
    _area_types,
    _distribution,
    _holds_pst,
    _rates,
    _recovered_types,
    _Voucher,
    accrues_use_tax,
)

if TYPE_CHECKING:
    from ..coding.po import VoucherMatch

ORDER = ("GST", "HST", "QST", "PST")  # how the taxes are said


@dataclass
class MatchLine:
    """One row of a PO-matched voucher as it is keyed in Voucher Match (P0411, D-264)."""

    order_number: str
    order_type: str
    order_company: str
    line: str  # the PO line's own number: "12.000"
    quantity: float | None  # quantity matched; None (left empty): the PO line has none, the invoice bills none
    uom: str
    amount: float  # amount matched: the invoice line's, before tax
    account: str  # the PO line's, BU.Object.Subsidiary
    taxed: str  # Tax Y/N
    tax_code: str
    tax_area: str
    remark: str
    # False: a G/L line of its own, not matched to the PO (a charge the PO has no line for, a tax JD Edwards does not
    # work out on the rows, D-265)
    on_po: bool = True


@dataclass
class KeyedMatch:
    """A PO-matched voucher as keyed (``keyed_match``): its rows, the PO lines' first, then the G/L lines of its own;
    ``tax``: what JD Edwards works out on the rows taxed Y, on top of the amounts matched (cents); ``held``: why it
    can't be keyed so; ``warnings`` and ``notes``: what is said under it."""

    rows: list[MatchLine] = field(default_factory=list)
    tax: int = 0
    held: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        """The rows' amounts, added up (cents)."""
        return sum(_units(row.amount) for row in self.rows)


def row_taxes(v: _Voucher, settings: JdeSettings) -> list[str]:
    """The taxes JD Edwards works out on a row taxed Y at the voucher's tax area and explanation code: the area's
    (``jde_voucher._area_types``: the GST or HST, the QST, and the PST with code V or C), its provincial tax only when
    the area is that province's own (``_holds_pst``: one shared with other provinces holds the GST alone); none with
    the exempt code or no tax area."""
    if settings.tax_on_gl or not v.tax_area:
        return []
    types = _area_types(v.province, v.tax_code, _date(v.final.get("invoice_date")))
    if not _holds_pst(v.province, v.tax_area, settings):
        types = [t for t in types if t in ("GST", "HST")]
    return types


def _line_shares(v: _Voucher) -> dict[int, dict[str, int]]:
    """{invoice line number: {tax: its share of the tax charged, in cents}}: ``taxmath.invoice_taxes``' amounts, as
    the review screen's Tax check has them."""
    from ..coding.tax import TaxSetup
    from ..coding.taxmath import invoice_taxes
    from ..core.schema import InvoiceCoding

    coding = InvoiceCoding.model_validate({k: x for k, x in v.final.items() if k != "gl_distribution"})
    out: dict[int, dict[str, int]] = {}
    for line in invoice_taxes(coding, TaxSetup(_rates(), {})).lines:
        mine = out.setdefault(line.line_number, {})
        for t in line.taxes:
            mine[t.tax_type] = mine.get(t.tax_type, 0) + _units(t.amount, v.decimals)
    return out


def _names(types: Any, province: str) -> str:
    """The taxes as said where the invoice is supplied: "GST and PST", "RST" in Manitoba."""
    return " and ".join(tax_name(t, province) for t in ORDER if t in types)


def _money(cents: int) -> str:
    return f"{cents / 100:,.2f}"


def _charged(v: _Voucher, tax_type: str) -> int:
    """The tax of ``tax_type`` the invoice charges (cents)."""
    return sum(_units(t.get("tax_amount"), v.decimals) for t in v.charged if t["tax_type"] == tax_type)


def keyed_match(v: _Voucher, matched: VoucherMatch, settings: JdeSettings) -> KeyedMatch:
    """The rows of a PO-matched voucher (``coding.po.voucher_match``'s) as keyed: the order type and company the PO
    file gave (else Settings'), each row's amount matched (the line's before tax), its Tax Y/N, explanation code and
    rate/area from what the invoice charges on its line, its remark; then a G/L line on the PO line's account for each
    tax JD Edwards would not work out; and whether it all adds up to the gross (the module's first lines)."""
    out = KeyedMatch(held=list(matched.held))
    final, province = v.final, v.province
    items = {li.get("line_number"): li for li in final.get("line_items") or [] if isinstance(li, dict)}
    exempt = settings.exempt_code.strip().upper()
    company = settings.po_company.strip()
    company = (company.zfill(5) if company.isdigit() else company) or settings.company_code
    charged = {t["tax_type"] for t in v.charged}
    area = row_taxes(v, settings)
    on_rows = charged & set(area)  # JD Edwards works them out on the rows taxed Y
    apart = charged - set(area)  # charged, and not JD Edwards' to work out: on G/L lines of their own
    recovered = _recovered_types(_distribution(final), final)  # the Tax setup recovers them
    said = _names
    if apart & recovered:
        left = apart & recovered
        out.held.append(f"the {said(left, province)} is recovered (GL accounts & tax), but tax area "
                        f"{v.tax_area or '(none)'} (code {v.tax_code or 'blank'}) has JD Edwards work out "
                        f"{said(area, province) or 'no tax'} on a voucher match, so the {said(left, province)} would "
                        f"go nowhere: give {province or 'its province'} a tax area and code that hold it (Settings → "
                        "JD Edwards E1 → tax areas), or key the voucher by hand")  # fmt: skip
    kept = (on_rows & RECOVERABLE_BY_CODE.get(v.tax_code, frozenset())) - recovered
    if kept:
        out.held.append(f"the {said(kept, province)} is kept in the expense (GL accounts & tax), but tax area "
                        f"{v.tax_area} with code {v.tax_code} has JD Edwards recover it on a voucher match: set it "
                        f"to recoverable, or give {province} a code that keeps it in the expense (C), in Settings → "
                        "JD Edwards E1 → tax areas")  # fmt: skip
    shares = _line_shares(v)
    taxed_rows: list[int] = []
    for row in matched.rows:
        li = items.get(row.invoice_line) or {}
        carried = {t for t, cents in (shares.get(row.invoice_line) or {}).items() if cents} & charged
        mine = carried & set(area)
        flag, po_code, po_area = row.tax
        if area and mine == set(area):
            taxed, code, at = "Y", v.tax_code, v.tax_area
        elif not mine:
            taxed, code, at = "N", v.tax_code if v.tax_code in ("", exempt) else exempt, v.tax_area
        elif flag == "Y" and po_area and po_area != v.tax_area:  # the PO line's own area: JD Edwards' setup's
            taxed, code, at = "Y", po_code or v.tax_code, po_area
            out.notes.append(f"Order {row.order_number} line {row.line} is charged the {said(mine, province)} and not "
                             f"the {said(set(area) - mine, province)}: its row takes the PO line's own tax rate/area "
                             f"{po_area} (code {code}). Check JD Edwards works out the {said(mine, province)} alone "
                             "on it.")  # fmt: skip
        else:
            where = f"line {row.invoice_line} “{row.description[:40]}”"
            out.held.append(f"{where} is charged the {said(mine, province)} and not the "
                            f"{said(set(area) - mine, province)}, but tax area {v.tax_area} (code {v.tax_code}) has "
                            f"JD Edwards work out both on its row: give its PO line a tax rate/area for the "
                            f"{said(mine, province)} alone in JD Edwards and load the purchase orders again (with "
                            "their Tax Rate/Area column), or key the voucher by hand")  # fmt: skip
            taxed, code, at = "Y", v.tax_code, v.tax_area
        if flag and row.on_po and (flag, po_code or code, po_area or at) != (taxed, code, at):
            out.notes.append(f"Order {row.order_number} line {row.line}: JD Edwards has the PO line's tax as "
                             f"{flag}, {po_code or 'no code'}, {po_area or 'no tax area'}; type {taxed}, "
                             f"{code or 'no code'}, {at or 'no tax area'} as on this row, which is what the invoice "
                             "charges on it.")  # fmt: skip
        if taxed == "Y":
            taxed_rows.append(len(out.rows))
        out.rows.append(MatchLine(
            row.order_number, (row.order_type or settings.po_document_type.strip()) if row.on_po else "",
            (row.order_company or company) if row.on_po else "", row.line, row.quantity, row.uom, row.amount,
            row.account, taxed, code, at, line_remark(li), on_po=row.on_po))  # fmt: skip
        if not row.on_po:
            out.notes.append(f"Line {row.invoice_line} “{row.description[:40]}” ({row.amount:,.2f}) is not on "
                             f"PO {str(final.get('po_number') or '').strip()}: it is keyed as a G/L line of its own on "
                             f"{row.account} (Settings → JD Edwards E1 → Charges not on the purchase order), Tax "
                             f"{taxed}. Voucher Match takes a line that is on no order when its processing options "
                             "allow it: ask the JD Edwards team if it refuses one.")  # fmt: skip
    _apart_lines(v, matched, out, apart - recovered, shares, items, settings)
    out.tax = sum(_charged(v, t) for t in on_rows)
    _tax_said(v, settings, out, on_rows, taxed_rows, area)
    if not out.held and out.total + out.tax != v.gross:
        out.held.append(f"the amounts matched ({_money(out.total)}) and the tax JD Edwards works out on them "
                        f"({_money(out.tax)}) add up to {_money(out.total + out.tax)}, not the gross "
                        f"{_money(v.gross)}: check the lines and the taxes against the invoice")  # fmt: skip
    return out


def _apart_lines(v: _Voucher, matched: VoucherMatch, out: KeyedMatch, apart: set[str],
                 shares: dict[int, dict[str, int]], items: dict[Any, dict[str, Any]],
                 settings: JdeSettings) -> None:  # fmt: skip
    """The taxes the invoice charges that JD Edwards would not work out on the rows and the Tax setup keeps in the
    expense (``apart``): a G/L line on each PO line's account with its lines' share (what is left over of a tax on the
    largest), Tax N, after the rows."""
    if not apart:
        return
    exempt = settings.exempt_code.strip().upper()
    by_account: dict[str, int] = {}
    for t in (t for t in ORDER if t in apart):
        mine: list[tuple[str, int]] = []
        for row in matched.rows:
            cents = (shares.get(row.invoice_line) or {}).get(t, 0)
            if cents:
                li = items.get(row.invoice_line) or {}
                own = account_for(li.get("predicted_gl_code") or "", li.get("predicted_cost_center") or "", settings)
                mine.append((row.account or (ani(own) if own is not None else ""), cents))
        printed = _charged(v, t)
        if mine and (left := printed - sum(c for _, c in mine)):
            biggest = max(range(len(mine)), key=lambda i: abs(mine[i][1]))
            mine[biggest] = (mine[biggest][0], mine[biggest][1] + left)
        if not mine:
            out.held.append(f"the {tax_name(t, v.province)} ({_money(printed)}) is on no line matched: JD Edwards "
                            "would not work it out and it has no line to go with. Key the voucher by hand")  # fmt: skip
        for account, cents in mine:
            if not account:
                out.held.append(f"the {tax_name(t, v.province)} kept in the expense has no account to go to: the PO "
                                "line has none in the purchase orders loaded, nor the invoice line. Load the purchase "
                                "orders again with their Account Number column")  # fmt: skip
                continue
            by_account[account] = by_account.get(account, 0) + cents
    names = _names(apart, v.province)
    for account, cents in by_account.items():
        out.rows.append(MatchLine("", "", "", "", None, "", cents / 100, account, "N",
                                  exempt, "", f"{names} not recovered"[:30], on_po=False))  # fmt: skip
    total = sum(by_account.values())
    out.notes.append(f"The {names} ({_money(total)}) is not recovered and tax area {v.tax_area or '(none)'} does not "
                     f"hold it, so JD Edwards would not work it out: it is keyed on a G/L line of its own on the PO "
                     f"line's account (not on the PO, Tax N). To have JD Edwards work it out on the rows instead, "
                     f"give {v.province} a tax area that holds its {names} with code C, which keeps it in the expense "
                     "(Settings → JD Edwards E1 → tax areas).")  # fmt: skip


def _tax_said(v: _Voucher, settings: JdeSettings, out: KeyedMatch, on_rows: set[str], taxed_rows: list[int],
              area: list[str]) -> None:  # fmt: skip
    """What the tax on the rows is (``notes``), and a warning when JD Edwards, at the area's rate, would work out
    another tax than the invoice charges."""
    province = v.province
    if on_rows:
        each = ", ".join(f"{tax_name(t, province)} {_money(_charged(v, t))}" for t in ORDER if t in on_rows)
        kept = on_rows - RECOVERABLE_BY_CODE.get(v.tax_code, frozenset())
        how = "recovered" if not kept else (f"the {_names(on_rows - kept, province)} recovered, the "
                                            f"{_names(kept, province)} the area's sales tax, in the "
                                            "expense")  # fmt: skip
        out.notes.insert(0, f"Tax: JD Edwards works out the tax on the rows taxed Y (code {v.tax_code}, tax area "
                            f"{v.tax_area}): {each}, {how}. The amounts matched are before tax.")  # fmt: skip
    if accrues_use_tax(v.final, settings):
        out.notes.append(f"Code {v.tax_code}: JD Edwards accrues the {tax_name('PST', province)} not charged on the "
                         "rows taxed Y itself (self-assessed): it is not in the gross.")  # fmt: skip
    rows = [out.rows[i] for i in taxed_rows]
    if not rows or any((r.tax_code, r.tax_area) != (v.tax_code, v.tax_area) for r in rows):
        return
    on = _date(v.final.get("invoice_date"))
    rate = sum(_rates().rate_for(t, province, on) or 0.0 for t in area)
    works_out = sum(round(_units(r.amount) * rate) for r in rows)
    if rate and abs(works_out - out.tax) > len(rows) + 1:
        out.warnings.append(f"At tax area {v.tax_area}'s {rate * 100:.4g}% ({_names(area, province)}), JD Edwards "
                            f"works out {_money(works_out)} on the rows taxed Y, but the invoice charges "
                            f"{_money(out.tax)}: check the tax JD Edwards shows against the invoice before saving the "
                            "voucher.")  # fmt: skip
