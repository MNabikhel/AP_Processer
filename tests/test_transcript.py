"""The page reader (a vision model's transcription of the page) as one more independent reader in capture."""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

from ap_coder.capture import analyze, build_layout, layout
from ap_coder.capture.bridge import review_issues
from ap_coder.capture.normalize import find_currency, same_value
from ap_coder.capture.transcript import layout_from_transcript, transcript_fields, transcript_line_items
from ap_coder.capture.types import CHECK, MISSING, VERIFIED, CaptureResult, DocLayout, FieldResult, PageLayout

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "page_reader"
OVIS = (FIXTURES / "s212-0000.md").read_text(encoding="utf-8")  # OvisOCR2's reading of the scan s212-0000.pdf
TRUTH = json.loads((FIXTURES / "s212-0000.truth.json").read_text(encoding="utf-8"))
TODAY = dt.date(2025, 7, 30)
AMOUNTS = [27.05, 336.06, 757.92, 351.28, 1010.63, 1239.24]


def _top(cands: dict, field: str):
    return cands[field][0].value if cands.get(field) else None


def _assert_truth(cands: dict) -> None:
    for field, entry in TRUTH["fields"].items():
        assert same_value(field, _top(cands, field), entry["value"]), (field, _top(cands, field), entry["value"])


# ---------------------------------------------------------------- the transcription read


def test_ovisocr2_reading_gives_every_field():
    cands = transcript_fields([OVIS])
    _assert_truth(cands)
    assert _top(cands, "payment_terms") == "Net 30"
    assert cands["invoice_number"][0].method == "label-below"  # "INVOICE #:" then "26/71677" on the next line
    assert cands["subtotal"][0].method.startswith("label-inline")  # "Total before tax $3,722.18"


def test_ovisocr2_table_gives_the_line_items():
    items = transcript_line_items([OVIS])
    assert [li.amount for li in items] == AMOUNTS
    assert [li.quantity for li in items] == [1, 2, 2, 1, 10.5, 4]
    assert [li.unit_price for li in items] == [27.05, 168.03, 378.96, 351.28, 96.25, 309.81]
    assert items[1].description.startswith("CE-54251 Hydraulic hose assembly 1/2 in.")


def _as_markdown_tables(text: str) -> str:
    """The same reading as a general model writes it: markdown tables, bold labels, a heading."""
    head, rest = text.split("<table border=1>", 1)
    table, tail = rest.split("</table>", 1)
    rows = [r.replace("<tr>", "").replace("</tr>", "").split("</td><td>") for r in table.split("</tr><tr>")]
    rows = [[c.replace("<td>", "").replace("</td>", "") for c in row] for row in rows]
    md = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * len(rows[0])]
    md += ["| " + " | ".join(row) + " |" for row in rows[1:]]
    head = head.replace("TAX INVOICE", "# TAX INVOICE").replace("INVOICE #:", "**INVOICE #:**")
    return head + "\n".join(md) + "\n" + tail


def test_markdown_tables_read_like_html_ones():
    md = _as_markdown_tables(OVIS)
    assert "<table" not in md and "| Code | Product / Service |" in md
    _assert_truth(transcript_fields([md]))
    assert [li.amount for li in transcript_line_items([md])] == AMOUNTS


