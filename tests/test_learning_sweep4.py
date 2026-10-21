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
    UNSETTLED,
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


def test_a_date_printed_twice_does_not_settle_where_it_was_learned(tmp_path):
    """The delivery date was the invoice date on the first invoices: the template learned the invoice date at the
    delivery date's label, and read the delivery date as the invoice date once they differed (fx_en16931_teilrechnung:
    2018-06-05 read as 2018-06-03). Printed twice, the spot is not settled: what the template reads there does not
    replace the rule reader's labelled date; an approval where the date is printed only once settles the spot."""

    def invoice(name: str, delivered: str, dated: str) -> Path:
        return _pdf(tmp_path / name, [
            (40, 50, "Lieferant GmbH"), (330, 90, "Delivery Date:"), (430, 90, delivered),
            (330, 110, "Invoice Date:"), (430, 110, dated), (330, 130, "Invoice No:"), (430, 130, "471102"),
        ])  # fmt: skip

    template = None
    for n in range(2):
        layout = _layout(invoice(f"a{n}.pdf", "05.06.2018", "05.06.2018"))
        template = learn(template, layout, {"invoice_date": ("2018-06-05", None), "invoice_number": ("471102", None)})
    later = _layout(invoice("b.pdf", "03.06.2018", "05.06.2018"))
    got = apply_template(template, later)
    assert all(UNSETTLED in r.method for r in got["invoice_date"])
    rules = {"invoice_date": [_rule("invoice_date", "2018-06-05", 0.86)]}
    fields, _ = fuse({"rules": rules, "template": got}, [], fields=("invoice_date",))
    assert fields["invoice_date"].value == "2018-06-05" and fields["invoice_date"].status != VERIFIED
    template = learn(template, later, {"invoice_date": ("2018-06-05", None)})  # printed once: the spot is settled
    vouched = apply_template(template, _layout(invoice("c.pdf", "01.06.2018", "05.06.2018")))["invoice_date"]
    assert [(r.value, UNSETTLED in r.method) for r in vouched][:1] == [("2018-06-05", False)]


def test_one_printed_date_for_two_fields_settles_neither(tmp_path):
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
    assert all(UNSETTLED in r.method for f in ("invoice_date", "due_date") for r in got[f])
    rules = {"due_date": [_rule("due_date", "2017-12-05", 0.865)]}
    fields, _ = fuse({"rules": rules, "template": got}, [], fields=("due_date",))
    assert fields["due_date"].value == "2017-12-05"


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
    assert all(r.score < 0.69 for r in got.get("vendor_name", []))  # read by position only: weaker than by a label
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
    unanchored = "template" + UNSETTLED
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
    template = {"vendor_name": [_rule("vendor_name", "CONTOSO LTD.", 0.571, "template" + UNSETTLED)]}
    plain, _ = fuse({"rules": rules}, [], fields=("vendor_name",))
    taught, _ = fuse({"rules": rules, "template": template}, [], fields=("vendor_name",))
    assert taught["vendor_name"].value == "CONTOSO LTD."
    assert taught["vendor_name"].confidence >= plain["vendor_name"].confidence
    assert taught["vendor_name"].status == plain["vendor_name"].status == LIKELY


def test_a_reading_the_template_cannot_vouch_for_is_never_an_independent_reader():
    """The template, its label gone, read the true date first and the order date next; its first choice was dropped,
    its runner-up agreed with the rule reader's wrong first choice and counted as a second reader: verified (review
    of B-387, `relayout3`)."""
    for field, x, y in (("invoice_date", "2026-06-15", "2026-06-11"), ("invoice_number", "INV-555", "SO-778")):
        rules = {field: [_rule(field, y, 0.6, "label-inline")]}
        template = {
            field: [_rule(field, x, 0.62, "template" + UNSETTLED), _rule(field, y, 0.55, "template" + UNSETTLED)]
        }
        fields, _ = fuse({"rules": rules, "template": template}, [], fields=(field,))
        got = fields[field]
        assert got.value == y and got.status != VERIFIED and "contested" in got.evidence
        assert any(f"another reading: {x}" in r for r in got.reasons)  # the template's first choice still contests


