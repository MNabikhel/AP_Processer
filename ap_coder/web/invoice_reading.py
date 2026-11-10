"""What the review screen says of how the invoice was read: the page, and OvisOCR2 and the AI reviewer on it.

* How the pages were read (``document_kind``: a vendor's PDF, a scan...), the invoice's capture (``capture_of``) and
  how sure it is of each field: the badge beside a field's label (``capture_confidence``, ``shown_sure``).
* The lines under the document's title: what OvisOCR2 made of this invoice (``reader_state``: read, waiting, reading,
  skipped, failed; ``waiting_line``, ``agreement_line``, ``not_running``) and what the AI reviewer did
  (``reviewer_state``).

The page viewer's own data (the pages' pictures, the boxes) is ``web/document.py``'s.
"""

from __future__ import annotations

import math
from typing import Any

from ap_coder.ai import verifier
from ap_coder.ai.extraction import TEXT_EXTENSIONS
from ap_coder.capture.bridge import currency_read
from ap_coder.capture.types import MISSING, CaptureResult
from ap_coder.capture.workflow import AP_LESSON
from ap_coder.core.config import Settings
from ap_coder.core.store import Store
from ap_coder.core.ui import plural
from ap_coder.web.invoice_form import AMOUNT_FIELDS

# --- The page as read ---------------------------------------------------------------------------------------------

# How the invoice came in, from how its pages were read: a vendor's own PDF has its text inside and is read as printed;
# a scan or photo (or a picture saved as a PDF) is read with OCR, so its values deserve a closer look.
DOCUMENT_KINDS = {
    "text": ("Vendor PDF", "ok", "picture_as_pdf", "The PDF has its text inside: read exactly as printed."),
    "scan": ("Scan or photo", "warn", "photo_camera",
             "A picture of the invoice, read with OCR (reading the letters off the image): check its values closely."),
    "mixed": ("Partly scanned", "warn", "photo_camera",
              "Some pages have their text inside; the others are pictures, read with OCR: check their values closely."),
}  # fmt: skip


def document_kind(layout_source: str) -> dict[str, str] | None:
    """{key, label, tone, icon, title} for how the pages were read (``CaptureResult.layout_source``); None when they
    were not read (no capture, or nothing on the page)."""
    key = {"text": "text", "ocr": "scan", "di": "scan", "mixed": "mixed"}.get(layout_source or "")
    if key is None:
        return None
    label, tone, icon, title = DOCUMENT_KINDS[key]
    return {"key": key, "label": label, "tone": tone, "icon": icon, "title": title}


def capture_of(store: Store, invoice_id: int) -> CaptureResult | None:
    raw = store.get_capture(invoice_id)
    if not raw:
        return None
    try:
        return CaptureResult.from_dict(raw)
    except (KeyError, TypeError, ValueError):
        return None


# How sure the capture is of a field, beside its label in the form: (tone, icon).
CAPTURE_BADGES = {"verified": ("ok", "check_circle"), "likely": ("info", "check"), "check": ("warn", "error"),
                  "failed": ("err", "cancel")}  # fmt: skip
# ... in the words of the page's colour key (static/js/invoice_viewer.js, STATUS_HELP), with what each means (B-435).
CAPTURE_WORDS = {"verified": "Verified", "likely": "Likely", "check": "Check", "failed": "Failed check"}
CAPTURE_HELP = {
    "verified": "Verified: the readers agree and the checks pass.",
    "likely": "Likely: one strong reading and the checks pass. A quick look.",
    "check": "Check: the readers disagree or are unsure. Look at it on the page.",
    "failed": "Failed check: a check on this value failed. Look at it on the page.",
}


def capture_confidence(capture: CaptureResult | None) -> dict[str, dict[str, str]]:
    """{field: {tone, icon, text}}: how sure the capture is of each field, beside its label in the form."""
    if capture is None:
        return {}
    out = {}
    for name, fr in capture.fields.items():
        failed = any(r.startswith("check failed") for r in fr.reasons)
        status = "failed" if failed and fr.status != MISSING else fr.status
        if status not in CAPTURE_BADGES:
            continue
        tone, icon_name = CAPTURE_BADGES[status]
        # the status in words, and how sure in figures where that tells more (never rounded up to certainty)
        sure = f"{math.floor(fr.confidence * 100 + 1e-9)}%"
        words = CAPTURE_WORDS[status] + (f" {sure}" if status in ("likely", "check") else "")
        about = CAPTURE_HELP[status]
        if AP_LESSON in fr.sources:  # not read: what AP set on the vendor's last invoices (D-121)
            words = "As you set it" + (f" {sure}" if status != "verified" else "")
            about = "The page does not print it: AP set it this way on this vendor's last invoices."
        out[name] = {"tone": tone, "icon": icon_name, "text": words, "status": status,
                     "help": f"{about} How sure: {sure}."}  # fmt: skip
    return out


