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
from typing import Any

from .memory import vendor_key
from .safe import csv_row
from .tax import CANADIAN_TAX_TYPES, OUTSIDE_CANADA, PROVINCES, ZERO_DECIMAL_CURRENCIES
from .vendors import norm_invoice_number

SETTING_KEY = "jde_e1"
FORMAT = "jde"
LABEL = "JD Edwards E1 (F0411Z1 / F0911Z1)"

AMOUNT_GROSS, AMOUNT_TAX = "gross", "tax"
AMOUNT_MODES = {
    AMOUNT_GROSS: "Gross amount only (VLAG): E1 calculates the tax from the tax area",
    AMOUNT_TAX: "Taxable / non-taxable / tax amounts (VLATXA, VLATXN, VLSTAM): the tax printed on the invoice",
}
IMPLIED, POINT = "implied", "point"
DECIMAL_MODES = {IMPLIED: "Implied decimals ($5.50 → 550)", POINT: "Decimal point ($5.50 → 5.50)"}
ACCOUNT_ANI, ACCOUNT_COLUMNS = "ani", "columns"
ACCOUNT_MODES = {
    ACCOUNT_ANI: "One account field (VNAM = 2, VNANI = BU.Object.Subsidiary)",
    ACCOUNT_COLUMNS: "Separate columns (VNMCU, VNOBJ, VNSUB)",
}
SEQUENTIAL, MATCH_HEADER = "sequential", "match_header"
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
    "the tax area names (ON-HST, QC-GSTQST, …) and explanation codes",
    "VLCRR (exchange rate): left blank when no rate is set, so E1 takes it from the exchange-rate table",
)

HEADER_FILE, DIST_FILE, MATCH_FILE = "F0411Z1.csv", "F0911Z1.csv", "F0411Z1T.csv"


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
    amount_mode: str = AMOUNT_GROSS
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
    payment_terms_code: str = ""  # blank: E1 takes the supplier's terms
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
        if out.amount_mode not in AMOUNT_MODES:
            out.amount_mode = AMOUNT_GROSS
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
    if value.year < 1900:
        raise ValueError(f"{value} is before 1900")
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
    if not gl:
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


def ani(account: GlMap) -> str:
    """VNANI in format 2: BU.Object.Subsidiary ("CC400.6010", "CC400.6010.01")."""
    return f"{account.bu}.{account.obj}" + (f".{account.sub}" if account.sub else "")


# --- One voucher ----------------------------------------------------------------------------------------------


@dataclass
class _Voucher:
    inv_id: int
    final: dict[str, Any]
    problems: list[str]
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


def _place_of_supply(final: dict[str, Any]) -> str:
    for key in ("ship_to_province", "supplier_province"):
        prov = _norm(final.get(key))
        if prov in PROVINCES:
            return prov
    if OUTSIDE_CANADA in (_norm(final.get("ship_to_province")), _norm(final.get("supplier_province"))):
        return OUTSIDE_CANADA
    return ""


def _distribution(final: dict[str, Any]) -> list[dict[str, Any]]:
    dist = final.get("gl_distribution")
    if dist:
        return list(dist)
    # Not stored (an old invoice): rebuild it with the default tax treatments; recoverable taxes are dropped
    # from the F0911Z1 lines anyway, so their GL accounts do not matter here.
    from .schema import InvoiceCoding
    from .tax import TaxRateTable, TaxSetup, build_gl_distribution

    try:
        coding = InvoiceCoding.model_validate({k: v for k, v in final.items() if k != "gl_distribution"})
    except ValueError:
        return []
    return build_gl_distribution(coding, TaxSetup(TaxRateTable(()), {}))


def _is_recoverable_entry(e: dict[str, Any]) -> bool:
    return e.get("kind") == "tax" and abs(float(e.get("non_recoverable_tax") or 0)) < 0.005


