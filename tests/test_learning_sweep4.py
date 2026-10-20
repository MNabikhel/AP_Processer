"""Supplier learning on real repeat vendors (sweep 4): a template learned from earlier approvals must not replace a
correct, labelled reading of the page when the vendor's layout changed. Synthetic pages only (the corpus is not in the
repo)."""

from pathlib import Path

import pymupdf

from ap_coder.capture import build_layout
from ap_coder.capture.confidence import fuse
from ap_coder.capture.supplier import apply_template, learn
from ap_coder.capture.types import (
    CHECK,
    EXTRA_FIELDS,
    FIELDS,
    LIKELY,
    RIVAL_NAME,
    UNANCHORED,
    VERIFIED,
    Box,
    DocLayout,
    PageLayout,
    Reading,
    Word,
)


def _pdf(path: Path, lines: list[tuple], size: float = 10) -> Path:
    """``lines``: (x, y, text) or (x, y, text, font size)."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    for x, y, text, *big in lines:
        page.insert_text((x, y), text, fontsize=big[0] if big else size)
    doc.save(path)
    return path


def _layout(path: Path) -> DocLayout:
    return build_layout(path, ocr=False)


def _values(readings: dict[str, list[Reading]], field: str) -> list:
    return [r.value for r in readings.get(field, [])]


def test_a_date_printed_twice_is_learned_where_the_reader_reads_it(tmp_path):
    """The delivery date was the invoice date on the first invoices: the template learned the invoice date at the
    delivery date's label, and read the delivery date as the invoice date once they differed (fx_en16931_teilrechnung:
    2018-06-05 read as 2018-06-03)."""

    def invoice(name: str, delivered: str, dated: str) -> Path:
        return _pdf(tmp_path / name, [
            (40, 50, "Lieferant GmbH"), (330, 90, "Delivery Date:"), (430, 90, delivered),
            (330, 110, "Invoice Date:"), (430, 110, dated), (330, 130, "Invoice No:"), (430, 130, "471102"),
        ])  # fmt: skip

    template = None
    for n in range(2):
        layout = _layout(invoice(f"a{n}.pdf", "05.06.2018", "05.06.2018"))
        template = learn(template, layout, {"invoice_date": ("2018-06-05", None), "invoice_number": ("471102", None)})
    got = apply_template(template, _layout(invoice("b.pdf", "03.06.2018", "05.06.2018")))
    assert _values(got, "invoice_date")[:1] == ["2018-06-05"]


def test_one_printed_date_does_not_teach_two_fields(tmp_path):
    """A credit note dated and due the same day prints the date once: the due date was learned at the invoice date's
    label and read the invoice date as the due date on the next invoice (fx_fnfe_facture_dom: 2017-12-05 verified,
    2017-11-05 by the template)."""
    first = _layout(_pdf(tmp_path / "a.pdf", [(40, 50, "Au bon moulin"), (330, 90, "Date de facture :"),
                                              (450, 90, "05/11/2017")]))  # fmt: skip
    template = learn(None, first, {"invoice_date": ("2017-11-05", None), "due_date": ("2017-11-05", None)})
    later = _layout(_pdf(tmp_path / "b.pdf", [(40, 50, "Au bon moulin"), (330, 90, "Date de facture :"),
                                              (450, 90, "05/11/2017"), (330, 110, "Date d'échéance :"),
                                              (450, 110, "05/12/2017")]))  # fmt: skip
    got = apply_template(template, later)
    assert _values(got, "invoice_date")[:1] == ["2017-11-05"]
    assert "2017-11-05" not in _values(got, "due_date")


def test_the_suppliers_name_printed_again_is_not_a_label(tmp_path):
    """The name printed under the logo was learned with the logo above it as its "label": on the next invoice the
    words under the logo (the street) were read as the supplier (az_invoice_spanish: "Calle de")."""
    first = _layout(_pdf(tmp_path / "a.pdf", [(40, 50, "CONTOSOLTD.", 20), (40, 66, "CONTOSO"), (40, 82, "123 Main St"),
                                              (330, 90, "Invoice No:"), (430, 90, "INV-100")]))  # fmt: skip
    template = learn(None, first, {"vendor_name": ("CONTOSO LTD.", None)})
    assert all(v.anchor is None for v in template.fields["vendor_name"])
    later = _layout(_pdf(tmp_path / "b.pdf", [(40, 50, "CONTOSOLTD.", 20), (40, 66, "Calle de"), (40, 82, "Atocha 27"),
                                              (330, 90, "Invoice No:"), (430, 90, "INV-101")]))  # fmt: skip
    got = apply_template(template, later)
    assert all(UNANCHORED in r.method for r in got.get("vendor_name", []))  # where the name was, nothing more
    rules = {"vendor_name": [_rule("vendor_name", "CONTOSOLTD.", 0.69, "top-of-page")]}
    fields, _ = fuse({"rules": rules, "template": got}, [], fields=("vendor_name",))
    assert fields["vendor_name"].value == "CONTOSOLTD."


def test_a_tax_rate_is_not_read_as_the_tax_amount(tmp_path):
    """ "Sales Tax 3%  $311.25": the template read the rate where the amount was learned (az_project_statement_6:
    tax 3.00)."""
    first = _layout(_pdf(tmp_path / "a.pdf", [(40, 50, "Contoso"), (330, 600, "Sales Tax"), (450, 600, "$10.00")]))
    template = learn(None, first, {"tax_total": (10.0, None)})
    later = _layout(_pdf(tmp_path / "b.pdf", [(40, 50, "Contoso"), (330, 600, "Sales Tax"), (450, 600, "3%"),
                                              (520, 600, "$311.25")]))  # fmt: skip
    assert 3.0 not in _values(apply_template(template, later), "tax_total")


def test_the_learned_date_order_holds_only_for_dates_printed_the_same_way(tmp_path):
    """Day first was learned from "10-06-19"; a receipt printing "6/10/2019" (month first) was read as 2019-10-06
    (az_receipt_tips)."""
    first = _layout(_pdf(tmp_path / "a.pdf", [(40, 50, "Contoso"), (40, 90, "10-06-19")]))
    template = learn(None, first, {"invoice_date": ("2019-06-10", None)})
    assert template.fields["invoice_date"][0].date_order == "dmy"
    later = _layout(_pdf(tmp_path / "b.pdf", [(40, 50, "Contoso"), (40, 90, "6/10/2019")]))
    assert "2019-10-06" not in _values(apply_template(template, later), "invoice_date")
    same_way = _layout(_pdf(tmp_path / "c.pdf", [(40, 50, "Contoso"), (40, 90, "07-06-19")]))
    assert _values(apply_template(template, same_way), "invoice_date")[:1] == ["2019-06-07"]


def _page(items, number=1):
    words = [Word(text, Box(number, x, y, x + 0.008 * len(text), y + 0.012)) for text, x, y in items]
    return PageLayout(number, 612, 792, words, [])


def _invoice_layout(number: str, label: tuple[str, ...]) -> DocLayout:
    items = [("Northwind", 0.06, 0.05), *[(t, 0.6 + 0.06 * i, 0.1) for i, t in enumerate(label)], (number, 0.72, 0.1)]
    return DocLayout([_page(items)], "text")


def _rule(field: str, value, score: float, method: str = "label-right") -> Reading:
    return Reading(field, value, str(value), [Box(1, 0.5, 0.5, 0.6, 0.51)], score, method)


def test_a_reading_at_the_learned_spot_without_its_label_does_not_beat_a_labelled_reading():
    """Without its label on the page, the template reads whatever is printed where the value used to be: it may
    fill a field nothing else read (to be checked), not replace what the page's own label says."""
    template = None
    for n in range(2):
        template = learn(template, _invoice_layout(f"NW-2026-09{n}", ("Invoice", "No:")),
                         {"invoice_number": (f"NW-2026-09{n}", None)})  # fmt: skip
    moved = apply_template(template, _invoice_layout("NW-2026-0999", ("Ref",)))  # the label is not printed
    assert _values(moved, "invoice_number")[:1] == ["NW-2026-0999"]
    rules = {"invoice_number": [_rule("invoice_number", "NW-2026-1000", 0.6)]}
    fields, _ = fuse({"rules": rules, "template": moved}, [], fields=("invoice_number",))
    assert fields["invoice_number"].value == "NW-2026-1000"
    alone, _ = fuse({"rules": {}, "template": moved}, [], fields=("invoice_number",))
    assert alone["invoice_number"].value == "NW-2026-0999" and alone["invoice_number"].status == CHECK
    agree = {"invoice_number": [_rule("invoice_number", "NW-2026-0999", 0.6)]}
    both, _ = fuse({"rules": agree, "template": moved}, [], fields=("invoice_number",))
    assert both["invoice_number"].value == "NW-2026-0999" and "rules, template" in both["invoice_number"].reasons[0]


