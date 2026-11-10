"""The remark of a G/L line (D-213): suggested from the supplier's usual one, fitted to JD Edwards' 30 characters,
never replaced once a person typed it, learned on approval. The suppliers are made up (``tests/books.py``)."""

import datetime as dt
import unicodedata

import pandas as pd
import pytest

from ap_coder.ai.offline_coder import code_from_capture
from ap_coder.ai.pipeline import finalise_coding
from ap_coder.ai.supplier_prior import prior_for, remark_shape
from ap_coder.coding import remark as rk
from ap_coder.coding.remark import REMARK_MOST, Period, fit, period_of, shortened, suggest_remark, with_period
from ap_coder.coding.review import coding_from_inputs
from ap_coder.coding.validation import validate_coding
from ap_coder.config import Settings
from ap_coder.core.schema import InvoiceCoding
from ap_coder.erp import jde
from tests.books import FACILITIES, MAIN, approve, captured, load_chart, load_journal, monthly

JANITOR = f"{MAIN}.7340.3307"
CARPET = f"{MAIN}.7340.3313"
COURIER = f"{MAIN}.7420.4207"
CELL = f"{MAIN}.7510.5107"
BRIGHTWAY = "BRIGHTWAY JANITORIAL LTD."
MAY = {"invoice_date": "2026-05-20", "invoice_number": "BW-2291"}


def line(description="Cleaning", **more):
    return {"line_number": 1, "description": description, **more}


# --- 30 characters, never cut without saying ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "text, fitted, cut",
    [
        ("coffee supplies 3-1-204", "coffee supplies 3-1-204", False),
        ("x" * 30, "x" * 30, False),  # exactly 30: as it is
        ("janitorial services 410 Elm St", "janitorial services 410 Elm St", False),  # 30 with its spaces
        ("janitorial services 410 Elm St.", "janitorial services 410 Elm", True),  # 31: the whole words that fit
        ("abcdefghij abcdefghij abcdefgh k", "abcdefghij abcdefghij abcdefgh", True),  # the 31st is a space
        ("Supercalifragilisticexpialidocious", "Supercalifragilisticexpialidoc", True),  # one word: no word end
        ("  two\n lines  ", "two lines", False),
        ("Entretien ménager, 3e étage, mai", "Entretien ménager, 3e étage", True),
        ("é" * 30, "é" * 30, False),  # an accented letter is one character
        (unicodedata.normalize("NFD", "é" * 30), "é" * 30, False),  # however it was typed
        (None, "", False),
    ],
)
def test_a_remark_is_fitted_to_30_characters_at_a_word_end_and_says_when_it_was_shortened(text, fitted, cut):
    assert fit(text) == (fitted, cut) and len(fit(text)[0]) <= REMARK_MOST


@pytest.mark.parametrize(
    "description, remark",
    [
        ("Courier charges", "Courier charges"),  # it fits: as written, its small words too
        ("Coffee for the kitchen", "Coffee for the kitchen"),
        ("Monthly janitorial service for the head office building - May 2026", "Monthly janitorial service May"),
        ("Entretien ménager du bureau de Montréal pour février 2026", "Entretien ménager février"),
        ("Delivery of the parcels to your office, waybill 7781234567", "Delivery parcels office"),
        ("Overnight envelope to the north office", "Overnight envelope north"),
        ("", ""),
    ],
)
def test_an_invoice_line_is_shortened_without_its_filler_words_and_keeps_its_month(description, remark):
    assert shortened(description) == remark and len(remark) <= REMARK_MOST


# --- The period put back -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "written, now",
    [
        ("Jul janitorial 410 Elm", "May janitorial 410 Elm"),
        ("KT Jul.cellphone", "KT May.cellphone"),
        ("JULY RENT", "MAY RENT"),
        ("August parking 3-1-204", "May parking 3-1-204"),
        ("juillet loyer", "mai loyer"),  # French stays French
        ("Loyer décembre", "Loyer mai"),
        ("Sept/25 lease 2-3-104", "May/26 lease 2-3-104"),
        ("Aug 2025 cleaning", "May 2026 cleaning"),
        ("Jul 25 rent", "May 26 rent"),  # the year it was written in: a year
        ("Jul 15 delivery", "May 15 delivery"),  # no year: left
        ("Q3 retainer", "Q2 retainer"),
        ("Q3 2025 audit fees", "Q2 2026 audit fees"),
        ("INV 778812 freight", "INV BW-2291 freight"),
        ("INVOICE#778812", "INVOICE#BW-2291"),
        ("invoice 410 Elm", "invoice 410 Elm"),  # an office, not an invoice number
        ("acct 0045123 hydro", "acct 0045123 hydro"),  # the supplier's account number: the same every time
        ("0045123 freight", "freight"),  # a long number nothing explains was that voucher's
        ("courier 2040 Alder", "courier 2040 Alder"),  # a street number is no year
        ("courier charges", "courier charges"),
    ],
)
def test_a_remark_gets_this_invoices_month_quarter_year_and_number_and_keeps_its_office(written, now):
    assert with_period(written, Period(2026, 5, 2), "BW-2291", written=2025) == now


