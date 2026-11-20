"""Which of JD Edwards' companies an invoice is ours under (D-267), and what follows from it.

JD Edwards' companies (report 9 of the build sheet: F0010's Company, Company Name and Currency Code, with the country of
its address book entry) are loaded on JD Edwards files and kept whole in a setting (``COMPANIES_SETTING``). With fewer
than two of them (no report 9 loaded, or one company: ``several``), nothing here decides anything: every invoice is
the company of Settings → JD Edwards E1, as it always was.

With several, an invoice's company is the first of these that gives one answer (``decide``), never a guess between two:

(a) its PO with its supplier: the company of the supplier's orders of that number;
(b) the companies' names (report 9's, and the variations of Settings → Review's "Your company's names" written
    "name = company") printed on the page, the supplier's own name left out (a company of the group billing another);
(c) the supplier's history: it only ever billed one company (the past invoice coding loaded, the vouchers already in
    JD Edwards);
(d) the business units its lines are coded to: all of one company (F0006);
(e) else none: the invoice is held for a person (``COMPANY_UNKNOWN``, "Pick the company"), who picks it in the
    company box on the review screen.

The coding keeps how its company was decided (``InvoiceCoding.company_how``, D-286): a person's pick (``PICKED``) is
kept whatever the rules say; a company AP Coder decided is decided again at every check, as the facts it rests on
change (a PO loaded, the supplier's history, the lines recoded). A company kept before its source was is AP Coder's.

A company gets the Canadian tax checks (GST/HST, PST, QST) only when its country is CA and its currency CAD
(``canadian``): report 9's country is not reliable (blank, or a USD company of CA), so every other company is checked
like a US one: every check but the Canadian taxes, a warning that its tax was not checked (``US_TAX_NOT_CHECKED``), and
never approved without a person. Settings → JD Edwards E1 can turn the Canadian checks on or off for a company, which
wins (``TAX_CHECKS``).
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..core.headers import jde_names
from ..core.keys import company_code, master_vendor_id, po_key
from ..core.memory import vendor_key

if TYPE_CHECKING:
    from ..core.schema import InvoiceCoding
    from ..core.store import Store

ERROR, WARNING = "error", "warning"
COMPANIES_SETTING = "jde_companies"  # report 9 as loaded: {company: {"name", "currency", "country"}}
# F0010 as ReportsNow titles it by default ("Co", "Name", "Cur Cod") and as the build sheet does ("Company", "Company
# Name", "Currency Code", "Country"), and JD Edwards' fields (CCCO, CCNAME, CCCRCD; the country: the address book's
# ALCTR).
COMPANY_ALIASES: dict[str, tuple[str, ...]] = {
    "company": ("company", "co", "companynumber", *jde_names("F0010", "CC", "CO")),
    "name": ("companyname", "name", *jde_names("F0010", "CC", "NAME")),
    "currency": ("currencycode", "curcod", "currency", *jde_names("F0010", "CC", "CRCD")),
    "country": ("country", "countrycode", *jde_names("F0116", "AL", "CTR")),
}  # fmt: skip
COMPANY_NEEDS = ("company", "name")
COMPANY_ONE_OF = ("currency", "country")  # with the company and its name, one of these: report 9, never a 2-column list
DEFAULT_COMPANY = "00000"  # JD Edwards' default company: the model of the others, no company's books
# Settings → JD Edwards E1, per company: the Canadian tax checks as worked out from report 9, or turned on or off.
AUTO, ON, OFF = "auto", "on", "off"
TAX_CHECKS = (AUTO, ON, OFF)


@dataclass(frozen=True)
class Company:
    code: str
    name: str = ""
    currency: str = ""
    country: str = ""


# How an invoice's company was decided (``Decision.how``, kept with the coding as ``company_how``): a person's pick, or
# one of AP Coder's rules, said on the review screen's Company box (``SOURCES``).
PICKED, BY_PO, BY_NAME, BY_HISTORY, BY_UNITS = "picked", "po", "name", "history", "business unit"
SOURCES = {PICKED: "Picked by a person", BY_PO: "Decided by AP Coder from its PO and supplier",
           BY_NAME: "Decided by AP Coder from a name on the page",
           BY_HISTORY: "Decided by AP Coder from the supplier's history",
           BY_UNITS: "Decided by AP Coder from the business units of its lines"}  # fmt: skip


@dataclass
class Decision:
    """An invoice's company: ``company`` ("" none), how it was decided (``how``: ``PICKED``, ``BY_PO``, ``BY_NAME``,
    ``BY_HISTORY``, ``BY_UNITS``), and why in plain words (``said``); undecided, the companies it could be
    (``could_be``)."""

    company: str = ""
    how: str = ""
    said: str = ""
    could_be: list[str] = field(default_factory=list)


# --- Report 9 ------------------------------------------------------------------------------------------------------


def _text(value: Any) -> str:
    if value is None or (isinstance(value, float) and value != value):
        return ""
    text = str(value).strip()
    return text[:-2] if re.fullmatch(r"\d+\.0", text) else text


# Report 9's country as it may be written (the address book's, typed by people): CA, CAN or Canada; US, USA or United
# States; letters only, any case (B-2689).
_CANADA, _UNITED_STATES = {"CA", "CAN", "CANADA"}, {"US", "USA", "UNITEDSTATES", "UNITEDSTATESOFAMERICA"}


def _country(value: Any) -> str:
    """A country of report 9 as compared: "CA" (CAN, Canada, " ca "), "US" (USA, United States, U.S.), any other its
    first three letters in capitals."""
    letters = re.sub(r"[^A-Z]", "", _text(value).upper())
    return "CA" if letters in _CANADA else "US" if letters in _UNITED_STATES else letters[:3]


def _currency(value: Any) -> str:
    """A currency code of report 9 as compared: its letters in capitals (" cad ", "C AD": CAD)."""
    return re.sub(r"[^A-Z]", "", _text(value).upper())[:3]


def company_table(records: list[dict[str, Any]], columns: dict[str, str]) -> tuple[dict[str, dict[str, str]], int]:
    """({company: {"name", "currency", "country"}}, rows left out) of report 9's rows (``columns``: {field: the
    file's column}). A row with no company or name is left out, and JD Edwards' default company (00000), no
    company's books."""
    table: dict[str, dict[str, str]] = {}
    if not set(COMPANY_ONE_OF) & set(columns):  # a company and a name alone: any list's, never report 9
        return table, len(records)
    left = 0
    for record in records:
        code = company_code(_text(record.get(columns.get("company", ""))))
        name = _text(record.get(columns.get("name", "")))
        if not code or not name or code == DEFAULT_COMPANY:
            left += 1
            continue
        table[code] = {"name": name, "currency": _currency(record.get(columns.get("currency", ""))),
                       "country": _country(record.get(columns.get("country", "")))}  # fmt: skip
    return table, left


