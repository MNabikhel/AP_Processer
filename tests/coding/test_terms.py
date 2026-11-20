"""Payment terms, due dates and early-payment discounts."""

import datetime as dt
import json
import sqlite3

import pytest

from ap_coder.coding.terms import Terms, describe, parse_terms, payment, payment_findings, terms_key
from ap_coder.core.store import Store
from tests.conftest import SAMPLE_STEM, SAMPLES


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Net 30", Terms(30)),
        ("N/45", Terms(45)),
        ("Payment terms: Net 30. Thank you", Terms(30)),
        ("Payable dans les 30 jours", Terms(30)),
        ("2/10 Net 30", Terms(30, 2.0, 10)),
        ("1.5/15 n60", Terms(60, 1.5, 15)),
        ("2% 10 net 30", Terms(30, 2.0, 10)),
        ("1% 15 days, net 45", Terms(45, 1.0, 15)),
        ("2 % escompte 10 jours, net 30 jours", Terms(30, 2.0, 10)),
        ("Due on receipt", Terms(0, on_receipt=True)),
        ("Payable sur réception", Terms(0, on_receipt=True)),
        ("Net 30 due 10/31", Terms(30)),  # a date is not a 10% discount
        ("2/10 EOM", Terms(30, 2.0, 10, eom=True)),  # no net printed: due 20 days after the discount date
        ("2/10 EOM net 45", Terms(45, 2.0, 10, eom=True)),
        ("Net 30, 2% 10", Terms(30, 2.0, 10)),  # "days" left out
        ("Net 30 - 1.5% per month on overdue", Terms(30)),
        ("1.5% interest per month after 30 days", Terms(30)),
        ("Overdue accounts are charged 2% interest per month", Terms()),
        # A rate for a number of months or years, or charged on late payment, is interest, not a discount.
        ("Net 30 days 1.5% 1 month interest", Terms(30)),
        ("Net 30, 2% 2 months", Terms(30)),
        ("Net 30, 1% 1 an", Terms(30)),
        ("Net 30. Interest of 2% 30 days after due date", Terms(30)),
        ("Net 30 days; 2% 30 days past due", Terms(30)),
        ("Net 15, 2% 30 days late charge", Terms(15)),
        ("Net 30, 1.5% per month on overdue accounts, 2% 10 days", Terms(30, 2.0, 10)),
        ("Net 30 jours, 2% 10 jours, intérêts 1% par mois", Terms(30, 2.0, 10)),
        ("", Terms()),
    ],
)
def test_parse_terms(text, expected):
    assert parse_terms(text) == expected


def _doc(**kw):
    base = {
        "invoice_date": "2026-10-01",
        "payment_terms": "",
        "due_date": "",
        "subtotal": 1000.0,
        "grand_total": 1130.0,
    }
    return {**base, **kw}


def test_due_date_printed_then_terms_then_default():
    assert payment(_doc(due_date="2026-10-20", payment_terms="Net 60")).due == dt.date(2026, 10, 20)
    p = payment(_doc(payment_terms="Net 15"))
    assert p.due == dt.date(2026, 10, 16) and p.source == "terms"
    assert payment(_doc(payment_terms="Due on receipt")).due == dt.date(2026, 10, 1)
    p = payment(_doc(), default_days=45)
    assert p.due == dt.date(2026, 11, 15) and p.source == "default"
    assert payment(_doc(invoice_date="")).due is None


def test_discount_deadline_and_amount():
    p = payment(_doc(payment_terms="2/10 Net 30"))
    assert p.discount_by == dt.date(2026, 10, 11) and p.discount_amount == 20.0
    assert p.discount_open(dt.date(2026, 10, 11)) and not p.discount_open(dt.date(2026, 10, 12))
    assert payment(_doc(payment_terms="2/10 Net 30", grand_total=-50.0)).discount_by is None  # credit note


