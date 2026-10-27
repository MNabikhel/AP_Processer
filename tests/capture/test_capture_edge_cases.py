"""Capture edge cases found by a bug hunt: each test reproduces a wrong result the reader once gave."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from ap_coder.capture import analyze, build_layout
from ap_coder.capture.bridge import ai_values
from ap_coder.capture.supplier import AUTONOMOUS, confirmed_values, learn, should_auto_approve
from ap_coder.capture.types import VERIFIED
from tests.pdfs import text_pdf as _pdf

TODAY = dt.date(2026, 10, 9)


_ACME = [
    (60, 60, "Acme Supply Ltd."), (60, 75, "12 Main St, Calgary, AB T2P 1A1"),
    (60, 90, "GST Reg. No. 123456782 RT0001"),
    (380, 120, "Invoice No:"), (460, 120, "AC-1001"), (380, 135, "Invoice Date:"), (460, 135, "2026-09-14"),
    (60, 250, "Description"), (300, 250, "Qty"), (500, 250, "Amount"),
    (60, 265, "Toner cartridges"), (300, 265, "2"), (500, 265, "1,000.00"),
    (380, 500, "Subtotal"), (500, 500, "1,000.00"), (380, 515, "GST 5%"), (500, 515, "50.00"),
    (380, 530, "Total Due CAD"), (500, 530, "1,050.00"),
]  # fmt: skip
_ACME_TRUTH = {
    "vendor_name": "Acme Supply Ltd.", "invoice_number": "AC-1001", "invoice_date": "2026-09-14", "currency": "CAD",
    "gst_hst_registration_number": "123456782RT0001", "subtotal": 1000.0, "grand_total": 1050.0,
    "tax_lines": [{"tax_type": "GST", "tax_amount": 50.0}],
}  # fmt: skip


def test_an_amount_the_coding_has_differently_is_not_verified(tmp_path):
    """The coding (what an autonomous approval posts) says subtotal 1,450.00; the page prints 1,000.00. The
    page's value adding up must not make the field "verified" (and the invoice touchless) while the posted
    value differs from it."""
    pdf = _pdf(tmp_path, _ACME)
    layout = build_layout(pdf, ocr=False)
    template = None
    for _ in range(3):
        template = learn(template, layout, confirmed_values(_ACME_TRUTH))
    agree = analyze(pdf, ocr=False, ai_values=ai_values(_ACME_TRUTH), template=template, today=TODAY)
    assert should_auto_approve(AUTONOMOUS, agree, True)[0]  # the coding matches the page: touchless
    coding = {**_ACME_TRUTH, "subtotal": 1450.0}
    capture = analyze(pdf, ocr=False, ai_values=ai_values(coding), template=template, today=TODAY)
    assert capture.fields["subtotal"].status != VERIFIED
    assert not should_auto_approve(AUTONOMOUS, capture, True)[0]


def test_credit_marks_and_separator_dashes_around_amounts(tmp_path):
    """A "Cr" after an amount is a credit however it is cased; a dash between the amount and more words on
    the line ("$1,050.00 - Payable on receipt") is a separator, not a trailing minus."""
    from ap_coder.capture.normalize import find_amounts, parse_amount

    assert parse_amount("1,050.00 Cr") == -1050.0
    assert parse_amount("1,050.00 CR") == -1050.0
    assert parse_amount("1,050.00-") == -1050.0
    assert parse_amount("$1,050.00 - Payable on receipt") == 1050.0
    assert [v for v, _, _ in find_amounts("Period 2026-10 total 45.00")][-1] == 45.0
    assert -2026.0 not in [v for v, _, _ in find_amounts("Period 2026-10 total 45.00")]
    pdf = _pdf(tmp_path, [(60, 60, "Acme Supply Ltd."), (380, 500, "Subtotal"), (500, 500, "1,000.00"),
                          (380, 515, "GST 5%"), (500, 515, "50.00"),
                          (380, 530, "Total Due: $1,050.00 - Payable on receipt")])  # fmt: skip
    capture = analyze(pdf, ocr=False, today=TODAY)
    assert capture.fields["grand_total"].value == 1050.0
    assert all(c["ok"] for c in capture.checks if c["code"] == "TOTALS_ADD_UP")


def _fake_ocr(monkeypatch) -> list:
    """OCR that records the page images it is given (and reads nothing): no model needed."""
    from ap_coder.capture import layout as layout_mod

    seen: list = []
    monkeypatch.delenv("AP_OCR_CACHE", raising=False)
    monkeypatch.setattr(layout_mod, "ocr_available", lambda: True)
    monkeypatch.setattr(layout_mod, "_engine", lambda name=None: lambda img: seen.append(img) or [])
    return seen


def test_a_multi_page_tiff_scan_is_read_page_by_page_at_its_own_resolution(tmp_path, monkeypatch):
    """A faxed or scanned invoice saved as one TIFF with a page per frame, at 200 dpi: every page is read
    (the totals are often on the last), and at the resolution it was scanned at, not shrunk to 72 dpi."""
    from PIL import Image

    seen = _fake_ocr(monkeypatch)
    pages = [Image.new("RGB", (1700, 2200), "white") for _ in range(2)]
    path = tmp_path / "scan.tif"
    pages[0].save(path, save_all=True, append_images=pages[1:], dpi=(200, 200))
    layout = build_layout(path)
    assert [p.number for p in layout.pages] == [1, 2]
    assert [img.shape[:2] for img in seen] == [(2200, 1700), (2200, 1700)]


def test_a_transparent_png_is_read_on_white_paper(tmp_path, monkeypatch):
    """A PNG with a transparent background (an export, a screenshot): its transparent pixels are paper, not
    black, or the black text disappears into a black page."""
    from PIL import Image, ImageDraw

    seen = _fake_ocr(monkeypatch)
    img = Image.new("RGBA", (400, 300), (0, 0, 0, 0))
    ImageDraw.Draw(img).text((20, 20), "Total 1,050.00", fill=(0, 0, 0, 255))
    path = tmp_path / "invoice.png"
    img.save(path)
    build_layout(path)
    assert tuple(seen[0][150, 200]) == (255, 255, 255)  # the background
    assert seen[0].min() < 50  # the text is still dark


def _unicode_pdf(tmp_path: Path, lines: list[tuple[float, float, str]], name: str) -> Path:
    """A PDF whose text layer holds exactly these characters (a Unicode font, so accents and leaders survive)."""
    import pymupdf

    font = pymupdf.Font("cjk")
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    for x, y, text in lines:
        writer = pymupdf.TextWriter(page.rect)
        writer.append((x, y), text, font=font, fontsize=10)
        writer.write_text(page)
    path = tmp_path / name
    doc.save(path)
    return path


def test_accents_stored_decomposed_in_the_text_layer(tmp_path):
    """Some PDF producers store "é" as "e" + a combining accent: the labels must still point at their values."""
    import unicodedata

    lines = [(60, 60, "Acme Supply Ltd."), (300, 120, "Numéro de facture: 12345"),
             (300, 135, "Date de facturation: 2026-09-14")]  # fmt: skip
    pdf = _unicode_pdf(tmp_path, [(x, y, unicodedata.normalize("NFD", t)) for x, y, t in lines], "nfd.pdf")
    import pymupdf

    with pymupdf.open(pdf) as doc:
        assert "é" in doc[0].get_text()  # the text layer really is decomposed
    capture = analyze(pdf, ocr=False, today=TODAY)
    assert capture.fields["invoice_number"].value == "12345"


def test_an_amount_after_ellipsis_leaders_keeps_its_place(tmp_path):
    """Word turns "..." into "…": "Total………… 1,050.00". The amount's span must stay on the printed text
    (compatibility forms such as "…" -> "..." change the length), or the total is lost."""
    from ap_coder.capture.normalize import find_amounts

    text = "Total………………… 1,050.00"
    assert [(v, text[a:b].strip()) for v, a, b in find_amounts(text)] == [(1050.0, "1,050.00")]
    pdf = _unicode_pdf(tmp_path, [(60, 60, "Acme Supply Ltd."), (300, 530, text)], "leaders.pdf")
    assert analyze(pdf, ocr=False, today=TODAY).fields["grand_total"].value == 1050.0


def _credit_memo(title: str, total_label: str, amounts: tuple[str, str, str, str]) -> list[tuple[float, float, str]]:
    line, sub, gst, total = amounts
    return [
        (60, 60, "Acme Supply Ltd."), (60, 75, "12 Main St, Calgary, AB T2P 1A1"), (400, 60, title),
        (380, 100, "Credit Memo No: CM-2045"), (380, 115, "Date: 2026-09-20"), (380, 130, "Original Invoice: AC-0998"),
        (60, 200, "Description"), (300, 200, "Qty"), (500, 200, "Amount"),
        (60, 215, "Returned toner cartridges"), (300, 215, "2"), (500, 215, line),
        (380, 500, "Subtotal"), (500, 500, sub), (380, 515, "GST 5%"), (500, 515, gst),
        (380, 530, total_label), (500, 530, total),
    ]  # fmt: skip


_POSITIVE = ("1,000.00", "1,000.00", "50.00", "1,050.00")


def test_a_credit_memo_printed_with_positive_amounts_is_a_credit(tmp_path):
    """Many suppliers print a credit memo's amounts without a sign, under a "CREDIT MEMO" title. AP Coder
    posts credits as negative amounts: read as printed, the credit would be posted as an invoice to pay."""
    for i, (title, label) in enumerate([("CREDIT MEMO", "Total Credit"), ("NOTE DE CRÉDIT", "Montant crédité")]):
        pdf = _unicode_pdf(tmp_path, _credit_memo(title, label, _POSITIVE), f"cm{i}.pdf")
        capture = analyze(pdf, ocr=False, today=TODAY)
        got = {f: capture.fields[f].value for f in ("subtotal", "gst_amount", "grand_total")}
        assert got == {"subtotal": -1000.0, "gst_amount": -50.0, "grand_total": -1050.0}, title
        assert [li.amount for li in capture.line_items] == [-1000.0]
        assert all(c["ok"] for c in capture.checks if c["code"] in ("TOTALS_ADD_UP", "LINES_ADD_UP"))
    # Printed with its signs, a credit memo is read as printed; an invoice stays positive.
    signed = ("-1,000.00", "-1,000.00", "-50.00", "-1,050.00")
    capture = analyze(_pdf(tmp_path, _credit_memo("CREDIT MEMO", "Total Credit", signed), "signed.pdf"), ocr=False,
                      today=TODAY)  # fmt: skip
    assert capture.fields["grand_total"].value == -1050.0 and capture.line_items[0].amount == -1000.0
    invoice = [(x, y, "INVOICE" if t == "CREDIT MEMO" else t) for x, y, t in _credit_memo("CREDIT MEMO", "Total Due",
               _POSITIVE) if not t.startswith(("Credit Memo No", "Original"))]  # fmt: skip
    capture = analyze(_pdf(tmp_path, invoice, "invoice.pdf"), ocr=False, today=TODAY)
    assert capture.fields["grand_total"].value == 1050.0


def test_a_learned_template_reads_a_positive_credit_memo_as_a_credit(tmp_path):
    from ap_coder.capture.supplier import apply_template

    truth = {"vendor_name": "Acme Supply Ltd.", "subtotal": -1000.0, "grand_total": -1050.0,
             "tax_lines": [{"tax_type": "GST", "tax_amount": -50.0}]}  # fmt: skip
    layout = build_layout(_pdf(tmp_path, _credit_memo("CREDIT MEMO", "Total Credit", _POSITIVE)), ocr=False)
    read = apply_template(learn(None, layout, confirmed_values(truth)), layout)
    assert read["grand_total"][0].value == -1050.0 and read["subtotal"][0].value == -1000.0


def test_a_canadian_supplier_billing_in_usd_keeps_03_04_ambiguous(tmp_path):
    """A Toronto supplier billing in US dollars may well print 03/04/2026 day first: the currency alone does
    not make it March 4 (that is for a supplier with a US address), so it is settled like any other 03/04."""
    lines = [(60, 60, "Maple Tech Ltd."), (60, 75, "12 Main St, Toronto, ON M5V 1A1"),
             (380, 100, "Invoice No: MT-1001"), (380, 115, "Invoice Date: 03/04/2026"), (380, 500, "Subtotal"),
             (500, 500, "1,000.00"), (380, 530, "Total USD"), (500, 530, "1,000.00")]  # fmt: skip
    capture = analyze(_pdf(tmp_path, lines), ocr=False, today=dt.date(2026, 4, 9))
    fr = capture.fields["invoice_date"]
    assert fr.value == "2026-04-03" and fr.status != VERIFIED  # received on April 9: April 3, not March 4
    us = [(x, y, t.replace("12 Main St, Toronto, ON M5V 1A1", "500 Pine St, Seattle, WA 98101")) for x, y, t in lines]
    assert analyze(_pdf(tmp_path, us, "us.pdf"), ocr=False, today=dt.date(2026, 4, 9)).fields["invoice_date"].value \
        == "2026-03-04"  # fmt: skip


def test_a_template_does_not_read_the_gst_registration_number_as_the_gst(tmp_path):
    """The template learned "GST" beside the GST amount. On a two-page invoice from the same supplier, the
    first page has no GST row, only "GST Reg. No. 123456782 RT0001": that number is not a GST of $123 million."""
    from ap_coder.capture.supplier import apply_template

    layout = build_layout(_pdf(tmp_path, _ACME), ocr=False)
    template = None
    for _ in range(3):
        template = learn(template, layout, confirmed_values(_ACME_TRUTH))
    head = [line for line in _ACME if line[1] < 300]
    tail = [(380, 500, "Subtotal"), (500, 500, "1,000.00"), (380, 515, "GST 5%"), (500, 515, "50.00"),
            (380, 530, "Total Due CAD"), (500, 530, "1,050.00")]  # fmt: skip
    import pymupdf

    doc = pymupdf.open()
    for lines in (head, tail):
        page = doc.new_page(width=612, height=792)
        for x, y, text in lines:
            page.insert_text((x, y), text, fontsize=10)
    doc.save(tmp_path / "two.pdf")
    read = apply_template(template, build_layout(tmp_path / "two.pdf", ocr=False))
    assert all(abs(r.value) < 1_000_000 for r in read.get("gst_amount", [])), read.get("gst_amount")


_MAPLE = [(50, 60, "Maple Supply Inc."), (50, 75, "100 King St W, Toronto, ON M5H 1A1"),
          (380, 60, "Invoice No.: INV-1001"), (380, 75, "Invoice Date: 2026-04-03")]  # fmt: skip


def test_a_spaced_dash_or_a_run_of_dashes_before_an_amount_is_a_separator():
    """A total printed "Total - $113.00" is 113.00, not a credit: a minus sign touches its number or its currency
    sign. Credits printed with a sign, parentheses, a trailing minus or CR stay credits."""
    from ap_coder.capture.normalize import find_amounts, parse_amount

    for text in ("Total - $113.00", "Total – 113.00", "Invoice Total: -- 113.00", "Total ---------- 113.00"):
        assert [v for v, _, _ in find_amounts(text)] == [113.0], text
    assert parse_amount("Amount Due – $1,234.56") == 1234.56
    for text in ("-113.00", "-$113.00", "$-113.00", "(113.00)", "113.00-", "113.00 CR", "Total: -113.00"):
        assert parse_amount(text) == -113.0, text


def test_totals_printed_after_a_spaced_dash_are_positive(tmp_path):
    lines = _MAPLE + [(50, 230, "Description"), (300, 230, "Qty"), (500, 230, "Amount"), (50, 250, "Widgets"),
                      (300, 250, "10"), (500, 250, "100.00"), (380, 320, "Subtotal - $100.00"),
                      (380, 335, "HST 13% - $13.00"), (380, 350, "Total - $113.00")]  # fmt: skip
    capture = analyze(_pdf(tmp_path, lines), ocr=False, today=TODAY)
    assert capture.fields["subtotal"].value == 100.0
    assert capture.fields["hst_amount"].value == 13.0
    assert capture.fields["grand_total"].value == 113.0


def _credit_note(title: str, label: str) -> list[tuple[float, float, str]]:
    return [(50, 60, "Imprimerie Beauce Inc."), (50, 75, "195 boul. des Laurentides, Levis (QC) G2Y 6K1"),
            (380, 50, title), (50, 180, "Date"), (250, 180, label), (50, 195, "2026-04-02"), (250, 195, "FA2627383"),
            (50, 230, "Description"), (500, 230, "Montant"), (50, 250, "Cable Cat6"), (500, 250, "-100,00"),
            (380, 320, "Sous-total"), (500, 320, "-100,00"), (380, 335, "TPS 5 %"), (500, 335, "-5,00"),
            (380, 350, "Total"), (500, 350, "-105,00")]  # fmt: skip


def test_a_credit_note_is_numbered_by_its_title_not_by_the_invoice_it_credits(tmp_path):
    """A title "Note de crédit 051527-CR" over "Facture originale n° FA2627383": the credit note's own number is
    its title's, and the original invoice's number is no candidate for it (also in English)."""
    cases = [("Note de crédit 051527-CR", "Facture originale n°", "051527-CR"),
             ("Note de crédit 051527-CR", "Facture d'origine", "051527-CR"),
             ("CREDIT NOTE CN-0042", "Original Invoice No.", "CN-0042"),
             ("CREDIT NOTE CN-0042", "Applies to Invoice #", "CN-0042"),
             ("CREDIT NOTE CN-0042", "Ref. Invoice #", "CN-0042")]  # fmt: skip
    for i, (title, label, number) in enumerate(cases):
        capture = analyze(_pdf(tmp_path, _credit_note(title, label), f"cn{i}.pdf"), ocr=False, today=TODAY)
        fr = capture.fields["invoice_number"]
        assert fr.value == number, (title, label, fr.value)
        assert not any("FA2627383" in f"{k} {v}" for k, v in fr.sources.items()), fr.sources


def test_the_currency_the_amounts_are_in_wins_over_one_named_in_a_note():
    """A note "All amounts in USD. Exchange rate 1 USD = 1.37 CAD": US dollars. Several codes and nothing to tell
    which is the invoice's: a code is given, but not as sure (the reviewer checks it)."""
    from ap_coder.capture.normalize import currency_evidence

    assert currency_evidence("Total 113.00\nAll amounts in USD. Exchange rate 1 USD = 1.37 CAD") == ("USD", 0.8)
    assert currency_evidence("Total Due USD 1,050.00\nUS customers pay in USD; others in CAD")[0] == "USD"
    assert currency_evidence("Total 1,050.00 CAD") == ("CAD", 0.8)
    assert currency_evidence("We accept CAD and USD")[1] < 0.8


