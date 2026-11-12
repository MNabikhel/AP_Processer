"""No Azure and no AI model: invoices are read on this computer and coded from what AP approved before."""

import json
from dataclasses import replace

from ap_coder.ai.pipeline import InvoicePipeline
from ap_coder.core.config import Settings
from ap_coder.core.reference_data import UNASSIGNED
from ap_coder.core.store import Store
from tests.conftest import SAMPLES

STEM = "harbourview_NS_HST_HPS-2026-0347"


def test_a_tax_above_the_provinces_rate_is_not_a_partial_base():
    """14% HST on $1,000 while the province was read as Ontario (13%) is Nova Scotia's rate on the whole
    subtotal, not 13% on a $1,076.92 base: the rate the amounts show is kept, with the province it names (the
    tax check then flags the place of supply that differs). Less than the rate is still a partial base."""
    import datetime as dt

    from ap_coder.ai.offline_coder import _tax_lines

    on = dt.date(2026, 4, 3)
    (hst,) = _tax_lines({"hst_amount": 140.0}, 1000.0, "ON", on)
    assert (hst.rate, hst.province, hst.taxable_amount, hst.tax_amount) == (0.14, "NS", 1000.0, 140.0)
    (rst,) = _tax_lines({"pst_amount": 63.0}, 1000.0, "MB", on)  # delivery exempt from Manitoba RST
    assert (rst.rate, rst.province, rst.taxable_amount) == (0.07, "MB", 900.0)


def _offline_settings():
    s = Settings()
    return replace(s, llm=replace(s.llm, provider="off"))


def test_a_pdf_is_read_and_coded_with_no_azure_and_no_model(tmp_path, reference):
    store = Store(tmp_path / "ap.db")
    result = InvoicePipeline(_offline_settings(), reference, store=store).process(SAMPLES / f"{STEM}.pdf")

    assert result.ok, result.error
    truth = json.loads((SAMPLES / "ground_truth" / f"{STEM}.json").read_text(encoding="utf-8"))
    coding = result.coding.coding
    assert result.coding.model.startswith("local reader")
    assert result.extraction.model_id.startswith("local-")
    assert coding.invoice_number == truth["invoice_number"]
    assert abs(coding.grand_total - truth["grand_total"]) < 0.01
    assert result.capture is not None and result.invoice_id
    assert len(coding.line_items) == len(truth["line_items"])
    # Nothing approved yet: an account comes only from the chart's own names, with its reason, else none.
    codes = set(reference.chart_of_accounts.codes)
    assert all(li.predicted_gl_code in codes | {UNASSIGNED} and li.reasoning_justification for li in coding.line_items)
    # A person looks at it before it goes out: no cost center yet (a warning), or a low confidence.
    blocking = [i for i in result.report.issues if i.severity in ("error", "warning")]
    assert result.report.requires_review or blocking


def test_approved_coding_is_reused_for_the_next_invoice_from_that_vendor(tmp_path, reference):
    store = Store(tmp_path / "ap.db")
    pipe = InvoicePipeline(_offline_settings(), reference, store=store)
    first = pipe.process(SAMPLES / f"{STEM}.pdf")
    truth = json.loads((SAMPLES / "ground_truth" / f"{STEM}.json").read_text(encoding="utf-8"))
    final = dict(first.output)
    final["line_items"] = [
        {**li, "predicted_gl_code": t["predicted_gl_code"]}
        for li, t in zip(first.output["line_items"], truth["line_items"] * 10, strict=False)
    ]
    store.approve_invoice(first.invoice_id, final, "Pat")

    again = pipe.process(SAMPLES / f"{STEM}.pdf")
    assert again.ok, again.error
    assert [li.predicted_gl_code for li in again.coding.coding.line_items] == [
        li["predicted_gl_code"] for li in final["line_items"]
    ]


def test_every_sample_reads_right_offline(tmp_path, reference):
    """All ten samples, no Azure and no model: header, lines, provinces and tax lines as the ground truth;
    nothing is wrong, only accounts left to pick."""
    pipe = InvoicePipeline(_offline_settings(), reference, store=Store(tmp_path / "ap.db"))
    for pdf in sorted(SAMPLES.glob("*.pdf")):
        truth = json.loads((SAMPLES / "ground_truth" / f"{pdf.stem}.json").read_text(encoding="utf-8"))
        result = pipe.process(pdf)
        assert result.ok, (pdf.name, result.error)
        got = result.output
        for field in ("vendor_name", "invoice_number", "invoice_date", "supplier_province", "ship_to_province"):
            assert got[field] == truth[field], (pdf.name, field)
        for field in ("subtotal", "tax_total", "grand_total"):
            assert abs(got[field] - truth[field]) < 0.011, (pdf.name, field)
        assert [round(li["amount"], 2) for li in got["line_items"]] == [li["amount"] for li in truth["line_items"]]
        taxes = [(t["tax_type"], t["province"], t["rate"]) for t in got["tax_lines"]]
        assert taxes == [(t["tax_type"], t["province"], t["rate"]) for t in truth["tax_lines"]], pdf.name
        assert not [i for i in result.report.issues if i.severity == "error"], pdf.name