def test_html_header_grid_merged_cells_and_totals_rows():
    page = (
        "Acme Supply Ltd.\n\n12 Main St, Calgary, AB T2P 1A1\n\n"
        "<table><tr><th>Invoice No.</th><th>Date</th><th>PO</th></tr>"
        "<tr><td>AC-1001</td><td>2026-09-14</td><td>PO-7781</td></tr></table>\n\n"
        "<table border=1><tr><td>Description</td><td>Qty</td><td>Price</td><td>Amount</td></tr>"
        "<tr><td rowspan=2>Toner<br>cartridges</td><td>2</td><td>300.00</td><td>600.00</td></tr>"
        "<tr><td>1</td><td>400.00</td><td>400.00</td></tr>"
        "<tr><td colspan=3>Subtotal</td><td>1,000.00</td></tr>"
        "<tr><td colspan=3>GST 5%</td><td>50.00</td></tr>"
        "<tr><td colspan=3>Total Due</td><td>1,050.00</td></tr></table>"
    )
    cands = transcript_fields([page])
    assert _top(cands, "invoice_number") == "AC-1001" and cands["invoice_number"][0].method == "label-below"
    assert _top(cands, "invoice_date") == "2026-09-14" and _top(cands, "po_number") == "PO-7781"
    assert (_top(cands, "subtotal"), _top(cands, "gst_amount"), _top(cands, "grand_total")) == (1000.0, 50.0, 1050.0)
    items = transcript_line_items([page])
    assert [li.amount for li in items] == [600.0, 400.0]  # the merged-down cell keeps the columns of the row below
    assert items[0].description == "Toner cartridges"


def test_markup_and_latex_are_read_as_printed():
    page = (
        '<think>the user wants the page</think>\n## **Acme Supply Ltd.**\n\n<img src="images/bbox_1_2_3_4.jpg" />\n\n'
        "- Invoice No: `INV_2026_01`\n\n*Terms:* Net 30\n\nSubtotal \\$1,000.00\n\nGST $5\\%$ $50.00\n\n"
        "Total &amp; due \\( \\text{CAD} \\) 1,050.00"
    )
    lines = [ln.text for ln in layout_from_transcript([page]).lines()]
    assert lines == ["Acme Supply Ltd.", "Invoice No: INV_2026_01", "Terms: Net 30", "Subtotal $1,000.00",
                     "GST 5% $50.00", "Total & due CAD 1,050.00"]  # fmt: skip
    cands = transcript_fields([page])
    assert _top(cands, "invoice_number") == "INV_2026_01" and _top(cands, "gst_amount") == 50.0
    assert _top(cands, "currency") == "CAD" and _top(cands, "grand_total") == 1050.0


def test_layout_geometry_and_a_long_page():
    doc = layout_from_transcript(["INVOICE #:\n\n26/71677", ""])
    assert doc.source == "vlm" and [p.number for p in doc.pages] == [1, 2]  # a blank page keeps its number
    label, value = doc.pages[0].lines
    assert value.box.y0 > label.box.y1 and abs(value.box.x0 - label.box.x0) < 1e-9
    assert all(w.source == "vlm" and 0 <= w.box.x0 < w.box.x1 <= 1 for w in doc.words())
    rows = "\n".join(f"| Item {i} | 1 | {i}.00 |" for i in range(1, 301))
    long = layout_from_transcript([f"Acme Supply Ltd.\n\n| Description | Qty | Amount |\n|---|---|---|\n{rows}"])
    assert len(long.pages) > 1 and all(0 <= ln.box.y1 <= 1 for ln in long.lines())
    for page in long.pages:  # rows stay apart, and each page carries the table's heading row
        cys = sorted({round(ln.box.cy, 6) for ln in page.lines})
        assert min(b - a for a, b in zip(cys, cys[1:], strict=False)) > 0.006
    items = transcript_line_items([f"| Description | Qty | Amount |\n|---|---|---|\n{rows}"])
    assert len(items) == 300 and items[-1].amount == 300.0


# ---------------------------------------------------------------- capture with the page reader, on the real scan

ocr = pytest.mark.skipif(not layout.ocr_available(), reason="local OCR not installed (pip install -e .[ocr])")


@pytest.fixture(scope="module")
def scan():
    """The scan's OCR read once (both engines), for every analyze below."""
    path = FIXTURES / "s212-0000.pdf"
    return path, build_layout(path), layout.second_read_layout(path)