def test_findings():
    today = dt.date(2026, 10, 5)
    codes = {c for _, c, _ in payment_findings(_doc(payment_terms="2/10 Net 30"), today=today)}
    assert codes == {"DISCOUNT_AVAILABLE"}
    late = payment_findings(_doc(payment_terms="Net 15"), today=dt.date(2026, 11, 1))
    assert [(s, c) for s, c, _ in late] == [("info", "PAYMENT_OVERDUE")]
    odd = payment_findings(_doc(due_date="2026-09-01"), today=today)
    assert ("warning", "DUE_BEFORE_INVOICE") in [(s, c) for s, c, _ in odd]
    assert payment_findings(_doc(grand_total=-10.0, payment_terms="Net 1"), today=dt.date(2027, 1, 1)) == []


def test_describe():
    p = payment(_doc(due_date="2026-10-10"))
    assert describe(p, dt.date(2026, 10, 5)) == "Due in 5 days (Oct 10)"
    assert describe(p, dt.date(2026, 10, 10)) == "Due today (Oct 10)"
    assert describe(p, dt.date(2026, 10, 11)) == "Overdue by 1 day (Oct 10)"
    assert describe(payment(_doc(invoice_date=""))) == "No due date"


def test_store_keeps_the_due_date_and_the_default(tmp_path):
    store = Store(tmp_path / "a.db")
    gt = json.loads((SAMPLES / "ground_truth" / f"{SAMPLE_STEM}.json").read_text())
    printed = store.add_invoice(tmp_path / "a.pdf", gt, {})
    store.set_setting("default_terms_days", "10")
    assert store.default_terms_days() == 10
    no_terms = store.add_invoice(
        tmp_path / "b.pdf", {**gt, "invoice_number": "X", "payment_terms": "", "due_date": ""}, {}
    )
    due = {i["id"]: i["due_date"] for i in store.list_invoices()}
    assert due[printed] == "2026-10-14" and due[no_terms] == "2026-09-24"
    store.set_setting("default_terms_days", "soon")
    assert store.default_terms_days() == 30


def test_upgrade_fills_in_due_dates(tmp_path):
    path = tmp_path / "old.db"
    store = Store(path)
    gt = json.loads((SAMPLES / "ground_truth" / f"{SAMPLE_STEM}.json").read_text())
    store.add_invoice(tmp_path / "a.pdf", gt, {})
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE invoices SET due_date = NULL")
        conn.execute("UPDATE settings SET value = '4' WHERE key = 'schema_version'")
    assert Store(path).list_invoices()[0]["due_date"] == "2026-10-14"


def test_queue_sorts_by_due_date_with_unknown_last():
    from ap_coder.web.pages.review import sort_queue

    rows = [{"id": 1, "due_date": "2026-11-01"}, {"id": 2, "due_date": None}, {"id": 3, "due_date": "2026-10-01"}]
    assert [r["id"] for r in sort_queue(rows, "due")] == [3, 1, 2]


def test_a_printed_discount_is_kept_when_the_net_days_come_from_the_vendor_master():
    coding = {"payment_terms": "2% 10 days", "invoice_date": "2026-09-01", "grand_total": 113.0, "subtotal": 100.0}
    p = payment(coding, vendor_terms="Net 45")
    assert p.due == dt.date(2026, 10, 16) and p.source == "vendor"
    assert p.discount_by == dt.date(2026, 9, 11) and p.discount_amount == 2.0


@pytest.mark.parametrize("invoice_date", ["9999-12-20", "9999-12-31"])
def test_a_date_at_the_end_of_the_calendar_has_no_due_date_instead_of_crashing(invoice_date):
    """An invoice date typed as a placeholder (9999-12-31) made the due date overflow: the review screen's check and
    the approval raised OverflowError."""
    for terms in ("", "Net 30", "Net 30 EOM", "2/10 net 30"):
        coding = {"invoice_date": invoice_date, "payment_terms": terms, "grand_total": 100}
        assert payment(coding).due is None
        assert isinstance(payment_findings(coding), list)


