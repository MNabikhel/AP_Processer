"""What AP sets on a vendor's invoices that they do not print (its terms, its currency): proposed on the next one
without the chat model (D-121)."""

from pathlib import Path

from tests.pdfs import text_pdf

VENDOR = "Lakeshore Fasteners Inc."


def _invoice(path: Path, number: str, total: str, terms: str = "", currency: str = "") -> Path:
    items = [(40, 50, VENDOR, 14), (330, 90, "Invoice No:"), (430, 90, number), (330, 105, "Date:"),
             (430, 105, "2026-09-12"), (40, 300, "Description"), (450, 300, "Amount"), (40, 320, "Hex bolts"),
             (450, 320, total), (330, 560, "Subtotal:"), (450, 560, total), (330, 590, "Total:"),
             (450, 590, f"{currency} {total}".strip())]  # fmt: skip
    if terms:
        items += [(330, 120, "Terms:"), (430, 120, terms)]
    return text_pdf(path, items)


def _read(store, path: Path):
    from ap_coder.ai.offline_coder import read_invoice

    return read_invoice(path, store)[0]


def _approve(store, path: Path, capture, changes: dict) -> int:
    """AP approves what was read, with ``changes`` (the fields AP set)."""
    from ap_coder.capture.workflow import learn_from_approval

    proposed = {f: fr.value for f, fr in capture.fields.items() if fr.value not in (None, "")}
    invoice_id = store.add_invoice(path, {**proposed, "line_items": []}, None)
    store.save_capture(invoice_id, capture.to_dict())
    final = {**proposed, **changes, "line_items": []}
    store.approve_invoice(invoice_id, final, "Ann")
    learn_from_approval(store, invoice_id, final, actor="Ann")
    return invoice_id


def _field(capture, name: str) -> tuple:
    fr = capture.fields.get(name)
    return (fr.value, fr.status) if fr is not None and fr.status != "missing" else (None, "missing")


def test_terms_and_currency_ap_sets_on_every_invoice_are_proposed_without_the_chat_model(tmp_path, store):
    """The vendor's invoices print neither its terms (Net 45, agreed with it) nor its currency (it bills in US dollars
    from a Canadian address, "$" alone): AP set both on every invoice. The lesson was kept, but only the chat model
    read it: without LM Studio AP set them again each time, and each counted as a correction, so the vendor never
    reached touchless. Once AP has set them twice, they are proposed: likely until AP has approved them so twice,
    then verified."""
    seen = []
    for n in range(6):
        path = _invoice(tmp_path / f"{n}.pdf", f"LF-{100 + n}", "250.00")
        capture = _read(store, path)
        seen.append((_field(capture, "payment_terms"), _field(capture, "currency")))
        _approve(store, path, capture, {"payment_terms": "Net 45", "currency": "USD"})
    assert seen[0] == ((None, "missing"), (None, "missing"))  # the first two AP sets
    assert seen[2] == (("Net 45", "likely"), ("USD", "likely"))  # set twice: proposed
    assert seen[4] == (("Net 45", "verified"), ("USD", "verified"))  # approved so twice more: vouched for
    stats = store.supplier_stats(store.supplier_key_for(VENDOR))
    assert stats.fields["payment_terms"].correct >= 3  # no longer a correction on each invoice

    from ap_coder.ai.offline_coder import code_from_capture
    from ap_coder.core.reference_data import load_reference_data
    from tests.conftest import DATA

    reference = load_reference_data(DATA / "chart_of_accounts.csv", DATA / "cost_centers.csv",
                                    DATA / "tax_gl_mapping.csv", DATA / "coding_policy.md")  # fmt: skip
    coding = code_from_capture(_read(store, _invoice(tmp_path / "x.pdf", "LF-200", "90.00")), reference, [], store)
    assert (coding.coding.currency, coding.coding.payment_terms) == ("USD", "Net 45")  # what is posted


def test_a_value_the_invoice_prints_stands_and_one_correction_waits(tmp_path, store):
    for n in range(2):
        path = _invoice(tmp_path / f"{n}.pdf", f"LF-{100 + n}", "250.00")
        _approve(store, path, _read(store, path), {"payment_terms": "Net 45"})
    printed = _read(store, _invoice(tmp_path / "p.pdf", "LF-300", "80.00", terms="Net 15"))
    assert _field(printed, "payment_terms")[0] == "Net 15"  # this invoice says otherwise: the page wins

    other = "Harbour Supply Co."
    path = tmp_path / "h.pdf"
    text_pdf(path, [(40, 50, other, 14), (330, 90, "Invoice No:"), (430, 90, "H-1"), (330, 560, "Total:"),
                    (450, 560, "10.00")])  # fmt: skip
    capture = _read(store, path)
    _approve(store, path, capture, {"payment_terms": "Net 60"})
    assert _field(_read(store, path), "payment_terms") == (None, "missing")  # set once: maybe a slip, it waits


def test_ap_keeping_another_value_ends_the_lesson(tmp_path, store):
    for n in range(2):
        path = _invoice(tmp_path / f"{n}.pdf", f"LF-{100 + n}", "250.00")
        _approve(store, path, _read(store, path), {"payment_terms": "Net 45"})
    path = _invoice(tmp_path / "2.pdf", "LF-102", "250.00")
    capture = _read(store, path)
    assert _field(capture, "payment_terms")[0] == "Net 45"
    _approve(store, path, capture, {"payment_terms": "Net 30"})  # the vendor changed its terms
    capture = _read(store, _invoice(tmp_path / "3.pdf", "LF-103", "250.00"))
    assert _field(capture, "payment_terms")[0] in (None, "Net 30")  # never the old terms again


def test_the_review_screen_says_the_value_is_as_ap_set_it(tmp_path, store):
    from ap_coder.web.invoice_view import capture_confidence

    for n in range(3):
        path = _invoice(tmp_path / f"{n}.pdf", f"LF-{100 + n}", "250.00")
        capture = _read(store, path)
        _approve(store, path, capture, {"payment_terms": "Net 45"})
    badge = capture_confidence(capture)["payment_terms"]
    assert badge["text"].startswith("As you set it") and "this vendor's last invoices" in badge["help"]


def test_a_number_the_reader_takes_for_the_po_each_time_is_cleared_once_ap_cleared_it_twice(tmp_path, store):
    """The vendor prints our customer account number under "PO / Account:": the reader takes it for the PO number on
    every invoice, and AP clears it each time. The template learns where values are, not that there is none: it is
    cleared once AP has cleared it twice."""

    def invoice(n: int) -> Path:
        return text_pdf(tmp_path / f"{n}.pdf", [
            (40, 50, VENDOR, 14), (330, 90, "Invoice No:"), (430, 90, f"LF-{100 + n}"), (330, 105, "Date:"),
            (430, 105, "2026-09-12"), (330, 120, "PO #:"), (430, 120, "AC-55512"), (330, 560, "Total:"),
            (450, 560, "40.00")])  # fmt: skip

    seen = []
    for n in range(4):
        capture = _read(store, invoice(n))
        seen.append(_field(capture, "po_number")[0])
        _approve(store, tmp_path / f"{n}.pdf", capture, {"po_number": ""})
    assert seen[:2] == ["AC-55512", "AC-55512"] and seen[2:] == [None, None]
