"""Payment terms, due dates and early-payment discounts."""

import datetime as dt
import json
import sqlite3

import pytest

from ap_coder.coding.terms import Terms, describe, parse_terms, payment, payment_findings
from ap_coder.core.store import Store

from .conftest import SAMPLE_STEM, SAMPLES


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
