"""Real-world vendors and totals (sweep 4): what public sample invoices taught about the supplier's name and the
subtotal / taxes / total on invoices whose totals are laid out as a summary table. Every fixture is generated here."""

from pathlib import Path

import pymupdf

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