def _prepare(inv: dict[str, Any], settings: JdeSettings, an8: str) -> _Voucher:
    final = inv.get("final_output") or {}
    v = _Voucher(int(inv.get("id") or 0), final, [])
    add = v.problems.append

    if not an8:
        add("no JDE supplier number (AN8): set the vendor's ERP ID in the vendor master, or in Settings → JD Edwards")
    elif not an8.isdigit() or len(an8) > 8:
        add(f"supplier number {an8!r} is not a JDE address number (digits only, up to 8)")
    number = str(final.get("invoice_number") or "").strip()
    if not number:
        add("no invoice number (VLVINV)")
    elif len(number) > 25:
        add(f"invoice number {number!r} is longer than 25 characters (VLVINV)")
    po = str(final.get("po_number") or "").strip()
    if po and len(po) > 8 and (settings.send_po_reference or settings.po_matched):
        add(f"PO number {po!r} is longer than 8 characters (VLPO)")
    if _date(final.get("invoice_date")) is None:
        add("no invoice date")

    v.currency = _norm(final.get("currency")) or settings.domestic_currency.upper()
    allowed = {_norm(c) for c in settings.currencies} | {_norm(settings.domestic_currency)}
    if v.currency not in allowed:
        add(f"currency {v.currency} is not set up for JD Edwards (Settings → JD Edwards → currencies)")
    v.foreign = v.currency != _norm(settings.domestic_currency)
    v.decimals = NO_DECIMAL_CURRENCIES.get(v.currency, 2)

    # Tax area and explanation code from the province of supply.
    charged = [
        t for t in final.get("tax_lines") or []
        if t.get("tax_type") in CANADIAN_TAX_TYPES and abs(float(t.get("tax_amount") or 0)) > 0.004
    ]  # fmt: skip
    types = {t["tax_type"] for t in charged}
    v.province = _place_of_supply(final)
    row = settings.tax_map.get(v.province) or TaxArea("", "")
    v.tax_area, v.tax_code = row.tax_area.strip(), row.code.strip().upper()
    if charged and v.province in ("", OUTSIDE_CANADA):
        add("province of supply unknown: no JDE tax area for the Canadian tax charged")
    elif not charged and v.province != OUTSIDE_CANADA:
        v.tax_code = settings.exempt_code.strip().upper()  # nothing charged: exempt (keeps the area)
    elif v.province in PST_PROVINCES and types & {"GST", "HST"} and "PST" not in types:
        v.tax_code = settings.self_assessed_code.strip().upper()  # PST to self-assess
    if v.tax_code not in RECOVERABLE_BY_CODE:
        add(f"tax explanation code {v.tax_code!r} is not one AP Coder can balance (V, C, B, E or blank)")
    elif charged and v.tax_code in ("V", "C", "B") and not v.tax_area:
        add(f"no JDE tax area for {v.province} (Settings → JD Edwards → tax areas)")

    # In the currency's own units: whole yen are sent as whole yen, so the voucher balances as E1 reads it.
    v.gross = _units(final.get("grand_total"), v.decimals)
    v.stam = sum(_units(t.get("tax_amount"), v.decimals) for t in charged)
    v.atxa = max((_units(t.get("taxable_amount"), v.decimals) for t in charged), key=abs, default=0)
    v.charged, v.recoverable_types = charged, RECOVERABLE_BY_CODE.get(v.tax_code, frozenset())
    recoverable = [t for t in charged if t["tax_type"] in v.recoverable_types]
    v.recoverable = sum(_units(t.get("tax_amount"), v.decimals) for t in recoverable)

    v.po_matched = bool(settings.po_matched and po)
    if v.po_matched:
        items = final.get("line_items") or []
        lines = sum(_cents(li.get("amount")) for li in items)
        if not items:
            add("PO-matched voucher without invoice lines to match")
        elif abs(lines - _cents(final.get("subtotal"))) > len(items):
            add(f"the lines add up to {lines / 100:,.2f}, not the subtotal {float(final.get('subtotal') or 0):,.2f}")
        return v

    dist = _distribution(final)
    if not dist:
        add("no GL distribution to post")
    total = sum(_cents(e.get("amount")) for e in dist)
    gross_cents = _cents(final.get("grand_total"))
    if dist and total != gross_cents:
        add(f"the GL distribution adds up to {total / 100:,.2f}, not the invoice total {v.gross / 100:,.2f}")
    for e in dist:
        if _is_recoverable_entry(e):
            continue  # E1 posts recoverable tax itself (tax AAIs)
        account = account_for(e.get("gl_code") or "", e.get("cost_center") or "", settings)
        where = f"GL {e.get('gl_code') or '(none)'}" + (f" / {e['cost_center']}" if e.get("cost_center") else "")
        if account is None:
            add(f"{where} has no JDE account (BU.Object.Subsidiary): add it to the account mapping")
        elif len(account.bu) > 12 or len(account.obj) > 6 or len(account.sub) > 8:
            add(f"{where}: JDE account {ani(account)} is too long (BU 12, object 6, subsidiary 8 characters)")
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
    if dist and total == gross_cents and kept != expected:
        add(
            f"unbalanced: the distribution is {kept / 100:,.2f} but gross {v.gross / 100:,.2f} minus recoverable "
            f"tax {v.recoverable / 100:,.2f} (code {v.tax_code or 'blank'}) is {expected / 100:,.2f}: check the Tax "
            "setup against the JDE tax explanation code"
        )
    return v


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
    due = _date(final.get("due_date") or invoice.get("due_date"))
    if v.gross < 0 or due is None:
        due = invoice_date  # a credit note is not "due"; E1 needs a date
    doc_type = _doc_type(v, settings)
    po = str(final.get("po_number") or "").strip()
    rate = (fx_rates or {}).get(v.currency) if v.foreign else None
    common = {
        **keys, "VLDCT": doc_type, "VLCO": settings.company_code, "VLMCU": settings.header_bu.strip(),
        "VLAN8": str(vendor_an8 or "").strip(), "VLVINV": str(final.get("invoice_number") or "").strip()[:25],
        "VLDIVJ": julian(invoice_date), "VLDGJ": julian(gl_date), "VLDSVJ": julian(invoice_date),
        "VLTXA1": v.tax_area, "VLEXR1": v.tax_code, "VLCRRM": "F" if v.foreign else "D", "VLCRCD": v.currency,
        "VLCRR": _plain(rate, 7) if rate else "", "VLPTC": settings.payment_terms_code.strip(),
        "VLDDJ": julian(due) if settings.send_due_date else "", "VLPST": settings.pay_status.strip(),
        "VLPO": po[:8] if po and (settings.send_po_reference or v.po_matched) else "",
        "VLPDCT": settings.po_document_type.strip() if po and (settings.send_po_reference or v.po_matched) else "",
        "VLPKCO": (settings.po_company.strip().zfill(5) if settings.po_company.strip().isdigit()
                   else settings.po_company.strip() or settings.company_code) if po else "",
        "VLRMK": f"AP Coder #{v.inv_id}"[:30],
    }  # fmt: skip

    def amounts(gross: int, stam: int, atxa: int) -> dict[str, Any]:
        out: dict[str, Any] = {}
        prefix = ("VLACR", "VLCTXA", "VLCTXN", "VLCTAM") if v.foreign else ("VLAG", "VLATXA", "VLATXN", "VLSTAM")
        if settings.amount_mode == AMOUNT_GROSS:
            out[prefix[0]] = _amount(gross, settings, v.decimals)
        elif v.tax_code in ("E", "") or not stam:
            out.update({prefix[1]: _amount(0, settings, v.decimals), prefix[2]: _amount(gross, settings, v.decimals),
                        prefix[3]: _amount(0, settings, v.decimals)})  # fmt: skip
        else:
            out.update({prefix[1]: _amount(atxa, settings, v.decimals),
                        prefix[2]: _amount(gross - atxa - stam, settings, v.decimals),
                        prefix[3]: _amount(stam, settings, v.decimals)})  # fmt: skip
        return out

    extras = dict(settings.po_extra_columns) if settings.po_matched else {}
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
            "VNEXR1": v.tax_code if settings.dist_tax_columns else "",
        }  # fmt: skip
        if settings.account_mode == ACCOUNT_ANI:
            row.update({"VNAM": "2", "VNANI": ani(acct) if account else ""})
        else:
            row.update({"VNMCU": acct.bu, "VNOBJ": acct.obj, "VNSUB": acct.sub})
        return row

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
        stams = [s + _units(e.get("non_recoverable_tax"), v.decimals) for e, s in zip(v.entries, shares, strict=True)]
        stams[max(range(len(base)), key=lambda i: abs(base[i]))] += v.stam - sum(stams)
        grosses = [e["cents"] + s for e, s in zip(v.entries, shares, strict=True)]
        headers = [
            {**common, "VLEDLN": n, **amounts(g, s, a), **extras}
            for n, (g, s, a) in enumerate(zip(grosses, stams, atxas, strict=True), 1)
        ]
        return headers, [dist_row(n, e) for n, e in enumerate(v.entries, 1)], []
    header = {**common, "VLEDLN": 1, **amounts(v.gross, v.stam, v.atxa), **extras}
    return [header], [dist_row(n, e) for n, e in enumerate(v.entries, 1)], []


