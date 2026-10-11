"""Per-supplier learning and the path to touchless processing ("autonomy").

Pure logic, no Streamlit and no database (the store keeps what this module computes):

* ``supplier_key``: one stable key per supplier (vendor master id, else GST/HST business number, else
  the normalized name).
* ``Template``: where each header field is printed on this supplier's invoices, learned from values AP
  confirmed (``learn``) and read back on the next invoice (``apply_template``). Per field up to
  ``MAX_VARIANTS`` variants, each with the page, the value box, the label printed next to it (the
  *anchor*) with the offset from the label to the value, and the *shape* of the confirmed values.
* ``SupplierStats`` / ``wilson_lower`` / ``autonomy_status``: how accurate AP Coder has been on this
  supplier, and whether it has earned touchless processing under the ``AutonomyPolicy`` (one fixed bar for
  every supplier).
* ``automatic_transition``: with touchless processing on (one company-wide switch), a supplier that meets
  the bar goes touchless by itself, and one that no longer may goes back to review.
* ``touchless_gates`` / ``should_auto_approve`` / ``pick_for_audit``: the decision for one invoice of a
  touchless supplier; some invoices always go to a person whatever the supplier's record.

States: ``learning`` (too few invoices), ``supervised``, ``ready`` (meets the bar; goes touchless as soon
as touchless processing is on), ``autonomous`` (touchless), ``suspended`` (a person found an error since it
went touchless: it needs a fresh clean streak to go touchless again) and ``held`` (a manager keeps it
supervised whatever its record). Only ``supervised``, ``autonomous``, ``suspended`` and ``held`` are stored;
``learning`` and ``ready`` are computed.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import math
import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from functools import lru_cache
from typing import Any

from ..memory import vendor_key
from .types import (
    AMOUNT_FIELDS,
    DATE_FIELDS,
    FIELDS,
    MISSING,
    VERIFIED,
    Box,
    CaptureResult,
    DocLayout,
    Reading,
    Word,
    union_all,
)

MAX_VARIANTS = 3
MAX_PATTERNS = 5
ID_FIELDS = ("invoice_number", "po_number")
TAX_ID_FIELDS = ("gst_hst_registration_number", "qst_registration_number")
REQUIRED_FIELDS = ("vendor_name", "invoice_number", "invoice_date", "grand_total")  # must be found to go touchless

LEARNING, SUPERVISED, READY, AUTONOMOUS, SUSPENDED = "learning", "supervised", "ready", "autonomous", "suspended"
HELD = "held"  # a manager keeps this supplier supervised, whatever its record
STATES = (LEARNING, SUPERVISED, READY, AUTONOMOUS, SUSPENDED, HELD)
STORED_STATES = (SUPERVISED, AUTONOMOUS, SUSPENDED, HELD)  # what touchless processing, a manager or a found error sets
STATE_LABELS = {
    LEARNING: "Learning", SUPERVISED: "Supervised", READY: "Ready", AUTONOMOUS: "Autonomous", SUSPENDED: "Suspended",
    HELD: "Kept supervised",
}  # fmt: skip

ANCHOR_SCORE, POSITION_SCORE, SHAPE_WEIGHT = 0.75, 0.45, 0.2
ABOVE_MAX = 0.05  # a label above its value is at most this far above it (fraction of the page)
# A value read at its label's learned offset gains up to EXPERIENCE_BONUS in score as the variant is
# confirmed again, in full after EXPERIENCE_FULL more confirmations (a label-anchored score tops out at 0.99).
EXPERIENCE_BONUS, EXPERIENCE_FULL = 0.04, 4
RIVAL_MARGIN = 0.1  # template readings scoring this much below the best are not offered
_LABEL_WORDS = {
    "invoice", "inv", "facture", "date", "total", "subtotal", "sous", "tps", "tvq", "gst", "hst", "pst", "qst", "rst",
    "tax", "taxe", "taxes", "po", "order", "commande", "bon", "due", "echeance", "terms", "conditions", "amount",
    "montant", "balance", "solde", "no", "number", "numero", "ref", "reference", "currency", "devise", "bn",
    "registration", "payer", "pay",
}  # fmt: skip
_CURRENCY_WORDS = {"cad", "usd", "eur", "gbp", "ca", "us", "cdn", "dr", "cr"}
_RATE = re.compile(r"[(@]*\d*(?:[.,]\d+)?\s*%\)?:?|@")  # "5%", "(13%)", "%)", "5%:", "@"


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


# --- Supplier key ---------------------------------------------------------------------------------------------


def supplier_key(vendor_name: str | None, gst_number: str | None = None, vendor_id: str | None = None) -> str:
    """``id:<vendor master id>``, else ``bn:<the 9 digits of the GST/HST business number>``, else
    ``name:<memory.vendor_key(name)>``; "" when there is nothing to go on."""
    vid = str(vendor_id or "").strip()
    if vid:
        return f"id:{vid}"
    digits = re.sub(r"\D", "", str(gst_number or ""))
    if len(digits) >= 9:
        return f"bn:{digits[:9]}"
    name = vendor_key(vendor_name or "")
    return f"name:{name}" if name else ""


# --- Normalizing (local; ``ap_coder.capture.normalize`` is used when it exists) --------------------------------


def _fold(text: Any) -> str:
    text = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(c for c in text if not unicodedata.combining(c)).lower()


def _compact(text: Any) -> str:
    return re.sub(r"[^0-9a-z]", "", _fold(text))


def parse_amount(text: Any) -> float | None:
    """ "1,234.56", "1 234,56 $", "(12.00)", "-12.00", "12.00 CR", "$1,234.56" -> float; None if not an amount."""
    if isinstance(text, (int, float)) and not isinstance(text, bool):
        return float(text)
    s = str(text or "").strip()
    if not s or not re.search(r"\d", s):
        return None
    low = _fold(s)
    words = re.findall(r"[a-z]+", low)
    if any(w not in _CURRENCY_WORDS for w in words):
        return None  # letters other than a currency code or CR: an id, not an amount
    negative = "cr" in words or ("(" in s and ")" in s) or bool(re.match(r"^\s*[-−–]", s)) or s.rstrip().endswith("-")
    s = re.sub(r"[^\d.,\s'  ]", "", s).strip()
    s = re.sub(r"(?<=\d)['\s  ]+(?=\d{3}(?:\D|$))", "", s)  # "1 234,56" / "1'234.56"
    if not re.fullmatch(r"\d[\d.,]*", s):
        return None
    m = re.search(r"[.,](\d{1,2})$", s)
    int_part, dec = (s[: m.start()], m.group(1)) if m else (s, "")
    if re.search(r"[.,]", int_part) and not re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", int_part):
        return None
    value = float(f"{re.sub(r'[.,]', '', int_part) or '0'}.{dec or '0'}")
    return -value if negative else value


_MONTHS = {
    "jan": 1, "janv": 1, "january": 1, "janvier": 1, "feb": 2, "fev": 2, "fevr": 2, "february": 2, "fevrier": 2,
    "mar": 3, "march": 3, "mars": 3, "apr": 4, "avr": 4, "april": 4, "avril": 4, "may": 5, "mai": 5,
    "jun": 6, "june": 6, "juin": 6, "jul": 7, "july": 7, "juil": 7, "juillet": 7, "aug": 8, "august": 8, "aou": 8,
    "aout": 8, "sep": 9, "sept": 9, "september": 9, "septembre": 9, "oct": 10, "october": 10, "octobre": 10,
    "nov": 11, "november": 11, "novembre": 11, "dec": 12, "december": 12, "decembre": 12,
}  # fmt: skip


def _iso(y: int, m: int, d: int) -> str | None:
    if y < 100:
        y += 2000
    try:
        return dt.date(y, m, d).isoformat()
    except ValueError:
        return None


def date_candidates(text: Any) -> list[tuple[str, str]]:
    """Every reading of a printed date as (ISO date, order) with order "ymd", "dmy", "mdy" or "text"."""
    s = _fold(text).strip()
    m = re.fullmatch(r"(\d{4})[-/. ](\d{1,2})[-/. ](\d{1,2})", s)
    if m:
        iso = _iso(int(m[1]), int(m[2]), int(m[3]))
        return [(iso, "ymd")] if iso else []
    m = re.fullmatch(r"(\d{1,2})[-/. ](\d{1,2})[-/. ](\d{2}|\d{4})", s)
    if m:
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        out = [(iso, order) for iso, order in ((_iso(y, b, a), "dmy"), (_iso(y, a, b), "mdy")) if iso]
        return out[:1] if a == b else out
    words = re.findall(r"[a-z]+|\d+", s)
    if len(words) > 6:
        return []
    month = next((_MONTHS[w] for w in words if w in _MONTHS), None)
    numbers = [w for w in words if w.isdigit()]
    others = [
        w for w in words if not w.isdigit() and w not in _MONTHS and w not in {"er", "st", "nd", "rd", "th", "le"}
    ]
    if month is None or others or len(numbers) != 2:
        return []
    year = next((int(n) for n in numbers if len(n) == 4), None)
    day = next((int(n) for n in numbers if len(n) <= 2), None)
    iso = _iso(year, month, day) if year and day else None
    return [(iso, "text")] if iso else []


def parse_date(text: Any, order: str | None = None) -> str | None:
    """A printed date as YYYY-MM-DD. A numeric day/month that could be either is read in ``order``
    ("dmy" or "mdy", e.g. learned for the supplier), else day first."""
    found = date_candidates(text)
    if not found:
        return None
    return next((iso for iso, o in found if o == order), found[0][0])


def norm_id(text: Any) -> str:
    """An invoice or PO number as printed, tidied: no label prefix, single spaces, upper case."""
    s = " ".join(str(text or "").split()).strip(" :#.,;")
    s = re.sub(r"^(?:n[o°º]\.?|#)\s*", "", s, flags=re.I).strip(" :#")
    return s.upper()


def _norm_tax_id(text: Any) -> str:
    return re.sub(r"[^0-9A-Z]", "", str(text or "").upper())


def _currency(text: Any) -> str | None:
    up = str(text or "").upper()
    for code in ("CAD", "USD", "EUR", "GBP"):
        if re.search(rf"\b{code}\b", up):
            return code
    return {"CDN": "CAD", "CDN$": "CAD", "CA$": "CAD", "US$": "USD", "€": "EUR", "£": "GBP"}.get(up.strip())


def _local_normalize(name: str, raw: Any, date_order: str | None = None) -> Any:
    if name in AMOUNT_FIELDS:
        return parse_amount(raw)
    if name in DATE_FIELDS:
        return parse_date(raw, date_order)
    if name in ID_FIELDS:
        return norm_id(raw) or None
    if name in TAX_ID_FIELDS:
        return _norm_tax_id(raw) or None
    if name == "currency":
        return _currency(raw)
    return " ".join(str(raw or "").split()).strip(" :") or None


@lru_cache(maxsize=1)
def _external_normalizer() -> Any:
    try:
        from . import normalize  # written separately; optional here
    except Exception:
        return None
    return getattr(normalize, "normalize_value", None)


def normalize_value(name: str, raw: Any, date_order: str | None = None) -> Any:
    """The normalized value of ``raw`` for field ``name`` (float amount, ISO date, tidy id...), or None."""
    if name in DATE_FIELDS and date_order in ("dmy", "mdy") and re.fullmatch(r"\s*\d{1,2}[-/. ]\d{1,2}[-/. ]\d+\s*",
                                                                             str(raw or "")):  # fmt: skip
        return parse_date(raw, date_order)  # the order this supplier prints its dates in
    ext = _external_normalizer()
    # The shared normalizer gives comparison keys; ids, names and terms keep their printed form here.
    if ext is not None and (name in AMOUNT_FIELDS or name in DATE_FIELDS or name.endswith("registration_number")):
        try:
            value = ext(name, raw)
        except Exception:
            value = None
        if value not in (None, ""):
            return value
    return _local_normalize(name, raw, date_order)


def compare_key(name: str, value: Any) -> Any:
    """What two values of a field must share to be the same: rounded amount, ISO date, alphanumerics."""
    if value is None or value == "":
        return None
    if name in AMOUNT_FIELDS:
        amount = parse_amount(value)
        return None if amount is None else round(amount, 2)
    if name in DATE_FIELDS:
        if isinstance(value, (dt.date, dt.datetime)):
            return value.isoformat()[:10]
        return parse_date(value)
    if name == "currency":
        return _currency(value) or _compact(value).upper() or None
    if name == "vendor_name":
        return vendor_key(str(value)) or None
    if name in ID_FIELDS:
        return _compact(norm_id(value)) or None
    if name == "payment_terms":
        return _terms_key(value)
    return _compact(value) or None


def _terms_key(value: Any) -> str | None:
    """Terms by what they mean ("30 days" = "Net 30"; "2% 10, Net 30" is other terms)."""
    s = _fold(value)
    numbers = re.findall(r"\d+", s)
    receipt = bool(re.search(r"receipt|reception", s))
    if not numbers and not receipt:
        return _compact(value) or None
    return "terms:" + "/".join(str(int(n)) for n in numbers) + (":receipt" if receipt else "")


def _matches(name: str, raw: str, value: Any) -> str | None:
    """Does printed ``raw`` show ``value``? Returns the date order it was read in (or "" for a match)."""
    if name == "payment_terms" and (not _has_letters(raw) or any(t.endswith(":") for t in raw.split())):
        return None  # the terms as printed ("30 days"): not just the number in them, nor with their label
    if name in DATE_FIELDS:
        target = compare_key(name, value)
        for iso, order in date_candidates(raw):
            if iso == target:
                return order
        return None
    if name in AMOUNT_FIELDS:
        a, b = parse_amount(raw), compare_key(name, value)
        return "" if a is not None and b is not None and abs(abs(a) - abs(b)) < 0.005 else None
    key = compare_key(name, value)
    return "" if key is not None and compare_key(name, raw) == key else None


# --- Value shapes ---------------------------------------------------------------------------------------------


def value_shape(text: Any) -> str:
    """Character classes: letters "A", digits "9", the rest as printed ("INV-2026-0912" -> "AAA-9999-9999")."""
    return "".join("9" if c.isdigit() else "A" if c.isalpha() else c for c in " ".join(str(text or "").split()))


def _collapsed(shape: str) -> str:
    return re.sub(r"(.)\1+", r"\1", shape)


@dataclass
class Shape:
    patterns: dict[str, int] = field(default_factory=dict)
    min_len: int = 0
    max_len: int = 0

    def add(self, raw: str) -> None:
        text = " ".join(str(raw or "").split())
        if not text:
            return
        pattern = value_shape(text)
        self.patterns[pattern] = self.patterns.get(pattern, 0) + 1
        if len(self.patterns) > MAX_PATTERNS:
            del self.patterns[min(self.patterns, key=lambda p: self.patterns[p])]
        self.min_len = len(text) if not self.max_len else min(self.min_len, len(text))
        self.max_len = max(self.max_len, len(text))

    def score(self, name: str, raw: str) -> float:
        """0..1: how well ``raw`` fits the values confirmed so far (amounts and dates: does it parse)."""
        text = " ".join(str(raw or "").split())
        if not text:
            return 0.0
        if name in AMOUNT_FIELDS:
            return 1.0 if parse_amount(text) is not None else 0.0
        if name in DATE_FIELDS:
            return 1.0 if date_candidates(text) else 0.0
        if not self.patterns:
            return 0.5
        pattern = value_shape(text)
        if pattern in self.patterns:
            return 1.0
        in_range = self.min_len - 2 <= len(text) <= self.max_len + 2
        if in_range and _collapsed(pattern) in {_collapsed(p) for p in self.patterns}:
            return 0.7  # same structure, a digit more or less (INV-999 -> INV-1000)
        if name in ID_FIELDS or name in TAX_ID_FIELDS or name == "currency":
            return 0.0
        return 0.3 if in_range else 0.1

    def fit(self, raw: str) -> float:
        """0..1: how much ``raw`` looks like the values confirmed so far, by their printed pattern only
        ("2 292,84" fits "9 999,99"; its first word "2" does not), whatever the field."""
        text = " ".join(str(raw or "").split())
        if not self.patterns or not text:
            return 0.5 if text else 0.0
        pattern = value_shape(text)
        if pattern in self.patterns:
            return 1.0
        in_range = self.min_len - 2 <= len(text) <= self.max_len + 2
        if in_range and _collapsed(pattern) in {_collapsed(p) for p in self.patterns}:
            return 0.7
        return 0.3 if in_range else 0.1

    def to_dict(self) -> dict[str, Any]:
        return {"patterns": dict(self.patterns), "min_len": self.min_len, "max_len": self.max_len}

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Shape:
        d = d or {}
        return cls(dict(d.get("patterns") or {}), int(d.get("min_len") or 0), int(d.get("max_len") or 0))


# --- Template --------------------------------------------------------------------------------------------------


def _box4(b: Box) -> list[float]:
    return b.to_list()[1:]


@dataclass
class Anchor:
    text: str  # the label as printed, e.g. "Invoice No:"
    box: list[float]  # [x0, y0, x1, y1] on the value's page
    offset: list[float]  # [dx, dy]: value box top-left minus anchor box top-left
    where: str = "left"  # "left" | "above"

    @property
    def key(self) -> str:
        return _compact(self.text)

    def to_dict(self) -> dict[str, Any]:
        return {"text": self.text, "box": [round(v, 5) for v in self.box],
                "offset": [round(v, 5) for v in self.offset], "where": self.where}  # fmt: skip

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Anchor | None:
        if not d or not d.get("text"):
            return None
        return cls(
            str(d["text"]), [float(v) for v in d["box"]], [float(v) for v in d["offset"]], d.get("where", "left")
        )


@dataclass
class Variant:
    page: int | str  # 1, 2... or "last" (totals on multi-page invoices)
    box: list[float]  # [x0, y0, x1, y1], fractions of the page
    anchor: Anchor | None = None
    shape: Shape = field(default_factory=Shape)
    count: int = 0
    last_seen: str = ""
    date_order: str | None = None  # "dmy" / "mdy" when the supplier's numeric dates told us

    def to_dict(self) -> dict[str, Any]:
        return {
            "page": self.page, "box": [round(v, 5) for v in self.box],
            "anchor": self.anchor.to_dict() if self.anchor else None, "shape": self.shape.to_dict(),
            "count": self.count, "last_seen": self.last_seen, "date_order": self.date_order,
        }  # fmt: skip

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Variant:
        page = d.get("page", 1)
        return cls(
            page="last" if page == "last" else int(page), box=[float(v) for v in d["box"]],
            anchor=Anchor.from_dict(d.get("anchor")), shape=Shape.from_dict(d.get("shape")),
            count=int(d.get("count") or 0), last_seen=d.get("last_seen") or "", date_order=d.get("date_order"),
        )  # fmt: skip


@dataclass
class Template:
    fields: dict[str, list[Variant]] = field(default_factory=dict)
    invoices: int = 0  # confirmed invoices learned from
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": 1, "invoices": self.invoices, "updated_at": self.updated_at,
            "fields": {k: [v.to_dict() for v in vs] for k, vs in self.fields.items()},
        }  # fmt: skip

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> Template:
        d = d or {}
        return cls(
            fields={k: [Variant.from_dict(v) for v in vs] for k, vs in (d.get("fields") or {}).items()},
            invoices=int(d.get("invoices") or 0), updated_at=d.get("updated_at") or "",
        )  # fmt: skip

    def learned_fields(self) -> list[str]:
        return [f for f, vs in self.fields.items() if vs]


# --- Layout helpers --------------------------------------------------------------------------------------------


def _rows(words: list[Word]) -> list[list[Word]]:
    """Words grouped into visual rows (top to bottom), each left to right."""
    rows: list[list[Word]] = []
    for w in sorted(words, key=lambda w: (w.box.cy, w.box.x0)):
        if rows:
            row = rows[-1]
            cy = sum(x.box.cy for x in row) / len(row)
            h = max(sum(x.box.height for x in row) / len(row), w.box.height, 1e-4)
            if abs(w.box.cy - cy) <= 0.5 * h:
                row.append(w)
                continue
        rows.append([w])
    return [sorted(r, key=lambda w: w.box.x0) for r in rows]


def _span_box(words: list[Word]) -> Box:
    out = words[0].box
    for w in words[1:]:
        out = out.union(w.box)
    return out


def _join(words: Iterable[Word]) -> str:
    return " ".join(w.text for w in words)


def _has_letters(text: str) -> bool:
    return sum(c.isalpha() for c in text) >= 2


def _looks_like_label(text: str) -> bool:
    folded = _fold(text)
    return text.rstrip().endswith((":", "#")) or bool(set(re.findall(r"[a-z]+", folded)) & _LABEL_WORDS)


def _page(layout: DocLayout, page: int | str) -> Any:
    if not layout.pages:
        return None
    if page == "last":
        return layout.pages[-1]
    return next((p for p in layout.pages if p.number == page), None)


def _page_ref(layout: DocLayout, number: int) -> int | str:
    last = layout.pages[-1].number if layout.pages else 1
    return "last" if len(layout.pages) > 1 and number == last else number


def _find_anchor_words(rows: list[list[Word]], value: list[Word]) -> tuple[list[Word], str] | None:
    """The label printed nearest left of the value on its row, or just above it."""
    vbox = _span_box(value)
    ids = {id(w) for w in value}
    candidates: list[tuple[float, list[Word], str]] = []
    row = next((r for r in rows if any(id(w) in ids for w in r)), None)
    if row is not None:  # left: walk left from the value, a phrase of up to 4 words
        left = [w for w in row if id(w) not in ids and w.box.x1 <= vbox.x0 + 0.002]
        picked: list[Word] = []
        rate = False
        for w in reversed(left):
            gap_to = picked[0].box.x0 if picked else vbox.x0
            if picked and gap_to - w.box.x1 > 2.5 * max(w.box.height, 0.004):
                break
            token = w.text.strip()
            if not picked and re.fullmatch(r"[$€£:#.\-–]+", token):
                continue  # a currency sign or colon between label and value
            if not picked and (_RATE.fullmatch(token) or (rate and re.fullmatch(r"\(?\d+(?:[.,]\d+)?", token))):
                rate = rate or "%" in token
                continue  # the tax rate between label and value: "GST 5%", "TPS (5 %)", "HST @ 13%"
            if not picked and not any(c.isalpha() for c in w.text):
                break  # another value (a column of numbers), not a label
            if picked and not re.search(r"[A-Za-z#]", w.text):
                break
            picked.insert(0, w)
            if len(picked) == 4:
                break
        if picked and _has_letters(_join(picked)) and vbox.x0 - picked[-1].box.x1 < 0.5:
            dist = vbox.x0 - picked[-1].box.x1
            # A label on the value's own row is its label, whatever is printed in the row above (another
            # field's label and value in a key-value header); labels above are for grids and boxes.
            score = 1.3 if _looks_like_label(_join(picked)) else 0.3
            candidates.append((score - dist, picked, "left"))
    # Above: the nearest row above with words over the value; a label is printed right over its value,
    # so a row further up (a title, a column heading over the line items) is not taken instead.
    above = [
        (r, [w for w in r if w.box.x1 >= vbox.x0 - 0.02 and w.box.x0 <= vbox.x1 + 0.02 and id(w) not in ids])
        for r in rows
        if _span_box(r).y1 <= vbox.y0 + 0.002 and vbox.y0 - _span_box(r).y1 <= ABOVE_MAX
    ]
    above = [(r, over) for r, over in above if over]
    for r, over in sorted(above, key=lambda a: -_span_box(a[0]).y1)[:1]:
        rb = _span_box(r)
        over = [w for w in over if _has_letters(w.text) and not any(c.isdigit() for c in w.text)]  # not a value
        if not over:
            continue
        i0, i1 = r.index(over[0]), r.index(over[-1])
        while i0 > 0 and over[0].box.x0 - r[i0 - 1].box.x1 < max(r[i0].box.height, 0.004) and len(over) < 4:
            i0 -= 1
            over.insert(0, r[i0])
        phrase = r[i0 : i1 + 1][:4]
        dist = vbox.y0 - rb.y1
        candidates.append(((0.9 if _looks_like_label(_join(phrase)) else 0.2) - 2 * dist, phrase, "above"))
    if not candidates:
        return None
    _, words, where = max(candidates, key=lambda c: c[0])
    return words, where


def _make_anchor(rows: list[list[Word]], value: list[Word]) -> Anchor | None:
    found = _find_anchor_words(rows, value)
    if not found:
        return None
    words, where = found
    abox, vbox = _span_box(words), _span_box(value)
    return Anchor(_join(words), _box4(abox), [vbox.x0 - abox.x0, vbox.y0 - abox.y0], where)


def _find_spans(layout: DocLayout, name: str, value: Any) -> list[tuple[int, list[Word], str]]:
    """Where the confirmed ``value`` is printed: (page number, words, date order), smallest spans only."""
    found: list[tuple[int, list[Word], str]] = []
    for page in layout.pages:
        for row in _rows(page.words):
            for i in range(len(row)):
                for j in range(i, min(i + 8, len(row))):
                    order = _matches(name, _join(row[i : j + 1]), value)
                    if order is not None:
                        found.append((page.number, row[i : j + 1], order))

    # A name or id printed exactly as confirmed wins over looser matches ("Fournitures Laval S.E.N.C.",
    # not "Fournitures Laval", which also matches once legal suffixes are ignored).
    if name not in AMOUNT_FIELDS and name not in DATE_FIELDS:
        want = " ".join(str(value).split()).casefold()
        exact = [f for f in found if _join(f[1]).casefold() == want]
        if exact:
            return exact

    # Drop spans that contain a smaller match ("$ 1,234.56" when "1,234.56" matches alone).
    def inside(a: list[Word], b: list[Word]) -> bool:
        return len(a) < len(b) and {id(w) for w in a} <= {id(w) for w in b}

    return [f for f in found if not any(inside(g[1], f[1]) for g in found if g is not f)]


def _box_distance(a: list[float], b: list[float]) -> float:
    return math.hypot((a[0] + a[2]) / 2 - (b[0] + b[2]) / 2, (a[1] + a[3]) / 2 - (b[1] + b[3]) / 2)


# --- Learning ----------------------------------------------------------------------------------------------------


def learn(
    template: Template | dict[str, Any] | None, layout: DocLayout, confirmed: dict[str, tuple[Any, list[Box] | None]],
    when: str | None = None,
) -> Template:  # fmt: skip
    """Update ``template`` (a ``Template`` or its dict, as the store keeps it) from the values AP
    confirmed: ``{field: (value, boxes or None)}``.

    ``boxes`` are the words the reviewer clicked (teach-by-click) and are authoritative; without them the
    value is looked for on the page. A value that cannot be found on the page teaches nothing."""
    if isinstance(template, dict):
        template = Template.from_dict(template)
    template = template or Template()
    when = when or _now()
    learned = 0
    for name, item in confirmed.items():
        value, boxes = item if isinstance(item, tuple) else (item, None)
        if value is None or value == "" or name not in FIELDS:
            continue
        variants = template.fields.setdefault(name, [])
        hit = _taught(layout, boxes, name, value) if boxes else _best_span(layout, name, value, variants)
        if hit is None:
            continue
        page_no, words, raw, order = hit
        page = _page(layout, page_no)
        rows = _rows(page.words) if page is not None else []
        vbox = union_all(boxes) if boxes else _span_box(words)  # a taught box is authoritative
        if vbox is None:
            continue
        anchor = _make_anchor(rows, words) if words else None
        _update_variants(variants, _page_ref(layout, page_no), _box4(vbox), anchor, raw, order, when)
        learned += 1
    if learned:
        template.invoices += 1
        template.updated_at = when
    return template


def _taught(
    layout: DocLayout, boxes: list[Box], name: str, value: Any
) -> tuple[int, list[Word], str, str | None] | None:
    """The words inside the boxes the reviewer clicked (or just the boxes when no word is there)."""
    box = union_all(boxes)
    if box is None:
        return None
    page = _page(layout, box.page)
    pad = 0.002
    words = [
        w for w in (page.words if page is not None else [])
        if box.x0 - pad <= w.box.cx <= box.x1 + pad and box.y0 - pad <= w.box.cy <= box.y1 + pad
    ]  # fmt: skip
    words = [w for row in _rows(words) for w in row]
    raw = _join(words) if words else str(value)
    orders = [o for iso, o in date_candidates(raw) if iso == compare_key(name, value)] if name in DATE_FIELDS else []
    return box.page, words, raw, (orders[0] if len(orders) == 1 and orders[0] in ("dmy", "mdy") else None)


def _best_span(
    layout: DocLayout, name: str, value: Any, variants: list[Variant]
) -> tuple[int, list[Word], str, str | None] | None:
    spans = _find_spans(layout, name, value)
    if not spans:
        return None

    def rank(item: tuple[int, tuple[int, list[Word], str]]) -> tuple[float, float]:
        pos, (page_no, words, _) = item
        box = _box4(_span_box(words))
        ref = _page_ref(layout, page_no)
        near = max((1.0 - min(_box_distance(box, v.box) / 0.1, 1.0) for v in variants if v.page == ref), default=0.0)
        page = _page(layout, page_no)
        found = _find_anchor_words(_rows(page.words), words) if page is not None else None
        label = 1.0 if found and _looks_like_label(_join(found[0])) else 0.5 if found else 0.0
        # Totals are at the bottom: of equal amounts, the last one; for the rest, the first one.
        order = pos / len(spans) if name in AMOUNT_FIELDS else -pos / len(spans)
        return (2 * near + label, order)

    _, (page_no, words, order) = max(enumerate(spans), key=rank)
    return page_no, words, _join(words), (order if order in ("dmy", "mdy") else None)


def _update_variants(
    variants: list[Variant], page: int | str, box: list[float], anchor: Anchor | None, raw: str,
    order: str | None, when: str,
) -> None:  # fmt: skip
    def same(v: Variant) -> bool:
        if v.page != page:
            return False
        if anchor and v.anchor and v.anchor.key == anchor.key:
            return (
                abs(v.anchor.offset[0] - anchor.offset[0]) < 0.06 and abs(v.anchor.offset[1] - anchor.offset[1]) < 0.03
            )
        return _box_distance(v.box, box) < 0.04

    match = next((v for v in variants if same(v)), None)
    if match is None:
        if len(variants) >= MAX_VARIANTS:  # forget the least used, oldest layout
            variants.remove(min(variants, key=lambda v: (v.count, v.last_seen)))
        match = Variant(page=page, box=box)
        variants.append(match)
    weight = min(match.count, 4)  # recent invoices count more than old ones
    match.box = [(a * weight + b) / (weight + 1) for a, b in zip(match.box, box, strict=True)] if match.count else box
    if anchor is not None:
        if match.anchor is not None and match.anchor.key == anchor.key and match.count:
            anchor.offset = [
                (a * weight + b) / (weight + 1) for a, b in zip(match.anchor.offset, anchor.offset, strict=True)
            ]
        match.anchor = anchor
    match.shape.add(raw)
    if order in ("dmy", "mdy"):
        match.date_order = order
    match.count += 1
    match.last_seen = when
    variants.sort(key=lambda v: (-v.count, v.last_seen))


# --- Applying -----------------------------------------------------------------------------------------------------


def _glued(a: Word, b: Word) -> bool:
    """``b`` follows ``a`` as the next word of one phrase: a word, not a value ("Sub" before "Total:",
    "Taxes" after "Total"; not "INV-12" after "Invoice")."""
    return (
        _has_letters(a.text)
        and _has_letters(b.text)
        and not any(c.isdigit() for c in a.text + b.text)
        and b.box.x0 - a.box.x1 < max(a.box.height, 0.004)
    )


def _anchor_hits(rows: list[list[Word]], anchor: Anchor) -> list[tuple[Box, list[Word], str, float, bool]]:
    """Where the anchor's label is printed: (box, its words, text glued after it, similarity, whether
    it is the whole label: "Total:" alone, not the end of "Sub Total:" or the start of "Total Taxes")."""
    key = anchor.key
    hits: list[tuple[Box, list[Word], str, float, bool]] = []
    if not key:
        return hits
    for row in rows:
        for i in range(len(row)):
            acc = ""
            starts = i == 0 or not _glued(row[i - 1], row[i])
            for j in range(i, min(i + 6, len(row))):
                before = acc
                acc += _compact(row[j].text)
                words = row[i : j + 1]
                whole = starts and (j + 1 == len(row) or not _glued(row[j], row[j + 1]))
                if acc == key:
                    hits.append((_span_box(words), words, "", 1.0, whole))
                    break
                if acc.startswith(key) and len(before) < len(key):  # OCR glue: "InvoiceNo:INV-1"
                    hits.append((_span_box(words), words, _after(row[j].text, len(key) - len(before)), 1.0, starts))
                    break
                if len(acc) >= len(key) + 3:
                    break
                if len(key) >= 5 and abs(len(acc) - len(key)) <= 2:
                    sim = SequenceMatcher(None, acc, key).ratio()
                    if sim >= 0.85:
                        hits.append((_span_box(words), words, "", sim, whole))
    return hits


def _after(text: str, n_compact: int) -> str:
    """What follows the first ``n_compact`` letters and digits of ``text`` (the value glued to a label)."""
    seen = 0
    for i, c in enumerate(text):
        if _compact(c):
            seen += len(_compact(c))
            if seen >= n_compact:
                return text[i + 1 :].lstrip(" :#")
    return ""


def _region(box: list[float], h_pad: float, v_pad: float) -> list[float]:
    return [box[0] - h_pad, box[1] - v_pad, box[2] + h_pad, box[3] + v_pad]


def _overlap(a: Box, r: list[float]) -> bool:
    return a.x1 >= r[0] and a.x0 <= r[2] and a.cy >= r[1] and a.cy <= r[3]


def _geometry(box: Box, predicted: list[float]) -> float:
    """1 when the words sit where the value is expected, falling to 0 a tenth of a page away."""
    right_edge = abs(box.x1 - predicted[2])  # amounts are often right-aligned: either edge may hold
    left_edge = abs(box.x0 - predicted[0])
    dy = abs(box.cy - (predicted[1] + predicted[3]) / 2)
    return max(0.0, 1.0 - (min(left_edge, right_edge) + 2 * dy) / 0.1)


def _candidates(
    name: str,
    variant: Variant,
    rows: list[list[Word]],
    predicted: list[float],
    exclude: set[int],
    v_pad: float = 0.008,
) -> list[tuple[list[Word], str, float, float]]:
    """Word spans in the predicted region: (words, raw text, shape score, geometry score)."""
    w, h = predicted[2] - predicted[0], predicted[3] - predicted[1]
    region = _region(predicted, max(w * 0.6, 0.04), max(h * 0.8, v_pad))
    out = []
    for row in rows:
        inside = [x for x in row if id(x) not in exclude and _overlap(x.box, region)]
        for i in range(len(inside)):
            for j in range(i, min(i + 6, len(inside))):
                span = inside[i : j + 1]
                if j > i and span[-1].box.x0 - span[-2].box.x1 > 2.5 * max(span[-1].box.height, 0.004):
                    break  # a column gap: not one value
                raw = _join(span)
                shape = variant.shape.score(name, raw)
                if shape > 0:
                    out.append((span, raw, shape, _geometry(_span_box(span), predicted)))
    return out


def apply_template(template: Template | dict[str, Any] | None, layout: DocLayout) -> dict[str, list[Reading]]:
    """Read every learned field on a new invoice: ``{field: [Reading, ...]}``, best first, ``method="template"``.

    The label (anchor) is looked for first and the value read at the learned offset from it; when the
    label is not printed the value is read at the learned position, with a lower score."""
    if isinstance(template, dict):
        template = Template.from_dict(template)
    out: dict[str, list[Reading]] = {}
    if template is None or not layout.pages:
        return out
    from .reader import credit_printed_positive

    credit = credit_printed_positive(layout)  # a credit memo printed without signs: its amounts are credits
    for name, variants in template.fields.items():
        best: dict[Any, Reading] = {}
        for variant in variants:
            for reading in _apply_variant(name, variant, layout):
                if credit and name in AMOUNT_FIELDS and isinstance(reading.value, float) and reading.value > 0:
                    reading.value = -reading.value
                key = compare_key(name, reading.value)
                if key not in best or reading.score > best[key].score:
                    best[key] = reading
        if best:
            ranked = sorted(best.values(), key=lambda r: -r.score)
            # A rival is offered only when nearly as good: the row below the learned spot is not a
            # second opinion, and would only cast doubt on the reading where the value always is.
            out[name] = [r for r in ranked if r.score >= ranked[0].score - RIVAL_MARGIN]
    return out


_CREDIT_MARK = re.compile(r"^(cr|cr\.|dr|-)$", re.I)


def _credit_mark(rows: list[list[Word]], words: list[Word]) -> Word | None:
    """The "CR" (credit) printed right after an amount on its row, if any."""
    if not words or _CREDIT_MARK.match(words[-1].text):
        return None
    last = words[-1]
    for row in rows:
        if last in row:
            i = row.index(last)
            if i + 1 < len(row):
                nxt = row[i + 1]
                if _CREDIT_MARK.match(nxt.text) and nxt.box.x0 - last.box.x1 < 3 * max(last.box.height, 0.005):
                    return nxt
            return None
    return None


def _apply_variant(name: str, variant: Variant, layout: DocLayout) -> list[Reading]:
    page = _page(layout, variant.page)
    if page is None:
        return []
    rows = _rows(page.words)
    experience = 1.0 if variant.count >= 2 else 0.9
    found: list[tuple[Reading, set[int], tuple[float, float, int, float]]] = []

    def add(words: list[Word], raw: str, shape: float, geometry: float, base: float) -> None:
        if name == "payment_terms" and not _has_letters(raw):
            return  # terms are words ("Net 30", "30 days"), never a bare number
        if name in AMOUNT_FIELDS:
            from .normalize import looks_like_money

            if sum(c.isdigit() for c in raw) >= 9 and not looks_like_money(raw):
                return  # a registration or account number ("GST Reg. No. 123456782"), not an amount
            mark = _credit_mark(rows, words)
            if mark is not None:  # "$1,370.34 CR": the sign is printed beside the amount
                words, raw = [*words, mark], f"{raw} {mark.text}"
        value = normalize_value(name, raw, variant.date_order)
        if value is None or value == "":
            return
        conf = min((w.conf for w in words), default=1.0)
        score = (base * (0.6 + 0.4 * geometry) + SHAPE_WEIGHT * shape) * experience * conf - 0.005 * (len(words) - 1)
        reading = Reading(name, value, raw, [w.box for w in words], round(min(score, 0.99), 4), "template")
        # Of nested spans that fit alike, the longer is the value: "2 074,06", not its tail "074,06".
        rank = (variant.shape.fit(raw), shape, len(words), reading.score)
        found.append((reading, {id(w) for w in words}, rank))

    hits = _anchor_hits(rows, variant.anchor) if variant.anchor else []
    if hits:
        learned = variant.anchor.box
        abox, awords, glued, sim, _whole = min(
            hits, key=lambda h: (-h[3], not h[4], _box_distance(_box4(h[0]), learned))
        )
        # The label found where it always is: each confirmation at this label and offset adds trust.
        base = ANCHOR_SCORE * sim + EXPERIENCE_BONUS * min(max(variant.count - 1, 0), EXPERIENCE_FULL) / EXPERIENCE_FULL
        if glued:
            shape = variant.shape.score(name, glued)
            if shape > 0:
                add(awords[-1:], glued, shape, 1.0, base)
        dx, dy = variant.anchor.offset
        w, h = variant.box[2] - variant.box[0], variant.box[3] - variant.box[1]
        predicted = [abox.x0 + dx, abox.y0 + dy, abox.x0 + dx + w, abox.y0 + dy + h]
        exclude = {id(x) for x in awords}
    elif variant.anchor and name not in REQUIRED_FIELDS:
        return []  # the label of a field not every invoice has is missing: so, most likely, is the field
    else:
        base, predicted, exclude = POSITION_SCORE, variant.box, set()
    v_pad = 0.008 if hits else 0.025  # without the label the page may have moved a little more
    for words, raw, shape, geometry in _candidates(name, variant, rows, predicted, exclude, v_pad):
        add(words, raw, shape, geometry, base)

    # Of spans inside one another ("NET", "60" and "NET 60"), only the one shaped most like the values
    # confirmed so far is a reading: the parts of a value are not rival values.
    def nested(a: set[int], b: set[int]) -> bool:
        return a != b and (a <= b or b <= a)

    def beaten(ids: set[int], rank: tuple[float, float, int, float]) -> bool:
        return any(nested(ids, o_ids) and o_rank > rank for _, o_ids, o_rank in found)

    return [r for r, ids, rank in found if not beaten(ids, rank)]


# --- Outcomes, statistics and policy ----------------------------------------------------------------------------


def header_values(doc: dict[str, Any] | CaptureResult | None) -> dict[str, Any]:
    """{field: value} for the header ``FIELDS`` of a coding (tax amounts summed by type from its
    ``tax_lines``) or of a capture result (``CaptureResult`` or its dict)."""
    if doc is None:
        return {}
    if isinstance(doc, CaptureResult):
        doc = doc.to_dict()
    if isinstance(doc.get("fields"), dict):  # a capture result
        return {k: (v.get("value") if isinstance(v, dict) else v) for k, v in doc["fields"].items()}
    taxes: dict[str, float] = {}
    for t in doc.get("tax_lines") or []:
        key = f"{str(t.get('tax_type') or '').lower()}_amount"
        if key in FIELDS and t.get("tax_amount") is not None:
            taxes[key] = round(taxes.get(key, 0.0) + float(t["tax_amount"]), 2)
    return {name: doc[name] if name in doc else taxes.get(name) for name in FIELDS}


def confirmed_values(final: dict[str, Any], taught: dict[str, list[Box]] | None = None) -> dict[str, tuple]:
    """``learn``'s ``confirmed`` from an approved coding: {field: (value, the boxes taught by click or None)}."""
    return {
        name: (value, (taught or {}).get(name))
        for name, value in header_values(final).items()
        if value is not None and value != ""
    }


