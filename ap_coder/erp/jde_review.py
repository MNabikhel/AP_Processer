"""JD Edwards E1 before approval, on the review screen: what would hold the invoice back from a batch, as findings
with a check code (``voucher_findings``), the voucher header as JD Edwards gets it (``voucher_view``), and the tax on
the pay item (``pay_item_tax``, ``not_recovered``, ``taxes_e1_posts``), which the keying sheet and the pipeline ask
too. Every check code raised here needs its help entry (``ops/help.py``).
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from .jde_formats import _date, _units
from .jde_settings import (
    AMOUNT_TAX,
    GL_DATE_EXPORT,
    HST_PROVINCES,
    JdeSettings,
    gl_date_of,
    moved_note,
    settings_problems,
)
from .jde_voucher import (
    TERMS_CREDIT,
    TERMS_DEFAULT,
    TERMS_TABLE,
    _accounts_once,
    _check_tax_area,
    _distribution,
    _lines_said,
    _prepare,
    _tax_amounts,
    _Voucher,
    po_not_sent,
    sent_due,
    terms_code,
    terms_unmapped,
)

ERROR, WARNING, INFO = "error", "warning", "info"  # a finding's severity (coding.validation.ERROR, WARNING, INFO)

SETTINGS_URL = "/settings/jde"
# What the review screen's own checks already say (no second message): the invoice number, the date, a line without a
# GL account, the totals.
SAID_ELSEWHERE = frozenset({"MISSING_INVOICE_NUMBER", "INVOICE_DATE_INVALID", "GL_UNASSIGNED", "SUBTOTAL_MISMATCH",
                            "POSTING_UNBALANCED", "NO_LINE_ITEMS", "TAX_GL_UNMAPPED"})  # fmt: skip
# Said on the review screen as the voucher check words them, after "JD Edwards: ", for the whole invoice.
SAID_AS_IS = frozenset({"JDE_TAX_AREA", "JDE_MEALS_PO", "JDE_GL_PO", "JDE_GROSS_TAX", "JDE_UNBALANCED",
                        "JDE_PERIOD_CLOSED"})  # fmt: skip


def voucher_findings(output: dict[str, Any], settings: JdeSettings, an8: str) -> list[tuple[str, str, str, int | None]]:
    """What would hold this invoice back from a JD Edwards batch, as checks for the review screen (severity, code,
    message in plain words, line): ``validate`` asked before approval, so an approved voucher is never held back at
    export for something the screen could have said (D-75). The duplicate check is the screen's own."""
    out: list[tuple[str, str, str, int | None]] = []
    for problem in settings_problems(settings):
        out.append((ERROR, "JDE_SETTINGS", f"JD Edwards settings: {problem} Set it in "
                    "Settings → JD Edwards E1, or ask whoever sets up AP Coder", None))  # fmt: skip
    v = _prepare({"id": 0, "final_output": output}, settings, str(an8 or "").strip())
    seen: set[tuple[str, int | None]] = set()
    unmapped: dict[str, list[int | None]] = {}  # "GL 6010 / CC400": the lines posting to it with no E1 account
    number = str(output.get("invoice_number") or "").strip()
    po = str(output.get("po_number") or "").strip()
    for problem, (code, line) in zip(v.problems, v.codes, strict=True):
        if code in SAID_ELSEWHERE or (code, line) in seen:
            continue
        seen.add((code, line))
        if code == "JDE_NO_AN8":
            out.append((ERROR, "JDE_NO_AN8", "JD Edwards: this vendor has no supplier number (AN8). Pick the vendor "
                        "from the vendor list, or add its number in Settings → JD Edwards E1 → Supplier address "
                        "numbers (on the company server, an Admin does)", None))  # fmt: skip
        elif code == "JDE_BAD_AN8":
            out.append((ERROR, "JDE_BAD_AN8", f"JD Edwards: the supplier number {an8} is not an address number (AN8: "
                        "digits only, up to 8). Set this vendor's AN8 in Settings → JD Edwards E1, or ask whoever "
                        "sets up AP Coder", None))  # fmt: skip
        elif code == "JDE_INVOICE_NUMBER_LONG":
            out.append((ERROR, "JDE_INVOICE_NUMBER_LONG", f"JD Edwards keeps 25 characters of the invoice number: "
                        f"this one has {len(number)}", None))  # fmt: skip
        elif code == "JDE_PO_LONG":
            out.append((ERROR, "JDE_PO_LONG", f"JD Edwards keeps 8 characters of the PO number: “{po}” has "
                        f"{len(po)}. Type the PO number as JD Edwards has it", None))  # fmt: skip
        elif code == "JDE_DATE_RANGE":
            out.append((ERROR, "JDE_DATE_RANGE", f"JD Edwards: the {problem}", None))
        elif code == "JDE_CURRENCY":
            out.append((ERROR, "JDE_CURRENCY", f"JD Edwards isn't set up for {v.currency}: check the currency, or ask "
                        "the pilot tester to add it in Settings → JD Edwards E1", None))  # fmt: skip
        elif code == "JDE_NO_ACCOUNT":  # said once for all the lines below
            unmapped.setdefault(problem.split(" has no JDE account")[0].split(": JDE account")[0], []).append(line)
        elif code == "JDE_MEALS_GROSS":
            out.append((ERROR, "JDE_MEALS_GROSS", f"JD Edwards: {problem}, or ask the pilot tester", None))
        elif code in SAID_AS_IS:
            out.append((ERROR, code, f"JD Edwards: {problem}", None))
        else:
            out.append((ERROR, "JDE_VOUCHER", f"JD Edwards: {problem}", line))
    if said := not_recovered(v):  # a GST/HST the Tax setup keeps in the expense: meant, or a slip (B-2150)
        out.append((WARNING, "JDE_TAX_NOT_RECOVERED", f"JD Edwards: {said}", None))
    if said := area_mismatch(v, settings):  # the area of the ship-to, another province's tax charged (B-2345)
        out.append((WARNING, "JDE_TAX_AREA_MISMATCH", f"JD Edwards: {said}", None))
    if v.gl_moved and v.gl_day:  # posted in the open period: the person sees it before approving (D-182)
        out.append((WARNING, "JDE_GL_DATE_MOVED", f"JD Edwards: {moved_note(v.gl_day, v.gl_moved)}", None))
    no_code = terms_unmapped(output, settings)
    if no_code:  # a note: a blank VLPTC has E1 take the supplier's own terms, no reason to hold anything (B-896)
        out.append((INFO, "JDE_TERMS_NO_CODE", f"Payment terms {no_code} have no JD Edwards code: add it in "
                    "Settings → JD Edwards E1 (or ask the pilot tester)", None))  # fmt: skip
    if po_not_sent(output, settings):
        out.append((WARNING, "JDE_PO_LONG", "JD Edwards' PO field holds 8 characters: this PO number is kept in AP "
                    "Coder and not sent", None))  # fmt: skip
    if unmapped:  # one finding: "GL 6400 (lines 1, 2), 6500 (line 7) have no JDE account…", not one per account

        def where(account: str, lines: list[int | None]) -> str:
            numbers = [n for n in lines if n]
            return account.removeprefix("GL ") + (f" ({_lines_said(numbers)})" if numbers else "")

        accounts = [where(a, ls) for a, ls in unmapped.items()]
        one = len(accounts) == 1
        out.append((ERROR, "JDE_NO_ACCOUNT", f"JD Edwards: GL {', '.join(accounts)} {'has' if one else 'have'} no JDE "
                    "account (BU.Object.Subsidiary): ask the pilot tester to map "
                    f"{'it' if one else 'them'} in Settings → JD Edwards E1", None))  # fmt: skip
    from ap_coder.ops.offline import setup_contact

    return [(severity, code, setup_contact(text), line) for severity, code, text, line in out]


