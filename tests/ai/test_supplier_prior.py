"""The supplier prior (D-212): how a supplier's lines were coded last year and here, by account string."""

import datetime as dt

import pytest

from ap_coder.ai import supplier_prior as sp
from ap_coder.ai.offline_coder import addressed_to, code_from_capture
from ap_coder.ai.supplier_prior import prior_for, rank, remark_shape
from ap_coder.bench.jde_invoices import printed_name
from ap_coder.core.caches import size_of
from ap_coder.core.chart import split_account
from tests.books import FACILITIES, MAIN, approve, captured, load_chart, load_journal, monthly

JANITOR = f"{MAIN}.7340.3307"
CARPET = f"{MAIN}.7340.3313"
COURIER = f"{MAIN}.7420.4207"


def _vendor(store, name, number=""):
    store.import_vendor_master([{"vendor_name": name, "erp_id": str(number), "status": "active"}], by_erp_id=True)


def test_a_database_with_no_journal_has_no_prior(store):
    load_chart(store)
    assert prior_for(store, "Brightway Janitorial Ltd.") is sp.EMPTY and not sp.EMPTY


@pytest.mark.parametrize(
    "remark, shape",
    [
        ("KT Jul.cellphone", "kt cellphone"),
        ("KT Aug.cellphone", "kt cellphone"),
        ("May janitorial 410 Elm", "janitorial 410 elm"),
        ("Q3 2026 audit fees", "audit fees"),
        ("INVOICE 884512 courier", "invoice courier"),
        ("Sept/24 lease 2-3-104", "lease 2-3-104"),
        ("", ""),
    ],
)
def test_a_remarks_shape_leaves_out_the_month_the_quarter_and_long_numbers(remark, shape):
    assert remark_shape(remark) == shape


def test_a_suppliers_lines_are_counted_by_account_string(store):
    load_chart(store)
    _vendor(store, "BRIGHTWAY JANITORIAL LTD.", 740075)
    vouchers = monthly("00740075", "BRIGHTWAY JANITORIAL LTD.", JANITOR, "{month} janitorial 410 Elm", 9)
    vouchers += [(740075, "BRIGHTWAY JANITORIAL LTD.", dt.date(2025, 10, 3),
                  [(CARPET, 300, "carpet cleaning"), (f"{FACILITIES}.7340.3313", 200, "carpet cleaning")])]  # fmt: skip
    load_journal(store, vouchers)
    prior = prior_for(store, "Brightway Janitorial Ltd.")
    assert (prior.found_by, prior.supplier_number, prior.lines, prior.vouchers) == ("number", "740075", 11, 10)
    top = prior.accounts[0]
    assert (top.account, top.business_unit, top.code, top.lines, top.vouchers) == (JANITOR, MAIN, "7340.3307", 9, 9)
    assert round(top.share, 3) == round(9 / 11, 3) and top.last_date == "2025-09-15"
    assert [(r.shape, r.lines, r.example) for r in top.remarks] == [("janitorial 410 elm", 9, "Sep janitorial 410 Elm")]
    assert prior.one_line_share == 0.9  # nine of its ten vouchers have one coding line
    assert prior.units_of == {"7340.3307": (MAIN,), "7340.3313": (MAIN, FACILITIES)}
    assert prior.sure() is None  # 82% of its lines: not 90%
    assert prior_for(store, "Brightway Janitorial Ltd.") is prior  # kept until something changes


def test_a_supplier_number_the_journal_does_not_have_is_never_looked_up_by_name(store):
    """The vendor list says the vendor is supplier 555; the journal has the same name under 740075 only because the
    two read alike once cut: the number decides, and a whole-name twin is the one case that is added."""
    load_chart(store)
    _vendor(store, "RAPIDWAY COURIER CANADA LIMITED PARTNERSHIP", 555)
    load_journal(store, monthly(740075, "RAPIDWAY COURIER CANADA LI", COURIER, "courier charges", 4))
    prior = prior_for(store, "Rapidway Courier Canada Limited Partnership")
    assert not prior and prior.found_by == "" and prior.journal