def outcome_rows(proposed: Any, final: Any, fields: Iterable[str] = FIELDS) -> list[dict]:
    """One row per header field AP saw: what AP Coder proposed (a coding or a capture result), what was
    approved, whether they agree. A field blank on both sides was not printed and is not counted."""
    proposed, final = header_values(proposed), header_values(final)
    rows = []
    for name in fields:
        a, b = proposed.get(name), final.get(name)
        ka, kb = compare_key(name, a), compare_key(name, b)
        if ka is None and kb is None:
            continue
        rows.append({"field": name, "ai_value": a, "final_value": b, "correct": ka == kb})
    return rows


def wilson_lower(correct: int, n: int, z: float = 1.96) -> float:
    """Lower bound of the Wilson score interval: the accuracy we can claim with confidence, not luck."""
    if n <= 0:
        return 0.0
    p = correct / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    margin = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, (centre - margin) / denom)


def clean_fields_needed(correct: int, n: int, target: float, z: float = 1.96, limit: int = 1_000_000) -> int | None:
    """How many more correct fields in a row would lift the lower bound to ``target`` (None: out of reach)."""
    if wilson_lower(correct, n, z) >= target:
        return 0
    if wilson_lower(correct + limit, n + limit, z) < target:
        return None
    lo, hi = 0, limit
    while lo < hi:
        mid = (lo + hi) // 2
        if wilson_lower(correct + mid, n + mid, z) >= target:
            hi = mid
        else:
            lo = mid + 1
    return lo


