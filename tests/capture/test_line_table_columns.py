"""A line's amount is read from ONE column of the line table, chosen by its heading and by where the numbers are
printed, for the whole table (B-2197 to B-2203): never another figure of a row that happens to fit, never the amount
before a discount, never the tax; a tax cell not found is "not read", a column that names one tax says nothing of
another, and lines that do not add up are left as read for a person, not balanced by a made-up charge. The layouts
are the ones a review of B-2106 and B-2107 tried. Every invoice here is made up."""

from __future__ import annotations

import dataclasses as dc

import pymupdf
import pytest

from ap_coder.capture import layout
from ap_coder.capture.reader import read_line_items

ocr = pytest.mark.skipif(not layout.ocr_available(), reason="local OCR not installed (pip install -e .[ocr])")

ON = ["Northwind IT Solutions Inc.", "200 Bay Street, Toronto, ON M5J 2J2", "GST/HST Reg. No: 123456782 RT0001"]
BC = ["Pacific Office Supply Ltd", "1055 West Georgia Street, Vancouver, BC V6E 3P3", "GST Reg. No: 555666775 RT0001",
      "PST Reg. No: PST-1234-5678"]  # fmt: skip
QC = ["Fournitures Boreal Inc.", "800 rue Sherbrooke Ouest, Montreal, QC H3A 1G1", "TPS: 123456782 RT0001",
      "TVQ: 1234567890 TQ0001"]  # fmt: skip
AB = ["Prairie Storage Ltd", "410 4th Avenue SW, Calgary, AB T2P 0J1", "GST Reg. No: 555666775 RT0001"]
HST = [("Subtotal", "$300.00"), ("HST 13%", "$39.00"), ("Total", "$339.00")]
HST3 = [("Subtotal", "$1,400.00"), ("HST 13%", "$169.00"), ("Total", "$1,569.00")]
BCT = [("Subtotal", "$300.00"), ("GST 5%", "$15.00"), ("PST 7%", "$14.00"), ("Total", "$329.00")]
BCT3 = [("Subtotal", "$1,400.00"), ("GST 5%", "$70.00"), ("PST 7%", "$91.00"), ("Total", "$1,561.00")]
QCT = [("Sous-total", "300,00 $"), ("TPS 5 %", "15,00 $"), ("TVQ 9,975 %", "29,93 $"), ("Total", "344,93 $")]
MADE_UP = "Charges not in the line table"
FOUND = "LINE_AMOUNTS_TO_CHECK"


def _gst(subtotal: float) -> list[tuple[str, str]]:
    return [("Subtotal", f"${subtotal:,.2f}"), ("GST 5%", f"${subtotal * 0.05:,.2f}"),
            ("Total", f"${subtotal * 1.05:,.2f}")]  # fmt: skip


def _left(label: str, left: float, right: float) -> tuple:
    """A column as a spreadsheet prints it: the heading from its left edge, the numbers up to its right edge."""
    return (label, left + 3, "l", right - 3, "r")


def _centred(label: str, left: float, right: float) -> tuple:
    return (label, (left + right) / 2, "c", right - 4, "r")


def _pdf(path, items: list[tuple], pages: int = 1):
    """A letter-size PDF: (x, y, text[, "l" | "c" | "r"[, page]]), x the text's left edge, middle or right edge."""
    doc = pymupdf.open()
    for _ in range(pages):
        doc.new_page(width=612, height=792)
    for x, y, text, *more in items:
        width = pymupdf.get_text_length(text, fontname="helv", fontsize=10)
        at = x - {"l": 0, "c": width / 2, "r": width}[more[0] if more else "l"]
        doc[more[1] - 1 if len(more) > 1 else 0].insert_text((at, y), text, fontsize=10)
    doc.save(path)
    doc.close()
    return path


def _top(name: str, supplier: list[str]) -> list[tuple]:
    ours = supplier[1].replace(supplier[1].split(",")[0], "9 Elm Road")  # (delivered in the vendor's province)
    return [*((50, 60 + 14 * i, text) for i, text in enumerate(supplier)), (50, 130, "INVOICE"),
            (50, 146, f"Invoice No: {name}"), (50, 160, "Invoice Date: 2026-10-20"),
            (50, 174, "Bill To: Fabrikam Canada Ltd."), (50, 188, ours)]  # fmt: skip


def _invoice(tmp_path, name: str, supplier: list[str], heads: list[tuple], rows: list[list[str]],
             totals: list[tuple[str, str]]):  # fmt: skip
    """heads: (label, x), (label, x, align) for the heading and its cells, or (label, x, align, cell x, cell align)."""
    items = _top(name, supplier)
    for n, cells in enumerate([[h[0] for h in heads], *rows]):
        for cell, (_, x, *more) in zip(cells, heads, strict=True):
            at, align = (more[1], more[2]) if n and len(more) >= 3 else (x, more[0] if more else "l")
            if cell:
                items.append((at, 230 + (24 + 22 * (n - 1) if n else 0), cell, align))
    at = 254 + 22 * len(rows) + 20
    for n, (label, value) in enumerate(totals):
        items += [(380, at + 16 * n, label), (560, at + 16 * n, value, "r")]
    return _pdf(tmp_path / f"{name}.pdf", items)


