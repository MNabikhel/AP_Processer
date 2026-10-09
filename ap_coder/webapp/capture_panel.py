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
from ap_coder.webapp.common import esc, notify
from ap_coder.webapp.viewer import invoice_viewer, page_words, render_pages

VIEWER_HEIGHT = 760  # px; the page scrolls inside, the field list stays beside it
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