def test_with_no_date_the_month_is_taken_out_not_kept_stale():
    assert with_period("Jul janitorial 410 Elm", None, "", written=2025) == "janitorial 410 Elm"
    assert with_period("INV 778812 freight", None, "", written=2025) == "INV freight"


def test_every_month_put_back_is_one_the_shape_takes_out():
    for word in rk._MONTH_OF:
        assert remark_shape(f"{word} rent") == "rent", word
        assert with_period(f"{word} rent", Period(2026, 6, 2)).lower() in ("jun rent", "june rent", "juin rent")


@pytest.mark.parametrize(
    "description, others, date, period",
    [
        ("Janitorial service", [], "2026-05-20", (2026, 5, 2)),  # the invoice date
        ("Janitorial service April 2026", [], "2026-05-03", (2026, 4, 2)),  # the line names its month
        ("Service period Apr 1 - Apr 30, 2026", [], "2026-05-03", (2026, 4, 2)),
        ("Entretien de février", [], "2026-03-02", (2026, 2, 1)),
        ("December cleaning", [], "2026-01-05", (2025, 12, 4)),  # last year's
        ("Cell plan", ["Service for mai 2026"], "2026-06-02", (2026, 5, 2)),  # another line names the only one
        ("Cell plan", ["March 2026 plan", "April 2026 plan"], "2026-06-02", (2026, 6, 2)),  # two months: the date
        ("Q1 2026 retainer", [], "2026-04-10", (2026, 3, 1)),
        ("Late fees may apply", [], "2026-01-05", (2026, 1, 1)),  # "may" alone is no month
        ("Mar 12 delivery", [], "2026-05-20", (2026, 5, 2)),  # a short month with no year neither
    ],
)
def test_the_period_is_the_one_a_line_names_else_the_invoice_dates(description, others, date, period):
    invoice = {"invoice_date": date, "line_items": [line(description), *(line(text) for text in others)]}
    assert period_of(invoice["line_items"][0], invoice) == period
    assert period_of(line("Janitorial service"), {"invoice_date": None}) is None


# --- The rules ---------------------------------------------------------------------------------------------------


@pytest.fixture
def books(store):
    """A janitor paid monthly to one account ("Sep janitorial 410 Elm" the latest), a carpet cleaning once."""
    reference = load_chart(store)
    vouchers = monthly("00740075", BRIGHTWAY, JANITOR, "{month} janitorial 410 Elm", 9)
    vouchers += [(740075, BRIGHTWAY, dt.date(2025, 10, 3), [(CARPET, 300, "carpet cleaning 3-1-204")])]
    load_journal(store, vouchers)
    return store, reference, prior_for(store, "Brightway Janitorial Ltd.")


def test_a_the_suppliers_usual_remark_for_the_account_with_this_invoices_month(books):
    _, reference, prior = books
    assert suggest_remark(line("Cleaning"), MAY, prior, JANITOR, reference) == (
        "May janitorial 410 Elm",
        "this supplier's usual remark for this account, 9 of 9 times",
    )
    # the month the line names, not the invoice date's
    assert (
        suggest_remark(line("Cleaning for April 2026"), MAY, prior, JANITOR, reference)[0] == "Apr janitorial 410 Elm"
    )
    assert suggest_remark(line("Carpets"), MAY, prior, CARPET, reference) == (
        "carpet cleaning 3-1-204",
        "this supplier's usual remark for this account, the one time",
    )
    assert rk.suggested(line(), MAY, prior, JANITOR, reference).source == "supplier"


