"""Try the invoice page viewer on a sample invoice: ``streamlit run scripts/viewer_demo.py``.

A development page, not part of the dashboard. The field boxes are made by hand here (the words found with
PyMuPDF), with a mix of statuses so every colour shows; the events the viewer sends are printed below it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ap_coder.webapp.viewer import FIELD_LABELS, invoice_viewer, page_words, render_pages  # noqa: E402

SAMPLE = ROOT / "samples" / "northwind_ON_HST_NW-2026-0912.pdf"


def find(words: list[list[dict]], page: int, phrase: str, last: bool = False) -> list[float]:
    """The box round ``phrase`` (consecutive words) on ``page``, as ``[page, x0, y0, x1, y1]``."""
    want = phrase.split()
    row = words[page - 1]
    starts = range(len(row) - len(want) + 1)
    for i in reversed(starts) if last else starts:
        if [w["t"] for w in row[i : i + len(want)]] == want:
            bs = [w["b"] for w in row[i : i + len(want)]]
            return [page, min(b[0] for b in bs), min(b[1] for b in bs), max(b[2] for b in bs), max(b[3] for b in bs)]
    raise LookupError(f"{phrase!r} is not on page {page}")


def row_box(words: list[list[dict]], page: int, first: str, last: str) -> list[float]:
    a, b = find(words, page, first), find(words, page, last, last=True)
    return [page, min(a[1], b[1]), min(a[2], b[2]), max(a[3], b[3]), max(a[4], b[4])]


def demo_fields(words: list[list[dict]]) -> list[dict]:
    def f(name, value, display, status, conf, phrase=None, page=1, sources=None, reasons=None, failed=False):
        return {
            "field": name, "value": value, "label": FIELD_LABELS[name], "display": display, "status": status,
            "confidence": conf, "boxes": [find(words, page, phrase)] if phrase else [],
            "sources": sources or {}, "reasons": reasons or [], "failed": failed,
        }  # fmt: skip

    agree3 = ["3 readers agree"]
    vendor = "Northwind IT Solutions Inc."
    return [
        f("vendor_name", vendor, vendor, "verified", 0.997, vendor,
          sources=dict.fromkeys(("text", "template", "ai"), vendor),
          reasons=[*agree3, "Matches the vendor master"]),
        f("invoice_number", "NW-2026-0912", "NW-2026-0912", "verified", 0.999, "NW-2026-0912",
          sources={"rules": "NW-2026-0912", "template": "NW-2026-0912", "ai": "NW-2026-0912"}, reasons=agree3),
        f("invoice_date", "2026-09-14", "Sep 14, 2026", "verified", 0.985, "September 14, 2026",
          sources={"rules": "2026-09-14", "ai": "2026-09-14"}, reasons=["2 readers agree", "Date is plausible"]),
        f("due_date", "2026-10-14", "Oct 14, 2026", "likely", 0.88, "October 14, 2026",
          sources={"rules": "2026-10-14"},
          reasons=["One reader found it", "Agrees with Net 30 from the invoice date"]),
        f("po_number", "PO-88213", "PO-88213", "check", 0.61, "PO-88213",
          sources={"rules": "PO-88213", "ai": "PO-88218"},
          reasons=["2 readers disagree", "PO-88213 is open for this vendor"]),
        f("gst_hst_registration_number", "123456789RT0001", "123456789 RT0001", "verified", 0.995, "123456789 RT0001",
          sources={"rules": "123456789RT0001", "vendor_master": "123456789RT0001"},
          reasons=["Check digit is right", "Vendor master agrees"]),
        f("qst_registration_number", None, "", "missing", 0.0, reasons=["Not on the invoice (Ontario vendor)"]),
        f("subtotal", 15945.0, "15,945.00", "verified", 0.996, "$15,945.00", page=2,
          sources={"text": 15945.0, "di": 15945.0, "ai": 15945.0}, reasons=agree3),
        f("hst_amount", 2072.85, "2,072.85", "likely", 0.93, "$2,072.85", page=2,
          sources={"text": 2072.85, "ai": 2072.85}, reasons=["13% of the subtotal, the Ontario rate"]),
        f("grand_total", 18017.85, "18,017.85", "check", 0.42, "$18,017.85", page=2,
          sources={"text": 18017.85, "ai": 18071.85}, failed=True,
          reasons=["Failed check: the AI's total does not add up (15,945.00 + 2,072.85 = 18,017.85)"]),
        f("currency", "CAD", "CAD", "likely", 0.9, "(CAD)", page=2, sources={"rules": "CAD"},
          reasons=["Printed next to the total"]),
        f("payment_terms", "Net 30", "Net 30", "verified", 0.97, "Net 30.", page=2,
          sources={"rules": "Net 30", "template": "Net 30"}, reasons=["2 readers agree"]),
    ]  # fmt: skip


def demo_lines(words: list[list[dict]]) -> list[dict]:
    return [
        {"label": "Dell Latitude 7450 laptop, 32GB RAM", "boxes": [row_box(words, 1, "Dell Latitude", "4,350.00")]},
        {"label": "Dell PowerEdge R760 rack server", "boxes": [row_box(words, 1, "Dell PowerEdge", "8,900.00")]},
        {"label": "Microsoft 365 E3 licences", "boxes": [row_box(words, 1, "Microsoft 365", "800.00")]},
        {"label": "Implementation consulting", "boxes": [row_box(words, 2, "Implementation consulting", "1,800.00")]},
    ]


st.set_page_config(page_title="Invoice viewer demo", layout="wide")
st.markdown("### Invoice viewer demo")
st.caption(f"{SAMPLE.name} · fields made by hand to show every status. Click a field, press **n**, or teach one.")

words = page_words(SAMPLE)
pages = render_pages(SAMPLE)
state = st.session_state
state.setdefault("fields", demo_fields(words))
state.setdefault("teach", None)
state.setdefault("selected", None)
state.setdefault("log", [])

with st.sidebar:
    fixed = st.toggle("Fixed height (760 px)", value=False)
    names = [f["field"] for f in state.fields]
    start = names.index("qst_registration_number")
    pick = st.selectbox("Teach a field", names, format_func=lambda n: FIELD_LABELS.get(n, n), index=start)
    if st.button("Teach it", type="primary", use_container_width=True):
        state.teach = pick

event = invoice_viewer(pages, state.fields, words=words, line_items=demo_lines(words), selected=state.selected,
                       teach=state.teach, height=760 if fixed else None, key="demo_viewer")  # fmt: skip

if event and event["new"]:
    state.log.insert(0, event)
    if event["type"] == "select":
        state.selected = event["field"]
    elif event["type"] == "assign":
        for f in state.fields:
            if f["field"] == event["field"]:
                f.update(value=event["text"], display=event["text"], status="verified", confidence=1.0,
                         boxes=event["boxes"], sources={"you": event["text"]}, reasons=["Taught by you"],
                         failed=False)  # fmt: skip
        state.teach = None
        state.selected = event["field"]
        st.rerun()
    elif event["type"] == "teach_cancel":
        state.teach = None
        st.rerun()

st.markdown("**Last event**")
st.write(event)
with st.expander(f"All events ({len(state.log)})"):
    st.json(state.log)