def test_without_a_number_the_supplier_is_found_by_the_name_the_journal_prints(store):
    load_chart(store)
    load_journal(store, [
        *monthly(1001, "RAPIDWAY COURIER CANADA LT", COURIER, "courier charges", 3),  # cut at 26 characters
        *monthly(1002, "NORTHERN PAPER", JANITOR, "paper", 3),  # whole
        *monthly(1003, "OAK & EMBER STATIONERS INC", CARPET, "pens", 3),  # 26 characters, whole but for the dot
    ])  # fmt: skip
    cut = prior_for(store, "Rapidway Courier Canada Ltd.")
    assert (cut.found_by, cut.supplier_number, cut.supplier_name) == ("name", "1001", "RAPIDWAY COURIER CANADA LT")
    assert prior_for(store, "Northern Paper Ltd").supplier_number == "1002"  # legal forms aside
    assert prior_for(store, "Oak and Ember Stationers Inc.").supplier_number == "1003"  # "&" is "and"
    # False matches: a whole name that only starts the vendor's, a vendor's that only starts a cut one.
    assert not prior_for(store, "Northern Paper Recycling Inc.")
    assert not prior_for(store, "Rapidway Courier")
    assert not prior_for(store, "Rapidway Couriers of Canada Ltd.")


def test_two_suppliers_printed_with_the_same_26_characters_are_not_told_apart_by_name(store):
    load_chart(store)
    load_journal(store, [
        *monthly(2001, "ALDERBROOK OFFICE PRODUCTS", COURIER, "courier", 3),
        *monthly(2002, "ALDERBROOK OFFICE PRODUCTS", JANITOR, "cleaning", 3),
        *monthly(2003, "HEARTHSIDE BUILDING SERVIC", JANITOR, "cleaning", 3),
        *monthly(2004, "HEARTHSIDE BUILDING SERVIC", CARPET, "carpets", 3),
    ])  # fmt: skip
    cut = prior_for(store, "Hearthside Building Services East Ltd.")  # which of the two? Neither is taken.
    assert not cut and "2 suppliers of last year's journal print the start of this name (2003, 2004)" in cut.note
    # The same whole name under two numbers is one vendor in the vendor list: counted together, and said.
    whole = prior_for(store, "Alderbrook Office Products")
    assert whole.lines == 6 and {a.account for a in whole.accounts} == {COURIER, JANITOR} and whole.sure() is None
    assert whole.note == "2 supplier numbers of last year's journal go by this name (2001, 2002)"


def test_a_line_approved_here_outweighs_the_journal_and_a_correction_more(store, tmp_path):
    """A journal line counts 1, a line approved as proposed 3, a line a person coded or changed 5."""
    reference = load_chart(store)
    _vendor(store, "BRIGHTWAY JANITORIAL LTD.", 740075)
    load_journal(store, monthly(740075, "BRIGHTWAY JANITORIAL LTD.", JANITOR, "{month} janitorial", 4))
    before = prior_for(store, "Brightway Janitorial Ltd.")
    assert before.sure().account == JANITOR and before.weight == 4
    coding = code_from_capture(captured("Brightway Janitorial Ltd.", "Nightly office cleaning"), reference,
                               store.feedback_rows(), store).coding  # fmt: skip
    approve(store, tmp_path, coding, [CARPET])  # a person changes the account proposed
    after = prior_for(store, "Brightway Janitorial Ltd.")
    assert after is not before and [(a.account, a.weight) for a in after.accounts] == [(CARPET, 5.0), (JANITOR, 4.0)]
    assert after.latest_approved == CARPET and after.sure() is None and after.total_said() == (
        "5 lines (4 last year, 1 approved here)")  # fmt: skip
    # Kept as proposed the next time: 3 more for that account.
    again = code_from_capture(captured("Brightway Janitorial Ltd.", "Nightly office cleaning", number="INV-2"),
                              reference, store.feedback_rows(), store).coding  # fmt: skip
    assert again.line_items[0].predicted_gl_code == "7340.3313"  # this vendor's own approval, as before
    approve(store, tmp_path, again, [CARPET])
    assert prior_for(store, "Brightway Janitorial Ltd.").accounts[0].weight == 8.0


