"""Problems found by the pilot sweeps (B-650 onwards): what counts as a correction, the place of supply with no
ship-to, amounts without a leading zero, stale pages, payment terms, French headers and more."""

import copy
import sqlite3

from ap_coder.core.memory import ACCEPTED, CODED, CORRECTED
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import Store

# --- B-650: filling in a blank GL account is the reviewer's coding, not a correction ----------------------------------


def _blank_invoice():
    line = {"line_number": 1, "description": "Consulting", "quantity": 1, "unit_price": 100.0, "amount": 100.0,
            "predicted_gl_code": "UNASSIGNED", "predicted_cost_center": "", "taxes_applied": [],
            "reasoning_justification": "No earlier approval to learn from: pick the account."}  # fmt: skip
    return {"vendor_name": "Acme Ltd", "invoice_number": "1", "invoice_date": "2026-10-01", "currency": "CAD",
            "subtotal": 100.0, "tax_lines": [], "tax_total": 0.0, "grand_total": 100.0, "confidence_score": 0.9,
            "line_items": [line]}  # fmt: skip


def test_filling_in_a_blank_gl_account_is_coded_not_corrected(tmp_path):
    from ap_coder.ai import lessons
    from ap_coder.web.invoice_view import ai_changes, learn_text

    store = Store(tmp_path / "ap.db")
    read = _blank_invoice()
    iid = store.add_invoice(tmp_path / "x.pdf", read, {"requires_review": True})
    final = copy.deepcopy(read)
    final["line_items"][0]["predicted_gl_code"] = "6110"
    coding = InvoiceCoding.model_validate(final)
    # The review screen, before approving: nothing changed, one line the reviewer coded
    assert ai_changes(coding, read) == 0
    assert learn_text(coding, read) == "Teaches from 1 line: 1 you coded"
    # The lessons: a blank filled in is no correction (no "why" question for it)
    assert [r["corrected"] for r in lessons.outcomes(read, final) if r["field"] == lessons.LINE_GL] == [False]
    assert store.approve_invoice(iid, final, "pat") == {ACCEPTED: 0, CORRECTED: 0, CODED: 1}
    m = store.metrics()
    assert m["lines_corrected"] == 0 and m["lines_measured"] == 0 and m["by_vendor"] == []
    (event,) = [e for e in store.events() if e["action"] == "approved"]
    assert event["detail"]["corrected"] == 0 and event["detail"]["coded"] == 1
    inv = store.get_invoice(iid)
    assert "line_coding" not in inv["edits"]


def test_a_filled_in_blank_still_teaches_the_learning_memory(tmp_path):
    """The memory learns the account from a coded line: a recode made by filling in a blank line, then confirmed,
    still moves the memory away from the older account (as when the blank was recorded as a correction, B-605)."""
    from ap_coder.core.memory import select_examples, vendor_key

    def row(gl, outcome, suggested):
        return {"vendor_key": vendor_key("Northwind"), "vendor_name": "Northwind", "description": "Laptop",
                "final_gl": gl, "final_cc": "", "suggested_gl": suggested, "outcome": outcome,
                "created_at": "2026-10-01T10:00:00"}  # fmt: skip

    rows = [row("1500", ACCEPTED, "1500"), row("1500", CODED, "UNASSIGNED")]
    rows += [row("6010", ACCEPTED, "6010") for _ in range(12)]
    assert [(e.gl_code, e.decisions) for e in select_examples(rows, "Northwind invoice")] == [("1500", 2)]


def test_blanks_an_older_version_recorded_as_corrections_are_recounted(tmp_path):
    path = tmp_path / "ap.db"
    store = Store(path)
    read = _blank_invoice()
    iid = store.add_invoice(tmp_path / "x.pdf", read, {"requires_review": True})
    final = copy.deepcopy(read)
    final["line_items"][0]["predicted_gl_code"] = "6110"
    store.approve_invoice(iid, final, "pat")
    with sqlite3.connect(path) as conn:  # as an older version recorded it
        conn.execute("UPDATE feedback SET outcome = 'corrected'")
    assert Store(path).metrics()["lines_corrected"] == 0