def _scan(pdf):
    """An image-only PDF of the page at 200 dpi: a clean scan, read by OCR."""
    out = pymupdf.open()
    with pymupdf.open(pdf) as src:
        for page in src:
            new = out.new_page(width=page.rect.width, height=page.rect.height)
            new.insert_image(new.rect, pixmap=page.get_pixmap(dpi=200))
    target = pdf.with_name(pdf.stem + "-scan.pdf")
    out.save(target)
    out.close()
    return target


def _rows_read(tmp_path, heads: list[tuple], rows: list[list[str]]) -> list:
    """The rule reader's own lines of the table, on the page as its PDF prints it."""
    return read_line_items(layout.build_layout(_invoice(tmp_path, "T-1", ON, heads, rows, HST)))


@pytest.fixture
def pipe(tmp_path):
    from ap_coder.ai.pipeline import InvoicePipeline
    from ap_coder.core.config import Settings
    from ap_coder.core.store import Store, load_sample_setup

    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    settings = Settings()
    settings = dc.replace(settings, llm=dc.replace(settings.llm, provider="off"))
    return InvoicePipeline(settings, store.reference_data(), store=store)


def _amounts(result) -> list[float]:
    return [li["amount"] for li in result.output["line_items"]]


def _taxes(result) -> list[list[str]]:
    return [li["taxes_applied"] for li in result.output["line_items"]]


def _codes(result) -> set[str]:
    return {i.code for i in result.report.issues}


def _said(result) -> str:
    return " ".join(i.message for i in result.report.issues if i.code == FOUND)


def _left_for_a_person(result, amounts: list[float]) -> None:
    """The lines are as printed, nothing made up balances them, and two findings say so (one closes Approve)."""
    assert _amounts(result) == amounts
    assert not any(li["description"] == MADE_UP for li in result.output["line_items"])
    assert {"SUBTOTAL_MISMATCH", FOUND} <= _codes(result)


# --- One column for the whole table, never another figure of a row (B-2197) -----------------------------------------

COINCIDENCES = {
    "a number in the description": (AB, [("Description", 50), ("Amount", 440, "r"), ("GST", 550, "r")],
                                    [["Storage unit 95", "100.00", "5.00"], ["Storage unit 12", "100.00", "5.00"]]),
    "an item code": (AB, [("Code", 50), ("Description", 110), ("Amount", 440, "r"), ("GST", 550, "r")],
                     [["190", "Shelving, steel", "200.00", "10.00"], ["204", "Bin, large", "60.00", "3.00"]]),
    "a row number": (AB, [("#", 50), ("Description", 80), ("Amount", 440, "r"), ("GST", 550, "r")],
                     [[str(n), f"Locker rental, bay {chr(65 + n % 26)}", "20.00", "1.00"] for n in range(17, 21)]),
    "a count of days": (ON, [("Description", 50), ("Days", 330, "r"), ("Amount", 440, "r"), ("Tax", 550, "r")],
                        [["Scaffold rental", "87", "100.00", "13.00"], ["Fence rental", "30", "200.00", "26.00"]]),
    "a model number": (AB, [("Description", 50), ("Amount", 440, "r"), ("GST", 550, "r")],
                       [["Plotter model 1900", "2,000.00", "100.00"], ["Setup", "100.00", "5.00"]]),
}  # fmt: skip


@pytest.mark.parametrize("what", COINCIDENCES)
def test_a_figure_that_is_no_amount_is_never_the_amount_before_tax(what, tmp_path):
    """Row 19 of 20.00 + 1.00 GST read 19.00; "Storage unit 95" at 100.00 + 5.00 read 95.00; item code 190, 87 days
    and "model 1900" the same: any figure of the row that made the amount less its tax was taken, row by row."""
    _supplier, heads, rows = COINCIDENCES[what]
    read = _rows_read(tmp_path, heads, rows)
    assert [li.amount for li in read] == [float(row[-2].replace(",", "")) for row in rows]
    assert [li.taxes_read for li in read] == [{heads[-1][0].upper(): float(row[-1])} for row in rows]
    # (a row's number under "#" is no part of its description, with two digits as with one)
    assert not any(li.description.split()[0].isdigit() or li.description.split()[-1] == row[0]
                   for li, row in zip(read, rows, strict=True) if what == "a row number")  # fmt: skip


@ocr
@pytest.mark.parametrize("what", COINCIDENCES)
def test_a_figure_that_is_no_amount_is_never_the_amount_on_a_scan(what, pipe, tmp_path):
    supplier, heads, rows = COINCIDENCES[what]
    subtotal = sum(float(row[-2].replace(",", "")) for row in rows)
    totals = HST if supplier is ON else _gst(subtotal)
    result = pipe.process(_scan(_invoice(tmp_path, "S-1", supplier, heads, rows, totals)), save=False)
    assert _amounts(result) == [float(row[-2].replace(",", "")) for row in rows]
    assert not _codes(result) & {"SUBTOTAL_MISMATCH", FOUND}


