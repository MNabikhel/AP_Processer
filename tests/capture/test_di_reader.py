"""Azure Document Intelligence's reading as one more reader (capture ``_di_readings``, ``_di_lines``; D-245): each
field of ``prebuilt-invoice`` (API 2024-11-30) mapped to AP Coder's, from the made-up recorded answers of the samples
(tests/fixtures/di/)."""

import copy

import pytest

from ap_coder.capture import _di_lines, _di_readings, analyze
from ap_coder.capture.line_items import sources_line
from tests.conftest import SAMPLES
from tests.fake_di import recorded
from tests.pdfs import text_pdf

NORTHWIND, LAURENTIDES = "northwind_ON_HST_NW-2026-0912", "laurentides_QC_TPS_TVQ_SIL-4471"
PACIFIC, CASCADE = "pacific_BC_GST_PST_PO-77120", "cascade_US_SalesTax_INV-30981"


def _values(raw):
    return {name: found[0].value for name, found in _di_readings(raw).items()}


def test_the_header_fields_are_read_with_their_confidence_and_boxes():
    found = _di_readings(recorded(NORTHWIND))
    values = {name: r[0].value for name, r in found.items()}
    assert values["vendor_name"] == "Northwind IT Solutions Inc." and values["invoice_number"] == "NW-2026-0912"
    assert values["invoice_date"] == "2026-09-14" and values["due_date"] == "2026-10-14"
    assert values["po_number"] == "PO-88213"
    assert (values["subtotal"], values["tax_total"], values["grand_total"]) == (15945.0, 2072.85, 18017.85)
    reading = found["invoice_number"][0]
    assert reading.method == "di" and reading.score == 0.97 and reading.raw == "NW-2026-0912"
    box = reading.boxes[0]  # inches turned into page-relative boxes
    assert box.page == 1 and 0 < box.x0 < box.x1 < 1 and 0 < box.y0 < box.y1 < 0.5


def test_pixels_are_page_relative_too():
    raw = copy.deepcopy(recorded(NORTHWIND))
    raw["pages"][0].update(width=1700, height=2200, unit="pixel")
    field = raw["documents"][0]["fields"]["InvoiceId"]
    field["boundingRegions"] = [{"pageNumber": 1, "polygon": [850, 220, 1020, 220, 1020, 250, 850, 250]}]
    box = _di_readings(raw)["invoice_number"][0].boxes[0]
    assert (round(box.x0, 3), round(box.y0, 3), round(box.x1, 3), round(box.y1, 3)) == (0.5, 0.1, 0.6, 0.114)


def test_the_customers_name_and_tax_number_are_never_taken_for_the_suppliers():
    raw = copy.deepcopy(recorded(NORTHWIND))
    fields = raw["documents"][0]["fields"]
    fields["CustomerTaxId"] = {"type": "string", "valueString": "987654324 RT0001", "confidence": 0.9}
    del fields["VendorTaxId"]
    values = _values(raw)
    assert "gst_hst_registration_number" not in values
    assert values["vendor_name"] == "Northwind IT Solutions Inc."


def test_the_currency_comes_from_the_totals_currency_code():
    assert _values(recorded(CASCADE))["currency"] == "USD"
    assert _values(recorded(NORTHWIND))["currency"] == "CAD"
    reading = _di_readings(recorded(CASCADE))["currency"][0]
    assert reading.boxes == []  # a code Azure worked out, not printed: never verified on its own


@pytest.mark.parametrize(
    ("stem", "field", "number"),
    [
        (NORTHWIND, "gst_hst_registration_number", "123456782 RT0001"),
        (LAURENTIDES, "qst_registration_number", "1234567890 TQ0001"),
    ],
)
def test_the_suppliers_tax_number_goes_where_its_pattern_says(stem, field, number):
    values = _values(recorded(stem))
    assert values[field] == number
    other = {"gst_hst_registration_number", "qst_registration_number"} - {field}
    assert not other & set(values)


def test_a_tax_number_of_neither_pattern_is_left_out():
    raw = copy.deepcopy(recorded(NORTHWIND))
    raw["documents"][0]["fields"]["VendorTaxId"]["valueString"] = "WA UBI 604 118 352"
    values = _values(raw)
    assert "gst_hst_registration_number" not in values and "qst_registration_number" not in values


def test_each_tax_is_read_by_its_rate():
    assert _values(recorded(NORTHWIND))["hst_amount"] == 2072.85
    quebec = _values(recorded(LAURENTIDES))
    assert (quebec["gst_amount"], quebec["qst_amount"]) == (120.0, 239.4)
    assert "tax_total" not in quebec  # not printed as one figure: worked out from the taxes, as always
    pacific = _values(recorded(PACIFIC))
    assert (pacific["gst_amount"], pacific["pst_amount"]) == (136.3, 190.82)
    reading = _di_readings(recorded(PACIFIC))["pst_amount"][0]
    assert reading.method == "di" and reading.boxes  # boxed where the amount is printed


