"""What each import of the app takes (D-234): one ``ImportSpec`` per file a person loads, so a page can say which report
to export, the columns it needs and the ones it may have, check a file's header row before anything is kept, and give
a blank template.

A spec is made from its reader's own tables (the alias tables, the required columns, the reader's way of finding
them: ``ImportSpec.find``), never from a copy of them: what a page says a file needs is what the reader refuses a file
without (``tests/erp/test_import_specs.py`` proves it for each). Column order, case, spaces and accents never matter
(``headers.match_columns``).

Entry points: ``SPECS`` (by key), ``check`` (a header row against a spec: ``HeaderCheck``), ``identify`` (which of
several reports a file is), ``header_row`` (the row the column names are on, under a grid's title rows), ``preview``
(the first rows under them), ``journal_head`` and ``journal_lines`` (the Transaction Journal's header and first lines:
a printed report, not a table) and ``template`` (a blank file of a spec's columns).

The Accounts tables (GL accounts, cost centers) have their columns picked on their page, guessed by words a header
contains (``ACCOUNT_HINTS``, the page's own guess: ``_account_find``).
"""

from __future__ import annotations

import csv
import io
import re
from collections import Counter
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..ai import history
from ..coding import banks, companies, po, terms, vendor_info
from ..coding.vendors import MASTER_ALIASES
from ..core import chart
from ..core.headers import header_key, jde_names, match_columns
from ..core.reference_data import MEALS_HEADERS, meals_column
from . import jde_settings, jde_worksheet, journal, registers, statements
from .jde_accounts import GL_MAP_ALIASES

XLSX, CSV, XLS, TXT = ".xlsx", ".csv", ".xls", ".txt"
SHEETS = (CSV, XLSX, XLS)  # what a page that reads any spreadsheet takes (web/files.SHEET_TYPES)
PREVIEW_ROWS = 5
HEAD_BYTES = 256 * 1024  # the start of a journal its preview is read from


@dataclass(frozen=True)
class Column:
    """One column of an import. ``header``: how the export (JD Edwards' own for its reports) names it, the one a
    template writes; ``names``: every header the reader accepts (its alias table, compared as ``header_key``);
    ``about``: for a required column why it is needed, for an optional one what it gives."""

    field: str
    label: str
    header: str
    names: tuple[str, ...] = ()
    required: bool = False
    about: str = ""

    @property
    def others(self) -> list[str]:
        """The other names accepted, beside ``header``: each once, as the reader's table writes it."""
        seen, out = {header_key(self.header)}, []
        for name in self.names:
            if header_key(name) not in seen:
                seen.add(header_key(name))
                out.append(name)
        return out


@dataclass(frozen=True)
class ImportSpec:
    """One import: what it is for, where its file comes from and its columns. ``find``: the reader's own way of
    finding them, {field: the file's header} of a header row; ``one_of``: optional fields of which at least one is
    needed (a statement's amount, credit or balance); ``no_template``: why no blank template is offered ("" when one
    is); ``report``: how a message names the file it must be."""

    key: str
    title: str
    purpose: str
    source: str
    report: str
    file_types: tuple[str, ...]
    columns: tuple[Column, ...]
    find: Callable[[list[str]], dict[str, str]]
    page: str
    more: str = ""
    one_of: tuple[str, ...] = ()
    no_template: str = ""
    fix: str = "Add the column to the file and load it again."
    jde: bool = False
    card: str = ""  # a JD Edwards report's card on the JD Edwards files page, as the page numbers it
    guess: bool = False  # its columns are guessed by a word in their header (the Accounts tables)
    # (field, header): a needed field whose header a known export has twice, the first being it (JD Edwards' order
    # details grid has "Long Description" twice, the line's first, D-264): taken, not left for a person to pick
    first_of: tuple[tuple[str, str], ...] = ()

    @property
    def required(self) -> tuple[Column, ...]:
        return tuple(c for c in self.columns if c.required)

    @property
    def optional(self) -> tuple[Column, ...]:
        return tuple(c for c in self.columns if not c.required)

    def column(self, name: str) -> Column:
        return next(c for c in self.columns if c.field == name)

    @property
    def accept(self) -> str:
        """The file box's ``accept``."""
        return ",".join(self.file_types)


def _columns(aliases: dict[str, Sequence[str]], required: Iterable[str], labels: dict[str, tuple[str, str]],
             about: dict[str, str] | None = None) -> tuple[Column, ...]:  # fmt: skip
    """A spec's columns, one for each field of the reader's ``aliases`` in its order, required ones first.
    ``labels``: {field: (label, the export's header)}; a field the reader has and ``labels`` does not is still a
    column, named by its first alias (``test_every_field_of_a_reader_has_its_own_label``)."""
    needed, about = set(required), about or {}
    made = [Column(f, *labels.get(f, (f.replace("_", " ").capitalize(), str(names[0]) if names else f)),
                   names=tuple(names), required=f in needed, about=about.get(f, ""))
            for f, names in aliases.items()]  # fmt: skip
    return tuple(sorted(made, key=lambda c: not c.required))


def _matcher(aliases: dict[str, Sequence[str]]) -> Callable[[list[str]], dict[str, str]]:
    return lambda headers: match_columns(headers, dict(aliases))


# --- JD Edwards' own exports (Setup → JD Edwards files) ------------------------------------------------------------

JDE_FIX = "Add the column to the grid in JD Edwards (every column shown), export it to Excel again and load that file."

CHART = ImportSpec(
    key="chart", card="1. Chart of accounts", title="Chart of accounts", jde=True, page="/jde-files#chart",
    purpose="Every account a line may be coded to, and which ones can be posted to.",
    source="JD Edwards' account master: list your company's accounts in its grid, every column shown, and export the "
           "grid to Excel (.xlsx).",
    more="Business unit, object and subsidiary make the account string (business unit.object.subsidiary). The "
         "posting edit code tells a title from an account that can be posted to. A newer export replaces the "
         "accounts of the companies it has (its Company column), the other companies' kept, so a chart split by "
         "company loads a file at a time; without that column it replaces the chart whole. What you changed by hand "
         "is kept.",
    report="JD Edwards' account master export (the chart of accounts)", file_types=(XLSX,), fix=JDE_FIX,
    columns=_columns(chart.CHART_ALIASES, (*chart.CHART_NEEDS, *chart.CHART_WANTS), {
        "business_unit": ("Business unit", "Business Unit"), "object": ("Object account", "Object Account"),
        "subsidiary": ("Subsidiary", "Subsidiary"), "description": ("Account description", "Account Description"),
        "lod": ("Level of detail", "L D"), "posting_edit": ("Posting edit code", "P E"),
        "company": ("Company", "Company"),
    }, {"business_unit": "the first part of every account string", "object": "the account itself",
        "company": "the account's company: a chart split by company loads a file at a time, each replacing only "
                   "its companies' accounts",
        **{f: f"{why[1][0].upper()}{why[1][1:]}" for f, why in chart.CHART_WANTS.items()},
        "description": "the account's name, shown wherever the account is", "lod": "the title an account sits "
        "under (its level of detail), so suggestions and the review screen group accounts as JD Edwards does"}),
    find=_matcher(chart.CHART_ALIASES),
)  # fmt: skip

