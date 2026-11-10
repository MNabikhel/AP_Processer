"""The bank account on the voucher (D-220): our bank accounts typed in and removed by hand under Setup, by an
Approver or an Admin where people sign in; with one entered, the review screen's header shows "Bank account" and the
keying sheet's header block carries it; a currency with no account is a warning, not a hold; with none entered
nothing changes."""

from __future__ import annotations

from ap_coder.bench import jde_invoices
from ap_coder.coding import banks
from ap_coder.erp import jde_keying, jde_settings, jde_zfiles
from ap_coder.web import core, roles
from ap_coder.web.app import create_app
from tests.erp.test_jde_keying import field
from tests.web.test_chart_company_walk import _coded, _sheet, _vouchers
from tests.web.test_server_web import account, log_in, server_client
from tests.webclient import PAGE, follow, web_settings

ACCOUNT, USD_ACCOUNT = "100.1110.BMO", "100.1115.USD"


def _typed(*rows: tuple[str, str, str, bool], kept: int = 0) -> dict:
    """The short grid as the page posts it: (account as keyed, description, currency, default) a row; the first
    ``kept`` rows are accounts in the list already (in use)."""
    form: dict = {name: [] for name in ("code", "name", "currency", "institution", "transit", "account_number",
                                        "gl_account", "is_default", "active")}  # fmt: skip
    for n, (code, name, currency, default) in enumerate(rows):
        for key, value in (("code", code), ("name", name), ("currency", currency), ("institution", ""),
                           ("transit", ""), ("account_number", ""), ("gl_account", "")):  # fmt: skip
            form[key].append(value)
        if default:
            form["is_default"].append(str(n))
        if n < kept:
            form["active"].append(str(n))
    return form


def _add(store, *rows: dict) -> None:
    cleaned, problems = banks.clean_rows(list(rows))
    assert not problems
    store.save_bank_accounts(cleaned)


def test_in_simple_screens_bank_accounts_are_typed_in_and_removed_by_hand(client, store):
    store.set_setting(core.SCREENS, core.SIMPLE)
    page = client.get("/accounts?tab=bank").text
    assert "Bank account (as keyed in JD Edwards)" in page and "<th>Description</th>" in page
    assert "<th>Institution</th>" not in page and "/accounts/bank/upload" not in page  # nothing to import
    saved = client.post("/accounts/bank/save", headers=PAGE, data=_typed(
        (ACCOUNT, "Operating account", "cad", True), (USD_ACCOUNT, "US dollar account", "USD", True)))  # fmt: skip
    page = follow(client, saved)
    assert "Saved: 2 bank accounts changed." in page
    kept = {a["code"]: (a["name"], a["currency"], a["is_default"], a["active"]) for a in store.bank_accounts()}
    assert kept == {ACCOUNT: ("Operating account", "CAD", True, True),
                    USD_ACCOUNT: ("US dollar account", "USD", True, True)}  # fmt: skip
    assert f'formaction="/accounts/bank/remove" name="remove" value="{ACCOUNT}"' in page
    again = client.post("/accounts/bank/save", headers=PAGE, data=_typed(
        (ACCOUNT, "Operating", "CAD", True), (USD_ACCOUNT, "US dollar account", "USD", True), kept=2))  # fmt: skip
    assert "Saved: 1 bank account changed." in follow(client, again)
    assert all(a["active"] for a in store.bank_accounts())  # the short grid has no In use box: kept as it was
    page = follow(client, client.post("/accounts/bank/remove", headers=PAGE, data={"remove": USD_ACCOUNT}))
    assert f"Bank account {USD_ACCOUNT} removed." in page and [a["code"] for a in store.bank_accounts()] == [ACCOUNT]
    gone = client.post("/accounts/bank/remove", headers=PAGE, data={"remove": USD_ACCOUNT})
    assert gone.status_code == 400 and "not in the list any more" in gone.json()["error"]
    removed = store.events(actions=["accounts_deleted"], limit=1)[0]["detail"]
    assert removed == {"table": "bank_accounts", "codes": [USD_ACCOUNT]}


