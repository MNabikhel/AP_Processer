"""The JD Edwards E1 settings (Settings → JD Edwards E1): what they are and their choices (``JdeSettings``), reading
and saving them (``load``, ``save``), what stops any export (``settings_problems``), whether JD Edwards is the
export in use (``in_use``, ``show``), and the G/L date they give a voucher in the open period (``gl_date_of``,
``period_problem``). The other ``jde_*.py`` modules start from here (D-222).
"""

from __future__ import annotations

import datetime as dt
import json
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from ..coding.tax import OUTSIDE_CANADA, PROVINCES
from ..core.memory import refresh_vendor_key
from .jde_formats import _date

SETTING_KEY = "jde_e1"
FORMAT = "jde"
EXPORT_FORMAT = "export_format"  # the setting: the format the Exports page opens on (the last batch's)
# The formats of a batch whose vouchers go into JD Edwards: loaded as Z-files, or typed from the keying sheet
# (``jde_keying.FORMAT``, D-210). What JD Edwards does with a voucher (the PST it accrues itself) holds for both.
FORMATS = (FORMAT, "jde_keying")

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

LINE_NUMBERING = {
    SEQUENTIAL: "Sequential: one pay item (VLEDLN 1), distribution lines 1, 2, 3…",
    MATCH_HEADER: "Match header: one pay item per distribution line (VNEDLN = VLEDLN)",
}
GL_DATE_INVOICE, GL_DATE_EXPORT = "invoice", "export"
GL_DATES = {GL_DATE_INVOICE: "The invoice date", GL_DATE_EXPORT: "The day the batch is exported"}
# A G/L date before the open period (D-182): moved to its first day (said, and confirmed by the go-ahead), or held.
CLOSED_MOVE, CLOSED_HOLD = "move", "hold"
CLOSED_PERIODS = {CLOSED_MOVE: "Move its G/L date to the first day of the open period",
                  CLOSED_HOLD: "Hold the invoice back"}  # fmt: skip
# What the two remarks of a voucher carry (D-210): AP Coder's references (as before), or the remarks a person types.
REMARKS_REFERENCE, REMARKS_TYPED = "reference", "typed"
REMARKS = {
    REMARKS_REFERENCE: "The pay item: AP Coder's invoice number (AP Coder #12); each G/L line: the invoice number",
    REMARKS_TYPED: "The pay item: the first line's remark; each G/L line: its own remark, as typed by hand",
}
# Seconds more than a voucher's wait before a lost answer's "not there" is believed (``JdeSettings.resend_seconds``).
RESEND_MARGIN = 60.0

PST_PROVINCES = ("BC", "SK", "MB")
HST_PROVINCES = ("ON", "NB", "NL", "NS", "PE")


@dataclass
class TaxArea:
    tax_area: str
    code: str


def default_tax_map() -> dict[str, TaxArea]:
    """Province of supply → (tax area, explanation code). The area names are placeholders: each E1
    customer names its tax areas (F4008) its own way."""
    out: dict[str, TaxArea] = {}
    for p in PROVINCES:
        if p in HST_PROVINCES:
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


@dataclass
class GlMap:
    """AP Coder GL code (+ cost center, blank = any) → E1 business unit / object / subsidiary. A blank
    BU or object is filled by the default rule (BU = cost center or default BU, object = GL code, or the object
    and the subsidiary of a GL code "object.subsidiary", D-209)."""

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
    remarks: str = REMARKS_REFERENCE  # VLRMK and VNEXR
    gl_date: str = GL_DATE_INVOICE
    # JD Edwards' open period (D-182, R5): G/L dates from ``open_from`` to ``open_to`` (blank: no end) take postings;
    # one before it is in a closed period (``closed_period``: moved or held), one after it is held. Blank: not checked.
    open_from: str = ""
    open_to: str = ""
    closed_period: str = CLOSED_MOVE
    # Posting through the API (D-182): how long a voucher's request may take (R04110ZA runs in it), how long one run
    # may send, and how long after a send's answer was lost JDE's "not there" is believed (it may still be making it):
    # sent again only after that, and never sooner than a voucher's wait and ``RESEND_MARGIN`` (``resend_seconds``).
    voucher_timeout: float = 120.0
    post_minutes: float = 60.0
    resend_minutes: float = 10.0
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
        if out.remarks not in REMARKS:
            out.remarks = REMARKS_REFERENCE
        out.open_from, out.open_to = (str(_date(d) or "") for d in (out.open_from, out.open_to))
        if out.closed_period not in CLOSED_PERIODS:
            out.closed_period = CLOSED_MOVE
        for name, low, high in (("voucher_timeout", 0.1, 900.0), ("post_minutes", 0.01, 1440.0),
                                ("resend_minutes", 0.0, 1440.0)):  # fmt: skip
            try:
                setattr(out, name, min(high, max(low, float(getattr(out, name)))))
            except (TypeError, ValueError):
                setattr(out, name, getattr(base, name))
        return out

    @property
    def resend_seconds(self) -> float:
        """How long after a voucher's answer was lost JDE's "not there" is believed: ``resend_minutes``, never less
        than a voucher's own wait and ``RESEND_MARGIN`` (the request may have begun in JDE as AP Coder gave up)."""
        return max(self.resend_minutes * 60, self.voucher_timeout + RESEND_MARGIN)

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