def test_a_the_commonest_shape_wins_in_its_latest_wording_and_a_line_with_no_unit_takes_the_codes(store):
    reference = load_chart(store)
    vouchers = monthly(5100, "NORTHLINE MOBILITY", CELL, "KT {month}.cellphone", 6)
    vouchers += [(5100, "NORTHLINE MOBILITY", dt.date(2025, 7, 2), [(CELL, 80, "INV 778812 handset")]),
                 (5100, "NORTHLINE MOBILITY", dt.date(2025, 8, 2), [(CELL, 80, "Q3 roaming")])]  # fmt: skip
    load_journal(store, vouchers)
    prior = prior_for(store, "Northline Mobility")
    assert suggest_remark(line("Plan"), MAY, prior, CELL, reference) == (
        "KT May.cellphone",
        "this supplier's usual remark for this account, 6 of 8 times",
    )
    assert suggest_remark(line("Plan"), MAY, prior, "7510.5107", reference)[0] == "KT May.cellphone"


def test_b_an_account_with_no_remark_yet_takes_the_suppliers_one_shape_only_when_it_is_on_most_lines(books, store):
    _, reference, prior = books
    assert suggest_remark(line("Recycling pick-up"), MAY, prior, f"{FACILITIES}.7340.3307", reference) == (
        "May janitorial 410 Elm",
        "this supplier's usual remark, 9 of its 10 lines (none for this account yet)",
    )
    # A supplier with no usual shape: the invoice's own words.
    load_journal(store, [
        (6200, "CEDAR OFFICE SUPPLY", dt.date(2025, m, 5), [(f"{MAIN}.7410.4107", 50, remark)])
        for m, remark in enumerate(["copy paper", "toner 3-1-204", "pens", "desk lamp"], 1)
    ], name="second.csv")  # fmt: skip
    mixed = prior_for(store, "Cedar Office Supply")
    assert suggest_remark(line("Recycling pick-up"), MAY, mixed, COURIER, reference) == (
        "Recycling pick-up",
        "from the invoice's line",
    )
    # Two lines are too few to call a shape the usual one.
    load_journal(store, monthly(6300, "TWO LINE COURIER", COURIER, "courier charges", 2), name="third.csv")
    few = prior_for(store, "Two Line Courier")
    assert suggest_remark(line("Parcel"), MAY, few, CARPET, reference)[1] == "from the invoice's line"


def test_c_with_no_history_the_invoices_line_else_the_accounts_name(books):
    store, reference, _ = books
    nobody = prior_for(store, "Somebody New Inc.")
    assert not nobody
    for prior in (nobody, None):
        assert suggest_remark(line("Courier charges"), MAY, prior, COURIER, reference) == (
            "Courier charges",
            "from the invoice's line",
        )
    long = line("Monthly janitorial service for the head office building - May 2026")
    assert suggest_remark(long, MAY, None, JANITOR, reference) == (
        "Monthly janitorial service May",
        "from the invoice's line, shortened to 30 characters",
    )
    assert suggest_remark(line(""), MAY, None, JANITOR, reference) == ("Janitorial Contract", "the account's name")
    assert suggest_remark(line(""), MAY, None, "", reference) == ("", "")
    # A line with no account: the invoice's wording, whatever the supplier's history.
    assert suggest_remark(line("Extra clean-up"), MAY, books[2], "", reference)[1] == "from the invoice's line"


# --- Filling a coding's lines ------------------------------------------------------------------------------------


def _coding(store, reference, *descriptions, vendor="Brightway Janitorial Ltd."):
    return code_from_capture(captured(vendor, *descriptions), reference, [], store, text="").coding


def test_each_line_gets_its_accounts_remark_and_a_persons_is_never_replaced(books):
    store, reference, _ = books
    coding = _coding(store, reference, "Janitorial service", "Something else entirely")
    first, second = coding.line_items
    first.predicted_cost_center, first.predicted_gl_code = MAIN, "7340.3307"
    second.predicted_cost_center, second.predicted_gl_code = "UNASSIGNED", "UNASSIGNED"
    rk.fill_remarks(coding, store, reference)
    assert (first.remark, first.remark_from) == ("Jan janitorial 410 Elm", "supplier")  # (read on 2026-01-20)
    assert (second.remark, second.remark_from) == ("Something else entirely", "invoice")  # no account: its wording

    # The account changes: the suggestion follows it...
    first.predicted_gl_code = "7340.3313"
    rk.fill_remarks(coding, store, reference)
    assert (first.remark, first.remark_from) == ("carpet cleaning 3-1-204", "supplier")
    # ...unless a person typed the remark,
    first.remark, first.remark_from = "PM windows 3-1-204", "person"
    first.predicted_gl_code = "7340.3307"
    rk.fill_remarks(coding, store, reference)
    assert (first.remark, first.remark_from) == ("PM windows 3-1-204", "person")
    # a remark typed exactly as suggested is the suggestion, and a person's remark cleared asks for it again.
    first.remark = "Jan janitorial 410 Elm"
    rk.fill_remarks(coding, store, reference)
    assert first.remark_from == "supplier"
    first.remark, first.remark_from = " ", "person"
    rk.fill_remarks(coding, store, reference)
    assert (first.remark, first.remark_from) == ("Jan janitorial 410 Elm", "supplier")


