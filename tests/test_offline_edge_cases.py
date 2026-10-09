"""The offline path on unusual but real invoices: French labels, freight, a missing subtotal."""

from ap_coder.capture.types import VERIFIED, CaptureResult, FieldResult, LineReading
from ap_coder.offline_coder import code_from_capture, provinces
from ap_coder.validation import validate_coding

HEADER = {"vendor_name": "Acme", "invoice_number": "A1", "invoice_date": "2026-01-05", "currency": "CAD"}


def _capture(lines=(), **values):
    return CaptureResult(
        fields={k: FieldResult(k, v, 0.95, VERIFIED) for k, v in {**HEADER, **values}.items()},
        line_items=[LineReading(d, q, u, a) for d, q, u, a in lines],
    )


def _errors(coding, reference):
    return [i.code for i in validate_coding(coding, reference).issues if i.severity == "error"]


def test_a_french_invoice_title_above_the_letterhead_is_not_the_customer_label():
    # "FACTURE" (invoice) is the title of a French invoice, not a "bill to" label: the supplier is the first
    # address and the customer's (after "Vendu à") is the place of supply.
    text = (
        "FACTURE\nFournitures Laval inc.\n100 boul. Saint-Martin, Laval QC H7N 1A1\nVendu à :\nAcme Ltd\n"
        "50 King St, Toronto ON M5V 1A1\n"
    )
    assert provinces(text) == ("QC", "ON")
    assert provinces(text.replace("FACTURE\n", "Fournitures Laval inc.\nFacture no 1234\n", 1)) == ("QC", "ON")


def test_customer_service_or_delivery_date_in_the_letterhead_is_not_the_customer_label():
    text = "Acme Supply\nCustomer service 1-800-555-1212\n1 Main St, Toronto ON M5V 1A1\nBill to:\nHalifax NS B3H 1A1\n"
    assert provinces(text) == ("ON", "NS")
    text = "Date de livraison : 2026-01-02\nFournitures Laval\nLaval QC H7N 1A1\nClient :\nToronto ON M5V 1A1\n"
    assert provinces(text) == ("QC", "ON")


def test_expedie_a_is_the_french_ship_to():
    text = (
        "Fournitures Laval inc.\nLaval QC H7N 1A1\nFacturé à :\nAcme, Toronto ON M5V 1A1\nExpédié à :\n"
        "Acme, Halifax NS B3H 1A1\n"
    )
    assert provinces(text) == ("QC", "NS")


def test_bill_to_and_ship_to_side_by_side():
    # Two columns on one row are read as separate segments, left before right: the labels come first,
    # then each column's address in the same order.
    rows = ["Acme", "Toronto ON M5V 1A1", "Bill to:", "Ship to:", "Fabrikam Ltd", "Fabrikam Ltd"]
    text = "\n".join([*rows, "Calgary AB T2P 1A1", "Halifax NS B3H 1A1"])
    assert provinces(text) == ("ON", "NS")
    text = "\n".join([*rows, "Calgary AB T2P 1A1", "Halifax NS B3H 1A1"]).replace(
        "Bill to:\nShip to:", "Ship to:\nBill to:"
    )
    assert provinces(text) == ("ON", "AB")


def test_freight_beside_the_subtotal_is_in_the_coded_subtotal_and_the_tax_base(reference):
    # Subtotal 1,000 + freight 50 (printed between the subtotal and the taxes), GST 5% on 1,050.
    capture = _capture([("Widgets", 10, 100.0, 1000.0)], subtotal=1000.0, other_charges=50.0, gst_amount=52.5,
                       tax_total=52.5, grand_total=1102.5)  # fmt: skip
    text = "Acme\nToronto ON M5V 1A1\nBill to:\nCalgary AB T2P 1A1\nWidgets 1,000.00\nFreight 50.00\n"
    coding = code_from_capture(capture, reference, [], text=text).coding
    assert [li.amount for li in coding.line_items] == [1000.0, 50.0]
    assert coding.subtotal == 1050.0
    assert [(t.tax_type, t.rate, t.taxable_amount) for t in coding.tax_lines] == [("GST", 0.05, 1050.0)]
    assert _errors(coding, reference) == []


