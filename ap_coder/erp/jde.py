"""JD Edwards EnterpriseOne (E1) batch vouchers: F0411Z1 / F0911Z1 files for R04110ZA.

E1 loads vouchers from outside systems through two "Z" (interface) tables, which the Voucher Batch
Processor (R04110ZA, or R04110Z) turns into real vouchers (F0411 pay items and F0911 G/L lines):

* **F0411Z1**: one row per voucher pay item (supplier, invoice number, dates, gross / tax amounts,
  tax area and explanation code, currency, PO reference).
* **F0911Z1**: one row per G/L distribution line (account, amount, description).

AP Coder writes both as CSV files (plus, for PO-matched vouchers, an optional match file); the
customer's JDE team loads them into the Z-tables and runs R04110ZA (proof mode first). See
``docs/JDE_E1.md`` for the field-by-field mapping and the open questions.

How the distribution balances
-----------------------------
In E1 the tax explanation code decides which part of the tax the system posts by itself (through the
tax AAIs, e.g. PT / GT) and which part must already be in the G/L distribution:

* ``V`` (VAT: GST, HST, QST): all the tax is recoverable and posted by E1, so the distribution is the
  gross amount minus the tax.
* ``C`` (GST + seller-charged PST): the GST/HST part is posted by E1, the PST is an expense: the
  distribution is the gross minus the GST/HST, with the PST inside the expense lines.
* ``B`` (GST + self-assessed PST): the supplier charged GST only; E1 accrues the PST. The distribution
  is the gross minus the GST/HST.
* ``E`` (exempt) or blank: no tax handling, the distribution is the gross.

AP Coder's own GL distribution (``tax.build_gl_distribution``) has the same shape: recoverable taxes
are separate lines, non-recoverable taxes are added to the expense lines (or their own expense line).
The F0911Z1 lines are that distribution without the recoverable-tax lines, so ``validate`` checks
``sum(F0911Z1) == gross - recoverable tax for the code``. When the Tax setup and the JDE code
disagree (e.g. PST set up as recoverable in AP Coder but code C), the invoice is unbalanced and is
left out of the batch, instead of E1 posting the PST twice or not at all.

With the tax coded to G/L accounts (``AMOUNT_GL``, the default for a new setup, D-93) E1 posts no tax: the F0911Z1
lines are AP Coder's distribution unfiltered (each recoverable tax on its own line to its tax account), the voucher
goes with the gross, code E and no tax area, and ``sum(F0911Z1) == gross``.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import zipfile
from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from types import SimpleNamespace
from typing import Any

from ..coding.line_tax import exempt_of, legacy_basis
from ..coding.tax import CANADIAN_TAX_TYPES, OUTSIDE_CANADA, PLACE_OF_SUPPLY, PROVINCES, ZERO_DECIMAL_CURRENCIES
from ..coding.terms import terms_key
from ..coding.vendors import norm_invoice_number
from ..core.headers import header_key
from ..core.memory import refresh_vendor_key, vendor_key
from ..core.reference_data import UNASSIGNED

SETTING_KEY = "jde_e1"
FORMAT = "jde"
LABEL = "JD Edwards E1 (F0411Z1 / F0911Z1)"
# The two reasons an invoice is held back for its supplier number: the Exports page links them to Settings → JD
# Edwards E1 → Supplier address numbers (``an8_problem``).
NO_AN8 = "no JDE supplier number (AN8)"
BAD_AN8 = "is not a JDE address number"

AMOUNT_GROSS, AMOUNT_TAX, AMOUNT_GL = "gross", "tax", "gl"
AMOUNT_MODES = {
    AMOUNT_GL: "Tax coded to G/L accounts: the tax goes on its own G/L lines to your tax accounts, E1 computes none "
    "(VLAG, code E, no tax area)",
    AMOUNT_GROSS: "Gross amount only (VLAG): E1 calculates the tax from the tax area",
    AMOUNT_TAX: "Taxable / non-taxable / tax amounts (VLATXA, VLATXN, VLSTAM): the tax printed on the invoice",
}
# Saved settings from before D-93 (no amount mode in them, or one not known): what they were, the gross amount only.
SAVED_DEFAULT_MODE = AMOUNT_GROSS
IMPLIED, POINT = "implied", "point"
DECIMAL_MODES = {IMPLIED: "Implied decimals ($5.50 → 550)", POINT: "Decimal point ($5.50 → 5.50)"}
ACCOUNT_ANI, ACCOUNT_COLUMNS = "ani", "columns"
ACCOUNT_MODES = {
    ACCOUNT_ANI: "One account field (VNAM = 2, VNANI = BU.Object.Subsidiary)",
    ACCOUNT_COLUMNS: "Separate columns (VNMCU, VNOBJ, VNSUB)",
}
SEQUENTIAL, MATCH_HEADER = "sequential", "match_header"
EXEMPT_STATUSES = ("exempt", "zero_rated")  # a line's tax_status that E1 takes as exempt (D-64)
LINE_NUMBERING = {
    SEQUENTIAL: "Sequential: one pay item (VLEDLN 1), distribution lines 1, 2, 3…",
    MATCH_HEADER: "Match header: one pay item per distribution line (VNEDLN = VLEDLN)",
}
GL_DATE_INVOICE, GL_DATE_EXPORT = "invoice", "export"
GL_DATES = {GL_DATE_INVOICE: "The invoice date", GL_DATE_EXPORT: "The day the batch is exported"}

# Which taxes E1 itself posts (so they are NOT in the distribution), by tax explanation code.
RECOVERABLE_BY_CODE: dict[str, frozenset[str]] = {
    "V": frozenset({"GST", "HST", "QST", "PST"}),
    "C": frozenset({"GST", "HST"}),
    "B": frozenset({"GST", "HST"}),
    "E": frozenset(),
    "": frozenset(),
}
PST_PROVINCES = ("BC", "SK", "MB")
NO_DECIMAL_CURRENCIES = dict.fromkeys(sorted(ZERO_DECIMAL_CURRENCIES), 0)

HEADER_COLUMNS = [
    "VLEDUS", "VLEDBT", "VLEDTN", "VLEDLN", "VLEDSP", "VLEDTC", "VLEDTR",
    "VLDCT", "VLCO", "VLMCU", "VLAN8",
    "VLVINV", "VLDIVJ", "VLDGJ", "VLDSVJ",
    "VLAG", "VLATXA", "VLATXN", "VLSTAM", "VLTXA1", "VLEXR1",
    "VLCRRM", "VLCRCD", "VLCRR", "VLACR", "VLCTXA", "VLCTXN", "VLCTAM",
    "VLPTC", "VLDDJ", "VLPST",
    "VLPO", "VLPDCT", "VLPKCO", "VLRMK",
]  # fmt: skip
DIST_COLUMNS = [
    "VNEDUS", "VNEDBT", "VNEDTN", "VNEDLN", "VNEDSP", "VNEDTC", "VNEDTR",
    "VNCO", "VNDGJ", "VNDCT",
    "VNAM", "VNANI", "VNMCU", "VNOBJ", "VNSUB", "VNSBL", "VNSBLT",
    "VNLT", "VNAA", "VNCRCD", "VNACR", "VNEXA", "VNEXR", "VNTXA1", "VNEXR1",
]  # fmt: skip
# PO receipt-match lines: the layout is NOT a standard Oracle table; to be confirmed with the JDE team.
MATCH_COLUMNS = [
    "EDUS", "EDBT", "EDTN", "EDLN", "DOCO", "DCTO", "KCOO", "LNID", "LITM", "DSC1", "UORG", "PRRC", "AEXP", "CRCD",
]  # fmt: skip
UNCONFIRMED = (
    "the PO-matched extra columns (e.g. VLATFLG, VLPSTE) and the whole F0411Z1T match file",
    "VNEDLN numbering (sequential, or matching VLEDLN)",
    "VLEDLN / VNEDLN as written (1, 2, 3): E1 shows EDLN with display decimals, so a direct table load of 1 may "
    "read as 0.001 (1000 needed) where a table conversion would not",
    "the tax area names (ON-HST, QC-GSTQST, …) and explanation codes",
    "VLCRR (exchange rate): left blank when no rate is set, so E1 takes it from the exchange-rate table",
    "VLGLBA (G/L bank account), when set per Pay from account: the value E1 expects there (the bank account's short "
    "account ID) and whether R04110ZA keeps it or takes the AAI PB bank",
)

HEADER_FILE, DIST_FILE, MATCH_FILE = "F0411Z1.csv", "F0911Z1.csv", "F0411Z1T.csv"
# The text columns filled from invoice text, at their Z-table widths.
TEXT_WIDTHS = {"VLVINV": 25, "VLRMK": 30, "VNEXA": 30, "VNEXR": 30, "DSC1": 30}
E1_YEARS = range(1900, 2900)  # what CYYDDD holds: century digit 0 (1900s) to 9 (2800s)


@dataclass
class TaxArea:
    tax_area: str
    code: str


def default_tax_map() -> dict[str, TaxArea]:
    """Province of supply → (tax area, explanation code). The area names are placeholders: each E1
    customer names its tax areas (F4008) its own way."""
    out: dict[str, TaxArea] = {}
    for p in PROVINCES:
        if p in ("ON", "NB", "NL", "NS", "PE"):
            out[p] = TaxArea(f"{p}-HST", "V")
        elif p == "QC":
            out[p] = TaxArea("QC-GSTQST", "V")
        elif p in PST_PROVINCES:
            out[p] = TaxArea(f"{p}-GSTPST", "C")
        else:  # AB and the territories: GST only
            out[p] = TaxArea(f"{p}-GST", "V")
    out[OUTSIDE_CANADA] = TaxArea("", "")
    return out


# Payment terms → JD Edwards payment terms code (VLPTC, D-91): example rows, offered pre-filled in Settings → JD Edwards
# E1 until the pilot tester saves the table, each to check against the company's own codes (P0014 / F0014).
EXAMPLE_TERMS_CODES = {
    "Due on receipt": "Z", "Net 10": "N10", "Net 15": "N15", "Net 30": "N30", "Net 45": "N45", "Net 60": "N60",
    "Net 90": "N90", "1/10 Net 30": "110", "2/10 Net 30": "210", "Net 30 EOM": "E30",
}  # fmt: skip
# Where a voucher's terms code comes from (``terms_code``); a credit memo is sent without one (B-896).
TERMS_TABLE, TERMS_DEFAULT, TERMS_CREDIT = "table", "default", "credit"
PTC_WIDTH = 3  # VLPTC


@dataclass
class GlMap:
    """AP Coder GL code (+ cost center, blank = any) → E1 business unit / object / subsidiary. A blank
    BU or object is filled by the default rule (BU = cost center or default BU, object = GL code)."""

    gl_code: str
    cost_center: str = ""
    bu: str = ""
    obj: str = ""
    sub: str = ""
    sbl: str = ""
    sblt: str = ""


@dataclass
class JdeSettings:
    company: str = "00001"
    user: str = "APCODER"
    document_type: str = "PV"
    credit_document_type: str = ""  # blank: credit notes use the document type above (negative amounts)
    batch_prefix: str = "APC"
    amount_mode: str = AMOUNT_GL  # a new setup (D-93); saved settings keep theirs (``from_json``)
    decimals: str = IMPLIED
    account_mode: str = ACCOUNT_ANI
    line_numbering: str = SEQUENTIAL
    po_matched: bool = False
    po_extra_columns: dict[str, str] = field(default_factory=lambda: {"VLATFLG": "", "VLPSTE": ""})
    po_document_type: str = "OP"
    po_company: str = ""  # blank: the company above
    send_po_reference: bool = True
    domestic_currency: str = "CAD"
    currencies: list[str] = field(default_factory=lambda: ["CAD", "USD"])
    header_bu: str = ""
    pay_status: str = "A"
    payment_terms_code: str = ""  # the code for terms not in ``terms_codes``; blank: E1 takes the supplier's terms
    # The meaning of the printed terms (``terms.terms_key``: "Net 30", "2/10 Net 30"...) → the company's code (D-91).
    terms_codes: dict[str, str] = field(default_factory=dict)
    send_due_date: bool = True
    gl_date: str = GL_DATE_INVOICE
    default_bu: str = ""
    bu_from_cost_center: bool = True
    use_default_rule: bool = True
    gl_map: list[GlMap] = field(default_factory=list)
    tax_map: dict[str, TaxArea] = field(default_factory=default_tax_map)
    self_assessed_code: str = "B"
    exempt_code: str = "E"
    dist_tax_columns: bool = False
    an8_overrides: dict[str, str] = field(default_factory=dict)  # vendor key → address number
    # Pay from account code (our bank accounts, D-66) → VLGLBA, the G/L bank account E1 pays the voucher from, as the
    # JDE team gives it (E1 keeps the short account ID there). Empty: no VLGLBA column, E1 takes the AAI PB bank.
    bank_glba: dict[str, str] = field(default_factory=dict)
    answers: dict[str, str] = field(default_factory=dict)  # the JDE team's answers that are not settings (worksheet)

    def to_json(self) -> str:
        return json.dumps(asdict(self), sort_keys=True)

    @classmethod
    def from_json(cls, text: str | None) -> JdeSettings:
        """Saved settings; anything missing or unreadable falls back to the default."""
        try:
            data = json.loads(text or "{}")
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}
        base = cls()
        known = set(asdict(base))
        values: dict[str, Any] = {k: v for k, v in data.items() if k in known and k not in ("gl_map", "tax_map")}
        out = cls(**{**{k: getattr(base, k) for k in known if k not in ("gl_map", "tax_map")}, **values})
        out.gl_map = [
            GlMap(**{k: str(r.get(k) or "") for k in GlMap.__dataclass_fields__})
            for r in data.get("gl_map") or []
            if isinstance(r, dict) and r.get("gl_code")
        ]
        tax_map = default_tax_map()
        for prov, row in (data.get("tax_map") or {}).items():
            if isinstance(row, dict):
                tax_map[prov] = TaxArea(str(row.get("tax_area") or ""), str(row.get("code") or ""))
        out.tax_map = tax_map
        if not isinstance(out.answers, dict):
            out.answers = {}
        out.answers = {str(k): str(v) for k, v in out.answers.items()}
        if not isinstance(out.bank_glba, dict):
            out.bank_glba = {}
        out.bank_glba = {str(k).strip().upper(): str(v).strip() for k, v in out.bank_glba.items()
                         if str(k).strip() and str(v).strip()}  # fmt: skip
        if not isinstance(out.terms_codes, dict):
            out.terms_codes = {}
        out.terms_codes = {str(k): str(v).strip().upper() for k, v in out.terms_codes.items()
                           if str(k).strip() and str(v).strip()}  # fmt: skip
        if not isinstance(out.an8_overrides, dict):
            out.an8_overrides = {}
        # Vendor keys saved under older vendor-matching rules, as today's rules give them ("energir sec" -> "energir").
        out.an8_overrides = {refresh_vendor_key(str(k)): v for k, v in out.an8_overrides.items()}
        saved = str(text or "").strip() not in ("", "{}")  # a setup saved (or unreadable), not a new one
        if saved and "amount_mode" not in data or out.amount_mode not in AMOUNT_MODES:
            out.amount_mode = SAVED_DEFAULT_MODE
        if out.decimals not in DECIMAL_MODES:
            out.decimals = IMPLIED
        if out.account_mode not in ACCOUNT_MODES:
            out.account_mode = ACCOUNT_ANI
        if out.line_numbering not in LINE_NUMBERING:
            out.line_numbering = SEQUENTIAL
        if out.gl_date not in GL_DATES:
            out.gl_date = GL_DATE_INVOICE
        return out

    @property
    def tax_on_gl(self) -> bool:
        """The tax coded to G/L accounts (D-93): F0911Z1 is AP Coder's distribution, tax lines included."""
        return self.amount_mode == AMOUNT_GL

    @property
    def gl_tax_code(self) -> str:
        """VLEXR1 when the tax is coded to G/L accounts: the code for no tax handling (E), to confirm with the JDE
        administrator (R04110ZA with it and no tax area posts the G/L lines as sent)."""
        return self.exempt_code.strip().upper() or "E"

    @property
    def company_code(self) -> str:
        """VLCO: five characters, zero-padded ("1" → "00001")."""
        text = self.company.strip()
        return text.zfill(5) if text.isdigit() else text