def test_the_quantity_the_price_and_the_tax_itself_are_no_amount_before_tax(tmp_path):
    """A row of quantity 1 has its unit price + tax = its total, and a row whose total is twice its tax has its tax +
    tax = its total: neither is the line's amount, on that row alone."""
    heads = [("Description", 50), ("Qty", 300, "r"), ("Unit Price", 380, "r"), ("Tax", 460, "r"), ("Total", 550, "r")]
    rows = [["Network cabling", "4", "50.00", "26.00", "226.00"], ["Patch panel", "1", "100.00", "13.00", "113.00"]]
    assert [li.amount for li in _rows_read(tmp_path, heads, rows)] == [226.0, 113.0]
    heads = [("Description", 50), ("Tax", 440, "r"), ("Total", 550, "r")]
    rows = [["Gift basket", "13.00", "26.00"], ["Patch panel", "13.00", "113.00"]]
    assert [li.amount for li in _rows_read(tmp_path, heads, rows)] == [26.0, 113.0]


def test_thirty_rows_on_two_pages_keep_their_amounts_and_their_own_taxes(pipe, tmp_path):
    """Row 19 at 20.00 + 1.00 broke the reading of the whole table: the PDF's text table was taken in its place,
    which knows no tax column, and the three lines printed without GST were given it."""
    heads = [("#", 50), ("Description", 80), ("Qty", 330, "r"), ("Unit Price", 400, "r"), ("Amount", 460, "r"),
             ("GST", 505, "r"), ("Total", 560, "r")]  # fmt: skip
    items, exempt = _top("P-30", AB), (5, 12, 27)
    page, y = 1, 254
    items += [(x, 230, h, *al, 1) if al else (x, 230, h, "l", 1) for h, x, *al in heads]
    for n in range(1, 31):
        if n == 19:  # the second page: the headings again
            page, y = 2, 104
            items += [(x, 80, h, al[0] if al else "l", 2) for h, x, *al in heads]
        tax = 0.0 if n in exempt else 1.0
        row = [str(n), f"Locker rental, bay {chr(64 + (n - 1) % 26 + 1)}{n % 7}", "1", "20.00", "20.00",
               f"{tax:.2f}" if tax else "", f"{20 + tax:.2f}"]  # fmt: skip
        items += [
            (x, y, cell, al[0] if al else "l", page) for cell, (_, x, *al) in zip(row, heads, strict=True) if cell
        ]
        y += 22
    for k, (label, value) in enumerate([("Subtotal", "$600.00"), ("GST 5%", "$27.00"), ("Total", "$627.00")]):
        items += [(380, y + 20 + 16 * k, label, "l", 2), (560, y + 20 + 16 * k, value, "r", 2)]
    result = pipe.process(_pdf(tmp_path / "P-30.pdf", items, pages=2), save=False)
    assert _amounts(result) == [20.0] * 30
    assert [n for n, taxes in enumerate(_taxes(result), 1) if taxes != ["GST"]] == list(exempt)
    assert result.output["line_items"][0]["taxes_read"] == {"GST": 1.0}
    assert not _codes(result) & {"SUBTOTAL_MISMATCH", "TAX_CALC_MISMATCH", FOUND}


# --- Which heading names the amount (B-2198) -------------------------------------------------------------------------

DISCOUNTED = [["Office chair", "2", "125.00", "250.00", "50.00", "200.00"],
              ["Desk lamp", "1", "100.00", "100.00", "0.00", "100.00"]]  # fmt: skip


@pytest.mark.parametrize("before, after", [("Subtotal", "Total"), ("Net", "Amount")])
def test_the_amount_after_a_discount_column_is_the_lines_amount(before, after, pipe, tmp_path):
    """ "Qty | Price | Subtotal | Discount | Total" read 250.00 for 200.00, with "Charges not in the line table"
    -50.00 to hide it: "Subtotal" outranked "Total" in any table, with or without a tax column."""
    heads = [("Description", 50), ("Qty", 270, "r"), ("Price", 340, "r"), (before, 410, "r"), ("Discount", 480, "r"),
             (after, 550, "r")]  # fmt: skip
    assert [li.amount for li in _rows_read(tmp_path, heads, DISCOUNTED)] == [200.0, 100.0]
    result = pipe.process(_invoice(tmp_path, "N-2", ON, heads, DISCOUNTED, HST), save=False)
    assert _amounts(result) == [200.0, 100.0] and not _codes(result) & {"SUBTOTAL_MISMATCH", FOUND}


@ocr
def test_the_amount_after_a_discount_column_is_the_lines_amount_on_a_scan(pipe, tmp_path):
    heads = [("Description", 50), ("Qty", 270, "r"), ("Price", 340, "r"), ("Subtotal", 410, "r"),
             ("Discount", 480, "r"), ("Total", 550, "r")]  # fmt: skip
    result = pipe.process(_scan(_invoice(tmp_path, "S-12", ON, heads, DISCOUNTED, HST)), save=False)
    assert _amounts(result) == [200.0, 100.0] and not _codes(result) & {"SUBTOTAL_MISMATCH", FOUND}