def shown_sure(sure: dict[str, dict[str, str]], header: dict[str, str], read: dict[str, Any],
               capture: CaptureResult | None) -> dict[str, dict[str, str]]:  # fmt: skip
    """The reading's badges the form shows: only beside a value that is still the one read (an empty field, or one
    the reviewer changed, has none: the badge would vouch for what is not there), and none for a currency the page
    does not print (the domestic CAD assumed: "Check" on almost every invoice said nothing)."""

    def same(name: str) -> bool:
        now, was = str(header.get(name) or "").strip(), read.get(name)
        if not now:
            return False
        if name in AMOUNT_FIELDS:
            try:
                return abs(float(now) - float(was or 0)) < 0.005
            except (TypeError, ValueError):
                return False
        return now.casefold() == str(was or "").strip().casefold()

    out = {}
    for name, badge in sure.items():
        if not same(name):
            continue
        fr = capture.fields.get(name) if capture is not None else None
        if name == "currency" and fr is not None and not currency_read(fr) and header.get("currency") == "CAD":
            continue
        out[name] = badge
    return out


# --- OvisOCR2 and the AI reviewer, for this invoice ---------------------------------------------------------------

NOT_TOUCHLESS = "It can't be approved without a person until OvisOCR2 has read it."


def waiting_line(status: Any, ahead: int) -> str:
    """ "Waiting for OvisOCR2: 2 invoices ahead, about 6 minutes" (the time when a page time has been measured)."""
    from ap_coder.ai import page_reader

    where = f"{plural(ahead, 'invoice')} ahead" if ahead else "it's next"
    eta = ""
    if status is not None and status.eta_minutes and status.queue:
        eta = ", " + page_reader.duration(status.eta_minutes * 60 * min(ahead + 1, status.queue) / status.queue)
    return f"Waiting for OvisOCR2: {where}{eta}."


def agreement_line(done: dict[str, Any]) -> str:
    """ "OvisOCR2 read this invoice: 9 of 11 fields agree" and what else it found."""
    agree, differ, only = (len(done.get(k) or []) for k in ("agree", "differ", "only"))
    line = f"OvisOCR2 read this invoice: {agree} of {agree + differ + only} fields agree"
    extra = []
    if differ:
        extra.append(f"{differ} read differently, marked Check")
    if only:
        extra.append(f"{only} found only by OvisOCR2")
    figs = done.get("figures") or {}
    total = max(figs.get("figures", 0), figs.get("first_figures", 0))
    if total:
        extra.append(f"{figs.get('confirmed', 0)} of {total} figures on the page read the same by both")
    minutes = (done.get("seconds") or 0) / 60
    return line + (f" ({'; '.join(extra)})" if extra else "") + (f" · {minutes:.1f} min" if minutes else "")


def not_running(status: Any, settings: Settings, store: Store) -> str:
    """Why OvisOCR2 isn't reading now ("" when it is): from the reading status, else from the background reader."""
    if status is not None:
        return "" if status.page_reader_ready else "OvisOCR2 isn't running: see Settings → Reading."
    from ap_coder.ai.page_worker import ready

    try:
        model, _why = ready(settings, store)
    except Exception:  # noqa: BLE001 - it can't be asked: said as not running
        model = ""
    return "" if model else "OvisOCR2 isn't running: see Settings → Reading."