def test_a_supplier_with_one_line_last_year_is_counted_in_the_singular(store):
    """ "1 line last year" where the line left for a person said "this supplier's 1 lines" (B-2182)."""
    reference = load_chart(store)
    _vendor(store, "BRIGHTWAY JANITORIAL LTD.", 740075)
    load_journal(store, monthly(740075, "BRIGHTWAY JANITORIAL LTD.", JANITOR, "{month} janitorial", 1))
    assert prior_for(store, "Brightway Janitorial Ltd.").total_said() == "1 line last year"
    coding = code_from_capture(captured("Brightway Janitorial Ltd.", "Nightly office cleaning"), reference,
                               store.feedback_rows(), store).coding  # fmt: skip
    assert coding.line_items[0].reasoning_justification == (
        f"Only 1 of this supplier's 1 line last year went to {JANITOR}: too few to code it alone: pick the account."
    )


def test_thirty_lines_of_history_are_not_sure_for_a_line_like_one_a_person_coded_elsewhere(store, tmp_path):
    """A line a person coded to another account is evidence about lines like it, which are no longer coded alone;
    a line worded otherwise still is, on the thirty lines of history (B-2252: it was left too)."""
    reference = load_chart(store)
    _vendor(store, "BRIGHTWAY JANITORIAL LTD.", 740075)
    printed = "BRIGHTWAY JANITORIAL LTD."
    load_journal(store, [*monthly(740075, printed, JANITOR, "{month} janitorial", 12),
                         *monthly(740075, printed, JANITOR, "{month} janitorial", 12, year=2024),
                         *monthly(740075, printed, JANITOR, "{month} janitorial", 6, year=2023)])  # fmt: skip
    coding = code_from_capture(captured("Brightway Janitorial Ltd.", "Steam clean boardroom carpets"), reference,
                               store.feedback_rows(), store).coding  # fmt: skip
    # 30 of 30 lines, but the line names the "Carpet" of another account of the chart (B-2251: it was coded alone).
    assert coding.line_items[0].predicted_gl_code == "UNASSIGNED" and coding.line_items[0].reasoning_justification == (
        f"This supplier's lines usually go to {JANITOR}, but this line reads like Carpet & Window Cleaning "
        f"({CARPET}): check it.")  # fmt: skip
    approve(store, tmp_path, coding, [CARPET])
    prior = prior_for(store, "Brightway Janitorial Ltd.")
    assert prior.accounts[0].account == JANITOR and round(prior.accounts[0].share, 3) == round(30 / 35, 3)
    assert prior.sure() is None  # of all its lines: 86%, and the last line approved here went elsewhere
    alike = "Steam clean the lobby and stair carpets on two floors"
    assert prior.sure("Day porter service").account == JANITOR and prior.sure(alike) is None
    porter, carpets = code_from_capture(captured("Brightway Janitorial Ltd.", "Day porter service", alike,
                                                 number="INV-2"), reference, store.feedback_rows(),
                                        store).coding.line_items  # fmt: skip
    assert porter.predicted_gl_code == "7340.3307"
    assert porter.reasoning_justification == "30 of this supplier's 31 lines (30 last year, 1 approved here)"
    assert carpets.predicted_gl_code == "UNASSIGNED"
    assert [c["account"] for c in carpets.candidates[:2]] == [CARPET, JANITOR]