def test_a_heading_that_says_before_tax_outranks_a_plain_one_only_beside_a_tax(tmp_path):
    """The whole precedence (``reader.line_items._amount_heading``), each rank on a table whose rows cannot decide."""
    weights = [["Crushed gravel, tonnes", "32.40", "12.40", "20.00", "10.00", "200.00"],
               ["Fill sand, tonnes", "22.50", "12.50", "10.00", "10.00", "100.00"]]  # fmt: skip
    ticket = [("Description", 50), ("Gross", 280, "r"), ("Tare", 340, "r"), ("Net", 400, "r"), ("Rate", 470, "r"),
              ("Amount", 550, "r")]  # fmt: skip
    assert [li.amount for li in _rows_read(tmp_path, ticket, weights)] == [200.0, 100.0]  # "Net" is a weight
    # Beside a tax column, the heading that says before tax; the tax is printed as a rate, so no row can prove it.
    rated = [["Network cabling", "226.00", "13%", "200.00"], ["Patch panel", "113.00", "13%", "100.00"]]
    heads = [("Description", 50), ("Total", 400, "r"), ("Tax", 470, "r"), ("Net", 550, "r")]
    assert [li.amount for li in _rows_read(tmp_path, heads, rated)] == [200.0, 100.0]
    heads = [("Description", 50), ("Net", 400, "r"), ("Tax %", 470, "r"), ("Total", 550, "r")]  # (a rate's heading)
    rated = [[d, net, rate, total] for d, total, rate, net in rated]
    assert [li.amount for li in _rows_read(tmp_path, heads, rated)] == [200.0, 100.0]
    # Never the heading that says the tax is in it while another could be the amount, whichever comes first.
    both = [["Network cabling", "226.00", "200.00"], ["Patch panel", "113.00", "100.00"]]
    for second in ("Amount", "Excl. tax"):
        heads = [("Description", 50), ("Total incl. tax", 440, "r"), (second, 550, "r")]
        assert [li.amount for li in _rows_read(tmp_path, heads, both)] == [200.0, 100.0], second
    # With no tax column and no discount, a plain "Total" over a "Subtotal" (quantity x list price, say).
    heads = [("Description", 50), ("Subtotal", 440, "r"), ("Total", 550, "r")]
    assert [li.amount for li in _rows_read(tmp_path, heads, both)] == [200.0, 100.0]


# --- Headings on the left, numbers on the right (B-2200) -------------------------------------------------------------

THREE = [["Server rack, 42U", "1,200.00", "156.00", "1,356.00"], ["Patch cords", "100.00", "13.00", "113.00"],
         ["Training day", "100.00", "", "100.00"]]  # fmt: skip


@pytest.mark.parametrize("width", [65, 75])
def test_under_left_aligned_headings_the_tax_is_not_the_amount(width, pipe, tmp_path):
    """ "Description | Tax | Total" as a spreadsheet prints it read the TAX as each line's amount (156.00, 13.00) and
    balanced it with a made-up charge of 1,131.00: the tax cells end nearer the "Total" heading than the totals do.
    Now each heading has its own column of numbers; the totals carry their tax, so the lines go to a person."""
    x3, x2, x1 = 570, 570 - width, 570 - 2 * width
    heads = [("Description", 50), _left("Tax", x1, x2), _left("Total", x2, x3)]
    rows = [[d, tax, total] for d, _net, tax, total in THREE]
    read = _rows_read(tmp_path, heads, rows)
    assert [(li.amount, li.taxes_read) for li in read] == [(1356.0, {"TAX": 156.0}), (113.0, {"TAX": 13.0}),
                                                           (100.0, {"TAX": None})]  # fmt: skip
    result = pipe.process(_invoice(tmp_path, "X-3", ON, heads, rows, HST3), save=False)
    _left_for_a_person(result, [1356.0, 113.0, 100.0])
    assert "include the tax" in _said(result)


@pytest.mark.parametrize("names", [("Base", "Tax", "Total"), ("Amount", "Tax", "Total"), ("Net", "HST", "Gross")])
@pytest.mark.parametrize("column", [_left, _centred])
@pytest.mark.parametrize("width", [70, 80, 100])
def test_headings_left_or_centred_over_right_aligned_numbers_read_each_cell_in_its_column(width, column, names, pipe,
                                                                                         tmp_path):  # fmt: skip
    """Left-aligned or centred headings over right-aligned numbers: the amount before tax, each line's own tax, and
    the line printed without tax exempt (it was "Charges not in the line table", or a taxed line read exempt)."""
    x3, x2, x1, x0 = 570, 570 - width, 570 - 2 * width, 570 - 3 * width
    heads = [("Description", 50), column(names[0], x0, x1), column(names[1], x1, x2), column(names[2], x2, x3)]
    result = pipe.process(_invoice(tmp_path, "M-1", ON, heads, THREE, HST3), save=False)
    assert _amounts(result) == [1200.0, 100.0, 100.0] and _taxes(result) == [["HST"], ["HST"], []]
    assert [li.get("taxes_read") for li in result.output["line_items"]] == [{"HST": 156.0}, {"HST": 13.0},
                                                                             {"HST": 0.0}]  # fmt: skip
    assert not _codes(result) & {"SUBTOTAL_MISMATCH", "TAX_BASE_MISMATCH", "EXEMPT_LINE_TAXED", FOUND}


@pytest.mark.parametrize("width", [65, 90])
def test_quantity_price_and_total_under_left_aligned_headings(width, pipe, tmp_path):
    """Without a tax column the PRICE was the amount (10.00 for 100.00); in columns 90 points wide still, the
    quantity's heading being too far from its numbers to have them."""
    x3, x2, x1, x0 = 570, 570 - width, 570 - 2 * width, 570 - 3 * width
    heads = [("Description", 50), _left("Qty", x0, x1), _left("Price", x1, x2), _left("Total", x2, x3)]
    rows = [["Server rack, 42U", "1", "1,200.00", "1,200.00"], ["Patch cords", "10", "10.00", "100.00"],
            ["Training day", "2", "50.00", "100.00"]]  # fmt: skip
    result = pipe.process(_invoice(tmp_path, "X-2", ON, heads, rows, HST3), save=False)
    assert _amounts(result) == [1200.0, 100.0, 100.0] and not _codes(result) & {"SUBTOTAL_MISMATCH", "LINE_MATH"}