def test_a_usd_invoice_that_mentions_cad_reads_usd(tmp_path):
    lines = _MAPLE + [(380, 320, "Subtotal"), (500, 320, "100.00"), (380, 350, "Total"), (500, 350, "100.00"),
                      (50, 400, "All amounts in USD. Exchange rate 1 USD = 1.37 CAD")]  # fmt: skip
    assert analyze(_pdf(tmp_path, lines), ocr=False, today=TODAY).fields["currency"].value == "USD"
    several = _MAPLE + [(380, 350, "Total"), (500, 350, "100.00"), (50, 400, "We accept CAD and USD")]
    fr = analyze(_pdf(tmp_path, several, "several.pdf"), ocr=False, today=TODAY).fields["currency"]
    assert fr.status == "check"


def test_an_ambiguous_date_the_coding_agrees_with_is_not_verified(tmp_path):
    """03/04/2026 with nothing on the page to settle it: the AI's coding choosing the same order (or the day
    the invoice arrived choosing it) does not make it verified."""
    lines = [(50, 60, "Maple Supply Inc."), (50, 75, "100 King St W, Toronto, ON M5H 1A1"),
             (380, 60, "Invoice No.: INV-1001"), (380, 75, "Invoice Date: 03/04/2026"),
             (380, 320, "Subtotal"), (500, 320, "100.00"), (380, 335, "HST 13%"), (500, 335, "13.00"),
             (380, 350, "Total"), (500, 350, "113.00")]  # fmt: skip
    pdf = _pdf(tmp_path, lines)
    ai = {"invoice_date": "2026-04-03", "invoice_number": "INV-1001", "subtotal": 100.0, "grand_total": 113.0}
    for today in (None, dt.date(2026, 4, 20)):
        fr = analyze(pdf, ocr=False, ai_values=ai, today=today).fields["invoice_date"]
        assert fr.value == "2026-04-03" and fr.status != VERIFIED, (today, fr.status, fr.confidence)
    # Settled on the page (a due date printed 25/04/2026: day first), the agreement verifies it as before.
    settled = [*lines, (380, 90, "Due Date: 25/04/2026")]
    ai["due_date"] = "2026-04-25"
    fr = analyze(_pdf(tmp_path, settled, "settled.pdf"), ocr=False, ai_values=ai, today=None).fields["invoice_date"]
    assert fr.value == "2026-04-03" and fr.status == VERIFIED