@pytest.mark.parametrize("terms", ["2/10 EOM", "2/10 EOM net 30"])  # with or without the net days printed
def test_2_10_eom_counts_the_discount_and_the_net_from_month_end(terms):
    p = payment({"payment_terms": terms, "invoice_date": "2026-09-15", "grand_total": 113.0, "subtotal": 100.0})
    assert p.source == "terms" and p.discount_amount == 2.0
    assert p.discount_by == dt.date(2026, 10, 10) and p.due == dt.date(2026, 10, 30)


@pytest.mark.parametrize(
    "text, invoice_date, due",
    [
        ("Net 1 month", "2026-09-10", dt.date(2026, 10, 10)),
        ("Net 2 months", "2026-09-10", dt.date(2026, 11, 10)),
        ("net 1 mois", "2026-01-31", dt.date(2026, 2, 28)),  # a calendar month: the last day of a shorter month
        ("2% 10 days, net 1 month", "2026-09-10", dt.date(2026, 10, 10)),
        ("Net 30 days 1.5% 1 month interest", "2026-09-10", dt.date(2026, 10, 10)),  # interest: still net 30 days
    ],
)
def test_net_months_are_calendar_months_never_days(text, invoice_date, due):
    assert payment({"invoice_date": invoice_date, "payment_terms": text, "grand_total": 100}).due == due


@pytest.mark.parametrize(
    "text, pct, amount",
    [
        ("1/2% 10 net 30", 0.5, 50.0),  # half a percent, not 2%
        ("2% 10 net 30", 2.0, 200.0),
        ("1.5% 10 net 30", 1.5, 150.0),
    ],
)
def test_a_half_percent_discount_is_half_a_percent(text, pct, amount):
    p = payment({"invoice_date": "2026-09-10", "payment_terms": text, "grand_total": 10000, "subtotal": 10000})
    assert (p.terms.discount_pct, p.discount_amount) == (pct, amount)


def test_interest_words_of_another_rate_do_not_veto_the_discount():
    """S2-T5: with no punctuation between them, the interest words of the late-payment rate that follows belong to
    that rate, not to the discount before it."""
    assert parse_terms("2/10 net 30 interest 1.5% per month on overdue accounts") == Terms(30, 2.0, 10)
    assert parse_terms("2% 10 net 30 overdue accounts 2% per month") == Terms(30, 2.0, 10)
    assert parse_terms("Net 30 interest 2% 30 days after due date") == Terms(30)  # no later rate: still interest


@pytest.mark.parametrize(
    "text, due",
    [
        ("Net 30, interest of 2% per month charged at end of month on overdue accounts", dt.date(2026, 10, 10)),
        ("Net 30 days. Overdue accounts are charged 1.5% at the end of the month", dt.date(2026, 10, 10)),
        ("Net 30 EOM, interest 2% per month on overdue accounts", dt.date(2026, 10, 30)),  # EOM of the terms: kept
        ("Net 30 EOM overdue interest 2% per month", dt.date(2026, 10, 30)),
    ],
)
def test_end_of_month_in_the_interest_clause_does_not_make_the_terms_eom(text, due):
    """B-135: "charged at end of month on overdue accounts" says when interest is charged, not that the net days
    count from the end of the month (as B-83 did for a discount)."""
    assert payment({"invoice_date": "2026-09-10", "payment_terms": text, "grand_total": 1130}).due == due


@pytest.mark.parametrize(
    "text, due",
    [
        ("Net 2 weeks", dt.date(2026, 9, 24)),
        ("Payable within 2 weeks", dt.date(2026, 9, 24)),
        ("Net 3 semaines", dt.date(2026, 10, 1)),
        ("Net 10 prox", dt.date(2026, 10, 10)),  # the 10th of the next month
        ("Net 25th prox", dt.date(2026, 10, 25)),
        ("Net 31 proximo", dt.date(2026, 10, 31)),
    ],
)
def test_weeks_and_prox_terms_are_never_read_as_days(text, due):
    """B-136: "Net 2 weeks" is 14 days and "Net 3 semaines" 21, never 2 or 3 days; "Net 10 prox" is due on the 10th
    of the month after the invoice's, not 10 days after it."""
    p = payment({"invoice_date": "2026-09-10", "payment_terms": text, "grand_total": 100})
    assert (p.due, p.source) == (due, "terms")
    shorter = payment({"invoice_date": "2026-01-31", "payment_terms": "Net 30 prox", "grand_total": 1})
    assert shorter.due == dt.date(2026, 2, 28)  # the last day of a shorter month
    assert parse_terms("2% 2 weeks net 30").discount_days != 2  # nor a discount's weeks