def test_a_rate_that_names_no_tax_or_two_amounts_at_one_rate_are_left_to_the_total_tax():
    us = _values(recorded(CASCADE))  # a 10.35 % sales tax
    assert not {"gst_amount", "hst_amount", "pst_amount", "qst_amount"} & set(us) and us["tax_total"] == 710.01
    raw = copy.deepcopy(recorded(PACIFIC))
    details = raw["documents"][0]["fields"]["TaxDetails"]["valueArray"]
    details.append(copy.deepcopy(details[0]))  # two amounts at 5 %: which is the GST, the page doesn't say
    details[-1]["valueObject"]["Amount"]["valueCurrency"]["amount"] = 12.0
    values = _values(raw)
    assert "gst_amount" not in values and values["pst_amount"] == 190.82
    details[0]["valueObject"]["Rate"]["valueString"] = ""
    details[-1]["valueObject"]["Rate"]["valueString"] = "five"
    assert "gst_amount" not in _values(raw)


def test_the_line_items_are_read_as_azures():
    lines = _di_lines(recorded(NORTHWIND))
    assert [(li.description, li.quantity, li.unit_price, li.amount) for li in lines[:2]] == [
        ("Dell Latitude 7450 laptop, 32GB RAM", 3, 1450.0, 4350.0),
        ("Dell PowerEdge R760 rack server - datacentre", 1, 8900.0, 8900.0),
    ]
    assert len(lines) == 5 and all(li.source == "di" and li.boxes and li.score == 0.9 for li in lines)
    assert lines[3].boxes[0].page == 2  # the rows on the second page are boxed there
    assert all(li.taxes_read is None for li in lines)


def test_a_lines_tax_is_kept_as_read_and_a_line_without_an_amount_is_left_out():
    raw = copy.deepcopy(recorded(PACIFIC))
    items = raw["documents"][0]["fields"]["Items"]["valueArray"]
    items[0]["valueObject"]["Tax"] = {"type": "currency", "valueCurrency": {"amount": 106.8}, "confidence": 0.8}
    del items[2]["valueObject"]["Amount"]
    lines = _di_lines(raw)
    assert len(lines) == 2 and lines[0].taxes_read == {"TAX": 106.8}


def test_no_document_reads_nothing():
    assert _di_readings({"pages": [], "documents": []}) == {} and _di_lines({}) == []


# --- Its line items beside the page's own ---------------------------------------------------------------------------


def test_azures_lines_that_agree_with_the_pages_are_marked_so_and_never_replace_them():
    alone = analyze(SAMPLES / f"{PACIFIC}.pdf", tables=False)
    both = analyze(SAMPLES / f"{PACIFIC}.pdf", tables=False, di_raw=recorded(PACIFIC))
    assert [li.amount for li in both.line_items] == [li.amount for li in alone.line_items] == [890.0, 1316.0, 520.0]
    assert all(li.source != "di" and "di" in li.agreed for li in both.line_items)
    assert "Azure Document Intelligence" in sources_line(both.line_items, both.checks)
    # The words under the headings read 4 of Northwind's 5 lines, Azure 5: the page's 4 stay, none marked agreed.
    northwind = analyze(SAMPLES / f"{NORTHWIND}.pdf", tables=False, di_raw=recorded(NORTHWIND)).line_items
    assert len(northwind) == 4 and not any("di" in li.agreed or li.source == "di" for li in northwind)


def test_azures_lines_fill_in_only_where_the_page_gave_none(tmp_path):
    pdf = text_pdf(tmp_path, [(50, 60, "Acme Repairs Ltd."), (50, 90, "Invoice No: AR-1001"),
                              (50, 110, "Date: 2026-10-01"), (50, 140, "Total: $250.00")])  # fmt: skip
    assert analyze(pdf, tables=False).line_items == []
    row = {"Description": {"type": "string", "valueString": "Furnace repair"},
           "Amount": {"type": "currency", "valueCurrency": {"amount": 250.0, "currencyCode": "CAD"}}}  # fmt: skip
    items = {"type": "array", "valueArray": [{"type": "object", "confidence": 0.8, "valueObject": row}]}
    raw = {"pages": [{"pageNumber": 1, "width": 8.5, "height": 11, "unit": "inch"}],
           "documents": [{"fields": {"Items": items}}]}  # fmt: skip
    lines = analyze(pdf, tables=False, di_raw=raw).line_items
    assert [(li.description, li.amount, li.source) for li in lines] == [("Furnace repair", 250.0, "di")]


# --- An answer of another shape: skipped, never a crash (the review of D-245) --------------------------------------


def _fields(raw):
    return raw["documents"][0]["fields"]