def _table(rows: list[tuple[float, float, str]], heads=((280, "Qty"), (380, "Unit Price"))):
    head = [(50, 230, "Description"), *((x, 230, t) for x, t in heads), (500, 230, "Amount")]
    return _MAPLE + head + rows


def test_unit_prices_to_three_decimals_are_not_thousands(tmp_path):
    """Diesel 1,000 L at 1.459 = 1,459.00 and fasteners 200 at 0.125 = 25.00: "0.125" is never 125, and
    "1.459" reads as the price that makes the line's amount."""
    from ap_coder.capture.normalize import parse_amount

    assert parse_amount("0.125") == 0.125 and parse_amount("0,125") == 0.125
    rows = [(50, 250, "Diesel (L)"), (280, 250, "1,000"), (380, 250, "1.459"), (500, 250, "1,459.00"),
            (50, 265, "Fasteners"), (280, 265, "200"), (380, 265, "0.125"), (500, 265, "25.00"),
            (380, 320, "Subtotal"), (500, 320, "1,484.00"), (380, 350, "Total"), (500, 350, "1,484.00")]  # fmt: skip
    capture = analyze(_pdf(tmp_path, _table(rows)), ocr=False, today=TODAY)
    got = [(li.quantity, li.unit_price, li.amount) for li in capture.line_items]
    assert got == [(1000.0, 1.459, 1459.0), (200.0, 0.125, 25.0)], got