@pytest.mark.parametrize("text", ["Net EOM", "Due EOM", "Payable fin de mois", "Due end of month"])
def test_eom_terms_with_no_days_are_due_at_the_end_of_the_month(text):
    """B-137: "Net EOM" prints no days: it is due at the end of the invoice's month, not the default 30 days later."""
    p = payment({"invoice_date": "2026-09-10", "payment_terms": text, "grand_total": 100})
    assert (p.due, p.source) == (dt.date(2026, 9, 30), "terms")


@pytest.mark.parametrize(
    "text, discount_by, due",
    [
        ("2/10th prox net 30th", dt.date(2026, 10, 10), dt.date(2026, 10, 30)),  # both days of the next month
        ("2% 10th prox", dt.date(2026, 10, 10), dt.date(2026, 10, 10)),  # no net printed: due that day too
        ("2% 10 prox, net 25th prox", dt.date(2026, 10, 10), dt.date(2026, 10, 25)),
        ("1/10 prox net 25 prox", dt.date(2026, 10, 10), dt.date(2026, 10, 25)),
        ("2/10 prox net 30", dt.date(2026, 10, 10), dt.date(2026, 10, 10)),  # net 30 days from Sep 10
        ("2% 2 weeks net 30", dt.date(2026, 9, 24), dt.date(2026, 10, 10)),  # 14 days
        ("2% 2 wks, net 45", dt.date(2026, 9, 24), dt.date(2026, 10, 25)),
    ],
)
def test_discount_terms_in_prox_or_weeks_give_the_discount(text, discount_by, due):
    """B-216: "2/10th prox net 30th" is 2% off until the 10th of the next month, due on its 30th; "2% 2 weeks net 30"
    is 2% off for 14 days. Neither gave a discount."""
    p = payment({"invoice_date": "2026-09-10", "payment_terms": text, "grand_total": 113, "subtotal": 100})
    pct = float(text[0])
    assert (p.terms.discount_pct, p.discount_by, p.due, p.discount_amount) == (pct, discount_by, due, pct)


def test_a_prox_late_payment_rate_is_still_no_discount():
    """B-216: a rate charged on the 10th prox on overdue accounts is a late-payment rate, not a discount."""
    assert parse_terms("Net 30, 1.5% 10th prox interest on overdue accounts").discount_pct is None


@pytest.mark.parametrize("text", ["2/10 Net 1 month", "2% 10 net 1 month", "1/15 n 1 mois"])
def test_a_discount_with_the_net_in_months_is_never_due_the_next_day(text):
    """B-1460: "2/10 Net 1 month" (the standard wording of "2% 10 days, net 1 month", its JD Edwards terms key) was
    read as a discount and net 1 day: due the day after the invoice date, before its own discount date."""
    p = payment({"invoice_date": "2026-09-10", "payment_terms": text, "grand_total": 113, "subtotal": 100})
    assert p.terms.net_months == 1 and p.terms.discount_pct is not None
    assert p.due == dt.date(2026, 10, 10) and p.discount_by < p.due


def test_a_discount_written_with_slashes_to_the_net_is_kept():
    """B-1461: "2/10/N30" (2% in 10 days, net 30) was read as net 30 with no discount: the early-payment discount
    was never said."""
    assert parse_terms("2/10/N30") == parse_terms("2/10 Net 30") == Terms(30, 2.0, 10)
    assert parse_terms("1%/15/net 45") == Terms(45, 1.0, 15)


