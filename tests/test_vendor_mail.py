"""Ask the vendor: an email from the open checks, never mentioning internal fraud controls."""

import json
from urllib.parse import unquote

from ap_coder import vendor_mail
from ap_coder.validation import Issue

from .conftest import SAMPLES


def _doc(stem="northwind_ON_HST_NW-2026-0912", **changes):
    return {**json.loads((SAMPLES / "ground_truth" / f"{stem}.json").read_text()), **changes}


def test_only_what_the_vendor_can_fix():
    doc = _doc(po_number="PO-89904")
    issues = [
        Issue("error", "GST_HST_NUMBER_MISSING", "no number"),
        Issue("warning", "PO_PRICE_OVER", "price", 2),
        Issue("warning", "VENDOR_BANK_CHANGED", "bank account …7766 differs"),
        Issue("error", "VENDOR_ON_HOLD", "on hold: lawsuit"),
        Issue("warning", "AMOUNT_UNUSUAL", "3x usual"),
        Issue("warning", "GL_UNKNOWN", "gl"),
        Issue("warning", "LINE_MATH", "math", 3),
        Issue("error", "TOTAL_MISMATCH", "total"),
    ]
    mail = vendor_mail.draft(doc, issues, signature="Jane Doe")
    assert len(mail.points) == 3  # totals (one point), GST number, price above the PO
    body = mail.body
    assert "GST/HST registration number" in body and "PO-89904" in body and "(line 3)" in body
    line2 = next(li for li in doc["line_items"] if li["line_number"] == 2)
    assert f"{line2['unit_price']:,.2f}" in body
    for secret in ("bank", "7766", "hold", "lawsuit", "usual", "GL"):
        assert secret not in body
    assert body.rstrip().endswith("Jane Doe") and doc["invoice_number"] in mail.subject
    assert unquote(mail.mailto()).endswith(body)


def test_nothing_to_ask():
    issues = [Issue("warning", "VENDOR_TAX_NUMBER_CHANGED", "x"), Issue("info", "VENDOR_NEW", "y")]
    assert vendor_mail.points(_doc(), issues) == [] and vendor_mail.askable(issues) == []


def test_french_for_a_quebec_vendor():
    doc = _doc("montroyal_QC_TPS_TVQ_ACMR-2026-1187")
    assert vendor_mail.suggested_language(doc) == vendor_mail.FRENCH
    assert vendor_mail.suggested_language(_doc()) == vendor_mail.ENGLISH
    mail = vendor_mail.draft(doc, [{"code": "QST_NUMBER_FORMAT", "line_number": None}], vendor_mail.FRENCH)
    assert mail.body.startswith("Bonjour") and "TVQ" in mail.body and "Facture" in mail.subject
    total = f"{doc['grand_total']:,.2f}".replace(",", " ").replace(".", ",")
    assert total in mail.body


def test_po_points_need_a_po_number_and_are_not_repeated():
    doc = _doc(po_number="")
    issues = [Issue("warning", "PO_UNKNOWN", "x"), Issue("info", "PO_NOT_QUOTED", "y")]
    assert vendor_mail.points(doc, issues) == [vendor_mail._TEXT["en"]["po_missing"]]
    doc = _doc(po_number="4500012")
    twice = [Issue("warning", "PO_QTY_OVER", "a", 1), Issue("warning", "PO_QTY_OVER", "b", 1)]
    assert len(vendor_mail.points(doc, twice)) == 1


def test_every_askable_code_is_a_real_check():
    from ap_coder.help import CHECKS

    assert vendor_mail.ASKABLE <= set(CHECKS)


def test_statement_request():
    from ap_coder import statements as stm

    lines = [
        stm.Line(stm.MATCHED, "101", "2026-09-01", 1000.0, 1000.0, 1),
        stm.Line(stm.NOT_RECEIVED, "103", "2026-09-20", 1234.5),
        stm.Line(stm.DIFFERS, "102", "", 250.0, 245.0, 2),
    ]
    mail = vendor_mail.statement_request(lines, signature="Jane")
    assert "- 103 dated 2026-09-20: 1,234.50" in mail.body
    assert "- 102: 250.00 on the statement, 245.00 on our copy" in mail.body
    assert "101" not in mail.body and len(mail.points) == 2
    french = vendor_mail.statement_request(lines, vendor_mail.FRENCH)
    assert "- 103 du 2026-09-20 : 1 234,50" in french.body
    assert vendor_mail.statement_request(lines[:1]) is None


def test_french_wording_numbers_and_taxes():
    doc = _doc("montroyal_QC_TPS_TVQ_ACMR-2026-1187", po_number="4500012")
    issues = [Issue("warning", "TAX_RATE_NONSTANDARD", "x"), Issue("warning", "PO_PRICE_OVER", "y", 1)]
    body = vendor_mail.draft(doc, issues, vendor_mail.FRENCH).body
    assert "TPS 5 %" in body and "TVQ 9,975 %" in body and "au Québec" in body and "GST" not in body
    price = f"{doc['line_items'][0]['unit_price']:,.2f}".replace(",", " ").replace(".", ",")
    assert f"facturée {price} l'unité" in body
    english = vendor_mail.draft({**doc, "ship_to_province": "OUTSIDE_CANADA", "supplier_province": ""}, issues).body
    assert "a supply outside Canada" in english and "OUTSIDE_CANADA" not in english


def test_credit_note_wording():
    doc = _doc("northwind_ON_HST_CN-2026-0047")
    assert doc["grand_total"] < 0
    body = vendor_mail.draft(doc, [Issue("error", "GST_HST_NUMBER_MISSING", "x")]).body
    assert (
        "credit note" in body and "apply it" in body and "pay it" not in body and "-" not in body.split("for ")[1][:3]
    )