@dataclass
class FieldStat:
    n: int = 0
    correct: int = 0

    @property
    def accuracy(self) -> float | None:
        return self.correct / self.n if self.n else None


@dataclass
class SupplierStats:
    key: str = ""
    invoices: int = 0  # reviewed (or audited) invoices, all time
    window_invoices: int = 0  # of which in the window
    fields: dict[str, FieldStat] = field(default_factory=dict)  # header fields, over the window
    n: int = 0
    correct: int = 0
    touchless_rate: float = 0.0  # share of invoices in the window with no correction
    clean_streak: int = 0  # most recent invoices in a row with no correction
    last_correction_at: str | None = None
    history: list[dict[str, Any]] = field(default_factory=list)  # per invoice, newest first

    @property
    def accuracy(self) -> float | None:
        return self.correct / self.n if self.n else None

    def lower_bound(self, z: float = 1.96) -> float:
        return wilson_lower(self.correct, self.n, z)

    def corrections_since(self, iso: str | None) -> int:
        """Invoices with a correction recorded at or after ``iso``."""
        if not iso:
            return 0
        return sum(1 for h in self.history if h["corrections"] and h["at"] >= iso)

    @classmethod
    def from_rows(
        cls, rows: Iterable[dict[str, Any]], key: str = "", window: int = 200, total_invoices: int | None = None
    ) -> SupplierStats:
        """From outcome rows (``invoice_id``, ``field``, ``correct``, ``at``, optional ``source``)."""
        by_invoice: dict[Any, dict[str, Any]] = {}
        for r in rows:
            inv = by_invoice.setdefault(
                r["invoice_id"], {"invoice_id": r["invoice_id"], "at": "", "fields": 0, "corrections": 0,
                                  "sources": set(), "rows": []},
            )  # fmt: skip
            inv["at"] = max(inv["at"], str(r.get("at") or ""))
            inv["fields"] += 1
            inv["corrections"] += 0 if r["correct"] else 1
            inv["sources"].add(r.get("source") or "review")
            inv["rows"].append(r)
        history = sorted(by_invoice.values(), key=lambda h: (h["at"], str(h["invoice_id"])), reverse=True)
        recent = history[:window]
        stats = cls(key=key, invoices=total_invoices if total_invoices is not None else len(history))
        stats.window_invoices = len(recent)
        for inv in recent:
            for r in inv["rows"]:
                fs = stats.fields.setdefault(r["field"], FieldStat())
                fs.n += 1
                fs.correct += 1 if r["correct"] else 0
        stats.n = sum(f.n for f in stats.fields.values())
        stats.correct = sum(f.correct for f in stats.fields.values())
        stats.touchless_rate = sum(1 for h in recent if not h["corrections"]) / len(recent) if recent else 0.0
        for h in history:
            if h["corrections"]:
                break
            stats.clean_streak += 1
        stats.last_correction_at = next((h["at"] for h in history if h["corrections"]), None)
        stats.history = [
            {k: (sorted(v) if k == "sources" else v) for k, v in h.items() if k != "rows"} for h in history
        ]
        return stats