def test_a_heading_never_takes_the_numbers_printed_wholly_to_its_left(tmp_path):
    """With a tax column no row fills, there is one column of numbers fewer than headings: each left-aligned heading
    took the column before its own (the numbers that end just before it), the amount's the unit prices."""
    heads = [("Description", 50), _left("Qty", 300, 360), _left("Price", 360, 430), _left("Amount", 430, 500),
             _left("Tax", 500, 570)]  # fmt: skip
    rows = [["Training day", "2", "50.00", "100.00", ""], ["Postage", "10", "1.20", "12.00", ""]]
    read = _rows_read(tmp_path, heads, rows)
    assert [(li.quantity, li.unit_price, li.amount) for li in read] == [(2.0, 50.0, 100.0), (10.0, 1.2, 12.0)]


def test_a_heading_over_tax_codes_or_numbers_among_words_take_no_column_of_amounts(tmp_path):
    """Under left-aligned headings, a "Tax" heading over code letters ("H" set close after each amount) stands
    nearer the amounts than "Ext. Price" does, and the numbers in the descriptions ("Unit 4", "case of 10") made
    columns of their own: the unit price was read as the amount (39.30 for 78.60). A heading over words has no
    column of numbers, and a number among words is no cell of one."""
    heads = [("#", 44), ("Item No.", 70), ("Description", 139), _left("QTY", 298, 354), _left("Unit Price", 354, 441),
             _left("Ext. Price", 441, 534), ("Tax", 537)]  # fmt: skip
    rows = [["1", "120572", "Janitorial service - Unit 4", "1", "229.44", "229.44", "H"],
            ["2", "551105", "Copy paper 8.5x11, case of 10", "2", "39.30", "78.60", "H"],
            ["3", "369745", "Consulting services - September", "10.50", "150.11", "1,576.16", "H"],
            ["4", "RS-24769", "Nitrile gloves, box of 100", "100", "9.89", "989.00", "H"]]  # fmt: skip
    read = _rows_read(tmp_path, heads, rows)
    assert [(li.quantity, li.unit_price, li.amount) for li in read] == [
        (1.0, 229.44, 229.44), (2.0, 39.3, 78.6), (10.5, 150.11, 1576.16), (100.0, 9.89, 989.0)]  # fmt: skip
    assert [li.taxes_read for li in read] == [None] * 4  # (letters under "Tax": codes, no amounts)


def test_a_discount_and_the_amount_after_it_are_no_amount_and_its_tax(tmp_path):
    """Before = discount + after has the shape of total = amount + tax: with the amount under the tax's heading, the
    discount column (0.00, 6.00) was "the amount before tax". A tax is smaller than the amount it is charged on."""
    heads = [("Description", 50), ("Credit", 380, "r"), ("GST", 460, "r"), ("Total", 550, "r")]
    rows = [["Membership, prorated", "1.00", "20.00", "21.00"], ["Locker", "2.00", "40.00", "42.00"]]
    assert [li.amount for li in _rows_read(tmp_path, heads, rows)] == [21.0, 42.0]


# --- No made-up balancing line (B-2199) ------------------------------------------------------------------------------

WITH_TAX = [["Network cabling", "26.00", "226.00"], ["Patch panel", "13.00", "113.00"]]
FREIGHT = [("Subtotal", "$300.00"), ("Freight", "$20.00"), ("HST 13%", "$41.60"), ("Total", "$361.60")]


def test_lines_with_their_tax_in_them_and_freight_under_the_subtotal_are_not_balanced(pipe, tmp_path):
    """ "Tax | Total" lines and "Freight $20.00" under the subtotal ended 226.00, 113.00 and "Charges not in the line
    table" -19.00, with no finding. The freight is a charge READ from the page and stays a line; nothing else is
    added."""
    heads = [("Description", 50), ("Tax", 440, "r"), ("Total", 550, "r")]
    result = pipe.process(_invoice(tmp_path, "B-3", ON, heads, WITH_TAX, FREIGHT), save=False)
    _left_for_a_person(result, [226.0, 113.0, 20.0])
    assert result.output["line_items"][2]["description"] == "Freight" and "include the tax" in _said(result)


@ocr
def test_lines_with_their_tax_in_them_and_freight_are_not_balanced_on_a_scan(pipe, tmp_path):
    heads = [("Description", 50), ("Tax", 440, "r"), ("Total", 550, "r")]
    result = pipe.process(_scan(_invoice(tmp_path, "S-16", ON, heads, WITH_TAX, FREIGHT)), save=False)
    _left_for_a_person(result, [226.0, 113.0, 20.0])


