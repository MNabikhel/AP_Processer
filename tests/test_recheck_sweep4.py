"""The fourth sweep's re-check (B-1020 on): the tax rows following the place of supply on screen, the vendor found by
its GST number, the GL posting preview and pilot-score in the G/L tax mode, the stored second-approval reason, one tax
row for one printed tax, the tip after the tax, Ready as bulk approval means it, rates in percent, JDE's Tax ID
column, and the setup pages kept to this computer."""

from __future__ import annotations

from .test_web_invoice import check, form_of
from .test_web_review_signoff import _id, _review, browser, client, demo_db, me, store

__all__ = ["browser", "client", "demo_db", "me", "store"]  # the fixtures, shared


# --- 1. The tax rows follow the place of supply on screen ----------------------------------------------------------


def test_the_check_sends_back_the_tax_rows_moved_to_the_new_place_of_supply(client, store):
    invoice_id = _id(store, "Pacific")
    answer = check(client, invoice_id, form_of(store, invoice_id, ship_to_province="MB"))
    assert [t["province"] for t in answer["taxes"]] == ["", "MB"]  # PST BC 7% is Manitoba's RST 7% now


def test_in_a_browser_the_tax_row_follows_the_place_of_supply_there_and_back(browser, store):
    """B-1020: the tax row kept saying PST BC once the place of supply was MB (PST MB after a reload), and back to BC
    the page posted PST MB again, so the invoice was saved with PST MB on a BC supply."""
    page = _review(browser, store, "Pacific")
    province = page.locator('[data-grid="taxes"] tr').nth(1).locator("[name=tax_province]")
    assert province.input_value() == "BC"
    details = page.locator(".inv-supply-pick")
    if not details.evaluate("d => d.open"):
        details.locator("summary").click()
    with page.expect_response(lambda r: r.url.endswith("/check")):
        page.select_option("[name=ship_to_province]", "MB")
    page.wait_for_function("""() => document.querySelectorAll('[data-grid="taxes"] [name=tax_province]')[1].value
        === 'MB'""")
    with page.expect_response(lambda r: r.url.endswith("/check")):
        page.select_option("[name=ship_to_province]", "BC")
    page.wait_for_function("""() => document.querySelectorAll('[data-grid="taxes"] [name=tax_province]')[1].value
        === 'BC'""")
    assert not page.errors


# --- 2. Two vendors whose names read the same: each found by its own GST number ------------------------------------

INC, LTD = "994310787RT0001", "561646050RT0003"


def _two_great_lakes(tmp_path):
    from ap_coder.coding.vendors import master_columns, master_rows
    from ap_coder.core.store import Store

    store = Store(tmp_path / "t.db")
    records = [
        {"Name": "Great Lakes Mechanical Inc.", "AN8": "40110", "GST/HST Number": INC},
        {"Name": "Great Lakes Mechanical Limited", "AN8": "40120", "GST/HST Number": LTD},
    ]
    store.import_vendor_master(master_rows(records, master_columns(list(records[0]))))
    return store


def _coding(name, gst, number="GL-1"):
    from ap_coder.core.schema import InvoiceCoding

    from .webclient import gt

    return InvoiceCoding.model_validate({**gt(), "vendor_name": name, "gst_hst_registration_number": gst,
                                         "invoice_number": number})  # fmt: skip


def test_the_limited_is_checked_against_its_own_gst_number_not_the_incs(tmp_path):
    """B-1021: Great Lakes Mechanical Limited's invoice warned its GST number differed from the vendor list's (Inc.'s
    number), was told the Inc.'s hold, and learned from the Inc.'s invoices: both were looked up by the name alone."""
    from ap_coder.coding.vendors import expected_tax_numbers, vendor_findings

    store = _two_great_lakes(tmp_path)
    assert store.vendor_key_for("Great Lakes Mechanical Inc.", INC) == "great lakes mechanical"  # the usual key
    assert store.vendor_key_for("Great Lakes Mechanical Inc.", "") == "great lakes mechanical"
    assert store.vendor_key_for("Great Lakes Mechanical Limited", LTD) == "great lakes mechanical #40120"
    limited = _coding("Great Lakes Mechanical Limited", LTD)
    codes = {c for _s, c, _m in vendor_findings(limited, store)}
    assert "VENDOR_TAX_NUMBER_CHANGED" not in codes and "VENDOR_NOT_IN_MASTER" not in codes
    assert expected_tax_numbers(limited, store)["gst_hst_registration_number"] == f"Vendor list: {LTD}"
    # the Inc. on hold: the Limited is not
    inc_row = store.get_vendor("great lakes mechanical")
    store.save_vendor("great lakes mechanical", inc_row["display_name"], status="on_hold", expected_gst=INC)
    assert "VENDOR_ON_HOLD" not in {c for _s, c, _m in vendor_findings(limited, store)}
    assert "VENDOR_ON_HOLD" in {c for _s, c, _m in vendor_findings(_coding("Great Lakes Mechanical Inc.", INC), store)}


def test_the_limiteds_invoices_are_its_own_history(tmp_path):
    from .webclient import add_clean, gt

    store = _two_great_lakes(tmp_path)
    inc = add_clean(store, vendor_name="Great Lakes Mechanical Inc.", gst_hst_registration_number=INC)
    ltd = add_clean(store, vendor_name="Great Lakes Mechanical Limited", gst_hst_registration_number=LTD,
                    invoice_number="L-1")  # fmt: skip
    assert store.get_invoice(inc)["vendor_key"] == "great lakes mechanical"
    assert store.get_invoice(ltd)["vendor_key"] == "great lakes mechanical #40120"
    store.approve_invoice(ltd, {**gt(), "vendor_name": "Great Lakes Mechanical Limited",
                                "gst_hst_registration_number": LTD, "invoice_number": "L-1"}, "Ann")  # fmt: skip
    assert store.get_invoice(ltd)["vendor_key"] == "great lakes mechanical #40120"
    keys = {r["vendor_key"] for r in store.feedback_rows()}
    assert keys == {"great lakes mechanical #40120"}  # what AP approved is the Limited's to learn from
    assert [h["id"] for h in store.vendor_invoices("great lakes mechanical")] == [inc]
    # what an invoice of each is coded and checked with: its own lessons
    key = store.vendor_key_for("Great Lakes Mechanical Limited", LTD)
    assert store.feedback_rows(vendor_name="Great Lakes Mechanical Limited", key=key)
    assert not store.feedback_rows(vendor_name="Great Lakes Mechanical Inc.")
