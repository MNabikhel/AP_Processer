"""A supplier's line descriptions as AP approved them, known again on its next invoice (D-120)."""

from pathlib import Path

from ap_coder.capture.line_memory import apply, drops_codes, fold, item_code, observations
from ap_coder.capture.types import LineReading
from tests.pdfs import text_pdf

VENDOR = "Harbour Industrial Supply"
# How a scan of this supplier's invoices reads two of its items: the item code run into the description, and the
# spaces lost.
BEARING_READ, BEARING = "PB-93930 Bearing6205-2RS", "Bearing 6205-2RS"
WRAP_READ, WRAP = "Palletwrap18in.x1500ft", "Pallet wrap 18 in. x 1500 ft"


def _invoice(path: Path, number: str, lines: list[tuple[str, str, str, str]]) -> Path:
    items = [(40, 50, VENDOR, 14), (330, 90, "Invoice No:"), (430, 90, number), (330, 105, "Date:"),
             (430, 105, "2026-09-12"), (40, 300, "Description"), (300, 300, "Qty"), (370, 300, "Unit Price"),
             (470, 300, "Amount")]  # fmt: skip
    for n, (text, qty, price, amount) in enumerate(lines):
        y = 320 + 15 * n
        items += [(40, y, text), (300, y, qty), (370, y, price), (470, y, amount)]
    total = f"{sum(float(a) for *_, a in lines):.2f}"
    items += [(330, 560, "Subtotal:"), (470, 560, total), (330, 590, "Total:"), (470, 590, total)]
    return text_pdf(path, items)


def _code(store, reference, path: Path):
    """Read and code ``path`` as the app does without an AI model: (capture, the proposed coding as stored)."""
    from ap_coder.ai.offline_coder import code_from_capture, read_invoice

    capture = read_invoice(path, store)[0]
    feedback = store.learning_rows(VENDOR)
    coding = code_from_capture(capture, reference, feedback, store).coding
    return capture, coding.model_dump(mode="json")


def _approve(store, path: Path, capture, proposed: dict, edits: dict[int, dict], bulk: bool = False) -> int:
    """AP approves ``proposed`` with ``edits`` (line number -> the line's fields changed)."""
    from ap_coder.capture.workflow import learn_from_approval

    invoice_id = store.add_invoice(path, proposed, None)
    store.save_capture(invoice_id, capture.to_dict())
    final = {**proposed, "line_items": [{**li, **edits.get(li["line_number"], {})} for li in proposed["line_items"]]}
    store.approve_invoice(invoice_id, final, "Ann", bulk=bulk)
    learn_from_approval(store, invoice_id, final, actor="Ann", bulk=bulk)
    return invoice_id


def test_a_line_description_ap_corrected_reads_right_on_the_next_invoice_and_keeps_its_account(tmp_path, store,
                                                                                                reference):  # fmt: skip
    """A scan reads the supplier's items with the item code run in and the spaces lost, the same way on each invoice.
    AP corrected both lines and coded them: the next invoice read them as printed again, so AP corrected them again,
    and the coding memory did not know the lines by their misread names, so their accounts were left blank. The next
    invoice's lines read as AP approved them, and the accounts AP gave them follow."""
    first = _invoice(tmp_path / "a.pdf", "HI-2001", [(BEARING_READ, "3", "8.60", "25.80"),
                                                     (WRAP_READ, "1", "21.06", "21.06")])  # fmt: skip
    capture, proposed = _code(store, reference, first)
    assert [li["description"] for li in proposed["line_items"]] == [BEARING_READ, WRAP_READ]
    edits = {1: {"description": BEARING, "predicted_gl_code": "5000"},
             2: {"description": WRAP, "predicted_gl_code": "5100"}}  # fmt: skip
    invoice_id = _approve(store, first, capture, proposed, edits)
    from ap_coder.capture.workflow import learn_from_approval, taught_message

    learned = learn_from_approval(store, invoice_id, store.get_invoice(invoice_id)["final_output"], actor="Ann")
    assert "Remembered your 2 line descriptions" in taught_message(store, learned)  # said on approving

    later = _invoice(tmp_path / "b.pdf", "HI-2002", [(WRAP_READ, "2", "21.06", "42.12"),
                                                     (BEARING_READ, "10", "8.60", "86.00")])  # fmt: skip
    capture, proposed = _code(store, reference, later)
    lines = proposed["line_items"]
    assert [li["description"] for li in lines] == [WRAP, BEARING]
    assert [li["amount"] for li in lines] == [42.12, 86.0]  # the numbers are this invoice's own
    assert [li.read_as for li in capture.line_items] == [WRAP_READ, BEARING_READ]  # kept, for the review screen
    assert "read “Palletwrap18in.x1500ft”" in lines[0]["reasoning_justification"]
    assert [li["predicted_gl_code"] for li in lines] == ["5100", "5000"]


