"""Real-world vendors and totals (sweep 4): what public sample invoices taught about the supplier's name and the
subtotal / taxes / total on invoices whose totals are laid out as a summary table. Every fixture is generated here."""

from pathlib import Path

import pymupdf
import pytest

from ap_coder.capture import analyze, build_layout, read_fields
from ap_coder.capture.confidence import fuse


def _pdf(path: Path, cells: list[tuple], pages: int = 1) -> Path:
    """A digital PDF: each (x, y, text[, font size]) cell set at that point (in points, letter size); cells with
    y > 792 go on the next page."""
    doc = pymupdf.open()
    for n in range(pages):
        page = doc.new_page(width=612, height=792)
        for x, y, text, *size in cells:
            if n * 792 <= y < (n + 1) * 792:
                page.insert_text((x, y - n * 792), text, fontsize=size[0] if size else 9)
    doc.save(path)
    return path


# A summary of charges laid out as a table: a row of total labels over one row per kind of charge, and a last row,
# unlabelled, with the column totals (a postal or courier invoice's first page).
SUMMARY_HEAD = [
    (270, 290, "Subtotal"),
    (345, 290, "GST"),
    (380, 290, "HST"),
    (460, 290, "PST/QST"),
    (540, 290, "Total"),
]
SUMMARY_ROWS = [
    ("Parcels", "72.05", "3.60", "", "", "75.65"),
    ("Marketing mail", "270.00", "", "35.10", "", "305.10"),
    ("Special services", "1,250.00", "62.50", "", "", "1,312.50"),
    ("Adjustments", "-40.00", "", "", "", "-40.00"),
]
SUMMARY_FOOT = ("1,552.05", "66.10", "35.10", "", "1,653.25")


def _summary_invoice(path: Path, rows=SUMMARY_ROWS, foot=SUMMARY_FOOT, extra=()) -> Path:
    cells = [(60, 60, "Northern Parcel Service"), (420, 60, "Invoice number 7700012"),
             (420, 75, "Invoice date 2026-06-21"), (60, 250, "Summary of charges"),
             (60, 270, "Total amount of this invoice"), (240, 270, "$ " + foot[-1]), *SUMMARY_HEAD, *extra]  # fmt: skip
    y = 305
    for row in rows:
        cells.append((60, y, row[0]))
        for x, value in zip((270, 340, 380, 460, 540), row[1:], strict=True):
            if value:
                cells.append((x, y, value))
        y += 13
    for x, value in zip((270, 340, 380, 460, 540), foot, strict=True):
        if value:
            cells.append((x, y + 40, value))
    return _pdf(path, cells)


def test_a_summary_tables_column_totals_are_the_invoice_totals_not_its_first_row(tmp_path):
    # Canada Post's invoice (sweep 4): "Subtotal | GST | HST | PST/QST | Total" over a row per kind of charge. The
    # first row under each label (Parcels: 72.05 + 3.60 = 75.65) added up, so a section's figures were verified as
    # the invoice's subtotal, GST and total. The column's foot, the sum of the rows above it, is the invoice's.
    result = analyze(_summary_invoice(tmp_path / "summary.pdf"), tables=False)
    f = result.fields
    assert f["subtotal"].value == 1552.05
    assert f["gst_amount"].value == 66.10
    assert f["hst_amount"].value == 35.10
    assert f["grand_total"].value == 1653.25
    assert all(f[n].value not in (72.05, 3.60, 75.65) for n in ("subtotal", "gst_amount", "grand_total"))


def test_a_column_whose_foot_is_not_its_sum_gives_no_total(tmp_path):
    # The same table without its totals row: nothing under a label is the invoice's subtotal or total then.
    cells = [(60, 60, "Northern Parcel Service"), *SUMMARY_HEAD]
    y = 305
    for row in SUMMARY_ROWS[:3]:
        for x, value in zip((270, 340, 380, 460, 540), row[1:], strict=True):
            if value:
                cells.append((x, y, value))
        y += 13
    cands = read_fields(build_layout(_pdf(tmp_path / "rows.pdf", cells)))
    assert 72.05 not in [r.value for r in cands.get("subtotal", [])]
    assert 75.65 not in [r.value for r in cands.get("grand_total", [])]


# The same summary with the QST in the "PST/QST" column (a Quebec supplier's invoice).
QC_ROWS = [
    ("Parcels", "100.00", "5.00", "", "9.98", "114.98"),
    ("Special services", "200.00", "10.00", "", "19.95", "229.95"),
]
QC_FOOT = ("300.00", "15.00", "", "29.93", "344.93")


def test_a_pst_or_qst_column_is_the_qst_when_the_supplier_prints_its_qst_number(tmp_path):
    reg = [(60, 500, "Registration nos.: GST 119321495; QST 1006393299")]
    f = analyze(_summary_invoice(tmp_path / "qc.pdf", QC_ROWS, QC_FOOT, reg), tables=False).fields
    assert (f["subtotal"].value, f["gst_amount"].value, f["grand_total"].value) == (300.0, 15.0, 344.93)
    assert f["qst_amount"].value == 29.93 and f["pst_amount"].status == "missing"