def test_space_thousands_with_a_decimal_point_are_one_amount(tmp_path):
    """A space between the thousands and a decimal point ("1 050.00") is one amount on a totals line and as a single
    value: a text PDF's "Subtotal 1 000.00 / GST 50.00 / Total Due 1 050.00" is not read 0 + 50 = 50."""
    from ap_coder.capture import read_fields
    from ap_coder.capture.normalize import find_amounts, parse_amount

    singles = {"1 234.56": 1234.56, "$1 234.56": 1234.56, "CAD 1 234.56": 1234.56, "1 234": 1234.0,
               "1 234.56 $": 1234.56, "12 345.67": 12345.67, "1 234 567.89": 1234567.89}  # fmt: skip
    for text, value in singles.items():
        assert parse_amount(text) == value, text
    assert [v for v, _, _ in find_amounts("Total Due 1 050.00")] == [1050.0]
    assert [v for v, _, _ in find_amounts("Subtotal 1 100.00 GST 55.00")] == [1100.0, 55.0]  # a label: never split
    assert [v for v, _, _ in find_amounts("Total 1 234")] == [1234.0]
    assert [v for v, _, _ in find_amounts("1 050.00")] == [1050.0]  # nothing on the line it adds up to
    spans = [(a, b) for _, a, b in find_amounts("Bolts 3 250.00 750.00")]
    assert [("Bolts 3 250.00 750.00")[a:b].strip() for a, b in spans] == ["3", "250.00", "750.00"]
    lines = [(60, 60, "Acme Supply Ltd."), (380, 500, "Subtotal"), (500, 500, "1 000.00"), (380, 515, "GST 5%"),
             (500, 515, "50.00"), (380, 530, "Total Due"), (500, 530, "1 050.00")]  # fmt: skip
    fields = read_fields(build_layout(_pdf(tmp_path, lines), ocr=False))
    got = {f: (c[0].value if c else None) for f, c in fields.items()}
    assert (got["subtotal"], got["gst_amount"], got["grand_total"]) == (1000.0, 50.0, 1050.0)