@pytest.mark.parametrize("price", ["Each", "List"])
def test_rows_proven_only_in_part_are_not_balanced_by_a_made_up_charge(price, pipe, tmp_path):
    """ "Qty | Each | Tax | Total": the row of quantity 1 has its price + tax = its total, the row of quantity 4 has
    not. It ended 226.00, 100.00 and "Charges not in the line table" -26.00."""
    heads = [("Description", 50), ("Qty", 300, "r"), (price, 380, "r"), ("Tax", 460, "r"), ("Total", 550, "r")]
    rows = [["Network cabling", "4", "50.00", "26.00", "226.00"], ["Patch panel", "1", "100.00", "13.00", "113.00"]]
    result = pipe.process(_invoice(tmp_path, "B-8", ON, heads, rows, HST), save=False)
    _left_for_a_person(result, [226.0, 113.0])
    assert "include the tax" in _said(result)


def test_a_line_whose_tax_cell_says_included_is_not_balanced_by_a_made_up_charge(pipe, tmp_path):
    heads = [("Description", 50), ("Amount", 440, "r"), ("Tax", 550, "r")]
    rows = [["Network cabling", "200.00", "26.00"], ["Parking (tax incl.)", "113.00", "incl."]]
    result = pipe.process(_invoice(tmp_path, "F-2", ON, heads, rows, HST), save=False)
    _left_for_a_person(result, [200.0, 113.0])
    assert "the lines add up to 313.00, the invoice says 300.00" in _said(result)


def test_lines_with_their_tax_in_them_and_no_tax_read_on_the_invoice_are_said(pipe, tmp_path):
    """ "Tax | Total" with the totals as a row of the table and "Amount due" under it: no tax and no subtotal is
    read, the lines add up to the total, and only "is the vendor registered?" was asked. The tax printed on the
    lines says their amounts include it."""
    heads = [("Description", 50), ("Tax", 470, "r"), ("Total", 550, "r")]
    rows = [*WITH_TAX, ["Total", "39.00", "339.00"]]
    result = pipe.process(_invoice(tmp_path, "K-6", ON, heads, rows, [("Amount due", "$339.00")]), save=False)
    assert _amounts(result) == [226.0, 113.0] and FOUND in _codes(result) and "include the tax" in _said(result)


def test_a_charge_printed_outside_the_table_under_its_own_label_is_still_a_line(pipe, tmp_path):
    """What stays: a charge READ from the page (its amount printed beside its own label)."""
    heads = [("Description", 50), ("Amount", 550, "r")]
    rows = [["Network cabling", "200.00"], ["Patch panel", "100.00"]]
    totals = [("Delivery & handling", "$20.00"), ("Subtotal", "$320.00"), ("HST 13%", "$41.60"), ("Total", "$361.60")]
    result = pipe.process(_invoice(tmp_path, "D-1", ON, heads, rows, totals), save=False)
    assert [(li["description"], li["amount"]) for li in result.output["line_items"]][2:] == [
        ("Delivery & handling", 20.0)]  # fmt: skip
    assert not _codes(result) & {"SUBTOTAL_MISMATCH", FOUND}


# --- A tax cell not found is "not read"; a column that names one tax says nothing of another (B-2201, B-2202) --------

PAPER = [
    ["Copy paper, case", "200.00", "10.00", "14.00", "224.00"],
    ["Courier service", "100.00", "5.00", "", "105.00"],
]
BOTH = [["GST", "PST"], ["GST"]]
TIGHT = [["Server rack, 42U", "1", "1,200.00", "1,200.00", "60.00", "84.00", "1,344.00"],
         ["Patch cords", "10", "10.00", "100.00", "5.00", "7.00", "112.00"],
         ["Courier service", "1", "100.00", "100.00", "5.00", "", "105.00"]]  # fmt: skip
WIDE = [["Server rack, 42U", "1,200.00", "60.00", "84.00"], ["Patch cords", "100.00", "5.00", "7.00"],
        ["Courier service", "100.00", "5.00", ""]]  # fmt: skip
PAPIER = [["Papier, caisse", "4", "50,00", "200,00", "10,00", "19,95", "229,95"],
          ["Service de messagerie", "1", "100,00", "100,00", "5,00", "9,98", "114,98"]]  # fmt: skip