def test_rank_orders_by_share_and_words_and_says_why_with_the_counts(store):
    reference = load_chart(store)
    _vendor(store, "RAPIDWAY COURIER CANADA LTD.", 3001)
    load_journal(store, [
        *monthly(3001, "RAPIDWAY COURIER CANADA LT", COURIER, "{month} courier charges", 9),
        *monthly(3001, "RAPIDWAY COURIER CANADA LT", f"{MAIN}.7420.4213", "customs courier", 4),
        *monthly(3001, "RAPIDWAY COURIER CANADA LT", JANITOR, "mailroom cleaning", 2),
    ])  # fmt: skip
    prior = prior_for(store, "Rapidway Courier Canada Ltd.")
    plain = rank(prior, "Fuel surcharge", reference)  # no word says anything: the shares
    assert [c.account for c in plain] == [COURIER, f"{MAIN}.7420.4213", JANITOR]
    assert plain[0].reason == "9 of this supplier's 15 lines last year" and plain[0].source == "supplier"
    assert (plain[0].lines, plain[0].of, round(plain[0].share, 2), plain[0].example) == (
        9,
        15,
        0.6,
        "Sep courier charges",
    )
    customs = rank(prior, "Customs brokerage - entry 77812", reference)
    assert customs[0].account == f"{MAIN}.7420.4213"
    assert customs[0].reason == "4 of this supplier's 15 lines last year; 4 of its 4 “custom” lines"
    cleaning = rank(prior, "Cleaning of the mailroom", reference)
    assert cleaning[0].account == JANITOR and cleaning[0].reason == (
        "2 of this supplier's 15 lines last year; 2 of its 2 “cleaning” lines; the word “cleaning” in the account's "
        "name")  # fmt: skip
    named = rank(prior, "Courier", reference)[0]
    assert named.account == COURIER and "the word “courier” in the account's name" in named.reason


def test_a_prior_is_small_and_kept_within_the_caches_bytes(store):
    load_chart(store)
    load_journal(store, [v for n in range(40) for v in monthly(5000 + n, f"SUPPLIER {n:02d} SERVICES", JANITOR,
                                                               "{month} janitorial", 6)])  # fmt: skip
    sizes = [size_of(prior_for(store, f"Supplier {n:02d} Services")) for n in range(40)]
    assert max(sizes) < 8_000  # (the made-up year's 950 suppliers: 4.6 KB each at the median, 5 MB in all)
    assert sp._KEPT.held <= sp._KEPT.most and len(sp._KEPT) >= 40


def _line_of(store, reference, vendor, text):
    return code_from_capture(captured(vendor, text), reference, store.feedback_rows(), store).coding.line_items[0]


def test_a_line_that_plainly_says_another_kind_of_thing_is_not_coded_to_the_suppliers_one_account(store):
    """B-2228: a supplier whose every line went to one account had ALL its lines coded there with no one picking,
    a lunch or a box of stationery on a courier's invoice too. The one account still codes a line whose words say
    nothing of any account (the supplier's own: a waybill, a surcharge); a line that reads as a meal, or like the
    name of another account, is left for a person with both accounts offered."""
    reference = load_chart(store)
    _vendor(store, "RAPIDWAY COURIER CANADA LTD.", 3001)
    load_journal(store, monthly(3001, "RAPIDWAY COURIER CANADA LT", COURIER, "{month} courier charges", 12))
    vendor = "Rapidway Courier Canada Ltd."
    for text in ("Ground shipment waybill 77812", "Fuel surcharge", "Courier charges March", "Desk to desk delivery"):
        line = _line_of(store, reference, vendor, text)
        assert f"{line.predicted_cost_center}.{line.predicted_gl_code}" == COURIER, text
        assert line.reasoning_justification.startswith("12 of this supplier's 12 lines last year")
    lunch = _line_of(store, reference, vendor, "Working lunch for the project kickoff, 12 people")
    assert lunch.predicted_gl_code == "UNASSIGNED" and lunch.candidates[0]["account"] == COURIER
    assert lunch.reasoning_justification == (
        f"This supplier's lines usually go to {COURIER}, but this line reads like a meal: check it.")  # fmt: skip
    paper = _line_of(store, reference, vendor, "Stationery and desk supplies")
    stationery = f"{MAIN}.7410.4107"
    assert paper.predicted_gl_code == "UNASSIGNED"
    assert [c["account"] for c in paper.candidates[:2]] == [COURIER, stationery]
    assert paper.reasoning_justification == (
        f"This supplier's lines usually go to {COURIER}, but this line reads like Stationery & Desk Supplies "
        f"({stationery}): check it.")  # fmt: skip