def loaded(text: str) -> tuple[Company, ...]:
    """JD Edwards' companies of report 9 as loaded: ``text``, the setting it is kept in (``COMPANIES_SETTING``)."""
    try:
        data = json.loads(text or "{}")
    except ValueError:
        return ()
    if not isinstance(data, dict):
        return ()
    return tuple(Company(company_code(code), str(row.get("name") or ""), _currency(row.get("currency")),
                         _country(row.get("country")))
                 for code, row in sorted(data.items()) if isinstance(row, dict))  # fmt: skip


def companies(store: Any) -> dict[str, Company]:
    """JD Edwards' companies as loaded (report 9), by code ({} when none are)."""
    return {c.code: c for c in loaded(store.get_setting(COMPANIES_SETTING) or "")}


def several(store: Any) -> bool:
    """Whether AP Coder works for several companies at once: report 9 lists two or more."""
    return store is not None and len(companies(store)) > 1


def name_variations(store: Any) -> dict[str, str]:
    """{a name's vendor key: its company} of Settings → Review's "Your company's names" lines written "name =
    company" (a trade name, a former name), then report 9's names."""
    out: dict[str, str] = {}
    for line in str(store.get_setting("our_names") or "").splitlines():
        name, sep, code = line.rpartition("=")
        if sep and vendor_key(name) and company_code(code):
            out[vendor_key(name)] = company_code(code)
    for company in companies(store).values():
        out.setdefault(vendor_key(company.name), company.code)
    out.pop("", None)
    return out


def unknown_variations(store: Any, text: str) -> list[str]:
    """The lines of "Your company's names" (``text``, as typed) written "name = company" whose company report 9 does
    not list, nor the pilot (D-272), each as typed: none without report 9 loaded (B-2694)."""
    table = companies(store)
    pilot = store.chart_settings().pilot if table else ()
    return [line.strip() for line in text.splitlines() if "=" in line and line.rpartition("=")[0].strip()
            and company_code(line.rpartition("=")[2]) not in (*table, *pilot)]  # fmt: skip


def own_names(store: Any) -> list[str]:
    """The company's own names: Settings → Review's (without their "= company") and report 9's, never a supplier."""
    names = [line.rpartition("=")[0].strip() if "=" in line else line.strip()
             for line in str(store.get_setting("our_names") or "").splitlines()]  # fmt: skip
    return [n for n in names if n] + [c.name for c in companies(store).values() if c.name]