def _without(doc: DocLayout | None, text: str) -> DocLayout | None:
    """The layout as if OCR had missed every line holding ``text``."""
    if doc is None:
        return None
    pages = []
    for p in doc.pages:
        lines = [ln for ln in p.lines if text not in ln.text]
        pages.append(PageLayout(p.number, p.width, p.height, [w for ln in lines for w in ln.words], lines))
    return DocLayout(pages, doc.source)


def _analyze(monkeypatch, scan, page_text=None, drop: str | None = None):
    path, first, second = scan
    if drop:
        first, second = _without(first, drop), _without(second, drop)
    monkeypatch.setattr(layout, "second_read_layout", lambda p: second)
    return analyze(path, layout=first, today=TODAY, page_text=page_text)


@ocr
def test_page_reader_agreeing_with_ocr_verifies_and_disagreeing_sends_to_check(monkeypatch, scan):
    alone = _analyze(monkeypatch, scan).fields
    both = _analyze(monkeypatch, scan, [OVIS]).fields
    assert alone["invoice_number"].value == "26/71677" and alone["invoice_number"].status != VERIFIED
    fr = both["invoice_number"]
    assert fr.status == VERIFIED and fr.boxes and "vlm" in fr.sources and "+vlm" in fr.evidence
    for field in ("invoice_date", "subtotal", "grand_total", "gst_hst_registration_number"):
        assert both[field].status == VERIFIED, (field, both[field].reasons)
    assert both["currency"].boxes  # the page reader's "CAD" found on the page
    differ = _analyze(monkeypatch, scan, [OVIS.replace("26/71677", "26/71617")]).fields["invoice_number"]
    assert differ.status == CHECK and any("page reader read 26/71617" in r for r in differ.reasons)


@ocr
def test_a_value_only_the_page_reader_has_is_kept_but_not_verified(monkeypatch, scan):
    fr = _analyze(monkeypatch, scan, [OVIS], drop="4500095595").fields["po_number"]
    assert fr.value == "4500095595" and fr.status != VERIFIED and fr.boxes == [] and set(fr.sources) == {"vlm"}


@ocr
def test_page_reader_descriptions_on_a_scan_whose_amounts_agree(monkeypatch, scan):
    items = _analyze(monkeypatch, scan, [OVIS]).line_items
    assert [li.amount for li in items] == AMOUNTS and all(li.boxes for li in items)
    assert items[0].description.startswith("Disinfectant 4 L")  # OCR's tilted row had only "ea"


# ---------------------------------------------------------------- line items from the page reader


def _pdf(tmp_path: Path, lines: list[tuple[float, float, str]], name: str = "inv.pdf") -> Path:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    for x, y, text in lines:
        page.insert_text((x, y), text, fontsize=10)
    path = tmp_path / name
    doc.save(path)
    return path


_HEAD = [(60, 60, "Acme Supply Ltd."), (60, 75, "12 Main St, Calgary, AB T2P 1A1"), (380, 120, "Invoice No: AC-1001"),
         (380, 135, "Invoice Date: 2026-09-14"), (60, 250, "Description"), (300, 250, "Qty"), (500, 250, "Amount"),
         (60, 265, "Toner"), (300, 265, "2"), (500, 265, "600.00"), (60, 280, "Paper"), (300, 280, "1"),
         (500, 280, "350.00"), (380, 500, "Subtotal"), (500, 500, "1,000.00"), (380, 515, "GST 5%"),
         (500, 515, "50.00"), (380, 530, "Total Due CAD"), (500, 530, "1,050.00")]  # fmt: skip
_READ = ("Acme Supply Ltd.\n\n12 Main St, Calgary, AB T2P 1A1\n\nInvoice No: AC-1001\n\nInvoice Date: 2026-09-14\n\n"
         "| Description | Qty | Amount |\n|---|---|---|\n| Toner | 2 | 600.00 |\n| Paper | 1 | 350.00 |\n"
         "| Cleaning kit | 1 | 50.00 |\n\nSubtotal 1,000.00\n\nGST 5% 50.00\n\nTotal Due CAD 1,050.00")  # fmt: skip