def test_withdrawing_the_approval_forgets_its_line_names(tmp_path, store, reference):
    first = _invoice(tmp_path / "a.pdf", "HI-2001", [(WRAP_READ, "1", "21.06", "21.06")])
    capture, proposed = _code(store, reference, first)
    invoice_id = _approve(store, first, capture, proposed, {1: {"description": WRAP}})
    assert store.line_texts(store.supplier_key_for(VENDOR))
    store.reopen(invoice_id, "Ann", "approved by mistake")
    assert store.line_texts(store.supplier_key_for(VENDOR)) == []
    capture, proposed = _code(store, reference, _invoice(tmp_path / "b.pdf", "HI-2002",
                                                         [(WRAP_READ, "1", "21.06", "21.06")]))  # fmt: skip
    assert proposed["line_items"][0]["description"] == WRAP_READ


def test_an_untouched_bulk_approval_teaches_no_line_names_and_ap_s_latest_decision_wins(tmp_path, store, reference):
    """Nobody opened a bulk approval: its lines as read are no decision of AP's. Approving a renamed line back as
    read takes the renaming back (AP's latest decision, D-118)."""
    lines = [(WRAP_READ, "1", "21.06", "21.06")]
    capture, proposed = _code(store, reference, _invoice(tmp_path / "a.pdf", "HI-2001", lines))
    _approve(store, tmp_path / "a.pdf", capture, proposed, {}, bulk=True)
    assert store.line_texts(store.supplier_key_for(VENDOR)) == []

    capture, proposed = _code(store, reference, _invoice(tmp_path / "b.pdf", "HI-2002", lines))
    _approve(store, tmp_path / "b.pdf", capture, proposed, {1: {"description": WRAP}})
    capture, proposed = _code(store, reference, _invoice(tmp_path / "c.pdf", "HI-2003", lines))
    assert proposed["line_items"][0]["description"] == WRAP
    _approve(store, tmp_path / "c.pdf", capture, proposed, {1: {"description": WRAP_READ}})  # AP wants it as printed
    capture, proposed = _code(store, reference, _invoice(tmp_path / "d.pdf", "HI-2004", lines))
    assert proposed["line_items"][0]["description"] == WRAP_READ


def test_read_the_same_way_is_spacing_case_punctuation_and_letters_ocr_mixes_up_never_another_part_number():
    assert fold("Hexbo1t 3/8-16x2 in.,zinc") == fold("Hex bolt 3/8-16 x 2 in., zinc")
    assert fold(WRAP_READ) == fold(WRAP)
    assert fold("Safety glasses,clear，ANsI Z87") == fold("Safety glasses, clear, ANSI Z87")
    assert fold("Bearing 6205-2RS") != fold("Bearing 6206-2RS")
    assert fold("Toner 58X") != fold("Toner 58A")


def test_an_item_code_is_letters_and_digits_before_the_words_never_a_quantity_size_or_year():
    assert item_code(BEARING_READ) == ("PB-93930", "Bearing6205-2RS")
    assert item_code("100234 Widget, blue") == ("100234", "Widget, blue")
    for text in ("2026 Annual plan", "8.5x11 paper, case", "2 x Widget", "Calibration of gauges", "LED panel 2x4"):
        assert item_code(text) is None, text


def test_a_new_items_code_is_left_out_once_ap_has_dropped_two_and_kept_none_since():
    def lines(*texts: str) -> list[LineReading]:
        return [LineReading(t, 1.0, 1.0, 1.0) for t in texts]

    def approved(printed: str, description: str) -> list[dict]:
        return observations([(printed, printed, description)])

    one = approved("AR-63142 Diesel exhaust fluid 9.46 L", "Diesel exhaust fluid 9.46 L")
    new = lines("MM-66636 Software subscription - 12 users")
    assert not drops_codes(one) and apply(new, one) == 0  # dropped once: maybe that line only
    two = approved("BK-75124 Calibration of pressure gauges", "Calibration of pressure gauges") + one
    assert drops_codes(two)
    assert apply(new, two) == 1 and new[0].description == "Software subscription - 12 users"
    kept = approved("PC-29617 Nitrile gloves, box of 100", "PC-29617 Nitrile gloves, box of 100") + two
    assert not drops_codes(kept)  # AP's latest decision keeps the code: it is part of this supplier's descriptions
    known = lines("AR-63142 Dieselexhaustfluid9.46L")
    assert apply(known, kept) == 1 and known[0].description == "Diesel exhaust fluid 9.46 L"  # an item AP named