@dataclass
class AutonomyPolicy:
    min_invoices: int = 20
    min_lower_bound: float = 0.99  # Wilson lower bound of header-field accuracy...
    window: int = 200  # ...over the last this many reviewed invoices
    clean_streak: int = 10  # the last this many invoices without a single correction
    audit_rate: float = 0.05  # share of autonomous invoices still checked by a person
    z: float = 1.96

    def to_dict(self) -> dict[str, Any]:
        return {"min_invoices": self.min_invoices, "min_lower_bound": self.min_lower_bound, "window": self.window,
                "clean_streak": self.clean_streak, "audit_rate": self.audit_rate, "z": self.z}  # fmt: skip

    @classmethod
    def from_dict(cls, d: dict[str, Any] | None) -> AutonomyPolicy:
        base = cls()
        d = d or {}
        return cls(
            min_invoices=int(d.get("min_invoices", base.min_invoices)),
            min_lower_bound=float(d.get("min_lower_bound", base.min_lower_bound)),
            window=int(d.get("window", base.window)), clean_streak=int(d.get("clean_streak", base.clean_streak)),
            audit_rate=float(d.get("audit_rate", base.audit_rate)), z=float(d.get("z", base.z)),
        )  # fmt: skip

    def describe(self) -> str:
        return (
            f"at least {self.min_invoices} invoices reviewed, header-field accuracy of at least "
            f"{self.min_lower_bound:.0%} at 95% confidence over the last {self.window}, and the last "
            f"{self.clean_streak} without a single correction; {self.audit_rate:.0%} of touchless invoices "
            "are still audited, and one correction suspends it"
        )

    def plain_rules(self) -> list[tuple[str, str]]:
        """The bar in plain English, one (what, the standard) per rule, for the Automation settings."""
        return [
            ("Invoices reviewed by a person", f"at least {self.min_invoices}"),
            ("Header fields read right",
             f"at least {self.min_lower_bound:.0%}, with 95% confidence, over its last {self.window} invoices"),
            ("Clean invoices in a row", f"the last {self.clean_streak}, with no correction at all"),
            ("Audit sample", f"{self.audit_rate:.0%} of touchless invoices still go to a person"),
            ("A correction", "suspends the vendor at once; it needs a fresh clean streak to go touchless again"),
        ]  # fmt: skip