# --- Canadian tax or not -------------------------------------------------------------------------------------------


def canadian(store: Any, company: str) -> tuple[bool, str]:
    """(whether ``company``'s invoices get the Canadian tax checks, why in a few words). One company (no report 9):
    always, as before. Settings' choice for the company wins; else only a company whose country is CA and whose
    currency is CAD ("CA and CAD"); any other is checked like a US one ("currency USD", "country US", "no country",
    "not in JD Edwards' companies")."""
    table = companies(store) if store is not None else {}
    if len(table) < 2:
        return True, "one company"
    code = company_code(company)
    chosen = tax_choices(store).get(code, AUTO)
    if chosen == ON:
        return True, "turned on in Settings"
    if chosen == OFF:
        return False, "turned off in Settings"
    return worked_out(table.get(code))


def worked_out(row: Company | None) -> tuple[bool, str]:
    """Whether report 9's row of a company gives it the Canadian tax checks, and why (``canadian``)."""
    if row is None:
        return False, "not in JD Edwards' companies"
    if row.country == "CA" and row.currency == "CAD":
        return True, "CA and CAD"
    if not row.country:
        return False, "no country"
    if row.currency != "CAD":
        return False, f"currency {row.currency}" if row.currency else "no currency"
    return False, f"country {row.country}"


def tax_choices(store: Any) -> dict[str, str]:
    """{company: "on" or "off"} of Settings → JD Edwards E1's Canadian tax checks per company ("auto" left out)."""
    from ..erp import jde_settings

    return {company_code(k): v for k, v in jde_settings.load(store).company_tax.items() if v in (ON, OFF)}


def currency_of(store: Any, company: str) -> str:
    """A company's currency as report 9 gives it ("" when not known)."""
    found = companies(store).get(company_code(company)) if store is not None else None
    return found.currency if found else ""


# --- Which company ---------------------------------------------------------------------------------------------------


def _one(found: Iterable[str]) -> str:
    codes = {company_code(c) for c in found if str(c or "").strip()}
    return codes.pop() if len(codes) == 1 else ""


SHORT_NAME = 3  # a name of this many letters and digits or fewer ("MRB") is named only as a word in capitals (B-2690)


@dataclass(frozen=True)
class _Page:
    """The page's letters and digits run together in small letters (``run``: a scan runs words together, B-2691),
    where its words start and end in it (``edges``: a space or punctuation between, a capital after a small letter
    as in "MapleRidge", a digit beside a letter), and where each word printed in capitals is ({word: [(start, end)]},
    for the short names)."""

    run: str
    edges: frozenset[int]
    capitals: dict[str, list[tuple[int, int]]]


def _page(text: str) -> _Page:
    folded = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    run: list[str] = []
    edges, capitals, at = {0}, {}, 0  # type: ignore[var-annotated]
    for word in re.findall(r"[A-Za-z0-9]+", folded.replace("&", " and ")):
        edges.update(at + i for i in range(1, len(word)) if (word[i].isupper() and word[i - 1].islower())
                     or word[i].isdigit() != word[i - 1].isdigit())  # fmt: skip
        if word.isupper():
            capitals.setdefault(word.lower(), []).append((at, at + len(word)))
        run.append(word.lower())
        at += len(word)
        edges.add(at)
    return _Page("".join(run), frozenset(edges), capitals)


def _at(page: _Page, key: str, ends: bool = True) -> list[tuple[int, int]]:
    """Where a name (``key``, its vendor key) is printed on ``page``, its spaces and punctuation left out: (start, end)
    of each, starting a word and (``ends``) ending one; a short name (``SHORT_NAME``) only as a word of its own in
    capitals, never inside another or run into one."""
    name = key.replace(" ", "")
    if len(name) <= SHORT_NAME:
        return list(page.capitals.get(name, []))
    found, start = [], page.run.find(name)
    while start >= 0:
        if start in page.edges and (not ends or start + len(name) in page.edges):
            found.append((start, start + len(name)))
        start = page.run.find(name, start + 1)
    return found


def _own_key(name: str) -> str:
    """A supplier's name as compared with the company's own names and report 9's: its vendor key, without the
    supplier number it may be picked with ("MAPLE RIDGE CONSTRUCTORS LTD #4410900", B-2794)."""
    from ..core.store.vendor_master import without_number

    return vendor_key(without_number(str(name or "")))


