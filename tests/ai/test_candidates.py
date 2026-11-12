"""Whole account strings for a line (D-212): ``suggest.candidates`` from every source, each a pair that may be posted
to; a line coded alone only on strong evidence, else left with a specific reason and the accounts offered."""

import datetime as dt
import json

from ap_coder.ai.offline_coder import NO_HISTORY, code_from_capture
from ap_coder.ai.suggest import candidates, complete_pair, suggest_gl
from ap_coder.ai.supplier_prior import prior_for
from ap_coder.core.chart import BuRow
from ap_coder.core.reference_data import UNASSIGNED
from ap_coder.core.schema import InvoiceCoding
from tests import books
from tests.books import CLOSED, FACILITIES, IT, MAIN, RETIRED, approve, captured, load_chart, load_journal, monthly

JANITOR = f"{MAIN}.7340.3307"
CARPET = f"{MAIN}.7340.3313"
COURIER = f"{MAIN}.7420.4207"
NAME = "Brightway Janitorial Ltd."


def _books(store, vouchers, name="BRIGHTWAY JANITORIAL LTD.", number=740075):
    reference = load_chart(store)
    store.import_vendor_master([{"vendor_name": name, "erp_id": str(number), "status": "active"}], by_erp_id=True)
    load_journal(store, vouchers)
    return reference


def _line(store, reference, text, vendor=NAME, number="INV-1"):
    return code_from_capture(captured(vendor, text, number=number), reference, store.feedback_rows(), store)


def test_one_account_with_nine_of_ten_lines_codes_the_line_and_says_what_decided_it(store):
    """B-2136: 30 lines to one account under one remark got no suggestion at all (the usual account counted
    different descriptions): the supplier's coding now codes it, with the business unit (B-2137)."""
    vouchers = monthly(740075, "BRIGHTWAY JANITORIAL LTD.", JANITOR, "janitorial", 12)
    vouchers += monthly(740075, "BRIGHTWAY JANITORIAL LTD.", JANITOR, "janitorial", 12, year=2024)
    reference = _books(store, vouchers)
    line = _line(store, reference, "Fuel surcharge").coding.line_items[0]
    assert (line.predicted_cost_center, line.predicted_gl_code) == (MAIN, "7340.3307")
    assert line.reasoning_justification == "24 of this supplier's 24 lines last year"
    assert line.decided_by == {"source": "supplier", "lines": 24, "of": 24, "share": 1.0} and line.candidates == []
    stored = json.loads(json.dumps(line.model_dump(mode="json")))
    assert stored["decided_by"]["source"] == "supplier" and "candidates" not in stored  # (nothing offered: not kept)


def test_a_line_is_left_with_the_specific_reason_and_the_accounts_offered(store):
    vouchers = [*monthly(740075, "BRIGHTWAY JANITORIAL LTD.", JANITOR, "{month} janitorial", 12),
                *monthly(740075, "BRIGHTWAY JANITORIAL LTD.", CARPET, "carpet cleaning", 5),
                *monthly(740075, "BRIGHTWAY JANITORIAL LTD.", f"{FACILITIES}.7340.3307", "janitorial annex", 3),
                *monthly(8001, "TWO LINE SUPPLY CO", COURIER, "courier", 2)]  # fmt: skip
    reference = _books(store, vouchers)
    result = _line(store, reference, "Day porter service")
    line = result.coding.line_items[0]
    assert line.predicted_gl_code == UNASSIGNED and line.decided_by == {}
    assert line.reasoning_justification == ("This supplier's 20 lines last year went to 3 accounts: 60% / 25% / 15%: "
                                            "pick the account.")  # fmt: skip
    assert [c["account"] for c in line.candidates[:3]] == [JANITOR, CARPET, f"{FACILITIES}.7340.3307"]
    assert line.candidates[0] == {
        "account": JANITOR, "business_unit": MAIN, "code": "7340.3307", "name": "Janitorial Contract",
        "reason": "12 of this supplier's 20 lines last year", "source": "supplier", "lines": 12, "of": 20,
        "share": 0.6}  # fmt: skip
    assert len(line.candidates) <= 5 and result.offered[1][0].account == JANITOR  # (all of them, for the chat model)
    new = _line(store, reference, "Day porter service", vendor="Never Seen Cleaning Inc.").coding.line_items[0]
    assert new.predicted_gl_code == UNASSIGNED
    assert new.reasoning_justification == "A new supplier: not in last year's journal: pick the account."
    few = _line(store, reference, "Parcel", vendor="Two Line Supply Co").coding.line_items[0]
    assert few.predicted_gl_code == UNASSIGNED and few.reasoning_justification == (
        f"Only 2 of this supplier's 2 lines last year went to {COURIER}: too few to code it alone: pick the account."
    )


