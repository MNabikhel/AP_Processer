"""pilot-score compares what AP Coder sent to JD Edwards (B-933, B-934): a credit memo's due date (its invoice date),
no due date when none was sent, and the tax JD Edwards was given (the claimable tax, or the tax on its G/L lines)."""

from ap_coder.coding.tax import TaxRateTable, TaxSetup, load_tax_mapping
from ap_coder.core.store import Store
from ap_coder.erp import jde
from ap_coder.ops import pilot_score
from tests.conftest import DATA
from tests.erp.test_jde_gl_mode import coded, doc, line, tl


def _store(tmp_path, *docs, setup=None):
    store = Store(tmp_path / "t.db")
    for n, d in enumerate(docs):
        inv = coded(d, setup)
        i = store.add_invoice(tmp_path / f"{n}.pdf", inv["final_output"], {})
        store.approve_invoice(i, inv["final_output"], "Jane")
    return store


def test_a_credit_memo_due_date_is_the_invoice_date_sent(tmp_path):
    credit = doc([line(1, -100.0, taxes=["HST"])], [tl("HST", "ON", 0.13, -100.0, -13.0)], invoice_number="CN-1")
    (o,) = pilot_score.our_invoices(_store(tmp_path, credit), jde.JdeSettings(amount_mode=jde.AMOUNT_TAX))
    assert o.due_date == "2026-05-01"


def test_no_due_date_sent_is_not_compared(tmp_path):
    guessed = doc([line(1, 100.0, taxes=["HST"])], [tl("HST", "ON", 0.13, 100.0, 13.0)], payment_terms="")
    (o,) = pilot_score.our_invoices(_store(tmp_path, guessed), jde.JdeSettings(amount_mode=jde.AMOUNT_TAX))
    assert o.due_date == ""
    row = {"RPAN8": "10023", "RPVINV": "A-1", "RPDIVJ": str(jde.julian("2026-05-01")), "RPAG": "113.00",
           "RPSTAM": "13.00", "RPCRCD": "CAD", "RPDDJ": str(jde.julian("2026-05-31"))}  # fmt: skip
    posted, _ = pilot_score.posted_vouchers([row])
    o.an8 = "10023"
    report = pilot_score.score([o], posted)
    assert report["header"]["due_date"]["n"] == 0


def test_the_tax_compared_is_what_jd_edwards_was_given(tmp_path):
    setup = TaxSetup(TaxRateTable.load(DATA / "canada_tax_rates.csv"), load_tax_mapping(DATA / "tax_gl_mapping.csv"),
                     meals_gl_codes=frozenset({"6320"}))  # fmt: skip
    meal = doc([line(1, 820.0, gl="6320", taxes=["HST"])], [tl("HST", "ON", 0.13, 820.0, 106.6)])
    store = _store(tmp_path, meal, setup=setup)
    (tax_mode,) = pilot_score.our_invoices(store, jde.JdeSettings(amount_mode=jde.AMOUNT_TAX))
    assert tax_mode.tax_total == 53.3  # VLSTAM: the claimable half
    (gl_mode,) = pilot_score.our_invoices(store, jde.JdeSettings(amount_mode=jde.AMOUNT_GL, default_bu="HQ"))
    assert gl_mode.tax_total == 53.3  # the GL tax line
    (gross,) = pilot_score.our_invoices(store, jde.JdeSettings(amount_mode=jde.AMOUNT_GROSS))
    assert gross.tax_total == 106.6  # E1 works it out: the tax charged