def not_recovered(v: _Voucher) -> str:
    """What to say of a voucher whose GST or HST the Tax setup keeps in the expense under code V ("" otherwise): no
    input tax credit is claimed for it. Nearly every company recovers them, so it is asked, on the review screen and
    on the keying sheet; a provincial tax left in the expense is the company's choice (D-210) and is not."""
    left = [t for t in v.charged if t["tax_type"] in v.expensed and t["tax_type"] in ("GST", "HST")]
    if not left:
        return ""
    names = " and ".join(dict.fromkeys(t["tax_type"] for t in left))
    amount = sum(_units(t.get("tax_amount"), v.decimals) for t in left) / 100
    return (f"the {names} ({amount:,.2f}) is not recovered: GL accounts & tax keeps it in the expense, so the "
            "voucher claims no input tax credit for it. If that is not meant, set it to recoverable in GL accounts "
            "& tax (or ask the pilot tester), then save the invoice again")  # fmt: skip


def area_mismatch(v: _Voucher, settings: JdeSettings) -> str:
    """What to say of a voucher whose tax area, which follows the place of supply (the ship-to first), is a province's
    whose federal tax is not the one charged ("" otherwise): an Alberta supplier's GST on goods shipped to Ontario
    goes to Ontario's HST area with 5% tax. JD Edwards works the tax out at the area's rate, so it is said, not held:
    the supplier may have charged the wrong tax, or the ship-to may be wrong (B-2345)."""
    if settings.tax_on_gl or not v.tax_area or v.tax_code in ("E", settings.exempt_code.strip().upper()):
        return ""
    types = {t["tax_type"] for t in v.charged} & {"GST", "HST"}
    due = "HST" if v.province in HST_PROVINCES else "GST"
    if len(types) != 1 or due in types:
        return ""
    (other,) = types
    amount = sum(_units(t.get("tax_amount"), v.decimals) for t in v.charged if t["tax_type"] == other) / 100
    return (f"tax area {v.tax_area} is for a supply in {v.province}, where {due} is charged, but this invoice charges "
            f"{other} ({amount:,.2f}): JD Edwards may work the tax out at the area's rate and find it different. Check "
            "the ship-to and the tax charged before typing it (or ask the pilot tester)")  # fmt: skip


