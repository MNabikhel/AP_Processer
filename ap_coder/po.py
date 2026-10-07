"""Purchase orders and PO matching (2-way and, when receipts are known, 3-way).

AP imports open purchase orders from the ERP (one row per PO line, CSV or Excel). When an invoice
quotes a PO number, each invoice line is paired with the PO line it bills for, and the checks
compare what is billed with what was ordered (and received):

* PO_UNKNOWN (warning): the quoted PO is not in the list
* PO_CLOSED (warning): the PO is closed
* PO_VENDOR_MISMATCH (warning): the PO was raised for another vendor
* PO_LINE_NOT_ON_PO (warning): an invoice line matches nothing on the PO
* PO_PRICE_OVER (warning): unit price above the PO price beyond the tolerance
* PO_QTY_OVER (warning): billed so far (this and earlier invoices) exceeds the quantity ordered
* PO_NOT_RECEIVED (warning): billed so far exceeds the quantity received (3-way match)
* PO_OVER_BILLED (warning): invoices on this PO add up to more than the PO total
* PO_CODING_DIFFERS (info): the PO codes a line to another GL account / cost center
* PO_NOT_QUOTED (info): no PO on the invoice although the vendor has open POs
* PO_MATCHED (info): everything is within tolerance

Matching is local and deterministic; nothing here calls Azure.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any

from .memory import vendor_key

if TYPE_CHECKING:
    from .schema import InvoiceCoding
    from .store import Store

ERROR, WARNING, INFO = "error", "warning", "info"
OPEN, CLOSED = "open", "closed"
PRICE_TOLERANCE = 0.02  # unit price may be 2% above the PO price...
PRICE_TOLERANCE_ABS = 0.50  # ...or 50 cents, whichever is larger
TOTAL_TOLERANCE = 0.02  # invoices on a PO may total 2% more than the PO (rounding, small freight)
MATCH_THRESHOLD = 0.45

Finding = tuple[str, str, str, "int | None"]

# Column names people use in ERP exports, normalised (lower case, letters and digits only).
COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "po_number": ("ponumber", "po", "pono", "ponum", "purchaseorder", "purchaseordernumber", "ordernumber",
                  "order", "pobr", "bondecommande"),
    "vendor_name": ("vendorname", "vendor", "supplier", "suppliername", "fournisseur"),
    "line_number": ("linenumber", "line", "lineno", "linenum", "poline", "item", "itemnumber"),
    "description": ("description", "desc", "itemdescription", "linedescription", "product", "service"),
    "quantity": ("quantity", "qty", "orderedqty", "qtyordered", "quantityordered", "ordered"),
    "unit_price": ("unitprice", "price", "unitcost", "rate", "prixunitaire"),
    "amount": ("amount", "linetotal", "extendedamount", "extended", "total", "lineamount", "netamount"),
    "received_quantity": ("receivedquantity", "received", "qtyreceived", "receivedqty", "quantityreceived",
                          "receipts", "recu"),
    "gl_code": ("glcode", "gl", "glaccount", "account", "accountcode", "costcode", "expenseaccount"),
    "cost_center": ("costcenter", "costcentre", "cc", "department", "dept"),
    "status": ("status", "postatus", "state"),
}  # fmt: skip
REQUIRED_COLUMNS = ("po_number", "description")
TEMPLATE_COLUMNS = (
    "po_number", "vendor_name", "line_number", "description", "quantity", "unit_price", "amount",
    "received_quantity", "gl_code", "cost_center", "status",
)  # fmt: skip


def po_key(value: Any) -> str:
    """Compare PO numbers the way people mean them: "PO-00412", "PO 412" and "412" are the same."""
    text = re.sub(r"[^0-9a-z]", "", str(value or "").lower())
    text = re.sub(r"^(purchaseorder|po|bc|order|no|num)+", "", text) or text
    return text.lstrip("0") or text


def _norm_header(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def map_columns(headers: list[str]) -> dict[str, str]:
    """{canonical field: column in the file} for the columns that could be recognised."""
    found: dict[str, str] = {}
    normalised = {_norm_header(h): h for h in headers}
    for target, aliases in COLUMN_ALIASES.items():
        for alias in (_norm_header(target), *aliases):
            if alias in normalised and normalised[alias] not in found.values():
                found[target] = normalised[alias]
                break
    return found


def _blank(value: Any) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value)) or str(value).strip() == ""


def _number(value: Any) -> float | None:
    if _blank(value):
        return None
    text = str(value).replace(",", "").replace("$", "").strip()
    if text.startswith("(") and text.endswith(")"):
        text = "-" + text[1:-1]
    try:
        return float(text)
    except ValueError:
        return None


def _text(value: Any) -> str:
    if _blank(value):
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def rows_from_records(records: list[dict[str, Any]], columns: dict[str, str]) -> tuple[list[dict[str, Any]], int]:
    """Turn spreadsheet rows into PO line dicts using ``map_columns`` output. Returns (rows, skipped)."""
    rows: list[dict[str, Any]] = []
    skipped = 0
    for rec in records:

        def get(target: str, rec: dict[str, Any] = rec) -> Any:
            column = columns.get(target)
            return rec.get(column) if column else None

        number, description = _text(get("po_number")), _text(get("description"))
        if not number or not description:
            skipped += 1
            continue
        qty, price, amount = _number(get("quantity")), _number(get("unit_price")), _number(get("amount"))
        if qty is None:
            qty = 1.0 if price is None or amount is None or not price else amount / price
        if price is None:
            price = (amount / qty) if amount is not None and qty else 0.0
        if amount is None:
            amount = round(qty * price, 2)
        line = _number(get("line_number"))
        status = _text(get("status")).lower()
        rows.append(
            {
                "po_number": number,
                "vendor_name": _text(get("vendor_name")),
                "line_number": int(line) if line is not None else None,
                "description": description,
                "quantity": qty,
                "unit_price": price,
                "amount": amount,
                "received_quantity": _number(get("received_quantity")),
                "gl_code": _text(get("gl_code")),
                "cost_center": _text(get("cost_center")),
                "status": CLOSED if status in {"closed", "close", "complete", "completed", "fermé", "ferme"} else OPEN,
            }
        )
    return rows, skipped


# --- Matching ------------------------------------------------------------------------------------------

_STOP = {"the", "and", "for", "of", "a", "an", "to", "in", "with", "on", "per", "de", "la", "le", "et", "du", "des"}
_PREFIXES = re.compile(r"^\s*(return|credit|returned|refund|retour|crédit|credit note)\s*[-:–]?\s*", re.IGNORECASE)


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", _PREFIXES.sub("", text or "").lower())
    return {w for w in words if w not in _STOP and len(w) > 1}


def similarity(a: str, b: str) -> float:
    """0..1: how likely two line descriptions are the same item (word overlap, then character similarity)."""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return 0.0
    overlap = len(ta & tb) / min(len(ta), len(tb))
    ratio = SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    return max(overlap * 0.9, ratio)


def _same_price(a: float, b: float) -> bool:
    return abs(abs(a) - abs(b)) <= max(PRICE_TOLERANCE_ABS, abs(b) * PRICE_TOLERANCE)


def pair_with_po(invoice_lines: list[dict[str, Any]], po_lines: list[dict[str, Any]]) -> dict[int, int]:
    """{invoice line_number: PO line_number}. Each invoice line takes the PO line it most resembles
    (a matching unit price counts in favour); several invoice lines may bill the same PO line."""
    pairs: dict[int, int] = {}
    for li in invoice_lines:
        best, best_score = None, 0.0
        for pl in po_lines:
            score = similarity(li.get("description", ""), pl.get("description", ""))
            if _same_price(float(li.get("unit_price") or 0), float(pl.get("unit_price") or 0)):
                score += 0.2
            if score > best_score:
                best, best_score = pl, score
        if best is not None and best_score >= MATCH_THRESHOLD:
            pairs[int(li["line_number"])] = int(best["line_number"])
    return pairs


@dataclass
class LineMatch:
    invoice_line: int
    description: str
    quantity: float
    unit_price: float
    po_line: int | None = None
    po_description: str = ""
    po_quantity: float | None = None
    po_unit_price: float | None = None
    received: float | None = None
    billed_before: float = 0.0  # quantity billed on this PO line by other invoices
    po_gl: str = ""
    po_cc: str = ""
    problems: list[str] = field(default_factory=list)


@dataclass
class PoMatch:
    po_number: str
    found: bool
    po: dict[str, Any] | None = None
    lines: list[LineMatch] = field(default_factory=list)
    other_invoices: list[dict[str, Any]] = field(default_factory=list)
    billed_before: float = 0.0  # subtotal of other invoices on this PO
    findings: list[Finding] = field(default_factory=list)

    @property
    def coding_differs(self) -> list[LineMatch]:
        return [m for m in self.lines if "coding" in m.problems]


def po_label(number: str) -> str:
    """'PO-123' stays as is; '123' becomes 'PO 123'."""
    number = (number or "").strip()
    return number if re.match(r"(?i)^p\.?o\.?(?![a-z])", number) else f"PO {number}"


def _money(v: float) -> str:
    return f"${v:,.2f}"


def _qty(v: float) -> str:
    return f"{v:,.2f}".rstrip("0").rstrip(".")


def match_invoice(coding: InvoiceCoding, store: Store, exclude_invoice_id: int | None = None) -> PoMatch | None:
    """The PO match for one invoice, or None when the invoice quotes no PO (or no POs are loaded)."""
    number = (coding.po_number or "").strip()
    if not number or not store.has_purchase_orders():
        return None
    key = po_key(number)
    po = store.purchase_order(key)
    result = PoMatch(po_number=number, found=po is not None, po=po)
    if po is None:
        result.findings.append((WARNING, "PO_UNKNOWN", f"{po_label(number)} is not in the purchase orders list", None))
        return result

    result.findings.extend(_header_findings(coding, po))
    po_lines = po["lines"]
    by_line = {pl["line_number"]: pl for pl in po_lines}
    others = store.po_invoices(key, exclude_id=exclude_invoice_id)
    result.other_invoices = others
    result.billed_before = sum(float(o["coding"].get("subtotal") or 0) for o in others)
    billed_before = billed_by_line(po, others)

    items = [li.model_dump() for li in coding.line_items]
    pairs = pair_with_po(items, po_lines)
    billed_now: dict[int, float] = {}
    for li in items:
        m = LineMatch(li["line_number"], li["description"], float(li["quantity"]), float(li["unit_price"]))
        result.lines.append(m)
        pl = by_line.get(pairs.get(m.invoice_line, -1))
        if pl is None:
            m.problems.append("not_on_po")
            result.findings.append(
                (WARNING, "PO_LINE_NOT_ON_PO", f"no line on {po_label(number)} matches “{_short(m.description)}”",
                 m.invoice_line)
            )  # fmt: skip
            continue
        m.po_line, m.po_description = pl["line_number"], pl["description"]
        m.po_quantity, m.po_unit_price, m.received = pl["quantity"], pl["unit_price"], pl["received_quantity"]
        m.billed_before = billed_before.get(pl["line_number"], 0.0)
        m.po_gl, m.po_cc = pl.get("gl_code") or "", pl.get("cost_center") or ""
        _line_findings(m, li, result, billed_now)

    subtotal = float(coding.subtotal or 0)
    total = float(po.get("total") or 0)
    if subtotal > 0 and total > 0:
        billed = result.billed_before + subtotal
        if billed > total * (1 + TOTAL_TOLERANCE) + 0.01:
            earlier = f" ({_money(result.billed_before)} on earlier invoices)" if result.billed_before else ""
            result.findings.append(
                (WARNING, "PO_OVER_BILLED",
                 f"invoices on {po_label(number)} add up to {_money(billed)}{earlier}, "
                 f"more than the PO total {_money(total)}",
                 None)
            )  # fmt: skip
    if not any(f[0] in (ERROR, WARNING) for f in result.findings):
        matched = sum(1 for m in result.lines if m.po_line is not None)
        three_way = any(m.received is not None for m in result.lines)
        result.findings.append(
            (INFO, "PO_MATCHED",
             f"matches {po_label(number)}: {matched} line(s) within price and quantity"
             + (" ordered and received" if three_way else " ordered"), None)
        )  # fmt: skip
    return result


def _short(text: str, n: int = 50) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


def _header_findings(coding: InvoiceCoding, po: dict[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    number = po["po_number"]
    if po.get("status") == CLOSED:
        findings.append(
            (WARNING, "PO_CLOSED", f"{po_label(number)} is closed: confirm it should still be billed", None)
        )
    po_vendor = po.get("vendor_key") or ""
    if po_vendor and vendor_key(coding.vendor_name) != po_vendor:
        if similarity(coding.vendor_name, po.get("vendor_name") or "") < 0.6:
            findings.append(
                (WARNING, "PO_VENDOR_MISMATCH",
                 f"{po_label(number)} was raised for {po.get('vendor_name')}, not {coding.vendor_name}", None)
            )  # fmt: skip
    return findings


def _line_findings(m: LineMatch, li: dict[str, Any], result: PoMatch, billed_now: dict[int, float]) -> None:
    number = result.po_number
    assert m.po_line is not None and m.po_unit_price is not None and m.po_quantity is not None
    if m.quantity > 0 and abs(m.unit_price) > abs(m.po_unit_price) and not _same_price(m.unit_price, m.po_unit_price):
        over = (abs(m.unit_price) / abs(m.po_unit_price) - 1) if m.po_unit_price else 1.0
        m.problems.append("price")
        result.findings.append(
            (WARNING, "PO_PRICE_OVER",
             f"unit price {_money(m.unit_price)} is above the PO price {_money(m.po_unit_price)} (+{over:.1%})",
             m.invoice_line)
        )  # fmt: skip
    billed_now[m.po_line] = billed_now.get(m.po_line, 0.0) + m.quantity
    billed = m.billed_before + billed_now[m.po_line]
    if m.quantity > 0 and billed > m.po_quantity + 1e-6:
        earlier = f", {_qty(m.billed_before)} on earlier invoices" if m.billed_before else ""
        m.problems.append("quantity")
        result.findings.append(
            (WARNING, "PO_QTY_OVER",
             f"{_qty(billed)} billed on {po_label(number)} line {m.po_line} but {_qty(m.po_quantity)} ordered{earlier}",
             m.invoice_line)
        )  # fmt: skip
    elif m.quantity > 0 and m.received is not None and billed > m.received + 1e-6:
        m.problems.append("received")
        result.findings.append(
            (WARNING, "PO_NOT_RECEIVED",
             f"{_qty(billed)} billed but only {_qty(m.received)} received on {po_label(number)} line {m.po_line}",
             m.invoice_line)
        )  # fmt: skip
    gl, cc = li.get("predicted_gl_code") or "", li.get("predicted_cost_center") or ""
    if (m.po_gl and m.po_gl != gl) or (m.po_cc and m.po_cc != cc):
        m.problems.append("coding")
        target = " · ".join(x for x in (f"GL {m.po_gl}" if m.po_gl else "", m.po_cc) if x)
        result.findings.append(
            (INFO, "PO_CODING_DIFFERS", f"{po_label(number)} line {m.po_line} is coded to {target}", m.invoice_line)
        )


def po_findings(coding: InvoiceCoding, store: Store, exclude_invoice_id: int | None = None) -> list[Finding]:
    """The checks for ``validate_coding`` (empty when no purchase orders are loaded)."""
    if not store.has_purchase_orders():
        return []
    if not (coding.po_number or "").strip():
        open_pos = store.open_pos_for_vendor(vendor_key(coding.vendor_name))
        if open_pos:
            listed = ", ".join(po_label(n) for n in open_pos[:3]) + ("…" if len(open_pos) > 3 else "")
            return [(INFO, "PO_NOT_QUOTED", f"no PO number on the invoice; this vendor has open {listed}", None)]
        return []
    result = match_invoice(coding, store, exclude_invoice_id)
    return result.findings if result else []


def billed_by_line(po: dict[str, Any], invoices: list[dict[str, Any]]) -> dict[int, float]:
    """Quantity billed so far on each PO line by ``invoices`` (``Store.po_invoices`` rows; credits subtract)."""
    billed: dict[int, float] = {}
    for inv in invoices:
        lines = inv["coding"].get("line_items") or []
        quantities = {int(li["line_number"]): float(li.get("quantity") or 0) for li in lines}
        for inv_line, po_line in pair_with_po(lines, po["lines"]).items():
            billed[po_line] = billed.get(po_line, 0.0) + quantities.get(inv_line, 0.0)
    return billed


def template_csv() -> bytes:
    example = [
        "PO-1001", "Example Supplier Ltd.", "1", "Example item or service", "10", "25.00", "250.00", "10",
        "6000", "", "open",
    ]  # fmt: skip
    return ("﻿" + ",".join(TEMPLATE_COLUMNS) + "\r\n" + ",".join(example) + "\r\n").encode("utf-8")