def test_an_install_with_no_chart_stores_its_invoices_as_before_unless_remarks_go_to_jd_edwards(
        store, reference, ground_truth):  # fmt: skip
    coding = InvoiceCoding.model_validate(ground_truth)
    before = coding.to_output()
    output, _ = finalise_coding(coding, reference, Settings(), store=store)
    assert output["line_items"] == before["line_items"] and not any("remark" in li for li in output["line_items"])
    # JD Edwards shown, its remarks still AP Coder's references: nothing either.
    store.set_setting(jde.SHOW_SETTING, "1")
    jde.save(store, jde.JdeSettings())
    output, _ = finalise_coding(InvoiceCoding.model_validate(ground_truth), reference, Settings(), store=store)
    assert output["line_items"] == before["line_items"]
    # The remarks go as typed: each line gets one, from the invoice's line (no history).
    jde.save(store, jde.JdeSettings(remarks=jde.REMARKS_TYPED))
    output, _ = finalise_coding(InvoiceCoding.model_validate(ground_truth), reference, Settings(), store=store)
    for li, was in zip(output["line_items"], before["line_items"], strict=True):
        assert li["remark"] == shortened(was["description"]) and li["remark_from"] == "invoice"
        assert {k: v for k, v in li.items() if k not in ("remark", "remark_from")} == was


def test_with_a_chart_an_invoice_read_gets_its_remarks(books):
    store, reference, _ = books
    coding = _coding(store, reference, "Janitorial service")
    output, _ = finalise_coding(coding, reference, Settings(), store=store)
    (li,) = output["line_items"]
    assert (li["predicted_gl_code"], li["remark"], li["remark_from"]) == (
        "7340.3307",
        "Jan janitorial 410 Elm",
        "supplier",
    )


# --- Too long for JD Edwards -------------------------------------------------------------------------------------


def test_a_remark_over_30_characters_is_a_finding_that_holds_only_where_remarks_are_typed(reference, ground_truth):
    ground_truth["line_items"][0].update(remark="coffee supplies for the 3rd floor kitchen", remark_from="person")
    ground_truth["line_items"][1].update(remark="x" * 30, remark_from="person")  # exactly 30: fine
    coding = InvoiceCoding.model_validate(ground_truth)
    found = [i for i in validate_coding(coding, reference).issues if i.code == "REMARK_LONG"]
    assert [(i.severity, i.line_number) for i in found] == [("warning", 1)]
    assert found[0].message == ("The remark has 41 characters and JD Edwards keeps 30: shorten it, or it is keyed as "
                                "“coffee supplies for the 3rd”")  # fmt: skip
    typed = [i for i in validate_coding(coding, reference, remarks_typed=True).issues if i.code == "REMARK_LONG"]
    assert [(i.severity, i.line_number) for i in typed] == [("error", 1)]
    assert coding.to_output()["line_items"][0]["remark"] == "coffee supplies for the 3rd floor kitchen"  # not cut


# --- The review screen's grid ------------------------------------------------------------------------------------

HEADER = ("vendor_name", "invoice_number", "invoice_date", "po_number", "payment_terms", "due_date", "currency",
          "supplier_province", "ship_to_province", "gst_hst_registration_number", "qst_registration_number",
          "original_invoice_number", "remit_bank_account", "subtotal", "tax_total", "grand_total")  # fmt: skip


def _rebuilt(ground_truth, proposed, **columns):
    lines = pd.DataFrame(ground_truth["line_items"])
    for name, values in columns.items():
        lines[name] = values
    coding, problems = coding_from_inputs({k: ground_truth[k] for k in HEADER}, lines,
                                          pd.DataFrame(ground_truth["tax_lines"]), proposed)  # fmt: skip
    assert not problems
    return [(li.remark, li.remark_from) for li in coding.line_items]


