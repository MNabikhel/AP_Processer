"""The fourth sweep's re-check (B-1020 on): the tax rows following the place of supply on screen, the vendor found by
its GST number, the GL posting preview and pilot-score in the G/L tax mode, the stored second-approval reason, one tax
row for one printed tax, the tip after the tax, Ready as bulk approval means it, rates in percent, JDE's Tax ID
column, and the setup pages kept to this computer."""

from __future__ import annotations

from .test_web_invoice import PAGE, check, form_of
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


# --- 3. The GL posting preview in the G/L tax mode -----------------------------------------------------------------


def test_the_posting_preview_shows_the_tax_account_in_the_gl_tax_mode():
    """B-1022: with the tax coded to G/L accounts the preview said "posted by JD Edwards" on the tax line that goes to
    the tax account, and a tax line with no account never said "not mapped"."""
    from ap_coder.erp import jde
    from ap_coder.web.invoice_view import distribution

    from .test_jde_gl_mode import coded, doc, line, tl

    inv = coded(doc([line(1, 1000.0, taxes=["HST"])], [tl("HST", "ON", 0.13, 1000.0, 130.0)]))
    out = inv["final_output"]
    gl = jde.JdeSettings(amount_mode=jde.AMOUNT_GL, default_bu="100")
    assert [r["account"] for r in distribution(out, None, gl)["rows"]] == ["CC100.6010", "100.2310"]
    assert (
        distribution(out, None, jde.JdeSettings(amount_mode=jde.AMOUNT_TAX, default_bu="100"))["rows"][1]["account"]
        == "posted by JD Edwards"
    )  # E1 posts it by its tax AAIs in the other modes
    for e in out["gl_distribution"]:
        if e["kind"] == "tax":
            e["gl_code"] = ""
    assert distribution(out, None, gl)["rows"][1]["account"] == "not mapped"


# --- 4. pilot-score's tax in the G/L tax mode ----------------------------------------------------------------------


def test_pilot_score_compares_the_tax_with_the_tax_account_lines_in_the_gl_tax_mode(tmp_path):
    """B-1023: with the tax coded to G/L accounts, a voucher's F0411 carries RPSTAM 0 (exempt code E), and pilot-score
    compared it with the tax on our G/L lines: a false tax difference on every voucher."""
    from ap_coder.erp import jde
    from ap_coder.ops import pilot_score

    from .test_jde_gl_mode import doc, line, tl
    from .test_pilot_score_sweep4 import _store

    store = _store(tmp_path, doc([line(1, 1000.0, taxes=["HST"])], [tl("HST", "ON", 0.13, 1000.0, 130.0)]))
    (o,) = pilot_score.our_invoices(store, jde.JdeSettings(amount_mode=jde.AMOUNT_GL, default_bu="HQ"))
    o.an8 = "10023"
    voucher = {"RPDOC": "7001", "RPDCT": "PV", "RPKCO": "00001", "RPAN8": "10023", "RPVINV": "A-1",
               "RPDIVJ": str(jde.julian("2026-05-01")), "RPAG": "1130.00", "RPSTAM": "0.00", "RPEXR1": "E"}  # fmt: skip
    gl = [{"GLDOC": "7001", "GLDCT": "PV", "GLKCO": "00001", "GLANI": "CC100.6010", "GLAA": "1000.00"},
          {"GLDOC": "7001", "GLDCT": "PV", "GLKCO": "00001", "GLANI": "HQ.2310", "GLAA": "130.00"}]  # fmt: skip
    posted, notes = pilot_score.posted_vouchers([voucher], gl)
    report = pilot_score.score([o], posted, notes)
    assert report["header"]["tax_total"] == {"n": 1, "right": 1}
    assert not [d for d in report["differences"] if d["field"] == "Tax"]
    # without the G/L export: the tax is not compared, and the report says why
    posted, notes = pilot_score.posted_vouchers([voucher])
    report = pilot_score.score([o], posted, notes)
    assert report["header"]["tax_total"]["n"] == 0
    assert any("F0911" in n and "tax" in n for n in report["notes"])


# --- 5. The stored reason for the second approval -----------------------------------------------------------------