UNITS = ImportSpec(
    key="units", card="2. Business units", title="Business units", jde=True, page="/jde-files#units",
    purpose="Which business units are open, what they are called and the company each belongs to.",
    source="JD Edwards' business unit master: export its grid to Excel (.xlsx), every column shown.",
    more="A newer export replaces the business units of the companies it has, the others kept; what you changed by "
         "hand is kept.",
    report="JD Edwards' business unit master export", file_types=(XLSX,), fix=JDE_FIX,
    columns=_columns(chart.BU_ALIASES, chart.BU_NEEDS, {
        "code": ("Business unit", "Job Number"), "name": ("Name", "Job Name"), "company": ("Company", "Co"),
        "type": ("Business unit type", "BU Ty"), "status": ("Status", "Job Sts"),
    }, {"code": "the business unit's number", "name": "the name shown beside its number",
        "company": "JD Edwards' company number, offered for the vouchers", "type": "kept with the business unit",
        "status": "a closed business unit is never offered"}),
    find=_matcher(chart.BU_ALIASES),
)  # fmt: skip

_SUPPLIER_LABELS = {
    "vendor_name": ("Supplier name", "Alpha Name"), "erp_id": ("Supplier number", "Address Number"),
    "gst": ("GST/HST number", "GST/HST Number"), "qst": ("QST number", "QST Number"),
    "pst": ("PST number", "PST Number"), "province": ("Province", "Province"),
    "terms": ("Payment terms", "Payment Terms"), "status": ("Status", "Status"),
    "hold": ("Payment hold", "Hold Payment Code"), "default_gl": ("Default GL account", "Default GL"),
}  # fmt: skip
_SUPPLIER_ABOUT = {
    "vendor_name": "the supplier's name", "erp_id": "JD Edwards' supplier number: what tells one supplier from "
    "another, two of one name too", "gst": "the GST/HST number an invoice's is compared with",
    "qst": "the QST number an invoice's is compared with", "pst": "the PST number an invoice's is compared with",
    "province": "where the supplier is, for the tax check", "terms": "the supplier's payment terms",
    "status": "a supplier on hold is flagged", "hold": "JD Edwards' payment hold: an invoice of a held supplier is "
    "flagged", "default_gl": "the account a new supplier's lines start with",
}  # fmt: skip
SUPPLIER_MASTER_NEEDS = ("vendor_name", "erp_id")  # (B-2275: the number is the supplier's identity)

SUPPLIER_MASTER = ImportSpec(
    key="supplier_master", card="3. Suppliers", title="Supplier master", jde=True, page="/jde-files#suppliers",
    purpose="Each supplier's number, name and payment hold.",
    source="JD Edwards' supplier master: export its grid to Excel (.xlsx), every column shown.",
    report="JD Edwards' supplier master export", file_types=(XLSX, CSV), fix=JDE_FIX,
    columns=_columns(MASTER_ALIASES, SUPPLIER_MASTER_NEEDS, _SUPPLIER_LABELS, _SUPPLIER_ABOUT),
    find=_matcher(MASTER_ALIASES),
)  # fmt: skip

# The vendor information list's headers as JD Edwards writes them (``vendor_info.COLUMNS`` is their ``header_key``).
_INFO_HEADERS = {
    "erp_id": "Internal Supplier Number", "vendor_name": "Company Name", "address1": "Address Line 1",
    "address2": "Address Line 2", "address3": "Address Line 3", "address4": "Address Line 4", "city": "City - CTY1",
    "state": "State", "country": "Country", "postal_code": "Postal Code", "tax_id": "Tax ID", "currency": "Cur Cod",
    "email": "JDE email address", "terms": "Payment Terms - A/P", "business_unit": "Business Unit",
    "business_unit_name": "Business Unit",
}  # fmt: skip
_INFO_ABOUT = {
    "erp_id": "the supplier number each row goes to", "vendor_name": "the supplier's name",
    "tax_id": "the tax ID: a Canadian business number fills and checks the invoice's GST/HST number",
    "currency": "the supplier's currency", "terms": "the payment terms in words", "address1": "the address, kept "
    "with the supplier", "email": "the supplier's e-mail address", "business_unit": "the business unit it is paid on",
}  # fmt: skip


def _info_find(headers: list[str]) -> dict[str, str]:
    return {f: headers[at] for f, at in vendor_info.positions(headers).items()}


VENDOR_INFO = ImportSpec(
    key="vendor_info", card="3. Suppliers", title="Vendor information list", jde=True, page="/jde-files#suppliers",
    purpose="Each supplier's tax ID, currency, payment terms, address and e-mail.",
    source="JD Edwards' vendor information list: export it to Excel (.xlsx) as it comes.",
    report="JD Edwards' vendor information list", file_types=(XLSX,), fix=JDE_FIX,
    columns=_columns({f: (vendor_info.COLUMNS[vendor_info.SECOND.get(f, f)],)
                      for f in (*vendor_info.COLUMNS, *vendor_info.SECOND)}, vendor_info.NEEDS,
                     {f: (vendor_info.LABELS.get(f, f.replace("_", " ").capitalize()), header)
                      for f, header in _INFO_HEADERS.items()}
                     | {"business_unit": ("Business unit number", "Business Unit"),  # (the first of the two: B-2480)
                        "business_unit_name": ("Business unit name", "Business Unit")}
                     | {f"address{n}": (f"Address line {n}", f"Address Line {n}") for n in range(1, 5)},
                     _INFO_ABOUT),
    find=_info_find,
)  # fmt: skip

# The Transaction Journal's header words (``journal.HEADER_WORDS``) as the report prints them; the document type's and
# the supplier number's are both "Number", under "Document" and "Address" on the row above.
_JOURNAL_LABELS = {
    "doc_type": ("Document type", "Number"), "company": ("Company", "Co"), "gl_date": ("G/L date", "G/L Date"),
    "account": ("Account number", "Account Number"), "description": ("Account description", "Description"),
    "debit": ("Debit", "Debit Amount"), "credit": ("Credit", "Credit Amount"), "remark": ("Remark", "Remark"),
    "supplier": ("Supplier number", "Number"), "name": ("Supplier name", "Name"),
}  # fmt: skip
_JOURNAL_NEEDS = ("account", "remark")  # the header row is the one naming both (``journal._positions``)
_JOURNAL_NUMBERS = tuple(journal._NUMBER_HEADERS)


def _journal_find(headers: list[str]) -> dict[str, str]:
    """{field: header} of a row of the journal, as ``journal._positions`` reads its header: {} when the row is not
    the header (it names no account number and remark)."""
    positions, at = journal._positions([[str(h) for h in headers]], loose=True)
    if at is None:
        return {}
    words = {f: (journal.HEADER_WORDS[f],) if f in journal.HEADER_WORDS else _JOURNAL_NUMBERS for f in positions}
    return {f: headers[i] for f, i in positions.items() if i < len(headers) and header_key(headers[i]) in words[f]}


JOURNAL = ImportSpec(
    key="journal", card="4. Past invoice coding", title="Past invoice coding", jde=True, page="/jde-files#journal",
    purpose="Lines of invoices already posted in JD Edwards (the Transaction Journal export), any period: the "
            "more history, the better the suggestions.",
    source="JD Edwards' Transaction Journal, report R09321: run it for document type PV (vouchers), your company and "
           "the G/L dates to learn from (a year or more), and save it as CSV.",
    more="The report is read as it prints: its title rows, page headers and totals are left out; the header's two "
         "rows (Document / Number, Address / Number) are read as one. Excel's Unicode Text (.txt) is read too.",
    report="JD Edwards' Transaction Journal (report R09321) saved as CSV", file_types=(CSV, TXT),
    fix="Run report R09321 again and save it as CSV as it comes.",
    columns=_columns({f: (*((journal.HEADER_WORDS[f],) if f in journal.HEADER_WORDS else _JOURNAL_NUMBERS),)
                      for f in journal.POSITIONS}, _JOURNAL_NEEDS, _JOURNAL_LABELS, {
        "account": "the account string each line was coded to", "remark": "AP's words on each line, learned with it",
        "supplier": "the supplier each voucher was for", "debit": "the line's amount", "credit": "the line's amount",
    }),
    find=_journal_find,
)  # fmt: skip