def test_a_pst_or_qst_column_the_page_does_not_settle_is_never_verified(tmp_path):
    f = analyze(_summary_invoice(tmp_path / "either.pdf", QC_ROWS, QC_FOOT), tables=False).fields
    (tax,) = [f[n] for n in ("pst_amount", "qst_amount") if f[n].status != "missing"]
    assert tax.value == 29.93 and tax.status != "verified"
    assert any("PST/QST" in r for r in tax.reasons)


# --- the supplier's name ------------------------------------------------------------------------------------------


def _vendor(path: Path, cells: list[tuple[float, float, str]], pages: int = 1) -> str | None:
    cands = read_fields(build_layout(_pdf(path, cells, pages))).get("vendor_name") or []
    return cands[0].value if cands else None


def test_a_heading_or_title_is_not_the_supplier_but_the_name_with_its_registration_numbers_is(tmp_path):
    # Canada Post (sweep 4): its name is in its logo, a picture; "Summary of charges" (French "Sommaire des frais"),
    # the biggest text at the top, was read as the supplier. The page names it with its registration numbers.
    cells = [(60, 200, "Summary of charges"), (420, 60, "Invoice number 7700012"), (420, 140, "Contact us"),
             (60, 270, "Total amount of this invoice"), (240, 270, "$ 1,653.25"),
             (60, 520, "Northern Parcel registration nos.: HST/GST 123456789; QST 1234567890")]  # fmt: skip
    assert _vendor(tmp_path / "en.pdf", cells) == "Northern Parcel"
    cells_fr = [(60, 200, "Sommaire des frais"), (420, 140, "Contactez-nous"),
                (60, 520, "Numéros d'enregistrement de Colis Boréal TVH/TPS 123456789; TVQ 1234567890")]  # fmt: skip
    assert _vendor(tmp_path / "fr.pdf", cells_fr) == "Colis Boréal"
    titled = [(150, 60, "SAMPLE INVOICE WITH REQUIRED INFORMATION"), (60, 100, "From: Lions and Co."),
              (60, 114, "4700 Keele St, Toronto ON M3J 1P3")]  # fmt: skip
    assert _vendor(tmp_path / "title.pdf", titled) == "Lions and Co."


def test_a_city_a_staff_line_or_a_placeholder_is_not_the_supplier(tmp_path):
    # Receipts photographed in Canada (WildReceipt): "VAUGHAN ON", "Montreal,Quebec", "Server: Bhomik" were read as
    # the supplier; a form's "Company Name" and "Registered Business Name" too.
    for n, junk in enumerate(["VAUGHAN ON", "Montreal,Quebec", "MONTREAL", "Server: Bhomik", "Company Name",
                              "Registered Business Name", "Table 23"]):  # fmt: skip
        cells = [(60, 40, junk), (60, 60, "Maple Leaf Hardware"), (60, 74, "GST/HST 123456789RT0001")]
        assert _vendor(tmp_path / f"r{n}.pdf", cells) == "Maple Leaf Hardware", junk


def test_the_name_under_a_sellers_label_is_the_supplier(tmp_path):
    # Factur-X / ZUGFeRD samples print "Verkäufer" (seller) or "Lieferant" over the supplier's name.
    cells = [(60, 60, "Verkäufer"), (60, 74, "Lieferant GmbH"), (60, 88, "Lieferantenstraße 20"),
             (320, 60, "Käufer"), (320, 74, "Kunden AG Mitte")]  # fmt: skip
    assert _vendor(tmp_path / "de.pdf", cells) == "Lieferant GmbH"
    # The sender's line over the envelope's window (Hetzner's invoice): the customer's address under it is no name.
    sender = [(60, 100, "Nordlicht Hosting GmbH • Industriestr. 25 • 91710 Gunzenhausen"),
              (60, 114, "Usegroup Inh. Jochen Stärk"), (60, 128, "Huswertstr. 14"),
              (60, 142, "60435 Frankfurt")]  # fmt: skip
    assert _vendor(tmp_path / "sender.pdf", sender) == "Nordlicht Hosting GmbH"


def test_a_stores_brand_and_its_operators_name_leave_the_supplier_unverified(tmp_path):
    # A Canadian Tire receipt (WildReceipt): "CANADIAN TIRE 235" at the top, "Gestions Daniel Picard Inc" (the store's
    # operator) over its GST number. Two OCR reads agreed on the operator and verified it; the receipt's supplier is
    # the store. Which one AP pays, the page does not say: a person (or the vendor master) does.
    cells = [(150, 40, "MAPLE AUTO 112", 13), (150, 56, "11200 BOUL. CAVENDISH"), (150, 70, "Ville St-Laurent H4R 2J7"),
             (60, 84, "Gestions Exemple Inc"), (60, 98, "TPS: 123456782RT0001 TVQ: 1234567890TQ0001"),
             (60, 200, "SOUS-TOTAL"), (300, 200, "33.79"), (60, 214, "TPS 5%"), (300, 214, "1.69"),
             (60, 228, "TVQ 9.975%"), (300, 228, "3.37"), (60, 242, "TOTAL"), (300, 242, "38.85")]  # fmt: skip
    cands = read_fields(build_layout(_pdf(tmp_path / "store.pdf", cells)))
    fields, _ = fuse({"rules": cands, "ocr2": cands}, [])  # two OCR engines reading the receipt alike
    fr = fields["vendor_name"]
    assert fr.value == "Gestions Exemple Inc" and fr.status != "verified"
    assert any("another name" in r for r in fr.reasons)
    # The real receipt prints its header twice: the brand is also below the operator's name.
    below = [(60, 40, "Gestions Exemple Inc"), (60, 54, "TPS: 123456782RT0001"), (150, 68, "MAPLE AUTO 112", 13),
             (60, 200, "TOTAL"), (300, 200, "38.85")]  # fmt: skip
    cands = read_fields(build_layout(_pdf(tmp_path / "below.pdf", below)))
    fr = fuse({"rules": cands, "ocr2": cands}, [])[0]["vendor_name"]
    assert fr.status != "verified" and any("another name" in r for r in fr.reasons)