DEFAULT_POLICY = AutonomyPolicy()


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def policy_gaps(stats: SupplierStats, policy: AutonomyPolicy = DEFAULT_POLICY) -> tuple[list[str], float]:
    """What is still missing to meet the policy (plain English, empty when met) and the progress 0..1."""
    gaps: list[str] = []
    p_inv = min(1.0, stats.invoices / policy.min_invoices) if policy.min_invoices > 0 else 1.0
    if stats.invoices < policy.min_invoices:
        gaps.append(f"{stats.invoices} of {policy.min_invoices} invoices")
    p_streak = min(1.0, stats.clean_streak / policy.clean_streak) if policy.clean_streak > 0 else 1.0
    if stats.clean_streak < policy.clean_streak:
        gaps.append(f"{policy.clean_streak - stats.clean_streak} more clean in a row needed")
    need = clean_fields_needed(stats.correct, stats.n, policy.min_lower_bound, policy.z)
    if need == 0:
        p_bound = 1.0
    elif need is None or not stats.n:
        p_bound = 0.0
        if stats.n:
            gaps.append(f"accuracy {stats.lower_bound(policy.z):.1%} is below {policy.min_lower_bound:.0%}")
    else:
        p_bound = stats.n / (stats.n + need)
        per_invoice = stats.n / stats.window_invoices if stats.window_invoices else 10
        more = math.ceil(need / max(per_invoice, 1))
        if not gaps or more > max(policy.min_invoices - stats.invoices, policy.clean_streak - stats.clean_streak):
            bound = f"{policy.min_lower_bound:.0%}"
            gaps.append(f"about {_plural(more, 'more clean invoice')} for a {bound} accuracy bound")
    progress = 1.0 if not gaps else min(0.99, (p_inv + p_streak + p_bound) / 3)
    return gaps, progress


