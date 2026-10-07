"""Find an invoice: answer a vendor's "did you get it, and when will it be paid?".

One search box: words of the vendor name, an invoice or PO number (however it is written), or an amount.
Each match says where the invoice stands; the ERP's invoice register (Exports page) is searched too.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .po import po_key
from .rules import _plain
from .safe import parse_amount
from .store import APPROVED, FAILED, PARKED, PENDING, REJECTED, REVIEW, Store
from .vendors import norm_invoice_number

LIMIT = 50


@dataclass
class Hit:
    score: int
    row: dict[str, Any]
    source: str = "AP Coder"  # or "ERP"


def _day(iso: str | None) -> str:
    return (iso or "")[:10]


def where(row: dict[str, Any]) -> str:
    """Where an invoice stands, in a sentence."""
    status = row["status"]
    approved = f"approved by {row['reviewer'] or '?'} on {_day(row['reviewed_at'])}"
    if row.get("second_reviewer"):
        approved += f", second approval by {row['second_reviewer']} on {_day(row['second_reviewed_at'])}"
    if status == REVIEW:
        return f"In the review queue since {_day(row['created_at'])}"
    if status == PARKED:
        follow = f"; follow up {row['follow_up']}" if row.get("follow_up") else ""
        return f"Parked, waiting for: {row.get('parked_reason') or '?'}{follow}"
    if status == PENDING:
        return f"Waiting for a second approval ({approved})"
    if status == APPROVED and row.get("export_batch"):
        return f"In the ERP: {approved}; exported in batch {row['export_batch']} on {_day(row.get('exported_at'))}"
    if status == APPROVED:
        return f"{approved[0].upper()}{approved[1:]}; not exported to the ERP yet"
    if status == REJECTED:
        return f"Rejected by {row['reviewer'] or '?'} on {_day(row['reviewed_at'])}: {row.get('error') or 'no reason'}"
    if status == FAILED:
        return f"Could not be read ({row.get('error') or 'error'}): process it again or enter it by hand"
    return status


def _raw(text: str) -> str:
    """Letters and digits only, leading zeros kept: "CN-2026-0047" -> "cn20260047"."""
    return re.sub(r"[^0-9a-z]", "", text.lower())


def _score(row: dict[str, Any], words: list[str], numbers: list[str]) -> int:
    """0 unless every part of the query matches: the words in the vendor (or file) name, and each number as
    the invoice number, the PO, the amount, or part of the name ("3m")."""
    score = 0
    name = _plain(f"{row.get('vendor_name') or ''} {row.get('file_name') or ''}")
    if words:
        if not all(w in name for w in words):
            return 0
        score += 2 + len(words)
    invoice_number = norm_invoice_number(row.get("invoice_number") or "")
    raw_number = _raw(row.get("invoice_number") or "")
    total = row.get("grand_total")
    for token in numbers:
        number, amount = norm_invoice_number(token), parse_amount(token)
        found = 0
        if number and invoice_number == number:
            found = 6
        elif len(_raw(token)) >= 3 and _raw(token) in raw_number:
            found = 3
        if number and row.get("po_key") and po_key(token) == row["po_key"]:
            found = max(found, 4)
        if amount is not None and total is not None and abs(abs(total) - abs(amount)) < 0.005:
            found = max(found, 5)
        if not found and _plain(token) in name.split():
            found = 1  # a vendor name with a digit in it
        if not found:
            return 0
        score += found
    return score


def _tokens(query: str) -> list[str]:
    """The query's parts; "18 017,85" (an amount written the French way) stays one part."""
    parts = [p.strip("\"'?!;:") for p in query.split()]
    out: list[str] = []
    for part in (p for p in parts if p):
        if out and re.fullmatch(r"[-+(]?\$?\d{1,3}", out[-1]) and re.fullmatch(r"\d{3}(?:[.,]\d+)?\)?\$?", part):
            out[-1] += part
        else:
            out.append(part)
    return out


def find(store: Store, query: str, limit: int = LIMIT) -> list[Hit]:
    tokens = [t.lower() for t in _tokens(query)]
    if not tokens:
        return []
    numbers = [w for w in tokens if any(c.isdigit() for c in w)]
    words = [_plain(w.strip(",.")) for w in tokens if w not in numbers and len(w.strip(",.")) > 1]
    rows = store.search_rows()
    hits = [Hit(s, r) for r in rows if (s := _score(r, words, numbers))]
    # An exported invoice's own row in the ERP register is the same bill: shown once, as the AP Coder invoice.
    ours = {(norm_invoice_number(r["invoice_number"] or ""), round(abs(r["grand_total"] or 0), 2)) for r in rows
            if r["export_batch"]}  # fmt: skip
    for r in store.erp_register():
        if (r["number_key"], round(abs(r["total"] or 0), 2)) in ours:
            continue
        row = {"vendor_name": r["vendor_name"], "invoice_number": r["invoice_number"], "grand_total": r["total"],
               "invoice_date": r["invoice_date"], "po_key": ""}  # fmt: skip
        if s := _score(row, words, numbers):
            hits.append(Hit(s, row, "ERP"))
    hits.sort(key=lambda h: (-h.score, h.source != "AP Coder", -(h.row.get("id") or 0)))
    return hits[:limit]