# --- totals: tax rows, several rates, labels in other languages, amounts across the page --------------------------


def _amounts(path: Path, cells: list[tuple[float, float, str]]) -> dict:
    f = analyze(_pdf(path, cells), tables=False).fields
    return {n: f[n].value for n in ("subtotal", "tax_total", "grand_total") if f[n].status != "missing"}


def test_the_total_tax_the_totals_add_up_with_is_the_one_read(tmp_path):
    # Factur-X samples (sweep 4): a VAT breakdown ("Umsatzsteuer (S) | 275,00 | 7 | 19,25") over the totals block
    # ("Steuerbetrag 56,87"). The solver found that 473,00 + 56,87 = 529,87 but kept the breakdown's first figure as
    # the total tax: the subtotal and total were then failed for a tax that was not theirs.
    cells = [(60, 60, "Lieferant GmbH"), (60, 300, "Umsatzsteuer ( S )"), (380, 300, "275,00"), (470, 300, "7"),
             (530, 300, "19,25"), (60, 320, "Umsatzsteuer ( S )"), (380, 320, "198,00"), (470, 320, "19"),
             (530, 320, "37,62"), (220, 380, "Rechnungssumme ohne USt."), (530, 380, "473,00"),
             (220, 400, "Steuerbetrag"), (530, 400, "56,87"), (220, 420, "Bruttosumme"),
             (530, 420, "529,87")]  # fmt: skip
    assert _amounts(tmp_path / "vat.pdf", cells) == {"subtotal": 473.0, "tax_total": 56.87, "grand_total": 529.87}


def test_vat_at_two_rates_with_no_total_of_it_is_their_sum(tmp_path):
    cells = [(60, 60, "Lieferant GmbH"), (60, 300, "Rechnungssumme Netto (excl. USt.)"), (530, 300, "473,00"),
             (60, 320, "Steuerbasisbetrag USt. 7%"), (480, 320, "275,00"), (530, 320, "19,25"),
             (60, 340, "Steuerbasisbetrag USt. 19%"), (480, 340, "198,00"), (530, 340, "37,62"),
             (60, 360, "Rechnungssumme Brutto (inkl. USt.)"), (530, 360, "529,87")]  # fmt: skip
    assert _amounts(tmp_path / "two.pdf", cells) == {"subtotal": 473.0, "tax_total": 56.87, "grand_total": 529.87}


@pytest.mark.parametrize("labels", [
    ("WA State Sales Tax 6.5%", "Spokane County Sales Tax 0.5%", "City of Spokane Sales Tax 1.5%",
     "Transit District Tax 0.6%"),
    ("State Tax 6.5%", "County Tax 0.5%", "City Tax 1.5%", "Transit Tax 0.6%"),
])  # fmt: skip
@pytest.mark.parametrize("rows", [3, 4])
def test_a_us_state_county_and_city_sales_tax_with_no_total_of_them_is_their_sum(tmp_path, labels, rows):
    """B-2870: a US invoice's state, county and city sales tax, each on a row of its own and no total tax printed: only
    two amounts were ever summed, so one of the three was read as the whole tax (subtotal + tax not the total), or
    none was."""
    taxes = ("81.25", "6.25", "18.75", "7.50")[:rows]
    total = 1250 + sum(float(t) for t in taxes)
    cells = [(60, 60, "Cascade Bearing & Drive Inc"), (60, 74, "401 Industrial Way, Spokane, WA 99202"),
             (330, 300, "Subtotal"), (490, 300, "1,250.00"),
             *[(330, 315 + 15 * n, labels[n]) for n in range(rows)],
             *[(490, 315 + 15 * n, taxes[n]) for n in range(rows)],
             (330, 315 + 15 * rows, "Total Due (USD)"), (490, 315 + 15 * rows, f"${total:,.2f}")]  # fmt: skip
    assert _amounts(tmp_path / "us.pdf", cells) == {"subtotal": 1250.0, "tax_total": round(total - 1250, 2),
                                                    "grand_total": total}  # fmt: skip


_CANADIAN_HEAD = [(60, 60, "Northern Supply Ltd."), (60, 74, "200 Ridge Road SW, Calgary AB T2P 1J9"),
                  (400, 60, "INVOICE"), (400, 74, "Invoice No: NS-1001"), (400, 88, "Invoice Date: 2026-05-04"),
                  (60, 120, "GST/HST No. 123456782RT0001")]  # fmt: skip


