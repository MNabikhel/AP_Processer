"""The invoice page on the review screen: every header field boxed on the page, coloured by how sure
AP Coder is, with teach-by-click.

Clicking a field in the panel shows it on the page. *Teach* asks the reviewer to click the words that
hold a field: the value goes into the form, and on approval the supplier's template learns where that
field is printed, so the next invoice from the same supplier is read from the right place.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from ap_coder import page_reader, ui
from ap_coder.capture.confidence import LABELS
from ap_coder.capture.normalize import parse_amount, parse_date
from ap_coder.capture.types import Box, CaptureResult
from ap_coder.safe import md
from ap_coder.webapp.common import (
    esc,
    forget_drafts,
    get_settings,
    get_store,
    notify,
    reading_status_or_none,
    reviewer,
)
from ap_coder.webapp.viewer import invoice_viewer, page_words, render_pages

VIEWER_HEIGHT = 760  # px; the page scrolls inside, the field list stays beside it
PAGE_READER_POLL_SECONDS = 20  # while the page reader has the open invoice in its queue
VIEWABLE = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".heic", ".heif"}

# Capture fields a reviewer can teach: they are fields of the review form (same names).
TEACHABLE = (
    "vendor_name", "invoice_number", "invoice_date", "due_date", "po_number", "gst_hst_registration_number",
    "qst_registration_number", "payment_terms", "currency", "subtotal", "tax_total", "grand_total",
)  # fmt: skip
AMOUNT_FORM_FIELDS = {"subtotal", "tax_total", "grand_total"}
# The review form's header fields (widgets keyed ``inv<id>_<field>``).
FORM_FIELDS = (
    *TEACHABLE, "supplier_province", "ship_to_province", "remit_bank_account", "original_invoice_number",
)  # fmt: skip


def _teach_key(key: str) -> str:
    return f"{key}_teach"


def _taught_key(key: str) -> str:
    return f"{key}_taught"


def taught_boxes(key: str) -> dict[str, list[Box]]:
    """{field: [Box]} the reviewer showed on the page for this invoice (for the supplier's template)."""
    raw = st.session_state.get(_taught_key(key)) or {}
    return {f: [Box.from_list(b) for b in boxes] for f, boxes in raw.items()}


def _form_value(field: str, text: str) -> Any:
    if field in AMOUNT_FORM_FIELDS:
        return parse_amount(text)
    if field in ("invoice_date", "due_date"):
        return parse_date(text, prefer_day_first=None) or parse_date(text, prefer_day_first=True) or text.strip()
    return " ".join(text.split())


def _viewer_fields(capture: CaptureResult) -> list[dict[str, Any]]:
    out = []
    for fr in capture.fields.values():
        d = fr.to_dict()
        d["label"] = LABELS.get(fr.field, fr.field)
        d["failed"] = any(r.startswith("check failed") for r in fr.reasons)
        out.append(d)
    return out


def document_head(file_name: str) -> str:
    """The document card's title line: what it is and the file it came from."""
    return (
        f"<div class='rvw-doc-head'><b>Document</b>"
        f"<span class='file' title='{esc(file_name)}'>{esc(file_name)}</span></div>"
    )


def edited_on_screen(key: str, shown: dict[str, Any]) -> bool:
    """Whether the review form holds something other than ``shown`` (the proposal its fields were drawn from): a
    header field typed or taught, or a line or tax row changed, added or deleted. Read from the widgets themselves,
    so an edit made on the run under way counts too."""
    state = st.session_state
    for field in FORM_FIELDS:
        name = f"{key}_{field}"
        if name not in state:
            continue
        value, before = state[name], shown.get(field, "CAD" if field == "currency" else "")  # the form's defaults
        if isinstance(value, int | float) and not isinstance(value, bool):
            try:
                if abs(float(before or 0) - float(value)) > 0.005:
                    return True
            except (TypeError, ValueError):
                return True
        elif str(value or "").strip() != str(before or "").strip():
            return True
    for grid in ("lines", "taxlines"):
        edits = state.get(f"{key}_{grid}")
        if isinstance(edits, dict) and any(edits.get(k) for k in ("edited_rows", "added_rows", "deleted_rows")):
            return True
    return False


def follow_page_reader(invoice_id: int, key: str, meta: dict[str, Any]) -> None:
    """The page reader's reading came in while this invoice was open (it reads in the background): the fields show
    it at once when the reviewer hasn't typed anything; what they typed is never changed under them (a note under the
    document offers to start over from the reading instead)."""
    at = (meta.get("page_reader") or {}).get("at", "")
    seen = st.session_state.setdefault(f"{key}_page_reader_seen", at)
    if f"{key}_page_reader_base" not in st.session_state:  # what the fields were drawn from, before any reading
        inv = get_store().get_invoice(invoice_id) or {}
        st.session_state[f"{key}_page_reader_base"] = inv.get("final_output") or inv.get("ai_output") or {}
    if at == seen:
        return
    # Edits counted on an earlier run, or made on this very run (the reading came in just before it, and this run's
    # edit isn't counted yet): either way they are kept.
    if invoice_id in (st.session_state.get("unsaved_edits") or set()) or edited_on_screen(
        key, st.session_state[f"{key}_page_reader_base"]
    ):
        st.session_state[f"{key}_page_reader_seen"] = at
        st.session_state[f"{key}_page_reader_kept"] = True
        return
    forget_drafts(key)  # the fields are drawn again from the new proposal
    st.session_state[f"{key}_page_reader_seen"] = at
    notify("OvisOCR2 has read this invoice: the fields show its reading now.", ":material/visibility:")
    st.rerun()


NOT_TOUCHLESS = "It can't be approved without a person until OvisOCR2 has read it."


def _not_running(status: Any) -> str:
    """Why OvisOCR2 isn't reading now ("" when it is): from the reading status, else from the background reader."""
    if status is not None:
        return "" if status.page_reader_ready else "OvisOCR2 isn't running: see Settings → Reading."
    from ap_coder.page_worker import ready

    try:
        model, _why = ready(get_settings(), get_store())
    except Exception:  # noqa: BLE001 - it can't be asked: said as not running
        model = ""
    return "" if model else "OvisOCR2 isn't running: see Settings → Reading."


def waiting_line(status: Any, ahead: int) -> str:
    """ "Waiting for OvisOCR2: 2 invoices ahead, about 6 minutes" (the time when a page time has been measured)."""
    where = f"{ui.plural(ahead, 'invoice')} ahead" if ahead else "it's next"
    eta = ""
    if status is not None and status.eta_minutes and status.queue:
        eta = ", " + page_reader.duration(status.eta_minutes * 60 * min(ahead + 1, status.queue) / status.queue)
    return f"Waiting for OvisOCR2: {where}{eta}."


@st.fragment(run_every=PAGE_READER_POLL_SECONDS)
def _page_reader_waiting(invoice_id: int) -> None:
    """While OvisOCR2 has this invoice in its queue: where it is, looked up again every few seconds; the whole screen
    is drawn again once it is done, so the fields show its reading."""
    store = get_store()
    row = store.page_read(invoice_id) or {}
    state = row.get("status", "")
    if state not in ("waiting", "reading"):
        st.rerun()
    if state == "reading":
        st.caption(":material/hourglass_top: OvisOCR2 is reading this invoice now. The fields update when it is "
                   f"done, unless you have edited the invoice. {NOT_TOUCHLESS}")  # fmt: skip
        return
    status = reading_status_or_none()
    stopped = _not_running(status)
    if stopped:
        st.caption(f":material/pause_circle: {stopped} {NOT_TOUCHLESS}")
        return
    ahead = store.page_reads_ahead(invoice_id)  # in line before it, and the one read now
    st.caption(f":material/hourglass_top: {waiting_line(status, ahead)} The fields update when it is done, unless "
               f"you have edited the invoice. {NOT_TOUCHLESS}")  # fmt: skip
    if row.get("tries") and row.get("error"):  # cut off by the model server last time
        st.caption(f":material/sync_problem: Last time: {md(row['error'])}")


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


def page_reader_line(inv: dict[str, Any], key: str) -> None:
    """Under the document title: what OvisOCR2 made of this invoice, where it is in its queue, why it couldn't read
    it, or that it isn't running. Every invoice is read by it; until it has, a person approves the invoice."""
    from ap_coder.extraction import TEXT_EXTENSIONS

    path = Path(inv.get("source_path") or "")
    if path.suffix.lower() in TEXT_EXTENSIONS:  # a text file has no page to read
        return
    store = get_store()
    done = (inv.get("meta") or {}).get("page_reader")
    row = store.page_read(inv["id"]) or {}
    state = row.get("status", "")
    if done:
        st.caption(":material/visibility: " + agreement_line(done))
        figs = done.get("figures") or {}
        if figs.get("differ"):
            first = "PDF text" if ((inv.get("meta") or {}).get("capture") or {}).get("layout") == "text" else "OCR"
            pairs = "; ".join(f"{md(a)} (OvisOCR2) / {md(b)} ({first})" for a, b in figs["differ"])
            st.caption(f":material/compare_arrows: Read differently: {pairs}")
        if st.session_state.get(f"{key}_page_reader_kept"):
            line = st.container(horizontal=True, vertical_alignment="center")
            line.caption(":material/edit_note: It finished after you started editing: your edits are kept, so the "
                         "fields still show the first reading.")  # fmt: skip
            if line.button("Start over from its reading", key=f"{key}_page_reader_reset", type="tertiary",
                           icon=":material/restart_alt:"):  # fmt: skip
                forget_drafts(key)
                st.rerun()
        return
    if state in ("waiting", "reading"):
        _page_reader_waiting(inv["id"])
        return
    reason = md(row.get("error") or "no reason given").rstrip(".")
    if state == "skipped":
        st.caption(f":material/error: OvisOCR2 couldn't read it: {reason}; a person decides.")
        return
    if state == "done":  # read, but its reading wasn't applied (the invoice was edited or decided meanwhile)
        st.caption(f":material/visibility: OvisOCR2 read this invoice: {reason}.")
        return
    line = st.container(horizontal=True, vertical_alignment="center")
    if state == "failed":  # e.g. cut off by the model server MAX_TRIES times: it can be read again from here
        line.caption(f":material/error: OvisOCR2 couldn't read it: {reason}; a person decides.")
    else:  # not in its queue (processed before every invoice was read by it, or taken out of line): put it back
        stopped = _not_running(reading_status_or_none())
        line.caption(f":material/visibility_off: OvisOCR2 hasn't read this invoice yet. {stopped} {NOT_TOUCHLESS}"
                     .replace("  ", " "))  # fmt: skip
    if line.button("Add it to OvisOCR2's queue", key=f"{key}_read_pages", type="tertiary",
                   icon=":material/playlist_add:"):  # fmt: skip
        store.queue_page_read(inv["id"], "not read yet", requested_by=reviewer())
        notify("Added to OvisOCR2's queue: it reads it in the background.", ":material/visibility:")
        st.rerun()


def capture_panel(inv: dict[str, Any], capture_dict: dict[str, Any], key: str) -> None:
    path = Path(inv["source_path"])
    capture = CaptureResult.from_dict(capture_dict)
    teach = st.session_state.get(_teach_key(key))

    head, tools = st.columns([2.4, 1], vertical_alignment="center")
    head.html(document_head(inv["file_name"]))
    with tools.popover("Teach a field", icon=":material/touch_app:", width="stretch",
                       help="Show AP Coder where a field is printed: it learns it for this supplier."):  # fmt: skip
        field = st.selectbox(
            "Field", TEACHABLE, format_func=lambda f: LABELS.get(f, f), key=f"{key}_teach_pick",
            help="Then click the words on the page that hold it, and Use these words.",
        )  # fmt: skip
        if st.button("Show it on the page", type="primary", key=f"{key}_teach_go", icon=":material/ads_click:"):
            st.session_state[_teach_key(key)] = field
            st.rerun()
    page_reader_line(inv, key)

    pages = render_pages(path)
    words = None
    if teach:
        words = page_words(path) if path.suffix.lower() == ".pdf" else None
        if not words or not any(words):
            from ap_coder.capture.layout import build_layout

            layout = build_layout(path)
            words = [
                [{"t": w.text, "b": [w.box.x0, w.box.y0, w.box.x1, w.box.y1]} for w in p.words] for p in layout.pages
            ]
    line_items = [
        {"label": (li.description or "Line")[:40], "boxes": [b.to_list() for b in li.boxes]}
        for li in capture.line_items
        if li.boxes
    ]
    event = invoice_viewer(pages, _viewer_fields(capture), words=words, line_items=line_items, teach=teach,
                           height=VIEWER_HEIGHT, key=f"{key}_viewer")  # fmt: skip
    if not event or not event.get("new"):
        return
    if event["type"] == "assign" and event.get("field") in TEACHABLE:
        field = event["field"]
        value = _form_value(field, event.get("text") or "")
        if value in (None, ""):
            notify("Those words don't read as a value for that field: try again.", ":material/error:")
        else:
            st.session_state[f"{key}_{field}"] = value
            taught = dict(st.session_state.get(_taught_key(key)) or {})
            taught[field] = event.get("boxes") or []
            st.session_state[_taught_key(key)] = taught
            notify(f"{LABELS.get(field, field)} set to {md(value)}. Approving teaches it for this supplier.",
                   ":material/school:")  # fmt: skip
        st.session_state.pop(_teach_key(key), None)
        st.rerun()
    elif event["type"] == "teach_cancel":
        st.session_state.pop(_teach_key(key), None)
        st.rerun()