READ_RIGHT = {
    "French columns 45 points apart": (QC, [("Description", 50), ("Qté", 280, "r"), ("Prix unitaire", 350, "r"),
                                            ("Montant", 420, "r"), ("TPS", 470, "r"), ("TVQ", 515, "r"),
                                            ("Total", 560, "r")], PAPIER, QCT, [200.0, 100.0], [["GST", "QST"]] * 2),
    "thousands set apart by a space": (QC, [("Description", 50), ("Montant", 420, "r"), ("TPS", 470, "r"),
                                            ("TVQ", 515, "r"), ("Total", 560, "r")],
                                       [["Serveur lame", "1 200,00", "60,00", "119,70", "1 379,70"],
                                        ["Installation", "2 400,00", "120,00", "239,40", "2 759,40"]],
                                       [("Sous-total", "3 600,00 $"), ("TPS 5 %", "180,00 $"),
                                        ("TVQ 9,975 %", "359,10 $"), ("Total", "4 139,10 $")],
                                       [1200.0, 2400.0], [["GST", "QST"]] * 2),
    "headings that touch": (QC, [("Désignation", 50), ("Montant HT", 380, "r"), ("TPS", 440, "r"), ("TVQ", 500, "r"),
                                 ("Montant TTC", 560, "r")], [row[:1] + row[3:] for row in PAPIER], QCT,
                            [200.0, 100.0], [["GST", "QST"]] * 2),
    "seven tight columns": (BC, [("Description", 50), ("Qty", 290, "r"), ("Price", 340, "r"), ("Amount", 395, "r"),
                                 ("GST", 440, "r"), ("PST", 485, "r"), ("Total", 540, "r")], TIGHT, BCT3,
                            [1200.0, 100.0, 100.0], [*BOTH[:1] * 2, ["GST"]]),
    "the total first": (BC, [("Description", 50), ("Qty", 290, "r"), ("Price", 340, "r"), ("Total", 395, "r"),
                             ("GST", 440, "r"), ("PST", 485, "r"), ("Net", 540, "r")],
                        [[*row[:3], row[6], row[4], row[5], row[3]] for row in TIGHT], BCT3,
                        [1200.0, 100.0, 100.0], [*BOTH[:1] * 2, ["GST"]]),
    "wide centred tax headings": (BC, [("Description", 50), _centred("Amount", 330, 410),
                                       _centred("GST amount", 410, 490), _centred("PST amount", 490, 570)], WIDE, BCT3,
                                  [1200.0, 100.0, 100.0], [*BOTH[:1] * 2, ["GST"]]),
    "a PST column alone": (BC, [("Description", 50), ("Amount", 440, "r"), ("PST", 550, "r")],
                           [["Copy paper, case", "200.00", "14.00"], ["Courier service", "100.00", "0.00"]], BCT,
                           [200.0, 100.0], BOTH),
    "a TVQ column alone": (QC, [("N°", 50), ("Désignation", 90), ("Qté", 330, "r"), ("Prix unitaire", 410, "r"),
                                ("TVQ", 470, "r"), ("Montant HT", 560, "r")],
                           [["1", "Papier, caisse", "4", "50,00", "19,95", "200,00"],
                            ["2", "Service de messagerie", "1", "100,00", "9,98", "100,00"]], QCT,
                           [200.0, 100.0], [["GST", "QST"]] * 2),
    "GST/HST and PST columns": (BC, [("Description", 50), ("Amount", 380, "r"), ("GST/HST", 440, "r"),
                                     ("PST", 500, "r"), ("Total", 560, "r")], PAPER, BCT, [200.0, 100.0], BOTH),
    "Tax and PST columns": (BC, [("Description", 50), ("Amount", 380, "r"), ("Tax", 440, "r"), ("PST", 500, "r"),
                                 ("Total", 560, "r")], PAPER, BCT, [200.0, 100.0], BOTH),
    "a GST column, the invoice's tax HST": (ON, [("Description", 50), ("Amount", 410, "r"), ("GST", 480, "r"),
                                                 ("Total", 550, "r")],
                                            [["Network cabling", "200.00", "26.00", "226.00"],
                                             ["Patch panel", "100.00", "13.00", "113.00"]], HST,
                                            [200.0, 100.0], [["HST"]] * 2),
}  # fmt: skip


@pytest.mark.parametrize("what", READ_RIGHT)
def test_a_tax_cell_not_found_or_a_tax_no_column_names_exempts_no_line(what, pipe, tmp_path):
    """Each of these read right before the tax columns were read, by the amounts alone; with them, a tax cell set
    close to the total, one under a wide or a touching heading, or a tax no column names made a taxed line exempt
    (EXEMPT_LINE_TAXED, TAX_NOT_ALLOCATED, TAX_BASE_MISMATCH: "ask the vendor for a corrected invoice")."""
    supplier, heads, rows, totals, amounts, taxes = READ_RIGHT[what]
    result = pipe.process(_invoice(tmp_path, "C-1", supplier, heads, rows, totals), save=False)
    assert _amounts(result) == amounts and _taxes(result) == taxes
    assert not _codes(result) & {"EXEMPT_LINE_TAXED", "TAX_NOT_ALLOCATED", "TAX_BASE_MISMATCH", "LINE_TAX_NOT_CHARGED",
                                 "SUBTOTAL_MISMATCH", FOUND}  # fmt: skip


