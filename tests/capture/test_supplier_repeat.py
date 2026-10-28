"""A supplier's next invoice after AP corrected one: wrong this time, right next time. The template read on an invoice
like the ones it learned from, a vendor name read wrong the same way each time (remembered once AP corrects it), what
an untouched bulk approval may teach, and a withdrawn approval's teaching taken back after a later one."""

from pathlib import Path

import pymupdf

from ap_coder.capture import build_layout
from ap_coder.capture.supplier import apply_template, learn
from ap_coder.capture.types import DocLayout
from tests.pdfs import text_pdf


def _layout(path: Path) -> DocLayout:
    return build_layout(path, ocr=False)


def _right(x1: float, y: float, text: str, size: float = 10) -> tuple:
    """``text`` set to end at ``x1`` (right-aligned, as totals are)."""
    return (x1 - pymupdf.get_text_length(text, fontsize=size), y, text)


def _french_totals(path: Path, subtotal: str, tps: str, tvq: str, total: str) -> Path:
    return text_pdf(path, [
        (40, 50, "Fournitures Laval inc."), (330, 90, "Facture no :"), (430, 90, "F-1001"),
        (330, 560, "Sous-total :"), _right(560, 560, subtotal), (330, 575, "TPS :"), _right(560, 575, tps),
        (330, 590, "TVQ :"), _right(560, 590, tvq), (330, 605, "TOTAL :"), _right(560, 605, total),
    ])  # fmt: skip


def test_a_total_over_a_thousand_printed_with_a_space_is_read_whole(tmp_path):
    """Learned from totals under a thousand ("211,29"), the template read "11 211,29" as 211.29: the shape it knew beat
    the longer reading of the same words (bench learn, quebec_fr supplier, invoice 5: subtotal, QST, tax and total
    wrong, worse than without the template). Digits grouped by a space are one amount."""
    template = None
    for n, (sub, tps, tvq, tot) in enumerate([("211,29", "10,56", "21,08", "242,93"),
                                              ("305,10", "15,26", "30,43", "350,79")]):  # fmt: skip
        layout = _layout(_french_totals(tmp_path / f"a{n}.pdf", sub, tps, tvq, tot))
        amounts = {"subtotal": sub, "gst_amount": tps, "qst_amount": tvq, "grand_total": tot}
        confirmed = {f: (float(v.replace(",", ".")), None) for f, v in amounts.items()}
        template = learn(template, layout, confirmed)
    later = _layout(_french_totals(tmp_path / "b.pdf", "11 211,29", "560,56", "1 118,33", "12 890,18"))
    got = apply_template(template, later)
    assert got["subtotal"][0].value == 11211.29
    assert got["qst_amount"][0].value == 1118.33
    assert got["grand_total"][0].value == 12890.18
    assert got["gst_amount"][0].value == 560.56
    # Only the whole amount is offered: its last three digits are no rival reading.
    assert 211.29 not in [r.value for r in got["subtotal"]]


# --- A vendor name read wrong the same way on every invoice -------------------------------------------------------

PRINTED, NAME = "N0rthwind Supp1y Ltd.", "Northwind Supply Ltd."


def _misread_invoice(path: Path, number: str, total: str) -> Path:
    """No GST/HST number: nothing but the name (read wrong: a zero and a one) to know the supplier by."""
    return text_pdf(path, [
        (40, 50, PRINTED, 14), (330, 90, "Invoice No:"), (430, 90, number),
        (330, 105, "Date:"), (430, 105, "2026-09-12"),
        (40, 300, "Description"), (450, 300, "Amount"), (40, 320, "Toner"), (450, 320, total),
        (330, 560, "Subtotal:"), (450, 560, total), (330, 590, "Total:"), (450, 590, total),
    ])  # fmt: skip


def _approve(store, path: Path, capture, final: dict, bulk: bool = False) -> int:
    from ap_coder.capture.workflow import learn_from_approval

    proposed = {f: fr.value for f, fr in capture.fields.items() if fr.value not in (None, "")}
    invoice_id = store.add_invoice(path, {**proposed, "line_items": []}, None)
    store.save_capture(invoice_id, capture.to_dict())
    coding = {**proposed, **final, "line_items": []}
    store.approve_invoice(invoice_id, coding, "Ann", bulk=bulk)
    learn_from_approval(store, invoice_id, coding, actor="Ann", bulk=bulk)
    return invoice_id