def _named(text: str, names: dict[str, str], supplier: str) -> set[str]:
    """The companies whose names (``names``: {vendor key: company}) are printed in ``text``, the supplier's own left
    out, and a name printed only inside the supplier's ("fraser bend" in "Fraser Bend Aggregates Ltd.", B-2680, run
    together or not: the supplier's name found wherever it starts a word); of two names one inside the other ("maple
    ridge" in "maple ridge us"), the longer."""
    page = _page(text)
    theirs = _at(page, supplier, ends=False) if supplier else []
    hits = [key for key in names if key and key != supplier
            and any(not any(s <= a and b <= e for s, e in theirs) for a, b in _at(page, key))]  # fmt: skip
    longest = [key for key in hits if not any(key != other and f" {key} " in f" {other} " for other in hits)]
    return {names[key] for key in longest}


def group_suppliers(store: Any, text: str, coding: InvoiceCoding) -> list[tuple[str, str]]:
    """[(report 9's name, the supplier as the vendor box takes it)] of the companies of the group printed on the page
    (``text``) outside its bill-to and ship-to (``offline_coder.customer_block``) that are suppliers in use of the
    vendor master: one of them may have billed the invoice to another (an intercompany invoice), and their names are
    never read as the supplier (``own_names``: the bill-to prints one on every outside invoice), so a person is
    offered it to pick (D-298). Only with no supplier in the box, or one of the company's own names (the company
    billed, by mistake: B-2792); never the company the invoice is for (``coding.company``), one in its bill-to or
    ship-to, or the supplier in the box (its name, and a name only inside it); [] with fewer than two companies."""
    table = companies(store)
    supplier = _own_key(coding.vendor_name)
    if len(table) < 2 or not text.strip() or (supplier and supplier not in map(vendor_key, own_names(store))):
        return []
    from ..ai.offline_coder import customer_block
    from ..core.keys import IN_ERP
    from ..core.store.vendor_master import erp_stopped, with_number

    names = name_variations(store)
    billed, rest = customer_block(text)
    printed = _named(rest, names, supplier) - _named(billed, names, "")
    out = []
    for code in sorted((printed - {company_code(coding.company)}) & set(table)):
        name = table[code].name
        shared = store.vendors_sharing(name)  # several supplier numbers under the name: each with its number
        found = shared or [v for v in [store.find_vendor(name)] if v is not None]
        out += [(name, with_number(v) if shared else str(v["display_name"])) for v in found
                if v.get("in_master") == IN_ERP and not erp_stopped(v)]  # fmt: skip
    return out


def decide(store: Store, coding: InvoiceCoding, text: str = "") -> Decision:
    """The invoice's company (module docstring: (a) to (e)); ``text``: the page as read. With one company: none. A
    company a person picked (``coding.company_how``) is kept; any other is decided again (D-286)."""
    table = companies(store)
    if len(table) < 2:
        return Decision()
    has = company_code(coding.company)
    if has and coding.company_how == PICKED:
        return Decision(has, PICKED, "picked on the review screen")
    return _decided(store, coding, text, table)


def _decided(store: Store, coding: InvoiceCoding, text: str, table: dict[str, Company]) -> Decision:
    vendor = vendor_key(coding.vendor_name)
    supplier = master_vendor_id(store.vendor_ids(), coding.vendor_name, coding.gst_hst_registration_number)
    could: set[str] = set()
    number = po_key(coding.po_number)
    if number:
        from .po import of_supplier

        mine = [o for o in store.orders_numbered(number) if of_supplier(o, vendor, supplier)]
        found = {company_code(o["order_company"]) for o in mine if o.get("order_company")}
        if len(found) == 1:
            return Decision(found.pop(), BY_PO, f"the company of PO {coding.po_number.strip()}")
        could |= found
    named = _named(text, name_variations(store), _own_key(coding.vendor_name)) if text else set()
    if len(named) == 1:
        code = named.pop()
        name = table[code].name if code in table else code
        return Decision(code, BY_NAME, f"{name} is named on the invoice")
    could |= named
    billed = store.supplier_companies(vendor, supplier)
    if len(billed) == 1:
        return Decision(next(iter(billed)), BY_HISTORY, "the only company this supplier has billed")
    could |= billed
    pairs = store.account_pairs()
    units = {li.predicted_cost_center.strip() for li in coding.line_items if li.predicted_cost_center.strip()}
    if pairs is not None and units:
        owners = {pairs.company_of(unit) for unit in units}
        if "" not in owners and len(owners) == 1:
            return Decision(owners.pop(), BY_UNITS, "the company of the business units its lines are coded to")
        could |= owners - {""}
    return Decision(could_be=sorted(could))