def test_what_a_rows_tax_cells_say_tax_by_tax(tmp_path):
    """The reader keeps each tax amount printed on a row under the tax its column names, None where it found no
    cell: zero only where it is printed, or where the row's own total proves it. What they say of the line's tax
    code depends on the taxes the invoice charges (``bridge.printed_taxes``)."""
    from ap_coder.capture.bridge import printed_taxes

    heads = [("Description", 50), ("Amount", 380, "r"), ("GST", 440, "r"), ("PST", 500, "r"), ("Total", 560, "r")]
    rows = [*PAPER, ["Gift card", "50.00", "", "", "50.00"], ["Postage", "10.00", "0.50", "0.00", "10.50"]]
    read = _rows_read(tmp_path, heads, rows)
    assert [li.taxes_read for li in read] == [{"GST": 10.0, "PST": 14.0}, {"GST": 5.0, "PST": 0.0},
                                              {"GST": 0.0, "PST": 0.0}, {"GST": 0.5, "PST": 0.0}]  # fmt: skip
    assert [li.tax_flags for li in read] == [""] * 4  # (the tax-code column's text only)
    charged = {"GST": 15.5, "PST": 14.0}
    assert printed_taxes(read, charged) == [
        ("GST/PST", {"GST": 10.0, "PST": 14.0}), ("GST", {"GST": 5.0, "PST": 0.0}),
        ("Exempt", {"GST": 0.0, "PST": 0.0}), ("GST", {"GST": 0.5, "PST": 0.0})]  # fmt: skip
    # Without the row's total nothing proves a blank cell: not read, until the invoice's own tax line does.
    read = _rows_read(tmp_path, heads[:-1], [row[:-1] for row in rows])
    assert [li.taxes_read for li in read][1:3] == [{"GST": 5.0, "PST": None}, {"GST": None, "PST": None}]
    assert [flags for flags, _ in printed_taxes(read, charged)] == ["GST/PST", "GST", "Exempt", "GST"]
    assert [flags for flags, _ in printed_taxes(read, {"GST": 15.5, "PST": 21.0})] == ["GST/PST", "", "", "GST"]
    # A column that names one tax says nothing of another: the GST column on an invoice that also charges PST.
    gst_only = _rows_read(tmp_path, heads[:3], [row[:3] for row in rows])
    assert printed_taxes(gst_only, charged)[0] == ("", {"GST": 10.0})
    # ... and is the invoice's HST where that is the tax charged; "Tax" on a two-tax invoice names neither.
    assert printed_taxes(gst_only, {"HST": 15.5})[0] == ("HST", {"HST": 10.0})
    plain = _rows_read(tmp_path, [*heads[:2], ("Tax", 440, "r")], [row[:3] for row in rows])
    assert printed_taxes(plain, {"GST": 15.5})[0] == ("GST", {"GST": 10.0})
    assert printed_taxes(plain, charged)[0] == ("", None)


# --- A row's discount explains its amount; the supplier's account is no line (B-2269, B-2270) ------------------------

PRICED = [("Description", 50), ("Qty", 300, "r"), ("Unit Price", 380, "r"), ("Discount", 460, "r"),
          ("Amount", 550, "r")]  # fmt: skip


@pytest.mark.parametrize("discount", ["10.00", "10%"])
def test_a_rows_discount_explains_its_amount_and_no_line_math_is_raised(discount, pipe, tmp_path):
    """ "Qty 2 | Unit Price 50.00 | Discount 10.00 | Amount 90.00" read the amount right and raised "quantity × unit
    price = 100.00 but the amount is 90.00": the unit price is now the price after the row's discount."""
    rows = [["Courier service downtown", "2", "50.00", discount, "90.00"],
            ["Courier service airport", "1", "200.00", "0.00", "200.00"]]  # fmt: skip
    read = _rows_read(tmp_path, PRICED, rows)
    assert [(li.quantity, li.unit_price, li.amount) for li in read] == [(2.0, 45.0, 90.0), (1.0, 200.0, 200.0)]
    result = pipe.process(_invoice(tmp_path, "L-1", AB, PRICED, rows, _gst(290.0)), save=False)
    assert _amounts(result) == [90.0, 200.0] and "LINE_MATH" not in _codes(result)


def test_a_discount_that_does_not_explain_the_amount_still_raises_line_math(pipe, tmp_path):
    rows = [["Courier service downtown", "2", "50.00", "10.00", "80.00"],
            ["Courier service airport", "1", "200.00", "0.00", "200.00"]]  # fmt: skip
    assert [li.unit_price for li in _rows_read(tmp_path, PRICED, rows)] == [50.0, 200.0]
    result = pipe.process(_invoice(tmp_path, "L-2", AB, PRICED, rows, _gst(280.0)), save=False)
    assert "LINE_MATH" in _codes(result)


ACCOUNT = [["Previous balance", "500.00"], ["Payment received - thank you", "-500.00"]]
FORWARD = [["Balance forward", "500.00"], ["Payment - thank you", "-500.00"]]


@pytest.mark.parametrize("rows", [ACCOUNT, ACCOUNT[:1], FORWARD])
def test_the_suppliers_account_in_the_table_is_no_line_and_one_finding_names_its_rows(rows, pipe, tmp_path):
    """ "Previous balance 500.00" and "Payment received -500.00" were read as lines and coded: they are the
    supplier's account, as on a statement. They are left out and one info finding names them; a charge for paying
    late is a line."""
    from ap_coder.ops.help import CHECKS

    heads = [("Description", 50), ("Amount", 550, "r")]
    table = [*rows, ["Courier service downtown", "300.00"], ["Late payment charge", "20.00"]]
    left: list = []
    pdf = _invoice(tmp_path, "M-7", AB, heads, table, _gst(320.0))
    read = read_line_items(layout.build_layout(pdf), left_out=left)
    assert [(li.description, li.amount) for li in read] == [("Courier service downtown", 300.0),
                                                            ("Late payment charge", 20.0)]  # fmt: skip
    assert [(li.description, li.amount) for li in left] == [(words, float(amount)) for words, amount in rows]
    result = pipe.process(pdf, save=False)
    assert _amounts(result) == [300.0, 20.0] and not _codes(result) & {"SUBTOTAL_MISMATCH", FOUND}
    (said,) = [i for i in result.report.issues if i.code == "ACCOUNT_ROWS_LEFT_OUT"]
    assert said.severity == "info" and all(words in said.message and amount in said.message for words, amount in rows)
    assert "ACCOUNT_ROWS_LEFT_OUT" in CHECKS