def test_runner_ups_that_add_up_do_not_replace_amounts_two_readers_chose():
    """subtotal 100 and total 110, both the first choice of the rules and the template; the IVA row below (8,00) was
    each one's runner-up for both, and "8 = 8" added up, so both were replaced (az_invoice_spanish)."""
    rules = {"subtotal": [_rule("subtotal", 100.0, 0.877)],
             "grand_total": [_rule("grand_total", 110.0, 0.668), _rule("grand_total", 8.0, 0.651)]}  # fmt: skip
    template = {"subtotal": [_rule("subtotal", 100.0, 0.762, "template"), _rule("subtotal", 8.0, 0.664, "template")],
                "grand_total": [_rule("grand_total", 110.0, 0.913, "template"),
                                _rule("grand_total", 8.0, 0.536, "template")]}  # fmt: skip
    fields, _ = fuse({"rules": rules, "template": template}, [])
    assert (fields["subtotal"].value, fields["grand_total"].value) == (100.0, 110.0)
    assert {fields["subtotal"].status, fields["grand_total"].status} <= {CHECK, LIKELY, VERIFIED}


def test_a_template_reading_without_its_label_does_not_re_decide_the_other_totals():
    """A subtotal filled by the template at its old spot (no label on the page) made "-7,90 + 0,03 - 0,92 = -8,79" add
    up with the rule reader's runner-up tax: the wrong tax was shown likely (zf1_comfort_korrektur, -1,12 printed)."""
    unanchored = "template" + UNANCHORED
    rules = {"tax_total": [_rule("tax_total", 12.09, 0.722, "label-below"),
                           _rule("tax_total", -0.92, 0.722, "label-below")],
             "other_charges": [_rule("other_charges", 0.03, 0.9, "label-inline")],
             "grand_total": [_rule("grand_total", -5.77, 0.825, "label-below")]}  # fmt: skip
    template = {"subtotal": [_rule("subtotal", -7.9, 0.573, unanchored)],
                "grand_total": [_rule("grand_total", -8.79, 0.829, "template")]}  # fmt: skip
    fields, _ = fuse({"rules": rules, "template": template}, [], fields=FIELDS + EXTRA_FIELDS)
    assert fields["subtotal"].value == -7.9 and fields["subtotal"].status == CHECK
    assert fields["tax_total"].value == 12.09