# --- B-651: the vendor's province is never the ship-to (D-61) ---------------------------------------------------------

PRAIRIE = ("Prairie Print Ltd.\n100 Main St SW, Calgary AB T2P 1J9\nGST Reg. 123456782 RT0001\n\nINVOICE\n"
           "Invoice # PP-77\nInvoice Date: October 1, 2026\nTerms: Net 30\n\nDescription        Amount\n"
           "Business cards, 500    100.00\n\nSubtotal    100.00\nGST 5%    5.00\nTotal    105.00\n")  # fmt: skip


def test_the_vendors_province_is_never_the_ship_to():
    from types import SimpleNamespace

    from ap_coder.ai.offline_coder import addresses, provinces
    from ap_coder.coding.tax import supply_source

    assert provinces(PRAIRIE) == ("AB", "")
    coding = SimpleNamespace(ship_to_province=provinces(PRAIRIE)[1], supplier_province="AB", line_items=[])
    assert supply_source(coding, SimpleNamespace(site_provinces={}, our_province="ON")) == ("ON", "ours")
    # A bill-to is our address, as the customer: the place of supply when no ship-to is printed; a ship-to wins
    billed = "Acme Supply\n1 Main St, Toronto ON M5V 1A1\nBill to:\nHalifax NS B3H 1A1\n"
    assert provinces(billed) == ("ON", "NS") and addresses(billed) == ("ON", "", "NS")
    shipped = billed + "Ship to:\nCalgary AB T2P 1A1\n"
    assert provinces(shipped) == ("ON", "AB") and addresses(shipped) == ("ON", "AB", "NS")


def test_the_screen_never_calls_a_bill_to_the_ship_to():
    from types import SimpleNamespace

    from ap_coder.web.invoice_view import supply_line

    setup = SimpleNamespace(site_provinces={}, our_province="ON")
    why = supply_line({"ship_to_province": "NS", "supplier_province": "ON"}, [], setup)["why"]
    assert "ship-to on the invoice" not in why and "bill-to" in why
    assert supply_line({"ship_to_province": "", "supplier_province": "AB"}, [], setup)["why"] == (
        "your province (Settings): no ship-to or bill-to printed"
    )


def test_with_no_ship_to_printed_the_tax_check_says_the_province_is_ours():
    from ap_coder.coding.tax import TaxSetup, check_taxes
    from ap_coder.core.schema import TaxLine

    coding = InvoiceCoding.model_validate({**_blank_invoice(), "supplier_province": "AB", "subtotal": 100.0,
                                           "tax_total": 5.0, "grand_total": 105.0})  # fmt: skip
    coding.tax_lines = [TaxLine(tax_type="GST", province="", rate=0.05, taxable_amount=100.0, tax_amount=5.0)]
    setup = TaxSetup.default()
    setup.our_province = "ON"
    messages = [f.message for f in check_taxes(coding, setup) if f.code == "TAX_REGIME_MISMATCH"]
    assert messages and all("your province in Settings: no ship-to or bill-to printed" in m for m in messages), messages


# --- B-652: amounts printed without a leading zero --------------------------------------------------------------------


def test_amounts_without_a_leading_zero():
    from ap_coder.capture.normalize import find_amounts, parse_amount

    assert parse_amount("$.99") == 0.99 and parse_amount(".65") == 0.65 and parse_amount("-.50") == -0.50
    assert parse_amount("(.50)") == -0.50 and parse_amount("$.65 CR") == -0.65
    assert [v for v, _, _ in find_amounts("HST 13%                $.65")] == [13.0, 0.65]
    assert [v for v, _, _ in find_amounts("Total ........ 65.00")] == [65.0]
    assert [v for v, _, _ in find_amounts("Invoice No.12 Ver.25")] == [12.0, 25.0]  # a number after a word's point
    assert parse_amount("1,234.56") == 1234.56 and parse_amount("0.65") == 0.65  # unchanged


