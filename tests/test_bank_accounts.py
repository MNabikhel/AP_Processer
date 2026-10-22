"""Our company's bank accounts and the "Pay from" account of an invoice (D-66): the rules of coding/banks.py and
the store's table."""

from __future__ import annotations

from ap_coder.coding import banks
from ap_coder.core.store import Store

ACCOUNTS = [
    {"code": "CAD-OP", "name": "Sample Bank CAD operating", "currency": "CAD", "account_number": "0000001234",
     "is_default": True, "active": True},
    {"code": "CAD-PAY", "name": "Sample Bank CAD payroll", "currency": "CAD", "account_number": "0000005678",
     "is_default": False, "active": True},
    {"code": "USD-OP", "name": "Sample Bank USD", "currency": "USD", "account_number": "0000009999",
     "is_default": False, "active": True},
    {"code": "EUR-OLD", "name": "Closed EUR", "currency": "EUR", "account_number": "1111", "is_default": True,
     "active": False},
]  # fmt: skip


def test_an_account_shows_only_its_last_four_digits():
    assert banks.masked("004-12345-0000001234") == "••••1234"
    assert banks.masked("") == ""
    assert banks.label(ACCOUNTS[0]) == "Sample Bank CAD operating · CAD · ••••1234"


def test_the_default_follows_the_currency_and_the_vendors_last_account():
    assert banks.choose(ACCOUNTS, "CAD") == "CAD-OP"  # the currency's default
    assert banks.choose(ACCOUNTS, "usd") == "USD-OP"  # the only USD account
    assert banks.choose(ACCOUNTS, "CAD", preferred="CAD-PAY") == "CAD-PAY"  # this vendor's last one
    assert banks.choose(ACCOUNTS, "USD", preferred="CAD-PAY") == "USD-OP"  # never one in another currency
    assert banks.choose(ACCOUNTS, "EUR") == ""  # the EUR account is not in use: the reviewer picks
    two = [dict(a, is_default=False) for a in ACCOUNTS[:2]]
    assert banks.choose(two, "CAD") == ""  # two CAD accounts, no default: no guess


def test_the_checks_on_the_pay_from_account():
    assert banks.findings("", "CAD", []) == []  # none set up: nothing required
    assert [f[1] for f in banks.findings("", "CAD", ACCOUNTS)] == ["PAY_FROM_MISSING"]
    assert banks.findings("", "CAD", ACCOUNTS)[0][2] == "Pick the bank account to pay this invoice from"
    assert [f[1] for f in banks.findings("EUR-OLD", "EUR", ACCOUNTS)] == ["PAY_FROM_UNKNOWN"]
    assert [(f[0], f[1]) for f in banks.findings("USD-OP", "CAD", ACCOUNTS)] == [("warning", "PAY_FROM_CURRENCY")]
    assert banks.findings("CAD-PAY", "CAD", ACCOUNTS) == []


def test_rows_from_a_spreadsheet_are_checked():
    rows = [
        {"code": "CAD-OP", "name": "Operating", "currency": "cad", "institution": 4.0, "transit": "123",
         "account_number": "000-0001234", "gl_account": "1010", "is_default": "Yes", "active": ""},
        {"code": "", "name": "", "currency": ""},  # a blank row
        {"code": "USD-OP", "name": "US", "currency": "US dollars"},
        {"code": "CAD-2", "name": "Second", "currency": "CAD", "gl_account": "9999"},
        {"code": "cad-op", "name": "Again"},
        {"code": "CAD-3", "currency": "CAD", "is_default": "x"},
    ]  # fmt: skip
    clean, problems = banks.clean_rows(rows, gl_codes={"1010"})
    first = clean[0]
    assert (first["currency"], first["institution"], first["transit"], first["account_number"]) == (
        "CAD",
        "004",
        "00123",
        "0000001234",
    )
    assert first["is_default"] and first["active"]
    assert [a["code"] for a in clean] == ["CAD-OP", "CAD-3"]
    assert any("not a 3-letter code" in p for p in problems)
    assert any("9999 is not in your GL accounts" in p for p in problems)
    assert any("cad-op is used twice" in p for p in problems)
    assert any("Only one default account per currency" in p for p in problems)
    assert banks.clean_rows([rows[3]], gl_codes=None)[1] == []  # no chart loaded: any GL account


def test_the_columns_of_a_bank_file_are_recognised():
    headers = ["Bank account code", "Account name", "Devise", "Institution #", "Transit", "Account No.", "Cash GL",
               "Default"]  # fmt: skip
    found = banks.columns_for(headers)
    assert found == {"code": "Bank account code", "name": "Account name", "currency": "Devise",
                     "institution": "Institution #", "transit": "Transit", "account_number": "Account No.",
                     "gl_account": "Cash GL", "is_default": "Default"}  # fmt: skip


def test_the_store_keeps_one_default_per_currency_and_the_vendors_last_account(tmp_path):
    store = Store(tmp_path / "ap.db")
    assert store.bank_accounts() == []
    clean, _ = banks.clean_rows(ACCOUNTS[:3])
    assert store.save_bank_accounts(clean, actor="Pat") == {"added": 3, "updated": 0}
    assert store.save_bank_accounts(clean) == {"added": 0, "updated": 0}  # nothing changed
    store.save_bank_accounts([dict(clean[1], is_default=True)])
    defaults = [a["code"] for a in store.bank_accounts() if a["is_default"]]
    assert defaults == ["CAD-PAY"]  # the new default replaced the old one
    store.save_bank_accounts([dict(clean[2], active=False)])
    assert [a["code"] for a in store.bank_accounts(active_only=True)] == ["CAD-PAY", "CAD-OP"] or [
        a["code"] for a in store.bank_accounts(active_only=True)
    ] == ["CAD-OP", "CAD-PAY"]
    assert store.last_pay_from("acme") == ""
    assert store.events(actions=["accounts_edited"])[0]["detail"]["table"] == "bank_accounts"