# The past invoice coding as a table (the account ledger, F0911, D-266): a row for each G/L line of a voucher.
GL_LINES = ImportSpec(
    key="gl_lines", card="4. Past invoice coding", title="Past invoice coding (G/L lines)", jde=True,
    page="/jde-files#journal",
    purpose="The G/L lines of the invoices already posted in JD Edwards, a row for each with one signed amount: the "
            "same learning as the Transaction Journal's.",
    source="A query of JD Edwards' account ledger (F0911) in ReportsNow or a like tool, ledger type AA, the "
           "vouchers' document types, exported to Excel (.xlsx) or CSV; split by company or year into several files "
           "if it is large.",
    more="The lines of one document (company, type and number) are one voucher. The payable lines JD Edwards adds may "
         "be left out of the export: a voucher without them is read as complete. Lines of another company than "
         "JD Edwards' company in Settings are left out. A second file adds to the first: a voucher in both counts "
         "once.",
    report="the G/L lines of JD Edwards' vouchers (its account ledger, F0911, as a table)", file_types=(XLSX, CSV),
    fix="Add the column to the query, export it again and load that file.", one_of=("account", "object"),
    columns=_columns(journal.FLAT_ALIASES, journal.FLAT_NEEDS, {
        "document": ("Document number", "Document Number"), "doc_type": ("Document type", "Doc Type"),
        "company": ("Document company", "Doc Co"), "gl_date": ("G/L date", "G/L Date"),
        "line": ("Line number", "Line Number"), "line_ext": ("Line extension", "Line Ext"),
        "account": ("Account number", "Account Number"), "business_unit": ("Business unit", "Business Unit"),
        "object": ("Object account", "Object Account"), "subsidiary": ("Subsidiary", "Subsidiary"),
        "description": ("Account description", "Account Description"), "amount": ("Amount", "Amount"),
        "remark": ("Remark", "Remark"), "explanation": ("Explanation", "Explanation"),
        "supplier": ("Supplier number", "Supplier Number"), "name": ("Supplier name", "Supplier Name"),
        "invoice": ("Invoice number", "Invoice Number"), "po": ("Purchase order", "Purchase Order"),
        "currency": ("Currency", "Curr Code"),
    }, {"document": "with the company and type, the voucher each line is of", "amount": "the line's amount, a "
        "debit plus and a credit minus", "supplier": "the supplier each voucher was for", "account": "the account "
        "string each line was coded to (business unit.object.subsidiary)", "object": "with the business unit and "
        "subsidiary, the account each line was coded to (instead of the account number)",
        "remark": "AP's words on each line, learned with it", "company": "lines of another company are left out",
        "doc_type": "tells two vouchers of one number apart"}),
    find=_matcher(journal.FLAT_ALIASES),
)  # fmt: skip

_VOUCHER_LABELS = {
    "document": ("Document number", "Document Number"), "doc_type": ("Document type", "Doc Type"),
    "company": ("Document company", "Doc Co"), "pay_item": ("Pay item", "Pay Item"),
    "an8": ("Supplier number", "Supplier Number"), "supplier_name": ("Supplier name", "Supplier Number Desc"),
    "invoice_number": ("Invoice number", "Invoice Number"), "invoice_date": ("Invoice date", "Invoice Date"),
    "gl_date": ("G/L date", "G/L Date"), "gross": ("Gross amount", "Gross Amount"),
    "foreign": ("Foreign amount", "Foreign Amount"), "currency": ("Currency", "Curr Code"),
    "batch": ("Batch number", "Batch Number"), "pay_ext": ("Pay item extension", "Pay Ext"),
}  # fmt: skip

VOUCHERS = ImportSpec(
    key="vouchers", card="5. Invoices already in JD Edwards", title="Invoices already in JD Edwards", jde=True,
    page="/jde-files#vouchers",
    purpose="The invoices entered in JD Edwards already: one entered again is caught as a duplicate.",
    source="JD Edwards' supplier ledger inquiry: export its grid (a row for each pay item) to Excel (.xlsx).",
    more="The pay items of one voucher are added up into it. Without the document company and type columns a "
         "voucher is told by its document number and supplier.",
    report="JD Edwards' voucher list (the supplier ledger inquiry's grid)", file_types=(XLSX, CSV), fix=JDE_FIX,
    columns=_columns(registers.VOUCHER_ALIASES, registers.VOUCHER_REQUIRED, _VOUCHER_LABELS, {
        "doc_type": "tells two vouchers of one number apart", "company": "tells two vouchers of one number apart",
        "pay_ext": "with the pay item, tells a pay item listed twice (two files that overlap) from another",
        "supplier_name": "the supplier's name when it is not in the supplier list", "invoice_date": "the invoice's "
        "date, shown with a duplicate", "gl_date": "the period the list covers", "foreign": "a voucher in another "
        "currency compared by its own amount", "currency": "the voucher's currency", "batch": "the batch it was "
        "entered in",
    }),
    find=_matcher(registers.VOUCHER_ALIASES),
)  # fmt: skip

PAYMENT_TERMS = ImportSpec(
    key="payment_terms", card="7. Payment terms", title="Payment terms", jde=True, page="/jde-files#terms",
    purpose="What each of JD Edwards' payment terms codes means: a supplier's code (N, for instance) counts as its "
            "discount and net days.",
    source="A query of JD Edwards' payment terms (F0014) in ReportsNow or a like tool, exported to Excel (.xlsx).",
    more="Optional. A newer file replaces the table whole.", report="JD Edwards' payment terms (F0014)",
    file_types=(XLSX, CSV), fix="Add the column to the query, export it again and load that file.",
    columns=_columns(terms.JDE_TERMS_ALIASES, terms.JDE_TERMS_NEEDS, {
        "code": ("Payment terms code", "Payment Terms Code"),
        "description": ("Description", "Payment Terms Description"),
        "discount_pct": ("Discount percent", "Discount Percent"), "discount_days": ("Discount days", "Discount Days"),
        "net_days": ("Net days to pay", "Net Days to Pay"),
    }, {"code": "the code a supplier has", "net_days": "when an invoice of the code is due",
        "discount_pct": "the early-payment discount", "discount_days": "until when the discount is offered"}),
    find=_matcher(terms.JDE_TERMS_ALIASES),
)  # fmt: skip

TAX_AREAS = ImportSpec(
    key="tax_areas", card="8. Tax areas", title="Tax areas", jde=True, page="/jde-files#areas",
    purpose="JD Edwards' tax areas and their rates, compared with the tax area Settings gives each province.",
    source="A query of JD Edwards' tax areas (F4008) in ReportsNow or a like tool, exported to Excel (.xlsx).",
    more="Optional. Only the areas in force today are kept; a newer file replaces them whole.",
    report="JD Edwards' tax areas (F4008)", file_types=(XLSX, CSV),
    fix="Add the column to the query, export it again and load that file.",
    columns=_columns(jde_settings.TAX_AREA_ALIASES, jde_settings.TAX_AREA_NEEDS, {
        "area": ("Tax area", "Tax Rate/Area"), "description": ("Description", "Description"),
        **{f"authority{n}": (f"Tax authority {n}", f"Tax Authority {n}") for n in range(1, 6)},
        **{f"rate{n}": (f"Tax rate {n}", f"Tax Rate {n}") for n in range(1, 6)},
        "effective": ("Effective date", "Effective Date"), "expired": ("Expired date", "Expired Date"),
    }, {"area": "the tax area a voucher names", "rate1": "with the other rates, the area's rate"}),
    find=_matcher(jde_settings.TAX_AREA_ALIASES),
)  # fmt: skip