def test_where_people_sign_in_an_approver_adds_and_removes_them_and_a_clerk_sees_and_picks(store):
    app = create_app(web_settings(), store, start_reader=False, server=True)
    for path in ("/accounts/bank/save", "/accounts/bank/remove"):
        assert roles.can(app, "approver", "POST", path) and roles.can(app, "admin", "POST", path), path
        assert not roles.can(app, "clerk", "POST", path) and not roles.can(app, "readonly", "POST", path), path
    assert roles.can(app, "clerk", "GET", "/accounts")
    assert not roles.can(app, "approver", "POST", "/accounts/bank/import")  # a file of them: an Admin's, as before
    account(store, "ann", "clerk", "Ann Lee")
    account(store, "cy", "approver", "Cy Tran")
    ann, cy = server_client(store), server_client(store)
    log_in(ann, "ann")
    log_in(cy, "cy")
    typed = _typed((ACCOUNT, "Operating account", "CAD", True))
    assert ann.post("/accounts/bank/save", headers=PAGE, data=typed).status_code == 403
    assert store.bank_accounts() == []
    assert cy.post("/accounts/bank/save", headers=PAGE, data=typed).status_code == 200
    assert [a["code"] for a in store.bank_accounts()] == [ACCOUNT]
    seen = ann.get("/accounts?tab=bank").text  # a Clerk sees the list, with nothing to change it
    assert ACCOUNT in seen and "An Approver or an Admin adds, changes and removes bank accounts." in seen
    assert 'action="/accounts/bank/save"' not in seen and "/accounts/bank/remove" not in seen
    assert ann.post("/accounts/bank/remove", headers=PAGE, data={"remove": ACCOUNT}).status_code == 403
    assert cy.post("/accounts/bank/remove", headers=PAGE, data={"remove": ACCOUNT}).status_code == 200
    assert store.bank_accounts() == []


def test_the_review_header_shows_the_bank_account_once_one_is_entered(chart_company, chart_company_client):
    store, client = chart_company.store, chart_company_client
    cad = chart_company.invoices["fifteen_lines"][1]
    usd = chart_company.invoices["usd_new_supplier"][1]
    before = client.get(f"/review/{cad}").text
    assert "inv-head-row bank" not in before and ">Bank account <" not in before  # none entered: as it was
    _add(store, {"code": ACCOUNT, "name": "Operating account", "currency": "CAD", "is_default": "yes"},
         {"code": "100.1112.PAY", "name": "Payroll account", "currency": "CAD"})  # fmt: skip
    page = client.get(f"/review/{cad}").text
    head = page.split('<div class="inv-head-row bank">', 1)[1].split("</div>", 1)[0]
    assert ">Bank account <" in head and f'<option value="{ACCOUNT}" data-currency="CAD" selected>' in head
    assert "pay from</span>" not in page  # no longer under More details
    # a voucher in a currency no account is in: a plain warning, and Approve is not held for it
    answer = client.post(f"/review/{usd}/check", data={}, headers=PAGE).json()
    assert "No bank account is entered for USD" in answer["sections"]["checks"]
    assert "Pick the bank account" not in answer["sections"]["bar"]
    assert "pay_from_account" not in answer["header"]["flag_errors"]


def test_the_keying_sheet_and_the_batch_file_carry_the_bank_account(chart_company, chart_company_client):
    store, client = chart_company.store, chart_company_client
    _add(store, {"code": ACCOUNT, "name": "Operating account", "currency": "CAD", "is_default": "yes"})
    page = _coded(client, chart_company, jde_invoices.NAMED[0])
    assert page.check()["allow"] and page.approve()["ok"]
    inv = store.get_invoice(page.id)
    assert inv["final_output"]["pay_from_account"] == ACCOUNT
    _batch, workbook = _sheet(client, store, [page.id])
    (block,) = _vouchers(workbook).values()
    assert field(block, "G/L bank account").value == ACCOUNT  # as typed in JD Edwards
    assert field(block, "Bank account").value == "Operating account"
    # the batch file: its VLGLBA column, once the JDE team's value is mapped in Settings (as before, D-66)
    settings = jde_settings.load(store)
    assert "VLGLBA" not in jde_zfiles.header_columns(settings)
    settings.bank_glba = {ACCOUNT: "00012345"}
    assert (
        "VLGLBA" in jde_zfiles.header_columns(settings) and jde_zfiles.glba(inv["final_output"], settings) == "00012345"
    )
    ready, _held = jde_keying.vouchers([inv], settings, store.vendor_ids(),
                                       bank_accounts={a["code"]: a for a in store.bank_accounts()})  # fmt: skip
    assert ready[0].bank == ("00012345", "Operating account")  # the sheet shows what the file sends