def meets_policy(stats: SupplierStats, policy: AutonomyPolicy = DEFAULT_POLICY) -> bool:
    return not policy_gaps(stats, policy)[0]


def fresh_streak(stats: SupplierStats, since: str | None) -> int:
    """Clean invoices in a row reviewed after ``since`` (when the supplier was suspended): the streak a suspended
    supplier must build again. Without ``since`` (suspended by a correction), its clean streak."""
    if not since:
        return stats.clean_streak
    streak = 0
    for h in stats.history:  # newest first
        if h["corrections"] or str(h["at"]) <= since:
            break
        streak += 1
    return streak


def judged_stats(stats: SupplierStats, stored_state: str, suspended_at: str | None = None) -> SupplierStats:
    """The record the bar is checked on: a suspended supplier's clean streak counts only invoices reviewed since
    it was suspended (a reopened touchless invoice suspends it without recording a correction), also while a
    manager keeps it supervised and once allowed again (``suspended_at`` is kept until it is touchless again)."""
    if stored_state != SUSPENDED and not suspended_at:
        return stats
    from dataclasses import replace

    return replace(stats, clean_streak=min(stats.clean_streak, fresh_streak(stats, suspended_at)))


def clean_invoices_to_go(stats: SupplierStats, policy: AutonomyPolicy = DEFAULT_POLICY) -> int | None:
    """About how many more invoices reviewed with no correction would meet the bar (0: met; None: out of reach
    within the window, which only more clean invoices replacing old corrections will change)."""
    need_invoices = max(0, policy.min_invoices - stats.invoices)
    need_streak = max(0, policy.clean_streak - stats.clean_streak)
    need_fields = clean_fields_needed(stats.correct, stats.n, policy.min_lower_bound, policy.z)
    if need_fields is None:
        return None
    per_invoice = stats.n / stats.window_invoices if stats.window_invoices else 10
    need_bound = math.ceil(need_fields / max(per_invoice, 1)) if need_fields else 0
    return max(need_invoices, need_streak, need_bound)