def test_the_grids_remark_is_a_persons_once_it_is_not_the_one_proposed(ground_truth):
    proposed = {**ground_truth, "line_items": [dict(li) for li in ground_truth["line_items"]]}
    proposed["line_items"][0].update(remark="May janitorial 410 Elm", remark_from="supplier")
    n = len(ground_truth["line_items"])
    rest = [("", "")] * (n - 1)
    # A grid with no remark column: each line keeps the remark it was proposed with.
    assert _rebuilt(ground_truth, proposed) == [("May janitorial 410 Elm", "supplier"), *rest]
    # Left as proposed, typed, cleared.
    assert _rebuilt(ground_truth, proposed, remark=["May janitorial 410 Elm"] + [""] * (n - 1))[0] == (
        "May janitorial 410 Elm",
        "supplier",
    )
    assert _rebuilt(ground_truth, proposed, remark=["  KT May.windows   3-1-204 "] + [None] * (n - 1)) == [
        ("KT May.windows 3-1-204", "person"),
        *rest,
    ]
    assert _rebuilt(ground_truth, proposed, remark=[""] * n) == [("", ""), *rest]
    # With where it is from beside it (the screen's draft), that is what it says.
    assert _rebuilt(ground_truth, proposed, remark=["May janitorial 410 Elm"] + [""] * (n - 1),
                    remark_from=["person"] + [""] * (n - 1))[0] == ("May janitorial 410 Elm", "person")  # fmt: skip
    assert rk.typed("x" * 40, {"remark": "old"}) == ("x" * 40, "person")  # kept whole: the check says it is long


# --- Learned on approval -----------------------------------------------------------------------------------------


def test_an_approved_remark_is_learned_and_a_typed_one_outweighs_last_years(books, tmp_path):
    """The journal has "courier charges" four times (each counts 1). AP types its own remark once (5): it is the
    supplier's remark for the account from then on, with the next invoice's month; approved as suggested (3) it
    counts again. A remark approved for another account does not move this one."""
    store, reference, _ = books
    vendor = "Rapidway Courier Inc."
    load_journal(store, monthly(8800, "RAPIDWAY COURIER INC.", COURIER, "courier charges", 4), name="courier.csv")

    def read(number, description="Overnight parcel"):
        coding = code_from_capture(captured(vendor, description, number=number), reference, [], store, text="").coding
        rk.fill_remarks(coding, store, reference)
        return coding

    first = read("RC-1")
    (li,) = first.line_items
    assert (li.predicted_gl_code, li.remark, li.remark_from) == ("7420.4207", "courier charges", "supplier")
    li.remark, li.remark_from = "KT Jan.parcels 3-1-204", "person"
    approve(store, tmp_path, first, [COURIER])
    assert [(r["final_remark"], r["remark_from"], r["lines"]) for r in store.approved_coding(
        "rapidway courier")["remarks"]] == [("KT Jan.parcels 3-1-204", "person", 1)]  # fmt: skip

    prior = prior_for(store, vendor)
    usual = prior.use(COURIER).remarks[0]
    assert (usual.shape, usual.lines, usual.example, usual.typed) == (
        "kt parcels 3-1-204", 1, "KT Jan.parcels 3-1-204", True)  # fmt: skip
    assert prior.use(COURIER).remarked == 5
    assert suggest_remark(line("Parcel"), MAY, prior, COURIER, reference) == (
        "KT May.parcels 3-1-204", "the remark AP typed for this supplier's account here, 1 of 5 times")  # fmt: skip

    second = read("RC-2")
    assert (second.line_items[0].remark, second.line_items[0].remark_from) == ("KT Jan.parcels 3-1-204", "supplier")
    approve(store, tmp_path, second, [COURIER])
    again = prior_for(store, vendor).use(COURIER)
    assert [(r.shape, r.lines) for r in again.remarks] == [("kt parcels 3-1-204", 2), ("courier charges", 4)]
    assert again.remarked == 6
    # the accounts are ranked by the journal's remarks as before: an approved remark is no word of the account's
    assert again.remark_keys == prior_for(store, vendor).use(COURIER).remark_keys == prior.use(COURIER).remark_keys


def _split(store, reference, vendor, first_account, second_account):
    """A one-line invoice of ``vendor`` split 60/40 over two accounts, as ``review.split_line`` leaves its parts."""
    coding = _coding(store, reference, "Parcel pick-up", "Parcel pick-up", vendor=vendor)
    for li, account, share in zip(coding.line_items, (first_account, second_account), ("60%", "40%"), strict=True):
        li.predicted_cost_center, li.predicted_gl_code = account.split(".", 1)
        li.description, li.reasoning_justification = f"Parcel pick-up ({share})", f"Split from line 1 ({share})."
    return coding