def _lines(rows: list[tuple[str, ...]]) -> list[tuple]:
    """A line table from y 180: a description and an amount, or a description, quantity, rate and amount."""
    wide = len(rows[0]) == 4
    head = [(60, 164, "Description"), (490, 164, "Amount")] + ([(330, 164, "Qty"), (400, 164, "Rate")] if wide else [])
    cells = []
    for n, row in enumerate(rows):
        xs = (60, 330, 400, 490) if wide else (60, 490)
        cells += [(x, 180 + 14 * n, text) for x, text in zip(xs, row, strict=True)]
    return head + cells


def _totals(rows: list[tuple[str, str]]) -> list[tuple]:
    return [cell for n, (label, value) in enumerate(rows) for cell in ((330, 320 + 15 * n, label),
                                                                       (490, 320 + 15 * n, value))]  # fmt: skip


@pytest.mark.parametrize(("lines", "totals", "tax"), [
    ([("US state tax return preparation, 2025", "1,500.00")], [("Subtotal", "1,500.00"), ("GST 5%", "75.00"),
                                                               ("Total", "1,575.00")], 75.0),
    ([("Professional fees", "1,000.00"), ("City of Vancouver tax certificate", "57.00")],
     [("Subtotal", "1,057.00"), ("GST 5%", "50.00"), ("PST 7%", "70.00"), ("Total", "1,177.00")], 120.0),
    ([("Legal services re purchase of 12 Elm St", "1,200.00"), ("Town of Oakville tax certificate", "75.00")],
     [("Subtotal", "1,275.00"), ("HST 13%", "156.00"), ("Total", "1,431.00")], 156.0),
    ([("County tax search", "45.00"), ("Title search", "30.00")], [("Subtotal", "75.00"), ("GST 5%", "3.75"),
                                                                    ("Total", "78.75")], 3.75),
    ([("Local tax services", "300.00"), ("State tax filing review", "200.00"), ("City tax consulting", "100.00")],
     [("Subtotal", "600.00"), ("GST 5%", "30.00"), ("Total", "630.00")], 30.0),
    ([("County tax services", "300.00"), ("City tax services", "200.00"), ("Local tax services", "100.00")],
     [("Subtotal", "600.00"), ("HST 13%", "78.00"), ("Total", "678.00")], 78.0),
    ([("T1 personal return 2025", "1", "650.00", "650.00"), ("US state tax return (Arizona)", "1", "450.00", "450.00"),
      ("Bookkeeping", "4", "100.00", "400.00")], [("Subtotal", "1,500.00"), ("GST 5%", "75.00"),
                                                  ("Total", "1,575.00")], 75.0),
    ([("Legal fees: purchase of 1234 Main St", "1", "1,200.00", "1,200.00"),
      ("City of Surrey tax certificate", "1", "57.00", "57.00"), ("Land title search", "2", "11.00", "22.00")],
     [("Subtotal", "1,279.00"), ("GST 5%", "63.95"), ("PST 7%", "84.00"), ("Total", "1,426.95")], 147.95),
    ([("Legal fees re sale of 55 King St", "1", "1,500.00", "1,500.00"),
      ("Town of Milton tax certificate", "1", "75.00", "75.00"), ("Courier", "1", "25.00", "25.00")],
     [("Subtotal", "1,600.00"), ("HST 13%", "208.00"), ("Total", "1,808.00")], 208.0),
])  # fmt: skip
def test_a_line_items_words_about_a_tax_are_never_its_tax_total(tmp_path, lines, totals, tax):
    """B-2874: the US state, county and city sales tax labels of B-2870 were labels wherever a line started with
    them, so a Canadian invoice's line items ("US state tax return preparation, 2025", "City of Vancouver tax
    certificate", "County tax search", "Local tax services") were its tax total (the year, the line's amount or its
    quantity), and the line table ended at them: TOTAL_MISMATCH on clean GST/HST invoices. Such a row is read as a tax
    only in the totals block, under the subtotal, its amount alone on its row."""
    read = analyze(_pdf(tmp_path / "ca.pdf", _CANADIAN_HEAD + _lines(lines) + _totals(totals)))
    f = read.fields
    subtotal, total = (float(dict(totals)[k].replace(",", "")) for k in ("Subtotal", "Total"))
    assert (f["subtotal"].value, f["tax_total"].value, f["grand_total"].value) == (subtotal, tax, total)
    assert f["tax_total"].status != "check" and f["subtotal"].status != "check"
    assert [li.description for li in read.line_items] == [line[0] for line in lines]  # (no line cut off at it)