def test_gst_and_qst_on_an_invoice_with_freight(reference):
    capture = _capture([("Service", 1, 1000.0, 1000.0)], subtotal=1000.0, other_charges=25.0, gst_amount=51.25,
                       qst_amount=102.24, tax_total=153.49, grand_total=1178.49)  # fmt: skip
    text = "Fournisseur\nMontréal (QC) H2W 2R2\nFacturer à :\nLaval QC H7N 1A1\nLivraison 25.00\n"
    coding = code_from_capture(capture, reference, [], text=text).coding
    assert coding.subtotal == 1025.0
    assert [(t.tax_type, t.province, t.rate) for t in coding.tax_lines] == [("GST", "", 0.05), ("QST", "QC", 0.09975)]
    assert _errors(coding, reference) == []


def test_a_subtotal_the_reader_could_not_find_does_not_cancel_the_lines(reference):
    capture = _capture([("Widgets", 10, 100.0, 1000.0)], gst_amount=50.0, grand_total=1050.0)
    coding = code_from_capture(capture, reference, [], text="Acme\nCalgary AB T2P 1A1\n").coding
    assert [li.amount for li in coding.line_items] == [1000.0]
    assert coding.subtotal == 1000.0
    assert [(t.tax_type, t.rate) for t in coding.tax_lines] == [("GST", 0.05)]
    assert _errors(coding, reference) == []


def test_freight_with_no_line_table_is_its_own_line(reference):
    capture = _capture(subtotal=1000.0, other_charges=50.0, gst_amount=52.5, tax_total=52.5, grand_total=1102.5)
    coding = code_from_capture(capture, reference, [], text="Acme\nCalgary AB T2P 1A1\nFreight 50.00\n").coding
    assert [li.amount for li in coding.line_items] == [1000.0, 50.0]
    assert _errors(coding, reference) == []


def test_a_file_that_cannot_be_read_does_not_stop_the_batch(tmp_path, reference, monkeypatch):
    # A file locked by another program (a scanner still writing it, a OneDrive file not downloaded):
    # that invoice fails, the rest of the batch is still read and saved.
    from dataclasses import replace
    from pathlib import Path

    from ap_coder.config import Settings
    from ap_coder.pipeline import InvoicePipeline
    from ap_coder.store import Store

    from .conftest import SAMPLES

    locked = tmp_path / "locked.pdf"
    locked.write_bytes(b"%PDF-1.4 still being written")
    real_read_bytes = Path.read_bytes

    def read_bytes(self):
        if self.name == "locked.pdf":
            raise PermissionError(13, "The process cannot access the file", str(self))
        return real_read_bytes(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    settings = Settings()
    settings = replace(settings, llm=replace(settings.llm, provider="off"))
    store = Store(tmp_path / "ap.db")
    good = SAMPLES / "harbourview_NS_HST_HPS-2026-0347.pdf"
    results = InvoicePipeline(settings, reference, store=store).process_many([locked, good])
    assert [r.source.name for r in results] == ["locked.pdf", good.name]
    assert not results[0].ok and results[0].error
    assert results[1].ok and results[1].invoice_id


def test_output_names_never_collide():
    from pathlib import Path

    from ap_coder.pipeline import output_stems

    paths = [Path("a/Invoice.pdf"), Path("b/Invoice.pdf"), Path("c/Invoice_2.pdf"), Path("d/INVOICE.pdf")]
    stems = output_stems(paths)
    assert len({s.lower() for s in stems}) == len(paths), stems
    assert stems == ["Invoice", "Invoice_3", "Invoice_2", "INVOICE_4"]  # c keeps its own name


def test_a_text_invoice_saved_by_windows_in_ansi_or_with_a_bom_is_read(tmp_path):
    from ap_coder.extraction import result_from_text

    ansi = tmp_path / "ansi.txt"
    ansi.write_bytes("Fournitures Laval\nFacture no 12\nTotal 114,98 $ (TPS incluse, été)\n".encode("cp1252"))
    assert "été" in result_from_text(ansi).content
    bom = tmp_path / "bom.md"
    bom.write_bytes("Acme Ltd\nInvoice 7\n".encode("utf-8-sig"))
    assert result_from_text(bom).content.startswith("Acme Ltd")


def test_a_line_with_a_quantity_but_no_unit_price_gets_its_unit_price(reference):
    capture = _capture([("Paper, cases", 5, None, 100.0)], subtotal=100.0, grand_total=100.0)
    coding = code_from_capture(capture, reference, [], text="").coding
    assert coding.line_items[0].unit_price == 20.0
    assert not [i for i in validate_coding(coding, reference).issues if i.code == "LINE_MATH"]
