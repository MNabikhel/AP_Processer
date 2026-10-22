"""The tax codes printed beside the lines, as the local reader reads them, and the offline coding that uses them
(D-64, B-701, B-702)."""

from __future__ import annotations

import json

import pytest

from ap_coder.ai.offline_coder import code_from_capture, local_extraction, read_invoice
from ap_coder.capture import analyze
from ap_coder.capture.reader import tax_flag_cell
from ap_coder.coding.line_tax import tax_code_of
from ap_coder.coding.taxmath import math_check

from .conftest import SAMPLES

RED_RIVER = SAMPLES / "redriver_MB_GST_RST_RRO-55821.pdf"
STEMS = sorted(p.stem for p in (SAMPLES / "ground_truth").glob("*.json"))


def test_the_tax_column_is_the_lines_code_not_part_of_its_description_b701():
    """B-701: Red River prints a "Tax" column ("G R", "G"): it was read into each line's description ("Delivery
    charge G R"), and the codes were lost."""
    lines = analyze(RED_RIVER).line_items
    assert [li.description for li in lines] == [
        "Height-adjustable sit-stand desk, 60 x 30 in", "Ergonomic task chair, mesh back",
        "Space planning & design consultation (hours)", "Delivery charge",
    ]  # fmt: skip
    assert [li.tax_flags for li in lines] == ["G R", "G R", "G", "G R"]
    assert lines[0].to_dict()["tax_flags"] == "G R"


@pytest.mark.parametrize(
    ("cell", "flag"),
    [("G R", True), ("GP", True), ("E", True), ("*", True), ("Exempt", True), ("HST ON", True), ("GST/PST", True),
     ("N/T", True), ("12.50", False), ("Delivery charge", False), ("EA", True), ("", False)],
)  # fmt: skip
def test_a_tax_code_cell(cell, flag):
    assert tax_flag_cell(cell) is flag


def test_offline_reading_codes_each_line_with_the_taxes_it_carries_b702(reference):
    """B-702: with no AI model every tax was put on every line, so Red River's design service (no RST) carried RST
    and the math said "1 difference: PST" on an invoice that adds up."""
    capture, _, _ = read_invoice(RED_RIVER)
    coding = code_from_capture(capture, reference, [], text=local_extraction(RED_RIVER).content).coding
    assert [tax_code_of(li) for li in coding.line_items] == ["GST+PST", "GST+PST", "GST~PST", "GST+PST"]
    assert math_check(coding, reference.tax)["ok"]


@pytest.mark.parametrize("stem", STEMS)
def test_every_demo_invoice_adds_up_as_read_with_each_lines_taxes(stem, reference):
    """The ten demo invoices, read with no AI model: the Tax check all ✓ and each line's taxes as in the ground
    truth."""
    pdf = SAMPLES / f"{stem}.pdf"
    truth = json.loads((SAMPLES / "ground_truth" / f"{stem}.json").read_text("utf-8"))
    capture, _, _ = read_invoice(pdf)
    coding = code_from_capture(capture, reference, [], text=local_extraction(pdf).content).coding
    m = math_check(coding, reference.tax)
    assert m["ok"], m["text"]
    assert [tax_code_of(li) for li in coding.line_items] == [tax_code_of(li) for li in truth["line_items"]]