def to_go_text(to_go: int | None) -> str:
    """ "about 6 more clean invoices to go touchless" (plain English for ``clean_invoices_to_go``)."""
    if to_go is None:
        return "too many corrections in its recent invoices to go touchless yet"
    if to_go == 0:
        return "meets the bar to go touchless"
    return f"about {_plural(to_go, 'more clean invoice')} to go touchless"


def autonomy_status(
    stats: SupplierStats, policy: AutonomyPolicy | None = None, stored_state: str = SUPERVISED,
    autonomous_since: str | None = None, *, touchless_on: bool = True, suspended_at: str | None = None,
) -> tuple[str, float, str]:  # fmt: skip
    """(state, progress 0..1, one-line explanation). ``stored_state``: what touchless processing, a manager or a
    found error set (supervised, autonomous, suspended, held). ``touchless_on``: the company-wide switch; while it
    is off a supplier that meets the bar shows as *ready*, and nothing is approved without a person."""
    policy = policy or DEFAULT_POLICY
    since = (autonomous_since or "")[:10]
    if stored_state == HELD:
        gaps, progress = policy_gaps(stats, policy)
        record = "it meets the bar" if not gaps else "; ".join(gaps)
        return HELD, progress, f"Kept supervised by a manager: every invoice is reviewed ({record})."
    if stored_state == AUTONOMOUS:
        if not stats.corrections_since(autonomous_since):
            if not touchless_on:
                return (READY, 1.0, "Meets the bar, but touchless processing is off (Settings → Automation): "
                                    "every invoice is reviewed.")  # fmt: skip
            return (AUTONOMOUS, 1.0, f"Touchless since {since or 'it met the bar'}; {policy.audit_rate:.0%} of its "
                                     "invoices are still audited, and one correction suspends it.")  # fmt: skip
        stored_state = SUSPENDED
    judged = judged_stats(stats, stored_state, suspended_at)
    gaps, progress = policy_gaps(judged, policy)
    if stored_state == SUSPENDED:
        when = (suspended_at or stats.last_correction_at or "")[:10]
        head = "Suspended" + (f" on {when}" if when else "") + ": a person found an error in a touchless invoice"
        if not gaps:
            tail = "goes touchless again on its next invoice" if touchless_on else "touchless processing is off"
            return SUSPENDED, 1.0, f"{head}; it meets the bar again ({tail})."
        return SUSPENDED, progress, f"{head}; {to_go_text(clean_invoices_to_go(judged, policy))} again."
    if stats.invoices < policy.min_invoices:
        return LEARNING, progress, "; ".join(gaps) + "."
    if not gaps:
        when = "on its next invoice" if touchless_on else "once touchless processing is on (Settings → Automation)"
        return (READY, 1.0, f"Meets the bar: {stats.invoices} invoices, accuracy at least "
                            f"{stats.lower_bound(policy.z):.1%}, last {policy.clean_streak} clean. Goes touchless "
                            f"{when}.")  # fmt: skip
    return SUPERVISED, progress, "; ".join(gaps) + "."


