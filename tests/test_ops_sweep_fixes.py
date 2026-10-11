"""Regression tests for bugs found sweeping the install, command line, exports and data handling. Each test
reproduces the bug as it was found, so it is not made again."""

from __future__ import annotations

import csv
import io
import json

from openpyxl import load_workbook

from ap_coder import jde, labels

from .test_jde import AN8, ON, _files, _invoice

# --- JD Edwards: dates and field widths ------------------------------------------------------------------------


def test_jde_a_date_e1_cannot_hold_is_left_out_not_a_crash():
    """An invoice dated 1899 (a typo for 1999 / 2026) passed the checks, then the batch's ZIP could not be built
    (julian() raised): the whole batch, every invoice in it, could not be downloaded."""
    settings = jde.JdeSettings()
    for changes in ({"invoice_date": "1899-05-01"}, {"due_date": "0026-05-01"}):
        inv = _invoice(ON, **changes)
        problems = jde.validate([inv], settings, AN8)
        assert problems and "JD Edwards" in " ".join(problems[1]), changes
    good = _invoice(ON, inv_id=2)
    zipped, _, _ = jde.build_zip([good], settings, AN8, batch=1)  # the others still go out
    assert zipped
    credit = _invoice(ON, inv_id=3, due_date="0026-05-01", grand_total=-10.0)  # a credit's due date isn't sent
    assert all("due date" not in p for p in jde.validate([credit], settings, AN8).get(3, []))


def test_jde_text_made_formula_safe_still_fits_its_column():
    """A description starting with "-" (a discount line) gets an apostrophe in front so a spreadsheet does not run
    it: a 30-character VNEXA became 31, and a 25-character VLVINV 26, longer than the Z-table columns."""
    inv = _invoice(ON, invoice_number="=" + "1" * 24)
    inv["final_output"]["gl_distribution"][0]["description"] = "-" + "x" * 40
    files = _files([inv])
    line = files[jde.DIST_FILE][0]
    assert line["VNEXA"].startswith("'-") and len(line["VNEXA"]) == 30
    assert len(line["VNEXR"]) <= 30
    header = files[jde.HEADER_FILE][0]
    assert header["VLVINV"].startswith("'=") and len(header["VLVINV"]) == 25


# --- Labels workbook (python -m ap_coder labels) ---------------------------------------------------------------


def _prediction(tmp_path, **changes):
    out = tmp_path / "output"
    out.mkdir()
    doc = {
        "vendor_name": "Fournitures Laval", "invoice_number": "A-1", "invoice_date": "2026-01-05", "currency": "CAD",
        "subtotal": 10, "tax_total": 0, "grand_total": 10,
        "line_items": [{"line_number": 1, "description": "Paper", "quantity": 1, "unit_price": 10, "amount": 10}],
        **changes,
    }  # fmt: skip
    (out / "inv.json").write_text(json.dumps(doc), encoding="utf-8")
    return out


def test_labels_workbook_with_a_damaged_pdfs_control_characters(tmp_path):
    """Text read from a damaged PDF can hold control characters: openpyxl refused them and the labels command
    stopped with a traceback (the export workbook already dropped them)."""
    out = _prediction(tmp_path, invoice_number="A\x0c1")
    out_doc = json.loads((out / "inv.json").read_text())
    out_doc["line_items"][0]["description"] = "Pa\x01per"
    (out / "inv.json").write_text(json.dumps(out_doc))
    assert labels.export_labels(out, tmp_path / "labels.xlsx") == 1
    wb = load_workbook(tmp_path / "labels.xlsx")
    values = [c.value for row in wb["Labels"].iter_rows(min_row=2) for c in row]
    assert "A1" in values and "Paper" in values


def test_labels_csv_is_formula_safe_and_reads_back_the_same(tmp_path):
    """The CSV labels file is opened in Excel by the AP team: a vendor name from a document such as
    =HYPERLINK(...) ran as a formula there. Neutralised with an apostrophe, and read back without it."""
    evil = '=HYPERLINK("http://evil.example","x")'
    out = _prediction(tmp_path, vendor_name=evil)
    dest = tmp_path / "labels.csv"
    labels.export_labels(out, dest)
    text = dest.read_text(encoding="utf-8-sig")
    assert '"=HYPERLINK' not in text and "\"'=HYPERLINK" in text
    rows = list(csv.DictReader(io.StringIO(text)))
    assert rows[0]["amount"] == "10"  # numbers untouched
    for row in rows:
        row["reviewed"] = "Y"
    with dest.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    docs, unreviewed = labels.load_labels(dest)
    assert not unreviewed and docs["inv"]["vendor_name"] == evil