def test_a_quantity_beside_its_price_is_not_one_spaced_thousand(tmp_path):
    """A row's "10 100.00" (quantity 10 set close to its price 100.00) is not 10,100; "1 234,56" and "1 234 567" are
    still French thousands, and a French "3 250,00" quantity and price is told apart by the line's amount."""
    from ap_coder.capture.normalize import find_amounts, parse_amount

    assert [v for v, _, _ in find_amounts("Widgets 10 100.00 1,000.00")] == [10.0, 100.0, 1000.0]
    assert [v for v, _, _ in find_amounts("Bolts 3 250.00 750.00")] == [3.0, 250.0, 750.0]
    assert [v for v, _, _ in find_amounts("10 100")] == [10.0, 100.0]  # whole numbers: a quantity and a price
    assert parse_amount("1 234,56 $") == 1234.56 and parse_amount("1 234 567") == 1234567.0
    rows = [(50, 250, "Widgets"), (330, 250, "10"), (345, 250, "100.00"), (500, 250, "1,000.00"),
            (380, 320, "Subtotal"), (500, 320, "1,000.00"), (380, 350, "Total"), (500, 350, "1,000.00")]  # fmt: skip
    capture = analyze(_pdf(tmp_path, _table(rows, ((330, "Qty"), (420, "Unit Price")))), ocr=False, today=TODAY)
    assert [(li.quantity, li.unit_price, li.amount) for li in capture.line_items] == [(10.0, 100.0, 1000.0)]
    fr_rows = [(50, 250, "Boulons"), (330, 250, "3"), (343, 250, "250,00"), (500, 250, "750,00"),
               (380, 320, "Sous-total"), (500, 320, "750,00"), (380, 350, "Total"), (500, 350, "750,00")]  # fmt: skip
    heads = ((330, "Qté"), (420, "Prix unitaire"))
    capture = analyze(_pdf(tmp_path, _table(fr_rows, heads), "fr.pdf"), ocr=False, today=TODAY)
    assert [(li.quantity, li.unit_price, li.amount) for li in capture.line_items] == [(3.0, 250.0, 750.0)]