def test_activity_says_the_stored_reason_on_the_second_approval(client, store):
    """B-1024: Activity's "Second approval" line said "(over the approval limit)" for a USD invoice with no rate."""
    from ap_coder.core import audit

    from .test_web_ui_sweep4 import _waiting_for_second

    invoice_id = _waiting_for_second(store, client)
    store.final_approve(invoice_id, "Sam Second", login="ssecond")
    event = next(e for e in store.events(invoice_id) if e["action"] == "final_approved")
    text = audit.describe(event)
    assert "no CAD exchange rate for USD" in text and "over the approval limit" not in text


def test_the_flash_after_a_first_approval_says_the_limit_in_force(client, store):
    """B-1024: a USD invoice whose rate was lowered after it came in still waits (B-970), but the flash said only
    "over the approval limit": the reason was worked out again at today's rate (none)."""
    from .test_web_invoice import clean_invoice, flash_text

    invoice_id = clean_invoice(client, store)
    total = float(store.get_invoice(invoice_id)["ai_output"]["grand_total"])
    store.set_setting("approval_limit", f"{total:.2f}")
    with store._conn() as conn:  # the rate in force when the invoice came in
        conn.execute("INSERT INTO settings (key, value) VALUES ('fx_rates', 'USD=1.37')")
    store.set_setting("fx_rates", "USD=0.05")  # lowered after the invoice came in: the 1.37 still decides
    answer = client.post(f"/review/{invoice_id}/approve", data=form_of(store, invoice_id, currency="USD"),
                         headers=PAGE).json()  # fmt: skip
    assert answer["ok"], answer
    assert store.get_invoice(invoice_id)["status"] == "pending_approval"
    assert f"over the approval limit of {total:,.2f} CAD" in flash_text(client, answer)


# --- 6. One printed tax amount, one tax row ----------------------------------------------------------------------


def test_a_template_that_learned_hst_does_not_add_a_second_tax_to_a_gst_page():
    """B-1025: an autonomous supplier's template learned HST; a page printing "G.S.T. @ 5%" was read as both GST 5%
    (the label) and HST 5% (the template, on the sales tax total's line): three math errors for one printed tax."""
    from ap_coder.capture.confidence import fuse
    from ap_coder.capture.types import MISSING, Box, Reading

    def r(field, value, y, method):
        return Reading(field, value, f"${value}", [Box(1, 0.355, y, 0.396, y + 0.015)], 0.9, method)

    rules = {"subtotal": [r("subtotal", "515.64", 0.417, "label")], "gst_amount": [r("gst_amount", "25.78", 0.438,
             "label")], "tax_total": [r("tax_total", "25.78", 0.459, "label")],
             "grand_total": [r("grand_total", "541.42", 0.478, "label")]}  # fmt: skip
    template = {"subtotal": [r("subtotal", "515.64", 0.417, "template")],
                "hst_amount": [r("hst_amount", "25.78", 0.459, "template")],
                "tax_total": [r("tax_total", "25.78", 0.459, "template")],
                "grand_total": [r("grand_total", "541.42", 0.478, "template")]}  # fmt: skip
    fields, checks = fuse({"rules": rules, "template": template}, [])
    assert fields["gst_amount"].value == 25.78
    assert fields["hst_amount"].status == MISSING
    assert not [c for c in checks if not c["ok"] and c["code"].startswith("TOTALS")]
    # two taxes of the same amount printed on their own lines, adding up: both kept
    rules["hst_amount"] = [r("hst_amount", "25.78", 0.448, "label")]
    rules["tax_total"] = [r("tax_total", "51.56", 0.459, "label")]
    rules["grand_total"] = [r("grand_total", "567.20", 0.478, "label")]
    fields, _ = fuse({"rules": rules}, [])
    assert fields["gst_amount"].value == fields["hst_amount"].value == 25.78


# --- 7. A tip after the tax: the printed subtotal is right -----------------------------------------------------------