def test_a_meal_on_the_invoice_of_a_supplier_of_meals_is_coded_to_its_one_account(store):
    """The guard is for another kind of thing: a caterer's lunch is its usual account's, as AP's remarks for it
    say."""
    reference = load_chart(store)
    _vendor(store, "HARVEST TABLE CATERING INC.", 5200)
    load_journal(store, monthly(5200, "HARVEST TABLE CATERING INC", f"{MAIN}.7410.4107", "{month} staff lunch", 12))
    line = _line_of(store, reference, "Harvest Table Catering Inc.", "Sandwich platters - board lunch")
    assert line.predicted_gl_code == "7410.4107"
    assert line.reasoning_justification.startswith("12 of this supplier's 12 lines last year")


# --- the made-up company (``chart_company``): what a reviewer of its first day found --------------------------------


def _courier(chart_company):
    """(the name its invoices print, its one account) of the supplier whose eight lines last year went to one
    account, Courier - Cross Border."""
    supplier = chart_company.invoices["one_line"][0].supplier
    return printed_name(supplier.name), supplier.accounts[0]


def _coded(chart_company, vendor, *texts, number="INV-1"):
    store = chart_company.store
    return code_from_capture(captured(vendor, *texts, number=number), store.reference_data(), store.feedback_rows(),
                             store).coding  # fmt: skip


def _account(line):
    return "" if line.predicted_gl_code == "UNASSIGNED" else f"{line.predicted_cost_center}.{line.predicted_gl_code}"


def test_a_line_that_names_another_account_of_the_chart_is_not_coded_to_the_suppliers_one_account(chart_company):
    """B-2251: a laptop, a hotel, a software subscription, consulting and a pizza on a courier's invoice arrived on
    the courier account with no one picking: a line was compared only with the few accounts already offered for it,
    and with a short list of meal words. A line that names another account of the chart, anywhere in it, is left
    with that account offered next; one whose words name no account is the supplier's own, as before."""
    vendor, courier = _courier(chart_company)
    reads_like = {
        "Dell Latitude 5540 laptop": "Laptops & Desktops",
        "Hotel accommodation 2 nights": "Hotels & Lodging",
        "Annual software subscription renewal": "Software Subscriptions",
        "Consulting services - October": "Management Consulting",
        "Pizza for staff meeting": "",
        "Refreshments for training day": "",
    }
    for line in _coded(chart_company, vendor, *reads_like).line_items:
        name = reads_like[line.description]
        assert _account(line) == "" and line.candidates[0]["account"] == courier, line.description
        if name:
            other = line.candidates[1]
            assert other["name"] == name and line.reasoning_justification == (
                f"This supplier's lines usually go to {courier}, but this line reads like {name} "
                f"({other['account']}): check it.")  # fmt: skip
        else:
            assert line.reasoning_justification == (
                f"This supplier's lines usually go to {courier}, but this line reads like a meal: check it."
            )
    # No account of this chart is named for a chair, interest or a golf tournament, and "ground" and "fuel" alone are
    # not "Ground Transport" or "Fleet Fuel": nothing says these are not the courier's, and they are coded.
    own = ("Courier service downtown", "Ground shipment waybill 77812", "Fuel surcharge", "Golf tournament tickets",
           "Ergonomic office chair", "Late payment interest")  # fmt: skip
    for line in _coded(chart_company, vendor, *own, number="INV-2").line_items:
        assert _account(line) == courier, line.description
        assert line.reasoning_justification.startswith("8 of this supplier's 8 lines last year")