def test_column_headings_set_close_together_keep_their_own_columns(tmp_path):
    """Headings "Qty" and "Unit Price" 13 points apart read as one segment: each heading still has its own column, so
    the quantity is read under "Qty" (10), not the unit price (100.00)."""
    rows = [(50, 250, "Widgets"), (330, 250, "10"), (360, 250, "100.00"), (500, 250, "1,000.00"),
            (50, 265, "Bolts"), (330, 265, "3"), (360, 265, "250.00"), (500, 265, "750.00"),
            (380, 320, "Subtotal"), (500, 320, "1,750.00"), (380, 350, "Total"), (500, 350, "1,750.00")]  # fmt: skip
    capture = analyze(_pdf(tmp_path, _table(rows, ((330, "Qty"), (360, "Unit Price")))), ocr=False, today=TODAY)
    got = [(li.quantity, li.unit_price, li.amount) for li in capture.line_items]
    assert got == [(10.0, 100.0, 1000.0), (3.0, 250.0, 750.0)], got


# --- Read from a page reader's transcription, as on a digital PDF (the same text as the page reader's) -------------

SAMPLES = Path(__file__).resolve().parents[2] / "samples"


def _read(text: str):
    """``text`` read as one page, by the rules and by the page reader (the same text, as on a digital PDF)."""
    from ap_coder.capture.transcript import layout_from_transcript

    return analyze(Path("x.md"), layout=layout_from_transcript([text]), tables=False, second_read=False,
                   today=dt.date(2026, 10, 16), page_text=[text])  # fmt: skip


_AVOIR = """Agence Créative Mont-Royal inc.
4200 boul. Saint-Laurent, bureau 600, Montréal (QC) H2W 2R2
No TPS : 812345676 RT0001    No TVQ : 1218765432 TQ0001

# {title}

Facture no : AV-2026-0042
Date de facturation : 28 septembre 2026

Facturer à : Fabrikam Canada Ltée, Service du marketing
1000 rue De La Gauchetière O., bureau 2400, Montréal (QC) H3B 4W5

<table>
<tr><th>#</th><th>Description</th><th>Qté</th><th>Prix unitaire</th><th>Montant</th></tr>
<tr><td>1</td><td>Crédit - conception graphique non livrée</td><td>1</td><td>2 100,00</td><td>2 100,00</td></tr>
</table>

<table>
<tr><td>Sous-total</td><td>2 100,00 $</td></tr>
<tr><td>TPS 5 %</td><td>105,00 $</td></tr>
<tr><td>TVQ 9,975 %</td><td>209,48 $</td></tr>
<tr><td>Total à créditer</td><td>2 414,48 $</td></tr>
</table>
"""


def test_a_french_avoir_printed_positive_is_a_credit_and_needs_a_person():
    """A French credit note titled "AVOIR" (or "Facture d'avoir", "Credit Invoice") printed with positive amounts is
    a credit, as a "CREDIT NOTE" is: read as printed, it was approved without a person as an invoice to pay."""
    for title in ("AVOIR", "FACTURE D'AVOIR", "CREDIT INVOICE"):
        capture = _read(_AVOIR.format(title=title))
        got = {f: capture.fields[f].value for f in ("subtotal", "gst_amount", "qst_amount", "grand_total")}
        assert got == {"subtotal": -2100.0, "gst_amount": -105.0, "qst_amount": -209.48, "grand_total": -2414.48}, title
        assert capture.credit_note, title
        assert should_auto_approve(AUTONOMOUS, capture, True, []) == (False, "always a person: it is a credit note")
    assert not _read(_AVOIR.format(title="FACTURE")).credit_note