def _entry_taxes(entries: list[dict[str, Any]], items: list[dict[str, Any]]) -> list[set[str]]:
    """The taxes the invoice line behind each distribution line is marked with (none for a tax line). Expense
    lines follow the invoice lines in order; matched by line number when the counts differ."""
    if sum(1 for e in entries if e.get("kind") == "expense") == len(items):
        marked = iter([set(li.get("taxes_applied") or []) for li in items])
        return [next(marked) if e.get("kind") == "expense" else set() for e in entries]
    by_number: dict[Any, set[str]] = {}
    for li in items:
        by_number.setdefault(li.get("line_number"), set(li.get("taxes_applied") or []))
    return [by_number.get(e.get("line_number"), set()) if e.get("kind") == "expense" else set() for e in entries]


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

    def an8_of_name(name: str) -> str:
        return resolve_an8({"vendor_name": name}, vendor_lookup, settings)

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
            if same(who, (an8_of_name(r.get("vendor_name") or ""), vendor_key(r.get("vendor_name") or ""))):
                msgs.append(f"already exported: AP Coder #{r['id']} in batch {r['export_batch']}")
                break
        for r in prior.get("register") or []:
            if norm_invoice_number(r.get("invoice_number")) != number or _negative(r.get("total")) != credit:
                continue
            key = r.get("vendor_key") or vendor_key(r.get("vendor_name") or "")
            if same(who, (an8_of_name(r.get("vendor_name") or ""), key)):
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