def load(store: Any) -> JdeSettings:
    return JdeSettings.from_json(store.get_setting(SETTING_KEY))


def save(store: Any, settings: JdeSettings, actor: str | None = None) -> bool:
    """Save to the store's settings table; True when something changed (and is logged)."""
    text = settings.to_json()
    if text == store.get_setting(SETTING_KEY):
        return False
    store.set_setting(SETTING_KEY, text, actor=actor)
    store.log_event("settings_changed", actor=actor, detail={"keys": [SETTING_KEY]})
    return True


def settings_problems(settings: JdeSettings) -> list[str]:
    """What stops any export (the same for every invoice)."""
    out = []
    if not settings.company.strip().isdigit() or len(settings.company.strip()) > 5:
        out.append("Company (VLCO) must be up to 5 digits, e.g. 00001.")
    if not settings.user.strip() or len(settings.user.strip()) > 10:
        out.append("User ID (VLEDUS) must be 1 to 10 characters.")
    if len(settings.batch_prefix.strip()) > 9:
        out.append("Batch prefix must be 9 characters or fewer (the batch number VLEDBT is 15 at most).")
    if len(settings.document_type.strip()) not in (0, 2):
        out.append("Document type (VLDCT) must be 2 characters, e.g. PV, or blank.")
    if not re.fullmatch(r"[A-Z]{3}", settings.domestic_currency.strip().upper()):
        out.append("Domestic currency must be a 3-letter code, e.g. CAD.")
    return out


# --- Formats ---------------------------------------------------------------------------------------------


def julian(value: dt.date | dt.datetime | str) -> int:
    """E1 "Julian" date CYYDDD: C = century (0 = 1900s, 1 = 2000s), YY = year, DDD = day of the year.
    2026-10-09 → 126282; 1999-12-31 → 99365 (099365)."""
    if isinstance(value, str):
        value = dt.date.fromisoformat(value.strip()[:10])
    if isinstance(value, dt.datetime):
        value = value.date()
    if value.year not in E1_YEARS:
        raise ValueError(f"{value} is outside what JD Edwards dates hold (1900 to 2899)")
    return (value.year - 1900) * 1000 + value.timetuple().tm_yday


def jde_amount(x: float | int | str | Decimal, implied: bool = True, decimals: int = 2) -> int | str:
    """An amount the way E1 Z-files take it: implied decimals by default ($5.50 → 550), or with a decimal
    point ("5.50"). Rounded half away from zero, on the decimal value (2.675 → 268)."""
    d = Decimal(str(x)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP)
    if implied:
        return int(d.scaleb(decimals))
    return f"{d:.{decimals}f}"


def _cents(x: Any) -> int:
    try:
        return int(Decimal(str(x or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP) * 100)
    except ArithmeticError:
        return 0


def _units(x: Any, decimals: int = 2) -> int:
    """An amount in cents, rounded to what the currency can hold (whole yen: a multiple of 100)."""
    try:
        return int(Decimal(str(x or 0)).quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP) * 100)
    except ArithmeticError:
        return 0


def _split(total: int, weights: list[int], unit: int = 1) -> list[int]:
    """``total`` cents split in proportion to ``weights``, in steps of ``unit`` cents (100 for a currency without
    cents). Exact: the remainder goes to the largest."""
    if not weights:
        return []
    base = sum(weights)
    if base == 0:
        return [total] + [0] * (len(weights) - 1)
    shares = [int(Decimal(total * w) / Decimal(base * unit)) * unit for w in weights]
    biggest = max(range(len(weights)), key=lambda i: abs(weights[i]))
    shares[biggest] += total - sum(shares)
    return shares


def _date(value: Any) -> dt.date | None:
    try:
        return dt.date.fromisoformat(str(value or "").strip()[:10])
    except ValueError:
        return None


def _norm(value: Any) -> str:
    return str(value or "").strip().upper()


# --- Supplier numbers and accounts ---------------------------------------------------------------------------

VendorLookup = Mapping[str, str] | Callable[[dict[str, Any]], str] | None


def resolve_an8(final: dict[str, Any], vendor_lookup: VendorLookup, settings: JdeSettings) -> str:
    """The supplier's address number: Settings → JD Edwards first, then the vendor master's ERP ID
    (``Store.vendor_ids()``: by name, else by GST/HST number) or a callable."""
    from .exports import _vendor_id

    override = settings.an8_overrides.get(vendor_key(final.get("vendor_name") or ""))
    if override:
        return str(override).strip()
    if callable(vendor_lookup):
        return str(vendor_lookup(final) or "").strip()
    return _vendor_id(final, dict(vendor_lookup or {})).strip()


def account_for(gl_code: str, cost_center: str, settings: JdeSettings) -> GlMap | None:
    """The E1 account of an AP Coder GL code (+ cost center), or None when it is not mapped."""
    gl, cc = _norm(gl_code), _norm(cost_center)
    cc = "" if cc == UNASSIGNED else cc  # a cost center still to pick is not a business unit (B-1211)
    if not gl or gl == UNASSIGNED:  # a line still to code has no object account (B-830)
        return None
    exact = next((m for m in settings.gl_map if _norm(m.gl_code) == gl and _norm(m.cost_center) == cc), None)
    row = exact or next((m for m in settings.gl_map if _norm(m.gl_code) == gl and not _norm(m.cost_center)), None)
    if row is None and not settings.use_default_rule:
        return None
    row = row or GlMap(gl_code)
    bu = row.bu.strip() or ((cc if settings.bu_from_cost_center and cc else "") or settings.default_bu.strip())
    obj = row.obj.strip() or gl
    if not bu or not obj:
        return None
    return GlMap(gl_code, cost_center, bu, obj, row.sub.strip(), row.sbl.strip(), row.sblt.strip())


def accounts_without_bu(settings: JdeSettings, gl_codes: Iterable[str], tax_codes: Iterable[str],
                        cost_centers: bool) -> list[str]:  # fmt: skip
    """The accounts a JD Edwards export would hold invoices back for, having no BU.Object for them: the tax accounts
    (their lines carry no cost center) and, unless each line's cost center gives the BU (``bu_from_cost_center`` with a
    cost center list), the GL accounts. None with a Default BU and the default rule (B-1300)."""
    if settings.use_default_rule and settings.default_bu.strip():
        return []
    needed = list(tax_codes) + ([] if settings.bu_from_cost_center and cost_centers else list(gl_codes))
    return [code for code in dict.fromkeys(c.strip() for c in needed if c.strip())
            if account_for(code, "", settings) is None]  # fmt: skip


def ani(account: GlMap) -> str:
    """VNANI in format 2: BU.Object.Subsidiary ("CC400.6010", "CC400.6010.01")."""
    return f"{account.bu}.{account.obj}" + (f".{account.sub}" if account.sub else "")


# --- One voucher ----------------------------------------------------------------------------------------------


@dataclass
class _Voucher:
    inv_id: int
    final: dict[str, Any]
    problems: list[str]
    codes: list[tuple[str, int | None]] = field(default_factory=list)  # each problem's check code and invoice line
    currency: str = "CAD"
    foreign: bool = False
    decimals: int = 2
    province: str = ""
    tax_area: str = ""
    tax_code: str = ""
    gross: int = 0  # cents
    stam: int = 0
    atxa: int = 0
    recoverable: int = 0
    charged: list[dict[str, Any]] = field(default_factory=list)  # Canadian tax lines with an amount
    recoverable_types: frozenset[str] = frozenset()
    entries: list[dict[str, Any]] = field(default_factory=list)  # distribution lines (cents in "cents")
    po_matched: bool = False
    # The GST/HST/QST on meals not claimable (D-90): in the expense lines, so E1 must not post it as recoverable.
    meals: int = 0


def _place_of_supply(final: dict[str, Any]) -> str:
    for key in ("ship_to_province", PLACE_OF_SUPPLY, "supplier_province"):  # printed, worked out, the vendor's
        prov = _norm(final.get(key))
        if prov in PROVINCES:
            return prov
    if OUTSIDE_CANADA in (_norm(final.get("ship_to_province")), _norm(final.get("supplier_province"))):
        return OUTSIDE_CANADA
    return ""