COMPANIES = ImportSpec(
    key="companies", card="9. Companies", title="Companies", jde=True, page="/jde-files#companies",
    purpose="Each of JD Edwards' companies, its name, currency and country: with two or more, each invoice is told "
            "which company it is for, and only a company whose country is CA and currency CAD is checked for "
            "Canadian tax.",
    source="A query of JD Edwards' companies (F0010, with the country of each company's address) in ReportsNow or a "
           "like tool, exported to Excel (.xlsx).",
    more="Optional: one company needs none. A newer file replaces the list whole. JD Edwards' default company "
         "(00000) is left out. Each company's name joins your company's names, never read as a supplier.",
    report="JD Edwards' companies (F0010)", file_types=(XLSX, CSV),
    fix="Add the column to the query, export it again and load that file.",
    columns=_columns(companies.COMPANY_ALIASES, companies.COMPANY_NEEDS, {
        "company": ("Company", "Company"), "name": ("Company name", "Company Name"),
        "currency": ("Currency code", "Currency Code"), "country": ("Country", "Country"),
    }, {"company": "the company a voucher is keyed under", "name": "how an invoice names it",
        "currency": "its currency, for its invoices' and the Canadian tax checks",
        "country": "with its currency, whether it is checked for Canadian tax (CA and CAD)"}),
    one_of=companies.COMPANY_ONE_OF,
    find=_matcher(companies.COMPANY_ALIASES),
)  # fmt: skip

# --- The imports of any ERP's lists, on their own pages -----------------------------------------------------------

# The Accounts tables' columns are picked on their page (web/pages/accounts.py), first guessed by a word in the header.
ACCOUNT_HINTS = {
    "code": ("gl", "account", "code", "cost", "centre", "center", "saknr", "kostl"),
    "description": ("desc", "name", "title", "text"),
    "category": ("categ", "group", "type", "class"),
}


def _account_find(meals: bool) -> Callable[[list[str]], dict[str, str]]:
    """The Accounts tables' guess: for each field in turn, the header not taken yet with one of its words, the one
    with the fewest words of the other fields first, then the most of its own, then by its name: the same columns
    whatever their order. The description and the category go first: a header with one of their words and one of the
    code's ("Account Name", "Account Type") is theirs, the code's words being the commonest."""

    def words(header: str, hints: Sequence[str]) -> int:
        return sum(w in header.lower() for w in hints)

    def find(headers: list[str]) -> dict[str, str]:
        found: dict[str, str] = {}
        for f in ("description", "category", "code"):
            hints = ACCOUNT_HINTS[f]
            others = [w for g, more in ACCOUNT_HINTS.items() if g != f for w in more]
            named = [h for h in headers if h not in found.values() and words(h, hints)]
            if named:
                found[f] = min(named, key=lambda h: (words(h, others), -words(h, hints), h.casefold(), h))
        if meals and (column := meals_column(headers)):
            found["meals"] = column
        return found

    return find


# The headers the other imports read a GL account under, as their files write them: Past coding's and the GL map's
# "GL Code", a bank account's cash "GL Account", the Transaction Journal's "Account Number". A GL account list's code
# under one of them is found by its name, not guessed (B-2470); the column picked is the guess's as before.
GL_CODE_NAMES = ("GL Code", "GL Account", JOURNAL.column("account").header)


def _accounts(key: str, title: str, noun: str, meals: bool, code_names: Sequence[str] = ()) -> ImportSpec:
    hints: dict[str, Sequence[str]] = {f: tuple(f"…{w}…" for w in words) for f, words in ACCOUNT_HINTS.items()}
    hints["code"] = (*code_names, *hints["code"])
    if meals:
        hints["meals"] = MEALS_HEADERS
    return ImportSpec(
        key=key, title=title, page="/accounts", file_types=SHEETS, report=f"a list of {noun}s", guess=True,
        purpose=f"The {noun}s a line may be coded to.",
        source="Any list from your ERP or a spreadsheet, a row for each: its columns are picked on the page.",
        columns=_columns(hints, ("code",), {
            "code": ("Code", "Code"), "description": ("Description", "Description"), "category": ("Category",
            "Category"), "meals": ("Meals & entertainment", "Meals"),
        }, {"description": "the name shown beside the code", "category": "groups the list",
            "meals": "yes, x or M&E marks the 50% tax credit"}),
        find=_account_find(meals),
    )  # fmt: skip


GL_ACCOUNTS = _accounts("gl_accounts", "GL accounts", "GL account", meals=True, code_names=GL_CODE_NAMES)
COST_CENTERS = _accounts("cost_centers", "Cost centers", "cost center", meals=False)

BANK_ACCOUNTS = ImportSpec(
    key="bank_accounts", title="Bank accounts", page="/accounts?tab=bank", file_types=SHEETS,
    purpose="The bank accounts an invoice is paid from, by currency.",
    source="A spreadsheet of your bank accounts, a row for each (the template has the columns).",
    report="a list of bank accounts",
    columns=_columns(banks.ALIASES, ("code",), {
        "code": ("Code", "Code"), "name": ("Name", "Name"), "currency": ("Currency", "Currency"),
        "institution": ("Institution", "Institution"), "transit": ("Transit", "Transit"),
        "account_number": ("Account number", "Account Number"), "gl_account": ("Cash GL account", "GL Account"),
        "is_default": ("Default", "Default"), "active": ("Active", "Active"),
    }, {"currency": "CAD when blank", "gl_account": "the cash account the payment posts to",
        "is_default": "the account picked first for its currency", "active": "an account no longer used"}),
    find=_matcher(banks.ALIASES),
)  # fmt: skip

VENDOR_MASTER = ImportSpec(
    key="vendor_master", title="Vendor list", page="/vendors#import", file_types=SHEETS,
    purpose="Your suppliers with their numbers, tax numbers, terms and holds.",
    source="The vendor or supplier list of any ERP, a row for each: its columns are picked on the page.",
    report="a vendor list",
    columns=_columns(MASTER_ALIASES, ("vendor_name",), {**_SUPPLIER_LABELS, "vendor_name": ("Vendor name",
                     "Vendor Name"), "erp_id": ("Vendor ID", "Vendor ID")}, {**_SUPPLIER_ABOUT, "erp_id": "the "
                     "ERP's number for the vendor: what tells two vendors of one name apart"}),
    find=_matcher(MASTER_ALIASES),
)  # fmt: skip