# --- Files -------------------------------------------------------------------------------------------------------


def header_columns(settings: JdeSettings) -> list[str]:
    extra = [c for c in settings.po_extra_columns if c not in HEADER_COLUMNS] if settings.po_matched else []
    return HEADER_COLUMNS + extra


def _csv(columns: list[str], rows: list[dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(columns)
    for r in rows:
        writer.writerow(csv_row(["" if r.get(c) is None else r.get(c) for c in columns]))
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


def readme(
    files: dict[str, bytes], settings: JdeSettings, batch: int | str, invoices: list[dict[str, Any]],
    left_out: Mapping[int, list[str]] | None = None,
) -> str:  # fmt: skip
    """README.txt of the ZIP: what is in it and how to load it."""
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
    amounts = "gross only (VLAG / VLACR)" if settings.amount_mode == AMOUNT_GROSS else "taxable, non-taxable and tax"
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
        "If the load fails: in AP Coder, Exports → Past batches → Undo this batch, fix, and export again.",
        "Each voucher's transaction number (EDTN) is APC + the AP Coder invoice number.",
        "",
        "To confirm with your JDE team before the first live load: " + "; ".join(UNCONFIRMED) + ".",
    ]
    if left_out:
        lines += ["", "Not in this batch (fix them, then export again)", "-" * 47]
        for inv_id, problems in left_out.items():
            lines.append(f"AP Coder #{inv_id}: " + "; ".join(problems))
    return "\n".join(lines) + "\n"


def build_zip(
    invoices: list[dict[str, Any]], settings: JdeSettings, vendor_lookup: VendorLookup = None, *,
    batch: int | str = "", fx_rates: Mapping[str, float] | None = None, on: dt.date | None = None,
    left_out: Mapping[int, list[str]] | None = None,
) -> tuple[bytes, str, str]:  # fmt: skip
    """(ZIP bytes, file name, mime type): the CSVs and a README.txt."""
    files = export_batch(invoices, settings, vendor_lookup, batch=batch, fx_rates=fx_rates, on=on)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
        zf.writestr("README.txt", readme(files, settings, batch, invoices, left_out))
    name = f"ap_coder_jde_batch_{batch}.zip" if batch != "" else "ap_coder_jde.zip"
    return buf.getvalue(), name, "application/zip"


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
    headers = {re.sub(r"[^a-z0-9]", "", str(h).lower()): h for h in records[0]}
    columns = {}
    for target, aliases in GL_MAP_ALIASES.items():
        for alias in (re.sub(r"[^a-z0-9]", "", target), *aliases):
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