def _distribution(final: dict[str, Any]) -> list[dict[str, Any]]:
    # Not stored (an old invoice): rebuilt with the default tax treatments (``exports.distribution``); recoverable
    # taxes are dropped from the F0911Z1 lines anyway, so their GL accounts do not matter here.
    from .exports import distribution

    return distribution(final)


def _is_recoverable_entry(e: dict[str, Any]) -> bool:
    return e.get("kind") == "tax" and abs(float(e.get("non_recoverable_tax") or 0)) < 0.005


def _entry_tax(e: dict[str, Any]) -> str:
    """The tax of a tax distribution line ("HST"), from its description ("HST ON 13% (recoverable)")."""
    words = str(e.get("description") or "").split()
    return words[0].upper() if words else ""


def sent_due(final: dict[str, Any], invoice: dict[str, Any] | None = None) -> dt.date | None:
    """The due date a voucher sends (VLDDJ, B-924): printed on the invoice, or worked out from the terms printed; None
    when there are none (or none understood), so E1 works it out from the supplier's own terms instead of being told
    AP Coder's default days to pay. A credit memo: its invoice date, as E1 needs one."""
    from ..coding.terms import payment

    invoice_date = _date(final.get("invoice_date"))
    if _negative(final.get("grand_total")):
        return invoice_date
    printed = _date(final.get("due_date"))
    if printed is not None:
        return printed
    if payment(final).source != "terms":  # no terms printed, or not understood: the due date would be a guess
        return None
    return _date((invoice or {}).get("due_date")) or payment(final).due


def terms_code(final: dict[str, Any], settings: JdeSettings) -> tuple[str, str, str]:
    """(VLPTC, the meaning of the printed terms, where the code comes from) of a voucher (D-91): the code of the
    printed terms' meaning in the table ("30 days" and "N30" are "Net 30"), else the default code, else blank (E1
    takes the supplier's own terms). A credit memo: blank, as its due date is not sent: a code can carry a discount
    (2/10 Net 30) that E1 would work out on the credit (B-896; to confirm with the JDE team, D-91)."""
    key = terms_key(str(final.get("payment_terms") or ""))
    if _negative(final.get("grand_total")):
        return "", key, TERMS_CREDIT
    code = settings.terms_codes.get(key, "").strip() if key else ""
    if code:
        return code, key, TERMS_TABLE
    default = settings.payment_terms_code.strip()
    return (default, key, TERMS_DEFAULT) if default else ("", key, "")


def terms_unmapped(final: dict[str, Any], settings: JdeSettings) -> str:
    """The meaning of the printed terms when the terms table is in use and has no code for them, nor a default: ""
    otherwise (no table: E1 takes the supplier's own terms, as before D-91; terms not understood: TERMS_NOT_FOUND)."""
    code, key, source = terms_code(final, settings)
    return key if settings.terms_codes and key and not code and source != TERMS_CREDIT else ""


def an8_problem(reason: str) -> bool:
    """Whether a reason ``validate`` gives is about the supplier's address number (set in Settings → JD Edwards E1)."""
    return reason.startswith(NO_AN8) or BAD_AN8 in reason


def _tax_area_code(final: dict[str, Any], settings: JdeSettings) -> tuple[list[dict[str, Any]], str, str, str]:
    """The Canadian tax lines charged, the province of supply, and the JDE tax area and explanation code for it."""
    charged = [
        t for t in final.get("tax_lines") or []
        if t.get("tax_type") in CANADIAN_TAX_TYPES and abs(float(t.get("tax_amount") or 0)) > 0.004
    ]  # fmt: skip
    types = {t["tax_type"] for t in charged}
    province = _place_of_supply(final)
    if settings.tax_on_gl:  # the tax is on its own G/L lines: no tax area, nothing for E1 to work out (D-93)
        return charged, province, "", settings.gl_tax_code
    row = settings.tax_map.get(province) or TaxArea("", "")
    area, code = row.tax_area.strip(), row.code.strip().upper()
    if charged and province in ("", OUTSIDE_CANADA):
        pass
    elif not charged and province != OUTSIDE_CANADA:
        code = settings.exempt_code.strip().upper()  # nothing charged: exempt (keeps the area)
    elif province in PST_PROVINCES and types & {"GST", "HST"} and "PST" not in types:
        # PST to self-assess: E1 accrues it (B) on the whole voucher, so only when every line owes it; on lines all
        # exempt from it there is none, and E1 handles no tax (the GST goes on its own G/L line, B-922). A mix is
        # held back (``pst_mix``).
        owes = pst_owed(final)
        code = (
            settings.exempt_code.strip().upper() if owes == NONE_OWED else settings.self_assessed_code.strip().upper()
        )
    return charged, province, area, code


ALL_OWED, NONE_OWED, SOME_OWED = "all", "none", "some"


def pst_owed(final: dict[str, Any]) -> str:
    """Whether the PST not charged is owed on every line of the invoice (ALL_OWED), on none (NONE_OWED: each exempt,
    zero-rated or out of scope) or on some only (SOME_OWED). Lines with no amount, and tips, don't count."""
    from ..coding.line_tax import NOT_DUE, tax_state
    from ..coding.tax import is_tip

    items = [li for li in final.get("line_items") or [] if isinstance(li, dict) and abs(_cents(li.get("amount")))]
    items = [li for li in items if not is_tip(SimpleNamespace(description=li.get("description")))] or items
    if not items:
        return ALL_OWED
    basis = legacy_basis(items, final.get("tax_lines") or [])
    exempt = [tax_state(li, "PST", basis) == NOT_DUE for li in items]
    return NONE_OWED if all(exempt) else SOME_OWED if any(exempt) else ALL_OWED


def accrues_use_tax(final: dict[str, Any], settings: JdeSettings) -> bool:
    """True when this invoice goes to JD Edwards with the self-assessed code (B): E1 accrues the PST (use tax)
    itself, so the Self-assessment report must not have it booked again (B-716, B-722)."""
    if settings.tax_on_gl:  # no code B: the Self-assessment report has the PST (D-93)
        return False
    code = _tax_area_code(final, settings)[3]
    return bool(code) and code == settings.self_assessed_code.strip().upper()


PO_WIDTH = 8  # VLPO
REMARK_WIDTH = 30  # VLRMK


def po_not_sent(final: dict[str, Any], settings: JdeSettings) -> bool:
    """Whether the PO number is left out of the voucher's PO reference because VLPO can't hold it (D-87): longer than
    8 characters, sent as a reference only (not a PO-matched voucher, where it holds the voucher back)."""
    po = str(final.get("po_number") or "").strip()
    return len(po) > PO_WIDTH and settings.send_po_reference and not settings.po_matched


# Why a voucher with meals on it is held back (D-90): E1 would claim the full ITC on them while AP Coder expenses half
# of it, so the GL and the ITC report would disagree.
MEALS_GROSS = (
    "meals and entertainment: with the gross amount only, JD Edwards works out the tax itself and would claim all of "
    "it, not half: switch to sending tax amounts in Settings → JD Edwards E1 (Amounts)"
)
MEALS_PO = (
    "meals and entertainment on a PO-matched voucher: JD Edwards distributes it from the receipts and would claim all "
    "the tax on the meals, not half: send it without the PO match, or post the half by hand in JD Edwards"
)
# With the tax coded to G/L accounts (D-93), a PO-matched voucher has no G/L lines of ours to put the tax on.
GL_PO = (
    "tax on a PO-matched voucher: JD Edwards distributes it from the receipts, and with the tax coded to G/L accounts "
    "it posts no tax of its own, so the tax would go nowhere: send it without the PO match"
)
# Code B accrues the PST on the whole voucher (B-922).
PST_MIX = (
    "PST is owed on some lines and not on others (exempt): with code B JD Edwards would accrue PST on every line. "
    "Check the lines' tax codes, or send it with the tax coded to G/L accounts and book the PST from the "
    "Self-assessment report"
)


def _area_types(province: str, code: str, on: dt.date | None) -> list[str]:
    """The taxes E1 works out of a gross amount for a tax area (gross mode): the province's VAT (HST; else GST, and QST
    in Quebec) and, with code C, its PST; code B accrues the PST on top (not in the gross); E or blank: none."""
    if code not in ("V", "C", "B") or province not in PROVINCES:
        return []
    rates = _rates()
    levied = [t for t in ("HST", "GST", "QST", "PST") if rates.rate_for(t, province, on) is not None]
    if "HST" in levied:
        return ["HST"]
    return [t for t in levied if t != "PST" or code in ("V", "C")]


_RATES: dict[str, Any] = {}


def _rates() -> Any:
    """The tax rate table (``data/canada_tax_rates.csv``, or the data folder's), read once per file change."""
    from ..coding.tax import TaxRateTable, rates_path

    path = rates_path()
    try:
        stamp = f"{path}:{path.stat().st_mtime_ns}"
    except OSError:
        stamp = str(path)
    if _RATES.get("stamp") != stamp:
        _RATES.update(stamp=stamp, table=TaxRateTable.load(path))
    return _RATES["table"]


def _gross_problems(v: _Voucher, final: dict[str, Any]) -> list[str]:
    """Why a voucher sent with the gross amount only would have E1 work out another tax than the invoice charges
    (B-921): a line exempt or zero-rated (E1 taxes the whole gross), or the tax charged not the area's rate on the
    gross."""
    on = _date(final.get("invoice_date"))
    types = _area_types(v.province, v.tax_code, on)
    if not types:
        return []
    items = final.get("line_items") or []
    basis = legacy_basis(items, final.get("tax_lines") or [])
    kinds = sorted({t["tax_type"] for t in v.charged})
    for li in items:
        if not isinstance(li, dict) or not abs(_cents(li.get("amount"))) or li.get("taxes_applied"):
            continue
        if li.get("tax_status") in EXEMPT_STATUSES or (
            kinds and not li.get("tax_status") and set(kinds) <= set(exempt_of(li, basis))
        ):
            return [
                f"line {li.get('line_number') or '?'} carries no tax (exempt or zero-rated): with the gross amount "
                "only JD Edwards would tax it too. Code the tax to G/L accounts or send tax amounts (Amounts): set "
                "it in Settings → JD Edwards E1, or ask whoever sets up AP Coder"
            ]
    rates = _rates()
    rate = Decimal(str(round(sum(rates.rate_for(t, v.province, on) or 0.0 for t in types), 6)))
    charged = sum(_units(t.get("tax_amount"), v.decimals) for t in v.charged)
    works_out = int((Decimal(v.gross) * rate / (1 + rate)).quantize(Decimal(1), ROUND_HALF_UP))
    if abs(works_out - charged) <= 10 ** (2 - v.decimals) * (len(v.charged) + 1):  # a cent a tax line, rounding
        return []
    return [
        f"with the gross amount only, JD Edwards works out {float(rate) * 100:.4g}% tax for "
        f"{v.tax_area or v.province} ({works_out / 100:,.2f}), but the invoice charges {charged / 100:,.2f}. Check "
        "the tax, or ask the pilot tester to code the tax to G/L accounts or send tax amounts (Settings → JD Edwards "
        "E1, Amounts)"
    ]


