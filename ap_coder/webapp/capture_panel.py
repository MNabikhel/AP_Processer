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

from ap_coder.capture.confidence import LABELS
from ap_coder.capture.normalize import parse_amount, parse_date
from ap_coder.capture.types import Box, CaptureResult
from ap_coder.safe import md
from ap_coder.webapp.common import esc, forget_drafts, get_settings, get_store, notify, reviewer
from ap_coder.webapp.viewer import invoice_viewer, page_words, render_pages

VIEWER_HEIGHT = 760  # px; the page scrolls inside, the field list stays beside it
PAGE_READER_POLL_SECONDS = 20  # while the page reader has the open invoice in its queue
VIEWABLE = {".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp"}

# Capture fields a reviewer can teach: they are fields of the review form (same names).
TEACHABLE = (
    "vendor_name", "invoice_number", "invoice_date", "due_date", "po_number", "gst_hst_registration_number",
    "qst_registration_number", "payment_terms", "currency", "subtotal", "tax_total", "grand_total",
)  # fmt: skip
AMOUNT_FORM_FIELDS = {"subtotal", "tax_total", "grand_total"}


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


def follow_page_reader(invoice_id: int, key: str, meta: dict[str, Any]) -> None:
    """The page reader's reading came in while this invoice was open (it reads in the background): the fields show
    it at once when the reviewer hasn't typed anything; what they typed is never changed under them (a note under the
    document offers to start over from the reading instead)."""
    at = (meta.get("page_reader") or {}).get("at", "")
    seen = st.session_state.setdefault(f"{key}_page_reader_seen", at)
    if at == seen:
        return
    if invoice_id in (st.session_state.get("unsaved_edits") or set()):
        st.session_state[f"{key}_page_reader_seen"] = at
        st.session_state[f"{key}_page_reader_kept"] = True
        return
    forget_drafts(key)  # the fields are drawn again from the new proposal
    st.session_state[f"{key}_page_reader_seen"] = at
    notify("The page reader has read this invoice: the fields show its reading now.", ":material/visibility:")
    st.rerun()


@st.fragment(run_every=PAGE_READER_POLL_SECONDS)
def _page_reader_waiting(invoice_id: int) -> None:
    """While the page reader has this invoice in its queue: where it is, looked up again every few seconds; the
    whole screen is drawn again once it is done, so the fields show its reading."""
    from ap_coder.page_worker import ready

    store = get_store()
    state = (store.page_read(invoice_id) or {}).get("status", "")
    if state not in ("waiting", "reading"):
        st.rerun()
    if state == "waiting":
        model, why = ready(get_settings(), store)
        if not model:
            st.caption(f":material/pause_circle: Page reader: waiting, not reading yet ({md(why)}).")
            return
    ahead = max(store.page_reads_waiting() - 1, 0) if state == "waiting" else 0
    note = "reading it now" if state == "reading" else f"waiting to read it ({ahead} ahead)" if ahead else "next"
    st.caption(f":material/hourglass_top: Page reader: {note}. The fields update when it is done, unless you have "
               "edited the invoice.")  # fmt: skip


def page_reader_line(inv: dict[str, Any], key: str) -> None:
    """Under the document title: what the page reader made of this invoice, where it is in its queue, or a button
    to have it read (Settings → Page reader)."""
    from ap_coder.extraction import TEXT_EXTENSIONS

    path = Path(inv.get("source_path") or "")
    settings = get_settings()
    if path.suffix.lower() in TEXT_EXTENSIONS or settings.page_reader.mode == "off":
        return
    store = get_store()
    done = (inv.get("meta") or {}).get("page_reader")
    row = store.page_read(inv["id"]) or {}
    state = row.get("status", "")
    if done:
        agree, differ, only = (len(done.get(k) or []) for k in ("agree", "differ", "only"))
        parts = [f"agrees on {agree} {'field' if agree == 1 else 'fields'}"]
        if differ:
            parts.append(f"reads {differ} differently (marked Check)")
        if only:
            parts.append(f"found {only} OCR missed")
        figs = done.get("figures") or {}
        total = max(figs.get("figures", 0), figs.get("first_figures", 0))
        if total:
            parts.append(f"{figs.get('confirmed', 0)} of {total} figures on the page read the same by both")
        minutes = (done.get("seconds") or 0) / 60
        st.caption(f":material/visibility: Page reader {md(done.get('model', ''))}: " + ", ".join(parts)
                   + (f" · {minutes:.1f} min" if minutes else ""))  # fmt: skip
        if figs.get("differ"):
            first = "PDF text" if ((inv.get("meta") or {}).get("capture") or {}).get("layout") == "text" else "OCR"
            pairs = "; ".join(f"{md(a)} (page reader) / {md(b)} ({first})" for a, b in figs["differ"])
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
    if state == "done" and row.get("error"):
        st.caption(f":material/visibility: Page reader: {md(row['error'])}")
        return
    if state == "failed":
        st.caption(f":material/error: The page reader couldn't read it: {md(row.get('error', ''))}")
    if st.button("Read with the page reader", icon=":material/visibility:", key=f"{key}_read_pages",
                 help="A vision model reads the pages as a second reader, in the background."):  # fmt: skip
        store.queue_page_read(inv["id"], "asked", requested_by=reviewer())
        notify("Queued: the page reader reads it in the background.", ":material/visibility:")
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
            notify(f"{LABELS.get(field, field)} set to {value}. Approving teaches it for this supplier.",
                   ":material/school:")  # fmt: skip
        st.session_state.pop(_teach_key(key), None)
        st.rerun()
    elif event["type"] == "teach_cancel":
        st.session_state.pop(_teach_key(key), None)
        st.rerun()