def test_a_discount_and_freight_between_two_subtotals_are_read_and_the_subtotal_is_not_verified(tmp_path):
    # Factur-X "Rabatte" sample: "Zwischensumme 202,76", "abzgl. Sondernachlass 10% von 147,30 -14,73", "zzgl.
    # Versandkosten 5,80 7", "Rechnungssumme Netto 193,83". The totals add up with either subtotal; 193,83 was verified.
    cells = [(60, 60, "Lieferant GmbH"), (180, 300, "Zwischensumme"), (530, 300, "202,76"),
             (180, 314, "abzgl. Sondernachlass"), (340, 314, "10%"), (390, 314, "von"), (460, 314, "147,30"),
             (530, 314, "-14,73"), (180, 328, "zzgl. Versandkosten"), (535, 328, "5,80"), (580, 328, "7"),
             (60, 350, "Rechnungssumme Netto (exkl. USt.)"), (530, 350, "193,83"),
             (180, 364, "Summe Umsatzsteuer"), (535, 364, "21,31"),
             (60, 378, "Rechnungssumme Brutto (inkl. USt.)"), (530, 378, "215,14")]  # fmt: skip
    f = analyze(_pdf(tmp_path / "rabatte.pdf", cells), tables=False).fields
    assert (f["subtotal"].value, f["other_charges"].value, f["grand_total"].value) == (202.76, -8.93, 215.14)
    assert f["subtotal"].status != "verified"


def test_a_note_about_vat_is_no_tax_amount(tmp_path):
    # With "VAT" a label (B-327), the note "VAT 0% - export of goods outside the EU" took the figure under it.
    cells = [(60, 60, "Acme Exports Ltd."), (60, 300, "VAT 0% - export of goods outside the EU"), (60, 314, "8004"),
             (380, 340, "Total"), (500, 340, "1,250.00")]  # fmt: skip
    assert not read_fields(build_layout(_pdf(tmp_path / "note.pdf", cells))).get("tax_total")


def test_totals_labelled_in_german_dutch_and_french_are_read(tmp_path):
    de = [(60, 60, "Nordlicht Hosting GmbH"), (440, 300, "Netto"), (500, 300, "87,39 EUR"), (430, 314, "USt. (19 %)"),
          (500, 314, "16,61 EUR"), (440, 328, "Brutto"), (500, 328, "104,00 EUR")]  # fmt: skip
    assert _amounts(tmp_path / "de.pdf", de) == {"subtotal": 87.39, "tax_total": 16.61, "grand_total": 104.0}
    nl = [(60, 60, "Contoso B.V."), (380, 300, "Subtotaal"), (500, 300, "100,00"), (380, 314, "BTW 21%"),
          (500, 314, "21,00"), (380, 328, "Totaal"), (500, 328, "121,00")]  # fmt: skip
    assert _amounts(tmp_path / "nl.pdf", nl) == {"subtotal": 100.0, "tax_total": 21.0, "grand_total": 121.0}
    fr = [(60, 60, "Au bon moulin SARL"), (380, 300, "Total HT"), (500, 300, "100,00 EUR"), (380, 314, "TVA 20%"),
          (500, 314, "20,00 EUR"), (380, 328, "Total TTC"), (500, 328, "120,00 EUR")]  # fmt: skip
    assert _amounts(tmp_path / "fr.pdf", fr) == {"subtotal": 100.0, "tax_total": 20.0, "grand_total": 120.0}


def test_a_totals_row_printed_across_the_page_is_read(tmp_path):
    # March of Dimes' sample (sweep 4): "Subtotal" at the left margin, "$3,050" at the right one, more than half the
    # page apart: the subtotal, HST and total were all missing.
    cells = [(20, 60, "Registered Business Name"), (20, 400, "Subtotal"), (520, 400, "$3,050"), (20, 420, "HST"),
             (520, 420, "$6.50"), (20, 440, "Total"), (520, 440, "$3,056.50")]  # fmt: skip
    f = analyze(_pdf(tmp_path / "wide.pdf", cells), tables=False).fields
    assert (f["subtotal"].value, f["hst_amount"].value, f["grand_total"].value) == (3050.0, 6.5, 3056.5)


def test_a_summary_rows_total_is_its_last_figure(tmp_path):
    # katanaml samples: "Total  $889,20  $88,92  $ 978,12" under "Net worth | VAT | Gross worth".
    cells = [(60, 60, "Acme Supplies Inc."), (240, 380, "VAT [%]"), (340, 380, "Net worth"), (440, 380, "VAT"),
             (500, 380, "Gross worth"), (240, 400, "10%"), (350, 400, "889,20"), (440, 400, "88,92"),
             (520, 400, "978,12"), (180, 420, "Total"), (345, 420, "$889,20"), (430, 420, "$88,92"),
             (510, 420, "$ 978,12")]  # fmt: skip
    assert _amounts(tmp_path / "sum.pdf", cells) == {"subtotal": 889.2, "tax_total": 88.92, "grand_total": 978.12}


def test_terms_with_a_deposit_first_are_never_verified(tmp_path):
    # FNFE samples (sweep 4): "30% d'acompte, solde à 30 j" was verified as "Net 30" by its due date 30 days on.
    cells = [(60, 60, "Au bon moulin SARL - 1242 chemin de l'olive - 84340 Malaucène - France"),
             (60, 200, "Date de facture"), (200, 200, "13/11/2017"), (60, 214, "Conditions de paiement"),
             (200, 214, "30% d'acompte, solde à 30 j"), (60, 228, "Date d'échéance"),
             (200, 228, "13/12/2017")]  # fmt: skip
    fr = analyze(_pdf(tmp_path / "terms.pdf", cells), tables=False).fields["payment_terms"]
    assert fr.value == "Net 30" and fr.status != "verified"
    assert any("deposit" in r for r in fr.reasons)


