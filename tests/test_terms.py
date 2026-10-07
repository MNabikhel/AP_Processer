"""Payment terms, due dates and early-payment discounts."""

import datetime as dt
import json
import sqlite3

import pytest

from ap_coder.store import Store
from ap_coder.terms import Terms, describe, parse_terms, payment, payment_findings

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
        ("Overdue accounts are charged 2% interest per month", Terms()),
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
    from ap_coder.webapp.review import sort_queue  # imported here: the page modules read AP_DB_PATH on import

    rows = [{"id": 1, "due_date": "2026-11-01"}, {"id": 2, "due_date": None}, {"id": 3, "due_date": "2026-10-01"}]
    assert [r["id"] for r in sort_queue(rows, "Due date: soonest")] == [3, 1, 2]