def test_a_line_alike_to_nothing_of_the_suppliers_account_and_to_another_accounts_name_is_left(chart_company):
    """B-2370: an insurance premium, a desk and a software licence on the courier's invoice were coded alone to its
    one account, with no finding: none of them says every word of another account's name, nor is half alike to one.
    A line alike to nothing known of the supplier's account that says a word of another offered account's name the
    supplier's has not, and is that alike to it, is left for a person with that account first."""
    vendor, courier = _courier(chart_company)
    kinds = {"Insurance premium - cargo policy": "Insurance", "Office furniture - desk": "Furniture",
             "Software licence renewal": "Software"}  # fmt: skip
    for line in _coded(chart_company, vendor, *kinds).line_items:
        assert _account(line) == "", line.description
        first = line.candidates[0]
        assert kinds[line.description] in first["name"], line.description
        assert courier in [c["account"] for c in line.candidates[1:4]], line.description
        assert line.reasoning_justification == (
            f"This supplier's lines usually go to {courier}, but this line reads like {first['name']} "
            f"({first['account']}): check it.")  # fmt: skip


def test_a_line_coded_by_hand_to_another_account_says_nothing_of_the_suppliers_ordinary_lines(chart_company, tmp_path):
    """B-2252: one lunch approved to Meals on the courier's invoice, and every later line of the supplier arrived
    empty ("69% / 31%: pick the account"), "Courier service airport" too: a person's pick weighs 5. It is evidence
    about lines like it: the courier lines are coded alone again, and the next lunch is not."""
    vendor, courier = _courier(chart_company)
    meals = next(a for a in chart_company.truth.suggestable() if a.description == "Meals - Travel").string
    lunch = "Working lunch for the project kickoff, 12 people"
    first = _coded(chart_company, vendor, "Courier service downtown", lunch)
    approve(chart_company.store, tmp_path, first, [courier, meals])
    prior = prior_for(chart_company.store, vendor)
    assert prior.shares() == "69% / 31%" and prior.sure() is None  # of all its lines, as before
    board = "Working lunch with the board, quarterly review"
    assert prior.sure("Courier service airport").account == courier and prior.sure(board) is None
    later = _coded(chart_company, vendor, "Courier service airport", "Ground shipment waybill 77812", board,
                   "Working lunch", "Team dinner", number="INV-2").line_items  # fmt: skip
    assert [_account(line) for line in later] == [courier, courier, "", meals, ""]
    assert later[0].reasoning_justification.startswith("9 of this supplier's 10 lines (8 last year, 2 approved here)")
    # A lunch like the one approved: the supplier's two accounts, and the shares say why no one is sure.
    assert [c["account"] for c in later[2].candidates[:2]] == [meals, courier]
    assert later[2].reasoning_justification == (
        "This supplier's 10 lines (8 last year, 2 approved here) went to 2 accounts: 69% / 31%: pick the account."
    )
    assert later[3].reasoning_justification.startswith("coded this way 1 time for similar lines from this vendor")
    # A meal worded otherwise: left as a meal, the meals account this supplier's lunch went to offered next.
    assert [c["account"] for c in later[4].candidates[:2]] == [courier, meals]
    assert later[4].reasoning_justification == (
        f"This supplier's lines usually go to {courier}, but this line reads like a meal: check it."
    )


def test_the_business_unit_a_line_names_puts_the_suppliers_account_on_it_first(chart_company):
    """B-2253: each line of the fifteen-line invoice names its business unit ("Smartphone device balance - IT - Data
    Centre"), and every line was offered the same five accounts in the same order, the one AP keys first on 1 line
    of 15 and among the five on 7. The supplier's account on the unit named is now first; no line is coded by it."""
    invoice, invoice_id = chart_company.invoices["fifteen_lines"]
    lines = chart_company.store.get_invoice(invoice_id)["ai_output"]["line_items"]
    keyed = [line.parts[0].account for line in invoice.lines]
    assert [line["candidates"][0]["account"] for line in lines] == keyed
    assert all(line["predicted_gl_code"] == "UNASSIGNED" for line in lines)
    unit = chart_company.store.reference_data().pairs.name_of(lines[0]["candidates"][0]["business_unit"])
    assert lines[0]["description"].endswith(f" - {unit}")
    assert lines[0]["candidates"][0]["reason"].startswith(f"the line names the business unit {unit}; ")