def test_the_learning_page_lists_a_corrected_description_and_that_it_read_right_the_next_time(tmp_path, store,
                                                                                               reference):  # fmt: skip
    lines = [(WRAP_READ, "1", "21.06", "21.06")]
    capture, proposed = _code(store, reference, _invoice(tmp_path / "a.pdf", "HI-2001", lines))
    _approve(store, tmp_path / "a.pdf", capture, proposed, {1: {"description": WRAP}})
    [fix] = [c for c in store.corrections() if c["field"] == "line_description"]
    assert (fix["proposed"], fix["value"], fix["next"]) == (WRAP_READ, WRAP, "")
    capture, proposed = _code(store, reference, _invoice(tmp_path / "b.pdf", "HI-2002", lines))
    _approve(store, tmp_path / "b.pdf", capture, proposed, {})  # kept as AP Coder named it: no correction
    [fix] = [c for c in store.corrections() if c["field"] == "line_description"]
    assert fix["next"] == "right" and fix["invoice_number"] == "HI-2001"

    from tests.webclient import client_for

    page = client_for(store).get("/learning").text
    assert "Description of a line, as read" in page and WRAP in page


def test_the_review_screen_says_under_the_line_what_was_printed(tmp_path, store, reference):
    from ap_coder.core.store.samples import load_sample_setup
    from tests.webclient import client_for

    load_sample_setup(store)
    lines = [(WRAP_READ, "1", "21.06", "21.06")]
    capture, proposed = _code(store, reference, _invoice(tmp_path / "a.pdf", "HI-2001", lines))
    _approve(store, tmp_path / "a.pdf", capture, proposed, {1: {"description": WRAP}})
    capture, proposed = _code(store, reference, _invoice(tmp_path / "b.pdf", "HI-2002", lines))
    invoice_id = store.add_invoice(tmp_path / "b.pdf", proposed, None)
    page = client_for(store).get(f"/review/{invoice_id}").text
    assert "Named as approved before · printed “Palletwrap18in.x1500ft”" in page
    assert f">{WRAP}</textarea>" in page


ITEMS = ["Safety glasses, clear, ANSI Z87", "Janitorial service - Unit 4", "Pallet wrap 18 in. x 1500 ft",
         "Disinfectant 4 L", "Toner cartridge HP 58X", "Bearing 6205-2RS", "Monthly maintenance contract"]  # fmt: skip


def test_what_the_columns_beside_ran_in_and_a_letter_or_two_ocr_dropped_are_forgiven():
    """What a scan of these items read, the next times (bench lines): a unit or the quantity from the columns beside
    run in, a stray mark, a letter or two dropped."""
    from ap_coder.capture.line_memory import recall

    known = {r["read_key"]: r["description"] for r in observations([(t, t, t) for t in ITEMS])}
    for read, numbers, item in [
        ("2 visit Janitorial service-Unit4", (2.0,), "Janitorial service - Unit 4"),
        ("Pallet wrap18in.x1500ft 100 roll", (100.0, 21.06), "Pallet wrap 18 in. x 1500 ft"),
        ("Disinfectant4L ea", (), "Disinfectant 4 L"),
        ("I Monthlymaintenance contract", (), "Monthly maintenance contract"),
        ("Saetyglasses,clear,ANSI87", (), "Safety glasses, clear, ANSI Z87"),
        ("I Toner cartridge HP58x", (), "Toner cartridge HP 58X"),
    ]:
        assert recall(read, known, False, numbers) == item, read


def test_never_another_item_a_digit_another_model_or_more_words():
    from ap_coder.capture.line_memory import recall

    known = {r["read_key"]: r["description"] for r in observations([(t, t, t) for t in ITEMS])}
    for read in (
        "Bearing 6206-2RS",
        "Toner cartridge HP 58A",
        "Toner cartridge HP 85X",
        "Bearing 6205-2RS seal kit",
        "Safety glasses, tinted, ANSI Z87",
        "Safety glasses,clear,ANSI7",
        "Disinfectant 4 L 12",
    ):
        assert recall(read, known, False) is None, read