ODD = {
    "documents is a string": lambda r: r.update(documents="x"),
    "fields is a list": lambda r: r["documents"][0].update(fields=[1, 2]),
    "a field is a string": lambda r: _fields(r).update(InvoiceId="valueString!"),
    "confidence is text": lambda r: _fields(r)["InvoiceId"].update(confidence="high"),
    "pageNumber is text": lambda r: _fields(r)["InvoiceId"].update(
        boundingRegions=[{"pageNumber": "one", "polygon": [0] * 8}]),
    "polygon of strings": lambda r: _fields(r)["InvoiceId"].update(
        boundingRegions=[{"pageNumber": 1, "polygon": ["a"] * 8}]),
    "pages a dict": lambda r: r.update(pages={"a": 1}),
    "valueString a list": lambda r: _fields(r).update(InvoiceId={"valueString": ["NW"], "confidence": 0.9}),
    "amount a string": lambda r: _fields(r).update(InvoiceTotal={"valueCurrency": {"amount": "lots"}}),
    "amount NaN": lambda r: _fields(r).update(InvoiceTotal={"valueCurrency": {"amount": float("nan")}}),
    "amount huge": lambda r: _fields(r).update(InvoiceTotal={"valueCurrency": {"amount": 1e300}}),
    "confidence 1e9": lambda r: _fields(r)["InvoiceId"].update(confidence=1e9),
    "TaxDetails junk": lambda r: _fields(r).update(TaxDetails={"valueArray": ["x", {"valueObject": "y"}]}),
    "Items junk": lambda r: _fields(r).update(Items={"valueArray": ["x", {"valueObject": [1]}]}),
    "boxes off the page": lambda r: _fields(r)["InvoiceId"].update(
        boundingRegions=[{"pageNumber": 1, "polygon": [-5e9, -5e9, 9e9, -5e9, 9e9, 9e9, -5e9, 9e9]}]),
    "box on page 99": lambda r: _fields(r)["InvoiceId"].update(
        boundingRegions=[{"pageNumber": 99, "polygon": [1] * 8}]),
}  # fmt: skip


@pytest.mark.parametrize("name", list(ODD))
def test_an_answer_of_another_shape_reads_what_it_can_and_never_raises(name):
    import math

    raw = copy.deepcopy(recorded(NORTHWIND))
    ODD[name](raw)
    found = _di_readings(raw)
    lines = _di_lines(raw)
    for readings in found.values():
        for r in readings:
            assert 0.0 <= r.score <= 1.0
            assert not isinstance(r.value, float) or (math.isfinite(r.value) and abs(r.value) < 1e12)
            assert isinstance(r.value, (str, int, float))
            assert all(0.0 <= v <= 1.0 for b in r.boxes for v in (b.x0, b.y0, b.x1, b.y1))
    assert all(isinstance(li.amount, float) for li in lines)


def test_analyze_reads_the_invoice_without_an_answer_it_cant_read(monkeypatch):
    import ap_coder.capture as capture

    def broken(raw):
        raise TypeError("an answer of another shape")

    monkeypatch.setattr(capture, "_di_readings", broken)
    monkeypatch.setattr(capture, "_di_lines", broken)
    result = analyze(SAMPLES / f"{PACIFIC}.pdf", tables=False, di_raw=recorded(PACIFIC))
    assert result.fields["invoice_number"].value == "PO-77120"
    assert not any("di" in fr.sources for fr in result.fields.values())


def test_the_rate_is_the_number_with_a_percent_sign_or_azures_number():
    raw = copy.deepcopy(recorded(PACIFIC))
    details = _fields(raw)["TaxDetails"]["valueArray"]
    details[0]["valueObject"]["Rate"] = {"type": "string", "valueString": "GST 2026: 5 %"}
    details[1]["valueObject"]["Rate"] = {"type": "number", "valueNumber": 7}
    values = _values(raw)
    assert (values["gst_amount"], values["pst_amount"]) == (136.3, 190.82)


def test_rate_taxes_are_not_read_off_an_invoice_in_another_currency_or_from_abroad():
    raw = copy.deepcopy(recorded(PACIFIC))
    for item in _fields(raw)["TaxDetails"]["valueArray"]:
        item["valueObject"]["Rate"]["valueString"] = "7%"  # a US sales tax at 7 %
    del _fields(raw)["TaxDetails"]["valueArray"][1]
    _fields(raw)["InvoiceTotal"]["valueCurrency"]["currencyCode"] = "USD"
    assert "pst_amount" not in _values(raw)
    _fields(raw)["InvoiceTotal"]["valueCurrency"]["currencyCode"] = "CAD"
    _fields(raw)["VendorAddress"] = {"type": "address", "valueAddress": {"countryRegion": "USA", "state": "WA"}}
    assert "pst_amount" not in _values(raw)
    _fields(raw)["VendorAddress"]["valueAddress"]["countryRegion"] = "CAN"
    assert _values(raw)["pst_amount"] == 136.3


# --- A line only Azure read is for a person (the review of D-245) --------------------------------------------------


def test_a_line_only_azure_read_is_never_approved_without_a_person():
    from ap_coder.capture.supplier import AUTONOMOUS, should_auto_approve
    from ap_coder.capture.types import VERIFIED

    capture = analyze(SAMPLES / f"{NORTHWIND}.pdf", tables=False, di_raw=recorded(NORTHWIND))
    for fr in capture.fields.values():
        if fr.value not in (None, ""):
            fr.status = VERIFIED
    assert should_auto_approve(AUTONOMOUS, capture, checks_ok=True)[0]  # the page's own lines
    for li in capture.line_items:
        li.source, li.agreed = "di", []
    ok, why = should_auto_approve(AUTONOMOUS, capture, checks_ok=True)
    assert not ok and "only Azure Document Intelligence read" in why
