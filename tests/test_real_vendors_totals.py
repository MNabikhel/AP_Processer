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
