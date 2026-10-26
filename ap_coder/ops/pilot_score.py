"""The shadow pilot's score: what AP Coder approved against what AP posted in JD Edwards (D-92).

During the shadow pilot the pilot tester runs real supplier invoices through AP Coder while AP keeps entering them in
JD Edwards as before. ``python -m ap_coder pilot-score --vouchers F0411.xlsx [--distribution F0911.xlsx]`` reads an
export from E1 and compares, invoice by invoice:

- the voucher header (F0411: supplier AN8, invoice number, invoice date, gross amount, tax, currency, due date);
- the G/L distribution (F0911, optional: account BU.Obj.Sub, amount, explanation), line by line, for what AP Coder
  approved and for what it proposed before a person looked at it (the learning curve);
- the tax area and tax explanation code, when the export has them.

Invoices are matched by supplier (the AN8, else the vendor name as ``vendor_key`` reads it), invoice number
(``norm_invoice_number``) and amount; an invoice whose number differs is still found by supplier, amount and date, and
the number is reported as a difference. Column names are matched as ``core.headers.match_columns`` reads headers, so
the export can use E1's column names (RPAN8, VINV...), the data dictionary's ("Address Number") or plain English.
Amounts, dates and currencies are compared with the accuracy benchmark's comparators (``bench.scoring``).

Writes ``pilot_score.md`` (the report), ``pilot_score_differences.csv`` (one row per difference) and
``pilot_score.json`` (the figures, which Insights shows) into the output folder.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..bench.scoring import values_match
from ..coding.vendors import norm_invoice_number
from ..core import ui
from ..core.headers import match_columns
from ..core.memory import vendor_key
from ..core.reference_data import UNASSIGNED
from ..core.safe import csv_row, parse_amount
from ..erp import jde
from ..erp.statements import day_first_order, read_date

REPORT, DIFFERENCES, FIGURES = "pilot_score.md", "pilot_score_differences.csv", "pilot_score.json"
FOLDER = "pilot_score"  # in the data folder, beside the database: where Insights finds the last score

# F0411 (voucher header): E1's column names with and without the table prefix, the data dictionary's titles, English.
VOUCHER_COLUMNS: dict[str, tuple[str, ...]] = {
    "an8": ("rpan8", "an8", "addressnumber", "suppliernumber", "supplierno", "vendornumber", "vendorno", "vendorid",
            "erpid", "supplierid"),
    "vendor_name": ("rpalph", "alph", "alphaname", "suppliername", "vendorname", "supplier", "vendor", "name"),
    "invoice_number": ("rpvinv", "vinv", "supplierinvoicenumber", "supplierinvoiceno", "supplierinvoice",
                       "vendorinvoicenumber", "vendorinvoice", "invoicenumber", "invoiceno", "invoice"),
    "invoice_date": ("rpdivj", "divj", "invoicedate", "dateinvoice"),
    "gross": ("rpag", "ag", "grossamount", "gross", "amountgross", "invoiceamount", "totalamount", "total"),
    "foreign_gross": ("rpacr", "acr", "foreigngrossamount", "foreignamount", "amountforeign"),
    "tax": ("rpstam", "stam", "taxamount", "tax", "amounttax"),
    "foreign_tax": ("rpctam", "ctam", "foreigntaxamount", "foreigntax"),
    "currency": ("rpcrcd", "crcd", "currencycode", "currency"),
    "due_date": ("rpddj", "ddj", "duedate", "netduedate"),
    "document": ("rpdoc", "doc", "documentnumber", "voucher", "vouchernumber", "documentno"),
    "pay_item": ("rpsfx", "sfx", "payitem", "payitemnumber"),
    "doc_type": ("rpdct", "dct", "doctype", "documenttype"),
    "company": ("rpkco", "kco", "documentcompany", "company"),
    "tax_area": ("rptxa1", "txa1", "taxarea", "taxratearea", "taxrate/area"),
    "tax_code": ("rpexr1", "exr1", "taxexplcode", "taxexplanationcode", "taxexplanation", "taxcode"),
}  # fmt: skip
# F0911 (G/L distribution).
DISTRIBUTION_COLUMNS: dict[str, tuple[str, ...]] = {
    "document": ("gldoc", "doc", "documentnumber", "voucher", "vouchernumber", "documentno"),
    "doc_type": ("gldct", "dct", "doctype", "documenttype"),
    "company": ("glkco", "kco", "documentcompany", "company"),
    "an8": ("glan8", "an8", "addressnumber", "suppliernumber", "vendornumber"),
    "invoice_number": ("glvinv", "vinv", "supplierinvoicenumber", "invoicenumber", "invoice"),
    "account": ("glani", "ani", "accountnumber", "account", "glaccount"),
    "bu": ("glmcu", "mcu", "businessunit", "costcenter", "costcentre"),
    "obj": ("globj", "obj", "objectaccount", "object"),
    "sub": ("glsub", "sub", "subsidiary"),
    "amount": ("glaa", "aa", "amount", "amountdomestic"),
    "foreign_amount": ("glacr", "acr", "foreignamount", "amountforeign"),
    "explanation": ("glexa", "exa", "explanation", "explanationalphaname", "description", "remark"),
    "ledger": ("gllt", "lt", "ledgertype", "ledger"),
    "tax_area": ("gltxa1", "txa1", "taxarea", "taxratearea"),
    "tax_code": ("glexr1", "exr1", "taxexplcode", "taxexplanationcode", "taxcode"),
}  # fmt: skip
REQUIRED = ("invoice_number", "gross")  # and an8 or vendor_name

HEADER_FIELDS = {"invoice_number": "Invoice number", "invoice_date": "Invoice date", "grand_total": "Gross amount",
                 "tax_total": "Tax", "currency": "Currency", "due_date": "Due date"}  # fmt: skip
CENT = 0.005
TOO_LARGE = "This Excel file is too large to open safely; export it as CSV instead."
Account = tuple[str, str, str]  # (business unit, object, subsidiary)


class ExportError(ValueError):
    """An export that cannot be scored; the message says what to do."""


# --- Reading the JD Edwards export ----------------------------------------------------------------------------------


def read_table(path: str | Path, sheet: str | None = None) -> tuple[list[dict[str, str]], list[str]]:
    """(rows as text, the workbook's sheet names) of a CSV or Excel file."""
    from ..intake import triage
    from ..web.files import SheetError, read_sheet

    path = Path(path)
    try:
        content = path.read_bytes()
        if path.suffix.lower() == ".xlsx" and triage.sheet_unzips_too_big(content):  # a large export, said plainly
            raise ExportError(f"{path}: {TOO_LARGE}")
        df, names = read_sheet(path.name, content, sheet)
    except (OSError, SheetError) as exc:
        raise ExportError(f"{path}: {exc}") from exc
    return [{str(k): str(v) for k, v in rec.items()} for rec in df.to_dict("records")], names


def julian_or_date(text: str, day_first: bool | None = None) -> str:
    """YYYY-MM-DD of an E1 Julian date (CYYDDD: 126282 is 2026-10-09) or of a date as people write it."""
    text = str(text or "").strip()
    if re.fullmatch(r"0+(\.0*)?", text):
        return ""  # E1 writes a blank date as 0
    if re.fullmatch(r"\d{5,6}(\.0)?", text):
        n = int(float(text))
        year, day = 1900 + n // 1000, n % 1000
        if 1 <= day <= 366:
            return (dt.date(year, 1, 1) + dt.timedelta(days=day - 1)).isoformat()
    return read_date(text, day_first) if text else ""


def _amount(text: str, implied: bool) -> float | None:
    value = parse_amount(text)
    if value is None:
        return None
    return round(value / 100, 2) if implied else value


def split_account(text: str) -> tuple[str, str, str]:
    """(BU, object, subsidiary) of an account written BU.Object.Subsidiary ("CC400.6010.01"); E1 right-justifies the
    business unit, so the spaces go."""
    parts = [p.strip().upper() for p in str(text or "").split(".")]
    while len(parts) < 3:
        parts.append("")
    return parts[0], parts[1], ".".join(parts[2:]).strip(".")


@dataclass
class Posted:
    """One voucher as AP posted it in JD Edwards (its pay items added up)."""

    key: str
    an8: str = ""
    vendor_name: str = ""
    invoice_number: str = ""
    invoice_date: str = ""
    gross: float | None = None
    tax: float | None = None
    currency: str = ""
    due_date: str = ""
    document: str = ""
    tax_area: str = ""
    tax_code: str = ""
    lines: list[dict[str, Any]] = field(default_factory=list)
    has_lines: bool = False
    not_compared: set[str] = field(default_factory=set)  # a foreign voucher's amounts the export has only in CAD

    @property
    def vkey(self) -> str:
        return vendor_key(self.vendor_name) if self.vendor_name else ""


def _sign(value: float | None) -> int:
    return 0 if not value else (1 if value > 0 else -1)


def _getter(rec: dict[str, Any], columns: dict[str, str]) -> Any:
    def get(target: str) -> str:
        value = rec.get(columns[target]) if target in columns else None
        text = "" if value is None else str(value).strip()
        return "" if text.lower() in ("nan", "none") else text

    return get


def _doc_key(get: Any) -> str:
    doc = get("document")
    return "|".join((doc.lstrip("0"), get("doc_type").upper(), get("company").lstrip("0"))) if doc else ""


def _an8(text: str) -> str:
    text = re.sub(r"\.0$", "", str(text or "").strip())
    return text.lstrip("0") or text


def posted_vouchers(
    vouchers: list[dict[str, Any]], distribution: list[dict[str, Any]] | None = None, *, implied_decimals: bool = False,
    domestic: str = "CAD",
) -> tuple[list[Posted], list[str]]:  # fmt: skip
    """(the vouchers, notes about the export) from F0411 rows (one per pay item) and F0911 rows."""
    notes: list[str] = []
    if not vouchers:
        raise ExportError("The voucher export (F0411) has no rows.")
    columns = match_columns(list(vouchers[0]), VOUCHER_COLUMNS)
    missing = [t for t in REQUIRED if t not in columns]
    if "an8" not in columns and "vendor_name" not in columns:
        missing.append("an8 or vendor_name")
    if missing:
        raise ExportError(
            "The voucher export (F0411) needs these columns: "
            + ", ".join(missing)
            + ". Its columns: "
            + ", ".join(list(vouchers[0]))
            + ". Rename them (e.g. RPAN8, RPVINV, RPDIVJ, RPAG) and run it again."
        )
    order_of = {
        c: day_first_order([str(r.get(columns[c]) or "") for r in vouchers])
        for c in ("invoice_date", "due_date")
        if c in columns
    }
    if "document" not in columns:
        notes.append("The voucher export (F0411) has no document number (RPDOC) or pay item (RPSFX): rows with the "
                     "same supplier and invoice number are taken as one voucher only when their date and sign agree, "
                     "and a row repeated as it is counts as a second voucher. Add RPDOC, RPDCT, RPKCO and RPSFX to "
                     "the export for an exact count.")  # fmt: skip
    found: dict[str, Posted] = {}
    seen: dict[str, set[Any]] = defaultdict(set)  # without RPDOC: the pay items (or whole rows) of each voucher
    groups: dict[str, list[str]] = defaultdict(list)  # without RPDOC: the vouchers of a supplier and invoice number
    domestic_only: dict[str, int] = {"grand_total": 0, "tax_total": 0}
    for rec in vouchers:
        get = _getter(rec, columns)
        if not get("invoice_number") and not get("gross"):
            continue
        who = _an8(get("an8")) or vendor_key(get("vendor_name"))
        currency = get("currency").upper()
        foreign = bool(currency) and currency != domestic.upper()
        gross = _amount(get("foreign_gross"), implied_decimals) if foreign and get("foreign_gross") else None
        in_domestic = {"grand_total"} if foreign and gross is None else set()
        gross = gross if gross is not None else _amount(get("gross"), implied_decimals)
        tax = _amount(get("foreign_tax"), implied_decimals) if foreign and get("foreign_tax") else None
        in_domestic |= {"tax_total"} if foreign and tax is None and "tax" in columns else set()
        tax = tax if tax is not None else (_amount(get("tax"), implied_decimals) if "tax" in columns else None)
        key = _doc_key(get)
        if not key:  # pay items of one voucher share its date and sign; a repeated pay item is another voucher
            base = f"{who}|{norm_invoice_number(get('invoice_number'))}"
            date = julian_or_date(get("invoice_date"), order_of.get("invoice_date"))
            item = get("pay_item").lstrip("0") if "pay_item" in columns else tuple(sorted(rec.items()))
            key = next((k for k in groups[base] if found[k].invoice_date == date and item not in seen[k]
                        and _sign(found[k].gross) * _sign(gross) >= 0), "")  # fmt: skip
            if not key:
                key = base if not groups[base] else f"{base}|{len(groups[base]) + 1}"
                groups[base].append(key)
            seen[key].add(item)
        p = found.get(key)
        if p is None:
            p = found[key] = Posted(
                key=key, an8=_an8(get("an8")), vendor_name=get("vendor_name"), invoice_number=get("invoice_number"),
                invoice_date=julian_or_date(get("invoice_date"), order_of.get("invoice_date")),
                currency=currency, due_date=julian_or_date(get("due_date"), order_of.get("due_date")),
                document=get("document"), tax_area=get("tax_area"), tax_code=get("tax_code").upper(),
            )  # fmt: skip
        else:  # another pay item of the same voucher
            p.tax_area, p.tax_code = p.tax_area or get("tax_area"), p.tax_code or get("tax_code").upper()
        for name in in_domestic - p.not_compared:
            domestic_only[name] += 1
        p.not_compared |= in_domestic
        if gross is not None:
            p.gross = round((p.gross or 0.0) + gross, 2)
        if tax is not None:
            p.tax = round((p.tax or 0.0) + tax, 2)
    out = list(found.values())
    for name, what, column in (("grand_total", "gross", "RPACR"), ("tax_total", "tax", "RPCTAM")):
        if domestic_only[name]:
            one = domestic_only[name] == 1
            many = ui.plural(domestic_only[name], "foreign-currency voucher")
            notes.append(f"{many} {'has' if one else 'have'} no foreign {what} amount ({column}) in the export: "
                         f"{'its' if one else 'their'} {what} is in {domestic.upper()}, so it is not compared. Add "
                         f"{column} to the export to compare it.")  # fmt: skip
    if distribution:
        notes += _attach_lines(out, distribution, implied_decimals, domestic)
    return out, notes


def _attach_lines(vouchers: list[Posted], rows: list[dict[str, Any]], implied: bool, domestic: str) -> list[str]:
    columns = match_columns(list(rows[0]), DISTRIBUTION_COLUMNS)
    if "account" not in columns and "obj" not in columns:
        return ["The G/L export (F0911) has no account column (GLANI, or GLMCU + GLOBJ + GLSUB): coding not compared."]
    if "amount" not in columns and "foreign_amount" not in columns:
        return ["The G/L export (F0911) has no amount column (GLAA): coding not compared."]
    by_doc = {p.key: p for p in vouchers}
    by_number: dict[tuple[str, str], Posted] = {}
    for p in vouchers:
        by_number.setdefault((p.an8, norm_invoice_number(p.invoice_number)), p)
    linked = "document" in columns or ("an8" in columns and "invoice_number" in columns)
    if not linked:
        return ["The G/L export (F0911) has neither the document number (GLDOC) nor the supplier and invoice number "
                "(GLAN8, GLVINV): its lines can't be tied to the vouchers, so coding is not compared."]  # fmt: skip
    unlinked = 0
    found: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for rec in rows:
        get = _getter(rec, columns)
        if "ledger" in columns and get("ledger").upper() not in ("", "AA"):
            continue  # another ledger (CA: foreign currency, BA: budget...)
        p = by_doc.get(_doc_key(get)) if "document" in columns else None
        if p is None and "an8" in columns:
            p = by_number.get((_an8(get("an8")), norm_invoice_number(get("invoice_number"))))
        if p is None:
            unlinked += 1
            continue
        foreign = bool(p.currency) and p.currency != domestic.upper()
        amount = _amount(get("foreign_amount"), implied) if foreign and get("foreign_amount") else None
        amount = amount if amount is not None else _amount(get("amount"), implied)
        if amount is None:
            continue
        if "account" in columns and get("account"):
            bu, obj, sub = split_account(get("account"))
        else:
            bu, obj, sub = get("bu").upper(), get("obj").upper(), get("sub").upper()
        p.has_lines = True
        found[p.key].append({"bu": bu, "obj": obj, "sub": sub, "amount": amount, "explanation": get("explanation"),
                             "tax_area": get("tax_area"), "tax_code": get("tax_code").upper()})  # fmt: skip
        p.tax_area = p.tax_area or get("tax_area")
        p.tax_code = p.tax_code or get("tax_code").upper()
    _drop_ap_offsets(by_doc, found)
    return [f"{unlinked} G/L lines (F0911) belong to no voucher in the F0411 export: left out."] if unlinked else []


def _drop_ap_offsets(by_key: dict[str, Posted], found: dict[str, list[dict[str, Any]]]) -> None:
    """Each voucher's G/L lines without the AP trade account's offset (the other side of the voucher): the line of
    minus its gross, and on a voucher with none (an offset per pay item), the opposite-sign lines on an account that
    is the offset of another voucher. A discount or credit line of the distribution is coding: it stays."""
    offsets: dict[str, dict[str, Any]] = {}
    for key, lines in found.items():
        gross = by_key[key].gross
        if gross:
            offsets[key] = next((t for t in reversed(lines) if _close(t["amount"], -gross)), {})
    ap_accounts = {_posted_account(t) for t in offsets.values() if t}
    for key, lines in found.items():
        p, offset = by_key[key], offsets.get(key)
        if offset:
            lines.remove(offset)
        else:
            lines = [t for t in lines if not (_posted_account(t) in ap_accounts
                                              and _sign(t["amount"]) * _sign(p.gross) <= 0)]  # fmt: skip
        p.lines += lines


# --- What AP Coder approved -----------------------------------------------------------------------------------------


@dataclass
class Ours:
    """One invoice AP Coder approved."""

    id: int
    vendor_name: str
    an8: str
    invoice_number: str
    invoice_date: str
    grand_total: float | None
    tax_total: float | None
    currency: str
    due_date: str
    tax_area: str = ""
    tax_code: str = ""
    touchless: bool = False
    lines: list[dict[str, Any]] = field(default_factory=list)
    tax_on_gl: bool = False  # the tax coded to G/L accounts (D-93): the voucher's F0411 carries none

    @property
    def vkey(self) -> str:
        return vendor_key(self.vendor_name)


def _account(gl: str, cc: str, settings: jde.JdeSettings) -> tuple[str, str, str] | None:
    """(BU, object, subsidiary) of a GL code and cost center through Settings → JD Edwards' account mapping."""
    account = jde.account_for(gl, cc, settings)
    return (account.bu.upper(), account.obj.upper(), account.sub.upper()) if account is not None else None


def _would_be_touchless(inv: dict[str, Any]) -> bool:
    """Approved by AP Coder on its own, or its supplier met the touchless bar when it was processed (with touchless
    processing off, it went to a person anyway)."""
    from ..capture.supplier import AUTONOMOUS, READY
    from ..capture.workflow import AUTONOMOUS_REVIEWER

    capture = (inv.get("meta") or {}).get("capture") or {}
    return inv.get("reviewer") == AUTONOMOUS_REVIEWER or bool(capture.get("auto")) or capture.get("state") in (
        READY, AUTONOMOUS)  # fmt: skip


def our_invoices(store: Any, settings: jde.JdeSettings | None = None) -> list[Ours]:
    """Every invoice fully approved in AP Coder, oldest first, as JD Edwards would get it: one still waiting for its
    second approval is not exported yet (B-1012)."""
    from ..core.store import APPROVED
    from ..erp.exports import distribution

    settings = settings or jde.load(store)
    ids = store.vendor_ids()
    rows = sorted(store.list_invoices(APPROVED), key=lambda r: r["id"])
    out = []
    for row in rows:
        inv = store.get_invoice(row["id"])
        final = inv.get("final_output") or {}
        if not final:
            continue
        an8 = jde.resolve_an8(final, ids, settings)
        taxes = [t for t in final.get("tax_lines") or [] if abs(float(t.get("tax_amount") or 0)) > 0.004]
        tax_total = final.get("tax_total")
        if tax_total is None and taxes:
            tax_total = round(sum(float(t.get("tax_amount") or 0) for t in taxes), 2)
        # What the voucher sent (B-933, B-934): its due date (a credit memo's is its invoice date; none when it was
        # only a guess, E1 then works it out) and the tax E1 was given (the claimable tax, or the tax on its own G/L
        # lines; with the gross only, the tax charged).
        due = jde.sent_due(final, inv) if settings.send_due_date else None
        try:
            given = jde.sent_tax(inv, settings, an8 or "0")
        except Exception:  # noqa: BLE001 - an invoice JD Edwards would refuse: the tax as charged
            given = None
        o = Ours(
            id=inv["id"], vendor_name=str(final.get("vendor_name") or ""), an8=_an8(an8),
            invoice_number=str(final.get("invoice_number") or ""), invoice_date=str(final.get("invoice_date") or ""),
            grand_total=parse_amount(final.get("grand_total")),
            tax_total=given if given is not None else parse_amount(tax_total),
            currency=str(final.get("currency") or settings.domestic_currency).upper(),
            due_date=due.isoformat() if due else "", touchless=_would_be_touchless(inv),
            tax_on_gl=settings.tax_on_gl,
        )  # fmt: skip
        try:  # the tax area and explanation code the voucher would carry
            header = jde.voucher_rows(inv, an8 or "0", settings)[0]
            o.tax_area, o.tax_code = str(header[0].get("VLTXA1") or ""), str(header[0].get("VLEXR1") or "").upper()
        except Exception:  # noqa: BLE001 - an invoice JD Edwards would refuse: its tax treatment is not compared
            pass
        proposed = {li.get("line_number"): li for li in (inv.get("ai_output") or {}).get("line_items") or []}
        for e in distribution(final):
            recoverable = e.get("kind") == "tax" and abs(float(e.get("non_recoverable_tax") or 0)) < CENT
            gl, cc = str(e.get("gl_code") or ""), str(e.get("cost_center") or "")
            cc = "" if cc.upper() == UNASSIGNED else cc
            ai = proposed.get(e.get("line_number")) if e.get("kind") == "expense" else None
            p_gl = str((ai or {}).get("predicted_gl_code") or UNASSIGNED) if ai is not None else ""
            p_cc = str((ai or {}).get("predicted_cost_center") or "")
            p_cc = "" if p_cc.upper() == UNASSIGNED else p_cc
            o.lines.append({
                "line_number": e.get("line_number"), "description": str(e.get("description") or ""),
                "amount": parse_amount(e.get("amount")) or 0.0, "kind": e.get("kind") or "expense",
                "recoverable_tax": recoverable, "gl": gl, "cc": cc, "account": _account(gl, cc, settings),
                "proposed_gl": p_gl, "proposed_cc": p_cc,
                "proposed_account": _account(p_gl, p_cc, settings) if p_gl and p_gl != UNASSIGNED else None,
            })  # fmt: skip
        out.append(o)
    return out


# --- Matching -------------------------------------------------------------------------------------------------------


def same_vendor(o: Ours, p: Posted) -> bool:
    return (bool(o.an8) and o.an8 == p.an8) or (bool(p.vkey) and p.vkey == o.vkey)


def _close(a: float | None, b: float | None) -> bool:
    return a is not None and b is not None and abs(a - b) <= CENT


def match(ours: list[Ours], posted: list[Posted]) -> tuple[list[tuple[Ours, Posted, str]], list[Ours], list[Posted]]:
    """(pairs with how they were matched, AP Coder's left over, JD Edwards' left over). First by supplier and invoice
    number (the closest amount when the number is there twice), then by supplier and amount (the closest date): an
    invoice number typed differently on one side is still found, and said."""
    free = list(posted)
    pairs: list[tuple[Ours, Posted, str]] = []
    left: list[Ours] = []
    for o in ours:
        number = norm_invoice_number(o.invoice_number)
        same = [p for p in free if same_vendor(o, p) and number and norm_invoice_number(p.invoice_number) == number]
        if same:
            best = min(same, key=lambda p: abs((p.gross or 0) - (o.grand_total or 0)))
            free.remove(best)
            pairs.append((o, best, "supplier and invoice number"))
        else:
            left.append(o)
    unmatched: list[Ours] = []
    for o in left:
        same = [p for p in free if same_vendor(o, p) and _close(p.gross, o.grand_total)]
        if same:
            best = min(same, key=lambda p: _days_apart(p.invoice_date, o.invoice_date))
            free.remove(best)
            pairs.append((o, best, "supplier and amount"))
        else:
            unmatched.append(o)
    pairs.sort(key=lambda t: t[0].id)
    return pairs, unmatched, free


def _days_apart(a: str, b: str) -> int:
    try:
        return abs((dt.date.fromisoformat(a[:10]) - dt.date.fromisoformat(b[:10])).days)
    except ValueError:
        return 10**6


# --- Comparing ------------------------------------------------------------------------------------------------------


def header_right(name: str, ours: Any, posted: Any) -> bool:
    """Whether AP Coder's value of a header field is what JD Edwards has (the benchmark's comparators)."""
    if name == "invoice_number":
        return bool(norm_invoice_number(ours)) and norm_invoice_number(ours) == norm_invoice_number(posted)
    return values_match(name, posted, ours)


def _posted_value(p: Posted, name: str) -> Any:
    """The voucher's value of a header field; None for what is not compared (a foreign voucher's gross and tax in
    the domestic currency)."""
    if name in p.not_compared:
        return None
    return {"invoice_number": p.invoice_number, "invoice_date": p.invoice_date, "grand_total": p.gross,
            "tax_total": p.tax, "currency": p.currency, "due_date": p.due_date}[name]  # fmt: skip


def _tax_on_gl_posted(o: Ours, p: Posted) -> float | None:
    """With the tax coded to G/L accounts (D-93) the tax JD Edwards has is on the F0911 lines of the tax accounts
    (the F0411's RPSTAM is 0, exempt code E): their sum, on the accounts our recoverable tax lines go to; None without
    the G/L export or a tax account to look for (B-1023)."""
    accounts = {(a[1], a[2]) for line in o.lines if line["recoverable_tax"] and (a := line["account"]) is not None}
    if not p.has_lines or not accounts:
        return None
    return round(sum(t["amount"] for t in p.lines if (t["obj"], t["sub"]) in accounts), 2)


def _ours_value(o: Ours, name: str) -> Any:
    return {"invoice_number": o.invoice_number, "invoice_date": o.invoice_date, "grand_total": o.grand_total,
            "tax_total": o.tax_total, "currency": o.currency, "due_date": o.due_date}[name]  # fmt: skip


def _shown(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def _account_text(account: tuple[str, str, str] | None, gl: str = "") -> str:
    if account is None:
        return gl
    return ".".join(x for x in account if x) if account[2] else f"{account[0]}.{account[1]}"


def _posted_account(line: dict[str, Any]) -> Account:
    return line["bu"], line["obj"], line["sub"]


def _pair_lines(o: Ours, p: Posted) -> list[tuple[dict[str, Any] | None, dict[str, Any] | None]]:
    """AP Coder's distribution lines beside the posted ones: same amount and object first, then same amount, then the
    last line left on each side together."""
    ours = list(o.lines)
    theirs = list(p.lines)
    pairs: list[tuple[dict[str, Any] | None, dict[str, Any] | None]] = []

    def obj(line: dict[str, Any]) -> str:
        return (line["account"] or ("", line["gl"].upper(), ""))[1]

    for strict in (True, False):
        for line in list(ours):
            found = next((t for t in theirs if _close(t["amount"], line["amount"])
                          and (not strict or t["obj"] == obj(line))), None)  # fmt: skip
            if found is not None:
                ours.remove(line)
                theirs.remove(found)
                pairs.append((line, found))
    expense = [line for line in ours if not line["recoverable_tax"]]
    if len(expense) == 1 and len(theirs) == 1:
        pairs.append((expense[0], theirs.pop()))
        ours.remove(expense[0])
    pairs += [(line, None) for line in ours] + [(None, t) for t in theirs]
    return pairs


def _line_verdict(gl: str, account: Account | None, cc: str, t: dict[str, Any]) -> tuple[bool, bool | None]:
    """(GL right, cost center right or None when there is none to compare) of one line against the posted one: the
    object and subsidiary through the JDE mapping, else the GL code against the object; the business unit against
    the mapped one, else against the line's cost center."""
    if not gl or gl.upper() == UNASSIGNED:
        return False, None
    if account is not None:
        gl_ok = (account[1], account[2]) == (t["obj"], t["sub"])
        cc_ok = account[0] == t["bu"] if t["bu"] else None
    else:
        gl_ok = gl.upper() == t["obj"]
        cc_ok = (cc.upper() == t["bu"]) if cc and t["bu"] else None
    return gl_ok, cc_ok


def score(ours: list[Ours], posted: list[Posted], notes: list[str] | None = None) -> dict[str, Any]:
    """The figures and the differences of a shadow pilot (see the module's docstring)."""
    pairs, ours_only, posted_only = match(ours, posted)
    diffs: list[dict[str, Any]] = []
    header = {name: {"n": 0, "right": 0} for name in HEADER_FIELDS}
    coding = {k: 0 for k in ("lines", "compared", "gl_right", "cc_n", "cc_right", "proposed_right", "proposed_blank",
                             "not_posted", "posted_only", "tax_n", "tax_right")}  # fmt: skip
    per_invoice: list[dict[str, Any]] = []

    def diff(o: Ours | None, p: Posted | None, kind: str, what: str, mine: Any, theirs: Any, note: str = "") -> None:
        diffs.append({
            "kind": kind, "supplier": (o.vendor_name if o else "") or (p.vendor_name if p else ""),
            "an8": (p.an8 if p else "") or (o.an8 if o else ""),
            "invoice_number": (o.invoice_number if o else "") or (p.invoice_number if p else ""),
            "ap_coder_id": o.id if o else "", "jde_document": p.document if p else "", "field": what,
            "ap_coder": _shown(mine), "jd_edwards": _shown(theirs), "note": note,
        })  # fmt: skip

    tax_not_compared = 0
    for o, p, how in pairs:
        wrong_fields: list[str] = []
        for name, label in HEADER_FIELDS.items():
            theirs = _posted_value(p, name)
            if name == "tax_total" and o.tax_on_gl and o.tax_total:  # RPSTAM is 0: the tax accounts' lines (B-1023)
                theirs = _tax_on_gl_posted(o, p)
                tax_not_compared += theirs is None
            if theirs in (None, ""):
                continue  # not in the export
            if name == "due_date" and not o.due_date:
                continue  # none sent: E1 worked it out from the supplier's terms (B-933)
            header[name]["n"] += 1
            if header_right(name, _ours_value(o, name), theirs):
                header[name]["right"] += 1
            else:
                wrong_fields.append(name)
                by_amount = name == "invoice_number" and how != "supplier and invoice number"
                diff(o, p, "header", label, _ours_value(o, name), theirs,
                     "matched by supplier and amount" if by_amount else "")  # fmt: skip
        tax_wrong = False
        for label, mine, theirs in (("Tax area", o.tax_area, p.tax_area),
                                    ("Tax explanation code", o.tax_code, p.tax_code)):  # fmt: skip
            if theirs:
                coding["tax_n"] += 1
                if mine.strip().upper() == theirs.strip().upper():
                    coding["tax_right"] += 1
                else:
                    tax_wrong = True
                    diff(o, p, "tax treatment", label, mine, theirs)
        lines = {"n": 0, "right": 0, "proposed_right": 0}
        if p.has_lines:
            for mine, theirs in _pair_lines(o, p):
                if mine is not None and mine["recoverable_tax"]:
                    continue  # E1 posts recoverable tax from its own tax rules: not coding
                if mine is None:
                    coding["posted_only"] += 1
                    diff(
                        o,
                        p,
                        "line",
                        "Posted line not in AP Coder",
                        "",
                        theirs["amount"],
                        f"{_account_text(_posted_account(theirs))} {theirs['explanation']}".strip(),
                    )
                    continue
                coding["lines"] += 1
                lines["n"] += 1
                if theirs is None:
                    coding["not_posted"] += 1
                    diff(
                        o,
                        p,
                        "line",
                        "Line not posted with this amount",
                        mine["amount"],
                        "",
                        f"{_account_text(mine['account'], mine['gl'])} {mine['description']}".strip(),
                    )
                    continue
                coding["compared"] += 1
                posted_account = _account_text(_posted_account(theirs))
                gl_ok, cc_ok = _line_verdict(mine["gl"], mine["account"], mine["cc"], theirs)
                if gl_ok:
                    coding["gl_right"] += 1
                    lines["right"] += 1
                else:
                    diff(
                        o,
                        p,
                        "line",
                        "GL account",
                        _account_text(mine["account"], mine["gl"]),
                        posted_account,
                        mine["description"],
                    )
                if cc_ok is not None:
                    coding["cc_n"] += 1
                    coding["cc_right"] += cc_ok
                    if gl_ok and not cc_ok:
                        diff(o, p, "line", "Cost center (business unit)", (mine["account"] or ("",))[0] or mine["cc"],
                             theirs["bu"], mine["description"])  # fmt: skip
                if not _close(mine["amount"], theirs["amount"]):
                    diff(o, p, "line", "Line amount", mine["amount"], theirs["amount"], mine["description"])
                if mine["proposed_gl"] in ("", UNASSIGNED):
                    coding["proposed_blank"] += 1
                else:
                    p_ok, p_cc = _line_verdict(mine["proposed_gl"], mine["proposed_account"], mine["proposed_cc"],
                                               theirs)  # fmt: skip
                    if p_ok and p_cc is not False:
                        coding["proposed_right"] += 1
                        lines["proposed_right"] += 1
        per_invoice.append({"id": o.id, "supplier": o.vendor_name, "vkey": o.vkey, "an8": o.an8 or p.an8,
                            "fields": sum(1 for n in HEADER_FIELDS if _posted_value(p, n) not in (None, "")),
                            "fields_wrong": len(wrong_fields), "lines": lines["n"], "lines_right": lines["right"],
                            "proposed_right": lines["proposed_right"], "touchless": o.touchless,
                            "wrong": bool(wrong_fields) or tax_wrong
                            or lines["proposed_right"] < lines["n"]})  # fmt: skip

    for o in ours_only:
        diff(o, None, "not in JD Edwards", "Invoice", o.grand_total, "", "approved in AP Coder, no voucher found")
    for p in posted_only:
        diff(None, p, "not in AP Coder", "Voucher", "", p.gross, "posted in JD Edwards, not approved in AP Coder")

    untaxed = [
        ("1 voucher's" if tax_not_compared == 1 else f"{tax_not_compared} vouchers'")
        + " tax is not compared: with the tax coded to G/L accounts the voucher (F0411) carries no tax, and the G/L "
        "export (F0911) was not given or their tax accounts are not in the account mapping (Settings → JD Edwards). "
        "Give the F0911 export to compare the tax with its tax account lines."
    ] if tax_not_compared else []  # fmt: skip
    return {
        "invoices": {
            "ap_coder": len(ours),
            "jd_edwards": len(posted),
            "matched": len(pairs),
            "matched_by_amount": sum(1 for *_, how in pairs if how != "supplier and invoice number"),
            "ap_coder_only": len(ours_only),
            "jd_edwards_only": len(posted_only),
        },  # fmt: skip
        "header": header,
        "coding": coding,
        "suppliers": _suppliers(per_invoice),
        "touchless": _touchless(per_invoice),
        "differences": diffs,
        "notes": [*(notes or []), *untaxed],
    }


def _suppliers(per_invoice: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Each supplier's first invoice in AP Coder against its later ones: the learning curve."""
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in sorted(per_invoice, key=lambda r: r["id"]):
        groups[row["vkey"] or row["an8"]].append(row)
    out = []
    for rows in groups.values():
        first, later = rows[:1], rows[1:]
        out.append({"supplier": rows[0]["supplier"], "an8": rows[0]["an8"], "invoices": len(rows),
                    "first": _part(first), "later": _part(later)})  # fmt: skip
    out.sort(key=lambda s: (-s["invoices"], s["supplier"].lower()))
    return out


def _part(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {"invoices": len(rows), "fields": sum(r["fields"] for r in rows),
            "fields_right": sum(r["fields"] - r["fields_wrong"] for r in rows),
            "lines": sum(r["lines"] for r in rows), "lines_right": sum(r["lines_right"] for r in rows),
            "proposed_right": sum(r["proposed_right"] for r in rows)}  # fmt: skip


def _touchless(per_invoice: list[dict[str, Any]]) -> dict[str, Any]:
    by: dict[str, dict[str, Any]] = {}
    for r in per_invoice:
        if not r["touchless"]:
            continue
        s = by.setdefault(r["vkey"] or r["an8"], {"supplier": r["supplier"], "invoices": 0, "wrong": 0, "ids": []})
        s["invoices"] += 1
        s["wrong"] += r["wrong"]
        if r["wrong"]:
            s["ids"].append(r["id"])
    rows = sorted(by.values(), key=lambda s: s["supplier"].lower())
    return {"suppliers": rows, "invoices": sum(s["invoices"] for s in rows), "wrong": sum(s["wrong"] for s in rows)}


# --- The report -----------------------------------------------------------------------------------------------------


def _pct(a: int, b: int) -> str:
    return "-" if not b else f"{100 * a / b:.1f}% ({a}/{b})"


def render_markdown(report: dict[str, Any], *, title: str = "Shadow pilot: AP Coder against JD Edwards") -> str:
    inv, header, coding = report["invoices"], report["header"], report["coding"]
    lines = [f"# {title}", ""]
    if report.get("made"):
        lines += [f"Made {report['made']} from {report.get('source', 'the JD Edwards export')}.", ""]
    lines += [
        f"- Invoices approved in AP Coder: {inv['ap_coder']}; vouchers in the JD Edwards export: {inv['jd_edwards']}.",
        f"- Matched: {inv['matched']}"
        + (
            f" ({inv['matched_by_amount']} by supplier and amount: their invoice numbers differ)"
            if inv["matched_by_amount"]
            else ""
        )
        + ".",  # fmt: skip
        f"- Approved in AP Coder, no voucher found: {inv['ap_coder_only']}. Posted in JD Edwards, not approved in AP "
        f"Coder: {inv['jd_edwards_only']}.",
        "",
        "## Header fields",
        "",
        "Each matched invoice's value as AP Coder approved it, against the voucher. A field the export does not have "
        "is not counted.",
        "",
        "| Field | Right |",
        "|---|---:|",
    ]
    lines += [
        f"| {label} | {_pct(header[name]['right'], header[name]['n'])} |" for name, label in HEADER_FIELDS.items()
    ]
    lines += ["", "## Coding", ""]
    if coding["lines"] or coding["posted_only"]:
        lines += [
            "Each invoice line's account against the G/L distribution posted (F0911), as BU.Object.Subsidiary through "
            "Settings → JD Edwards' account mapping, else the GL account against the object account. Recoverable "
            "GST/HST/QST lines are left out: E1 posts them from its own tax rules.",
            "",
            "| | |",
            "|---|---:|",
            f"| GL account as approved | {_pct(coding['gl_right'], coding['compared'])} |",
            "| GL account as AP Coder proposed it, before a person | "
            f"{_pct(coding['proposed_right'], coding['compared'])} ({coding['proposed_blank']} left blank) |",
            f"| Cost center (business unit) | {_pct(coding['cc_right'], coding['cc_n'])} |",
            f"| Tax area and explanation code | {_pct(coding['tax_right'], coding['tax_n'])} |",
            f"| Lines not posted with the same amount | {coding['not_posted']} |",
            f"| Posted lines AP Coder does not have | {coding['posted_only']} |",
        ]
    else:
        lines.append("No G/L distribution (F0911) to compare with: add `--distribution` with the F0911 export.")
        if coding["tax_n"]:
            lines += ["", f"Tax area and explanation code: {_pct(coding['tax_right'], coding['tax_n'])}."]
    lines += ["", "## By supplier", "",
              "Each supplier's first invoice in AP Coder against its later ones: the later ones show what it learned.",
              "", "| Supplier | AN8 | Invoices | First: fields | First: lines as proposed | Later: fields | "
              "Later: lines as proposed |", "|---|---|---:|---:|---:|---:|---:|"]  # fmt: skip
    total_first, total_later = defaultdict(int), defaultdict(int)
    for s in report["suppliers"]:
        f, later = s["first"], s["later"]
        for k, v in f.items():
            total_first[k] += v
        for k, v in later.items():
            total_later[k] += v
        lines.append(
            f"| {s['supplier']} | {s['an8']} | {s['invoices']} | {_pct(f['fields_right'], f['fields'])} | "
            f"{_pct(f['proposed_right'], f['lines'])} | {_pct(later['fields_right'], later['fields'])} | "
            f"{_pct(later['proposed_right'], later['lines'])} |"
        )
    if report["suppliers"]:
        lines.append(
            f"| **All** | | {sum(s['invoices'] for s in report['suppliers'])} | "
            f"{_pct(total_first['fields_right'], total_first['fields'])} | "
            f"{_pct(total_first['proposed_right'], total_first['lines'])} | "
            f"{_pct(total_later['fields_right'], total_later['fields'])} | "
            f"{_pct(total_later['proposed_right'], total_later['lines'])} |"
        )
    t = report["touchless"]
    lines += ["", "## Touchless", ""]
    if t["suppliers"]:
        lines += [f"{t['invoices']} invoices from suppliers that met the touchless bar when they were processed: "
                  f"{t['wrong']} of them would have gone to JD Edwards with something AP posted differently.", "",
                  "| Supplier | Invoices | Would have been wrong | AP Coder # |", "|---|---:|---:|---|"]  # fmt: skip
        lines += [f"| {s['supplier']} | {s['invoices']} | {s['wrong']} | {', '.join(map(str, s['ids']))} |"
                  for s in t["suppliers"]]  # fmt: skip
    else:
        lines.append("No supplier met the touchless bar yet.")
    unmatched = [d for d in report["differences"] if d["kind"] in ("not in JD Edwards", "not in AP Coder")]
    lines += ["", "## Not matched", ""]
    if unmatched:
        lines += ["| Side | Supplier | AN8 | Invoice number | Amount | AP Coder # | JDE document |",
                  "|---|---|---|---|---:|---|---|"]  # fmt: skip
        for d in unmatched:
            side = "AP Coder only" if d["kind"] == "not in JD Edwards" else "JD Edwards only"
            amount = d["ap_coder"] or d["jd_edwards"]
            lines.append(f"| {side} | {d['supplier']} | {d['an8']} | {d['invoice_number']} | {amount} | "
                         f"{d['ap_coder_id']} | {d['jde_document']} |")  # fmt: skip
    else:
        lines.append("Every invoice has its voucher.")
    found = [d for d in report["differences"] if d["kind"] not in ("not in JD Edwards", "not in AP Coder")]
    lines += ["", f"## Differences ({len(found)})", ""]
    if found:
        lines += ["| AP Coder # | Supplier | Invoice | What | AP Coder | JD Edwards | Note |",
                  "|---|---|---|---|---|---|---|"]  # fmt: skip
        lines += [f"| {d['ap_coder_id']} | {d['supplier']} | {d['invoice_number']} | {d['field']} | {d['ap_coder']} | "
                  f"{d['jd_edwards']} | {d['note']} |" for d in found]  # fmt: skip
    else:
        lines.append("None: every matched invoice is as AP posted it.")
    if report["notes"]:
        lines += ["", "## Notes on the export", "", *[f"- {n}" for n in report["notes"]]]
    return "\n".join(lines) + "\n"


DIFF_COLUMNS = ["kind", "supplier", "an8", "invoice_number", "ap_coder_id", "jde_document", "field", "ap_coder",
                "jd_edwards", "note"]  # fmt: skip


def differences_csv(report: dict[str, Any]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(DIFF_COLUMNS)
    for d in report["differences"]:
        writer.writerow(csv_row([d[c] for c in DIFF_COLUMNS]))
    return buf.getvalue().encode("utf-8-sig")


def figures(report: dict[str, Any]) -> dict[str, Any]:
    """The headline figures, no names or amounts: what Insights shows."""
    header, coding = report["header"], report["coding"]
    n = sum(h["n"] for h in header.values())
    return {"made": report.get("made", ""), **report["invoices"],
            "fields": n, "fields_right": sum(h["right"] for h in header.values()),
            "lines": coding["compared"], "lines_right": coding["gl_right"], "proposed_right": coding["proposed_right"],
            "touchless": report["touchless"]["invoices"], "touchless_wrong": report["touchless"]["wrong"],
            "differences": len(report["differences"])}  # fmt: skip


def in_window(date: str, start: str, end: str) -> bool:
    return not date or ((not start or date[:10] >= start) and (not end or date[:10] <= end))


def run(
    store: Any, vouchers: str | Path, distribution: str | Path | None = None, out: str | Path | None = None, *,
    implied_decimals: bool = False, start: str = "", end: str = "", every_voucher: bool = False,
) -> tuple[dict[str, Any], Path]:  # fmt: skip
    """Score the shadow pilot from the export files; writes the report and returns (report, folder written to).
    ``start``/``end``: invoice dates kept (default: the dates of the invoices approved in AP Coder, so the export may
    hold more; ``every_voucher`` keeps every voucher of the export)."""
    settings = jde.load(store)
    voucher_rows, sheets = read_table(vouchers)
    dist_rows: list[dict[str, str]] | None = None
    if distribution:
        dist_rows = read_table(distribution)[0]
    else:  # a workbook with an F0911 sheet beside the F0411 one
        named = next((s for s in sheets if "0911" in s), "")
        if named:
            dist_rows = read_table(vouchers, named)[0]
            first = next((s for s in sheets if "0411" in s), "")
            if first:
                voucher_rows = read_table(vouchers, first)[0]
    posted, notes = posted_vouchers(voucher_rows, dist_rows, implied_decimals=implied_decimals,
                                    domestic=settings.domestic_currency)  # fmt: skip
    ours = our_invoices(store, settings)
    ours = [o for o in ours if in_window(o.invoice_date, start, end)]
    if not every_voucher:
        dates = sorted(o.invoice_date[:10] for o in ours if o.invoice_date)
        lo, hi = start or (dates[0] if dates else ""), end or (dates[-1] if dates else "")
        kept = [p for p in posted if in_window(p.invoice_date, lo, hi)]
        if len(kept) < len(posted):
            left = len(posted) - len(kept)
            notes.append(f"{ui.plural(left, 'voucher')} dated outside {lo or '…'} to {hi or '…'} (the invoices "
                         f"approved in AP Coder) {'is' if left == 1 else 'are'} left out; --all keeps "
                         f"{'it' if left == 1 else 'them'}.")  # fmt: skip
        posted = kept
    report = score(ours, posted, notes)
    report["made"] = dt.datetime.now().isoformat(timespec="minutes").replace("T", " ")
    report["source"] = Path(vouchers).name + (f" and {Path(distribution).name}" if distribution else "")
    folder = Path(out) if out else Path(store.path).parent / FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    (folder / REPORT).write_text(render_markdown(report), encoding="utf-8")
    (folder / DIFFERENCES).write_bytes(differences_csv(report))
    (folder / FIGURES).write_text(json.dumps(figures(report), indent=1) + "\n", encoding="utf-8")
    return report, folder


def last_figures(store: Any) -> dict[str, Any] | None:
    """The figures of the last ``pilot-score`` run on this database (its default folder), or None."""
    try:
        return json.loads((Path(store.path).parent / FOLDER / FIGURES).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