def test_a_receipt_with_amounts_without_a_leading_zero_reads_them(tmp_path):
    from ap_coder.capture import analyze

    text = ("Northern Pet Supply Ltd.\n12 King St W, Toronto ON M5H 1A1\nGST/HST 123456782 RT0001\nINVOICE\n"
            "Invoice # 55013\nDate: October 1, 2026\n\nDescription            Amount\nDog chew               $.99\n\n"
            "Subtotal               $.99\nHST 13%                $.13\nTotal                  $1.12\n")  # fmt: skip
    path = tmp_path / "leading_point.txt"
    path.write_text(text)
    r = analyze(str(path))
    assert r.fields["subtotal"].value == 0.99 and r.fields["hst_amount"].value == 0.13
    assert r.fields["grand_total"].value == 1.12 and r.fields["subtotal"].status == "verified"


# --- B-653: Settings → Reading said OvisOCR2 had read everything while invoices still waited --------------------------


def test_the_reading_tab_says_what_still_waits_for_ovisocr2(tmp_path):
    from types import SimpleNamespace

    from ap_coder.ai.reading import _queue
    from ap_coder.web.pages.settings_reading import queue_text

    store = Store(tmp_path / "ap.db")
    iid = store.add_invoice(tmp_path / "x.pdf", _blank_invoice(), {"requires_review": True})
    store.queue_page_read(iid, "test")
    final = copy.deepcopy(_blank_invoice())
    final["line_items"][0]["predicted_gl_code"] = "6110"
    store.approve_invoice(iid, final, "pat")
    status = SimpleNamespace(page_reader_model="", queue=0, queue_closed=0, eta_minutes=None, figures_count=0,
                             figures_agree=None)  # fmt: skip
    _queue(None, store, status)
    assert (status.queue, status.queue_closed) == (0, 1)
    bold, rest = queue_text(status)
    assert "read every invoice" not in bold + rest
    assert bold + rest == ("No invoice to review waits for OvisOCR2. It still has 1 approved or rejected invoice to "
                           "read, to check its reading against what AP approved: nothing waits for you.")  # fmt: skip
    status.queue_closed = 0
    assert queue_text(status) == ("", "Nothing waiting for OvisOCR2.")


# --- B-654: a final approval clicked on a stale page approved another version -----------------------------------------


def test_a_final_approval_from_a_stale_page_is_refused(tmp_path, monkeypatch):
    import json
    import re

    from ap_coder.core.store import APPROVED, PENDING, load_sample_setup

    from .conftest import SAMPLE_STEM, SAMPLES
    from .webclient import client_for, post

    monkeypatch.setenv("AP_REVIEWER", "Sam")
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    store.set_setting("approval_limit", "10000")
    gt = json.loads((SAMPLES / "ground_truth" / f"{SAMPLE_STEM}.json").read_text())
    iid = store.add_invoice(tmp_path / "a.pdf", gt, {})
    store.approve_invoice(iid, gt, "Jane")
    client = client_for(store)

    def shown(page):
        form = re.search(rf'action="/review/{iid}/final-approve">(.*?)</form>', page, re.S).group(1)
        return dict(re.findall(r'<input type="hidden" name="([^"]+)" value="([^"]*)"', form))

    queue_tab, invoice_page = shown(client.get("/review?tab=second").text), shown(client.get(f"/review/{iid}").text)
    first = next(e for e in store.events(iid) if e["action"] == "approved")
    assert queue_tab == invoice_page == {"approval": str(first["id"])}
    # Meanwhile Jane has it sent back, changes the bank account and approves again
    store.send_back(iid, "Jane", "fix")
    store.approve_invoice(iid, {**gt, "remit_bank_account": "00099-001-7654321"}, "Jane")
    answer = post(client, f"/review/{iid}/final-approve", queue_tab).json()
    assert store.get_invoice(iid)["status"] == PENDING
    message = client.get(answer["redirect"]).text
    assert "approved again by Jane since you opened this page" in message and "reload" in message.lower()
    # The page drawn now gives it
    post(client, f"/review/{iid}/final-approve", shown(client.get("/review?tab=second").text))
    assert store.get_invoice(iid)["status"] == APPROVED