# --- the supplier billed to thirteen business units, and a supplier never seen (D-226) ----------------------------

NEW_SUPPLIER = "A new supplier: not in last year's journal: pick the account."


def _windrow(chart_company):
    """(the name its invoices print, its accounts) of the supplier of the fifteen-line invoice: one object.subsidiary
    on thirteen business units."""
    supplier = chart_company.invoices["fifteen_lines"][0].supplier
    return printed_name(supplier.name), list(supplier.accounts)


def _unit_name(chart_company, account):
    return chart_company.store.reference_data().pairs.name_of(split_account(account)[0])


def _named_by_name(chart_company, accounts):
    """The accounts whose business unit's name names that unit (``units_named``)."""
    reference = chart_company.store.reference_data()
    return [a for a in accounts if split_account(a)[0] in sp.units_named(reference, _unit_name(chart_company, a))]


def test_a_line_left_keeps_every_business_unit_the_supplier_used_its_account_on(chart_company):
    """B-2335: a line of the telephone supplier that names no business unit kept five of its thirteen accounts: a
    person keying another unit found it in no list of the line. It keeps all thirteen; still none is coded."""
    vendor, accounts = _windrow(chart_company)
    line = _coded(chart_company, vendor, "Smartphone device balance").line_items[0]
    assert _account(line) == "" and line.decided_by == {}
    assert sorted(c["account"] for c in line.candidates) == sorted(accounts)
    assert line.candidates[0]["account"] == prior_for(chart_company.store, vendor).accounts[0].account


def test_a_line_that_prints_a_business_units_number_puts_the_suppliers_account_on_it_first(chart_company):
    """B-2336: "Smartphone device balance - cost centre 21061016" (the unit's number, its leading zeros aside) was
    offered the most used unit's account first: only a unit's name was read. Its number names it too."""
    vendor, accounts = _windrow(chart_company)
    for account in (accounts[-1], accounts[-2]):
        unit = split_account(account)[0]
        line = _coded(chart_company, vendor, f"Smartphone device balance - cost centre {unit.lstrip('0')}")
        first = line.line_items[0].candidates[0]
        assert _account(line.line_items[0]) == "" and first["account"] == account
        assert first["reason"].startswith(f"the line names the business unit {_unit_name(chart_company, account)}; ")
    # A quantity is no unit's number.
    line = _coded(chart_company, vendor, "Wireless voice & data plan - 24 subscribers").line_items[0]
    assert not line.candidates[0]["reason"].startswith("the line names")


def test_the_invoices_ship_to_or_attention_line_puts_the_suppliers_account_on_the_unit_it_names_first(chart_company):
    """B-2337: an invoice printed "Ship to: ... Attn: <a department>", and each line that names no unit was offered
    the most used unit's account first. A line that names none goes by what the invoice is addressed to; a line
    that names its own unit keeps it; the bill-to (AP's own address) names nothing."""
    store = chart_company.store
    vendor, accounts = _windrow(chart_company)
    attention, own = _named_by_name(chart_company, accounts[1:])[:2]
    text = (
        f"{vendor}\nBill to: Maple Ridge Holdings Ltd.\nAttn: Accounts Payable\n410 Elm Street\n"
        f"Ship to: Maple Ridge Holdings Ltd.\nAttn: {_unit_name(chart_company, attention)}\n410 Elm Street\n"
    )
    read = captured(
        vendor, "Smartphone device balance", f"Roaming and airtime usage - {_unit_name(chart_company, own)}"
    )
    coding = code_from_capture(read, store.reference_data(), store.feedback_rows(), store, text=text).coding
    first, second = coding.line_items
    assert (_account(first), _account(second)) == ("", "")
    assert first.candidates[0]["account"] == attention and first.candidates[0]["reason"].startswith(
        f"the invoice's ship-to or attention names the business unit {_unit_name(chart_company, attention)}; "
    )
    assert second.candidates[0]["account"] == own
    assert addressed_to(text) == (f"Ship to: Maple Ridge Holdings Ltd.\nAttn: {_unit_name(chart_company, attention)}"
                                  "\n410 Elm Street")  # fmt: skip