def test_only_pairs_that_may_be_posted_to_are_offered(store):
    """Review finding F3: suggestions offered a reserved subsidiary, a business unit marked DO NOT USE, a closed one
    and a title account. None is a candidate, whatever the supplier's history says."""
    lines = [(f"{MAIN}.7340.9710", 10, "recovery"), (f"{RETIRED}.7340.3307", 10, "janitorial old"),
             (f"{CLOSED}.7340.3307", 10, "janitorial y2k"), (f"{MAIN}.7340", 10, "cleaning"),
             (f"{MAIN}.2110", 10, "payable by hand"), (CARPET, 10, "carpets")]  # fmt: skip
    reference = _books(store, [(740075, "BRIGHTWAY JANITORIAL LTD.", dt.date(2025, m, 5), lines) for m in (1, 2, 3)])
    prior = prior_for(store, NAME)
    assert len(prior.accounts) == 6
    found = candidates("Cleaning services recovery janitorial", NAME, store.feedback_rows(), reference, prior=prior)
    assert all(reference.pairs.offered(c.business_unit, c.code) for c in found) and found
    offered = {c.account for c in found}
    assert not offered & {f"{MAIN}.7340.9710", f"{RETIRED}.7340.3307", f"{CLOSED}.7340.3307", f"{MAIN}.7340",
                          f"{MAIN}.2110"}  # fmt: skip
    # The closed and the retired business units' account is offered on a unit that has it, and says why.
    moved = next(c for c in found if c.code == "7340.3307")
    assert moved.business_unit in (MAIN, FACILITIES)
    assert f"business unit {RETIRED} " in moved.reason or f"business unit {CLOSED} is no longer active" in moved.reason
    again = suggest_gl("Cleaning", NAME, [], reference, limit=20, prior=prior)  # the same list, through suggest_gl
    assert again and all(reference.pairs.offered(s.cost_center, s.gl_code) for s in again)


def test_the_usual_account_on_a_closed_business_unit_is_said_and_not_coded(store):
    reference = _books(store, monthly(740075, "BRIGHTWAY JANITORIAL LTD.", f"{CLOSED}.7340.3307", "janitorial", 6))
    line = _line(store, reference, "Nightly cleaning").coding.line_items[0]
    assert line.predicted_gl_code == UNASSIGNED
    assert line.reasoning_justification == (
        f"The supplier's usual account {CLOSED}.7340.3307 is no longer in the chart (business unit {CLOSED} is no "
        "longer active): pick the account.")  # fmt: skip
    assert line.candidates[0]["code"] == "7340.3307" and line.candidates[0]["business_unit"] in (MAIN, FACILITIES)


def test_the_business_unit_of_an_account_known_without_one(store):
    reference = load_chart(store)
    assert complete_pair(reference, "7420.4207", FACILITIES) == (FACILITIES, False)  # the past line's own
    assert complete_pair(reference, "7340.3307", CLOSED, [FACILITIES]) == (FACILITIES, False)  # closed: the supplier's
    assert complete_pair(reference, "7420.4207", "", [IT, FACILITIES]) == (FACILITIES, False)  # its only unit with it
    assert complete_pair(reference, "7550.5507") == (IT, False)  # the only unit of the chart that has it
    assert complete_pair(reference, "7510.5107", "", [IT, FACILITIES]) == (IT, True)  # several: the most used, a guess
    assert complete_pair(reference, "7420.4207") == (MAIN, True)  # nothing says: the home business unit, a guess
    assert complete_pair(reference, "7340.9710") is None and complete_pair(reference, "7340") is None


def test_the_words_of_an_accounts_name_never_pick_the_business_unit_by_code_order(store):
    """B-2139: a new supplier's line "Courier" was offered the first business unit in code order that has a courier
    account. It is offered on the home business unit, marked a guess, and never coded alone."""
    reference = load_chart(store, home=FACILITIES)
    load_journal(store, monthly(9, "SOMEONE ELSE", JANITOR, "x", 1))
    result = _line(store, reference, "Courier domestic", vendor="Never Seen Express Inc.")
    top = result.coding.line_items[0].candidates[0]
    assert (top["account"], top["source"], top.get("guess")) == (f"{FACILITIES}.7420.4207", "words", True)
    assert result.coding.line_items[0].predicted_gl_code == UNASSIGNED


def test_a_new_suppliers_line_is_not_coded_from_one_other_vendors_identical_line(store, tmp_path):
    """B-2138: one other vendor's approved "Courier charges" coded a never-seen supplier's line with no one picking."""
    reference = _books(store, monthly(740075, "BRIGHTWAY JANITORIAL LTD.", JANITOR, "janitorial", 6))
    first = _line(store, reference, "Courier charges overnight", vendor="Rapidway Courier Ltd.").coding
    approve(store, tmp_path, first, [COURIER])
    line = _line(store, reference, "Courier charges overnight", vendor="Never Seen Express Inc.", number="INV-2")
    line = line.coding.line_items[0]
    assert line.predicted_gl_code == UNASSIGNED and line.candidates[0]["account"] == COURIER
    assert line.candidates[0]["source"] == "others"