def test_a_vendor_name_ap_corrected_is_known_again_on_the_next_invoice(tmp_path, store):
    """The name printed reads "N0rthwind Supp1y Ltd." on every invoice and there is no GST/HST number: AP corrected it
    to Northwind Supply Ltd., but the next invoice was looked up by the name as read, so it had no template, no vendor
    history and no lessons, and AP corrected the name again. The misread name AP corrected is remembered: the next
    invoice is the vendor's, under its name, with its template."""
    from ap_coder.ai.offline_coder import read_invoice

    first = _misread_invoice(tmp_path / "a.pdf", "NW-1001", "100.00")
    capture, key, profile = read_invoice(first, store)
    assert capture.fields["vendor_name"].value == PRINTED and profile is None
    _approve(store, first, capture, {"vendor_name": NAME})

    later = _misread_invoice(tmp_path / "b.pdf", "NW-1002", "250.00")
    capture, key, profile = read_invoice(later, store)
    assert key == store.supplier_key_for(NAME) and (profile or {}).get("template")  # the vendor's own template
    assert capture.fields["vendor_name"].value == NAME
    assert capture.fields["invoice_number"].value == "NW-1002"
    assert store.vendor_key_for(PRINTED) == store.vendor_key_for(NAME)  # its coding memory and lessons
    # Corrected once: not vouched for yet. Approved again under that name: it is.
    assert capture.fields["vendor_name"].status != "verified"
    _approve(store, later, capture, {})
    third = read_invoice(_misread_invoice(tmp_path / "c.pdf", "NW-1003", "75.00"), store)[0]
    assert third.fields["vendor_name"].value == NAME


def test_the_misread_name_finds_the_vendor_in_the_vendor_master(tmp_path, store):
    from ap_coder.capture.bridge import vendor_record

    store.import_vendor_master([{"vendor_name": NAME, "gst": "", "erp_id": "V-17", "status": "active"}])
    assert vendor_record(store, PRINTED) is None
    first = _misread_invoice(tmp_path / "a.pdf", "NW-1001", "100.00")
    from ap_coder.ai.offline_coder import read_invoice

    capture, _, _ = read_invoice(first, store)
    _approve(store, first, capture, {"vendor_name": NAME})
    assert (store.find_vendor(PRINTED) or {}).get("erp_id") == "V-17"
    assert store.supplier_key_for(PRINTED) == "id:V-17"
    capture, _, _ = read_invoice(_misread_invoice(tmp_path / "b.pdf", "NW-1002", "250.00"), store)
    assert capture.fields["vendor_name"].value == NAME
    assert not [c for c in capture.checks if c["code"] == "VENDOR_NAME_MATCH" and not c["ok"]]


def test_a_withdrawn_correction_forgets_the_name_and_another_vendor_is_never_taken_for_it(tmp_path, store):
    from ap_coder.ai.offline_coder import read_invoice

    first = _misread_invoice(tmp_path / "a.pdf", "NW-1001", "100.00")
    capture, _, _ = read_invoice(first, store)
    invoice_id = _approve(store, first, capture, {"vendor_name": NAME})
    assert store.vendor_alias(PRINTED)
    store.reopen(invoice_id, "Ann", "wrong vendor")
    assert store.vendor_alias(PRINTED) is None
    # A name that does not look like the one printed (the customer's, read as the vendor) is not remembered for it.
    capture, _, _ = read_invoice(first, store)
    _approve(store, first, capture, {"vendor_name": "Fabrikam Industries Inc."})
    assert store.vendor_alias(PRINTED) is None
    # A GST/HST number on the invoice that is not the vendor's: not that vendor.
    store.remember_vendor_alias(PRINTED, NAME, "123456782", invoice_id=None)
    assert store.vendor_alias(PRINTED, "897654321RT0001") is None
    assert store.vendor_alias(PRINTED, "123456782 RT0001")["vendor_name"] == NAME


# --- What an approval teaches the template: a bulk approval, a withdrawn one -------------------------------------


def _numbered(path: Path, number: str, date: str, stub: bool = False) -> Path:
    return text_pdf(path, [
        (40, 50, "Acme Supply Ltd."), (330, 90, "Invoice No:"), (430, 90, number),
        (330, 105, "Date:"), (430, 105, date),
        *([(330, 700, "Remit with invoice"), (460, 700, number)] if stub else []),
        (330, 560, "Total:"), (450, 560, "100.00"),
    ])  # fmt: skip


def _read(store, path: Path):
    from ap_coder.capture.workflow import capture_invoice

    return capture_invoice(path, {"vendor_name": "Acme Supply Ltd."}, store=store)[0]


def _variants(store, field: str) -> list:
    from ap_coder.capture.supplier import Template

    template = Template.from_dict((store.get_supplier_profile(store.supplier_key_for("Acme Supply Ltd.")) or {})
                                  .get("template"))  # fmt: skip
    return template.fields.get(field, [])