# --- B-655: a stale tab saved over what another tab changed -----------------------------------------------------------

STALE = "This page was changed elsewhere since you opened it: reload to see the latest, then make your change again."


def _web(tmp_path, monkeypatch):
    from ap_coder.core.store import load_sample_setup

    from .webclient import client_for

    monkeypatch.setenv("AP_REVIEWER", "Sam")
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    return store, client_for(store)


def _form(page, action):
    import re

    form = re.search(rf'<form[^>]*action="{re.escape(action)}"[^>]*>(.*?)</form>', page, re.S).group(1)
    data = {}
    for tag in re.findall(r"<input[^>]*>", form):
        name, value = re.search(r'name="([^"]+)"', tag), re.search(r'value="([^"]*)"', tag)
        if name and 'type="checkbox"' not in tag and 'type="file"' not in tag:
            data.setdefault(name.group(1), []).append(value.group(1) if value else "")
    for name, body in re.findall(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', form, re.S):
        chosen = re.search(r'<option value="([^"]*)"[^>]*selected', body)
        data.setdefault(name, []).append(chosen.group(1) if chosen else "")
    for name, body in re.findall(r'<textarea[^>]*name="([^"]+)"[^>]*>(.*?)</textarea>', form, re.S):
        data.setdefault(name, []).append(body)
    return data


def test_a_stale_settings_tab_does_not_reset_the_approval_limit(tmp_path, monkeypatch):
    from .webclient import post

    store, client = _web(tmp_path, monkeypatch)
    stale = _form(client.get("/settings/review").text, "/settings/review")
    assert stale.get("version")
    assert post(client, "/settings/review", {"approval_limit": "5000"}).json()["ok"]  # another tab, or a colleague
    stale["terms_days"] = ["45"]
    answer = post(client, "/settings/review", stale)
    assert answer.status_code == 409 and answer.json()["error"] == STALE
    assert store.approval_limit() == 5000 and store.default_terms_days() == 30
    fresh = _form(client.get("/settings/review").text, "/settings/review")
    fresh["terms_days"] = ["45"]
    assert post(client, "/settings/review", fresh).json()["ok"]
    assert store.approval_limit() == 5000 and store.default_terms_days() == 45


def test_a_stale_gl_grid_does_not_delete_an_account_imported_meanwhile(tmp_path, monkeypatch):
    from .webclient import post

    store, client = _web(tmp_path, monkeypatch)
    stale = _form(client.get("/accounts?tab=gl").text, "/accounts/gl_accounts/save")
    store.import_accounts("gl_accounts", [{"c": "7777", "d": "New account from ERP"}], "c", "d")
    stale["description"][0] = "edited"
    answer = post(client, "/accounts/gl_accounts/save", stale)
    assert answer.status_code == 409 and answer.json()["error"] == STALE
    assert "7777" in {a["code"] for a in store.list_accounts("gl_accounts")}
    fresh = _form(client.get("/accounts?tab=gl").text, "/accounts/gl_accounts/save")
    fresh["description"][0] = "edited"
    assert post(client, "/accounts/gl_accounts/save", fresh).json()["ok"]
    assert "7777" in {a["code"] for a in store.list_accounts("gl_accounts")}


def test_a_stale_rules_tab_does_not_drop_a_rule_added_meanwhile(tmp_path, monkeypatch):
    from .webclient import post

    store, client = _web(tmp_path, monkeypatch)
    post(client, "/accounts/rules", {"vendor": ["Purolator"], "contains": [""], "gl_code": ["6000"],
                                     "cost_center": [""]})  # fmt: skip
    stale = _form(client.get("/accounts?tab=rules").text, "/accounts/rules")
    assert post(client, "/accounts/rules/add", {"vendor": "Bell Canada", "gl_code": "6420"}).json()["ok"]
    stale["gl_code"] = ["6010" if v == "6000" else v for v in stale["gl_code"]]
    answer = post(client, "/accounts/rules", stale)
    assert answer.status_code == 409 and answer.json()["error"] == STALE
    assert sorted(r.vendor for r in store.coding_rules()) == ["Bell Canada", "Purolator"]


# --- B-656: payment terms "30 EOM", "EOM 30", "Net: 45", "45 net" (D-62) ----------------------------------------------


def test_payment_terms_written_other_ways():
    import datetime as dt

    from ap_coder.coding.terms import payment

    def due(terms):
        return payment({"invoice_date": "2026-01-15", "grand_total": 100.0, "payment_terms": terms}, 30).due

    # EOM terms count the days from the end of the invoice's month (Jan 31)
    eom = [("30 EOM", "2026-03-02"), ("10 EOM", "2026-02-10"), ("EOM 30", "2026-03-02"), ("Net 30 EOM", "2026-03-02"),
           ("30 days EOM", "2026-03-02"), ("EOM", "2026-01-31"), ("Net EOM", "2026-01-31"),
           ("EOM net 30", "2026-03-02"), ("Net 10 EOM", "2026-02-10")]  # fmt: skip
    for terms, day in eom:
        assert due(terms) == dt.date.fromisoformat(day), terms
    for terms in ("Net: 45", "Net - 45", "NET-45", "45 net", "45 days net", "Net 45", "net45", "N45"):
        assert due(terms) == dt.date(2026, 3, 1), terms
    assert payment({"invoice_date": "2026-01-15", "grand_total": 100.0, "payment_terms": "2/10 Net: 30"}).terms \
        .discount_days == 10  # fmt: skip


# --- B-657: French column headers with accents ------------------------------------------------------------------------


def test_french_headers_with_accents_are_recognised():
    from ap_coder.coding import po, vendors
    from ap_coder.erp import registers, statements

    assert po.map_columns(["Bon de commande", "Fournisseur", "Reçu", "Prix unitaire"])["received_quantity"] == "Reçu"
    master = vendors.master_columns(["Fournisseur", "Numéro", "Numéro TPS", "Numéro TVQ", "Statut"])
    assert (master["erp_id"], master["gst"], master["qst"]) == ("Numéro", "Numéro TPS", "Numéro TVQ")
    assert statements.map_columns(["Facture", "Date", "Montant", "Crédit", "Solde"])["credit"] == "Crédit"
    assert registers.map_columns(["Fournisseur", "Facture", "Montant"])["vendor_name"] == "Fournisseur"
    from ap_coder.core.headers import header_key

    assert header_key("Numéro de facture") == "numerodefacture" and header_key("Œuvre N°") == "oeuvreno"


# --- B-658: a coding rule applied on the review screen could drop the AI reviewer's check -----------------------------


def test_rules_applied_on_screen_never_drop_a_background_check(tmp_path, monkeypatch):
    import json as _json
    import threading

    import ap_coder.core.store as store_module

    store = Store(tmp_path / "ap.db")
    iid = store.add_invoice(tmp_path / "x.pdf", _blank_invoice(), {"requires_review": True})
    paused, go = threading.Event(), threading.Event()

    class SlowJson:  # the screen's write pauses between its read and its write
        def __getattr__(self, name):
            return getattr(_json, name)

        def dumps(self, obj, *a, **k):
            if threading.current_thread().name == "screen" and isinstance(obj, dict) and "rules_applied" in obj:
                paused.set()
                go.wait(1)
            return _json.dumps(obj, *a, **k)

    monkeypatch.setattr(store_module, "json", SlowJson())
    change = {"line_number": 1, "rule": "r", "gl_from": "6000", "gl_to": "6010", "cc_from": "", "cc_to": ""}
    screen = threading.Thread(target=store.add_rules_applied, args=(iid, [change]), name="screen")
    screen.start()
    paused.wait(5)
    store.update_after_check(iid, None, None, ("X",), {"ai_reviewer": {"verdict": "checked"}})
    go.set()
    screen.join()
    meta = store.get_invoice(iid)["meta"]
    assert "ai_reviewer" in meta and "rules_applied" in meta


# --- B-659: extreme dates in the address gave a server error ----------------------------------------------------------


def test_extreme_dates_in_the_address_are_clamped(tmp_path, monkeypatch):
    store, client = _web(tmp_path, monkeypatch)
    store.add_invoice(tmp_path / "x.pdf", _blank_invoice(), {"requires_review": True})
    for url in ("/month-end?end=0001-01-01", "/month-end?end=9999-12-31", "/month-end/accruals.csv?end=0001-01-01",
                "/month-end/accruals.csv?end=9999-12-31", "/activity/controls-report?start=9999-12-31",
                "/activity/controls-report?start=0001-01-01&end=0001-01-01"):  # fmt: skip
        assert client.get(url).status_code == 200, url


# --- B-660: the automatic backup ran only when the dashboard started --------------------------------------------------


def test_the_daily_backup_also_runs_while_the_dashboard_keeps_running(tmp_path, monkeypatch):
    store, client = _web(tmp_path, monkeypatch)
    backup = client.app.state.daily_backup
    assert client.get("/healthz").status_code == 200 and backup.thread is None  # just started: the start's own
    backup.last -= backup.every  # an hour later
    assert client.get("/healthz").status_code == 200
    backup.thread.join(10)
    assert len(store.list_backups()) == 1
    first = backup.thread
    backup.last -= backup.every  # another hour: checked again, but one backup a day (auto_backup's own rule)
    client.get("/healthz")
    assert backup.thread is not first
    backup.thread.join(10)
    assert len(store.list_backups()) == 1


# --- B-662: a stale "as printed" warning, and a quantity-1 line whose amount the clerk fixed --------------------------


def test_fixed_values_drop_the_as_printed_warning_and_the_unit_price_follows(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from ap_coder.core.config import Settings
    from ap_coder.core.store import load_sample_setup
    from ap_coder.web import invoice_view
    from ap_coder.web.invoice_view import Draft, analyse

    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    read = _blank_invoice()  # read as 40.00, but the total printed is 45.00 (a receipt with no tax)
    read["line_items"][0].update(unit_price=40.0, amount=40.0, predicted_gl_code="6000")
    read.update(subtotal=40.0, tax_total=0.0, grand_total=45.0)
    iid = store.add_invoice(tmp_path / "x.pdf", read, {"requires_review": True})
    check = {"code": "TOTALS_ADD_UP", "ok": False, "detail": "subtotal 40.00 + tax 0.00 ≠ total 45.00"}
    capture = SimpleNamespace(fields={}, checks=[check], pages_left_out=0, page_count=1)
    monkeypatch.setattr(invoice_view, "capture_of", lambda *a: capture)

    def codes(draft):
        result = analyse(store, Settings(), store.reference_data(), store.get_invoice(iid), draft)
        return {i.code for i in result.errors + result.warnings}, result

    assert "CAPTURE_TOTALS" in codes(Draft.stored(read))[0]  # as read: the warning stands
    fixed = Draft.stored(read)  # the clerk puts in what the page says: the line is 45.00
    fixed.header["subtotal"] = "45.00"
    fixed.lines[0]["amount"] = 45.0
    found, result = codes(fixed)
    assert "CAPTURE_TOTALS" not in found and "LINE_MATH" not in found, found
    assert result.output["line_items"][0]["unit_price"] == 45.0


# --- B-663: payment terms not found were silent -----------------------------------------------------------------------


def test_terms_not_found_say_the_due_date_is_assumed():
    import datetime as dt

    from ap_coder.coding.terms import payment_findings
    from ap_coder.ops.help import CHECKS

    doc = {"invoice_date": "2026-10-01", "grand_total": 100.0, "payment_terms": ""}
    found = {c: (s, m) for s, c, m in payment_findings(doc, 30, today=dt.date(2026, 10, 2))}
    assert found["TERMS_NOT_FOUND"] == ("info", "Payment terms not found: the due date assumes 30 days (Settings → "
                                                "Review, Days to pay). Check the invoice.")  # fmt: skip
    unread = {
        c: m for _, c, m in payment_findings({**doc, "payment_terms": "As agreed"}, 45, today=dt.date(2026, 10, 2))
    }
    assert unread["TERMS_NOT_FOUND"].startswith(
        "Payment terms “As agreed” not understood: the due date assumes 45 days"
    )
    for quiet in ({**doc, "payment_terms": "Net 30"}, {**doc, "due_date": "2026-11-15"}, {**doc, "grand_total": -5.0}):
        assert "TERMS_NOT_FOUND" not in {c for _, c, _ in payment_findings(quiet, 30, today=dt.date(2026, 10, 2))}
    assert "TERMS_NOT_FOUND" not in {c for _, c, _ in payment_findings(doc, 30, vendor_terms="Net 45")}
    assert "TERMS_NOT_FOUND" in CHECKS


# --- B-664: a correct date older than 3 years showed a red "Failed check 13%" -----------------------------------------


def test_an_old_date_is_a_check_not_a_failed_check(tmp_path):
    import datetime as dt

    from ap_coder.capture import analyze
    from ap_coder.web.invoice_view import capture_confidence

    text = PRAIRIE.replace("October 1, 2026", "March 3, 2021")
    path = tmp_path / "old.txt"
    path.write_text(text)
    r = analyze(str(path), today=dt.date(2026, 10, 22))
    date = r.fields["invoice_date"]
    assert date.value == "2021-03-03" and date.status == "check"
    assert "check: dated more than 3 years ago: check the year" in date.reasons
    assert not any(x.startswith("check failed") for x in date.reasons)
    badge = capture_confidence(r)["invoice_date"]
    assert badge["status"] == "check" and badge["tone"] == "warn"
    future = analyze(str(path), today=dt.date(2020, 1, 1)).fields["invoice_date"]
    assert any(x.startswith("check failed") for x in future.reasons)  # a date far ahead still fails


# --- B-665: a utility bill with no invoice number: the help says what to type -----------------------------------------


def test_no_invoice_number_help_suggests_the_account_number_and_month():
    from ap_coder.ops.help import CHECKS

    action = CHECKS["MISSING_INVOICE_NUMBER"].action
    assert "account number" in action and "1234567-2026-09" in action


# --- B-666: the approval message after a vendor rename, and the progress towards skipping review ----------------------


def test_the_approval_message_names_a_renamed_vendor_and_states_progress_plainly(tmp_path):
    from ap_coder.capture.supplier import to_go_text
    from ap_coder.capture.workflow import learn_from_approval, taught_message

    assert to_go_text(17, done=3) == "3 of about 20 clean approvals toward skipping review"
    assert to_go_text(0, done=20) == "approved often enough without a correction to skip review"
    store = Store(tmp_path / "ap.db")
    read = {**_blank_invoice(), "vendor_name": "BDO Canada", "gst_hst_registration_number": "123456782RT0001"}
    iid = store.add_invoice(tmp_path / "x.pdf", read, {"requires_review": True})
    final = {**read, "vendor_name": "BDO Canada LLP"}
    message = taught_message(store, learn_from_approval(store, iid, final, actor="Ann"))
    assert "You changed no header field" not in message
    assert message.startswith("You changed the vendor (“BDO Canada” to “BDO Canada LLP”): the same vendor to AP Coder, "
                              "so not a correction. BDO Canada LLP: 1 invoice reviewed, ")  # fmt: skip
    assert "clean approvals toward skipping review" in message and "more invoices approved" not in message


# --- B-667: "Pick a GL account for lines 1, 2, 3, … 15" ---------------------------------------------------------------


def test_many_blank_lines_point_to_apply_to_all_lines():
    from ap_coder.coding.review import codes_to_pick

    lines = [{"line_number": n, "predicted_gl_code": "UNASSIGNED"} for n in range(1, 16)]
    assert codes_to_pick(lines, False) == "Pick a GL account for 15 lines to approve (or use Apply to all lines…)"
    assert codes_to_pick(lines, False, hint=False) == "Pick a GL account for 15 lines to approve"
    assert codes_to_pick(lines[:3], False) == "Pick a GL account for lines 1, 2, 3 to approve"


# --- B-668: the standard Custom CSV layout had no vendor name ---------------------------------------------------------


def test_the_standard_custom_csv_layout_names_the_vendor():
    from ap_coder.erp import export_layout as el

    headers = [c.header for c in el.default_columns()]
    assert headers[:2] == ["VendorID", "VendorName"]
    rows = [{"vendor_id": "", "vendor_name": "Acme Ltd", "invoice_number": "1", "amount": 10.0, "currency": "CAD"}]
    first, second = el.build(rows, el.Layout(el.default_columns())).decode("utf-8-sig").splitlines()[:2]
    assert first.startswith("VendorID,VendorName,") and second.startswith(",Acme Ltd,1,")


# --- B-669: a vendor read as "Inc.", one word in capitals, or the bill-to's name was not marked "check" ---------------


def test_a_doubtful_vendor_name_is_marked_check():
    from ap_coder.capture import doubtful_vendor

    assert doubtful_vendor("Inc.", set()) == "the vendor reads as a legal form only: check the name on the page"
    assert doubtful_vendor("Limitée", set()).startswith("the vendor reads as a legal form only")
    assert doubtful_vendor("POSTES", set()) == ("the vendor reads as one word in capitals: check it is the whole "
                                                "name")  # fmt: skip
    assert doubtful_vendor("Fabrikam Canada Ltée", {"fabrikam canada ltee"}) == (
        "the vendor reads as the name in the bill-to (yours, as the customer): check who the vendor is"
    )
    for fine in ("Northwind IT Solutions Inc.", "Bell Canada", "IKEA Canada", "Énergir", "3M Canada", "A&W"):
        assert doubtful_vendor(fine, {"fabrikam canada ltee"}) == "", fine


def test_a_vendor_read_from_the_bill_to_is_check(tmp_path):
    from ap_coder.capture import _mark_doubtful_vendor
    from ap_coder.capture.layout import build_layout
    from ap_coder.capture.types import FieldResult

    text = ("Northern Pet Supply Ltd.\n12 King St W, Toronto ON M5H 1A1\nBill to:\nFabrikam Canada Ltée\n"
            "1000 rue De La Gauchetière O., Montréal QC H3B 4W5\nINVOICE\nInvoice # 77\n")  # fmt: skip
    path = tmp_path / "billto.txt"
    path.write_text(text)
    layout = build_layout(path)

    def marked(name, checks=()):
        fields = {"vendor_name": FieldResult("vendor_name", name, 0.99, "verified", [], {}, [])}
        _mark_doubtful_vendor(layout, fields, list(checks))
        return fields["vendor_name"].status

    assert marked("Fabrikam Canada Ltée") == "check"
    assert marked("Northern Pet Supply Ltd.") == "verified"
    assert marked("POSTES", [{"code": "VENDOR_NAME_MATCH", "ok": True}]) == "verified"  # the vendor master says so
