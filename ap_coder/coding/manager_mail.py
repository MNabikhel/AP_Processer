"""The ready-made email that asks a manager to confirm an invoice's cost allocation (D-138), for the laptop, where
AP Coder sends nothing itself: AP downloads it, Outlook opens it as a draft to send (``X-Unsent: 1``), and the
manager's reply is later recorded on the invoice with the reply kept.

It carries the invoice's PDF (or picture), who it is from and for how much, the lines as AP coded them (GL account,
cost center, amount; the manager's own cost centers marked), AP's note, and the request number in the subject so the
reply is easy to match. Plain text and HTML, both. Entry point: ``request_email``.
"""

from __future__ import annotations

import html
import mimetypes
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path
from typing import Any

MAX_ATTACHMENT = 20 * 1024 * 1024  # a larger file is left out (said in the email): most mail servers refuse it


def _money(value: Any, currency: str) -> str:
    try:
        return f"{float(value):,.2f} {currency}".strip()
    except (TypeError, ValueError):
        return ""


def subject(inv: dict[str, Any], request_id: int) -> str:
    vendor = inv.get("vendor_name") or "an invoice"
    number = inv.get("invoice_number") or "without a number"
    total = _money(inv.get("grand_total"), inv.get("currency") or "")
    amount = f" ({total})" if total else ""
    return f"Please confirm the cost allocation: {vendor} invoice {number}{amount} [AP-{request_id}]"


def _rows(req: dict[str, Any], currency: str, mine: set[str]) -> list[tuple[str, str, str, str, str, bool]]:
    return [(str(li["line"]), li["description"], li["gl_code"] or "-", li["cost_center"] or "-",
             _money(li["amount"], currency), li["cost_center"] in mine) for li in req.get("lines") or []]  # fmt: skip


def bodies(inv: dict[str, Any], req: dict[str, Any], signature: str = "",
           mine: set[str] = frozenset()) -> tuple[str, str]:  # fmt: skip
    """(plain text, HTML) of the email, the lines of the manager's own cost centers (``mine``) marked."""
    currency = inv.get("currency") or ""
    who = req.get("manager_name") or ""
    facts = [("Vendor", inv.get("vendor_name") or "?"), ("Invoice", inv.get("invoice_number") or "?"),
             ("Date", inv.get("invoice_date") or "?"),
             ("Total", _money(inv.get("grand_total"), currency) or "?")]  # fmt: skip
    note = (req.get("note") or "").strip()
    ask = ("Please reply CONFIRMED if this is right, or tell us what to change (the GL account, the cost center or "
           "the amount of a line). The invoice is attached.")  # fmt: skip
    rows = _rows(req, currency, mine)
    intro = "Accounts payable would like you to confirm how this invoice is coded before it is approved."
    text = [f"Hello {who}," if who else "Hello,", "", intro, ""]
    text += [f"{k}: {v}" for k, v in facts] + [""]
    text += ["Line | Description | GL account | Cost center | Amount"]
    text += [" | ".join(r[:5]) for r in rows] + [""]
    if note:
        text += [f"Note from AP: {note}", ""]
    text += [ask, "", f"Reference: AP-{req.get('id')} (AP Coder #{inv.get('id')})"]
    if signature:
        text += ["", signature]
    e = html.escape
    mark, right = ' style="background:#fff4e0"', ' style="text-align:right"'  # (no backslash in an f-string: 3.11)
    cells = "".join(
        f"<tr{mark if r[5] else ''}><td>{e(r[0])}</td><td>{e(r[1])}</td><td>{e(r[2])}</td><td>{e(r[3])}</td>"
        f"<td{right}>{e(r[4])}</td></tr>"
        for r in rows
    )
    page = (
        f"<p>{e(f'Hello {who},' if who else 'Hello,')}</p>"
        "<p>Accounts payable would like you to confirm how this invoice is coded before it is approved.</p>"
        '<table style="border-collapse:collapse">'
        + "".join(f'<tr><td style="padding-right:12px"><b>{e(k)}</b></td><td>{e(v)}</td></tr>' for k, v in facts)
        + '</table><br><table border="1" cellpadding="4" style="border-collapse:collapse"><tr><th>Line</th>'
        "<th>Description</th><th>GL account</th><th>Cost center</th><th>Amount</th></tr>"
        + cells
        + "</table>"
        + (f"<p><b>Note from AP:</b> {e(note)}</p>" if note else "")
        + f'<p>{e(ask)}</p><p style="color:#666">Reference: AP-{e(str(req.get("id")))} '
        f"(AP Coder #{e(str(inv.get('id')))})</p>" + (f"<p>{e(signature)}</p>" if signature else "")
    )
    return "\n".join(text) + "\n", page


def request_email(inv: dict[str, Any], req: dict[str, Any], file: Path | None, signature: str = "",
                  mine: set[str] = frozenset()) -> bytes:  # fmt: skip
    """The email as an .eml file (Outlook and Mail open it as a draft to send); ``mine``: the manager's own cost
    centers (``Store.managers_cost_centers``)."""
    msg = EmailMessage(policy=SMTP)
    msg["To"] = req.get("manager_email") or ""
    msg["Subject"] = subject(inv, int(req.get("id") or 0))
    msg["X-Unsent"] = "1"  # Outlook opens it as a new message to send, not as one received
    text, page = bodies(inv, req, signature, mine)
    if file is not None and file.is_file() and file.stat().st_size > MAX_ATTACHMENT:
        text += "\n(The invoice is too large to attach: it is in AP Coder.)\n"
        file = None
    msg.set_content(text)
    msg.add_alternative(page, subtype="html")
    if file is not None and file.is_file():
        kind = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        main, sub = kind.split("/", 1)
        msg.add_attachment(file.read_bytes(), maintype=main, subtype=sub, filename=file.name)
    return msg.as_bytes()