def test_a_template_rival_does_not_mark_down_an_amount_the_totals_confirm():
    """The template read the total at the old "Total HT" spot (624,90); the rule reader's 671,15 adds up and was
    verified without the template, likely with it (fx_fnfe_facture_fr)."""
    rules = {"subtotal": [_rule("subtotal", 624.9, 0.9)], "tax_total": [_rule("tax_total", 46.25, 0.9)],
             "grand_total": [_rule("grand_total", 671.15, 0.82, "label-right+adds-up")]}  # fmt: skip
    template = {"subtotal": [_rule("subtotal", 624.9, 0.95, "template")],
                "grand_total": [_rule("grand_total", 624.9, 0.967, "template")]}  # fmt: skip
    plain, _ = fuse({"rules": rules}, [])
    taught, _ = fuse({"rules": rules, "template": template}, [])
    assert taught["grand_total"].value == 671.15
    assert taught["grand_total"].status == plain["grand_total"].status
    assert taught["grand_total"].confidence >= plain["grand_total"].confidence


def test_the_template_agreeing_never_makes_a_value_less_certain():
    """The rule reader's name with a rival name under it is likely; the template agreeing made it "check" (its
    evidence pattern with the template is not measured yet: az_contoso_invoice_pdf, Bei Spiel GmbH, Coolblue)."""
    rules = {"vendor_name": [_rule("vendor_name", "CONTOSO LTD.", 0.944, "top-of-page" + RIVAL_NAME),
                             _rule("vendor_name", "Microsoft Corp", 0.73, "top-of-page")]}  # fmt: skip
    template = {"vendor_name": [_rule("vendor_name", "CONTOSO LTD.", 0.571, "template" + UNANCHORED)]}
    plain, _ = fuse({"rules": rules}, [], fields=("vendor_name",))
    taught, _ = fuse({"rules": rules, "template": template}, [], fields=("vendor_name",))
    assert taught["vendor_name"].value == "CONTOSO LTD."
    assert taught["vendor_name"].confidence >= plain["vendor_name"].confidence
    assert taught["vendor_name"].status == plain["vendor_name"].status == LIKELY