def test_the_template_agreeing_is_never_enough_for_verified():
    """The floor of B-393 keeps a value as certain as the other readers alone make it, never more than likely."""
    rules = {"invoice_number": [_rule("invoice_number", "INV-10230", 0.95, "label-inline")]}
    for method in ("template", "template" + UNSETTLED):
        template = {"invoice_number": [_rule("invoice_number", "INV-10230", 0.6, method)]}
        plain, _ = fuse({"rules": rules}, [], fields=("invoice_number",))
        taught, _ = fuse({"rules": rules, "template": template}, [], fields=("invoice_number",))
        assert plain["invoice_number"].status != VERIFIED
        if method != "template":
            assert taught["invoice_number"].status != VERIFIED
            assert taught["invoice_number"].confidence <= max(plain["invoice_number"].confidence, 0.97)


# --- A supplier's stream: touchless after a layout change ------------------------------------------------------


def _stream_page(path: Path, i: int, header: list[tuple[str, str]], top: list[tuple] = ()) -> Path:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    items = [(50, 60, "Northwind Supply Ltd.", 16, "hebo"), (50, 78, "200 Industrial Rd, Burnaby BC", 9, "helv"),
             (50, 90, "GST/HST No. 123456709RT0001", 9, "helv"), (400, 60, "INVOICE", 18, "hebo"),
             (50, 130, "Bill To:", 10, "hebo"), (50, 144, "Acme Holdings Inc.", 10, "helv"), *top]  # fmt: skip
    for k, (label, value) in enumerate(header):
        items += [(360, 130 + 14 * k, label, 10, "hebo"), (460, 130 + 14 * k, value, 10, "helv")]
    sub = 1000.0 + 37 * i
    rows = [("Subtotal", sub), ("GST 5%", round(sub * 0.05, 2)), ("Total", round(sub * 1.05, 2))]
    items += [(50, 260, "Description", 10, "hebo"), (500, 260, "Amount", 10, "hebo"),
              (50, 276, "Industrial fasteners", 10, "helv"), (500, 276, f"{sub:,.2f}", 10, "helv")]  # fmt: skip
    items += [(x, 420 + 14 * k, t, 10, f) for k, (lab, v) in enumerate(rows) for x, t, f in
              ((380, lab, "hebo"), (500, f"{v:,.2f}", "helv"))]  # fmt: skip
    for x, y, text, size, font in items:
        page.insert_text((x, y), text, fontsize=size, fontname=font)
    doc.save(path)
    return path


def _stream(tmp_path: Path, pages) -> list:
    """Read each invoice with the template learned from the approved earlier ones; (truth date, result) per invoice."""
    import datetime as dt

    from ap_coder.capture import analyze
    from ap_coder.capture.supplier import confirmed_values

    template, out = None, []
    for i, (header, top, date) in enumerate(pages):
        path = _stream_page(tmp_path / f"s{i}.pdf", i, header, top)
        layout = _layout(path)
        sub = 1000.0 + 37 * i
        truth = {"vendor_name": "Northwind Supply Ltd.", "invoice_number": f"INV-{10230 + i}", "invoice_date": date,
                 "subtotal": sub, "gst_amount": round(sub * 0.05, 2), "grand_total": round(sub * 1.05, 2),
                 "gst_hst_registration_number": "123456709RT0001"}  # fmt: skip
        vendor = {"name": truth["vendor_name"], "gst_number": truth["gst_hst_registration_number"], "erp_id": ""}
        today = dt.date.fromisoformat(date) + dt.timedelta(days=3)
        out.append((date, analyze(path, template=template, vendor=vendor, layout=layout, today=today, tables=False)))
        template = learn(template, layout, confirmed_values(truth))
    return out


def _date(i: int, back: int = 0) -> str:
    import datetime as dt

    return (dt.date(2026, 1, 5) + dt.timedelta(days=7 * i - back)).isoformat()


def test_a_renamed_label_does_not_make_the_order_date_a_verified_invoice_date(tmp_path):
    """The supplier renames "Invoice Date:" to "Billed on:" and adds an order "Date:" row under it: the invoice date
    was the order date, verified, and the invoice went touchless (review of B-387, `relayout3`)."""
    from ap_coder.capture.supplier import AUTONOMOUS, should_auto_approve

    old = [([("Invoice #", f"INV-{10230 + i}"), ("Invoice Date:", _date(i))], [], _date(i)) for i in range(4)]
    new = [([("Invoice #", f"INV-{10230 + i}"), ("Billed on:", _date(i)), ("Date:", _date(i, 2 + i % 3))], [], _date(i))
           for i in range(4, 7)]  # fmt: skip
    for date, result in _stream(tmp_path, old + new)[4:]:
        got = result.fields["invoice_date"]
        if got.value != date:
            assert got.status != VERIFIED
            assert not should_auto_approve(AUTONOMOUS, result, checks_ok=True)[0]