def test_the_parts_of_a_split_line_share_the_lines_remark_without_the_splits_mark(books):
    """B-2263: the second part of a split, on an account the supplier has no remark for, took the description with
    the split's mark ("Parcel pick-up (40%)") as its remark. It takes the remark the first part has, or the one
    suggested for its own account when there is one; a remark typed on it stays."""
    store, reference, _ = books
    load_journal(store, monthly(6300, "TWO LINE COURIER", COURIER, "courier charges", 2), name="third.csv")
    elsewhere = f"{FACILITIES}.7420.4207"
    coding = _split(store, reference, "Two Line Courier", COURIER, elsewhere)
    first, second = coding.line_items
    rk.fill_remarks(coding, store, reference)
    assert (first.remark, first.remark_from) == ("courier charges", "supplier")
    assert (second.remark, second.remark_from) == ("courier charges", "supplier")
    # Neither account has a remark: the line's own words, without "(60%)" and "(40%)".
    coding = _split(store, reference, "Nobody Known Ltd.", elsewhere, COURIER)
    rk.fill_remarks(coding, store, reference)
    assert [(li.remark, li.remark_from) for li in coding.line_items] == [("Parcel pick-up", "invoice")] * 2
    # The part's own account has a remark: that one. A remark a person typed on the part stays.
    coding = _split(store, reference, "Brightway Janitorial Ltd.", JANITOR, CARPET)
    rk.fill_remarks(coding, store, reference)
    assert [li.remark for li in coding.line_items] == ["Jan janitorial 410 Elm", "carpet cleaning 3-1-204"]
    coding = _split(store, reference, "Two Line Courier", COURIER, elsewhere)
    first, second = coding.line_items
    second.remark, second.remark_from = "parcels annex", "person"
    rk.fill_remarks(coding, store, reference)
    assert (second.remark, second.remark_from) == ("parcels annex", "person")
    # A remark typed on the first part is the line's: the second part, not typed, follows it.
    second.remark, second.remark_from = "", ""
    first.remark, first.remark_from = "KT parcels 3-1-204", "person"
    rk.fill_remarks(coding, store, reference)
    assert (second.remark, second.remark_from) == ("KT parcels 3-1-204", "invoice")


@pytest.mark.parametrize(
    ("typed_on", "typed", "next_on", "carried"),
    [
        ("2026-11-12", "courier nov 2026 waybill", "2026-12-10", "courier dec 2026 waybill"),
        ("2026-11-12", "courier nov 2026 waybill", "2026-11-26", "courier nov 2026 waybill"),  # the same month
        ("2026-12-10", "courier Dec 2026 waybill", "2027-01-14", "courier Jan 2027 waybill"),  # across a year end
        ("2026-12-10", "courier dec/26 waybill", "2027-01-14", "courier jan/27 waybill"),
        ("2026-12-10", "courier December waybill", "2027-01-14", "courier January waybill"),
    ],
)
def test_a_remark_typed_with_a_month_names_the_next_invoices_month(books, tmp_path, typed_on, typed, next_on, carried):
    """A remark a person typed once is the supplier's from then on: the month (and year) it names is the one of the
    invoice it is carried to, never the one it was typed in."""
    store, reference, _ = books
    vendor = "Rapidway Courier Inc."
    load_journal(store, monthly(8800, "RAPIDWAY COURIER INC.", COURIER, "courier charges", 4), name="courier.csv")

    def read(number, on):
        coding = code_from_capture(captured(vendor, "Overnight parcel", number=number), reference, [], store,
                                   text="").coding  # fmt: skip
        coding.invoice_date = dt.date.fromisoformat(on)
        rk.fill_remarks(coding, store, reference)
        return coding

    first = read("RC-1", typed_on)
    first.line_items[0].remark, first.line_items[0].remark_from = typed, "person"
    approve(store, tmp_path, first, [COURIER])
    (li,) = read("RC-2", next_on).line_items
    assert (li.remark, li.remark_from) == (carried, "supplier")


def test_a_database_made_before_gets_the_remark_columns(store):
    with store._conn() as conn:
        columns = [row[1] for row in conn.execute("PRAGMA table_info(feedback)")]
    assert columns[-2:] == ["final_remark", "remark_from"]