def test_a_gratuity_after_the_tax_does_not_fail_the_printed_subtotal():
    """B-1026: with the tip line added (B-931) the screen still warned "As printed, subtotal 820.00 + tax 106.60 ≠
    total 1,046.60" and the subtotal showed "Failed check": the difference is the untaxed gratuity read after the
    tax."""
    from ap_coder.capture.bridge import review_issues
    from ap_coder.capture.confidence import fuse
    from ap_coder.capture.types import LIKELY, VERIFIED, Box, CaptureResult, Reading

    from .test_money_sweep4 import BISTRO

    def r(field, value, y):
        return Reading(field, value, value, [Box(1, 0.7, y, 0.8, y + 0.015)], 0.9, "label")

    rules = {"subtotal": [r("subtotal", "820.00", 0.50)], "hst_amount": [r("hst_amount", "106.60", 0.52)],
             "tax_total": [r("tax_total", "106.60", 0.52)],
             "grand_total": [r("grand_total", "1046.60", 0.56)]}  # fmt: skip
    fields, checks = fuse({"rules": rules}, [], text=BISTRO)
    (totals,) = [c for c in checks if c["code"] == "TOTALS_ADD_UP"]
    assert totals["ok"] and "gratuity 120.00" in totals["detail"].lower()
    assert fields["subtotal"].status in (VERIFIED, LIKELY), fields["subtotal"]
    assert not [x for x in fields["subtotal"].reasons if x.startswith("check failed")]
    assert not [i for i in review_issues(CaptureResult(fields=fields, checks=checks)) if i[1] == "CAPTURE_TOTALS"]
    # without the tip printed after the tax: the difference stays a difference
    _, checks = fuse({"rules": rules}, [], text=BISTRO.replace("Gratuity\n120.00\n", ""))
    assert not next(c for c in checks if c["code"] == "TOTALS_ADD_UP")["ok"]


# --- 8. Ready means bulk approval takes it -------------------------------------------------------------------------


def test_an_invoice_with_a_warning_bulk_approval_refuses_is_not_ready(client, store):
    """B-1027: HST 15% in Ontario, or no GST number: bulk approval and touchless refuse it (D-95), but the queue said
    Ready."""
    from ap_coder.web.pages.review import attention

    from .test_web_invoice import clean_invoice

    invoice_id = clean_invoice(client, store)
    store.refresh_confidence(invoice_id, 0.99, False)  # not flagged by its checks
    pending = [i for i in store.list_invoices("review") if i["id"] == invoice_id]
    reference = store.reference_data()
    assert invoice_id not in attention(store, reference, pending, today={invoice_id: []})
    warned = [{"severity": "warning", "code": "TAX_RATE_MISMATCH", "line": None,
               "message": "HST charged at 15% but the official rate in ON is 13%"}]  # fmt: skip
    reasons = attention(store, reference, pending, today={invoice_id: warned})
    assert "warning" in reasons[invoice_id].lower()
    always = [{"severity": "info", "code": "VENDOR_BANK_CHANGED", "line": None, "message": "bank changed"}]
    assert invoice_id in attention(store, reference, pending, today={invoice_id: always})
    assert invoice_id not in attention(
        store,
        reference,
        pending,
        today={
            invoice_id: [
                {"severity": "info", "code": "TERMS_FROM_VENDOR", "line": None, "message": "terms from the vendor list"}
            ]
        },
    )


# --- 9. Rates in percent -----------------------------------------------------------------------------------------


def test_the_tax_checks_say_rates_in_percent():
    """B-1028: bulk approval's reason read "HST ON rate 0.15 differs from the official 0.13"."""
    from ap_coder.coding.tax import TaxRateTable, TaxSetup, check_taxes, load_tax_mapping
    from ap_coder.core.schema import InvoiceCoding

    from .conftest import DATA
    from .test_jde_gl_mode import doc, line, tl

    setup = TaxSetup(TaxRateTable.load(DATA / "canada_tax_rates.csv"), load_tax_mapping(DATA / "tax_gl_mapping.csv"))
    coding = InvoiceCoding.model_validate(doc([line(1, 1000.0, taxes=["HST"])], [tl("HST", "ON", 0.15, 1000.0, 140.0)]))
    messages = {f.code: f.message for f in check_taxes(coding, setup)}
    assert "rate 15% differs from the official 13%" in messages["TAX_RATE_NONSTANDARD"]
    assert "1000.00 x 15% = 150.00" in messages["TAX_CALC_MISMATCH"]
    assert "0.15" not in " ".join(messages.values()) and "0.13" not in " ".join(messages.values())


# --- 10. JD Edwards' "Tax ID" column -------------------------------------------------------------------------------


def test_the_vendor_master_reads_jd_edwards_tax_id_as_the_gst_number():
    """B-1029: JD Edwards' address book exports the GST/HST number as "Tax ID" (TAX); the import left it out."""
    from ap_coder.coding.vendors import master_columns, master_rows

    records = [{"Alpha Name": "Great Lakes Mechanical Limited", "Address Number": "40120", "Tax ID": LTD}]
    columns = master_columns(list(records[0]))
    assert columns["gst"] == "Tax ID"
    assert master_rows(records, columns)[0]["gst"] == LTD