def test_the_same_words_approved_on_two_business_units_are_left_for_a_person(store, tmp_path):
    reference = _books(store, monthly(740075, "BRIGHTWAY JANITORIAL LTD.", JANITOR, "janitorial", 2))
    for n, account in enumerate((JANITOR, f"{FACILITIES}.7340.3307")):
        coding = _line(store, reference, "Nightly office cleaning", number=f"INV-{n}").coding
        approve(store, tmp_path, coding, [account])
    line = _line(store, reference, "Nightly office cleaning", number="INV-9").coding.line_items[0]
    assert line.predicted_gl_code == UNASSIGNED and "went to 2 business units" in line.reasoning_justification
    assert {c["account"] for c in line.candidates[:2]} == {JANITOR, f"{FACILITIES}.7340.3307"}


def test_one_remark_on_several_accounts_of_one_voucher_is_a_split_not_a_change_of_mind(store):
    """B-2135: past coding taught with one description on three accounts of one voucher, every month: the last
    line's account was coded with no one picking ("coded this way 1 time"), the others dropped."""
    reference = load_chart(store)
    accounts = [(MAIN, "7510.5107"), (FACILITIES, "7510.5107"), (MAIN, "7420.4207")]
    store.import_history([
        {"vendor_name": "Clearwater Wireless Inc.", "description": "Execs cellphone", "gl_code": gl,
         "cost_center": unit, "amount": 80.0, "date": f"2025-{month:02d}-15"}
        for month in range(1, 7) for unit, gl in accounts
    ])  # fmt: skip
    line = _line(store, reference, "Execs cellphone", vendor="Clearwater Wireless Inc.").coding.line_items[0]
    assert line.predicted_gl_code == UNASSIGNED and line.decided_by == {}
    assert {c["account"] for c in line.candidates} >= {f"{MAIN}.7510.5107", f"{FACILITIES}.7510.5107"}


def test_with_no_chart_and_no_journal_a_line_is_coded_as_before(store, reference):
    """An install with neither: the same account, cost center and words; the new record is added beside them."""
    rows = [{"vendor_name": "Staples", "description": f"Copy paper {size}", "gl_code": "6000", "cost_center": "CC200",
             "amount": 10.0, "date": "2025-01-01"} for size in ("letter", "legal", "A4", "tabloid")]  # fmt: skip
    store.import_history(rows)
    feedback = store.feedback_rows()
    result = code_from_capture(captured("Staples", "Copy paper letter", "Unheard-of thing"), reference, feedback, store)
    known, unknown = result.coding.line_items
    assert (known.predicted_gl_code, known.predicted_cost_center) == ("6000", "CC200")
    assert known.reasoning_justification.startswith("coded this way 4 times for similar lines from this vendor")
    assert known.decided_by == {"source": "vendor"} and result.offered == {}
    # (the vendor's usual account, as before: its four different lines all went to it; no cost center with it)
    assert (unknown.predicted_gl_code, unknown.predicted_cost_center) == ("6000", UNASSIGNED)
    assert unknown.decided_by == {"source": "vendor", "usual": True}
    legacy = suggest_gl("Copy paper letter", "Staples", feedback, reference, limit=20)
    assert [(c.code, c.business_unit) for c in candidates("Copy paper letter", "Staples", feedback, reference)][
        : len(legacy)] == [(s.gl_code, s.cost_center) for s in legacy]  # fmt: skip
    nothing = code_from_capture(captured("Nobody", "Zzz"), reference, [], store).coding.line_items[0]
    assert nothing.reasoning_justification == NO_HISTORY and nothing.candidates == []
    assert "candidates" not in nothing.model_dump() and "decided_by" not in nothing.model_dump()


def test_an_invoice_stored_before_reads_and_is_stored_unchanged(ground_truth):
    coding = InvoiceCoding.model_validate(ground_truth)
    assert all(li.candidates == [] and li.decided_by == {} for li in coding.line_items)
    assert coding.model_dump(mode="json")["line_items"][0].keys() == ground_truth["line_items"][0].keys()


def test_lines_learned_on_a_closed_business_unit_are_not_coded_alone_to_another_unit(store, tmp_path):
    """B-2518: AP coded a vendor's courier lines to Facilities; Facilities closes. The account was coded alone on the
    only other business unit that has it, which nobody ever chose for the vendor: now offered there for a person."""
    reference = load_chart(store)
    for n in range(3):
        approve(store, tmp_path, _line(store, reference, "Courier charges overnight", "Rapidway Courier Ltd.",
                                       f"INV-{n}").coding, [f"{FACILITIES}.7420.4207"])  # fmt: skip
    line = _line(store, reference, "Courier charges overnight", "Rapidway Courier Ltd.", "INV-9").coding.line_items[0]
    assert (line.predicted_cost_center, line.predicted_gl_code) == (FACILITIES, "7420.4207")  # learned
    store.import_business_units([BuRow(code, name, "00310", "IS", "INA" if code == FACILITIES else status)
                                 for code, (name, status) in books.UNITS.items()])  # fmt: skip
    line = _line(store, store.reference_data(), "Courier charges overnight", "Rapidway Courier Ltd.",
                 "INV-10").coding.line_items[0]  # fmt: skip
    assert line.predicted_gl_code == UNASSIGNED
    assert (line.candidates[0]["business_unit"], line.candidates[0]["code"]) == (MAIN, "7420.4207")
    assert f"business unit {FACILITIES} is no longer active: offered on {MAIN}" in line.candidates[0]["reason"]