@pytest.mark.parametrize("text", ["2/10 or net 30", "2/10, or net 30", "2/10 ou net 30", "2% 10 or net 30"])
def test_a_discount_or_the_net_keeps_the_discount(text):
    """B-1485: "2/10 or net 30" (2% off in 10 days, or the whole in 30) was read as net 30 with no discount."""
    assert parse_terms(text) == Terms(30, 2.0, 10)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Net 1 month, 2/10", Terms(30, 2.0, 10, net_months=1)),
        ("Net 2 months 1/15", Terms(60, 1.0, 15, net_months=2)),
        ("Net 30, 2/10", Terms(30, 2.0, 10)),
        ("Net 30 days or 2/10", Terms(30, 2.0, 10)),
    ],
)
def test_a_discount_after_the_net_is_kept(text, expected):
    """B-1486: the net first and the discount after it ("Net 1 month, 2/10") lost the discount: a slash discount was
    read only before the net."""
    assert parse_terms(text) == expected


def test_net_twelve_months_is_twelve_calendar_months():
    """B-1487: "2/10 net 12 months" fell back to the default days and its terms key was blank: net months stopped at
    6. Up to 12 calendar months are read."""
    p = payment({"invoice_date": "2026-01-31", "payment_terms": "2/10 net 12 months", "grand_total": 113,
                 "subtotal": 100})  # fmt: skip
    assert p.terms == Terms(360, 2.0, 10, net_months=12) and p.source == "terms"
    assert p.due == dt.date(2027, 1, 31) and p.discount_by == dt.date(2026, 2, 10)
    assert terms_key("2/10 net 12 months") == "2/10 Net 12 months" and terms_key("Net 12 months") == "Net 12 months"
    assert parse_terms("Net 13 months") == Terms()  # past a year: not read as terms


@pytest.mark.parametrize(
    "text",
    ["1/15/2026 net 30", "Due 10/15 net 45", "1/15/2026 or net 30", "Due 10/15 or net 45", "Net 30, 1/15/2026",
     "Net 30 10/15", "Net 30, due 1/15", "Net 1 month, 1/15/2026"],
)  # fmt: skip
def test_a_date_beside_the_net_is_never_a_discount(text):
    assert parse_terms(text).discount_pct is None


def test_jd_edwards_terms_codes_count_as_their_terms_once_the_table_is_loaded(store):
    """D-266: a supplier's terms from JD Edwards are a code ("N"), which AP Coder could not read: the invoice was due
    by the default days. With JD Edwards' payment terms table (F0014) loaded, a code is its discount and net days."""
    from ap_coder.coding.terms import JDE_TERMS_ALIASES, JDE_TERMS_SETTING, jde_meaning, jde_table
    from ap_coder.core.headers import match_columns
    from ap_coder.core.memory import vendor_key

    head = ["Payment Terms Code", "Payment Terms Description", "Discount Percent", "Discount Days", "Net Days to Pay"]
    rows = [dict(zip(head, row, strict=True)) for row in (
        ("N", "Net 30", ".00", "0", "30"), ("T", "2% 10, Net 15", ".02", "10", "15"), ("Z", "Due now", "", "", "0"),
        ("", "", "", "", ""), ("Q", "Quarterly", "", "", "soon"))]  # fmt: skip
    table, left = jde_table(rows, match_columns(head, JDE_TERMS_ALIASES))
    assert left == 1 and set(table) == {"N", "T", "Z"} and table["T"]["discount_pct"] == 2.0
    assert [jde_meaning(code, table) for code in ("N", "t", "Z", "Net 45", "")] == [
        "Net 30", "2/10 Net 15", "Due on receipt", "Net 45", ""]  # fmt: skip
    store.import_vendor_master([{"vendor_name": "Acme Ltd", "erp_id": "41001", "terms": "T", "gst": "",
                                 "status": "active"}])  # fmt: skip
    key = vendor_key("Acme Ltd")
    assert store.vendor_terms(key) == "T"
    store.set_setting(JDE_TERMS_SETTING, json.dumps(table))
    assert store.vendor_terms(key) == "2/10 Net 15" and store.all_vendor_terms()[key] == "2/10 Net 15"
    due = payment({"invoice_date": "2026-03-02", "grand_total": 100}, 30, store.vendor_terms(key))
    assert due.due == dt.date(2026, 3, 17) and due.discount_by == dt.date(2026, 3, 12)


