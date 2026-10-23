"""The shadow pilot's score (D-92): AP Coder's approvals against a JD Edwards export of what AP posted.

End to end on made-up invoices: the benchmark's generator makes three suppliers' invoices, the app's pipeline reads
them (no AI model) into a temporary store, AP approves the true values, and a fake "posted in JD Edwards" export
(F0411 + F0911) is built from the generator's truth with a few deliberate differences. The report must find exactly
those differences."""

from __future__ import annotations

import csv
import datetime as dt
import json
from dataclasses import replace
from pathlib import Path

import pytest

from ap_coder.ai.pipeline import InvoicePipeline, finalise_coding
from ap_coder.bench.coding import COST_CENTER, ITEM_GL
from ap_coder.bench.generator import make_stream_case
from ap_coder.bench.speed import _reference
from ap_coder.core.config import Settings
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import Store
from ap_coder.erp.jde import julian
from ap_coder.ops import pilot_score

SEED, SUPPLIERS, PER_SUPPLIER = 5, (2, 3, 7), 3
AN8 = {2: "1002", 3: "1003", 7: "1007"}
REVIEWER = "Pat Tester"


CHARGES = {"5100": "CC600"}  # freight lines ("Shipping & Handling") go to Freight Inbound


def _truth_gl(description: str) -> str:
    return ITEM_GL.get(description, "5100")


def _cc(gl: str) -> str:
    return COST_CENTER.get(gl) or CHARGES[gl]


def _approve(store: Store, settings: Settings, reference, invoice_id: int, truth: dict) -> dict:
    """AP approves the true header and the true account of every line, as the review screen would save it."""
    inv = store.get_invoice(invoice_id)
    final = json.loads(json.dumps(inv["ai_output"]))
    fields = {k: v["value"] for k, v in truth["fields"].items()}
    for name in ("vendor_name", "invoice_number", "invoice_date", "due_date", "subtotal", "tax_total", "grand_total"):
        if name in fields:
            final[name] = fields[name]
    lines = truth["line_items"]
    assert [round(li["amount"], 2) for li in final["line_items"]] == [round(t["amount"], 2) for t in lines]
    for li, t in zip(final["line_items"], lines, strict=True):
        gl = _truth_gl(t["description"])
        li["predicted_gl_code"], li["predicted_cost_center"] = gl, _cc(gl)
    final.pop("gl_distribution", None)
    output, _ = finalise_coding(InvoiceCoding.model_validate(final), reference, settings, store=store,
                                exclude_invoice_id=invoice_id)  # fmt: skip
    store.approve_invoice(invoice_id, output, REVIEWER)
    return output


@pytest.fixture(scope="module")
def pilot(tmp_path_factory):
    """(store, [(supplier, index, invoice id, truth)]): the invoices processed and approved."""
    tmp = tmp_path_factory.mktemp("pilot")
    mp = pytest.MonkeyPatch()
    mp.setenv("AP_PRIVATE_DIR", str(tmp / "data"))
    try:
        store = Store(tmp / "data" / "ap_coder.db")
        reference = _reference()
        settings = Settings()
        settings = replace(settings, llm=replace(settings.llm, provider="off"))
        cases = {(s, i): make_stream_case(tmp / "cases", SEED, s, i) for s in SUPPLIERS for i in range(PER_SUPPLIER)}
        store.import_vendor_master([
            {"vendor_name": cases[(s, 0)].truth["fields"]["vendor_name"]["value"], "erp_id": AN8[s], "status": "active"}
            for s in SUPPLIERS
        ])  # fmt: skip
        pipeline = InvoicePipeline(settings, reference, store=store)
        done = []
        for i in range(PER_SUPPLIER):  # the suppliers' invoices arrive interleaved
            for s in SUPPLIERS:
                case = cases[(s, i)]
                result = pipeline.process(case.path)
                assert result.error is None and result.invoice_id
                _approve(store, settings, reference, result.invoice_id, case.truth)
                done.append((s, i, result.invoice_id, case.truth))
        yield store, done
    finally:
        mp.undo()