PURCHASE_ORDERS = ImportSpec(
    key="purchase_orders", title="Purchase orders", page="/purchase-orders", file_types=SHEETS,
    card="6. Open purchase order lines",
    purpose="The open purchase orders' lines, matched to the invoices that bill them.",
    source="Your ERP's open purchase order lines, a row for each line. From JD Edwards: the open order lines (Open "
           "Order Inquiry, or the order detail of Work With Order Headers) exported to Excel, every column shown.",
    report="a list of purchase order lines", one_of=("quantity", "unit_price", "amount", "open_amount"),
    first_of=(("description", "Long Description"),),
    columns=_columns(po.COLUMN_ALIASES, po.REQUIRED_COLUMNS, {
        "po_number": ("PO number", "PO Number"), "order_type": ("Order type", "Or Ty"),
        "order_company": ("Order company", "Order Co"), "vendor_name": ("Vendor", "Vendor"),
        "supplier_number": ("Supplier number", "Address Number"),
        "line_number": ("Line number", "Line Number"), "description": ("Description", "Description"),
        "quantity": ("Quantity ordered", "Quantity"), "unit_price": ("Unit price", "Unit Price"),
        "amount": ("Line amount", "Amount"), "open_amount": ("Open amount", "Open Amount"),
        "open_quantity": ("Quantity open", "Quantity Open"), "uom": ("Unit of measure", "UM"),
        "received_quantity": ("Quantity received", "Received Quantity"), "gl_code": ("GL account", "GL Code"),
        "cost_center": ("Cost center", "Cost Center"), "account_number": ("Account number", "Account Number"),
        "subsidiary": ("Subsidiary", "Sub"),
        "status": ("Status", "Status"), "next_status": ("Next status", "Next Status"),
        "last_status": ("Last status", "Last Status"), "currency": ("Currency", "Curr"),
        "base_currency": ("Base currency", "Base Curr"),
        "foreign_open_amount": ("Foreign open amount", "Foreign Amount To Receive"),
        "foreign_unit_price": ("Foreign unit price", "Foreign Unit Cost"),
        "foreign_amount": ("Foreign line amount", "Original Ordered Foreign Amount"),
        "tax_flag": ("Taxable", "Tax Y/N"), "tax_code": ("Tax explanation code", "Tax Expl"),
        "tax_area": ("Tax rate/area", "Tax Rate/Area"),
    }, {"open_amount": "what is left to expense (alone, without a quantity, price or amount: the line's amount)",
        "received_quantity": "what was received, for the match",
        "gl_code": "the account the line is coded to (JD Edwards' object account)",
        "cost_center": "the cost center the line is coded to (JD Edwards' business unit)",
        "subsidiary": "the subsidiary of the object account",
        "status": "open or closed", "currency": "the PO's currency (without it, CAD)",
        "order_type": "JD Edwards' order type, named on a voucher match (without it, the one in Settings)",
        "order_company": "JD Edwards' order company, named on a voucher match (without it, the one in Settings)",
        "supplier_number": "the supplier's number (JD Edwards' address number)",
        "open_quantity": "what JD Edwards has open on the line, for the voucher match",
        "uom": "the line's unit of measure, for the voucher match",
        "account_number": "the account the line is coded to, business unit.object.subsidiary",
        "next_status": "JD Edwards' next status: 999 is closed", "last_status": "JD Edwards' last status: 980 is "
        "cancelled", "base_currency": "the company's currency: an order in another has its amounts in the foreign "
        "columns", "foreign_open_amount": "a foreign order's open amount, in its currency",
        "foreign_unit_price": "a foreign order's unit price, in its currency",
        "foreign_amount": "a foreign order's line amount, in its currency",
        "tax_flag": "JD Edwards' Tax Y/N of the line, typed on its voucher match row",
        "tax_code": "the line's tax explanation code, typed on its voucher match row",
        "tax_area": "the line's tax rate/area, typed on its voucher match row"}),
    find=_matcher(po.COLUMN_ALIASES),
)  # fmt: skip

STATEMENTS = ImportSpec(
    key="statements", title="Supplier statement", page="/statements", file_types=SHEETS,
    purpose="A supplier's statement, compared line by line with the invoices AP has.",
    source="The statement the supplier sent, as a spreadsheet: a row for each invoice or credit.",
    report="a supplier statement", one_of=("amount", "credit", "balance"),
    no_template="The supplier sends the statement: it is compared as it comes, its columns picked on the page.",
    columns=_columns(statements.COLUMN_ALIASES, ("number",), {
        "number": ("Invoice number", "Invoice Number"), "date": ("Date", "Date"), "amount": ("Amount", "Amount"),
        "credit": ("Credit", "Credit"), "balance": ("Open balance", "Balance"), "type": ("Type", "Type"),
    }, {"type": "tells an invoice from a payment or credit"}),
    find=_matcher(statements.COLUMN_ALIASES),
)  # fmt: skip

ERP_REGISTER = ImportSpec(
    key="erp_register", title="Invoices already in the ERP", page="/exports#register", file_types=SHEETS,
    purpose="Invoices entered in your ERP before or outside AP Coder: one entered again is caught as a duplicate.",
    source="Your ERP's list of AP invoices, a row for each (JD Edwards' voucher list is read as it comes).",
    report="a list of invoices",
    columns=_columns(registers.COLUMN_ALIASES, registers.REQUIRED, {
        "vendor_name": ("Vendor", "Vendor"), "invoice_number": ("Invoice number", "Invoice Number"),
        "invoice_date": ("Date", "Invoice Date"), "total": ("Total", "Total"),
    }, {"invoice_date": "shown with a duplicate"}),
    find=_matcher(registers.COLUMN_ALIASES),
)  # fmt: skip

PAST_CODING = ImportSpec(
    key="past_coding", title="Past coding", page="/learning#history", file_types=SHEETS,
    purpose="Lines coded before, by supplier: what the suggestions learn from.",
    source="Your ERP's invoice lines with the account each was coded to, a row for each line.",
    report="a list of coded invoice lines",
    columns=_columns(history.COLUMN_ALIASES, history.REQUIRED, {
        "vendor_name": ("Vendor", "Vendor"), "description": ("Line description", "Description"),
        "gl_code": ("GL account", "GL Code"), "cost_center": ("Cost center", "Cost Center"),
        "amount": ("Amount", "Amount"), "date": ("Date", "Date"),
    }, {"cost_center": "learned with the account", "amount": "learned with the account",
        "date": "newer lines count more"}),
    find=_matcher(history.COLUMN_ALIASES),
)  # fmt: skip

GL_MAP = ImportSpec(
    key="gl_map", title="GL map", page="/settings/jde#gl-map", file_types=SHEETS,
    purpose="Which JD Edwards account (business unit.object.subsidiary) each GL account and cost center posts to.",
    source="A spreadsheet, a row for each GL account (and cost center).", report="a GL map",
    columns=_columns(GL_MAP_ALIASES, ("gl_code",), {
        "gl_code": ("GL code", "GL Code"), "cost_center": ("Cost center", "Cost Center"),
        "bu": ("Business unit", "BU"), "obj": ("Object", "Object"), "sub": ("Subsidiary", "Subsidiary"),
        "sbl": ("Subledger", "Subledger"), "sblt": ("Subledger type", "Subledger Type"),
    }, {"cost_center": "a mapping for one cost center only", "bu": "JD Edwards' business unit",
        "obj": "JD Edwards' object account", "sub": "JD Edwards' subsidiary"}),
    find=_matcher(GL_MAP_ALIASES),
)  # fmt: skip

WORKSHEET_COLUMNS = {"id": ("ID", jde_worksheet.ID), "answer": ("Your answer", jde_worksheet.ANSWER)}


def _worksheet_find(headers: list[str]) -> dict[str, str]:
    stripped = {h.strip(): h for h in headers}
    return {f: stripped[header] for f, (_label, header) in WORKSHEET_COLUMNS.items() if header in stripped}


JDE_WORKSHEET = ImportSpec(
    key="jde_worksheet", title="JDE team's worksheet", page="/settings/jde", file_types=(XLSX,),
    purpose="The JD Edwards team's answers to the voucher setup questions.",
    source="Download the worksheet on this page, have the JDE team fill in “Your answer”, and load it back.",
    report="AP Coder's JDE worksheet (its Questions sheet)",
    no_template="The worksheet itself is the template: download it on this page.",
    fix=f"Download the worksheet again, fill in its “{jde_worksheet.ANSWER}” column and load that.",
    columns=_columns({f: (header,) for f, (_label, header) in WORKSHEET_COLUMNS.items()}, ("id", "answer"),
                     WORKSHEET_COLUMNS),
    find=_worksheet_find,
)  # fmt: skip