# F0014 by JD Edwards' field names: (code, description, discount, discount days, net days, due date, split payments,
# days to pay aging, proximate month, proximate day). Every code is made up.
F0014_HEAD = ["PNPTC", "PNPTD", "PNDCP", "PNDCD", "NDTP", "PNDDJ", "PNNSP", "PNDTPA", "PNPXDM", "PNPXDD"]
F0014_RULES = (
    ("E", "End of next month", "0", "0", "0", "", "0", "0", "1", "31"),
    ("P", "15th prox", ".00", "0", "0", "", "0", "0", "1", "15"),
    ("M", "End of this month", "", "", "0", "", "", "", "0", "31"),
    ("Y", "Year end", "", "", "0", "12/31/2026", "", "", "", ""),
    ("S", "2% 10, thirds", ".02", "10", "30", "", "3", "30", "", ""),
    ("Q", "15th, 3 months on", "", "", "0", "", "", "", "3", "15"),
    ("R", "1% 10, 25th prox", ".01", "10", "0", "", "", "", "1", "25"),
    ("D", "Fixed, Julian", "", "", "0", "126365", "", "", "", ""),
    ("V", "Halves", "", "", "30", "", "2", "0", "", ""),
)


def test_jd_edwards_proximate_fixed_and_split_terms_are_due_as_jd_edwards_has_them_and_others_are_said(store):
    """B-2834 (D-302): F0014's proximate date, fixed due date and split payments were not read (D-266 read the
    discount and net days only): a code due on the last day of next month, or on a fixed day, has no net days, so it
    read "Due on receipt" and the invoice was due the day it was issued. Now each is due as JD Edwards has it, and a
    rule AP Coder does not work out as JD Edwards does is said, never guessed: a proximate date other than a day of
    the next month or the end of the invoice's month, one with a discount, a fixed due date it cannot read, split
    payments with no days between them, a code not in the table (an advanced payment term, F00141)."""
    from ap_coder.coding.terms import JDE_TERMS_ALIASES, JDE_TERMS_SETTING, jde_meaning, jde_table
    from ap_coder.core.headers import match_columns
    from ap_coder.core.memory import vendor_key
    from ap_coder.web.invoice_totals import due_text

    rows = [dict(zip(F0014_HEAD, row, strict=True)) for row in F0014_RULES]
    table, left = jde_table(rows, match_columns(F0014_HEAD, JDE_TERMS_ALIASES))
    assert left == 0 and table["S"] == {"description": "2% 10, thirds", "net_days": 30, "discount_pct": 2.0,
                                        "discount_days": 10, "splits": 3, "split_days": 30}  # fmt: skip
    words = {code: jde_meaning(code, table) for code in "EPMYS"}
    assert words == {"E": "Net 31st prox", "P": "Net 15th prox", "M": "Due end of month", "Y": "Due 2026-12-31",
                     "S": "2/10 Net 30 in 3 payments 30 days apart"}  # fmt: skip
    assert all(terms_key(said) == said for said in words.values())  # (each reads back as itself)
    issued = {"invoice_date": "2026-01-20", "grand_total": 100.0, "subtotal": 100.0}
    due = {code: payment(issued, 30, said) for code, said in words.items()}
    assert {code: p.due for code, p in due.items()} == {
        "E": dt.date(2026, 2, 28), "P": dt.date(2026, 2, 15), "M": dt.date(2026, 1, 31), "Y": dt.date(2026, 12, 31),
        "S": dt.date(2026, 2, 19)}  # fmt: skip
    assert {p.source for p in due.values()} == {"vendor"}
    assert due["S"].instalments() == [dt.date(2026, 2, 19), dt.date(2026, 3, 21), dt.date(2026, 4, 20)]
    assert due["S"].discount_by == dt.date(2026, 1, 30) and due["E"].instalments() == [dt.date(2026, 2, 28)]
    assert "the first of 3 payments: Feb 19, Mar 21, Apr 20" in due_text(issued, 30, words["S"])
    for code, why in (("Q", "proximate month 3, day 15"), ("R", "a proximate date with a discount"),
                      ("D", "its due date “126365” is not a date AP Coder reads"),
                      ("V", "2 split payments with no days between them"),
                      ("Z", "not in JD Edwards' payment terms loaded (F0014): an advanced payment term's due date "
                            "rules (F00141, F00142) are not read")):  # fmt: skip
        said = jde_meaning(code, table)
        assert said == f"JD Edwards terms code {code}: rule not supported ({why})"
        assert payment(issued, 30, said).source == "default" and parse_terms(said) == Terms()
        found = {c: (severity, words) for severity, c, words in payment_findings(issued, 30, dt.date(2026, 1, 21),
                                                                                 vendor_terms=said)}  # fmt: skip
        assert found["JDE_TERMS_UNSUPPORTED"] == ("info", f"{said}, check the due date: it assumes 30 days "
                                                          "(Settings → Review, Days to pay)")  # fmt: skip
        assert "TERMS_NOT_FOUND" not in found
    assert jde_meaning("Net 45", table) == "Net 45" and jde_meaning("Z", {}) == "Z"  # (words, or no table loaded)
    store.import_vendor_master([{"vendor_name": "Acme Ltd", "erp_id": "41001", "terms": "E", "gst": "",
                                 "status": "active"}])  # fmt: skip
    store.set_setting(JDE_TERMS_SETTING, json.dumps(table))
    assert store.vendor_terms(vendor_key("Acme Ltd")) == "Net 31st prox"