def _posted(done, tmp: Path) -> tuple[Path, Path]:
    """The F0411 and F0911 exports AP's own entry would give, from the truth, with the deliberate differences."""
    vouchers, lines = [], []
    doc = 50000
    for s, i, _id, truth in done:
        if (s, i) == (2, 1):
            continue  # 6. not entered in JD Edwards (yet)
        f = {k: v["value"] for k, v in truth["fields"].items()}
        doc += 1
        number = f["invoice_number"]
        if (s, i) == (3, 1):
            number = "14-34730"  # 1. AP typed the invoice number differently
        gross = f["grand_total"] + (10.0 if (s, i) == (7, 0) else 0.0)  # 3. posted for 10.00 more
        tax = sum(f.get(k, 0.0) for k in ("gst_amount", "hst_amount", "pst_amount", "qst_amount"))
        vouchers.append({"RPDOC": doc, "RPDCT": "PV", "RPKCO": "00001", "RPSFX": "001", "RPAN8": AN8[s],
                         "RPVINV": number, "RPDIVJ": julian(f["invoice_date"]),
                         "RPAG": f"{gross:.2f}", "RPSTAM": f"{tax:.2f}", "RPCRCD": f.get("currency") or "CAD",
                         "RPDDJ": julian(f["due_date"]) if f.get("due_date") else "",
                         "RPTXA1": "", "RPEXR1": ""})  # fmt: skip
        for n, t in enumerate(truth["line_items"], 1):
            gl = _truth_gl(t["description"])
            bu = _cc(gl)
            if (s, i, n) == (2, 2, 2):
                gl = "6010"  # 2. AP coded the second line elsewhere
            if (s, i, n) == (7, 2, 1):
                bu = "CC100"  # 4. same account, another cost center
            lines.append({"GLDOC": doc, "GLDCT": "PV", "GLKCO": "00001", "GLLT": "AA", "GLANI": f"{bu}.{gl}",
                          "GLAA": f"{t['amount']:.2f}", "GLEXA": t["description"][:30]})  # fmt: skip
        lines.append({"GLDOC": doc, "GLDCT": "PV", "GLKCO": "00001", "GLLT": "AA", "GLANI": "CC100.2100",
                      "GLAA": f"{-gross:.2f}", "GLEXA": "AP trade"})  # the offset: not coding  # fmt: skip
    # 5. a voucher AP Coder never saw (in the pilot's dates)
    vouchers.append({"RPDOC": 59999, "RPDCT": "PV", "RPKCO": "00001", "RPSFX": "001", "RPAN8": "1099",
                     "RPVINV": "X-1", "RPDIVJ": vouchers[0]["RPDIVJ"], "RPAG": "99.00", "RPSTAM": "0",
                     "RPCRCD": "CAD", "RPDDJ": "", "RPTXA1": "", "RPEXR1": ""})  # fmt: skip
    paths = []
    for name, rows in (("F0411.csv", vouchers), ("F0911.csv", lines)):
        path = tmp / name
        with path.open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
        paths.append(path)
    return paths[0], paths[1]


def test_the_report_finds_exactly_the_differences_put_in(pilot, tmp_path):
    store, done = pilot
    ids = {(s, i): inv_id for s, i, inv_id, _ in done}
    f0411, f0911 = _posted(done, tmp_path)
    report, folder = pilot_score.run(store, f0411, f0911, tmp_path / "out")

    found = sorted((d["kind"], d["field"], d["ap_coder_id"]) for d in report["differences"])
    assert found == sorted([
        ("header", "Invoice number", ids[(3, 1)]),
        ("line", "GL account", ids[(2, 2)]),
        ("header", "Gross amount", ids[(7, 0)]),
        ("line", "Cost center (business unit)", ids[(7, 2)]),
        ("not in AP Coder", "Voucher", ""),
        ("not in JD Edwards", "Invoice", ids[(2, 1)]),
    ])  # fmt: skip
    inv = report["invoices"]
    assert (inv["ap_coder"], inv["jd_edwards"], inv["matched"], inv["matched_by_amount"]) == (9, 9, 8, 1)
    assert report["header"]["grand_total"] == {"n": 8, "right": 7}
    assert report["header"]["invoice_number"] == {"n": 8, "right": 7}
    assert report["header"]["tax_total"]["right"] == report["header"]["tax_total"]["n"] == 8
    c = report["coding"]
    assert c["compared"] == c["lines"] and c["gl_right"] == c["compared"] - 1 and c["cc_right"] == c["cc_n"] - 1
    assert c["not_posted"] == c["posted_only"] == 0  # the AP trade offset is no coding line

    md = (folder / pilot_score.REPORT).read_text(encoding="utf-8")
    assert "## Header fields" in md and "| Invoice number | 87.5% (7/8) |" in md
    assert "## By supplier" in md and "## Touchless" in md and "## Not matched" in md and "X-1" in md
    rows = list(csv.DictReader((folder / pilot_score.DIFFERENCES).read_text(encoding="utf-8-sig").splitlines()))
    assert len(rows) == 6 and {r["field"] for r in rows} >= {"GL account", "Invoice number"}
    gl = next(r for r in rows if r["field"] == "GL account")
    assert gl["jd_edwards"].endswith(".6010") and gl["ap_coder"].startswith("CC")
    assert json.loads((folder / pilot_score.FIGURES).read_text())["matched"] == 8