SPECS: dict[str, ImportSpec] = {spec.key: spec for spec in (
    CHART, UNITS, SUPPLIER_MASTER, VENDOR_INFO, JOURNAL, GL_LINES, VOUCHERS, PAYMENT_TERMS, TAX_AREAS, COMPANIES,
    GL_ACCOUNTS,
    COST_CENTERS, BANK_ACCOUNTS, VENDOR_MASTER, PURCHASE_ORDERS, STATEMENTS, ERP_REGISTER, PAST_CODING, GL_MAP,
    JDE_WORKSHEET,
)}  # fmt: skip
JDE_REPORTS = tuple(key for key, spec in SPECS.items() if spec.jde)
# The reports a file's columns name whole (``identify``): JD Edwards' companies only by ``company_list`` (a company and
# a name are any list's: the business units' grid has "Co" and "Company Name", D-267).
IDENTIFIED = tuple(key for key in JDE_REPORTS if key != "companies")


# --- A file's header row against a spec ------------------------------------------------------------------------------


def _text(value: Any) -> str:
    return "" if value is None else str(value).strip()


@dataclass(frozen=True)
class HeaderCheck:
    """A header row against a spec: ``found`` {field: the file's header}, the required columns ``missing``, the
    optional ones not there, ``none_of`` (the ``one_of`` columns when none is there) and the file's ``unused``
    headers, in the file's order."""

    spec: ImportSpec
    headers: tuple[str, ...]
    found: dict[str, str] = field(default_factory=dict)
    missing: tuple[Column, ...] = ()
    missing_optional: tuple[Column, ...] = ()
    none_of: tuple[Column, ...] = ()
    unused: tuple[str, ...] = ()
    twice: tuple[Column, ...] = ()  # needed columns whose header the file has more than once: which is it?
    guessed: tuple[str, ...] = ()  # fields found by a word in their header, not by a name read (``ImportSpec.guess``)

    @property
    def ok(self) -> bool:
        return not self.missing and not self.none_of and not self.twice

    def rows(self) -> list[dict[str, Any]]:
        """Each column of the spec, required first: {"column", "header" (the file's, "" when missing)}."""
        return [{"column": c, "header": self.found.get(c.field, "")} for c in self.spec.columns]

    def problem(self, other: ImportSpec | None = None, where: str = "") -> str:
        """Why the file is not loaded ("" when it can be): each missing column by its header and label, the report
        it belongs to and, when the file is ``other`` report, which one and ``where`` it is loaded (a file can have
        every column of this report and be another: the chart has a business unit column too)."""
        another = other is not None and other.key != self.spec.key
        if self.ok and not another:
            return ""
        load_it = f": load it under {where}" if where else ""
        if self.ok and other is not None:
            return f"Not loaded: this file is {other.report}, not {self.spec.report}{load_it}."
        if self.twice and not self.missing and not self.none_of:
            named = _and([f"“{self.found[c.field]}”" for c in self.twice])
            return (f"Not loaded: this file has more than one column named {named}, so which one is the "
                    f"{_and([_lower(c.label) for c in self.twice])} cannot be told. Delete the column that is not "
                    "the export's own (or rename it), then load the file again.")  # fmt: skip
        named = [f"“{c.header}” ({_lower(c.label)})" for c in self.missing]
        if self.none_of:
            named.append(" or ".join(f"“{c.header}”" for c in self.none_of))
        many = len(named) > 1
        said = (f"Not loaded: this file has no {_and(named)} column{'s' if many else ''}, which "
                f"{self.spec.report} has.")  # fmt: skip
        if another and other is not None:
            said += f" It looks like {other.report}{load_it}."
        else:
            said += " " + self.spec.fix.replace("the column", "the columns" if many else "the column")
        return said


def _lower(label: str) -> str:
    """A label inside a sentence: its first letter lower case, unless it starts a word in capitals ("GL code",
    "G/L date")."""
    return label if not label[1:2].islower() else label[:1].lower() + label[1:]


