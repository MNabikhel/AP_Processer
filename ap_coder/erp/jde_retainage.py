"""A subcontract's progress billing as it is keyed in JD Edwards' Voucher Match (D-273): each row's Retained Amount,
the holdback of the billing (``capture.reader.progress``, kept beside the coding), and whether its tax is charged on
the work before the holdback or after it.

In Voucher Match a subcontract line (order type OS) holds back its retainage percent of the amount matched on it; the
voucher is the work and the tax, the supplier is paid it less the retained, and the holdback is paid out later by a
release. So, of a billing that adds up as printed: amounts matched - retained + the tax on the rows = the amount due
as printed, whichever base the tax is charged on (the amounts matched + the tax being the gross, as D-265 has it).

* **The retained amounts follow the invoice**: each row's line's percent of its amount matched when the holdback
  printed is that (a cent a row either way, the largest row taking it); else the holdback printed, shared out over
  the rows as their percent (or their amounts) would share it, with a warning that names the subcontract's percent
  and what it would retain. A billing that holds nothing back on a line that has a percent retains nothing, warned.
* **The tax is worked out on the base the invoice charges it on**: the whole amount matched (most Canadian billings:
  the GST on the work, the holdback in it) or the amount less the retained. Voucher Match does one or the other, as
  its Retainage tab says (Settings → JD Edwards E1: ``JdeSettings.retainage_tax``); a billing charged the other way,
  or neither, is held.
* **Held, never guessed:** subcontract progress billings off in Settings (the default, until the JD Edwards team
  confirms them); a holdback release (it pays out what was held before: JD Edwards releases a retainage itself); a
  billing whose amounts do not add up as printed.

Entry point: ``retained``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..core.schema import PROGRESS_BILLING
from .jde_formats import _units
from .jde_settings import RETAINAGE_TAX_NET, JdeSettings

if TYPE_CHECKING:
    from ..coding.po import MatchRow
    from .jde_voucher import _Voucher

SUBCONTRACT = "OS"  # JD Edwards' order type of a subcontract (``coding.po.SUBCONTRACT``)
SETTING = "Settings → JD Edwards E1 → Subcontract progress billings"


@dataclass
class Retained:
    """A voucher match's retainage (``retained``): each row's retained amount, in cents, in ``rows``' order (none:
    not a progress billing); the amount due as printed; whether the tax is charged on the work before the holdback
    ("whole") or after it ("net"); why it can't be keyed so, and what is said under it."""

    by_row: list[int] = field(default_factory=list)
    due: int | None = None
    tax_on: str = ""
    by_percent: bool = False  # each row retains its line's percent (else: its share of the holdback printed)
    held: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def total(self) -> int:
        return sum(self.by_row)


def _money(cents: int) -> str:
    return f"{cents / 100:,.2f}"


def _pct(value: float) -> str:
    return f"{value:g}%"


def _order(rows: list[MatchRow]) -> str:
    first = next((r for r in rows if r.on_po), None)
    return f"order {first.order_number} {first.order_type or ''}".strip() if first else "the order"


def retained(v: _Voucher, rows: list[MatchRow], settings: JdeSettings, tax: int, rate: float,
             taxed: list[int]) -> Retained:  # fmt: skip
    """The retainage of a voucher match (the module's first lines): ``rows`` its PO lines' rows
    (``coding.po.voucher_match``'s), ``tax`` the tax JD Edwards works out on those of them taxed Y (``taxed``, their
    places) at ``rate`` (the tax area's taxes, together), in cents. Nothing for a voucher that is no subcontract's and
    holds nothing back."""
    progress: dict[str, Any] = v.final.get(PROGRESS_BILLING) or {}
    held_back = _units(progress.get("holdback") or 0)
    subcontract = any(r.on_po and (r.retainage or r.order_type.upper() == SUBCONTRACT) for r in rows)
    out = Retained()
    if not subcontract and not held_back and not progress.get("release"):
        return out
    if progress.get("release"):
        out.held.append(f"a holdback release: it pays out what was held back on {_order(rows)}, which JD Edwards "
                        "releases itself (Voucher Match's release of retainage), not as a new match: key it by "
                        "hand")  # fmt: skip
        return out
    what = f"a subcontract's progress billing ({_order(rows)})" if subcontract else \
        f"a billing that holds back {_money(held_back)}"  # fmt: skip
    if not settings.po_subcontracts:
        out.held.append(f"{what}: subcontract progress billings are off ({SETTING}) until the JD Edwards team "
                        "confirms how Voucher Match retains their holdback: key it by hand")  # fmt: skip
        return out
    if held_back and not progress.get("adds_up"):
        out.held.append(f"{what}: its work this period, less the holdback ({_money(held_back)}), plus the tax is not "
                        "the amount due printed (or the work to date less the work before is not the work this "
                        "period): check the billing's amounts against the invoice, or key it by hand")  # fmt: skip
        return out
    out.by_row = _shared(rows, held_back, out, progress)
    out.due = v.gross - out.total
    if progress.get("amount_due") is not None and held_back:
        out.due = _units(progress["amount_due"])
    if out.total:
        _tax_base(rows, out, settings, tax, rate, taxed)
    _before(rows, progress, out)
    return out


def _expected(rows: list[MatchRow]) -> list[int]:
    """Each row's line's percent of its amount matched (cents; 0 on a row of no percent, or not on the PO)."""
    return [round(_units(r.amount) * (r.retainage or 0) / 100) if r.on_po else 0 for r in rows]