def pay_item_tax(v: _Voucher, settings: JdeSettings) -> tuple[int | None, int | None]:
    """(the tax amount, the taxable amount) on the voucher's pay item, in cents, as ``voucher_rows`` sends them;
    (None, None) when it carries no tax: the tax coded to G/L lines, the exempt code or none, or no tax charged."""
    if settings.tax_on_gl or v.tax_code in ("", "E", settings.exempt_code.strip().upper()) or not v.stam:
        return None, None
    return v.stam, v.atxa


def taxes_e1_posts(final: dict[str, Any], settings: JdeSettings) -> frozenset[str]:
    """The taxes of this invoice JD Edwards posts itself from the pay item's tax area (the recovered tax under codes
    V, C and B), so no sales-tax GL account of AP Coder's is posted to for them (``_distribution_entries``); none with
    the tax coded to G/L lines, or under a code that recovers nothing."""
    v = _Voucher(0, final, [])
    charged = _check_tax_area(v, final, settings)
    _tax_amounts(v, final, {}, settings, charged, _distribution(final))
    return v.recoverable_types


def voucher_view(
    output: dict[str, Any], settings: JdeSettings, an8: str, *, on: dt.date | None = None
) -> dict[str, Any]:
    """The voucher header as JD Edwards gets it, in its order, for the review screen: each value, where it comes from
    (the invoice, or the settings: read-only there) and the Z-file column (``rows``). Beside them, what is keyed on
    the pay item: ``gl_date`` (ISO, ``gl_date_of``: moved into the open period when it must be), ``tax_code``,
    ``tax_area``, ``taxable`` and ``tax`` (None when the pay item carries no tax), ``not_ready`` (why the voucher is
    held back, each reason; empty when it can go) and ``keyed``, the one line that says it all ("Keyed on the pay
    item: …", or "Not ready to key: <the first reason>")."""
    final = output
    v = _prepare({"id": 0, "final_output": final}, settings, str(an8 or "").strip(), on)
    invoice_date = _date(final.get("invoice_date"))
    gl_date, gl_moved = gl_date_of(final, settings, on)  # as keyed: moved into the open period (D-182, B-2490)
    gl_from = "the day of the export" if settings.gl_date == GL_DATE_EXPORT else "the invoice date"
    if settings.gl_date != GL_DATE_EXPORT and invoice_date is None:
        gl_text = f"— ({gl_from})"
    elif gl_moved:
        gl_text = f"{gl_date} (moved into JD Edwards' open period from {gl_from}, {gl_moved}, in a closed period)"
    else:
        gl_text = f"{gl_date} ({gl_from})"
    due = sent_due(final)
    credit = v.gross < 0
    code, key, source = terms_code(final, settings)
    if source == TERMS_TABLE:
        terms_text = f"{code} ({key})"
        terms_note = f"Sent to JD Edwards as code {code}"
    elif source == TERMS_DEFAULT:
        terms_text = f"{code} (the default for terms not in the table)"
        terms_note = f"Sent to JD Edwards as code {code}, the default for terms not in the table"
    elif source == TERMS_CREDIT:
        terms_text = "Not sent (a credit): JD Edwards takes no terms code on a credit memo"
        terms_note = "Not sent to JD Edwards: a credit memo goes without a terms code"
    elif settings.terms_codes and key:
        terms_text = f"Not sent: no code for {key}, JD Edwards uses the supplier's own terms"
        terms_note = f"No JD Edwards code for {key}: it uses the supplier's own terms"
    else:  # the invoice's words are under its Payment terms box
        terms_text = "Not sent: JD Edwards uses the supplier's own terms"
        terms_note = "Not sent to JD Edwards: it uses the supplier's own terms"

    if not settings.send_due_date:
        due_text = "Not sent: JD Edwards works it out"
    elif due is None:
        due_text = "Not sent: JD Edwards works it out from the supplier's terms"
    else:
        due_text = due.isoformat() + (" (the invoice date: a credit)" if credit else "")

    def money(cents: int) -> str:
        return f"{cents / 100:,.{v.decimals}f}" if v.decimals else f"{cents // 100:,}"

    amounts = money(v.gross)
    if settings.amount_mode == AMOUNT_TAX:
        amounts = f"{money(v.gross)} gross · {money(v.atxa)} taxable · {money(v.stam)} tax"
    rows = [
        ("Company", "VLCO", settings.company_code, "settings"),
        ("Supplier (AN8)", "VLAN8", str(an8 or "").strip() or "none", "vendor"),
        ("Invoice number", "VLVINV", str(final.get("invoice_number") or "").strip()[:25] or "—", "invoice"),
        ("Invoice date", "VLDIVJ", invoice_date.isoformat() if invoice_date else "—", "invoice"),
        ("G/L date", "VLDGJ", gl_text, "settings"),
        ("Due date", "VLDDJ", due_text, "invoice"),
        ("Payment terms", "VLPTC", terms_text, "settings"),
        ("Currency", "VLCRCD", v.currency + (" (foreign)" if v.foreign else ""), "invoice"),
        ("Gross / taxable / tax" if settings.amount_mode == AMOUNT_TAX else "Gross amount",
         "VLAG" if not v.foreign else "VLACR", amounts, "invoice"),
        ("Tax area / explanation", "VLTXA1 / VLEXR1",
         f"— / {v.tax_code}: the tax goes on its own G/L lines (tax coded to G/L accounts)" if settings.tax_on_gl
         else f"{v.tax_area or '—'} / {v.tax_code or 'blank'}", "settings"),
        ("Pay status", "VLPST", settings.pay_status.strip() or "—", "settings"),
    ]  # fmt: skip
    tax, taxable = pay_item_tax(v, settings)
    day = gl_date_of(final, settings, on)[0].isoformat()
    not_ready = _accounts_once(v) if v.problems else []
    if not_ready:
        keyed = f"Not ready to key: {not_ready[0]}"
    elif tax is None:
        keyed = (f"Keyed on the pay item: G/L date {day} · Tax Ex {v.tax_code or 'blank'} · no tax"
                 + (" (it is on its own G/L lines)" if settings.tax_on_gl else ""))  # fmt: skip
    else:
        keyed = (f"Keyed on the pay item: G/L date {day} · Tax Ex {v.tax_code} · Tax Rate/Area "
                 f"{v.tax_area or 'blank'} · taxable amount {money(taxable)} · tax amount {money(tax)}")  # fmt: skip
    return {"rows": [{"label": a, "column": b, "value": c, "source": d} for a, b, c, d in rows],
            "settings_url": SETTINGS_URL, "terms_sent": bool(code), "terms_code": code,
            "terms_note": terms_note, "gl_date": day, "tax_code": v.tax_code, "tax_area": v.tax_area,
            "taxable": None if taxable is None else taxable / 100, "tax": None if tax is None else tax / 100,
            "not_ready": not_ready, "keyed": keyed}  # fmt: skip