def test_a_date_learned_where_the_reader_read_another_date_is_not_a_second_reader(tmp_path):
    """The invoice date printed only beside the title; "Date:" (the order date) was the same day on the first invoice:
    the template learned the date where the rule reader read it, at "Date:", and agreed with the reader's wrong date
    on the next invoice: verified (review of B-388, `p2`)."""
    pages = [([("Invoice No.:", f"INV-{10230 + i}"), ("Date:", _date(i, 0 if i == 0 else 2 + i % 3))],
              [(400, 80, _date(i), 10, "helv")], _date(i)) for i in range(4)]  # fmt: skip
    for date, result in _stream(tmp_path, pages)[1:]:
        got = result.fields["invoice_date"]
        assert got.value == date or got.status != VERIFIED


def test_a_new_label_at_the_old_spot_starts_unsettled(tmp_path):
    """The supplier's new layout prints an order "Date:" where "Invoice Date:" was, and the invoice date beside the
    title; on its first invoice the two were the same day. The template took the new label for the old spot's, kept the
    spot's settled record, and verified the order date as the invoice date from then on (`relayout`, base too)."""
    old = [([("Invoice #", f"INV-{10230 + i}"), ("Invoice Date:", _date(i))], [], _date(i)) for i in range(4)]
    new = [([("Invoice #", f"INV-{10230 + i}"), ("Date:", _date(i, 0 if i == 4 else 2 + i % 3))],
            [(400, 80, _date(i), 10, "helv")], _date(i)) for i in range(4, 8)]  # fmt: skip
    for date, result in _stream(tmp_path, old + new)[4:]:
        got = result.fields["invoice_date"]
        assert got.value == date or got.status != VERIFIED


def test_a_value_printed_twice_alike_is_vouched_for_after_two_approvals(tmp_path):
    """The invoice number printed in the header and on the remittance stub: two places, the same field. Two approvals in
    a row with it at both spots settle them (the template agrees with itself); a spot that then holds another value
    than the one approved starts over."""

    def invoice(name: str, top: str, stub: str) -> Path:
        return _pdf(tmp_path / name, [(40, 50, "Acme Supply Ltd."), (330, 90, "Invoice No:"), (430, 90, top),
                                      (330, 700, "Remit with invoice"), (460, 700, stub)])  # fmt: skip

    template = None
    for n in range(2):
        number = f"INV-{100 + n}"
        template = learn(template, _layout(invoice(f"a{n}.pdf", number, number)), {"invoice_number": (number, None)})
        found = apply_template(template, _layout(invoice("x.pdf", "INV-9", "INV-9")))["invoice_number"]
        vouched = [UNSETTLED not in r.method for r in found]
        assert any(vouched) == (n == 1)
    differ = apply_template(template, _layout(invoice("b.pdf", "INV-200", "SO-555")))["invoice_number"]
    assert all(UNSETTLED in r.method for r in differ)  # its own spots disagree: it vouches for neither
    template = learn(template, _layout(invoice("b.pdf", "INV-200", "SO-555")), {"invoice_number": ("INV-200", None)})
    stub = [v for v in template.fields["invoice_number"] if v.missed]
    assert stub and not stub[0].vouched


def test_a_total_tax_only_the_template_read_gives_way_to_the_taxes_on_the_page():
    """GST printed alone, the total tax learned on the same words (unsettled): the template's total tax was the only
    reading, so it was shown at *check* instead of being worked out from the taxes (the learning benchmark)."""
    rules = {"subtotal": [_rule("subtotal", 100.0, 0.9)], "gst_amount": [_rule("gst_amount", 5.0, 0.9)],
             "grand_total": [_rule("grand_total", 105.0, 0.9, "label-right+adds-up")]}  # fmt: skip
    template = {"tax_total": [_rule("tax_total", 5.0, 0.8, "template" + UNSETTLED)]}
    fields, _ = fuse({"rules": rules, "template": template}, [])
    plain, _ = fuse({"rules": rules}, [])
    assert fields["tax_total"].value == 5.0 and fields["tax_total"].status == plain["tax_total"].status