def _prepare(inv: dict[str, Any], settings: JdeSettings, an8: str) -> _Voucher:
    final = inv.get("final_output") or {}
    v = _Voucher(int(inv.get("id") or 0), final, [])

    def add(problem: str, code: str = "JDE_VOUCHER", line: int | None = None) -> None:
        v.problems.append(problem)
        v.codes.append((code, line))

    if not an8:
        add(f"{NO_AN8}: set the vendor's ERP ID in the vendor master, or in Settings → JD Edwards", "JDE_NO_AN8")
    elif not an8.isdigit() or len(an8) > 8:
        add(f"supplier number {an8!r} {BAD_AN8} (digits only, up to 8)", "JDE_BAD_AN8")
    number = str(final.get("invoice_number") or "").strip()
    if not number:
        add("no invoice number (VLVINV)", "MISSING_INVOICE_NUMBER")
    elif len(number) > 25:
        add(f"invoice number {number!r} is longer than 25 characters (VLVINV)", "JDE_INVOICE_NUMBER_LONG")
    po = str(final.get("po_number") or "").strip()
    # A PO-matched voucher is matched to E1's order by its number: one longer than VLPO holds can't be. As a reference
    # only, it is left out of VLPO and kept in AP Coder (``po_not_sent``, D-87).
    if po and len(po) > PO_WIDTH and settings.po_matched:
        add(f"PO number {po!r} is longer than 8 characters (VLPO)", "JDE_PO_LONG")
    invoice_date = _date(final.get("invoice_date"))
    if invoice_date is None:
        add("no invoice date", "INVOICE_DATE_INVALID")
    elif invoice_date.year not in E1_YEARS:
        add(f"invoice date {invoice_date} is outside what JD Edwards dates hold (1900 to 2899)", "JDE_DATE_RANGE")

    v.currency = _norm(final.get("currency")) or settings.domestic_currency.upper()
    allowed = {_norm(c) for c in settings.currencies} | {_norm(settings.domestic_currency)}
    if v.currency not in allowed:
        add(f"currency {v.currency} is not set up for JD Edwards (Settings → JD Edwards → currencies)", "JDE_CURRENCY")
    v.foreign = v.currency != _norm(settings.domestic_currency)
    v.decimals = NO_DECIMAL_CURRENCIES.get(v.currency, 2)

    # Tax area and explanation code from the province of supply.
    # With the tax coded to G/L accounts (D-93) there is no tax area: none of these is asked.
    charged, v.province, v.tax_area, v.tax_code = _tax_area_code(final, settings)
    on_gl = settings.tax_on_gl
    if on_gl:
        pass
    elif charged and v.province in ("", OUTSIDE_CANADA):
        add("province of supply unknown: no JDE tax area for the Canadian tax charged", "JDE_TAX_AREA")
    elif v.tax_code not in RECOVERABLE_BY_CODE:
        add(
            f"tax explanation code {v.tax_code!r} is not one AP Coder can balance (V, C, B, E or blank)", "JDE_TAX_AREA"
        )
    elif charged and v.tax_code in ("V", "C", "B") and not v.tax_area:
        add(f"no JDE tax area for {v.province} (Settings → JD Edwards → tax areas)", "JDE_TAX_AREA")
    elif v.tax_code == settings.self_assessed_code.strip().upper() and pst_owed(final) == SOME_OWED:
        add(PST_MIX, "JDE_TAX_AREA")

    # In the currency's own units: whole yen are sent as whole yen, so the voucher balances as E1 reads it.
    v.gross = _units(final.get("grand_total"), v.decimals)
    due = sent_due(final, inv)
    if settings.send_due_date and v.gross >= 0 and due is not None and due.year not in E1_YEARS:
        # (a credit's is not sent)
        add(f"due date {due} is outside what JD Edwards dates hold (1900 to 2899)", "JDE_DATE_RANGE")
    v.stam = sum(_units(t.get("tax_amount"), v.decimals) for t in charged)
    # The taxable amount of each tax, a tax printed twice (goods, freight) added up (B-923); GST and QST on the same
    # lines have the same one: the widest.
    bases: dict[str, int] = {}
    for t in charged:
        bases[t["tax_type"]] = bases.get(t["tax_type"], 0) + _units(t.get("taxable_amount"), v.decimals)
    v.atxa = max(bases.values(), key=abs, default=0)
    recovers = frozenset() if on_gl else RECOVERABLE_BY_CODE.get(v.tax_code, frozenset())
    v.charged, v.recoverable_types = charged, recovers
    recoverable = [t for t in charged if t["tax_type"] in v.recoverable_types]
    v.recoverable = sum(_units(t.get("tax_amount"), v.decimals) for t in recoverable)

    # Meals (D-90): half the tax on a meals line is not claimable, and is in its expense line, not E1's to post.
    dist = _distribution(final)
    meals = sum(_units(e.get("meals_tax"), v.decimals) for e in dist if e.get("kind") == "expense")
    if v.recoverable_types and meals:
        v.meals = meals
        v.recoverable -= meals
        v.stam -= meals
    v.po_matched = bool(settings.po_matched and po)
    if meals and v.po_matched:  # no G/L lines of ours: E1 distributes from the receipts (B-865)
        add(MEALS_PO, "JDE_MEALS_PO")
    elif meals and settings.amount_mode == AMOUNT_GROSS:  # E1 works out the tax and claims all of it (B-866)
        add(MEALS_GROSS, "JDE_MEALS_GROSS")
    if on_gl and v.po_matched and charged:  # no G/L lines of ours, and no tax E1 would post either (D-93)
        add(GL_PO, "JDE_GL_PO")
    if settings.amount_mode == AMOUNT_GROSS and charged and not meals:
        for problem in _gross_problems(v, final):  # E1 would work the tax out other than charged (B-921)
            add(problem, "JDE_GROSS_TAX")
    if v.po_matched:
        items = final.get("line_items") or []
        lines = sum(_cents(li.get("amount")) for li in items)
        if not items:
            add("PO-matched voucher without invoice lines to match", "JDE_UNBALANCED")
        elif abs(lines - _cents(final.get("subtotal"))) > len(items):
            add(
                f"the lines add up to {lines / 100:,.2f}, not the subtotal {float(final.get('subtotal') or 0):,.2f}",
                "SUBTOTAL_MISMATCH",
            )
        return v

    if not dist:
        add("no GL distribution to post", "NO_LINE_ITEMS")
    total = sum(_cents(e.get("amount")) for e in dist)
    gross_cents = _cents(final.get("grand_total"))
    if dist and total != gross_cents:
        add(
            f"the GL distribution adds up to {total / 100:,.2f}, not the invoice total {v.gross / 100:,.2f}",
            "POSTING_UNBALANCED",
        )
    for e in dist:
        if _is_recoverable_entry(e) and v.recoverable_types:
            continue  # E1 posts recoverable tax itself (tax AAIs); with code E (or the tax on G/L) it is ours to post
        where = f"GL {e.get('gl_code') or '(none)'}" + (f" / {e['cost_center']}" if e.get("cost_center") else "")
        if e.get("kind") == "tax":
            where = f"GL {e.get('gl_code') or '(none)'} (the {_entry_tax(e) or 'tax'} tax account)"
        if e.get("kind") == "tax" and not str(e.get("gl_code") or "").strip():
            add(
                f"the {_entry_tax(e) or 'tax'} tax line has no GL account: set it in GL accounts & tax",
                "TAX_GL_UNMAPPED",
            )
            v.entries.append({**e, "cents": _units(e.get("amount"), v.decimals), "account": None})
            continue
        if str(e.get("gl_code") or "").strip().upper() in ("", "UNASSIGNED"):
            add(
                f"a line has no GL account ({e.get('description') or 'no description'}): code it in the review screen",
                "GL_UNASSIGNED",
                e.get("line_number"),
            )
            continue
        account = account_for(e.get("gl_code") or "", e.get("cost_center") or "", settings)
        if account is None:
            add(
                f"{where} has no JDE account (BU.Object.Subsidiary): add it to the account mapping",
                "JDE_NO_ACCOUNT",
                e.get("line_number"),
            )
        elif len(account.bu) > 12 or len(account.obj) > 6 or len(account.sub) > 8:
            add(
                f"{where}: JDE account {ani(account)} is too long (BU 12, object 6, subsidiary 8 characters)",
                "JDE_NO_ACCOUNT",
                e.get("line_number"),
            )
        v.entries.append({**e, "cents": _units(e.get("amount"), v.decimals), "account": account})
    expected = v.gross - v.recoverable
    kept = sum(e["cents"] for e in v.entries)
    kept_cents = sum(_cents(e.get("amount")) for e in v.entries)
    if v.decimals < 2 and v.entries and kept != expected:
        # Whole units (yen): a distribution spread in cents (coded before tax was spread in whole units) balances
        # in cents, but its lines each rounded to the yen can be a yen or two off: the largest line takes it.
        if kept_cents == gross_cents - sum(_cents(t.get("tax_amount")) for t in recoverable):
            biggest = max(range(len(v.entries)), key=lambda i: abs(v.entries[i]["cents"]))
            v.entries[biggest]["cents"] += expected - kept
            kept = expected
    # A line with no GL account, or a province of supply not read, is said by its own finding and holds the voucher
    # back by itself: the imbalance it causes is no Tax setup problem to send the clerk to (B-812).
    said = {code for code, _ in v.codes} & {"GL_UNASSIGNED", "JDE_TAX_AREA", "TAX_GL_UNMAPPED"}
    if dist and total == gross_cents and kept != expected and not said:
        add(
            f"unbalanced: the distribution is {kept / 100:,.2f} but gross {v.gross / 100:,.2f} minus recoverable "
            f"tax {v.recoverable / 100:,.2f} (code {v.tax_code or 'blank'}) is {expected / 100:,.2f}: check the Tax "
            "setup against the JDE tax explanation code",
            "JDE_UNBALANCED",
        )
    return v


def sent_tax(invoice: dict[str, Any], settings: JdeSettings, an8: str = "") -> float | None:
    """The tax a voucher gives JD Edwards (pilot-score, B-934): with tax amounts, VLSTAM (the claimable tax: half of it
    on meals, D-90); with the tax coded to G/L accounts, the recoverable tax on its own G/L lines; with the gross only
    None (E1 works it out from the tax area)."""
    if settings.amount_mode == AMOUNT_GROSS:
        return None
    v = _prepare(invoice, settings, an8)
    if settings.tax_on_gl:
        cents = sum(e["cents"] for e in v.entries if _is_recoverable_entry(e))
    else:
        cents = v.stam
    return round(cents / 100, 2)


def _amount(cents: int, settings: JdeSettings, decimals: int) -> int | str:
    return jde_amount(Decimal(cents) / 100, settings.decimals == IMPLIED, decimals)


def _keys(prefix: str, settings: JdeSettings, batch: int | str, inv_id: int) -> dict[str, Any]:
    return {
        f"{prefix}EDUS": settings.user.strip()[:10],
        f"{prefix}EDBT": f"{settings.batch_prefix.strip()}{batch}"[:15],
        f"{prefix}EDTN": f"APC{inv_id}"[:22],
        f"{prefix}EDSP": "0",
        f"{prefix}EDTC": "A",
        f"{prefix}EDTR": "V",
    }


def _rate(fx_rates: Mapping[str, float] | None, currency: str, domestic: str) -> float | None:
    """VLCRR: units of the domestic currency per unit of ``currency``. AP Coder's rates are CAD per unit (Settings →
    Review), so with books in another currency it is the rate across CAD (USD books, a CAD invoice: 1 / 1.37), and
    None (left blank: E1's exchange-rate table) without a rate for both."""
    rates = {"CAD": 1.0, **(fx_rates or {})}
    foreign, home = rates.get(_norm(currency)), rates.get(_norm(domestic))
    return foreign / home if foreign and home else None


def _doc_type(v: _Voucher, settings: JdeSettings) -> str:
    if v.gross < 0 and settings.credit_document_type.strip():
        return settings.credit_document_type.strip().upper()
    return settings.document_type.strip().upper()