def early(store: Any, vendor_name: Any, gst_number: Any = "", po_number: Any = "", text: str = "") -> str:
    """The invoice's company as its lines are first coded (``offline_coder``: the supplier's suggestions limited to
    its business units), by what is read before them: its PO, the names on the page and the supplier's history
    (``decide``'s (a) to (c)); "" with one company or none decided."""
    if store is None or not several(store) or not str(vendor_name or "").strip():
        return ""
    from types import SimpleNamespace

    read = SimpleNamespace(vendor_name=str(vendor_name), gst_hst_registration_number=str(gst_number or ""),
                           po_number=str(po_number or ""), company="", company_how="",
                           line_items=[])  # fmt: skip
    return decide(store, read, text).company  # type: ignore[arg-type]


def voucher_tax(store: Store, coding: InvoiceCoding) -> dict[str, str]:
    """A company not checked for Canadian tax: its voucher's tax area (``schema.COMPANY_TAX``), the PO line's (the one
    area of the lines of the order the invoice bills), else the supplier's default (JD Edwards' supplier master); ""
    when neither gives one (the voucher is held)."""
    from .po import pick_order

    number = po_key(coding.po_number)
    if number:
        vendor = vendor_key(coding.vendor_name)
        supplier = master_vendor_id(store.vendor_ids(), coding.vendor_name, coding.gst_hst_registration_number)
        order = pick_order(store.orders_numbered(number), vendor, supplier, coding.company)
        full = store.purchase_order(order["po_key"]) if order else None
        areas = {str(li.get("tax_area") or "").strip() for li in (full or {}).get("lines") or []} - {""}
        if len(areas) == 1:
            return {"tax_area": areas.pop(), "from": f"PO {coding.po_number.strip()}"}
    supplier = master_vendor_id(store.vendor_ids(), coding.vendor_name, coding.gst_hst_registration_number)
    info = store.vendor_info(supplier) if supplier else None
    area = str((info or {}).get("tax_area") or "").strip()
    return {"tax_area": area, "from": "the supplier's default" if area else ""}


def unknown_finding(decision: Decision, store: Any) -> tuple[str, str, str]:
    """``COMPANY_UNKNOWN``: the invoice's company is not decided; the companies it could be, when some are."""
    table = companies(store)
    named = [f"{c} {table[c].name}" if c in table else c for c in decision.could_be]
    could = f" (it could be {' or '.join(named)})" if named else ""
    return (ERROR, "COMPANY_UNKNOWN", f"Pick the company this invoice is for{could}: nothing on it decides one")


def pilot_finding(store: Any, company: str, settings_company: bool = False) -> tuple[str, str, str] | None:
    """``COMPANY_NOT_IN_PILOT``: with companies in the pilot (the chart's settings, D-272), an invoice of another one
    is held, whoever approves it (``invoice_findings.HARD_CODES``); None for one in the pilot, or with no pilot.
    ``settings_company``: the company is Settings → JD Edwards E1's (fewer than two companies: every invoice's,
    B-2683)."""
    pilot = store.chart_settings().pilot
    code = company_code(company)
    if not pilot or not code or code in pilot:
        return None
    table = companies(store)
    named = f"{code} {table[code].name}" if code in table else code
    fix = ("set the company of Settings → JD Edwards E1 if it is wrong" if settings_company
           else "pick the right company if it is wrong")  # fmt: skip
    return (ERROR, "COMPANY_NOT_IN_PILOT", f"Company {named} is not in the pilot (Settings → JD Edwards E1 → "
            f"Accounts AP posts to: {', '.join(pilot)}), so this invoice can't be approved here: {fix}, or have "
            "someone who may change the settings add it to the pilot")  # fmt: skip


def report_finding(store: Any, company: str) -> tuple[str, str, str] | None:
    """``COMPANY_NOT_IN_REPORT``: with report 9 loaded (several companies), a company the coding names that it does not
    list (a PO's, a business unit's, one kept from an earlier load): an error only Approve anyway with a reason passes;
    None for one it lists, or one the pilot names (D-272: the pilot may name a company report 9 does not have)."""
    code = company_code(company)
    if not code or code in companies(store) or code in store.chart_settings().pilot:
        return None
    return (ERROR, "COMPANY_NOT_IN_REPORT", f"Company {code} isn't in the companies report (JD Edwards files, card 9): "
            "pick the company this invoice is for, or approve anyway saying why")  # fmt: skip


def us_tax_finding(company: str, why: str) -> tuple[str, str, str]:
    """``US_TAX_NOT_CHECKED``: a company without the Canadian tax checks (``canadian``)."""
    return (WARNING, "US_TAX_NOT_CHECKED", f"Company {company} is not checked for Canadian tax ({why}): its tax "
            "is not checked here, confirm it is right")  # fmt: skip
