"""Our company's bank accounts: the account an invoice is paid *from* ("Pay from" on the review screen, D-66).

These are the company's own accounts (imported on GL accounts & tax, ``Store.bank_accounts``), never the vendor's:
the vendor's banking details printed on an invoice stay evidence for the fraud check (VENDOR_BANK_CHANGED,
``vendors.py``). An account has a short code ("CAD-OP"), a name, a currency, the bank's institution, transit and
account numbers (only the last 4 digits are ever shown), the cash GL account it posts to, whether it is the default
for its currency and whether it is in use.

* ``clean_rows``: rows from a spreadsheet or the grid, checked (code, currency, GL account, one default per currency).
* ``choose``: the account an invoice is paid from by default (the vendor's last one, else its currency's default,
  else the only account in that currency).
* ``findings``: the checks on the account chosen (PAY_FROM_MISSING, PAY_FROM_UNKNOWN, PAY_FROM_CURRENCY).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from ..core.headers import header_key

ERROR, WARNING, INFO = "error", "warning", "info"
PICK = "Pick the bank account to pay this invoice from"
NONE_YET = "Add your bank accounts on GL accounts & tax to choose which account pays"
COLUMNS = ("code", "name", "currency", "institution", "transit", "account_number", "gl_account", "is_default", "active")
# The import's columns and the headers each is recognised by (``header_key`` of the header).
ALIASES: dict[str, tuple[str, ...]] = {
    "code": ("code", "bankcode", "accountcode", "id", "shortcode", "bankaccountcode", "bankid"),
    "name": ("name", "bankaccountname", "accountname", "description", "bankname", "nom"),
    "currency": ("currency", "cur", "currencycode", "ccy", "devise"),
    "institution": ("institution", "institutionnumber", "bank", "banknumber", "inst", "institutionno"),
    "transit": ("transit", "transitnumber", "branch", "branchnumber", "transitno", "succursale"),
    "account_number": ("accountnumber", "account", "accountno", "bankaccount", "bankaccountnumber", "acct",
                       "numerodecompte", "compte"),
    "gl_account": ("glaccount", "gl", "glcode", "cashgl", "cashaccount", "cashglaccount", "glbankaccount"),
    "is_default": ("default", "isdefault", "defaultaccount", "defaultforcurrency", "pardefaut"),
    "active": ("active", "inuse", "enabled", "actif", "status"),
}  # fmt: skip
IMPORT_FIELDS = {
    "code": "Code column", "name": "Name column", "currency": "Currency column", "institution": "Institution column",
    "transit": "Transit column", "account_number": "Account number column", "gl_account": "Cash GL account column",
    "is_default": "Default column (optional)", "active": "Active column (optional)",
}  # fmt: skip
_YES = {"yes", "y", "true", "1", "x", "oui", "o", "default", "active", "on"}
_NO = {"no", "n", "false", "0", "non", "inactive", "closed", "off"}


def digits(value: Any) -> str:
    return re.sub(r"\D", "", str(value or ""))


def last4(value: Any) -> str:
    """The last 4 digits of an account number: all that is ever shown of it."""
    return digits(value)[-4:]


def masked(value: Any) -> str:
    """ "••••1234", or "" with no digits."""
    end = last4(value)
    return f"••••{end}" if end else ""


def label(account: dict[str, Any]) -> str:
    """ "RBC CAD operating · CAD · ••••1234": how an account is shown in the Pay from list."""
    parts = [str(account.get("name") or account.get("code") or ""), str(account.get("currency") or "")]
    shown = masked(account.get("account_number"))
    return " · ".join(p for p in (*parts, shown) if p)


def _flag(value: Any, default: bool) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if text in ("nan", "none", ""):
        return default
    if text in _YES:
        return True
    if text in _NO:
        return False
    return default


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    text = str(value).strip()
    return "" if text.lower() in ("nan", "none") else text


def _number(value: Any) -> str:
    """A bank number as written ("004", "00012"): a spreadsheet that read it as a number lost its leading zeros,
    so only the digits are kept; the caller pads what has a fixed width."""
    return digits(_text(value))


def columns_for(headers: Iterable[str]) -> dict[str, str]:
    """{field: header} for the headers of a spreadsheet, recognised by ``ALIASES`` (accents and spacing aside)."""
    found: dict[str, str] = {}
    keyed = {header_key(h): h for h in headers}
    for target, aliases in ALIASES.items():
        for alias in (header_key(target), *aliases):
            if alias in keyed and keyed[alias] not in found.values():
                found[target] = keyed[alias]
                break
    return found


def clean_rows(rows: Iterable[dict[str, Any]], gl_codes: set[str] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    """(the accounts, the problems) from rows keyed by ``COLUMNS``. A row without a code is skipped when it is blank,
    else a problem; ``gl_codes``: the GL accounts loaded (None: no chart, any GL account is taken). The institution
    is kept at 3 digits and the transit at 5 (Canadian numbers), the currency upper case; two defaults for one
    currency is a problem."""
    out: list[dict[str, Any]] = []
    problems: list[str] = []
    seen: set[str] = set()
    for n, row in enumerate(rows, start=1):
        code = _text(row.get("code"))
        values = {c: _text(row.get(c)) for c in COLUMNS if c not in ("is_default", "active")}
        if not code:
            if any(values.values()):
                problems.append(f"Row {n}: give the account a code (e.g. CAD-OP).")
            continue
        key = code.upper()
        if key in seen:
            problems.append(f"The code {code} is used twice.")
            continue
        seen.add(key)
        currency = values["currency"].upper() or "CAD"
        if not re.fullmatch(r"[A-Z]{3}", currency):
            problems.append(f"{code}: the currency “{values['currency']}” is not a 3-letter code such as CAD or USD.")
            continue
        institution, transit = _number(row.get("institution")), _number(row.get("transit"))
        gl = values["gl_account"]
        if gl and gl_codes is not None and gl not in gl_codes:
            problems.append(f"{code}: GL account {gl} is not in your GL accounts.")
            continue
        out.append({
            "code": code, "name": values["name"] or code, "currency": currency,
            "institution": institution.zfill(3) if institution else "",
            "transit": transit.zfill(5) if transit else "", "account_number": _number(row.get("account_number")),
            "gl_account": gl, "is_default": _flag(row.get("is_default"), False), "active": _flag(row.get("active"), True),
        })  # fmt: skip
    defaults: dict[str, list[str]] = {}
    for a in out:
        if a["is_default"] and a["active"]:
            defaults.setdefault(a["currency"], []).append(a["code"])
    for currency, codes in sorted(defaults.items()):
        if len(codes) > 1:
            problems.append(f"Only one default account per currency: {', '.join(codes)} are all the {currency} default.")
    return out, problems


def choose(accounts: list[dict[str, Any]], currency: str, preferred: str = "") -> str:
    """The code of the account to pay an invoice in ``currency`` from, by default: ``preferred`` (the account this
    vendor's last invoice was paid from) when it is active and in that currency, else the currency's default, else the
    only active account in that currency, else "" (none to choose: the reviewer picks)."""
    currency = (currency or "CAD").strip().upper()
    same = [a for a in accounts if a.get("active") and str(a.get("currency") or "").upper() == currency]
    if preferred and any(a["code"] == preferred for a in same):
        return preferred
    default = [a for a in same if a.get("is_default")]
    if default:
        return str(default[0]["code"])
    return str(same[0]["code"]) if len(same) == 1 else ""


def findings(code: str, currency: str, accounts: list[dict[str, Any]]) -> list[tuple[str, str, str]]:
    """(severity, check, message) for the account an invoice is paid from. None while no account is set up (the
    review screen says how to add them)."""
    active = [a for a in accounts if a.get("active")]
    if not active:
        return []
    if not code:
        return [(ERROR, "PAY_FROM_MISSING", PICK)]
    account = next((a for a in active if a["code"] == code), None)
    if account is None:
        return [(ERROR, "PAY_FROM_UNKNOWN", f"the bank account {code} is not one of your bank accounts in use: pick "
                 "another")]  # fmt: skip
    currency = (currency or "CAD").strip().upper()
    if str(account.get("currency") or "").upper() != currency:
        return [(WARNING, "PAY_FROM_CURRENCY", f"a {currency} invoice paid from {account['name']}, a "
                 f"{account['currency']} account: pick a {currency} account, or confirm the conversion with the "
                 "bank")]  # fmt: skip
    return []