def test_an_office_only_one_units_remarks_name_puts_the_suppliers_account_on_that_unit_first(chart_company):
    """B-2338: AP ends a remark with the office a purchase is for ("cell phones 77 Kingfisher"), and the supplier's
    lines for that office all went to one business unit, yet a line printing the office was offered the most used
    unit's account first. A word only one unit's remarks have, which the journal writes with every kind of purchase
    (an office, not a thing), names that unit; a word of what was bought does not."""
    store = chart_company.store
    vendor, accounts = _windrow(chart_company)
    office = accounts[-1]
    number, printed = store.find_vendor(vendor)["erp_id"], vendor.upper()[:26]
    others = [a.string for a in chart_company.truth.suggestable()][:8]
    load_journal(store, [
        (number, printed, dt.date(2025, 11, 3), [(office, 80, "cell phones 77 Kingfisher")]),
        (number, printed, dt.date(2025, 12, 3), [(office, 80, "cell phones 77 Kingfisher")]),
        (98815, "TERN OFFICE SERVICES", dt.date(2025, 12, 5), [(a, 10, "misc purchase 77 Kingfisher") for a in others]),
    ], name="more.csv")  # fmt: skip
    prior = prior_for(store, store.find_vendor(vendor))
    assert prior.unit_words.get("kingfisher") == split_account(office)[0] and "phone" not in prior.unit_words
    line = _coded(chart_company, vendor, "Smartphone device balance - 77 Kingfisher").line_items[0]
    assert _account(line) == "" and line.candidates[0]["account"] == office
    assert line.candidates[0]["reason"].startswith(
        f"the line says “kingfisher”, as AP's remarks for this supplier's lines on {_unit_name(chart_company, office)} "
        "alone did; "
    )


def test_a_new_suppliers_line_is_offered_the_accounts_the_journal_has_for_its_words_and_its_name(chart_company):
    """B-2339: a supplier in neither the supplier master nor last year's journal was offered only the accounts whose
    name has a word of the line: Courier - Domestic for "Customs courier - entry 4471", though the department's
    remarks say "customs courier" of Courier - Cross Border, and nothing for "Ground shipment waybill" from a
    courier, though the suppliers named "Courier" all went to postage and courier. Its lines are offered what the
    journal as a whole has for their words and for the vendor's name, saying so; none is coded."""
    store = chart_company.store
    _, cross_border = _courier(chart_company)
    domestic = f"{split_account(cross_border)[0]}.7420.4207"
    load_journal(store, [(98825, "NORTHGATE COURIER LTD.", dt.date(2025, 12, 1), [(domestic, 40, "waybills")]),
                         (98825, "NORTHGATE COURIER LTD.", dt.date(2025, 12, 8), [(domestic, 40, "waybills")])],
                 name="couriers.csv")  # fmt: skip
    lines = _coded(chart_company, "Blue Heron Courier Inc.", "Customs courier - entry 4471",
                   "Ground shipment waybill 77812", "SaaS seats x 3 - annual").line_items  # fmt: skip
    assert [_account(line) for line in lines] == ["", "", ""]
    assert all(line.reasoning_justification == NEW_SUPPLIER and not line.decided_by for line in lines)
    customs, ground, saas = lines
    assert customs.candidates[0]["account"] == cross_border and customs.candidates[0]["source"] == "others"
    assert customs.candidates[0]["reason"].startswith("AP wrote “")
    assert {c["account"] for c in ground.candidates[:2]} == {cross_border, domestic}
    assert "suppliers named “courier” had lines on it last year" in ground.candidates[0]["reason"]
    # Near no remark and no account's name: what suppliers named alike went to, by the name alone.
    assert {c["account"] for c in saas.candidates} == {cross_border, domestic}
    assert all(c["reason"].startswith("1 of the 2 suppliers named “courier”") for c in saas.candidates)