def test_queue_card_confidence_follows_the_review_screen(tmp_path, reference):
    from ap_coder.core.store import REVIEW

    store = Store(tmp_path / "ap.db")
    result = InvoicePipeline(_offline_settings(), reference, store=store).process(SAMPLES / f"{STEM}.pdf")
    assert store.get_invoice(result.invoice_id)["status"] == REVIEW
    assert store.refresh_confidence(result.invoice_id, 0.42, True)
    assert store.get_invoice(result.invoice_id)["adjusted_confidence"] == 0.42
    assert not store.refresh_confidence(result.invoice_id, 0.42, True)  # unchanged: nothing written


def test_the_local_models_examples_have_the_vendor_the_local_reader_found(tmp_path, reference, monkeypatch):
    """The chat model's examples of AP's coding (``select_examples``) are picked once the local reader has the vendor:
    "Chinook Courier & Office Services Ltd." is not found in its own page by name ("&" against "and"), and its
    lessons were left out of the prompt (B-600)."""
    store = Store(tmp_path / "ap.db")
    store.import_history([{"vendor_name": "Chinook Courier & Office Services Ltd.", "description": "Pallet storage",
                           "gl_code": "6200", "cost_center": "", "amount": 10.0, "date": "2026-01-05"}])  # fmt: skip
    s = Settings()
    seen = {}
    monkeypatch.setattr(InvoicePipeline, "_local_accounts",
                        lambda self, result, history, **kw: seen.update(history=history))  # fmt: skip
    pipe = InvoicePipeline(replace(s, llm=replace(s.llm, provider="local")), reference, store=store)
    result = pipe.process(SAMPLES / "chinook_AB_GST_CCO-26-10418.pdf")

    assert result.ok, result.error
    assert result.coding.coding.vendor_name.startswith("Chinook Courier & Office")
    assert "Pallet storage" in seen["history"]
    assert result.history_examples >= 1


def test_a_province_with_the_postal_code_glued_on_is_read():
    """OCR often drops the spaces: "Steinbach,MBR6X5X0" and "Calgary,ABT7Z6L6" name Manitoba and Alberta. Ordinary
    words that start with a province's letters, or a province code with no postal code after it, are not read."""
    from ap_coder.ai.offline_coder import addresses

    assert addresses("Prairie Supply\n12 Main St\nSteinbach,MBR6X5X0")[0] == "MB"
    assert addresses("Foothills Ltd\nCalgary,ABT7Z6L6")[0] == "AB"
    assert addresses("Foothills Ltd\nCalgary,AB T7Z 6L6")[0] == "AB"
    assert addresses("Foothills Ltd\nCalgary AB,T7Z6L6")[0] == "AB"
    for text in (
        "ABOUT US\nONLINE ORDERS",
        "Order NSF1234",
        "Ref ONTARIO",
        "Code BCA1B",
        "SKU ABA1B2C3D",
        "Part NO. X4R1A3B2",
    ):
        assert addresses(text) == ("", "", ""), text


def test_the_rate_table_is_read_again_once_the_rate_file_changes(tmp_path, monkeypatch):
    """The local coder kept the first rate table it read for the life of the process: a rate edited in the data
    folder's copy was not seen until a restart (B-957)."""
    import datetime as dt
    import os
    import shutil

    from ap_coder.ai import offline_coder
    from ap_coder.coding import tax

    rates = tmp_path / "rates.csv"
    shutil.copy(tax.DEFAULT_RATES_PATH, rates)
    monkeypatch.setattr(tax, "rates_path", lambda: rates)
    on = dt.date(2026, 5, 1)
    assert offline_coder._rate_table().rate_for("HST", "ON", on) == 0.13
    rates.write_text(rates.read_text(encoding="utf-8").replace("HST,ON,0.13", "HST,ON,0.14"), encoding="utf-8")
    stamp = rates.stat().st_mtime_ns + 5_000_000_000
    os.utime(rates, ns=(stamp, stamp))
    assert offline_coder._rate_table().rate_for("HST", "ON", on) == 0.14