def test_a_receipts_tax_lines_with_the_number_in_brackets_or_the_rate_first_are_read(tmp_path):
    # WildReceipt photos (sweep 4): "TPS (824373609RT0001)  $3.82" in Quebec, "13%HST  14.69" in Ontario.
    qc = [(60, 40, "Restaurant Exemple"), (60, 300, "SUB-TOTAL"), (300, 300, "$76.30"),
          (60, 314, "TPS (123456782RT0001)"), (300, 314, "$3.82"), (60, 328, "TVQ (1234567890TQ0001)"),
          (300, 328, "$7.61"), (60, 342, "TOTAL"), (300, 342, "$87.73")]  # fmt: skip
    f = analyze(_pdf(tmp_path / "qc.pdf", qc), tables=False).fields
    assert (f["gst_amount"].value, f["qst_amount"].value, f["grand_total"].value) == (3.82, 7.61, 87.73)
    on = [(60, 40, "Exemple Grill"), (60, 300, "SUBTOTAL"), (300, 300, "112.99"), (60, 314, "13%HST"),
          (300, 314, "14.69"), (60, 328, "TOTAL"), (300, 328, "127.68")]  # fmt: skip
    f = analyze(_pdf(tmp_path / "on.pdf", on), tables=False).fields
    assert (f["subtotal"].value, f["hst_amount"].value, f["grand_total"].value) == (112.99, 14.69, 127.68)


SUBTOTAL_HEAD = ["Acme Supply Ltd.", "123 Main St, Toronto, ON M5V 2T6", "GST/HST No. 123456782 RT0001", "INVOICE",
                 "Invoice No: INV-7001", "Invoice Date: 2026-10-20", "Bill To: Our Company Inc."]  # fmt: skip


@pytest.mark.parametrize(
    ("subtotal", "tax", "total"),
    [("Montant avant taxes 100,00 $", "TVH 13 % 13,00 $", "Montant total 113,00 $"),
     ("Taxable amount 100.00", "HST @ 13% 13.00", "Amount due 113.00"),
     ("Total before HST 100.00", "HST 13.00", "Total incl. HST 113.00"),
     ("Total before GST/HST 100.00", "HST 13.00", "Total 113.00")],
)  # fmt: skip
def test_a_subtotal_labelled_before_the_tax_by_its_name_is_read(tmp_path, subtotal, tax, total):
    """B-1539: "Montant avant taxes", "Taxable amount" and "Total before HST" were not subtotal labels: the subtotal
    came out missing and the total was left to check, though the three add up."""
    rows = [*SUBTOTAL_HEAD, subtotal, tax, total]
    cells = [(50, 50 + 16 * i, t) for i, t in enumerate(rows)]
    fields = analyze(_pdf(tmp_path / "sub.pdf", cells), tables=False).fields
    assert fields["subtotal"].value == 100.0 and fields["subtotal"].status == "verified"
    assert fields["grand_total"].value == 113.0 and fields["grand_total"].status == "verified"


def test_a_taxable_amount_below_the_subtotal_is_not_the_subtotal(tmp_path):
    """A mixed invoice: the subtotal, then the part of it that is taxed. The subtotal is the subtotal."""
    rows = [*SUBTOTAL_HEAD, "Subtotal 120.00", "Taxable amount 100.00", "HST 13.00", "Total 133.00"]
    cells = [(50, 50 + 16 * i, t) for i, t in enumerate(rows)]
    fields = analyze(_pdf(tmp_path / "mixed.pdf", cells), tables=False).fields
    assert fields["subtotal"].value == 120.0 and fields["grand_total"].value == 133.0


@pytest.mark.parametrize(("labels", "values"), [("left", "right"), ("center", "center"), ("right", "left")])
def test_a_totals_table_of_one_row_reads_each_amount_under_its_label(tmp_path, labels, values):
    """B-2639: "Line-Item Total | Freight Amount | GST Tax | HST Tax | Sub Total | Deposits | Credits/Discounts |
    Amount Due:" over "656.50 | 32.00 | | 34.43 | 722.93 | | | 722.93 CAD". The freight and the tax were not read (the
    review said the total was 66.43 more than the lines): an amount at the right of its cell is under no word of its
    label, cells set close together are one line of text, and "CAD" after the total was no amount. Each amount is
    its column's; an empty cell is nothing, not its neighbour's amount."""
    from tests.pdfs import one_row_totals

    f = analyze(one_row_totals(tmp_path, labels=labels, values=values), tables=False).fields
    got = {n: f[n].value for n in ("subtotal", "other_charges", "hst_amount", "grand_total")}
    assert got == {"subtotal": 656.5, "other_charges": 32.0, "hst_amount": 34.43, "grand_total": 722.93}
    assert f["gst_amount"].status == "missing" and f["tax_total"].value in (None, 34.43)


def test_the_customers_name_beside_its_label_across_the_page_is_never_the_supplier(tmp_path):
    """B-2642: "Customer:" at the left margin and the customer's name in a column far to its right, the supplier's
    name only in its logo (a picture): the customer, the company AP Coder works for, was read as the supplier with
    nothing to say it is the customer. The name beside a customer's label is the customer's."""
    from tests.pdfs import ONE_ROW_CUSTOMER, one_row_totals

    cands = read_fields(build_layout(one_row_totals(tmp_path, supplier=False, customer="beside")))["vendor_name"]
    assert ONE_ROW_CUSTOMER not in [r.value for r in cands]