def _shared(rows: list[MatchRow], printed: int, out: Retained, progress: dict[str, Any]) -> list[int]:
    """Each row's retained amount (cents): the holdback printed (``printed``), as the lines' percents give it."""
    expected = _expected(rows)
    if not printed:
        if any(expected):
            out.warnings.append(f"The subcontract holds back {_pct(_rates(rows))} ({_money(sum(expected))}) but "
                                "the invoice holds back nothing: the rows retain nothing, as the invoice. Type "
                                f"{_money(sum(expected))} in Retained Amount to hold it back, and pay that much less "
                                "than the invoice's amount due.")  # fmt: skip
        return [0] * len(rows)
    if any(expected) and abs(sum(expected) - printed) <= len(rows):
        out.by_percent = True
        return _with_cents(expected, printed)
    weights = expected if any(expected) else [_units(r.amount) if r.on_po else 0 for r in rows]
    total = sum(weights)
    if not total:
        out.held.append(f"the invoice holds back {_money(printed)} but bills no line of the order to retain it on: "
                        "key it by hand")  # fmt: skip
        return [0] * len(rows)
    shares = _with_cents([round(printed * w / total) for w in weights], printed)
    printed_pct = f" ({_pct(progress['holdback_rate'])} printed)" if progress.get("holdback_rate") else ""
    if any(expected):
        out.warnings.append(f"The invoice holds back {_money(printed)}{printed_pct}, the subcontract "
                            f"{_pct(_rates(rows))} ({_money(sum(expected))}): the rows retain the invoice's "
                            f"{_money(printed)}. If the subcontract's is to be held back, type it in Retained Amount "
                            f"and pay {_money(sum(expected) - printed)} less than the invoice's amount "
                            "due.")  # fmt: skip
    else:
        out.notes.append(f"The order's lines have no retainage percent in the purchase orders loaded: the invoice's "
                         f"holdback {_money(printed)}{printed_pct} is retained on the rows by their amounts. Check "
                         "the retainage JD Edwards has on the order.")  # fmt: skip
    return shares


def _rates(rows: list[MatchRow]) -> float:
    """The rows' retainage percent, as said (the first: a subcontract's lines hold back one, as a rule)."""
    return next((r.retainage for r in rows if r.on_po and r.retainage), 0.0)


def _with_cents(shares: list[int], total: int) -> list[int]:
    """``shares`` adding up to ``total``: what is left over on the largest."""
    if shares and (left := total - sum(shares)):
        biggest = max(range(len(shares)), key=lambda i: abs(shares[i]))
        shares = [s + (left if i == biggest else 0) for i, s in enumerate(shares)]
    return shares


def _tax_base(rows: list[MatchRow], out: Retained, settings: JdeSettings, tax: int, rate: float,
              taxed: list[int]) -> None:  # fmt: skip
    """Whether the tax on the rows taxed Y is charged on their whole amounts matched or on them less the retained, and
    that Voucher Match works it out so (``JdeSettings.retainage_tax``); held when not, or when it is neither."""
    if not tax or not rate or not taxed:
        return
    whole = sum(round(_units(rows[i].amount) * rate) for i in taxed)
    net = sum(round((_units(rows[i].amount) - out.by_row[i]) * rate) for i in taxed)
    room = len(taxed) + 1
    out.tax_on = "whole" if abs(whole - tax) <= room else "net" if abs(net - tax) <= room else ""
    on_net = settings.retainage_tax == RETAINAGE_TAX_NET
    worked = net if on_net else whole
    said = "on the amounts matched less the retained" if on_net else "on the whole amounts matched"
    if not out.tax_on:
        out.held.append(f"the tax charged ({_money(tax)}) is neither the tax area's {rate * 100:.4g}% on the work "
                        f"this period ({_money(whole)}) nor on it less the holdback ({_money(net)}): check the tax "
                        "against the invoice, or key it by hand")  # fmt: skip
    elif out.tax_on != ("net" if on_net else "whole"):
        charged = "less the holdback" if out.tax_on == "net" else "with the holdback in it"
        out.held.append(f"the invoice charges its tax on the work {charged} ({_money(tax)}), but Voucher Match works "
                        f"it out {said} ({_money(worked)}), as {SETTING} says its Retainage tab's Taxes option is "
                        "set: key it by hand, or change the setting if JD Edwards is set the other way")  # fmt: skip
    else:
        each = (f"each row retains {_pct(_rates(rows))} of its amount matched" if out.by_percent
                else "the rows retain the invoice's holdback")  # fmt: skip
        out.notes.append(f"Retainage: {each} ({_money(out.total)} in all); the tax is worked out {said}. The supplier "
                         f"is paid {_money(out.due or 0)} now, the holdback when JD Edwards releases it.")  # fmt: skip


def _before(rows: list[MatchRow], progress: dict[str, Any], out: Retained) -> None:
    """The work the billing says was billed before, against what JD Edwards had vouchered on the lines and AP
    Coder's invoices not in it yet: said when they differ."""
    printed = progress.get("previous")
    if printed is None:
        return
    known = sum(_units(r.billed_before) for r in rows if r.on_po)
    if abs(_units(printed) - known) > len(rows):
        out.warnings.append(f"The billing says {_money(_units(printed))} was billed before; JD Edwards had "
                            f"vouchered and AP Coder has waiting {_money(known)} on these lines of {_order(rows)}: "
                            "check the billing's previous work against the subcontract before keying it.")  # fmt: skip
