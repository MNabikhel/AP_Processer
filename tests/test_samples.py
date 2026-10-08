"""Every bundled sample invoice: ground truth is valid, passes the controls and matches its documents."""

import json

import pymupdf
import pytest

from ap_coder.schema import InvoiceCoding
from ap_coder.tax import build_gl_distribution
from ap_coder.validation import validate_coding

from .conftest import SAMPLES

GROUND_TRUTH = sorted((SAMPLES / "ground_truth").glob("*.json"))
STEMS = [p.stem for p in GROUND_TRUTH]

# Warnings each sample is expected to raise with the bundled reference data (none unless listed).
EXPECTED_WARNINGS = {
    "cascade_US_SalesTax_INV-30981": {"TAX_NON_CANADIAN"},  # US sales tax: not recoverable, flagged for review
}


def _coding(stem: str) -> InvoiceCoding:
    return InvoiceCoding.model_validate(json.loads((SAMPLES / "ground_truth" / f"{stem}.json").read_text("utf-8")))


def _printed_totals(value: float) -> list[str]:
    """The grand total as English ("$1,234.56") or French ("1 234,56 $") documents print it."""
    sign = "-" if value < 0 else ""
    english = f"{abs(value):,.2f}"
    french = english.replace(",", " ").replace(".", ",")
    return [f"{sign}${english}", f"{sign}{french} $"]


def test_samples_are_bundled():
    assert len(STEMS) >= 10
    for suffix in (".pdf", ".md"):
        stems = sorted(p.stem for p in SAMPLES.glob(f"*{suffix}") if p.stem != "README")
        assert stems == STEMS, f"every sample needs a {suffix} and a ground truth"


@pytest.mark.parametrize("stem", STEMS)
def test_ground_truth_validates_as_invoice_coding(stem):
    coding = _coding(stem)
    assert coding.confidence_score == 1.0
    assert coding.line_items


@pytest.mark.parametrize("stem", STEMS)
def test_ground_truth_passes_controls(stem, reference):
    report = validate_coding(_coding(stem), reference)
    assert [(i.code, i.message) for i in report.errors] == []
    assert {i.code for i in report.warnings} == EXPECTED_WARNINGS.get(stem, set())
    if stem not in EXPECTED_WARNINGS:
        assert not report.requires_review and report.adjusted_confidence == 1.0


def test_alberta_gst_only_is_not_flagged_as_untaxed(reference):
    report = validate_coding(_coding("chinook_AB_GST_CCO-26-10418"), reference)
    assert "NO_TAX_CHARGED" not in {i.code for i in report.issues}
    assert "PROVINCIAL_TAX_NOT_CHARGED" not in {i.code for i in report.issues}


@pytest.mark.parametrize("stem", STEMS)
def test_gl_distribution_balances_to_grand_total(stem, reference):
    coding = _coding(stem)
    distribution = build_gl_distribution(coding, reference.tax)
    assert round(sum(e["amount"] for e in distribution), 2) == round(coding.grand_total, 2)
    for entry in distribution:
        if entry["kind"] == "tax":
            assert entry["gl_code"] in {"2310", "2320"}  # only recoverable GST/HST/QST get their own account


def test_non_recoverable_taxes_are_added_to_the_lines_that_carry_them(reference):
    coding = _coding("redriver_MB_GST_RST_RRO-55821")
    distribution = build_gl_distribution(coding, reference.tax)
    rst = {e["line_number"]: e["non_recoverable_tax"] for e in distribution if e["kind"] == "expense"}
    assert rst[3] == 0.0  # design service: GST only
    assert round(sum(rst.values()), 2) == 941.15
    us = build_gl_distribution(_coding("cascade_US_SalesTax_INV-30981"), reference.tax)
    assert [e["kind"] for e in us] == ["expense"] * 4  # US sales tax is expensed into the lines


@pytest.mark.parametrize("stem", STEMS)
def test_documents_show_invoice_number_and_total(stem):
    coding = _coding(stem)
    markdown = (SAMPLES / f"{stem}.md").read_text(encoding="utf-8")
    with pymupdf.open(SAMPLES / f"{stem}.pdf") as pdf:
        pdf_text = "\n".join(page.get_text() for page in pdf)
    totals = _printed_totals(coding.grand_total)
    for text, kind in ((markdown, "markdown"), (pdf_text, "PDF")):
        assert coding.invoice_number in text, f"{kind} lacks invoice number {coding.invoice_number}"
        assert any(t in text for t in totals), f"{kind} lacks the grand total as printed ({' or '.join(totals)})"