def test_canadian_dollars_stated_in_words_win_over_a_code_in_a_payment_note():
    """ "All prices in Canadian dollars" settles the currency; "Payments in USD accepted" (or a USD wire account)
    says how the supplier may be paid, not what it bills in. Read as USD, the invoice was approved without a person."""
    from ap_coder.capture.normalize import currency_evidence

    note = "All prices in Canadian dollars. Payments in USD accepted at the bank's posted rate."
    assert currency_evidence(f"Total $1,000.00\n{note}")[0] == "CAD"
    assert currency_evidence("Total $1,000.00\nMontants en dollars canadiens.")[0] == "CAD"
    assert currency_evidence("Total $1,000.00\nAll amounts in US dollars")[0] == "USD"
    assert currency_evidence("Total $1,000.00\nUSD cheques accepted at par")[1] < 0.8  # a guess, not verified
    assert currency_evidence("Total $1,000.00\nWire to: Bank of Nova Scotia, USD account 4455")[1] < 0.8
    text = (SAMPLES / "harbourview_NS_HST_HPS-2026-0347.md").read_text().replace("Total Due (CAD)", "Total Due")
    capture = _read(text.replace("Payable on receipt.", f"Payable on receipt.\n{note}"))
    assert capture.fields["currency"].value == "CAD"


def test_a_currency_in_words_in_a_payment_note_is_not_a_statement():
    """ "Payments in US dollars accepted" says how the supplier may be paid, as "Payments in USD accepted" does: the
    code printed on the total ("CAD 1,050.00") decides. Read as a statement, a Canadian invoice was read as USD."""
    from ap_coder.capture.normalize import currency_evidence

    for note in ("Payments in US dollars accepted.", "Paiements en dollars américains acceptés.",
                 "We accept payment in U.S. dollars", "Remit in Canadian dollars or US dollars"):  # fmt: skip
        assert currency_evidence(f"Total Due CAD 1,050.00\n{note}") == ("CAD", 0.8), note
    assert currency_evidence("Total Due $1,050.00\nAll amounts in US dollars. Remit to Box 12.") == ("USD", 0.8)


def test_a_registration_number_read_only_in_the_bill_to_block_is_not_verified():
    """The customer's GST/HST number printed in the "Client:" block, and no number of the supplier's: a valid check
    digit shows the number is well formed, not whose it is. Verified, input tax credits were claimed on it."""
    text = (SAMPLES / "harbourview_NS_HST_HPS-2026-0347.md").read_text()
    text = text.replace("HST Registration No. 356201848 RT0001\n", "")
    capture = _read(text.replace("Attn: Controller\n", "Attn: Controller\nGST/HST No. 123456782 RT0001\n"))
    gst = capture.fields["gst_hst_registration_number"]
    assert gst.status != VERIFIED and any("bill-to" in r for r in gst.reasons), (gst.status, gst.reasons)
    assert not should_auto_approve(AUTONOMOUS, capture, True, [])[0]
    # The supplier's own number at the top is still verified.
    assert _read((SAMPLES / "harbourview_NS_HST_HPS-2026-0347.md").read_text()).fields[
        "gst_hst_registration_number"].status == VERIFIED  # fmt: skip


def test_a_french_quantity_in_one_space_group_is_read_whole():
    """ "2 500" exemplaires at 0,84 make 2 100,00: the quantity is 2500, not 2 (and "6 000" mots at 0,22 are 6000).
    The amounts were right, so nothing failed, and the offline coder posted quantities 2 and 6."""
    from ap_coder.capture.reader import read_line_items
    from ap_coder.capture.tables import _qty_and_price
    from ap_coder.capture.transcript import layout_from_transcript

    text = (SAMPLES / "montroyal_QC_TPS_TVQ_ACMR-2026-1187.md").read_text(encoding="utf-8")
    page1 = text.split("<!-- PageBreak -->")[0]
    got = [(li.quantity, li.unit_price, li.amount) for li in read_line_items(layout_from_transcript([page1]))]
    assert got == [(24.0, 95.0, 2280.0), (2500.0, 0.84, 2100.0), (1.0, 3500.0, 3500.0), (6000.0, 0.22, 1320.0)], got
    assert _qty_and_price("2 500", "0,84", 2100.0) == (2500.0, 0.84)
    assert _qty_and_price("10", "100,00", 1000.0) == (10.0, 100.0)