@pytest.mark.parametrize(
    ("label", "heading", "top", "named", "ship_to"),
    [
        ("BILL TO:", "", False, True, False),  # its letterhead level with the label
        ("Bill To", "Remit Payment To", False, False, False),  # its name only in its logo
        ("Bill To", "Remit To", True, True, False),
        ("Sold To", "Vendor", True, True, False),
        ("Customer", "Pay To", True, True, False),
        ("Bill To", "From", True, False, False),
        ("Customer", "Supplier", True, True, False),
        ("Bill To", "Remit To", True, True, True),  # "Bill To | Ship To | Remit To"
    ],
)
def test_the_suppliers_block_level_with_a_bare_bill_to_label_is_never_the_customers(
    tmp_path, label, heading, top, named, ship_to
):
    """B-2781: B-2642 took the line level with a bare bill-to label, anywhere to its right, for the customer's block,
    though the customer's block was under the label: the supplier's own block beside it (its letterhead, "Remit To",
    "Vendor", "Pay To"...) was read as the customer's, its name dropped and its GST/HST number "may be the
    customer's". Only the customer's block is the customer's: under the label, or beside it when nothing is under."""
    from ap_coder.capture.types import BILL_TO_READING
    from tests.pdfs import ONE_ROW_CUSTOMER, ONE_ROW_GST, ONE_ROW_SUPPLIER, supplier_beside_bill_to

    path = supplier_beside_bill_to(tmp_path, "beside.pdf", label, heading, top=top, named=named, ship_to=ship_to)
    got = read_fields(build_layout(path))
    numbers = [(r.value, r.method) for r in got["gst_hst_registration_number"]]
    assert numbers and all(v == ONE_ROW_GST and BILL_TO_READING not in m for v, m in numbers)
    names = [str(r.value).rstrip(".") for r in got["vendor_name"]]  # "Inc" as read in a remit-to
    assert ONE_ROW_CUSTOMER.rstrip(".") not in names
    assert names[:1] == ([ONE_ROW_SUPPLIER.rstrip(".")] if top or named else [])


def test_the_customers_block_beside_its_label_ends_at_the_next_column_and_its_last_line(tmp_path):
    """B-2781: the customer's block beside "Customer:" (B-2642) was a fixed area 0.42 of the page wide and 0.12 tall
    from the name: the supplier's remit-to in the next column, and its GST/HST number printed under the customer's
    address, were read as the customer's. The block ends at the next column and before a number or label."""
    from ap_coder.capture.reader.patterns import _bill_to_regions
    from ap_coder.capture.types import BILL_TO_READING

    path = _pdf(tmp_path / "beside.pdf", [
        (400, 50, "INVOICE", 14), (400, 70, "Invoice #: 118204"), (400, 84, "Invoice Date: 2026-09-14"),
        (36, 120, "Customer:"), (260, 120, "Maple Ridge Holdings Ltd."), (260, 134, "200 Ridge Road SW"),
        (260, 158, "GST/HST No. 123456782 RT0001"), (440, 120, "Remit To"), (440, 134, "Northline Supply Ltd."),
        (440, 148, "PO Box 2210 Stn M"), (440, 162, "Calgary AB T2P 2M4"),
        (50, 260, "Description"), (500, 260, "Amount"), (50, 276, "Widgets"), (500, 276, "100.00"),
        (400, 340, "Subtotal"), (500, 340, "100.00"), (400, 354, "GST 5%"), (500, 354, "5.00"), (400, 368, "Total"),
        (500, 368, "105.00"),
    ])  # fmt: skip
    layout = build_layout(path)
    got = read_fields(layout)
    assert [r.method for r in got["gst_hst_registration_number"]] and not any(
        BILL_TO_READING in r.method for r in got["gst_hst_registration_number"]
    )
    names = [r.value for r in got["vendor_name"]]
    assert "Maple Ridge Holdings Ltd." not in names and names[:1] == ["Northline Supply Ltd."]
    in_bill_to = {ln.text for ln in layout.lines() for r in _bill_to_regions(layout)
                  if r.page == ln.box.page and r.x0 <= ln.box.cx <= r.x1 and r.y0 <= ln.box.cy <= r.y1}  # fmt: skip
    assert {"Maple Ridge Holdings Ltd.", "200 Ridge Road SW"} <= in_bill_to


def test_the_customers_block_beside_its_label_is_read_down_the_page_to_its_end():
    """B-2781: the customer's block beside "Customer:" is read a line at a time down the page. Two lines under the
    name, under a point tall and overlapping (a hidden text layer in tiny type), were each the line under the other:
    the reading never ended and the invoice was never read."""
    import threading

    from ap_coder.capture.reader.patterns import _block_beside
    from ap_coder.capture.types import Box, Line

    def line(text, x0, y0, x1, y1):
        return Line([], Box(0, x0, y0, x1, y1), text)

    label = line("Customer:", 0.06, 0.15, 0.14, 0.165)
    lines = [label, line("Maple Ridge Holdings Ltd.", 0.42, 0.15, 0.7, 0.165),
             line("200 Ridge Road SW", 0.42, 0.18, 0.6, 0.1805),
             line("Calgary AB", 0.45, 0.1803, 0.62, 0.1808)]  # fmt: skip
    got = []
    reading = threading.Thread(target=lambda: got.append(_block_beside(lines, label)), daemon=True)
    reading.start()
    reading.join(10)
    assert got, "the customer's block beside its label was never done"
    assert got[0] is not None and got[0].x0 < 0.42 and got[0].y1 > 0.18


