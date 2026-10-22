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