def test_jd_edwards_terms_longer_than_terms_are_read_are_said_not_read_as_none():
    """B-2939: codes of more net days (NDTP 270, a split Net 365), or of a longer or larger discount (2% by day 90),
    than AP Coder reads a term as were passed as worked out, but their words read back as no terms (due in Settings'
    30 days, "Payment terms not found") or without the discount, unsaid. Each is said, as any rule not worked out
    (D-302); the longest that reads back still does."""
    from ap_coder.coding.terms import jde_meaning

    def row(net, pct=0.0, days=0, **more):
        return {"description": "", "net_days": net, "discount_pct": pct, "discount_days": days, **more}

    table = {"N27": row(270), "S36": row(365, splits=2, split_days=30), "D90": row(120, 2.0, 90),
             "D25": row(30, 25.0, 10), "N18": row(180), "D60": row(90, 2.0, 60)}  # fmt: skip
    issued = {"invoice_date": "2026-01-15", "grand_total": 100.0, "subtotal": 100.0}
    for code, why in (("N27", "net 270 days: AP Coder reads 180 at most"),
                      ("S36", "net 365 days: AP Coder reads 180 at most"),
                      ("D90", "a discount for 90 days: AP Coder reads 60 at most"),
                      ("D25", "a discount of 25%: AP Coder reads less than 20%")):  # fmt: skip
        said = jde_meaning(code, table)
        assert said == f"JD Edwards terms code {code}: rule not supported ({why})"
        found = [c for _, c, _ in payment_findings(issued, 30, dt.date(2026, 1, 16), vendor_terms=said)]
        assert "JDE_TERMS_UNSUPPORTED" in found and "TERMS_NOT_FOUND" not in found
    assert payment(issued, 30, jde_meaning("N18", table)).due == dt.date(2026, 7, 14)
    longest = payment(issued, 30, jde_meaning("D60", table))
    assert (longest.due, longest.discount_by) == (dt.date(2026, 4, 15), dt.date(2026, 3, 16))