def test_page_reader_lines_are_used_when_they_add_up_and_the_pages_do_not(tmp_path):
    # The third row's amount sits outside the amount column: the page's own reading misses it (950 of 1,000).
    pdf = _pdf(tmp_path, [*_HEAD, (60, 295, "Cleaning kit"), (200, 295, "50.00")])
    alone = analyze(pdf, ocr=False, today=TODAY)
    assert [li.amount for li in alone.line_items] == [600.0, 350.0]
    got = analyze(pdf, ocr=False, today=TODAY, page_text=[_READ])
    assert [(li.description, li.amount) for li in got.line_items] == [("Toner", 600.0), ("Paper", 350.0),
                                                                      ("Cleaning kit", 50.0)]  # fmt: skip
    assert all(li.boxes for li in got.line_items)
    kit = got.line_items[2].boxes[0]
    assert abs(kit.cy - (295 - 4) / 792) < 0.02  # its own row, not the GST's 50.00 further down
    assert any(c["code"] == "LINES_ADD_UP" and c["ok"] for c in got.checks)
    # Both add up on a text PDF: the text layer's own lines stay.
    full = _pdf(tmp_path, [*_HEAD, (60, 295, "Cleaning kit"), (300, 295, "1"), (500, 295, "50.00")], "full.pdf")
    kept = analyze(full, ocr=False, today=TODAY, page_text=[_READ.replace("Cleaning kit", "Kit")])
    assert [li.description for li in kept.line_items] == ["Toner", "Paper", "Cleaning kit"]


# ---------------------------------------------------------------- local calibration


def test_local_counts_join_the_benchmark_once_there_are_enough(monkeypatch):
    from ap_coder.capture import confidence

    key = "invoice_number|rules+vlm|label-below|scan|"
    monkeypatch.setattr(confidence, "calibration_table", lambda: {key: (30, 30), "big": (500, 500)})
    assert confidence.calibrate(0.5, key) == 0.5 == confidence.calibrate(0.5, key, {})  # as before
    assert confidence.calibrate(0.5, key, {key: (5, 5)}) == 0.5  # 35 cases together: not enough yet
    assert confidence.calibrate(0.5, key, {key: (10, 9)}) == round(confidence.wilson_lower(39, 40), 4)
    assert confidence.calibrate(0.5, "new", {"new": (60, 60)}) == round(confidence.wilson_lower(60, 60), 4)
    assert confidence.calibrate(0.5, "big") == round(confidence.wilson_lower(500, 500), 4)
    assert confidence.calibrate(0.5, "big", {"big": (20, 10)}) == round(confidence.wilson_lower(510, 520), 4)
    assert confidence.calibrate(0.5, key, {key: ("x", None)}) == 0.5  # a bad count is ignored


def test_local_evidence_reaches_the_confidence(tmp_path):
    pdf = _pdf(tmp_path, _HEAD)
    plain = analyze(pdf, ocr=False, today=TODAY).fields["invoice_number"]
    local = {plain.evidence: (400, 200)}  # AP corrected this pattern half the time here
    lowered = analyze(pdf, ocr=False, today=TODAY, local_evidence=local).fields["invoice_number"]
    assert lowered.confidence < plain.confidence - 0.1 and lowered.status == CHECK