def reviewer_state(store: Store, inv: dict[str, Any]) -> dict[str, Any]:
    """What the AI reviewer (verifier.py) made of this invoice, for a line under the document title: {state, lines:
    [(icon, text)], again: offer to review it again}."""
    if not verifier.load_settings(store).on:
        return {"state": "off", "lines": [], "again": False}
    row = store.verification(inv["id"])
    if row is None:
        return {"state": "none", "lines": [], "again": True}
    state, result = row["status"], row.get("result") or {}
    name = verifier.NAME
    if state == "verifying":
        return {"state": state, "lines": [("hourglass_top", f"The {name} is checking this invoice against its pages "
                                           "now.")], "again": False}  # fmt: skip
    if state == "waiting":
        text = (
            f"The {name} checks this invoice against its pages when the chat model is free (after OvisOCR2). "
            "It isn't approved without a person until it agrees."
        )
        lines = [("hourglass_top", text)]
        if row.get("error"):
            lines.append(("sync_problem", f"Last time: {row['error']}"))
        return {"state": state, "lines": lines, "again": False}
    if state == "failed":
        return {"state": state, "lines": [("error", f"The {name} couldn't check it: {row.get('error')}. A person "
                                                    "decides.")], "again": True}  # fmt: skip
    if state == "skipped":
        return {"state": state, "lines": [("info", f"Not checked by the {name}: {row.get('error')}.")], "again": True}
    took = f"{plural(int(result.get('rounds') or 0), 'round')}, {float(result.get('seconds') or 0):.0f} s"
    status = result.get("status")
    if status == verifier.AGREED:
        line = ("verified", f"The {name} checked every field and line against the pages and agrees ({took}).")
    elif status == verifier.DISAGREES:
        what = ", ".join(verifier.FIELDS.get(f, f) for f in result.get("fields") or {})
        lines_ = ", ".join(f"line {n}" for n in result.get("lines") or {})
        line = ("rule", f"The {name} reads {', '.join(x for x in (what, lines_) if x)} differently ({took}): see the "
                        "checks.")  # fmt: skip
    else:
        line = ("help", f"The {name} is not sure ({result.get('why') or took}): a person checks it.")
    out = [line]
    if result.get("summary"):
        out.append(("chat", f"“{result['summary']}”"))
    return {"state": status or state, "lines": out, "again": True}


def reader_state(store: Store, settings: Settings, inv: dict[str, Any], status: Any) -> dict[str, Any]:
    """What OvisOCR2 made of this invoice, for the line under the document title: ``state`` is "read" (its reading
    is in), "waiting", "reading", "skipped", "done" (read, not applied), "failed", "none" (not in its queue) or ""
    (a text file: no page to read). ``lines``: (icon, text); ``queue``: offer to put it in OvisOCR2's queue."""
    path = str(inv.get("source_path") or "")
    if any(path.lower().endswith(s) for s in TEXT_EXTENSIONS):
        return {"state": "", "lines": [], "queue": False, "poll": False}
    meta = inv.get("meta") or {}
    done = meta.get("page_reader")
    row = store.page_read(inv["id"]) or {}
    state = row.get("status", "")
    if done:
        lines = [("visibility", agreement_line(done))]
        figs = done.get("figures") or {}
        if figs.get("differ"):
            first = "PDF text" if (meta.get("capture") or {}).get("layout") == "text" else "OCR"
            pairs = "; ".join(f"{a} (OvisOCR2) / {b} ({first})" for a, b in figs["differ"])
            lines.append(("compare_arrows", f"Read differently: {pairs}"))
        return {"state": "read", "lines": lines, "queue": False, "poll": False, "at": done.get("at", "")}
    if state == "reading":
        return {"state": state, "queue": False, "poll": True, "lines": [(
            "hourglass_top", "OvisOCR2 is reading this invoice now. The fields update when it is done, unless you "
            f"have edited the invoice. {NOT_TOUCHLESS}")]}  # fmt: skip
    if state == "waiting":
        stopped = not_running(status, settings, store)
        if stopped:
            lines = [("pause_circle", f"{stopped} {NOT_TOUCHLESS}")]
        else:
            ahead = store.page_reads_ahead(inv["id"])  # in line before it, and the one read now
            lines = [("hourglass_top", f"{waiting_line(status, ahead)} The fields update when it is done, unless "
                                       f"you have edited the invoice. {NOT_TOUCHLESS}")]  # fmt: skip
        if row.get("tries") and row.get("error"):  # cut off by the model server last time
            lines.append(("sync_problem", f"Last time: {row['error']}"))
        return {"state": state, "lines": lines, "queue": False, "poll": True}
    reason = str(row.get("error") or "no reason given").rstrip(".")
    if state == "skipped":
        return {"state": state, "lines": [("error", f"OvisOCR2 couldn't read it: {reason}; a person decides.")],
                "queue": False, "poll": False}  # fmt: skip
    if state == "done":  # read, but its reading wasn't applied (the invoice was edited or decided meanwhile)
        return {"state": state, "lines": [("visibility", f"OvisOCR2 read this invoice: {reason}.")], "queue": False,
                "poll": False}  # fmt: skip
    if state == "failed":  # e.g. cut off by the model server MAX_TRIES times: it can be read again from here
        return {"state": state, "lines": [("error", f"OvisOCR2 couldn't read it: {reason}; a person decides.")],
                "queue": True, "poll": False}  # fmt: skip
    stopped = not_running(status, settings, store)
    text = f"OvisOCR2 hasn't read this invoice yet. {stopped} {NOT_TOUCHLESS}".replace("  ", " ")
    return {"state": "none", "lines": [("visibility_off", text)], "queue": True, "poll": False}