def automatic_transition(
    stats: SupplierStats, policy: AutonomyPolicy | None = None, stored_state: str = SUPERVISED,
    autonomous_since: str | None = None, suspended_at: str | None = None,
) -> tuple[str, str] | None:  # fmt: skip
    """With touchless processing on: the stored state this supplier moves to now, and why (None: it stays).

    * a supplier that meets the bar goes touchless (``autonomous``) by itself, the same bar for every supplier;
    * a suspended one goes touchless again once it meets the bar with a fresh clean streak since its suspension;
    * a touchless one with a correction since is suspended (``Store.record_outcomes`` does it at once);
    * a touchless one that does not meet the standard bar (turned on by hand under another bar, before the bar was
      the same for everyone) goes back to review;
    * a supplier a manager keeps supervised (``held``) never moves."""
    policy = policy or DEFAULT_POLICY
    if stored_state == HELD:
        return None
    if stored_state == AUTONOMOUS:
        if stats.corrections_since(autonomous_since):
            return SUSPENDED, "a correction was found since it went touchless"
        if not meets_policy(stats, policy):
            return SUPERVISED, "it does not meet the standard bar for touchless processing"
        return None
    if not meets_policy(judged_stats(stats, stored_state, suspended_at), policy):
        return None
    if stored_state == SUSPENDED:
        return (
            AUTONOMOUS,
            f"meets the bar again, with {policy.clean_streak} clean invoices in a row since it was suspended",
        )
    return AUTONOMOUS, "meets the bar for touchless processing"


HEADER_PROVEN_SOFT = {"LINES_ADD_UP"}

# Findings that always send an invoice to a person, whatever its supplier's record: fraud and duplicate signals.
ALWAYS_A_PERSON: dict[str, str] = {
    "VENDOR_BANK_CHANGED": "the bank account to pay into changed",
    "VENDOR_TAX_NUMBER_CHANGED": "the GST/HST number changed",
    "DUPLICATE_INVOICE": "it may be a duplicate",
    "DUPLICATE_IN_ERP": "it may be a duplicate",
    "DUPLICATE_OTHER_VENDOR": "it may be a duplicate",
    "POSSIBLE_DUPLICATE_AMOUNT": "it may be a duplicate",
    "AMOUNT_UNUSUAL": "the amount is unusual for this vendor",
    "VENDOR_NOT_IN_MASTER": "the vendor is not in the vendor master",
    "VENDOR_ON_HOLD": "the vendor is on hold",
}
# What always goes to a person, in plain English (Settings → Automation).
ALWAYS_A_PERSON_RULES: list[tuple[str, str]] = [
    ("A vendor's first invoices", "until the vendor meets the bar above"),
    ("Bank account changed", "the account to pay into differs from the vendor's approved invoices"),
    ("GST/HST number changed", "it differs from the vendor's usual number or the vendor master"),
    ("Possible duplicate", "same number, same amount under a new number, already in the ERP, or another vendor name"),
    ("Unusual amount", "far above what this vendor usually bills"),
    ("Over the touchless limit", "a total above the largest amount approved without a person"),
    ("No exchange rate", "a foreign currency with no rate in Settings → Review, so the limit can't be checked"),
    ("Over the approval limit", "it needs a second approver anyway"),
    ("Credit note", "a credit is always applied by a person"),
    ("Not in the vendor master", "once a vendor master is imported, a vendor that is not in it"),
    ("Not read by every reader", "the page reader has not read it yet, or not every page of it"),
    ("Any failed check", "totals, tax, GST/HST check digit, or any field not verified"),
]  # fmt: skip


def touchless_gates(
    issues: Iterable[dict[str, Any]] | None = None, *, grand_total: Any = None, over_limit: float | None = None,
    over_approval_limit: bool = False, awaiting_page_reader: bool = False, pages_unread: int = 0,
    no_rate_for: str = "",
) -> list[str]:  # fmt: skip
    """Why this invoice must be seen by a person whatever its supplier's record (plain English; empty: none applies).
    ``issues``: the validation findings ({"code", ...}); ``grand_total``: below zero is a credit note;
    ``over_limit``: the touchless limit its total is over (None: not over); ``no_rate_for``: its currency, when no
    exchange rate is set for it (the limit is in CAD, so it can't be checked); ``over_approval_limit``: it needs a
    second approver; ``awaiting_page_reader``: not every reader has read it yet; ``pages_unread``: pages of it the
    page reader did not read (it reads at most AP_PAGE_READER_MAX_PAGES pages)."""
    reasons: list[str] = []
    for issue in issues or []:
        reason = ALWAYS_A_PERSON.get(str(issue.get("code") or ""))
        if reason and reason not in reasons:
            reasons.append(reason)
    total = parse_amount(grand_total) if grand_total is not None else None
    if total is not None and total < 0:
        reasons.append("it is a credit note")
    if over_limit is not None:
        reasons.append(f"the total is over the touchless limit of {over_limit:,.2f}")
    if no_rate_for:
        reasons.append(f"no exchange rate for {no_rate_for} (Settings → Review) to compare its total with the "
                       "touchless limit")  # fmt: skip
    if over_approval_limit:
        reasons.append("it is over the approval limit and needs a second approver")
    if awaiting_page_reader:
        reasons.append("the page reader has not read it yet")
    if pages_unread > 0:
        reasons.append(f"{pages_unread} of its pages {'was' if pages_unread == 1 else 'were'} not read by OvisOCR2")
    return reasons


def should_auto_approve(
    state: str, capture: CaptureResult | dict[str, Any] | None, checks_ok: bool,
    issues: Iterable[dict[str, Any]] | None = None,
) -> tuple[bool, str]:  # fmt: skip
    """Approve without a person only for an ``autonomous`` supplier, every printed header field
    ``verified``, no failed check, no error-level validation issue and none of the findings that always need a
    person (``touchless_gates``). Otherwise the reason it goes to review."""
    if state != AUTONOMOUS:
        return False, f"supplier is {STATE_LABELS.get(state, state).lower()}, not autonomous"
    if capture is None:
        return False, "no capture result"
    if isinstance(capture, dict):
        capture = CaptureResult.from_dict(capture)
    issues = list(issues or [])
    total = capture.fields.get("grand_total")
    gates = touchless_gates(issues, grand_total=total.value if total is not None else None)
    if gates:
        return False, "always a person: " + "; ".join(gates)
    if not checks_ok:
        return False, "a check failed"
    failed = [c.get("code") or "check" for c in capture.checks if not c.get("ok", True)]
    totals_ok = any(c.get("code") == "TOTALS_ADD_UP" and c.get("ok") for c in capture.checks)
    if totals_ok:
        # Line items that do not sum to the subtotal (a line the reader could not separate) do not
        # make the header wrong once subtotal + charges + taxes = total: the voucher is the header.
        failed = [code for code in failed if code not in HEADER_PROVEN_SOFT]
    if failed:
        return False, f"check failed: {', '.join(failed)}"
    errors = [i.get("code") or "error" for i in issues or [] if str(i.get("severity", "")).lower() == "error"]
    if errors:
        return False, f"validation error: {', '.join(errors)}"
    if not capture.fields:
        return False, "no header fields were read"
    for name in REQUIRED_FIELDS:
        fr = capture.fields.get(name)
        if fr is None or fr.status == MISSING:
            return False, f"{name.replace('_', ' ')} not found"
    for name, fr in capture.fields.items():
        if fr.status == MISSING and fr.value in (None, ""):
            continue  # not printed on this invoice
        if fr.status != VERIFIED:
            return False, f"{name.replace('_', ' ')} is {fr.status}, not verified"
    return True, "autonomous supplier: every header field verified and every check passed"


def pick_for_audit(invoice_id: Any, rate: float) -> bool:
    """Whether a person audits this autonomous invoice: a deterministic hash, so it is reproducible."""
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    digest = hashlib.sha256(f"ap-coder-audit:{invoice_id}".encode()).hexdigest()
    return int(digest[:15], 16) / float(16**15) < rate