def test_read_invoice_and_capture_invoice_pass_the_page_text_and_local_counts(monkeypatch):
    from ap_coder import offline_coder
    from ap_coder.capture import workflow

    from .conftest import SAMPLE_STEM, SAMPLES

    calls = []
    real = analyze

    def spy(path, **kwargs):
        calls.append(kwargs)
        return real(path, **{**kwargs, "ocr": False})

    class Store:
        def evidence_counts(self):
            return {"x|rules|label|text|": (50, 50)}

        def supplier_key_for(self, name, gst):
            return "acme"

        def get_supplier_profile(self, key):
            return {"template": None}

        def get_vendor(self, key):
            return {"in_master": True, "display_name": "Northwind"}

    monkeypatch.setattr(offline_coder, "analyze", spy)
    pdf = SAMPLES / f"{SAMPLE_STEM}.pdf"
    offline_coder.read_invoice(pdf, Store(), layout=build_layout(pdf, ocr=False), page_text=["Total 1.00"])
    assert len(calls) == 2  # the first read, then with the vendor master record
    assert all(c["page_text"] == ["Total 1.00"] and c["local_evidence"] == Store().evidence_counts() for c in calls)
    calls.clear()
    monkeypatch.setattr(workflow, "analyze", spy)
    workflow.capture_invoice(pdf, {"vendor_name": "Northwind"}, store=Store())
    assert calls[0]["local_evidence"] == Store().evidence_counts()
    assert workflow.local_evidence(object()) is None  # a store that does not count yet


# ---------------------------------------------------------------- currency


def _capture(currency: FieldResult) -> CaptureResult:
    return CaptureResult(fields={"currency": currency})


def test_a_currency_the_page_does_not_show_is_flagged():
    missing = FieldResult("currency", None, 0.0, MISSING)
    ai_only = FieldResult("currency", "CAD", 0.36, CHECK, [], {"ai": "CAD"})
    read = FieldResult("currency", "USD", 0.97, "likely", [], {"rules": "USD"})
    codes = {name: [i[1] for i in review_issues(_capture(c))] for name, c in
             (("missing", missing), ("ai", ai_only), ("read", read))}  # fmt: skip
    assert "CURRENCY_NOT_FOUND" in codes["missing"] and "CURRENCY_NOT_FOUND" in codes["ai"]
    assert "CURRENCY_NOT_FOUND" not in codes["read"]
    # Taken as CAD (how most Canadian invoices print): worth knowing. Another currency assumed: a warning.
    assert ("info", "CURRENCY_NOT_FOUND") == review_issues(_capture(missing))[-1][:2]
    usd = FieldResult("currency", "USD", 0.36, CHECK, [], {"ai": "USD"})
    assert ("warning", "CURRENCY_NOT_FOUND") == review_issues(_capture(usd))[-1][:2]
    from ap_coder.help import help_for

    assert help_for("CURRENCY_NOT_FOUND").title == "Currency not printed"
    assert find_currency("Total 1.234,56 €") == "EUR" and find_currency("Total £12.00") == "GBP"
    assert find_currency("Total $12.00") is None


@pytest.mark.parametrize(("address", "currency"), [("12 Main St, Calgary, AB T2P 1A1", "CAD"),
                                                   ("500 Main St, Cheyenne, WY 82001", "USD"),
                                                   ("Hauptstrasse 5, 10115 Berlin", "CAD")])  # fmt: skip
def test_offline_coding_assumes_a_currency_by_the_suppliers_address(tmp_path, reference, address, currency):
    from ap_coder.offline_coder import code_from_capture

    lines = [(60, 60, "Acme Supply Ltd."), (60, 75, address), (380, 120, "Invoice No: AC-1001"),
             (380, 135, "Invoice Date: 2026-09-14"), (380, 500, "Subtotal"), (500, 500, "$1,000.00"),
             (380, 530, "Total Due"), (500, 530, "$1,000.00")]  # fmt: skip
    pdf = _pdf(tmp_path, lines)
    capture = analyze(pdf, ocr=False, today=TODAY)
    assert capture.fields["currency"].status == MISSING
    coding = code_from_capture(capture, reference, [], text=build_layout(pdf, ocr=False).text()).coding
    assert coding.currency == currency
    assert "CURRENCY_NOT_FOUND" in [code for _, code, _ in review_issues(capture)]