# What a save may hold and still be no JD Edwards setup (``set_up``): the keying set-up keeps them as they are.
NO_SETUP = ("an8_overrides", "terms_codes", "payment_terms_code", "answers", "bank_glba")


def set_up(settings: JdeSettings) -> bool:
    """Whether saved settings are a JD Edwards setup someone made, which the keying set-up leaves as it is
    (``web.core.keying_to_set_up``, B-2532, B-2378): any value not the default (the department's own too: keying set
    up already) but supplier numbers, terms codes, the bank accounts and the worksheet's answers (``NO_SETUP``)."""
    mine, default = asdict(settings), asdict(JdeSettings())
    return any(mine[name] != default[name] for name in mine if name not in NO_SETUP)


def save(store: Any, settings: JdeSettings, actor: str | None = None) -> bool:
    """Save to the store's settings table; True when something changed (and is logged)."""
    text = settings.to_json()
    if text == store.get_setting(SETTING_KEY):
        return False
    store.set_setting(SETTING_KEY, text, actor=actor)
    store.log_event("settings_changed", actor=actor, detail={"keys": [SETTING_KEY]})
    return True


def show(store: Any, shown: bool = True, actor: str | None = None) -> None:
    """Turn "Show JD Edwards settings" (Settings → About) on or off without the page: on, JD Edwards E1 is a tab of
    Settings and its formats are on the Exports page, and its checks run on the review screen (``in_use``)."""
    store.set_setting(SHOW_SETTING, "1" if shown else "0", actor=actor)


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
    if settings.open_from and settings.open_to and settings.open_to < settings.open_from:
        out.append("The open period must end on or after its first day.")
    return out


def gl_date_of(
    final: Mapping[str, Any], settings: JdeSettings, on: dt.date | None = None
) -> tuple[dt.date, dt.date | None]:
    """(the voucher's G/L date (VLDGJ / VNDGJ), the date it was moved from: None when not moved). The invoice date, or
    the batch's day (``on``, else today) with Settings' G/L date; one before the open period, closed, is moved to its
    first day when closed periods are moved (D-182, R5)."""
    day = on or dt.date.today()
    base = day if settings.gl_date == GL_DATE_EXPORT else (_date(final.get("invoice_date")) or day)
    first = _date(settings.open_from)
    if first and base < first and settings.closed_period == CLOSED_MOVE:
        return first, base
    return base, None


def period_problem(day: dt.date, settings: JdeSettings) -> str:
    """Why a voucher with G/L date ``day`` can't be posted ("" when it can): in a closed period (before the open one:
    a date not moved), or after the open period (never moved, D-182)."""
    first, last = _date(settings.open_from), _date(settings.open_to)
    if first and day < first:
        return (f"G/L date {day} is in a closed period (JD Edwards' open period starts {first}, Settings → JD Edwards "
                "E1): held until the period is open, or set closed periods to be moved")  # fmt: skip
    if last and day > last:
        return (f"G/L date {day} is after the open period (it ends {last}, Settings → JD Edwards E1): held until that "
                "period is open")  # fmt: skip
    return ""


MOVED = "G/L date moved to"


def moved_note(day: dt.date, moved: dt.date) -> str:
    """What the review screen and the dry run say of a G/L date moved into the open period."""
    return f"{MOVED} {day}, the first day of JD Edwards' open period: {moved} is in a closed period"


SHOW_SETTING = "show_jde_settings"  # web.core.SHOW_JDE: "1" when JD Edwards is the export in use


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