_POSITIVE_CREDIT_NOTE = """Northwind IT Solutions Inc.
200 Bay Street, Suite 1500, Toronto, ON M5J 2J2
GST/HST Reg. No: 123456782 RT0001

# CREDIT NOTE

Credit Note No: CN-2026-0047
Credit Date: September 28, 2026
Original Invoice: NW-2026-0912

<table>
<tr><th>#</th><th>Description</th><th>Qty</th><th>Unit Price</th><th>Amount</th></tr>
<tr><td>1</td><td>Return of one Dell Latitude 7450 laptop</td><td>1</td><td>1,450.00</td><td>1,450.00</td></tr>
<tr><td>2</td><td>Consulting hours not delivered</td><td>4</td><td>150.00</td><td>600.00</td></tr>
<tr><td>3</td><td>Restocking fee</td><td>1</td><td>-50.00</td><td>-50.00</td></tr>
</table>

<table>
<tr><td>Subtotal</td><td>$2,000.00</td></tr>
<tr><td>HST 13% (ON)</td><td>$260.00</td></tr>
<tr><td>Total Credit (CAD)</td><td>$2,260.00</td></tr>
</table>
"""


def test_a_credit_note_printed_positive_with_one_negative_adjustment_is_still_a_credit():
    """A CREDIT NOTE printed without signs but with a "Restocking fee -50.00" line: the fee's sign is the fee's, not
    the document's. The credit's own subtotal and total are printed positive, so the credit is read negative."""
    capture = _read(_POSITIVE_CREDIT_NOTE)
    got = {f: capture.fields[f].value for f in ("subtotal", "hst_amount", "grand_total")}
    assert got == {"subtotal": -2000.0, "hst_amount": -260.0, "grand_total": -2260.0}, got
    assert [li.amount for li in capture.line_items] == [-1450.0, -600.0, 50.0]  # the fee lowers the credit
    assert not [c["code"] for c in capture.checks if not c["ok"] and c["code"] in ("TOTALS_ADD_UP", "TAX_RATE")]


def test_a_zero_or_a_total_lookalike_does_not_vote_on_a_credit_notes_sign():
    """A CREDIT NOTE printed positive that also prints "Balance Due $0.00" (nothing left to pay) is still a credit:
    a zero has no sign, and a line that only looks like a total ("Previous Balance") is not the credit's own."""
    from ap_coder.capture.reader import credit_printed_positive
    from ap_coder.capture.transcript import layout_from_transcript

    total = "<tr><td>Total Credit (CAD)</td><td>$2,260.00</td></tr>"
    for extra in ("<tr><td>Balance Due</td><td>$0.00</td></tr>", "<tr><td>Amount Due</td><td>0.00</td></tr>",
                  "<tr><td>Previous Balance</td><td>-1,200.00</td></tr>"):  # fmt: skip
        text = _POSITIVE_CREDIT_NOTE.replace(total, total + "\n" + extra)
        assert credit_printed_positive(layout_from_transcript([text])), extra
        got = {f: _read(text).fields[f].value for f in ("subtotal", "hst_amount", "grand_total")}
        assert got == {"subtotal": -2000.0, "hst_amount": -260.0, "grand_total": -2260.0}, (extra, got)


def test_a_field_that_failed_a_check_does_not_show_its_measured_confidence():
    """A total that does not add up is marked down (x0.3); the calibration's measure of its evidence pattern, which
    does not record the failure, must not put it back up (it showed 0.918 beside "check failed")."""
    text = (SAMPLES / "harbourview_NS_HST_HPS-2026-0347.md").read_text().replace("$10,960.53", "$10,990.53")
    total = _read(text).fields["grand_total"]
    assert total.status == "check" and any(r.startswith("check failed") for r in total.reasons)
    assert total.confidence <= 0.3, total.confidence


def test_a_price_to_four_decimals_with_four_digits_before_the_point_is_one_number():
    """ "1234.5678" is 1234.5678, not 1234.56 and a stray 78; "1.3023" stays a price to four decimals."""
    from ap_coder.capture.normalize import find_amounts, parse_amount

    assert parse_amount("1234.5678") == 1234.5678
    assert [v for v, _, _ in find_amounts("Fuel 1234.5678 L")] == [1234.5678]
    assert parse_amount("1.3023") == 1.3023 and parse_amount("1,234.56") == 1234.56


def test_a_grouped_amount_to_four_decimals_is_one_number():
    """ "12,345.6789", "1.234,5678" and "1 234.5678" are each one number, not a number to three decimals and a stray
    digit, or a 1 and a price; a quantity set close to a four-decimal price still splits when it is one."""
    from ap_coder.capture.normalize import find_amounts, parse_amount

    for text, value in (("12,345.6789", 12345.6789), ("1.234,5678", 1234.5678), ("1 234.5678", 1234.5678),
                        ("1'234.5678", 1234.5678), ("1,234,567.1234", 1234567.1234)):  # fmt: skip
        assert parse_amount(text) == value, text
        assert [v for v, _, _ in find_amounts(f"Fuel {text} L")] == [value], text
    assert [v for v, _, _ in find_amounts("Bolts 2 250.5000 501.00")] == [2, 250.5, 501.0]
    assert parse_amount("1,234.56") == 1234.56 and parse_amount("1.3023") == 1.3023