def test_an_hst_where_none_is_levied_is_the_hst_of_the_lines_that_make_it():
    """B-2226: GST on the Calgary lines and Ontario's HST on the Toronto line, read as "HST 3.824% of the subtotal,
    Alberta". The HST's rate is the one HST rate some of the lines make it at, in the province that rate names; an
    HST no lines make at any rate stays what the amounts show."""
    import datetime as dt

    from ap_coder.ai.offline_coder import _tax_lines

    on, amounts = dt.date(2026, 4, 3), [1000.0, 200.0, 500.0]
    gst, hst = _tax_lines({"gst_amount": 60.0, "hst_amount": 65.0}, 1700.0, "AB", on, amounts)
    assert (gst.rate, gst.taxable_amount) == (0.03529, 1700.0)  # on its lines once their codes are settled
    assert (hst.rate, hst.province, hst.taxable_amount, hst.tax_amount) == (0.13, "ON", 500.0, 65.0)
    (odd,) = _tax_lines({"hst_amount": 64.0}, 1700.0, "AB", on, amounts)
    assert (odd.rate, odd.province, odd.taxable_amount) == (0.03765, "AB", 1700.0)


def _plain_line(store, reference, number, text, vendor="Brightway Janitorial Ltd."):
    import datetime as dt
    from types import SimpleNamespace

    from ap_coder.ai.offline_coder import code_from_capture
    from ap_coder.bench.coding import _capture

    capture = _capture(vendor, number, dt.date(2026, 1, 20),
                       [SimpleNamespace(text=text, quantity=1.0, unit_price=100.0, amount=100.0)])  # fmt: skip
    return code_from_capture(capture, reference, store.feedback_rows(), store).coding


def test_the_vendors_usual_account_does_not_code_a_line_of_another_kind(tmp_path, reference):
    """B-2543: on plain lists, a vendor whose four different lines AP all coded to one account (its usual) had every
    new line coded there alone: a laptop on a cleaning company's invoice went to Cleaning. The usual account's guard
    of the chart's path (B-2228, B-2251) now holds it for a person; a line of its kind is still coded."""
    store = Store(tmp_path / "s.db")
    for n, text in enumerate(["Nightly janitorial service", "Window washing exterior", "Carpet shampoo boardroom",
                              "Waste bin liners replacement"]):  # fmt: skip
        out = _plain_line(store, reference, f"B-{n}", text).model_dump(mode="json")
        out["line_items"][0].update(predicted_gl_code="6230", predicted_cost_center="CC100")
        store.approve_invoice(store.add_invoice(tmp_path / f"{n}.pdf", out, None), out, "pat")
    line = _plain_line(store, reference, "B-9", "Dell Latitude 5540 laptop").line_items[0]
    assert line.predicted_gl_code == UNASSIGNED, f"a laptop coded alone to {line.predicted_gl_code} (cleaning)"
    assert line.reasoning_justification == (
        "This supplier's lines usually go to 6230, but this line reads like Computer Equipment (1500): check it."
    )
    assert [c["code"] for c in line.candidates[:2]] == ["1500", "6230"]
    assert _plain_line(store, reference, "B-10", "Floor stripping and waxing").line_items[0].predicted_gl_code == "6230"


def test_a_cost_center_no_longer_in_the_list_is_not_proposed(tmp_path, reference):
    """B-2545: on plain lists, a cost center taken out of the cost center list was still proposed with the line, from
    the vendor's past approvals (then CC_UNKNOWN), where a GL account taken out of the list never is."""
    from ap_coder.core.reference_data import load_reference_data
    from tests.conftest import DATA

    store = Store(tmp_path / "s.db")
    for n in range(3):
        out = _plain_line(store, reference, f"N-{n}", "Copy paper case of 10", "Northwind Supply Ltd.").model_dump(
            mode="json")  # fmt: skip
        out["line_items"][0].update(predicted_gl_code="6000", predicted_cost_center="CC200")
        store.approve_invoice(store.add_invoice(tmp_path / f"{n}.pdf", out, None), out, "pat")
    line = _plain_line(store, reference, "N-8", "Copy paper case of 10", "Northwind Supply Ltd.").line_items[0]
    assert (line.predicted_gl_code, line.predicted_cost_center) == ("6000", "CC200")
    centers = (DATA / "cost_centers.csv").read_text().splitlines()
    (tmp_path / "cc.csv").write_text("\n".join(r for r in centers if not r.startswith("CC200")) + "\n")
    retired = load_reference_data(DATA / "chart_of_accounts.csv", tmp_path / "cc.csv", DATA / "tax_gl_mapping.csv",
                                  DATA / "coding_policy.md")  # fmt: skip
    line = _plain_line(store, retired, "N-9", "Copy paper case of 10", "Northwind Supply Ltd.").line_items[0]
    assert (line.predicted_gl_code, line.predicted_cost_center) == ("6000", UNASSIGNED)