def _and(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


_MANGLED = re.compile(r"(.+)\.\d+")  # how pandas names the second column of a name the file has twice ("Alpha Name.1")


def _named(header: str, headers: set[str]) -> str:
    """A header as the file names it: "Alpha Name" for pandas' "Alpha Name.1" when "Alpha Name" is there too."""
    mangled = _MANGLED.fullmatch(header)
    return mangled.group(1) if mangled and mangled.group(1) in headers else header


def check(spec: ImportSpec, headers: Iterable[Any]) -> HeaderCheck:
    """``headers`` (a file's column names, in any order) against ``spec``, as its reader finds them. A needed column
    whose header the file has twice is ``twice`` (the reader would take the first, whichever it is), unless the spec
    reads both (the vendor information list's two "Business Unit" columns) or knows the first is it (``first_of``);
    for a spec whose columns are guessed by a word, the fields found by no name it reads are ``guessed``."""
    named = [_text(h) for h in headers]
    found = spec.find([h for h in named if h]) if any(named) else {}
    used = set(found.values())
    missing = tuple(c for c in spec.required if c.field not in found)
    optional = tuple(c for c in spec.optional if c.field not in found)
    none_of = tuple(spec.column(f) for f in spec.one_of) if spec.one_of and not set(spec.one_of) & set(found) else ()
    unused = tuple(h for h in named if h and h not in used)
    present = set(named)
    times = Counter(header_key(_named(h, present)) for h in named if h)
    taken = Counter(header_key(_named(h, present)) for h in found.values())
    first = {(f, header_key(h)) for f, h in spec.first_of}
    twice = tuple(c for c in spec.required if c.field in found
                  and times[key := header_key(_named(found[c.field], present))] > taken[key]
                  and (c.field, key) not in first)  # fmt: skip
    guessed = tuple(f for f, h in found.items() if spec.guess and header_key(h) not in {
        header_key(n) for n in (spec.column(f).header, *spec.column(f).names) if "…" not in n})  # fmt: skip
    return HeaderCheck(spec, tuple(named), found, missing, optional, none_of, unused, twice, guessed)


def identify(headers: Iterable[Any], keys: Iterable[str] = IDENTIFIED) -> ImportSpec | None:
    """Which of the reports ``keys`` a file with these column names is: the one with the most of its required columns
    there (a chart has a business unit column too, but more besides), then whole, then with the most columns found;
    None when that one is not whole (a chart without its posting edit column is a chart missing a column, not the
    business units) or nothing is found."""
    named = [_text(h) for h in headers]
    checks = [check(SPECS[key], named) for key in keys]
    best = max(checks, key=lambda c: (len(c.spec.required) - len(c.missing), c.ok, len(c.found)), default=None)
    return best.spec if best is not None and best.ok and best.found else None


# --- JD Edwards' own reports, told by the headers only JD Edwards writes --------------------------------------------


def _fields(table: str, prefix: str, *items: str) -> tuple[str, ...]:
    """JD Edwards' fields of a report's own table as a query tool titles them, with the table's prefix ("GMOBJ") or
    the table too ("F0901.GMOBJ"): no other table's (D-266)."""
    return tuple(name for name in jde_names(table, prefix, *items) if name not in items)


# Headers JD Edwards' grid of each report writes and another system's list does not (none a column of another report
# here), compared as ``header_key``; besides its reports, the grids of one voucher's screens, which nothing loads.
# Then each report as a query tool exports its table (ReportsNow, D-266): the build sheet's titles that are no other
# report's, its fields with their table's prefix, and the data items no other report here has, as a field alone.
JDE_MARKS: dict[str, tuple[str, ...]] = {
    "chart": ("Object Account", "L D", "P E", "Account ID", "Business Unit Description", "M C", "Taxable Account",
              "Posting Edit Code", "Level of Detail", "WCI Code", "Rt Cd", "Posting Edit", "Account Level of Detail",
              *_fields("F0901", "GM", "MCU", "OBJ", "SUB", "DL01", "LDA", "PEC", "AID", "CO", "CRCD"),
              "OBJ", "LDA", "PEC", "AID"),
    "units": ("Job Number", "Job Name", "BU Ty", "Job Sts", "Related BU", "Related Bu Description",
              "Address Number1", "Business Unit Type", "Posting Edit - Business Unit",
              *_fields("F0006", "MC", "MCU", "DL01", "CO", "STYL", "PECC", "LDM", "ADDS"), "STYL", "PECC", "LDM"),
    "supplier_master": ("Address Number", "Alpha Name", "Hold Payment Code", "Individual Tax ID", "Long Address",
                        "Industry Class", "Hold Orders Code", "C M", "Additional Tax ID",
                        "Additional Individual Tax ID", *_fields("F0101", "AB", "AN8", "ALPH", "TAX", "TX2", "AT1"),
                        *_fields("F0401", "A6", "HDPY", "CRRP", "TRAP", "TXA2", "EXR2"),
                        *_fields("F0116", "AL", "ADD1", "ADD2", "ADD3", "ADD4", "CTY1", "ADDS", "ADDZ", "CTR"),
                        *_fields("F01151", "EA", "EMAL"), "TX2", "AT1", "HDPY", "CRRP", "TRAP", "TXA2", "EXR2",
                        "ADD1", "ADD2", "CTY1", "ADDZ", "EMAL"),
    # (not "Cur Cod" or "Payment Terms - A/P": the build sheet titles the supplier master's columns so, D-266)
    "vendor_info": ("Internal Supplier Number", "Complete Address", "City - CTY1", "Check/ Itm Date",
                    "JDE email address", "Total Payment Count", "Pmt Trm", "DUNS Number"),
    "vouchers": ("Doc Co", "Pay Item", "Supplier Number Desc", "Pay Stat", "Pay Status Code", "Batch Number",
                 "Batch Type", "Batch Type Desc", "Foreign Open", "Doc Co (PO)", "PO Doc Type", "Pay Ext",
                 *_fields("F0411", "RP", "DOC", "DCT", "KCO", "SFX", "SFE", "AN8", "ALPH", "VINV", "DIVJ", "DGJ",
                          "DDJ", "AG", "AAP", "ACR", "CRCD", "ICU", "PST", "PO", "PDCT", "PKCO", "MCU"),
                 "SFX", "SFE", "DIVJ", "DDJ", "AAP", "ACR", "ICU", "PDCT", "PKCO"),
    "gl_lines": (*_fields("F0911", "GL", "DOC", "DCT", "KCO", "DGJ", "JELN", "EXTL", "ANI", "MCU", "OBJ", "SUB",
                          "AA", "EXA", "EXR", "AN8", "VINV", "PO", "CRCD"), "JELN", "EXTL", "EXA", "EXR"),
    "payment_terms": ("Payment Terms Description", "Discount Percent", "Discount Days", "Net Days to Pay",
                      "Description - Payment Terms", "Days - Net Days to Pay",
                      *_fields("F0014", "PN", "PTC", "PTD", "DCP", "DCD", "NDTP"), "PTC", "PTD", "DCP", "DCD", "NDTP"),
    "tax_areas": (*(f"Tax Authority {n}" for n in range(1, 6)), *(f"Tax Rate {n}" for n in range(1, 6)),
                  *_fields("F4008", "TA", "TXA1", "DL01", "EFDJ", "EFTJ", *(f"TA{n}" for n in range(1, 6)),
                           *(f"TXR{n}" for n in range(1, 6))),
                  "EFDJ", "EFTJ", *(f"TXR{n}" for n in range(1, 6))),
    # (not "Co", "Name", "Company Name" or "Country": other lists' too; the build sheet's and ReportsNow's titles:
    # ``company_list``, D-267)
    "companies": _fields("F0010", "CC", "CO", "NAME", "CRCD"),
    "gl_distribution": ("Explanation -Remark-", "Sub- ledger", "Subledger Description", "JE Line Number",
                        "Tax ExpCode", "TaxRate Area", "Track Taxes", "Alternate Home Business Unit",
                        "Employee Pool Grouping Code", "Job Pool Grouping Code", "Short Item No"),
    "pay_items": ("Pymt Terms", "Payee Number", "G/L Bank Account Number", "Amount To Distribute",
                  "Domestic Gross Amount", "Non-Recoverable Tax Amount", "Adjust Doc Ty", "Hdl Cod",
                  "Foreign Gross Amount", "G/L Credit Account ID"),
    "payment_history": ("Doc Pay Item", "Payment Amount", "Discount Taken", "Doc Type-M", "Exchange Rate"),
}  # fmt: skip
MARKS = 3  # a file with this many of a report's marks is that report, whichever columns it lacks
# JD Edwards' open purchase order lines (Work With Order Details, P4310, D-264; F4311 as a query exports it, D-266):
# loaded on the Purchase orders page and on JD Edwards files (card 6), never taken for one of the reports above for
# the columns it shares with them (Subledger, Tax Rate/Area, Short Item No: a voucher's G/L distribution's, B-2646).
ORDER_MARKS = ("Or Ty", "Order Co", "Quantity To Receive", "Amount To Receive", "Line Description",
               "Original Ordered Amount", "Foreign Amount To Receive", "Next Status", "Last Status",
               *_fields("F4311", "PD", "DOCO", "DCTO", "KCOO", "LNID", "AN8", "DSC1", "DSC2", "LNTY", "UOM", "UORG",
                        "UOPN", "UREC", "PRRC", "AEXP", "AOPN", "ANI", "OMCU", "MCU", "OBJ", "SUB", "TX", "EXR1",
                        "TXA1", "LTTR", "NXTR", "CRCD", "FRRC", "FAP", "FEA", "TRDJ"),
               "DOCO", "DCTO", "KCOO", "LNID", "DSC1", "LNTY", "UORG", "UOPN", "UREC", "PRRC", "AEXP", "AOPN",
               "LTTR", "NXTR", "FRRC", "TRDJ", "OMCU")  # fmt: skip
# The grids of one voucher in JD Edwards, as a message names them.
VOUCHER_GRIDS = {
    "gl_distribution": "the G/L distribution of one voucher (its account lines)",
    "pay_items": "the pay items of one voucher",
    "payment_history": "the payment history of one voucher",
}
_LEDGER = ("JD Edwards' vouchers are read from the Supplier Ledger Inquiry's grid, a row for each pay item of every "
           "voucher: export that grid to Excel")  # fmt: skip


def recognise(headers: Iterable[Any]) -> str | None:
    """Which of JD Edwards' reports (a key of ``SPECS``) or a voucher's grids (``VOUCHER_GRIDS``) a file with these
    column names is, whichever columns it lacks: the one with the most of its marks (``JDE_MARKS``), when it has
    ``MARKS`` of them, or has every column it needs (``identify``) and two of them. None for any other list: a vendor
    list of another system has a name and a number too, a cost center list a business unit; JD Edwards' open PO
    lines (``ORDER_MARKS``) are the Purchase orders page's."""
    named = [_text(h) for h in headers]
    keys = {header_key(h) for h in named if h}
    counts = {key: sum(header_key(mark) in keys for mark in marks) for key, marks in JDE_MARKS.items()}
    best = max(counts, key=lambda key: counts[key])
    if sum(header_key(mark) in keys for mark in ORDER_MARKS) >= max(MARKS, counts[best]):  # PO lines (B-2646)
        return None
    if counts[best] >= MARKS:
        return best
    if company_list(named):
        return "companies"
    whole = identify(named)
    return whole.key if whole is not None and counts.get(whole.key, 0) >= 2 else None


def company_list(headers: Iterable[Any]) -> bool:
    """Whether a file with these column names is JD Edwards' companies (report 9, D-267): a company, a name and a
    currency or a country ("Co", "Name", "Cur Cod"; "Company", "Company Name", "Country"; CCCO, CCNAME, CCCRCD,
    ALCTR); a company and a name alone are any list's, never this one's. Asked once the other reports' own marks have
    not told the file."""
    found = match_columns([_text(h) for h in headers if _text(h)], companies.COMPANY_ALIASES)
    return {"company", "name"} <= set(found) and bool(set(companies.COMPANY_ONE_OF) & set(found))


def order_lines(headers: Iterable[Any]) -> bool:
    """Whether a file with these column names is JD Edwards' open purchase order lines (``ORDER_MARKS``)."""
    keys = {header_key(_text(h)) for h in headers if _text(h)}
    return sum(header_key(mark) in keys for mark in ORDER_MARKS) >= MARKS


def workbook_report(headers: Iterable[Any]) -> str | None:
    """Which report a sheet of the master workbook is, by its column names (D-266): one of JD Edwards' reports
    (``recognise``: by their own headers; a voucher's grid too, which nothing loads), its open purchase order lines
    ("purchase_orders": ``order_lines``), or the G/L lines of its vouchers titled as the build sheet titles them,
    none a grid's (every column the account ledger's reader needs: ``journal.flat_header``); None for anything
    else, never a guess."""
    named = [_text(h) for h in headers]
    if (key := recognise(named)) is not None:
        return key
    if order_lines(named):
        return "purchase_orders"
    return "gl_lines" if journal.flat_header([named])[1] is not None else None


def elsewhere(key: str, card: str = "") -> str:
    """Why a file that is JD Edwards' report ``key`` (``recognise``) is not loaded here: what it is and the card
    of the JD Edwards files page it is loaded on (``card``: the card it was dropped on, when it is one)."""
    if key in VOUCHER_GRIDS:
        load = "here" if card == "vouchers" else f"on the JD Edwards files page, under {VOUCHERS.card}"
        what = f"this file is {VOUCHER_GRIDS[key]}, from a voucher's screen in JD Edwards"
        return f"Not loaded: {what}. {_LEDGER} and load it {load}."
    spec = SPECS[key]
    return f"Not loaded: this file is {spec.report}. It is loaded on the JD Edwards files page, under {spec.card}."


def header_row(rows: Sequence[Sequence[Any]], specs: Iterable[ImportSpec]) -> int:
    """Where the column names are among ``rows`` (a sheet's first ones): the first row that is one of ``specs`` whole,
    else the one with the most of their columns, else the first (a grid's title rows stand above its names)."""
    specs = list(specs)
    best, most = 0, 0
    for at, row in enumerate(rows):
        checks = [check(spec, row) for spec in specs]
        if any(c.ok and c.found for c in checks):
            return at
        count = max((len(c.found) for c in checks), default=0)
        if count > most:
            best, most = at, count
    return best


def preview(rows: Sequence[Sequence[Any]], at: int, used: Iterable[str] = (), most: int = PREVIEW_ROWS) -> dict:
    """The header row ``at`` of ``rows`` and the ``most`` first rows under it with a cell filled, as text: {"headers",
    "rows"}; ``used``: only these columns (in the file's order), when given."""
    headers = [_text(h) for h in rows[at]] if at < len(rows) else []
    wanted = set(used)
    keep = [i for i, h in enumerate(headers) if h and (not wanted or h in wanted)]
    shown = []
    for row in rows[at + 1 :]:
        cells = [_text(row[i]) if i < len(row) else "" for i in keep]
        if any(cells):
            shown.append(cells)
        if len(shown) == most:
            break
    return {"headers": [headers[i] for i in keep], "rows": shown}


def journal_head(data: bytes) -> tuple[list[list[str]], int | None]:
    """(the first rows of a Transaction Journal file, the header row's index or None): the header as the reader finds
    it (``journal._positions``), in the start of the file only. ``ValueError`` when it is no CSV."""
    start = data[:HEAD_BYTES]
    if len(data) > HEAD_BYTES:  # (cut after a line, an even number of bytes for UTF-16)
        start = start[: start.rfind(b"\n") + 1] or start
        start = start[: len(start) - len(start) % 2] if data.startswith((b"\xff\xfe", b"\xfe\xff")) else start
    rows = journal._rows(start)
    return rows, journal._positions(rows, loose=True)[1]


def journal_lines(rows: Sequence[Sequence[str]], at: int, most: int = PREVIEW_ROWS) -> dict:
    """The journal's columns found on its header row ``at`` (named by their labels: the report's header is split
    over two rows), in the report's order, and its first ``most`` lines with an account under it: {"headers",
    "rows"}."""
    positions = journal._positions([list(rows[at])], loose=True)[0]
    where = sorted((positions[f], JOURNAL.column(f).label) for f in JOURNAL.find(list(rows[at])))
    lines = [r for r in rows[at + 1 :] if chart.SEPARATOR in _text(r[positions["account"]] if positions["account"]
                                                                   < len(r) else "")][:most]  # fmt: skip
    return {"headers": [label for _i, label in where],
            "rows": [[_text(r[i]) if i < len(r) else "" for i, _l in where] for r in lines]}  # fmt: skip


# --- A blank template -------------------------------------------------------------------------------------------------


def template(spec: ImportSpec) -> tuple[bytes, str, str]:
    """(the file, its name, its type) of a blank template of ``spec``: its columns' headers in a row (an .xlsx); the
    Transaction Journal's two header rows at the report's positions (a CSV). ``ValueError`` for a spec that has
    none (``no_template`` says why)."""
    if spec.no_template:
        raise ValueError(spec.no_template)
    name = spec.title.lower().replace("'", "").replace(" ", "_")
    if spec is JOURNAL:
        return _journal_template(), f"{name}_template.csv", "text/csv"
    import openpyxl
    from openpyxl.styles import Font

    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Sheet1"
    sheet.append([c.header for c in spec.columns])
    for at, column in enumerate(spec.columns, start=1):
        sheet.cell(1, at).font = Font(bold=True)
        sheet.column_dimensions[openpyxl.utils.get_column_letter(at)].width = max(12, len(column.header) + 4)
    out = io.BytesIO()
    book.save(out)
    return out.getvalue(), f"{name}_template.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _journal_template() -> bytes:
    """The report's header as R09321 prints it: "Document" and "Address" over the two "Number" columns, the other
    names on the second row, each at its position (``journal.POSITIONS``)."""
    width = max(journal.POSITIONS.values()) + 1
    above, names = [""] * width, [""] * width
    above[journal.POSITIONS["doc_type"]], above[journal.POSITIONS["supplier"]] = "Document", "Address"
    for column in JOURNAL.columns:
        names[journal.POSITIONS[column.field]] = column.header
    text = io.StringIO()
    csv.writer(text, lineterminator="\r\n").writerows([[journal.REPORT, "Transaction Journal"], above, names])
    return text.getvalue().encode("utf-8-sig")