def test_an_untouched_bulk_approval_does_not_vouch_for_a_spot(tmp_path, store):
    """Approved in bulk, nobody opened the invoice: its values are what was proposed, right or wrong, and nobody
    confirmed one. It taught the template like a reviewed approval, so a spot of a value printed twice (vouched for
    after two approvals in a row) was vouched for by an approval nobody looked at."""
    from ap_coder.capture.types import UNSETTLED

    a = _numbered(tmp_path / "a.pdf", "INV-100", "2026-09-01", stub=True)
    _approve(store, a, _read(store, a), {"invoice_number": "INV-100"})
    b = _numbered(tmp_path / "b.pdf", "INV-101", "2026-09-08", stub=True)
    _approve(store, b, _read(store, b), {}, bulk=True)
    spots = _variants(store, "invoice_number")
    assert spots and not any(v.vouched for v in spots)
    c = _numbered(tmp_path / "c.pdf", "INV-102", "2026-09-15", stub=True)
    from ap_coder.capture.supplier import apply_template

    profile = store.get_supplier_profile(store.supplier_key_for("Acme Supply Ltd."))
    assert all(UNSETTLED in r.method for r in apply_template(profile["template"], _layout(c))["invoice_number"])
    _approve(store, c, _read(store, c), {})  # a person approved it: the spots are vouched for now
    assert any(v.vouched for v in _variants(store, "invoice_number"))


def test_withdrawing_an_approval_takes_back_its_teaching_after_a_later_approval(tmp_path, store, tmp_path_factory):
    """The clerk clicked the date for the invoice number and approved; the next invoice was approved before a second
    look caught it. The withdrawal put nothing back ("kept: a later approval changed the vendor's layout"): the right
    spot kept the label and the misses the wrong value gave it. What the withdrawn approval taught is taken back: the
    template is learned again from the later approvals, as if it had never been approved."""
    from ap_coder.capture import build_layout
    from ap_coder.capture.supplier import _find_spans
    from ap_coder.capture.workflow import learn_from_approval
    from ap_coder.core.store import Store

    pages = [_numbered(tmp_path / f"{n}.pdf", f"INV-{100 + n}", f"2026-09-{n + 1:02d}") for n in range(3)]
    _approve(store, pages[0], _read(store, pages[0]), {"invoice_number": "INV-100"})
    # The slip: the invoice date's words taught as the invoice number.
    words = _find_spans(build_layout(pages[1], ocr=False), "invoice_date", "2026-09-02")[0][1]
    capture = _read(store, pages[1])
    proposed = {f: fr.value for f, fr in capture.fields.items() if fr.value not in (None, "")}
    slip = store.add_invoice(pages[1], {**proposed, "line_items": []}, None)
    store.save_capture(slip, capture.to_dict())
    coding = {**proposed, "invoice_number": "2026-09-02", "line_items": []}
    store.approve_invoice(slip, coding, "Ann")
    learn_from_approval(store, slip, coding, actor="Ann", taught={"invoice_number": [w.box for w in words]})
    _approve(store, pages[2], _read(store, pages[2]), {"invoice_number": "INV-102"})  # AP corrected it
    store.reopen(slip, "Ann", "the wrong words were taught")

    # As if the slip had never been approved: the first and the third invoices only.
    other = Store(tmp_path_factory.mktemp("clean") / "s.db")
    for n in (0, 2):
        _approve(other, pages[n], _read(other, pages[n]), {"invoice_number": f"INV-{100 + n}"})

    def record(s):
        return sorted(
            (v.count, v.settled, v.streak, v.missed, (v.anchor.text if v.anchor else ""))
            for v in _variants(s, "invoice_number")
        )

    assert record(store) == record(other)
    assert "taken back" in str(store.events(actions=["reopened"])[0]["detail"])


def test_two_dates_side_by_side_are_never_read_as_one(tmp_path):
    """A header grid prints "September 10, 2025" and the due date "October 10, 2025" side by side: the template learned
    the due date on "10, 2025 October" (the invoice date's day and year, the due date's month) and read the due date as
    that text, not a date (bench learn, bilingual supplier, invoices 24, 25 and 30: worse than without the template)."""
    from ap_coder.capture.supplier import date_candidates

    months = ["February", "March", "May", "June", "July", "August", "September"]

    def invoice(n: int) -> Path:
        dated, due = f"{months[n]} {10 + n}, 2025", f"{months[n + 1]} {10 + n}, 2025"
        return text_pdf(tmp_path / f"d{n}.pdf", [
            (40, 50, "Contoso Ltd."), (60, 250, "DATE / Date de facturation"), (160, 250, "Date Due / Échéance"),
            (60, 264, dated), (60 + pymupdf.get_text_length(dated, fontsize=10) + 18, 264, due),
        ])  # fmt: skip

    template = None
    for n in range(6):
        layout = _layout(invoice(n))
        for reading in (apply_template(template, layout).get("due_date") or []) if template else []:
            assert date_candidates(reading.raw) and str(reading.value)[4:5] == "-"  # a date, never words
        dated, due = (f"2025-{[2, 3, 5, 6, 7, 8, 9][k]:02d}-{10 + n:02d}" for k in (n, n + 1))
        template = learn(template, layout, {"invoice_date": (dated, None), "due_date": (due, None)})
    for variant in template.fields["due_date"]:
        assert all(not p.startswith("9") for p in variant.shape.patterns)  # learned on the due date's own words
    assert date_candidates("10, 2025 October") == [] and date_candidates("2025 March 15,") == []
    assert date_candidates("March 15, 2025")[0][0] == "2025-03-15" and date_candidates("15 mars 2025")