def test_by_supplier_the_first_invoice_is_kept_apart_from_the_later_ones(pilot, tmp_path):
    store, done = pilot
    report, _ = pilot_score.run(store, *_posted(done, tmp_path), tmp_path / "out")
    suppliers = {s["an8"]: s for s in report["suppliers"]}
    assert set(suppliers) == {"1002", "1003", "1007"}
    assert suppliers["1003"]["first"]["invoices"] == 1 and suppliers["1003"]["later"]["invoices"] == 2
    assert suppliers["1002"]["later"]["invoices"] == 1  # its second invoice was never posted
    # With no AI model and no history, nothing is proposed on the very first invoice; later ones learn from approvals.
    assert suppliers["1002"]["first"]["proposed_right"] == 0
    first = sum(s["first"]["proposed_right"] for s in report["suppliers"])
    later = sum(s["later"]["proposed_right"] for s in report["suppliers"])
    assert later > first


def test_touchless_suppliers_and_how_many_would_have_been_wrong(pilot, tmp_path):
    store, done = pilot
    ids = {(s, i): inv_id for s, i, inv_id, _ in done}
    with store._conn() as conn:  # supplier 3 met the touchless bar from its second invoice on (touchless off)
        for i in (1, 2):
            meta = store.get_invoice(ids[(3, i)])["meta"] or {}
            meta["capture"] = {**(meta.get("capture") or {}), "state": "ready"}
            conn.execute("UPDATE invoices SET meta = ? WHERE id = ?", (json.dumps(meta), ids[(3, i)]))
    try:
        report, folder = pilot_score.run(store, *_posted(done, tmp_path), tmp_path / "out")
    finally:
        with store._conn() as conn:
            for i in (1, 2):
                meta = store.get_invoice(ids[(3, i)])["meta"]
                meta["capture"].pop("state")
                conn.execute("UPDATE invoices SET meta = ? WHERE id = ?", (json.dumps(meta), ids[(3, i)]))
    t = report["touchless"]
    assert t["invoices"] == 2 and [s["supplier"] for s in t["suppliers"]] == [
        done[1][3]["fields"]["vendor_name"]["value"]
    ]
    assert ids[(3, 1)] in t["suppliers"][0]["ids"]  # its invoice number was posted differently
    assert "would have gone to JD Edwards with something AP posted differently" in (
        folder / pilot_score.REPORT).read_text(encoding="utf-8")  # fmt: skip


def test_the_export_may_use_plain_english_columns_dates_and_implied_decimals(tmp_path):
    rows = [{"Address Number": "001003", "Supplier Invoice Number": "INV-0042", "Invoice Date": "126282",
             "Gross Amount": "193336", "Tax Amount": "25181", "Pay Item": "001", "Document Number": "7"},
            {"Address Number": "001003", "Supplier Invoice Number": "INV-0042", "Invoice Date": "126282",
             "Gross Amount": "1000", "Tax Amount": "0", "Pay Item": "002", "Document Number": "7"}]  # fmt: skip
    posted, notes = pilot_score.posted_vouchers(rows, implied_decimals=True)
    assert len(posted) == 1 and notes == []
    p = posted[0]
    assert (p.an8, p.invoice_date, p.gross, p.tax) == ("1003", "2026-10-09", 1943.36, 251.81)
    assert pilot_score.split_account("      CC400.6010.01") == ("CC400", "6010", "01")
    with pytest.raises(pilot_score.ExportError, match="needs these columns"):
        pilot_score.posted_vouchers([{"Vendor": "x", "Amount": "1"}])


def test_the_command_writes_the_report_and_says_where(pilot, tmp_path, capsys):
    from ap_coder.ops import cli

    store, done = pilot
    f0411, f0911 = _posted(done, tmp_path)
    out = tmp_path / "cli"
    code = cli.main(["--db", str(store.path), "pilot-score", "--vouchers", str(f0411), "--distribution", str(f0911),
                     "--out", str(out)])  # fmt: skip
    said = capsys.readouterr().out
    assert code == 0 and (out / pilot_score.REPORT).exists() and (out / pilot_score.DIFFERENCES).exists()
    assert "8 of 9" in said and str(out / pilot_score.REPORT) in said


def test_julian_dates_read_back():
    assert pilot_score.julian_or_date("126282") == "2026-10-09" == pilot_score.julian_or_date(str(julian("2026-10-09")))
    assert pilot_score.julian_or_date("2026-10-09") == "2026-10-09"
    assert pilot_score.julian_or_date("") == ""
    assert dt.date.fromisoformat(pilot_score.julian_or_date("99365")) == dt.date(1999, 12, 31)


def test_insights_shows_the_last_score(pilot, tmp_path):
    from ap_coder.web.pages.insights import pilot_card

    from .webclient import client_for

    store, done = pilot
    pilot_score.run(store, *_posted(done, tmp_path))  # into the data folder, where Insights looks
    card = pilot_card(store)
    assert ("Invoices matched in JD Edwards", "8 of 9") in card["rows"]
    page = client_for(store).get("/insights").text
    assert "Shadow pilot: AP Coder against JD Edwards" in page and "8 of 9" in page