def voucher_rows(
    invoice: dict[str, Any], vendor_an8: str, settings: JdeSettings, *, batch: int | str = "",
    fx_rates: Mapping[str, float] | None = None, on: dt.date | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:  # fmt: skip
    """(F0411Z1 rows, F0911Z1 rows, match rows) of one approved invoice, as {column: value}. Check it with
    ``validate`` first: an invoice with problems gives incomplete rows."""
    v = _prepare(invoice, settings, str(vendor_an8 or "").strip())
    final = v.final
    keys = _keys("VL", settings, batch, v.inv_id)
    invoice_date = _date(final.get("invoice_date")) or (on or dt.date.today())
    gl_date = (on or dt.date.today()) if settings.gl_date == GL_DATE_EXPORT else invoice_date
    due = sent_due(final, invoice)  # a credit note: its invoice date; a guessed one is not sent (B-924)
    doc_type = _doc_type(v, settings)
    po = str(final.get("po_number") or "").strip()
    # VLPO, VLPDCT and VLPKCO go together (B-457); a PO longer than VLPO is not sent, only kept in the remark (D-87).
    send_po = bool(po) and (v.po_matched or (settings.send_po_reference and len(po) <= PO_WIDTH))
    remark = f"AP Coder #{v.inv_id}"
    if po_not_sent(final, settings) and len(f"{remark} PO {po}") <= REMARK_WIDTH:
        remark = f"{remark} PO {po}"
    rate = _rate(fx_rates, v.currency, settings.domestic_currency) if v.foreign else None
    common = {
        **keys, "VLDCT": doc_type, "VLCO": settings.company_code, "VLMCU": settings.header_bu.strip(),
        "VLAN8": str(vendor_an8 or "").strip(), "VLVINV": str(final.get("invoice_number") or "").strip()[:25],
        "VLDIVJ": julian(invoice_date), "VLDGJ": julian(gl_date), "VLDSVJ": julian(invoice_date),
        "VLTXA1": v.tax_area, "VLEXR1": v.tax_code, "VLCRRM": "F" if v.foreign else "D", "VLCRCD": v.currency,
        "VLCRR": _plain(rate, 7) if rate else "", "VLPTC": terms_code(final, settings)[0][:PTC_WIDTH],
        # A due date E1 cannot hold is left blank, not an error: a batch downloaded again is not checked again (B-21),
        # and ``validate`` lists it for the README's "check before loading".
        "VLDDJ": julian(due) if settings.send_due_date and due is not None and due.year in E1_YEARS else "",
        "VLPST": settings.pay_status.strip(),
        "VLPO": po[:PO_WIDTH] if send_po else "",
        "VLPDCT": settings.po_document_type.strip() if send_po else "",
        "VLPKCO": (settings.po_company.strip().zfill(5) if settings.po_company.strip().isdigit()
                   else settings.po_company.strip() or settings.company_code) if send_po else "",
        "VLRMK": remark[:REMARK_WIDTH],
    }  # fmt: skip
    if settings.bank_glba:  # the G/L bank account of the Pay from account (D-66); blank: E1's AAI PB
        common["VLGLBA"] = glba(final, settings)

    def amounts(gross: int, stam: int, atxa: int, code: str | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {}
        prefix = ("VLACR", "VLCTXA", "VLCTXN", "VLCTAM") if v.foreign else ("VLAG", "VLATXA", "VLATXN", "VLSTAM")
        if settings.amount_mode in (AMOUNT_GROSS, AMOUNT_GL):  # with the tax on G/L lines, E1 takes no tax amount
            out[prefix[0]] = _amount(gross, settings, v.decimals)
        elif (v.tax_code if code is None else code) in ("E", "", exempt) or not stam:
            out.update({prefix[1]: _amount(0, settings, v.decimals), prefix[2]: _amount(gross, settings, v.decimals),
                        prefix[3]: _amount(0, settings, v.decimals)})  # fmt: skip
        else:
            out.update({prefix[1]: _amount(atxa, settings, v.decimals),
                        prefix[2]: _amount(gross - atxa - stam, settings, v.decimals),
                        prefix[3]: _amount(stam, settings, v.decimals)})  # fmt: skip
        return out

    extras = dict(settings.po_extra_columns) if settings.po_matched else {}
    exempt = settings.exempt_code.strip().upper()
    # Each distribution line's own explanation code (D-64): a line marked exempt or zero-rated, on an invoice whose
    # code handles tax, is the exempt code when nothing of the tax is put on it; the others keep the invoice's.
    items = final.get("line_items") or []
    kinds = sorted({t["tax_type"] for t in v.charged})
    basis = legacy_basis(items, final.get("tax_lines") or [])
    stated = [li is not None and not (li.get("taxes_applied") or [])
              and (li.get("tax_status") in EXEMPT_STATUSES
                   or (bool(kinds) and not li.get("tax_status") and set(kinds) <= set(exempt_of(li, basis))))
              for li in _entry_lines(v.entries, items)]  # fmt: skip
    line_codes = [exempt if no_tax and v.tax_code not in ("", exempt) else v.tax_code for no_tax in stated]
    if v.po_matched:
        header = {**common, "VLEDLN": 1, **amounts(v.gross, v.stam, v.atxa), **extras}
        matches = []
        for li in final.get("line_items") or []:
            matches.append({
                "EDUS": keys["VLEDUS"], "EDBT": keys["VLEDBT"], "EDTN": keys["VLEDTN"], "EDLN": 1,
                "DOCO": po[:8], "DCTO": settings.po_document_type.strip(), "KCOO": common["VLPKCO"],
                "LNID": li.get("line_number") or "", "LITM": "", "DSC1": _text(li.get("description"), 30),
                "UORG": _plain(li.get("quantity")), "PRRC": _plain(li.get("unit_price"), 4),
                "AEXP": _amount(_cents(li.get("amount")), settings, v.decimals), "CRCD": v.currency,
            })  # fmt: skip
        return [header], [], matches

    def dist_row(n: int, e: dict[str, Any]) -> dict[str, Any]:
        account: GlMap | None = e["account"]
        acct = account or GlMap(e.get("gl_code") or "")
        amount = _amount(e["cents"], settings, v.decimals)
        row = {
            **_keys("VN", settings, batch, v.inv_id), "VNEDLN": n, "VNCO": settings.company_code,
            "VNDGJ": julian(gl_date), "VNDCT": doc_type, "VNLT": "AA",
            "VNAA": "" if v.foreign else amount, "VNCRCD": v.currency, "VNACR": amount if v.foreign else "",
            "VNEXA": _text(e.get("description"), 30),
            "VNEXR": str(final.get("invoice_number") or "").strip()[:30],
            "VNSBL": acct.sbl, "VNSBLT": acct.sblt,
            "VNTXA1": v.tax_area if settings.dist_tax_columns else "",
            "VNEXR1": line_codes[n - 1] if settings.dist_tax_columns else "",
        }  # fmt: skip
        if settings.account_mode == ACCOUNT_ANI:
            row.update({"VNAM": "2", "VNANI": ani(acct) if account else ""})
        else:
            row.update({"VNMCU": acct.bu, "VNOBJ": acct.obj, "VNSUB": acct.sub})
        return row

    if settings.line_numbering == MATCH_HEADER and len(v.entries) > 1 and settings.tax_on_gl:
        # One pay item per G/L line, the line's own amount: the tax lines are lines like the others (D-93).
        headers = [{**common, "VLEDLN": n, "VLEXR1": v.tax_code, **amounts(e["cents"], 0, 0), **extras}
                   for n, e in enumerate(v.entries, 1)]  # fmt: skip
        return headers, [dist_row(n, e) for n, e in enumerate(v.entries, 1)], []
    if settings.line_numbering == MATCH_HEADER and len(v.entries) > 1:
        # One pay item per distribution line: each gets its share of each recoverable tax and of the taxable amount,
        # split over the lines that carry that tax (an exempt line gets none). Its tax is that share plus the
        # non-recoverable tax already in its line (e.g. PST charged on some lines only).
        unit = 10 ** (2 - v.decimals)
        taxes = _entry_taxes(v.entries, final.get("line_items") or [])
        net = [_cents(e.get("net_amount")) if e.get("kind") == "expense" else 0 for e in v.entries]
        weights = net if any(net) else [e["cents"] for e in v.entries]

        def carrying(tax_type: str) -> list[int]:
            w = [n if tax_type in t else 0 for n, t in zip(net, taxes, strict=True)]
            return w if any(w) else weights  # no line marked with it: by net amount

        shares = [0] * len(v.entries)
        for t in v.charged:
            if t["tax_type"] in v.recoverable_types:
                part = _split(_units(t.get("tax_amount"), v.decimals), carrying(t["tax_type"]), unit)
                shares = [a + b for a, b in zip(shares, part, strict=True)]
        widest = max(v.charged, key=lambda t: abs(_units(t.get("taxable_amount"), v.decimals)), default=None)
        base = carrying(widest["tax_type"]) if widest else weights  # the tax whose taxable amount is VLATXA
        atxas = _split(v.atxa, base, unit)
        # Half the tax on a meals line is in its expense amount (D-90): not E1's to post, so neither in its share
        # of the recoverable tax nor in its tax.
        meals = [_units(e.get("meals_tax"), v.decimals) if v.meals else 0 for e in v.entries]
        shares = [s - m for s, m in zip(shares, meals, strict=True)]
        own = _claimed_shares(v) if v.meals else None
        if own is not None:  # the distribution's own shares, not split again with other cents (B-867)
            shares = own
        stams = [s + _units(e.get("non_recoverable_tax"), v.decimals) - m
                 for e, s, m in zip(v.entries, shares, meals, strict=True)]  # fmt: skip
        stams[max(range(len(base)), key=lambda i: abs(base[i]))] += v.stam - sum(stams)
        grosses = [e["cents"] + s for e, s in zip(v.entries, shares, strict=True)]
        # An exempt line's pay item says so (its own VLEXR1) only when no tax is put on it.
        line_codes = [c if c != exempt or not s else v.tax_code for c, s in zip(line_codes, stams, strict=True)]
        headers = [
            {**common, "VLEDLN": n, "VLEXR1": line_codes[n - 1], **amounts(g, s, a, line_codes[n - 1]), **extras}
            for n, (g, s, a) in enumerate(zip(grosses, stams, atxas, strict=True), 1)
        ]
        return headers, [dist_row(n, e) for n, e in enumerate(v.entries, 1)], []
    header = {**common, "VLEDLN": 1, **amounts(v.gross, v.stam, v.atxa), **extras}
    return [header], [dist_row(n, e) for n, e in enumerate(v.entries, 1)], []


def _claimed_shares(v: _Voucher) -> list[int] | None:
    """Each distribution line's claimable share of the recoverable taxes E1 posts, as the GL distribution has them
    (``tax.tax_shares``, half of a meals line's share: D-90), in the currency's units; None when the invoice's lines
    can't be read back or don't match the distribution lines (then the pay items split the tax themselves)."""
    from ..coding.tax import half_down, is_meals, tax_shares
    from ..core.schema import InvoiceCoding

    try:
        coding = InvoiceCoding.model_validate({k: x for k, x in v.final.items() if k != "gl_distribution"})
    except ValueError:
        return None
    items = v.final.get("line_items") or []
    behind = _entry_lines(v.entries, items)
    position = {id(li): i for i, li in enumerate(items)}
    lines = [position.get(id(li)) if li is not None else None for li in behind]
    if len(coding.line_items) != len(items):
        return None
    meals_codes = {str(e.get("gl_code") or "").strip() for e in v.entries if e.get("meals_tax")}
    places = v.decimals
    per_line = [0] * len(items)
    for tl in coding.tax_lines:
        if tl.tax_type not in v.recoverable_types or abs(tl.tax_amount) < 0.005:
            continue
        for idx, share in tax_shares(coding.line_items, tl, places):
            meal = is_meals(coding.line_items[idx].predicted_gl_code, meals_codes)
            per_line[idx] += _units(half_down(share, places) if meal else share, v.decimals)
    shares = [per_line[i] if i is not None else 0 for i in lines]
    return shares if sum(shares) == v.recoverable else None


def _entry_lines(entries: list[dict[str, Any]], items: list[dict[str, Any]]) -> list[dict[str, Any] | None]:
    """The invoice line behind each distribution line (None for a tax line). Expense lines follow the invoice lines
    in order; matched by line number when the counts differ."""
    if sum(1 for e in entries if e.get("kind") == "expense") == len(items):
        it = iter(items)
        return [next(it) if e.get("kind") == "expense" else None for e in entries]
    by_number: dict[Any, dict[str, Any]] = {}
    for li in items:
        by_number.setdefault(li.get("line_number"), li)
    return [by_number.get(e.get("line_number")) if e.get("kind") == "expense" else None for e in entries]


def _entry_taxes(entries: list[dict[str, Any]], items: list[dict[str, Any]]) -> list[set[str]]:
    """The taxes the invoice line behind each distribution line is marked with (none for a tax line)."""
    return [set((li or {}).get("taxes_applied") or []) for li in _entry_lines(entries, items)]


def _text(value: Any, size: int) -> str:
    """One line of text, cut to the column's size."""
    return " ".join(str(value or "").split())[:size].rstrip()


def _plain(value: Any, places: int = 2) -> str:
    try:
        d = Decimal(str(value if value is not None else 0)).quantize(Decimal(1).scaleb(-places), ROUND_HALF_UP)
    except ArithmeticError:
        return ""
    text = f"{d:.{places}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


# --- Checks over a batch -----------------------------------------------------------------------------------------


def prior_records(store: Any, exclude_ids: Iterable[int] = ()) -> dict[str, list[dict[str, Any]]]:
    """What is already out (read-only): invoices in live export batches (not ``exclude_ids``) and the
    imported ERP invoice register."""
    exclude = set(exclude_ids)
    exported = [r for r in store.search_rows() if r.get("export_batch") and r["id"] not in exclude]
    return {"exported": exported, "register": store.erp_register()}


def _negative(value: Any) -> bool:
    try:
        return float(value or 0) < 0
    except (TypeError, ValueError):
        return False


def duplicate_problems(
    invoices: list[dict[str, Any]], settings: JdeSettings, vendor_lookup: VendorLookup,
    prior: Mapping[str, list[dict[str, Any]]] | None = None,
) -> dict[int, list[str]]:  # fmt: skip
    """Same supplier (address number or vendor name) and invoice number, within the batch or already
    exported / in the ERP. A credit note does not duplicate the invoice it reverses."""
    out: dict[int, list[str]] = {}
    prior = prior or {}

    def an8_of(row: dict[str, Any]) -> str:  # by name, else by GST/HST number, as for the invoices themselves
        gst = row.get("gst_hst_registration_number") or ""
        known = {"vendor_name": row.get("vendor_name") or "", "gst_hst_registration_number": gst}
        return resolve_an8(known, vendor_lookup, settings)

    def same(a: tuple[str, str], b: tuple[str, str]) -> bool:
        return (bool(a[0]) and a[0] == b[0]) or (bool(a[1]) and a[1] == b[1])

    earlier: list[tuple[tuple[str, str], str, bool, int]] = []
    for inv in invoices:
        final = inv.get("final_output") or {}
        number = norm_invoice_number(final.get("invoice_number"))
        if not number:
            continue
        who = (resolve_an8(final, vendor_lookup, settings), vendor_key(final.get("vendor_name") or ""))
        credit = _negative(final.get("grand_total"))
        msgs = []
        for other_who, other_number, other_credit, other_id in earlier:
            if other_number == number and other_credit == credit and same(who, other_who):
                msgs.append(f"same supplier and invoice number as AP Coder #{other_id} in this batch")
                break
        for r in prior.get("exported") or []:
            if norm_invoice_number(r.get("invoice_number")) != number or _negative(r.get("grand_total")) != credit:
                continue
            if same(who, (an8_of(r), vendor_key(r.get("vendor_name") or ""))):
                msgs.append(f"already exported: AP Coder #{r['id']} in batch {r['export_batch']}")
                break
        for r in prior.get("register") or []:
            if norm_invoice_number(r.get("invoice_number")) != number or _negative(r.get("total")) != credit:
                continue
            key = r.get("vendor_key") or vendor_key(r.get("vendor_name") or "")
            if same(who, (an8_of(r), key)):  # (the register has no GST/HST number: by name)
                when = f", dated {r['invoice_date']}" if r.get("invoice_date") else ""
                msgs.append(f"already in the ERP (invoice {r.get('invoice_number')}{when})")
                break
        if msgs:
            out[int(inv.get("id") or 0)] = msgs
        earlier.append((who, number, credit, int(inv.get("id") or 0)))
    return out


def validate(
    invoices: list[dict[str, Any]], settings: JdeSettings, vendor_lookup: VendorLookup = None,
    prior: Mapping[str, list[dict[str, Any]]] | None = None,
) -> dict[int, list[str]]:  # fmt: skip
    """{invoice id: blocking problems} for the invoices that cannot go out (the others are fine)."""
    out: dict[int, list[str]] = {}
    for inv in invoices:
        problems = _prepare(inv, settings, resolve_an8(inv.get("final_output") or {}, vendor_lookup, settings)).problems
        if problems:
            out[int(inv.get("id") or 0)] = problems
    for inv_id, msgs in duplicate_problems(invoices, settings, vendor_lookup, prior).items():
        out.setdefault(inv_id, []).extend(msgs)
    return out


# --- Before approval: the review screen ---------------------------------------------------------------------------

ERROR, WARNING, INFO = "error", "warning", "info"  # a finding's severity (coding.validation.ERROR, WARNING, INFO)
SHOW_SETTING = "show_jde_settings"  # web.core.SHOW_JDE: "1" when JD Edwards is the export in use
SETTINGS_URL = "/settings/jde"
# What the review screen's own checks already say (no second message): the invoice number, the date, a line without a
# GL account, the totals.
SAID_ELSEWHERE = frozenset({"MISSING_INVOICE_NUMBER", "INVOICE_DATE_INVALID", "GL_UNASSIGNED", "SUBTOTAL_MISMATCH",
                            "POSTING_UNBALANCED", "NO_LINE_ITEMS", "TAX_GL_UNMAPPED"})  # fmt: skip


def in_use(store: Any) -> bool:
    """Whether approved invoices go to JD Edwards: Settings → About's "Show JD Edwards settings" is on, or (never
    set) the database was set up for it (its settings saved, or a JD Edwards batch made), as ``web.core.jde_shown``
    reads it. Read only: nothing is stored."""
    try:
        value = store.get_setting(SHOW_SETTING, "unset")
        if value != "unset":
            return value == "1"
        return bool(store.get_setting(SETTING_KEY)) or any(b["format"] == FORMAT for b in store.export_batches())
    except Exception:  # noqa: BLE001 - a setting that can't be read: no JD Edwards checks
        return False


def voucher_findings(output: dict[str, Any], settings: JdeSettings, an8: str) -> list[tuple[str, str, str, int | None]]:
    """What would hold this invoice back from a JD Edwards batch, as checks for the review screen (severity, code,
    message in plain words, line): ``validate`` asked before approval, so an approved voucher is never held back at
    export for something the screen could have said (D-75). The duplicate check is the screen's own."""
    out: list[tuple[str, str, str, int | None]] = []
    for problem in settings_problems(settings):
        out.append((ERROR, "JDE_SETTINGS", f"JD Edwards settings: {problem} Set it in "
                    "Settings → JD Edwards E1, or ask whoever sets up AP Coder", None))  # fmt: skip
    v = _prepare({"id": 0, "final_output": output}, settings, str(an8 or "").strip())
    seen: set[tuple[str, int | None]] = set()
    unmapped: dict[str, list[int | None]] = {}  # "GL 6010 / CC400": the lines posting to it with no E1 account
    for problem, (code, line) in zip(v.problems, v.codes, strict=True):
        if code in SAID_ELSEWHERE or (code, line) in seen:
            continue
        seen.add((code, line))
        number = str(output.get("invoice_number") or "").strip()
        po = str(output.get("po_number") or "").strip()
        if code == "JDE_NO_AN8":
            out.append((ERROR, "JDE_NO_AN8", "JD Edwards: this vendor has no supplier number (AN8). Pick the vendor "
                        "from the vendor list, or add its number in Settings → JD Edwards E1 → Supplier address "
                        "numbers (on the company server, an Admin does)", None))  # fmt: skip
        elif code == "JDE_BAD_AN8":
            out.append((ERROR, "JDE_BAD_AN8", f"JD Edwards: the supplier number {an8} is not an address number (AN8: "
                        "digits only, up to 8). Set this vendor's AN8 in Settings → JD Edwards E1, or ask whoever "
                        "sets up AP Coder", None))  # fmt: skip
        elif code == "JDE_INVOICE_NUMBER_LONG":
            out.append((ERROR, "JDE_INVOICE_NUMBER_LONG", f"JD Edwards keeps 25 characters of the invoice number: "
                        f"this one has {len(number)}", None))  # fmt: skip
        elif code == "JDE_PO_LONG":
            out.append((ERROR, "JDE_PO_LONG", f"JD Edwards keeps 8 characters of the PO number: “{po}” has "
                        f"{len(po)}. Type the PO number as JD Edwards has it", None))  # fmt: skip
        elif code == "JDE_DATE_RANGE":
            out.append((ERROR, "JDE_DATE_RANGE", f"JD Edwards: the {problem}", None))
        elif code == "JDE_CURRENCY":
            out.append((ERROR, "JDE_CURRENCY", f"JD Edwards isn't set up for {v.currency}: check the currency, or ask "
                        "the pilot tester to add it in Settings → JD Edwards E1", None))  # fmt: skip
        elif code == "JDE_TAX_AREA":
            out.append((ERROR, "JDE_TAX_AREA", f"JD Edwards: {problem}", None))
        elif code == "JDE_NO_ACCOUNT":  # said once for all the lines below
            unmapped.setdefault(problem.split(" has no JDE account")[0].split(": JDE account")[0], []).append(line)
        elif code == "JDE_MEALS_GROSS":
            out.append((ERROR, "JDE_MEALS_GROSS", f"JD Edwards: {problem}, or ask the pilot tester", None))
        elif code == "JDE_MEALS_PO":
            out.append((ERROR, "JDE_MEALS_PO", f"JD Edwards: {problem}", None))
        elif code == "JDE_GL_PO":
            out.append((ERROR, "JDE_GL_PO", f"JD Edwards: {problem}", None))
        elif code == "JDE_GROSS_TAX":
            out.append((ERROR, "JDE_GROSS_TAX", f"JD Edwards: {problem}", None))
        elif code == "JDE_UNBALANCED":
            out.append((ERROR, "JDE_UNBALANCED", f"JD Edwards: {problem}", None))
        else:
            out.append((ERROR, "JDE_VOUCHER", f"JD Edwards: {problem}", line))
    no_code = terms_unmapped(output, settings)
    if no_code:  # a note: a blank VLPTC has E1 take the supplier's own terms, no reason to hold anything (B-896)
        out.append((INFO, "JDE_TERMS_NO_CODE", f"Payment terms {no_code} have no JD Edwards code: add it in "
                    "Settings → JD Edwards E1 (or ask the pilot tester)", None))  # fmt: skip
    if po_not_sent(output, settings):
        out.append((WARNING, "JDE_PO_LONG", "JD Edwards' PO field holds 8 characters: this PO number is kept in AP "
                    "Coder and not sent", None))  # fmt: skip
    if unmapped:  # one finding: "GL 6400 (lines 1, 2), 6500 (line 7) have no JDE account…", not one per account

        def where(account: str, lines: list[int | None]) -> str:
            numbers = [str(n) for n in lines if n]
            said = f" (line{'s' if len(numbers) > 1 else ''} {', '.join(numbers)})" if numbers else ""
            return account.removeprefix("GL ") + said

        accounts = [where(a, ls) for a, ls in unmapped.items()]
        one = len(accounts) == 1
        out.append((ERROR, "JDE_NO_ACCOUNT", f"JD Edwards: GL {', '.join(accounts)} {'has' if one else 'have'} no JDE "
                    "account (BU.Object.Subsidiary): ask the pilot tester to map "
                    f"{'it' if one else 'them'} in Settings → JD Edwards E1", None))  # fmt: skip
    from ap_coder.ops.offline import setup_contact

    return [(severity, code, setup_contact(text), line) for severity, code, text, line in out]


def account_text(gl_code: str, cost_center: str, settings: JdeSettings) -> str:
    """The E1 account a GL line posts to, BU.Object.Subsidiary ("CC400.6010.01"), or "" when none is mapped."""
    account = account_for(gl_code, cost_center, settings)
    return ani(account) if account else ""


def voucher_view(
    output: dict[str, Any], settings: JdeSettings, an8: str, *, on: dt.date | None = None
) -> dict[str, Any]:
    """The voucher header as JD Edwards gets it, in its order, for the review screen: each value, where it comes from
    (the invoice, or the settings: read-only there) and the Z-file column."""
    final = output
    v = _prepare({"id": 0, "final_output": final}, settings, str(an8 or "").strip())
    today = on or dt.date.today()
    invoice_date = _date(final.get("invoice_date"))
    gl_date = today if settings.gl_date == GL_DATE_EXPORT else invoice_date
    due = sent_due(final)
    credit = v.gross < 0
    code, key, source = terms_code(final, settings)
    if source == TERMS_TABLE:
        terms_text = f"{code} ({key})"
        terms_note = f"Sent to JD Edwards as code {code}"
    elif source == TERMS_DEFAULT:
        terms_text = f"{code} (the default for terms not in the table)"
        terms_note = f"Sent to JD Edwards as code {code}, the default for terms not in the table"
    elif source == TERMS_CREDIT:
        terms_text = "Not sent (a credit): JD Edwards takes no terms code on a credit memo"
        terms_note = "Not sent to JD Edwards: a credit memo goes without a terms code"
    elif settings.terms_codes and key:
        terms_text = f"Not sent: no code for {key}, JD Edwards uses the supplier's own terms"
        terms_note = f"No JD Edwards code for {key}: it uses the supplier's own terms"
    else:  # the invoice's words are under its Payment terms box
        terms_text = "Not sent: JD Edwards uses the supplier's own terms"
        terms_note = "Not sent to JD Edwards: it uses the supplier's own terms"

    if not settings.send_due_date:
        due_text = "Not sent: JD Edwards works it out"
    elif due is None:
        due_text = "Not sent: JD Edwards works it out from the supplier's terms"
    else:
        due_text = due.isoformat() + (" (the invoice date: a credit)" if credit else "")

    def money(cents: int) -> str:
        return f"{cents / 100:,.{v.decimals}f}" if v.decimals else f"{cents // 100:,}"

    amounts = money(v.gross)
    if settings.amount_mode == AMOUNT_TAX:
        amounts = f"{money(v.gross)} gross · {money(v.atxa)} taxable · {money(v.stam)} tax"
    rows = [
        ("Company", "VLCO", settings.company_code, "settings"),
        ("Supplier (AN8)", "VLAN8", str(an8 or "").strip() or "none", "vendor"),
        ("Invoice number", "VLVINV", str(final.get("invoice_number") or "").strip()[:25] or "—", "invoice"),
        ("Invoice date", "VLDIVJ", invoice_date.isoformat() if invoice_date else "—", "invoice"),
        ("G/L date", "VLDGJ", (gl_date.isoformat() if gl_date else "—") + (
            " (the day of the export)" if settings.gl_date == GL_DATE_EXPORT else " (the invoice date)"), "settings"),
        ("Due date", "VLDDJ", due_text, "invoice"),
        ("Payment terms", "VLPTC", terms_text, "settings"),
        ("Currency", "VLCRCD", v.currency + (" (foreign)" if v.foreign else ""), "invoice"),
        ("Gross / taxable / tax" if settings.amount_mode == AMOUNT_TAX else "Gross amount",
         "VLAG" if not v.foreign else "VLACR", amounts, "invoice"),
        ("Tax area / explanation", "VLTXA1 / VLEXR1",
         f"— / {v.tax_code}: the tax goes on its own G/L lines (tax coded to G/L accounts)" if settings.tax_on_gl
         else f"{v.tax_area or '—'} / {v.tax_code or 'blank'}", "settings"),
        ("Pay status", "VLPST", settings.pay_status.strip() or "—", "settings"),
    ]  # fmt: skip
    return {"rows": [{"label": a, "column": b, "value": c, "source": d} for a, b, c, d in rows],
            "settings_url": SETTINGS_URL, "terms_sent": bool(code), "terms_code": code,
            "terms_note": terms_note}  # fmt: skip


# --- Files -------------------------------------------------------------------------------------------------------


def glba(final: dict[str, Any], settings: JdeSettings) -> str:
    """VLGLBA of an invoice: the value mapped to its Pay from account, the code's case aside ("" when none)."""
    mapped = {k.upper(): v for k, v in settings.bank_glba.items()}
    return mapped.get(str(final.get("pay_from_account") or "").strip().upper(), "")


def bank_notes(invoices: list[dict[str, Any]], settings: JdeSettings) -> dict[int, list[str]]:
    """{invoice id: [note]} for the invoices whose Pay from account has no G/L bank account mapped, while the mapping
    is in use (B-684): their VLGLBA is blank and E1 pays them from its default bank (AAI PB), which may not be the
    account AP chose. A note to check before loading, never a reason to hold an invoice back."""
    if not settings.bank_glba:
        return {}
    out: dict[int, list[str]] = {}
    for inv in invoices:
        final = inv.get("final_output") or {}
        if glba(final, settings):
            continue
        code = str(final.get("pay_from_account") or "").strip()
        inv_id = int(inv.get("id") or 0)
        out[inv_id] = [  # whole sentences (B-689)
            f"AP Coder #{inv_id} is paid from {code}, which has no G/L bank account (VLGLBA) mapped: E1 pays it from "
            "its default bank (AAI PB), which may not be that account."
            if code
            else f"AP Coder #{inv_id} has no Pay from account: E1 pays it from its default bank (AAI PB)."
        ]
    return out


def header_columns(settings: JdeSettings) -> list[str]:
    extra = [c for c in settings.po_extra_columns if c not in HEADER_COLUMNS] if settings.po_matched else []
    bank = ["VLGLBA"] if settings.bank_glba and "VLGLBA" not in extra else []
    return HEADER_COLUMNS + bank + extra


def _csv(columns: list[str], rows: list[dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(columns)
    for r in rows:
        values = ["" if r.get(c) is None else r.get(c) for c in columns]
        # The text as it is: a database loader reads the Z-files, not Excel, so no spreadsheet quoting (an
        # apostrophe before "+A-100" would be loaded with it and JDE's supplier + invoice number check would miss).
        writer.writerow([v[: TEXT_WIDTHS[c]] if c in TEXT_WIDTHS and isinstance(v, str) else v
                         for c, v in zip(columns, values, strict=True)])  # fmt: skip
    return buf.getvalue().encode("utf-8")  # no BOM: database loaders read the first header as-is


def export_batch(
    invoices: list[dict[str, Any]], settings: JdeSettings, vendor_lookup: VendorLookup = None, *,
    batch: int | str = "", fx_rates: Mapping[str, float] | None = None, on: dt.date | None = None,
) -> dict[str, bytes]:  # fmt: skip
    """{"F0411Z1.csv": …, "F0911Z1.csv": …[, "F0411Z1T.csv": …]} for these invoices (validated first)."""
    headers: list[dict[str, Any]] = []
    dists: list[dict[str, Any]] = []
    matches: list[dict[str, Any]] = []
    for inv in invoices:
        an8 = resolve_an8(inv.get("final_output") or {}, vendor_lookup, settings)
        h, d, m = voucher_rows(inv, an8, settings, batch=batch, fx_rates=fx_rates, on=on)
        headers += h
        dists += d
        matches += m
    files = {HEADER_FILE: _csv(header_columns(settings), headers), DIST_FILE: _csv(DIST_COLUMNS, dists)}
    if settings.po_matched and matches:
        files[MATCH_FILE] = _csv(MATCH_COLUMNS, matches)
    return files


UNDO_TEXT = "in AP Coder, Exports → Past batches → Undo this batch, fix, and export again."


def readme(
    files: dict[str, bytes], settings: JdeSettings, batch: int | str, invoices: list[dict[str, Any]],
    left_out: Mapping[int, list[str]] | None = None, to_check: Mapping[int, list[str]] | None = None,
    no_undo: str = "",
) -> str:  # fmt: skip
    """README.txt of the ZIP: what is in it and how to load it. ``left_out``: invoices not in the file;
    ``to_check``: invoices in it that the current settings would refuse (a batch downloaded again); the invoices whose
    Pay from account has no VLGLBA mapped are listed too (``bank_notes``). ``no_undo``: why the batch can't be undone
    (vouchers JD Edwards has, or may have), said instead of the undo (B-1203)."""
    totals: dict[str, float] = {}
    for inv in invoices:
        final = inv.get("final_output") or {}
        cur = _norm(final.get("currency")) or settings.domestic_currency
        totals[cur] = round(totals.get(cur, 0.0) + float(final.get("grand_total") or 0), 2)

    def count(name: str) -> int:
        return len(list(csv.reader(io.StringIO(files[name].decode("utf-8"))))) - 1 if name in files else 0

    edbt = f"{settings.batch_prefix.strip()}{batch}"[:15]
    money = ", ".join(f"{c} {t:,.2f}" for c, t in sorted(totals.items())) or "-"
    decimals = "implied decimals (5.50 is written 550)" if settings.decimals == IMPLIED else "with a decimal point"
    amounts = {
        AMOUNT_GROSS: "gross only (VLAG / VLACR)",
        AMOUNT_TAX: "taxable, non-taxable and tax",
        AMOUNT_GL: f"gross only (VLAG / VLACR), tax coded to G/L accounts: each tax on its own F0911Z1 line to the "
        f"tax account, code {settings.gl_tax_code} and no tax area, so E1 computes no tax",
    }[settings.amount_mode]
    lines = [
        f"AP Coder: JD Edwards E1 voucher batch {edbt}",
        "=" * 60,
        "",
        f"Vouchers: {len(invoices)}   Totals: {money}",
        f"User (EDUS): {settings.user.strip()}   Batch (EDBT): {edbt}   Company: {settings.company_code}",
        f"Amounts: {decimals}; {amounts}",
        "Dates: E1 Julian CYYDDD (2026-10-09 is 126282).",
        "",
        "Files",
        "-----",
        f"{HEADER_FILE}  {count(HEADER_FILE)} row(s): one per voucher pay item, for table F0411Z1.",
        f"{DIST_FILE}  {count(DIST_FILE)} row(s): one per G/L distribution line, for table F0911Z1.",
    ]
    if MATCH_FILE in files:
        lines.append(
            f"{MATCH_FILE}  {count(MATCH_FILE)} row(s): PO match lines (PO, line, item, quantity, unit price, amount)."
            " NOT a standard Oracle layout: confirm with your JDE team how PO-matched vouchers are loaded."
        )
    lines += [
        "",
        "How to load",
        "-----------",
        "1. Load each CSV into its Z-table (F0411Z1, F0911Z1) in the E1 environment you test in first, e.g. with",
        "   your database's import tool or a table conversion. Each header name is the table's column name.",
        "2. Run the Voucher Batch Processor R04110ZA (or R04110Z) in PROOF mode on this batch number "
        f"({edbt}) and user ({settings.user.strip()}).",
        "   Read the report and the Work Center (P0471/P0411Z1) errors; fix the mapping in AP Coder if needed.",
        "3. Run R04110ZA in FINAL mode. The Z-rows are marked processed (VLEDSP = 1) and the vouchers are created.",
        "4. Post the voucher batch (P0011 / R09801) as usual.",
        "",
        f"If the load fails: {no_undo or UNDO_TEXT}",
        "Each voucher's transaction number (EDTN) is APC + the AP Coder invoice number.",
        "",
        "To confirm with your JDE team before the first live load: "
        + "; ".join(UNCONFIRMED)
        + (
            "; that R04110ZA with code E and no tax area posts the G/L lines as sent (the tax lines to the tax "
            "accounts) and works out no tax of its own (confirm with your JDE administrator)"
            if settings.tax_on_gl
            else ""
        )
        + ".",
    ]
    if left_out:
        lines += ["", "Not in this batch (fix them, then export again)", "-" * 47]
        for inv_id, problems in left_out.items():
            lines.append(f"AP Coder #{inv_id}: " + "; ".join(problems))
    notes = bank_notes(invoices, settings)
    if notes:
        title = "In this batch, paid from E1's default bank: check before loading"
        lines += ["", title, "-" * len(title)]
        lines += [n for said in notes.values() for n in said]
        lines.append("Map them in Settings -> JD Edwards E1 -> G/L bank account (VLGLBA) per Pay from account.")
    if to_check:
        title = "In this batch, but the current settings or vendor master would refuse them: check before loading"
        lines += ["", title, "-" * len(title)]
        for inv_id, problems in to_check.items():
            lines.append(f"AP Coder #{inv_id}: " + "; ".join(problems))
    return "\n".join(lines) + "\n"


def build_zip(
    invoices: list[dict[str, Any]], settings: JdeSettings, vendor_lookup: VendorLookup = None, *,
    batch: int | str = "", fx_rates: Mapping[str, float] | None = None, on: dt.date | None = None,
    left_out: Mapping[int, list[str]] | None = None, to_check: Mapping[int, list[str]] | None = None,
    left_invoices: list[dict[str, Any]] = (), no_undo: str = "",
) -> tuple[bytes, str, str]:  # fmt: skip
    """(ZIP bytes, file name, mime type): the CSVs and a README.txt. ``left_invoices``: the invoices of ``left_out``,
    for their vendor, number and date in DRY_RUN.txt (B-1197); ``no_undo``: why it can't be undone (``readme``)."""
    files = export_batch(invoices, settings, vendor_lookup, batch=batch, fx_rates=fx_rates, on=on)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
        zf.writestr("README.txt", readme(files, settings, batch, invoices, left_out, to_check, no_undo))
        report = dry_run(invoices, settings, vendor_lookup, batch=batch, fx_rates=fx_rates, on=on)
        for h in report["held"]:
            if h["id"] in (to_check or {}):  # the files hold it: it passed the checks when the batch was made
                h["reasons"] = ["it IS in this batch's files (checked when the batch was made): check it before "
                                "loading", *h["reasons"]]  # fmt: skip
        finals = {int(i.get("id") or 0): i.get("final_output") or {} for i in left_invoices}
        for inv_id, reasons in (left_out or {}).items():
            final = finals.get(inv_id, {})
            report["held"].append({"id": inv_id, "vendor": final.get("vendor_name") or "",
                                   "number": final.get("invoice_number") or "",
                                   "date": final.get("invoice_date") or "", "reasons": reasons})  # fmt: skip
        zf.writestr("DRY_RUN.txt", dry_run_text(report))
    name = f"ap_coder_jde_batch_{batch}.zip" if batch != "" else "ap_coder_jde.zip"
    return buf.getvalue(), name, "application/zip"


# --- Dry run ---------------------------------------------------------------------------------------------------


def _shown(value: Any, settings: JdeSettings, decimals: int) -> str:
    """An amount as a person reads it, from what the files hold (implied decimals or a decimal point)."""
    if value in ("", None):
        return ""
    try:
        number = Decimal(str(value))
    except ArithmeticError:
        return str(value)
    if settings.decimals == IMPLIED:
        number /= Decimal(10) ** decimals
    return f"{number:,.{decimals}f}"


def _gross_of(header: dict[str, Any], foreign: bool) -> Any:
    """A pay item's gross as the file holds it: VLAG / VLACR, or with taxable / non-taxable / tax amounts (no gross
    column) the three added up, from the currency's own VLC* columns for a foreign invoice."""
    gross_col = "VLACR" if foreign else "VLAG"
    parts = ("VLCTXA", "VLCTXN", "VLCTAM") if foreign else ("VLATXA", "VLATXN", "VLSTAM")
    if header.get(gross_col) not in ("", None):
        return header[gross_col]
    if all(header.get(p) in ("", None) for p in parts):
        return ""
    return sum(Decimal(str(header.get(p) or 0)) for p in parts)


def dry_run(
    invoices: list[dict[str, Any]],
    settings: JdeSettings,
    vendor_lookup: VendorLookup = None,
    *,
    prior: Mapping[str, list[dict[str, Any]]] | None = None,
    batch: int | str = "",
    fx_rates: Mapping[str, float] | None = None,
    on: dt.date | None = None,
) -> dict[str, Any]:
    """Exactly what a batch of these invoices would load, row by row, and why any invoice is held back, without
    making a batch. {"blocking": [...], "vouchers": [...], "held": [...], "files": {file: rows}, "totals": {...}}."""
    blocking = settings_problems(settings)
    problems = validate(invoices, settings, vendor_lookup, prior)
    notes = bank_notes(invoices, settings)
    out: dict[str, Any] = {
        "batch": f"{settings.batch_prefix.strip()}{batch}"[:15],
        "user": settings.user.strip(),
        "company": settings.company_code,
        "blocking": blocking,
        "vouchers": [],
        "held": [],
        "files": {HEADER_FILE: 0, DIST_FILE: 0, MATCH_FILE: 0},
        "totals": {},
    }
    for inv in invoices:
        final = inv.get("final_output") or {}
        inv_id = int(inv.get("id") or 0)
        who = {
            "id": inv_id,
            "vendor": final.get("vendor_name") or "",
            "number": final.get("invoice_number") or "",
            "date": final.get("invoice_date") or "",
            "currency": _norm(final.get("currency")) or settings.domestic_currency,
            "total": final.get("grand_total"),
        }
        if inv_id in problems or blocking:
            out["held"].append(
                {**who, "reasons": problems.get(inv_id, []) + (["the JD Edwards settings above"] if blocking else [])}
            )
            continue
        an8 = resolve_an8(final, vendor_lookup, settings)
        v = _prepare(inv, settings, an8)
        headers, dists, matches = voucher_rows(inv, an8, settings, batch=batch, fx_rates=fx_rates, on=on)
        aa_col = "VNACR" if v.foreign else "VNAA"
        dist_total = sum(Decimal(str(d.get(aa_col) or 0)) for d in dists)
        posted = Decimal(v.gross - sum(e["cents"] for e in v.entries)) / 100
        out["vouchers"].append(
            {
                **who,
                "an8": an8,
                "tax_area": v.tax_area,
                "tax_code": v.tax_code,
                "po_matched": v.po_matched,
                "pay_items": [
                    {
                        "line": h.get("VLEDLN"),
                        "gross": _shown(_gross_of(h, v.foreign), settings, v.decimals),
                        "tax": _shown(h.get("VLSTAM", h.get("VLCTAM", "")), settings, v.decimals),
                        "dates": f"invoice {h['VLDIVJ']}, G/L {h['VLDGJ']}"
                        + (f", due {h['VLDDJ']}" if h.get("VLDDJ") else ""),
                        "doc_type": h.get("VLDCT"),
                        "po": h.get("VLPO", ""),
                    }
                    for h in headers
                ],
                "lines": [
                    {
                        "line": d.get("VNEDLN"),
                        "account": d.get("VNANI")
                        or ".".join(x for x in (d.get("VNMCU"), d.get("VNOBJ"), d.get("VNSUB")) if x),
                        "amount": _shown(d.get(aa_col), settings, v.decimals),
                        "text": d.get("VNEXA", ""),
                    }
                    for d in dists
                ],
                "matches": len(matches),
                # The self-assessed PST is booked from the Self-assessment report by journal entry; a code that has
                # E1 accrue the use tax itself (B) would accrue it a second time (B-716).
                "warnings": (
                    [
                        f"code {v.tax_code} has JD Edwards accrue the PST (use tax): do not also book it from AP "
                        "Coder's Self-assessment report, or the PST is accrued twice"
                    ]
                    if accrues_use_tax(final, settings)
                    else []
                )
                + (
                    [f"payment terms {unmapped} have no JD Edwards code: add it in Settings -> JD Edwards E1"]
                    if (unmapped := terms_unmapped(final, settings))
                    else []
                ),
                "terms": _terms_shown(final, settings),
                "notes": notes.get(inv_id, []),
                "balance": (
                    f"distribution {_shown(dist_total, settings, v.decimals)} = gross {v.gross / 100:,.2f}, the tax "
                    f"on its own G/L lines (tax coded to G/L accounts, code {v.tax_code}: E1 posts no tax)"
                    if settings.tax_on_gl
                    else f"distribution {_shown(dist_total, settings, v.decimals)} = gross {v.gross / 100:,.2f} "
                    f"- tax E1 posts {posted:,.2f} (code {v.tax_code or 'none'})"
                )
                if dists
                else "PO-matched: E1 distributes from the receipts",
            }
        )
        out["files"][HEADER_FILE] += len(headers)
        out["files"][DIST_FILE] += len(dists)
        out["files"][MATCH_FILE] += len(matches)
        cur = who["currency"]
        out["totals"][cur] = round(out["totals"].get(cur, 0.0) + float(final.get("grand_total") or 0), 2)
    return out


def _terms_shown(final: dict[str, Any], settings: JdeSettings) -> str:
    """The dry run's line about the voucher's payment terms code (VLPTC), "" when there is nothing to say."""
    code, key, source = terms_code(final, settings)
    if not code and not key:
        return ""
    printed = key or "none printed"
    if source == TERMS_CREDIT:
        return f"payment terms {printed} -> none (a credit memo is sent without a terms code)"
    if source == TERMS_DEFAULT:
        return f"payment terms {printed} -> {code} (the default code)"
    if code:
        return f"payment terms {printed} -> {code}"
    return f"payment terms {printed} -> none (no code: JD Edwards uses the supplier's own terms)"


def dry_run_text(report: dict[str, Any]) -> str:
    """The dry run as plain text: for the JDE team, and kept in each batch's ZIP as DRY_RUN.txt."""
    lines = [
        f"AP Coder: JD Edwards E1 dry run of batch {report['batch']} "
        f"(user {report['user']}, company {report['company']})",
        "=" * 78,
        "",
    ]
    if report["blocking"]:
        lines += ["NOTHING CAN LOAD until these JD Edwards settings are fixed (Settings -> JD Edwards E1):"]
        lines += [f"  - {p}" for p in report["blocking"]] + [""]
    money = ", ".join(f"{c} {t:,.2f}" for c, t in sorted(report["totals"].items())) or "nothing to load"
    lines += [
        f"Will load: {len(report['vouchers'])} voucher(s), {money}. Held back: {len(report['held'])}.",
        "Rows: " + ", ".join(f"{f} {n}" for f, n in report["files"].items() if n or f != MATCH_FILE),
        "",
    ]
    for v in report["vouchers"]:
        lines.append(
            f"WILL LOAD  AP Coder #{v['id']}  {v['vendor']}  invoice {v['number']}  {v['date']}  "
            f"{v['currency']} {float(v['total'] or 0):,.2f}"
        )
        lines.append(f"  supplier AN8 {v['an8']}; tax area {v['tax_area'] or '-'}, code {v['tax_code'] or '-'}")
        for p in v["pay_items"]:
            lines.append(
                f"  F0411Z1 pay item {p['line']}: {p['doc_type']} gross {p['gross']}"
                + (f", tax {p['tax']}" if p["tax"] else "")
                + f"; {p['dates']}"
                + (f"; PO {p['po']}" if p["po"] else "")
            )
        for d in v["lines"]:
            lines.append(f"  F0911Z1 line {d['line']}: {d['account']}  {d['amount']}  {d['text']}")
        if v.get("terms"):
            lines.append(f"  {v['terms']}")
        if v["matches"]:
            lines.append(f"  F0411Z1T: {v['matches']} PO match line(s)")
        lines += [f"  WARNING: {w}" for w in v.get("warnings") or []]
        lines.append(f"  {v['balance']}")
        lines += [f"  NOTE: {n}" for n in v.get("notes") or []]
        lines.append("")
    for h in report["held"]:
        lines.append(f"HELD BACK  AP Coder #{h['id']}  {h['vendor']}  invoice {h['number']}  {h['date']}")
        lines += [f"  - {r}" for r in h["reasons"]] + [""]
    return "\n".join(lines) + "\n"


# --- Mapping import ----------------------------------------------------------------------------------------------

GL_MAP_ALIASES = {
    "gl_code": ("glcode", "gl", "glaccount", "account", "apcodergl", "code"),
    "cost_center": ("costcenter", "costcentre", "cc", "dept", "department"),
    "bu": ("bu", "businessunit", "mcu", "jdebu"),
    "obj": ("obj", "object", "objectaccount", "jdeobject"),
    "sub": ("sub", "subsidiary", "jdesub"),
    "sbl": ("sbl", "subledger"),
    "sblt": ("sblt", "subledgertype"),
}


def gl_map_from_records(records: list[dict[str, Any]]) -> list[GlMap]:
    """GL → BU.Obj.Sub rows from a spreadsheet (columns found by name, e.g. "GL code, Cost center, BU,
    Object, Subsidiary"); rows without a GL code are skipped."""
    if not records:
        return []
    headers = {header_key(h): h for h in records[0]}
    columns = {}
    for target, aliases in GL_MAP_ALIASES.items():
        for alias in (header_key(target), *aliases):
            if alias in headers and headers[alias] not in columns.values():
                columns[target] = headers[alias]
                break
    out = []
    for rec in records:

        def get(target: str, rec: dict[str, Any] = rec) -> str:
            value = rec.get(columns[target]) if target in columns else None
            text = "" if value is None else str(value).strip()
            return "" if text.lower() in ("nan", "none") else text

        if get("gl_code"):
            out.append(GlMap(**{k: get(k) for k in GL_MAP_ALIASES}))
    return out