_STREET, _CITY = "200 Ridge Road SW", "Calgary AB T2P 1J9"


@pytest.mark.parametrize(
    "labels",
    [
        ("Customer:", "", ""),  # B-2642's own
        ("Bill To", "Facturer à", ""),  # a bilingual label, stacked
        ("BILL TO", "FACTURER À", ""),
        ("Customer:", "Account No. 10045", ""),
        ("Sold To", "Cust. ID 10045", ""),
        ("Customer:", "Contact", "Phone"),
        ("Customer", "Sales Rep", ""),
        ("Sold To", "Address", "", "Customer No."),  # a form of bare labels, each level with its value
        ("Bill To", "Facturer à", "", "Account No."),
    ],
)
def test_labels_under_a_bare_bill_to_label_keep_the_customers_block_beside_it(tmp_path, labels):
    """B-2782: B-2781 took the customer's block beside a bare bill-to label (B-2642) only when nothing but headings
    was under the label: a label AP Coder reads no field of ("Facturer à" under "Bill To", "Address", "Account No.
    10045", "Contact", "Sales Rep") was taken for the customer's own block under it, and the customer's name across
    the page was read as the supplier again. Only a name or an address under the label is its block."""
    from tests.pdfs import ONE_ROW_CUSTOMER, customer_beside_label

    rows = list(zip(labels, (ONE_ROW_CUSTOMER, _STREET, _CITY, "10045"), strict=False))
    cands = read_fields(build_layout(customer_beside_label(tmp_path, "form.pdf", rows)))["vendor_name"]
    assert ONE_ROW_CUSTOMER not in [r.value for r in cands]


@pytest.mark.parametrize("label", ["Facturer à :", "FACTURER À", "Livrer à :"])
def test_a_french_bill_to_label_in_the_infinitive_is_the_customers_block(tmp_path, label):
    """B-2783: "Facturé à" and "Livré à" were bill-to labels, "Facturer à" and "Livrer à" (as the benchmark's own
    French invoices print them) were not: the customer's name under them, the supplier's only in its logo, was read
    as the supplier."""
    from tests.pdfs import ONE_ROW_CUSTOMER

    path = _pdf(tmp_path / "fr.pdf", [
        (400, 50, "FACTURE", 14), (400, 70, "No de facture : 118204"), (400, 84, "Date : 2026-09-14"),
        (50, 130, label), (50, 144, ONE_ROW_CUSTOMER), (50, 158, _STREET), (50, 172, _CITY),
        (50, 260, "Description"), (500, 260, "Montant"), (50, 276, "Articles"), (500, 276, "100,00"),
        (400, 340, "Sous-total"), (500, 340, "100,00"), (400, 354, "TPS 5 %"), (500, 354, "5,00"), (400, 368, "Total"),
        (500, 368, "105,00"), (50, 400, "TPS/TVH : 123456782 RT0001"),
    ])  # fmt: skip
    assert ONE_ROW_CUSTOMER not in [r.value for r in read_fields(build_layout(path))["vendor_name"]]


_LABELS = ["Billing Address", "SHIPPING ADDRESS:", "Adresse de facturation", "Adresse de livraison :", "Bill-To:",
           "BILL-TO", "Ship-To:", "Sold-To"]  # fmt: skip


@pytest.mark.parametrize("label", _LABELS)
def test_a_billing_or_shipping_address_label_or_a_hyphenated_bill_to_is_the_customers_block(tmp_path, label):
    """B-2784: "Billing Address", "Shipping Address", "Adresse de facturation" and "Bill-To" (a hyphen for the space)
    were no bill-to labels: the customer's name under them, the supplier's only in its logo, was read as the
    supplier."""
    from tests.pdfs import ONE_ROW_CUSTOMER

    path = _pdf(tmp_path / "label.pdf", [
        (400, 50, "INVOICE", 14), (400, 70, "Invoice #: 118204"), (400, 84, "Invoice Date: 2026-09-14"),
        (50, 130, label), (50, 144, ONE_ROW_CUSTOMER), (50, 158, _STREET), (50, 172, _CITY),
        (50, 260, "Description"), (500, 260, "Amount"), (50, 276, "Widgets"), (500, 276, "100.00"),
        (400, 340, "Subtotal"), (500, 340, "100.00"), (400, 354, "GST 5%"), (500, 354, "5.00"), (400, 368, "Total"),
        (500, 368, "105.00"), (50, 400, "GST/HST No. 123456782 RT0001"),
    ])  # fmt: skip
    assert ONE_ROW_CUSTOMER not in [r.value for r in read_fields(build_layout(path))["vendor_name"]]
