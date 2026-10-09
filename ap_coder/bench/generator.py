"""Random invoices with ground truth, for the capture benchmark.

`generate(out_dir, n, seed)` writes `<id>.pdf` (or `<id>.png` for some scans) and `<id>.truth.json`
for each case. The truth holds, for every header field printed on the invoice, its normalized value,
the text as printed and where it is printed (a `Box` as fractions of the page, top-left origin, pages
from 1), plus the line items. Values that a reader may legitimately infer without them being printed
(the currency of a Canadian invoice, the tax total) go under `implied`.

Every case comes from its own `random.Random`, seeded from (seed, index), so a case is the same
whatever `n` is and whichever process makes it.
"""

from __future__ import annotations

import io
import json
import math
import random
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

from ap_coder.bench import content as C
from ap_coder.bench.wording import label, phrase, title, with_sep
from ap_coder.capture.types import Box

GENERATOR_VERSION = 1

ARCHETYPES = (
    "classic_left",  # vendor top-left, key-value header on the right, totals bottom-right
    "vendor_right",  # vendor block top-right, title and header on the left
    "centered_logo",  # logo and vendor centred at the top
    "header_grid",  # header as a table: a row of labels over a row of values
    "boxed_fields",  # each header field in its own box, label above the value
    "totals_left",  # totals block bottom-left
    "table_footer",  # totals as footer rows of the line-item table
    "quebec_fr",  # French, Quebec: TPS and TVQ
    "bilingual",  # "Invoice / Facture" labels
    "us_vendor",  # US supplier: USD, sales tax or none, no GST
    "utility_bill",  # account number, statement date, billing period, amount due
    "credit_note",  # negative amounts, original invoice reference
    "multipage",  # line items spill onto page 2+, totals on the last page
    "remittance_stub",  # detachable stub repeating amount due and invoice number
    "minimal_courier",  # small vendor, typewriter font, few fields, a little crooked
)

FAMILIES = {"helv": ("helv", "hebo"), "tiro": ("tiro", "tibo"), "cour": ("cour", "cobo")}
INK = (0, 0, 0)
GREY = (0.35, 0.35, 0.35)
LIGHT = (0.9, 0.9, 0.9)
ACCENTS = [(0.1, 0.25, 0.5), (0.55, 0.1, 0.1), (0.05, 0.38, 0.3), (0.25, 0.25, 0.25), (0.8, 0.4, 0.0), (0.3, 0.1, 0.45)]
PROVINCE_WEIGHTS = {"ON": 35, "QC": 12, "BC": 14, "AB": 12, "NS": 9, "SK": 9, "MB": 9}

PtBox = tuple  # (page, x0, y0, x1, y1) in points
# Header labels that can take a "#" ("Invoice #", "PO #"); dates and terms never do.
ID_KEYS = {
    "invoice_number",
    "credit_number",
    "bill_number",
    "po_number",
    "customer_account",
    "quote_number",
    "sales_order",
    "original_invoice",
}
_FONTS: dict[str, Any] = {}


class Style:
    """Where a *style* choice (vendor, wording, fonts, layout, date and number formats) draws its
    randomness, so a supplier's invoices can all look alike.

    Unpinned (``key`` None), every choice comes from the invoice's own random stream, in the same order
    as ever: a single invoice is unchanged. Pinned to a supplier, each named choice comes from its own
    generator seeded by (supplier, name), so it is the same on every invoice of that supplier whatever
    else differs; the content (numbers, dates, lines, amounts) still comes from the invoice's stream.
    """

    def __init__(self, rng: random.Random, key: str | None = None, index: int = 0):
        self.rng, self.key, self.index = rng, key, index

    @property
    def pinned(self) -> bool:
        return self.key is not None

    def __call__(self, name: str) -> random.Random:
        if self.key is None:
            return self.rng
        return random.Random(f"ap-bench-style:{self.key}:{name}")


def _bump(number: str, step: int) -> str:
    """The next invoice number of a supplier: its last run of digits counted on by ``step``."""
    runs = list(re.finditer(r"\d+", number))
    if not runs:
        return number
    last = runs[-1]
    width = len(last.group())
    return number[: last.start()] + f"{int(last.group()) + step:0{width}d}" + number[last.end() :]


def _pymupdf():
    import pymupdf

    return pymupdf


def _font(name: str) -> Any:
    if name not in _FONTS:
        _FONTS[name] = _pymupdf().Font(name)
    return _FONTS[name]


def _metrics(font: str) -> tuple[float, float]:
    f = _font(font)
    return f.ascender, f.descender


def _width(s: str, font: str, size: float) -> float:
    # Font.text_length, not pymupdf.get_text_length: the latter gives accented letters no width.
    return _font(font).text_length(s, fontsize=size)


def _union(boxes: list[PtBox]) -> PtBox:
    return (
        boxes[0][0],
        min(b[1] for b in boxes),
        min(b[2] for b in boxes),
        max(b[3] for b in boxes),
        max(b[4] for b in boxes),
    )


# --- drawing ----------------------------------------------------------------------------------


class Canvas:
    """A PyMuPDF document that remembers where every string went."""

    def __init__(self, width: float, height: float, family: str, rng: random.Random, jitter: float = 0.0):
        self.doc = _pymupdf().open()
        self.width, self.height = width, height
        self.regular, self.bold = FAMILIES[family]
        self.rng = rng
        self.jitter = jitter
        self._cur: tuple[int, Any] | None = None
        self.new_page()

    @property
    def pno(self) -> int:
        return self.doc.page_count

    def new_page(self) -> None:
        self._cur = None  # PyMuPDF invalidates page objects when the page tree changes
        self.doc.new_page(width=self.width, height=self.height)

    def page(self, pno: int | None = None) -> Any:
        pno = pno or self.pno
        if self._cur is None or self._cur[0] != pno:
            self._cur = (pno, self.doc[pno - 1])
        return self._cur[1]

    def tw(self, s: str, size: float, bold: bool = False) -> float:
        return _width(s, self.bold if bold else self.regular, size)

    def fit(self, s: str, size: float, maxw: float, bold: bool = False, floor: float = 5.5) -> float:
        while size > floor and self.tw(s, size, bold) > maxw:
            size -= 0.25
        return size

    def clip(self, s: str, size: float, maxw: float, bold: bool = False) -> str:
        """Drop words from the end until `s` fits (a long description cut by the table column)."""
        words = s.split(" ")
        while len(words) > 1 and self.tw(" ".join(words), size, bold) > maxw:
            words.pop()
        return " ".join(words).rstrip(",;-")

    def text(
        self,
        x: float,
        y: float,
        s: str,
        size: float = 9,
        bold: bool = False,
        color=INK,
        align: str = "left",
        page: int | None = None,
    ) -> PtBox:
        """Draw `s` with its baseline at y; return its box (the same box PyMuPDF reports for words)."""
        font = self.bold if bold else self.regular
        w = _width(s, font, size)
        if align == "right":
            x -= w
        elif align == "center":
            x -= w / 2
        if self.jitter:
            x += self.rng.uniform(-self.jitter, self.jitter) * 1.5
            y += self.rng.uniform(-self.jitter, self.jitter)
        pno = page or self.pno
        self.page(pno).insert_text((x, y), s, fontname=font, fontsize=size, color=color)
        asc, desc = _metrics(font)
        return (pno, x, y - asc * size, x + w, y - desc * size)

    def parts(
        self,
        x: float,
        y: float,
        parts: list[str],
        size: float = 9,
        bold: bool | list[bool] = False,
        color=INK,
        align: str = "left",
        gap: float | None = None,
    ) -> list[PtBox]:
        """Draw strings one after another on a line, a space apart; return each one's box."""
        bolds = bold if isinstance(bold, list) else [bold] * len(parts)
        gap = max(3.0, self.tw(" ", size) * 1.2) if gap is None else gap
        widths = [self.tw(p, size, b) for p, b in zip(parts, bolds, strict=True)]
        total = sum(widths) + gap * (len(parts) - 1)
        if align == "right":
            x -= total
        elif align == "center":
            x -= total / 2
        out = []
        for p, b, w in zip(parts, bolds, widths, strict=True):
            out.append(self.text(x, y, p, size, b, color))
            x += w + gap
        return out

    def rect(self, x0, y0, x1, y1, stroke=None, fill=None, width: float = 0.6, page: int | None = None) -> None:
        r = _pymupdf().Rect(x0, y0, x1, y1)
        self.page(page).draw_rect(r, color=stroke, fill=fill, width=width)

    def hline(self, x0, x1, y, color=GREY, width: float = 0.6, dashes: str | None = None) -> None:
        self.page().draw_line((x0, y), (x1, y), color=color, width=width, dashes=dashes)

    def vline(self, x, y0, y1, color=GREY, width: float = 0.6) -> None:
        self.page().draw_line((x, y0), (x, y1), color=color, width=width)

    def logo(self, x: float, y: float, size: float, shape: str, color) -> None:
        pg = self.page()
        if shape == "circle":
            pg.draw_circle((x + size / 2, y + size / 2), size / 2, color=color, fill=color)
        elif shape == "triangle":
            pg.draw_polyline(
                [(x, y + size), (x + size / 2, y), (x + size, y + size)], color=color, fill=color, closePath=True
            )
        elif shape == "diamond":
            pts = [(x + size / 2, y), (x + size, y + size / 2), (x + size / 2, y + size), (x, y + size / 2)]
            pg.draw_polyline(pts, color=color, fill=color, closePath=True)
        else:
            pg.draw_rect(_pymupdf().Rect(x, y, x + size, y + size), color=color, fill=color)
            pg.draw_rect(
                _pymupdf().Rect(x + size * 0.25, y + size * 0.25, x + size * 0.75, y + size * 0.75),
                color=(1, 1, 1),
                fill=(1, 1, 1),
            )

    def tobytes(self) -> bytes:
        self.doc.set_metadata(
            {"producer": "ap_coder.bench", "creator": "ap_coder.bench", "creationDate": "", "modDate": ""}
        )
        return self.doc.tobytes(garbage=3, deflate=True, no_new_id=True)


class Truth:
    def __init__(self) -> None:
        self.fields: dict[str, dict[str, Any]] = {}
        self.lines: list[dict[str, Any]] = []

    def put(self, fld: str, value: Any, raw: str, box: PtBox, lbl: str = "") -> None:
        if fld in self.fields:
            entry = self.fields[fld]
            if entry["value"] != value:
                raise AssertionError(f"{fld}: {entry['value']!r} printed again as {value!r}")
            entry["also"].append({"raw": raw, "box": box})
        else:
            self.fields[fld] = {"value": value, "raw": raw, "label": lbl, "box": box, "also": []}


# --- the invoice's content --------------------------------------------------------------------


@dataclass
class Item:
    """One header entry: a capture field (`fld`) or a distractor (`fld` is None)."""

    key: str
    label: str
    raw: str
    fld: str | None = None
    value: Any = None


@dataclass
class Inv:
    archetype: str
    kind: str  # invoice | credit | utility | minimal
    lang: str  # en | fr | bi
    country: str
    province: str
    vendor: C.Party
    customer: C.Party
    currency: str
    number: str
    inv_date: date
    due: date | None
    po: str | None
    terms: C.Terms | None
    date_style: str
    ms: C.MoneyStyle
    lines: list[C.LineItem]
    freight: Decimal | None  # freight in the totals block (taxable, not in the subtotal)
    taxes: list[C.Tax]
    subtotal: Decimal
    grand: Decimal
    k: dict[str, Any] = field(default_factory=dict)  # layout knobs
    header: list[Item] = field(default_factory=list)  # header entries in print order
    extras: dict[str, Any] = field(default_factory=dict)
    sty: Style | None = None  # where style choices come from (see Style)

    def d(self, x: date) -> str:
        return C.format_date(x, self.date_style)

    def amt(self, x: Decimal, **kw) -> str:
        return self.ms.fmt(x, **kw)

    def lab(self, rng: random.Random, key: str) -> str:
        return label(rng, key, self.lang)

    @property
    def tax_total(self) -> Decimal:
        return sum((t.amount for t in self.taxes), Decimal("0"))


def _knobs(rng: random.Random, arch: str) -> dict[str, Any]:
    k = {
        "vendor_pos": rng.choice(["left", "left", "right", "center"]),
        "logo": rng.choice(["", "", "circle", "square", "triangle", "diamond", "initials"]),
        "header": rng.choice(["kv", "kv", "kv", "grid", "boxed", "stacked"]),
        "totals": rng.choice(["right", "right", "right", "left", "footer"]),
        "table": rng.choice(["band", "lines", "grid", "plain"]),
        "zebra": rng.random() < 0.25,
        "stub": rng.random() < 0.1,
        "multipage": False,
        "family": rng.choices(["helv", "tiro", "cour"], [0.6, 0.3, 0.1])[0],
        "title_number": rng.random() < 0.12,
        "ship_to": rng.random() < 0.4,
        "balance_row": rng.random() < 0.2,
        "sep": rng.choice([":", ":", ":", "", "#"]),
        "a4": rng.random() < 0.2,
        "accent": rng.choice(ACCENTS),
        "size": round(rng.uniform(8.0, 9.6), 2),
        "margin": round(rng.uniform(34, 56), 1),
        "bn_where": rng.choice(["vendor", "vendor", "footer", "footer", "header", "tax"]),
        "terms_where": rng.choice(["header", "header", "footer"]),
        "currency_where": rng.choices(["", "header", "note", "total"], [0.66, 0.13, 0.09, 0.12])[0],
        "show_tax_total": rng.random() < 0.25,
    }
    over: dict[str, dict[str, Any]] = {
        "classic_left": {"vendor_pos": "left", "header": "kv", "totals": "right"},
        "vendor_right": {"vendor_pos": "right", "header": "kv"},
        "centered_logo": {"vendor_pos": "center", "logo": rng.choice(["circle", "square", "triangle", "initials"])},
        "header_grid": {"header": "grid"},
        "boxed_fields": {"header": "boxed"},
        "totals_left": {"totals": "left"},
        "table_footer": {"totals": "footer", "table": rng.choice(["grid", "lines", "band"])},
        "multipage": {"multipage": True},
        "remittance_stub": {"stub": True, "vendor_pos": rng.choice(["left", "right"]), "ship_to": False},
        "minimal_courier": {"family": "cour", "header": "kv", "totals": "right", "stub": False, "logo": ""},
        "utility_bill": {"vendor_pos": "left", "header": "kv", "totals": "left", "stub": rng.random() < 0.7},
    }
    k.update(over.get(arch, {}))
    if k["family"] == "cour":
        k["size"] = min(k["size"], 8.6)
    return k


def _province(rng: random.Random) -> str:
    provs = list(PROVINCE_WEIGHTS)
    return rng.choices(provs, [PROVINCE_WEIGHTS[p] for p in provs])[0]


def _minimal_number(rng: random.Random) -> str:
    return rng.choice([f"{rng.randint(1, 999):04d}", str(rng.randint(1, 400)), f"{rng.randint(1, 99):03d}"])


def make_invoice(rng: random.Random, arch: str, style: Style | None = None) -> Inv:
    """One invoice of layout ``arch``. ``style`` pinned to a supplier keeps everything but the content
    the same from one invoice to the next (see Style); without it every choice comes from ``rng``."""
    S = style or Style(rng)
    k = _knobs(S("knobs"), arch)
    kind = {"credit_note": "credit", "utility_bill": "utility", "minimal_courier": "minimal"}.get(arch, "invoice")
    country = "US" if arch == "us_vendor" else "CA"
    if arch in ("quebec_fr",):
        province, lang = "QC", "fr"
    elif arch == "bilingual":
        province, lang = S("province").choice(["QC", "QC", "ON", "NS"]), "bi"
    elif country == "US":
        province, lang = "", "en"
    else:
        province = _province(S("province"))
        if kind == "minimal" and province == "QC":
            province = "ON"
        lang = "fr" if kind == "credit" and province == "QC" and S("lang").random() < 0.6 else "en"

    vendor_kind = (
        {"US": "us"}.get(country)
        or {"minimal": "small", "utility": "utility"}.get(kind)
        or ("fr" if lang == "fr" else "en")
    )
    vendor = C.make_vendor(S("vendor"), vendor_kind, province or "ON")
    # Tax follows the place of supply: the customer's site is in the province whose tax is charged.
    customer = C.make_customer(S("customer"), province or _province(S("customer_province")))
    currency = "USD" if country == "US" else "CAD"

    if country == "US":
        date_style = S("date_style").choice(C.DATE_STYLES_US)
    elif lang == "fr":
        date_style = S("date_style").choice(C.DATE_STYLES_FR)
    else:
        date_style = S("date_style").choice(C.DATE_STYLES_EN)
    ms = C.money_style(S("money"), lang, currency)
    if kind == "minimal":
        ms.code, ms.group = "", S("money_minimal").choice([",", ""])

    credit = kind == "credit"
    if S.pinned:  # a supplier's invoices: dated in order, numbered in sequence
        period = S("date_gap").choice([7, 10, 14, 14, 30, 30])
        inv_date = C.random_date(S("first_date")) + timedelta(days=S.index * period + rng.randint(0, period // 2))
    else:
        inv_date = C.random_date(rng)
    if kind == "minimal":
        number = _minimal_number(S("number"))
    else:
        number = C.invoice_number(S("number"), credit)
    if S.pinned:
        gap = S("number_gap").choice([1, 1, 3, 8, 25, 120])
        number = _bump(number, S.index * gap + rng.randint(0, gap - 1))
    has_terms = S("has_terms").random() < 0.65
    terms = C.payment_terms(S("terms"), "fr" if lang == "fr" else "en") if has_terms and not credit else None
    days = terms.days if terms and terms.days is not None else S("days").choice([30, 30, 15, 45])
    has_due = kind == "utility" or S("has_due").random() < 0.55
    due = inv_date + timedelta(days=days) if has_due and not credit else None
    if kind == "utility":
        terms = None if S("utility_terms").random() < 0.6 else terms
        due = inv_date + timedelta(days=S("utility_due").choice([14, 18, 21, 30]))
    po = (
        C.po_number(rng, pick=S("po_format"))
        if rng.random() < (0.25 if kind in ("utility", "minimal") else 0.62)
        else None
    )

    if kind == "utility":
        lines = _utility_lines(rng, lang)
    else:
        n = (
            rng.randint(28, 60)
            if k["multipage"]
            else rng.choices(range(1, 13), [8, 12, 14, 13, 11, 9, 7, 6, 5, 4, 3, 2])[0]
        )
        if kind == "minimal":
            n = rng.randint(1, 4)
        if k["stub"]:
            n = min(n, 6)
        lines = C.make_lines(rng, n, "fr" if lang == "fr" else "en", credit)
        if kind == "minimal":
            for li in lines:
                li.sku = ""
        if not credit and kind != "minimal" and rng.random() < 0.12:
            base = sum(li.amount for li in lines)
            pct = rng.choice([2, 5, 10])
            dl = C.money(-base * Decimal(pct) / 100)
            name = label(S("label:discount"), "discount", "fr" if lang == "fr" else "en") + f" {pct}%"
            lines.append(C.LineItem(name, None, "", None, dl, kind="discount"))
        if not credit and kind != "minimal" and rng.random() < 0.1:
            name = label(S("label:freight"), "freight", "fr" if lang == "fr" else "en")
            lines.append(C.LineItem(name, None, "", None, C.money(rng.uniform(15, 140)), kind="freight"))
    subtotal = sum((li.amount for li in lines), Decimal("0"))
    freight = None
    if kind == "invoice" and rng.random() < 0.15:
        freight = C.money(rng.uniform(12, 180))
    taxable = subtotal + (freight or 0)
    if country == "US":
        state = vendor.province
        taxes = []
        if S("us_tax").random() < 0.5:
            rate = Decimal(C.US_SALES_TAX.get(state, "0.07"))
            taxes = [C.Tax("SALES", "tax_total", rate, C.money(taxable * rate))]
    else:
        taxes = C.compute_taxes(province, taxable)
    grand = taxable + sum((t.amount for t in taxes), Decimal("0"))
    inv = Inv(
        arch,
        kind,
        lang,
        country,
        province,
        vendor,
        customer,
        currency,
        number,
        inv_date,
        due,
        po,
        terms,
        date_style,
        ms,
        lines,
        freight,
        taxes,
        subtotal,
        grand,
        k,
        sty=S,
    )
    inv.header = _header_items(rng, inv)
    return inv


def _utility_lines(rng: random.Random, lang: str) -> list[C.LineItem]:
    picks = rng.sample(C.UTILITY_LINES, rng.randint(3, 6))
    out = []
    for en, fr, lo, hi in picks:
        desc = fr if lang == "fr" else en
        if en in ("Energy charge", "Water consumption"):
            unit = "kWh" if en == "Energy charge" else "m3"
            qty = Decimal(rng.randint(120, 9000)) if unit == "kWh" else Decimal(rng.randint(10, 300))
            rate = Decimal(str(round(rng.uniform(0.07, 0.16) if unit == "kWh" else rng.uniform(1.1, 3.4), 4)))
            out.append(C.LineItem(desc, qty, unit, rate, C.money(qty * rate)))
        else:
            out.append(C.LineItem(desc, None, "", None, C.money(rng.uniform(lo, hi))))
    return out


def _account_number(rng: random.Random) -> str:
    return rng.choice(
        [
            f"{rng.randint(10000, 999999)}",
            f"C-{rng.randint(1, 99999):05d}",
            f"{rng.randint(1000, 9999)}-{rng.randint(100, 999)}",
            f"{rng.randint(1000, 9999)} {rng.randint(1000, 9999)} {rng.randint(100, 999)}",
        ]
    )


def _header_items(rng: random.Random, inv: Inv) -> list[Item]:
    S = inv.sty or Style(rng)
    lang = inv.lang
    sep = inv.k["sep"]
    if lang == "fr" and sep == ":" and S("fr_colon").random() < 0.6:
        sep = " :"  # French spacing before the colon

    def lab(key: str, s: str | None = None) -> str:
        if inv.k["header"] != "kv":
            return label(S(f"label:{key}"), key, lang)  # label above the value: no separator
        own = sep
        if sep == "#" and key not in ID_KEYS:
            own = ":" if S(f"sep:{key}").random() < 0.7 else ""
        return with_sep(s or label(S(f"label:{key}"), key, lang), own)

    num_key = {"credit": "credit_number", "utility": "bill_number"}.get(inv.kind, "invoice_number")
    date_key = {"credit": "credit_date", "utility": "statement_date"}.get(inv.kind, "invoice_date")
    first = [
        Item("invoice_number", lab(num_key), inv.number, "invoice_number", inv.number),
        Item("invoice_date", lab(date_key), inv.d(inv.inv_date), "invoice_date", inv.inv_date.isoformat()),
    ]
    if S("date_first").random() < 0.3:
        first.reverse()
    rest: list[Item] = []
    if inv.due:
        rest.append(Item("due_date", lab("due_date"), inv.d(inv.due), "due_date", inv.due.isoformat()))
    if inv.po:
        rest.append(Item("po_number", lab("po_number"), inv.po, "po_number", inv.po))
    if inv.terms and inv.k["terms_where"] == "header":
        rest.append(Item("payment_terms", lab("payment_terms"), inv.terms.raw, "payment_terms", inv.terms.raw))
    if inv.k["currency_where"] == "header":
        rest.append(Item("currency", lab("currency"), inv.currency, "currency", inv.currency))
    if inv.kind == "utility" or S("has:customer_account").random() < 0.6:
        acct = _account_number(S("customer_account"))
        inv.extras["customer_account"] = acct
        rest.insert(0 if inv.kind == "utility" else len(rest), Item("customer_account", lab("customer_account"), acct))
    if inv.kind == "utility":
        start = inv.inv_date - timedelta(days=rng.randint(28, 33) + rng.randint(1, 6))
        end = inv.inv_date - timedelta(days=rng.randint(1, 6))
        rest.append(Item("billing_period", lab("billing_period"), f"{inv.d(start)} - {inv.d(end)}"))
    if S("has:order_date").random() < 0.35 and inv.kind != "utility":
        rest.append(Item("order_date", lab("order_date"), inv.d(inv.inv_date - timedelta(days=rng.randint(1, 25)))))
    if S("has:ship_date").random() < 0.35 and inv.kind != "utility":
        rest.append(Item("ship_date", lab("ship_date"), inv.d(inv.inv_date - timedelta(days=rng.randint(0, 6)))))
    if S("has:quote_number").random() < 0.15 and inv.kind == "invoice":
        rest.append(Item("quote_number", lab("quote_number"), f"Q-{rng.randint(2025, 2026)}-{rng.randint(1, 999):03d}"))
    if S("has:sales_order").random() < 0.25 and inv.kind in ("invoice", "credit"):
        rest.append(Item("sales_order", lab("sales_order"), f"SO-{rng.randint(10000, 99999)}"))
    if S("has:sales_rep").random() < 0.2 and inv.kind == "invoice":
        lbl = lab("sales_rep")  # the label is drawn before the value
        rest.append(Item("sales_rep", lbl, S("sales_rep").choice(["JM", "K. Singh", "MLT", "A. Roy", "House"])))
    if S("has:ship_via").random() < 0.15 and inv.kind == "invoice":
        lbl = lab("ship_via")
        via = S("ship_via").choice(["Purolator", "UPS Ground", "Canpar", "FedEx", "Pickup"])
        rest.append(Item("ship_via", lbl, via))
    if inv.kind == "credit":
        orig = C.invoice_number(rng)
        inv.extras["original_invoice"] = orig
        rest.insert(0, Item("original_invoice", lab("original_invoice"), orig))
        if rng.random() < 0.6:
            reason = (
                rng.choice(["Returned goods", "Pricing error", "Damaged in transit"])
                if lang != "fr"
                else rng.choice(["Marchandise retournée", "Erreur de prix", "Endommagé au transport"])
            )
            rest.append(Item("reason", lab("reason"), reason))
    if inv.k["bn_where"] == "header" and inv.vendor.bn:
        raw = C.format_bn(S("bn_format"), inv.vendor.bn)
        rest.append(Item("bn", lab("bn"), raw, "gst_hst_registration_number", C.reg_value(raw)))
        if inv.vendor.qst:
            raw = C.format_qst(S("qst_format"), inv.vendor.qst)
            rest.append(Item("qst", lab("qst"), raw, "qst_registration_number", C.reg_value(raw)))
    head = rest[:2]
    tail = rest[2:]
    if S.pinned:  # the supplier's own order of header entries
        tail.sort(key=lambda it: S(f"order:{it.key}").random())
    else:
        rng.shuffle(tail)
    return first + head + tail


# --- rendering --------------------------------------------------------------------------------


class Renderer:
    def __init__(self, inv: Inv, rng: random.Random):
        self.inv, self.rng, self.k = inv, rng, inv.k
        self.S = S = inv.sty or Style(rng)  # style choices (the same on every invoice of a supplier)
        w, h = (595.0, 842.0) if self.k["a4"] else (612.0, 792.0)
        self.cv = Canvas(w, h, self.k["family"], rng, jitter=0.7 if inv.kind == "minimal" else 0.0)
        self.t = Truth()
        self.W, self.H = w, h
        self.m = self.k["margin"]
        self.s = self.k["size"]
        self.accent = self.k["accent"]
        self.lh = self.s * 1.42
        self.bn_raw = C.format_bn(S("bn_raw"), inv.vendor.bn) if inv.vendor.bn else ""
        self.qst_raw = C.format_qst(S("qst_raw"), inv.vendor.qst) if inv.vendor.qst else ""
        self.show_bn = bool(inv.vendor.bn) and S("show_bn").random() < 0.95
        self.show_qst = bool(inv.vendor.qst) and S("show_qst").random() < 0.92
        self.page_bottom = self.H - self.m - 14  # leave room for the page number line

    # small helpers
    def lab(self, key: str) -> str:
        return label(self.S(f"label:{key}"), key, self.inv.lang)

    def put(self, fld: str, value: Any, raw: str, box: PtBox, lbl: str = "") -> None:
        self.t.put(fld, value, raw, box, lbl)

    def put_amount(self, fld: str, amount: Decimal, raw: str, box: PtBox, lbl: str = "") -> None:
        self.put(fld, float(amount), raw, box, lbl)

    def reg_line(self, x: float, y: float, which: str, align: str = "left", size: float | None = None) -> PtBox | None:
        """'GST/HST Reg. No.: 123456782 RT0001' (or the QST number); records the number's box."""
        size = size or self.s * 0.92
        if which == "bn":
            if not self.show_bn:
                return None
            lbl, raw, fld = self.lab("bn"), self.bn_raw, "gst_hst_registration_number"
        else:
            if not self.show_qst:
                return None
            lbl, raw, fld = self.lab("qst"), self.qst_raw, "qst_registration_number"
        lbl = with_sep(lbl, self.S(f"reg_sep:{which}").choice([":", "", "#"]))
        boxes = self.cv.parts(x, y, [lbl, raw], size, align=align)
        self.put(fld, C.reg_value(raw), raw, boxes[1], lbl)
        return _union(boxes)

    # page furniture
    def render(self) -> tuple[bytes, dict[str, Any]]:
        if self.inv.kind == "minimal":
            self.render_minimal()
        elif self.inv.kind == "utility":
            self.render_utility()
        else:
            self.render_standard()
        self.page_numbers()
        return self.cv.tobytes(), self.truth()

    def page_numbers(self) -> None:
        n = self.cv.pno
        if n == 1 and self.S("page_numbers").random() < 0.6:
            return
        word = "de" if self.inv.lang == "fr" else "of"
        align = self.S("page_numbers_align").choice(["right", "center"])
        x = self.W - self.m if align == "right" else self.W / 2
        for p in range(1, n + 1):
            self.cv.text(x, self.H - self.m + 4, f"Page {p} {word} {n}", self.s * 0.85, color=GREY, align=align, page=p)

    # --- vendor block ---
    def vendor_block(self, x: float, y: float, align: str) -> float:
        """Returns the y below the block."""
        v, cv, s, S = self.inv.vendor, self.cv, self.s, self.S
        name_size = S("vendor_name_size").uniform(12, 17)
        logo = self.k["logo"]
        top = y
        if logo:
            ls = S("logo_size").uniform(26, 40)
            if logo == "initials":
                initials = "".join(w[0] for w in v.name.split()[:2] if w[0].isalpha()).upper() or "AB"
                lx = {"left": x, "right": x - ls * 1.4, "center": x - ls * 0.7}[align]
                cv.rect(lx, y, lx + ls * 1.4, y + ls, fill=self.accent, stroke=self.accent)
                cv.text(lx + ls * 0.7, y + ls * 0.7, initials, ls * 0.55, bold=True, color=(1, 1, 1), align="center")
                w_logo = ls * 1.4
            else:
                lx = {"left": x, "right": x - ls, "center": x - ls / 2}[align]
                cv.logo(lx, y, ls, logo, self.accent)
                w_logo = ls
            if align == "center":
                y += ls + 6
                name_y = y + name_size * 0.8
                nx = x
            else:
                name_y = y + ls / 2 + name_size * 0.35
                nx = x + w_logo + 8 if align == "left" else x - w_logo - 8
            maxw = (self.W * 0.42 - w_logo - 8) if align != "center" else self.W - 2 * self.m
            name_size = cv.fit(v.name, name_size, maxw, bold=True)
            box = cv.text(nx, name_y, v.name, name_size, bold=True, color=self.accent, align=align)
            y = max(name_y + name_size * 0.35, top + (ls if align != "center" else 0)) + 3
        else:
            name_size = cv.fit(v.name, name_size, self.W * 0.42, bold=True)
            y += name_size
            color = S("vendor_color").choice([INK, self.accent])
            box = cv.text(x, y, v.name, name_size, bold=True, color=color, align=align)
            y += name_size * 0.35 + 3
        self.put("vendor_name", v.name, v.name, box)
        y += s * 1.2
        for line in v.lines:
            cv.text(x, y, line, s, align=align)
            y += self.lh
        contact = []
        if v.phone:
            contact.append(f"{self.lab('phone')}: {v.phone}" if S("phone_label").random() < 0.8 else v.phone)
        if v.fax:
            contact.append(f"{self.lab('fax')}: {v.fax}")
        if contact:
            if S("contact_line").random() < 0.5:
                cv.text(x, y, "   ".join(contact), s, align=align)
                y += self.lh
            else:
                for c in contact:
                    cv.text(x, y, c, s, align=align)
                    y += self.lh
        for extra in (v.email, v.web):
            if extra:
                cv.text(x, y, extra, s, align=align, color=GREY)
                y += self.lh
        r = S("us_tax_id")
        if self.inv.country == "US" and r.random() < 0.5:
            cv.parts(
                x,
                y,
                [
                    label(r, "tax_id_us", "en") + ":",
                    f"{r.randint(10, 99)}-{r.randint(1000000, 9999999)}",
                ],
                s,
                align=align,
            )
            y += self.lh
        if self.k["bn_where"] == "vendor":
            if self.reg_line(x, y, "bn", align):
                y += self.lh
            if self.reg_line(x, y, "qst", align):
                y += self.lh
        return y

    # --- header entries ---
    def header_kv(self, items: list[Item], x0: float, x1: float, y: float) -> float:
        cv = self.cv
        size = self.s
        r = self.S("header_kv")
        bold_labels = r.random() < 0.6
        lw = max(cv.tw(it.label, size, bold_labels) for it in items)
        vw = max(cv.tw(it.raw, size) for it in items)
        while lw + 10 + vw > x1 - x0 and size > 6:
            size -= 0.25
            lw = max(cv.tw(it.label, size, bold_labels) for it in items)
            vw = max(cv.tw(it.raw, size) for it in items)
        right = r.random() < 0.45
        label_right = r.random() < 0.3
        lx = x0
        vx = x1 if right else x0 + lw + 10
        lh = size * r.uniform(1.4, 1.75)
        for it in items:
            y += lh
            if label_right:
                cv.text(x0 + lw, y, it.label, size, bold=bold_labels, align="right")
            else:
                cv.text(lx, y, it.label, size, bold=bold_labels)
            box = cv.text(vx, y, it.raw, size, align="right" if right else "left")
            self.record(it, box)
        return y + size * 0.6

    def record(self, it: Item, box: PtBox) -> None:
        if it.fld:
            self.put(it.fld, it.value, it.raw, box, it.label)

    def header_cells(self, items: list[Item], x0: float, x1: float, y: float, style: str) -> float:
        """grid: label row over value row with cell lines; boxed: one box per field; stacked: no lines."""
        cv = self.cv
        r = self.S("header_cells")
        per_row = {"grid": r.randint(3, 5), "boxed": r.randint(3, 4), "stacked": r.randint(3, 5)}[style]
        gap = 6 if style == "boxed" else 0
        lsize, vsize = self.s * 0.85, self.s * 1.02
        shade = r.random() < 0.6
        for r in range(0, len(items), per_row):
            row = items[r : r + per_row]
            cw = (x1 - x0 - gap * (per_row - 1)) / per_row
            lh_lab, lh_val = lsize * 2.0, vsize * 2.0
            for j, it in enumerate(row):
                cx0 = x0 + j * (cw + gap)
                cx1 = cx0 + cw
                rc = self.S(f"cell:{it.key}")
                if style == "grid":
                    if shade:
                        cv.rect(cx0, y, cx1, y + lh_lab, fill=LIGHT, stroke=GREY)
                    else:
                        cv.rect(cx0, y, cx1, y + lh_lab, stroke=GREY)
                    cv.rect(cx0, y + lh_lab, cx1, y + lh_lab + lh_val, stroke=GREY)
                elif style == "boxed":
                    cv.rect(cx0, y, cx1, y + lh_lab + lh_val, stroke=rc.choice([GREY, self.accent]), width=0.8)
                centered = style == "grid" and rc.random() < 0.5
                tx = (cx0 + cx1) / 2 if centered else cx0 + 4
                al = "center" if centered else "left"
                ls = cv.fit(it.label, lsize, cw - 8, bold=True)
                cv.text(
                    tx, y + lh_lab * 0.68, it.label, ls, bold=True, color=GREY if style != "grid" else INK, align=al
                )
                vs = cv.fit(it.raw, vsize, cw - 8)
                box = cv.text(tx, y + lh_lab + lh_val * 0.62, it.raw, vs, align=al)
                self.record(it, box)
            y += lh_lab + lh_val + (6 if style == "boxed" else 0)
        return y + 4

    def party_block(self, x: float, y: float, key: str, party: C.Party, width: float, customer_gst: bool) -> float:
        cv, s = self.cv, self.s
        cv.text(x, y, with_sep(self.lab(key), self.S(f"party_sep:{key}").choice([":", ""])), s, bold=True,
                color=self.accent)  # fmt: skip
        y += self.lh
        cv.text(x, y, cv.clip(party.name, s, width), s, bold=self.S(f"party_bold:{key}").random() < 0.6)
        y += self.lh
        for line in party.lines:
            cv.text(x, y, cv.clip(line, s, width), s)
            y += self.lh
        if customer_gst and party.bn:
            cv.parts(x, y, [with_sep(self.lab("customer_gst"), ":"), f"{party.bn} RT0001"], s * 0.9)
            y += self.lh
        return y

    # --- standard invoice / credit note ---
    def render_standard(self) -> None:
        inv, cv, k, m, s = self.inv, self.cv, self.k, self.m, self.s
        W = self.W
        pos = k["vendor_pos"]
        S = self.S
        y0 = m
        ttl = title(S("title"), inv.kind, inv.lang)
        tsize = S("title_size").uniform(16, 26)
        items = list(inv.header)
        if k["title_number"]:
            num_item = next(it for it in items if it.fld == "invoice_number")
            if S("title_number_only").random() < 0.6:
                items.remove(num_item)
        else:
            num_item = None

        def draw_title(x: float, y: float, align: str) -> float:
            if num_item:
                parts = [ttl, inv.number] if S("title_hash").random() < 0.5 else [ttl, "#" + inv.number]
                boxes = cv.parts(
                    x,
                    y + tsize,
                    parts,
                    cv.fit(" ".join(parts), tsize, W * 0.36, True),
                    bold=True,
                    color=self.accent,
                    align=align,
                )
                raw = parts[1]
                self.put("invoice_number", inv.number, raw, boxes[1], ttl)
            else:
                cv.text(
                    x, y + tsize, ttl, cv.fit(ttl, tsize, W * 0.36, True), bold=True, color=self.accent, align=align
                )
            return y + tsize + 8

        header_style = k["header"]
        if pos == "left":
            vb = self.vendor_block(m, y0, "left")
            ty = draw_title(W - m, y0, "right")
            if header_style == "kv":
                hy = self.header_kv(items, W * 0.55, W - m, ty)
                top_end = max(vb, hy)
                cy = vb + 10
                by = self.party_blocks(m, max(cy, vb + 8), W * 0.5 - m)
                y = max(by, hy) + 14
            else:
                by = self.party_blocks(m, vb + 10, W - 2 * m)
                y = self.header_cells(items, m, W - m, max(by, ty) + 8, header_style) + 8
        elif pos == "right":
            vb = self.vendor_block(W - m, y0, "right")
            ty = draw_title(m, y0, "left")
            if header_style == "kv":
                hy = self.header_kv(items, m, W * 0.46, ty)
                top_end = max(vb, hy)
                by = self.party_blocks(m, top_end + 12, W - 2 * m)
                y = by + 12
            else:
                top_end = max(vb, ty)
                by = self.party_blocks(m, top_end + 10, W - 2 * m)
                y = self.header_cells(items, m, W - m, by + 6, header_style) + 8
        else:
            vb = self.vendor_block(W / 2, y0, "center")
            ty = draw_title(W / 2, vb + 4, "center")
            if header_style == "kv":
                hy = self.header_kv(items, W * 0.55, W - m, ty)
                by = self.party_blocks(m, ty + 10, W * 0.5 - m)
                y = max(hy, by) + 14
            else:
                by = self.party_blocks(m, ty + 6, W - 2 * m)
                y = self.header_cells(items, m, W - m, by + 6, header_style) + 8
        if k["currency_where"] == "note":
            note = phrase(S("amounts_in"), "amounts_in", "fr" if inv.lang == "fr" else "en")
            boxes = cv.parts(m, y, [note, inv.currency], s * 0.9, color=GREY)
            self.put("currency", inv.currency, inv.currency, boxes[1])
            y += self.lh + 4
        y = self.table(m, W - m, y)
        y = self.totals(y)
        self.footer(y)

    def party_blocks(self, x: float, y: float, width: float) -> float:
        ship = self.k["ship_to"]
        cg = self.S("customer_gst").random() < 0.15
        colw = width / 2 - 8 if ship else width
        a = self.party_block(x, y, "bill_to", self.inv.customer, colw, cg)
        if ship:
            site = self.S("ship_site").choice(["Receiving Dock 3", "Plant 2", "Warehouse"])
            other = C.Party(
                self.inv.customer.name,
                [site] + self.inv.customer.lines[-2:],
                self.inv.customer.province,
                "CA",
            )
            b = self.party_block(x + width / 2 + 8, y, "ship_to", other, colw, False)
            a = max(a, b)
        return a

    # --- table ---
    def columns(self, x0: float, x1: float) -> list[tuple[str, str, float, str]]:
        inv, S = self.inv, self.S
        has_qty = any(li.quantity is not None for li in inv.lines)
        cols = []
        if any(li.sku for li in inv.lines) and S("col_sku").random() < 0.6:
            cols.append(("sku", self.lab("sku"), 0.13, "left"))
        cols.append(("description", self.lab("description"), 0.0, "left"))
        if has_qty and (inv.kind != "minimal" or S("col_qty").random() < 0.5):
            cols.append(("qty", self.lab("qty"), 0.08, "right"))
            if S("col_unit").random() < 0.3:
                cols.append(("unit", self.lab("unit"), 0.07, "left"))
            cols.append(("unit_price", self.lab("unit_price"), 0.14, "right"))
        cols.append(("amount", self.lab("amount"), 0.15, "right"))
        fixed = sum(c[2] for c in cols)
        width = x1 - x0
        out = []
        x = x0
        for key, head, rel, al in cols:
            w = (1 - fixed) * width if rel == 0 else rel * width
            out.append((key, head, x, x + w, al))
            x += w
        return out

    def table_header(self, cols, y: float) -> float:
        cv, s, style = self.cv, self.s, self.k["table"]
        h = s * 2.1
        x0, x1 = cols[0][2], cols[-1][3]
        color = INK
        if style == "band":
            cv.rect(x0, y, x1, y + h, fill=self.accent, stroke=self.accent)
            color = (1, 1, 1)
        elif style == "grid":
            cv.rect(x0, y, x1, y + h, fill=LIGHT, stroke=GREY)
        elif style == "lines":
            cv.hline(x0, x1, y, INK, 0.8)
            cv.hline(x0, x1, y + h, INK, 0.8)
        else:
            cv.hline(x0, x1, y + h, GREY, 0.5)
        for _key, head, cx0, cx1, al in cols:
            hs = cv.fit(head, s, cx1 - cx0 - 6, bold=True)
            tx = cx0 + 3 if al == "left" else cx1 - 3
            cv.text(tx, y + h * 0.66, head, hs, bold=True, color=color, align=al)
        return y + h

    def cont_header(self) -> float:
        """Top of a continuation page: vendor name, the invoice number again, the table header."""
        cv, s, inv, m = self.cv, self.s, self.inv, self.m
        y = m + s * 1.6
        box = cv.text(m, y, inv.vendor.name, s * 1.2, bold=True)
        self.put("vendor_name", inv.vendor.name, inv.vendor.name, box)
        if self.S("cont_number").random() < 0.8:
            lbl = with_sep(self.lab({"credit": "credit_number"}.get(inv.kind, "invoice_number")), ":")
            boxes = cv.parts(self.W - m, y, [lbl, inv.number], s, align="right")
            self.put("invoice_number", inv.number, inv.number, boxes[1], lbl)
        return y + s * 2.4

    def table(self, x0: float, x1: float, y: float) -> float:
        inv, cv, s, rng = self.inv, self.cv, self.s, self.rng
        cols = self.columns(x0, x1)
        self.cols = cols
        y = self.table_header(cols, y)
        rowh = s * self.S("row_height").uniform(1.75, 2.15)
        style = self.k["table"]
        dec = inv.ms.decimal
        sym_lines = inv.ms.symbol and self.S("line_symbols").random() < 0.4
        start_y = y
        for i, li in enumerate(inv.lines):
            if y + rowh > self.page_bottom - 6:
                cv.text(
                    x1,
                    y + rowh * 0.7,
                    phrase(self.S("continued"), "continued", "fr" if inv.lang == "fr" else "en"),
                    s * 0.85,
                    color=GREY,
                    align="right",
                )
                if style == "grid":
                    self.grid_lines(cols, start_y, y)
                cv.new_page()
                y = self.table_header(cols, self.cont_header())
                start_y = y
            if self.k["zebra"] and i % 2 == 1:
                cv.rect(x0, y, x1, y + rowh, fill=(0.95, 0.95, 0.95), stroke=None)
            base = y + rowh * 0.66
            boxes = []
            desc = li.description
            for key, _head, cx0, cx1, al in cols:
                tx = cx0 + 3 if al == "left" else cx1 - 3
                if key == "sku":
                    val = li.sku
                elif key == "description":
                    desc = cv.clip(li.description, s, cx1 - cx0 - 6)
                    val = desc
                elif key == "qty":
                    val = C.number_text(li.quantity, dec) if li.quantity is not None else ""
                elif key == "unit":
                    val = li.unit
                elif key == "unit_price":
                    if li.unit_price is None:
                        val = ""
                    else:
                        places = 4 if li.unit_price != li.unit_price.quantize(Decimal("0.01")) else 2
                        val = C.number_text(li.unit_price, dec, places)
                else:
                    val = inv.amt(li.amount, symbol=bool(sym_lines))
                if val:
                    boxes.append(cv.text(tx, base, val, s, align=al))
            if style in ("lines", "grid") and rng.random() < 0.7:
                cv.hline(x0, x1, y + rowh, (0.75, 0.75, 0.75), 0.4)
            y += rowh
            qty_col = any(c[0] == "qty" for c in cols)
            self.t.lines.append(
                {
                    "description": desc,
                    "quantity": float(li.quantity) if li.quantity is not None and qty_col else None,
                    "unit_price": float(li.unit_price) if li.unit_price is not None and qty_col else None,
                    "amount": float(li.amount),
                    "kind": li.kind,
                    "box": _union(boxes),
                }
            )
        if style == "grid":
            self.grid_lines(cols, start_y, y)
        if style != "plain" and self.k["totals"] != "footer":
            cv.hline(x0, x1, y, INK, 0.8)
        return y + 6

    def grid_lines(self, cols, y0: float, y1: float) -> None:
        for _k, _h, cx0, _cx1, _al in cols:
            self.cv.vline(cx0, y0, y1)
        self.cv.vline(cols[-1][3], y0, y1)
        self.cv.hline(cols[0][2], cols[-1][3], y1)

    # --- totals ---
    def total_rows(self) -> list[tuple[str, Decimal, str | None, bool]]:
        inv = self.inv
        lang = inv.lang
        rows: list[tuple[str, Decimal, str | None, bool]] = [(self.lab("subtotal"), inv.subtotal, "subtotal", False)]
        if inv.freight is not None:
            rows.append((self.lab("freight"), inv.freight, None, False))
        rate_style = self.S("rate_style").choice(["paren", "plain", "at", "none"])
        for tax in inv.taxes:
            name = self.lab(tax.code)
            rt = C.rate_text(tax.rate, "fr" if lang == "fr" or (lang == "bi" and inv.ms.decimal == ",") else "en")
            if rate_style == "paren":
                name = f"{name} ({rt})"
            elif rate_style == "plain":
                name = f"{name} {rt}"
            elif rate_style == "at":
                name = f"{name} @ {rt}"
            rows.append((name, tax.amount, tax.field, False))
        if inv.taxes and self.k["show_tax_total"] and inv.taxes[0].field != "tax_total":
            rows.append((self.lab("tax_total"), inv.tax_total, "tax_total", False))
        total_key = "credit_total" if inv.kind == "credit" else "grand_total"
        rows.append((self.lab(total_key), inv.grand, "grand_total", True))
        if self.k["balance_row"] and inv.kind == "invoice":
            rows.append((self.lab("amount_paid"), Decimal("0.00"), None, False))
            rows.append((self.lab("amount_due"), inv.grand, "grand_total", True))
        return rows

    def totals(self, y: float) -> float:
        inv, cv, s, S, k = self.inv, self.cv, self.s, self.S, self.k
        rows = self.total_rows()
        lh = s * S("totals_lh").uniform(1.55, 1.9)
        need = lh * len(rows) + 14 + self.lh * 4.5 + (122 if k["stub"] else 0)
        if y + need > self.page_bottom:
            cv.new_page()
            y = self.cont_header()
        style = k["totals"]
        sep = S("totals_sep").choice([":", "", ""])
        code_on_total = k["currency_where"] == "total" or bool(inv.ms.code)
        if code_on_total and not inv.ms.code:
            inv.ms.code = inv.currency
        total_size = s * S("total_size").uniform(1.0, 1.3)
        amt_texts = [inv.amt(a, code=(fld == "grand_total" and code_on_total)) for _l, a, fld, _e in rows]
        if style == "footer":
            cols = self.cols
            lx = cols[-2][3] - 3
            ax = cols[-1][3] - 3
        else:
            amt_w = max(cv.tw(t, total_size, True) for t in amt_texts) + 6
            lab_w = max(cv.tw(with_sep(r[0], sep), s, True) for r in rows)
            block = max(200.0, amt_w + lab_w + 24)
            if style == "right":
                ax = self.W - self.m - 3
                lx = ax - amt_w - 12
            else:
                lx = self.m + lab_w + 3
                ax = self.m + block
        for (lbl, amount, fld, emph), raw in zip(rows, amt_texts, strict=True):
            y += lh
            size = total_size if emph else s
            lbl_text = with_sep(lbl, sep)
            row_lx = min(lx, ax - cv.tw(raw, size, emph) - 14)  # never under a wide amount
            if style == "footer":
                cv.hline(self.cols[0][2], self.cols[-1][3], y - lh + 2, (0.7, 0.7, 0.7), 0.4)
            elif emph and (r := S(f"total_box:{lbl}")).random() < 0.5:
                x_left = row_lx - cv.tw(lbl_text, size, True) - 6
                if r.random() < 0.5:
                    cv.rect(x_left, y - size * 1.15, ax + 3, y + size * 0.45, fill=LIGHT, stroke=None)
                else:
                    cv.hline(x_left, ax + 3, y - size * 1.2, INK, 0.8)
            cv.text(row_lx, y, lbl_text, size, bold=emph or S(f"total_bold:{lbl}").random() < 0.3, align="right")
            if fld == "grand_total" and code_on_total and inv.ms.code and inv.ms.code in raw:
                num = raw.replace(inv.ms.code, "").strip()
                parts = [num, inv.ms.code] if inv.ms.code_after else [inv.ms.code, num]
                boxes = cv.parts(ax, y, parts, size, bold=emph, align="right")
                box = _union(boxes)
                code_box = boxes[1] if inv.ms.code_after else boxes[0]
                self.put("currency", inv.currency, inv.ms.code, code_box)
            else:
                box = cv.text(ax, y, raw, size, bold=emph, align="right")
            if fld:
                self.put_amount(fld, amount, raw, box, lbl_text)
        if k["bn_where"] == "tax":
            y += lh
            if self.reg_line(ax, y, "bn", "right"):
                y += self.lh
            if self.reg_line(ax, y, "qst", "right"):
                y += self.lh
        return y + 10

    # --- footer and stub ---
    def footer(self, y: float) -> None:
        inv, cv, s, S, k, m = self.inv, self.cv, self.s, self.S, self.k, self.m
        lang = "fr" if inv.lang == "fr" else "en"
        y += self.lh
        if inv.terms and k["terms_where"] == "footer":
            lbl = with_sep(self.lab("payment_terms"), ":")
            boxes = cv.parts(m, y, [lbl, inv.terms.raw], s)
            self.put("payment_terms", inv.terms.raw, inv.terms.raw, boxes[1], lbl)
            y += self.lh
        if (r := S("footer_interest")).random() < 0.4:
            cv.text(m, y, phrase(r, "interest", lang), s * 0.8, color=GREY)
            y += self.lh
        if (r := S("footer_bank")).random() < 0.3:
            cv.text(
                m,
                y,
                f"Transit {r.randint(10000, 99999)}  Institution {r.randint(1, 9):03d}  "
                f"Account {r.randint(1000000, 9999999)}",
                s * 0.8,
                color=GREY,
            )
            y += self.lh
        if (r := S("footer_thanks")).random() < 0.5:
            cv.text(self.W / 2, y + 2, phrase(r, "thanks", lang), s, color=self.accent, align="center")
            y += self.lh * 1.5
        stub_top = self.page_bottom - 118
        if k["bn_where"] == "footer":
            one_line = self.show_bn and self.show_qst and S("bn_one_line").random() < 0.6
            n_lines = 1 if one_line else int(self.show_bn) + int(self.show_qst)
            bottom = (stub_top - 10) if k["stub"] else self.page_bottom - 4
            fy = bottom - self.lh * (n_lines - 1)
            if fy < y:  # no room at the foot of the page: print it right here
                fy = y
            x = self.W / 2 if S("bn_footer_x").random() < 0.5 else m
            al = "center" if x == self.W / 2 else "left"
            if one_line:
                parts = [with_sep(self.lab("bn"), ":"), self.bn_raw, "|", with_sep(self.lab("qst"), ":"), self.qst_raw]
                boxes = cv.parts(x, fy, parts, s * 0.85, align=al, color=GREY)
                self.put("gst_hst_registration_number", C.reg_value(self.bn_raw), self.bn_raw, boxes[1], parts[0])
                self.put("qst_registration_number", C.reg_value(self.qst_raw), self.qst_raw, boxes[4], parts[3])
            else:
                if self.reg_line(x, fy, "bn", al, s * 0.85):
                    fy += self.lh
                self.reg_line(x, fy, "qst", al, s * 0.85)
        if k["stub"]:
            self.stub(stub_top)

    def stub(self, y: float) -> None:
        inv, cv, s, S, m, W = self.inv, self.cv, self.s, self.S, self.m, self.W
        lang = "fr" if inv.lang == "fr" else "en"
        cv.hline(m, W - m, y, GREY, 0.8, dashes="[4 3] 0")
        y += s * 1.6
        cv.text(W / 2, y, phrase(S("remit"), "remit", lang), s * 0.85, bold=True, align="center")
        y += s * 1.4
        left_y = y + self.lh
        lbl = phrase(S("payable"), "payable", lang) + ":"
        cv.text(m, left_y, lbl, s * 0.9, color=GREY)
        box = cv.text(m, left_y + self.lh, inv.vendor.name, s, bold=True)
        self.put("vendor_name", inv.vendor.name, inv.vendor.name, box)
        ly = left_y + self.lh * 2
        for line in inv.vendor.lines:
            cv.text(m, ly, line, s * 0.9)
            ly += self.lh * 0.9
        # stub fields on the right, kv
        items: list[tuple[str, str, str | None, Any]] = []
        acct = inv.extras.get("customer_account")
        if acct:
            items.append((self.lab("customer_account"), acct, None, None))
        num_key = {"credit": "credit_number", "utility": "bill_number"}.get(inv.kind, "invoice_number")
        items.append((self.lab(num_key), inv.number, "invoice_number", inv.number))
        if inv.due and S("stub_due").random() < 0.7:
            items.append((self.lab("due_date"), inv.d(inv.due), "due_date", inv.due.isoformat()))
        lbl = self.lab("amount_due")
        items.append((lbl, inv.amt(inv.grand, symbol=S("stub_symbol").random() < 0.5), "grand_total", float(inv.grand)))
        items.append((phrase(S("enclosed"), "enclosed", lang), "______________", None, None))
        x0 = W * 0.5
        x1 = W - m
        yy = y
        for lbl, raw, fld, value in items:
            yy += self.lh * 1.15
            lt = with_sep(lbl, ":")
            cv.text(x0, yy, lt, s * 0.95, bold=True)
            box = cv.text(x1, yy, raw, s * 0.95, align="right")
            if fld:
                self.put(fld, value, raw, box, lt)

    # --- utility bill ---
    def render_utility(self) -> None:
        inv, cv, s, rng, m, W = self.inv, self.cv, self.s, self.rng, self.m, self.W
        S = self.S
        lang = inv.lang
        vb = self.vendor_block(m, m, "left")
        ttl = title(S("title"), "utility", lang)
        cv.text(W - m, m + 18, ttl, 18, bold=True, color=self.accent, align="right")
        # amount-due panel, top right
        px0, px1 = W * 0.58, W - m
        py0 = m + 30
        cv.rect(px0, py0, px1, py0 + 70, stroke=self.accent, fill=(0.95, 0.96, 0.98), width=1.2)
        lbl = self.lab("amount_due")
        cv.text(px0 + 8, py0 + 18, lbl, s * 1.1, bold=True)
        raw = inv.amt(inv.grand, symbol=True)
        box = cv.text(px1 - 8, py0 + 44, raw, 20, bold=True, align="right")
        self.put_amount("grand_total", inv.grand, raw, box, lbl)
        dl = with_sep(self.lab("due_date"), ":")
        boxes = cv.parts(px1 - 8, py0 + 62, [dl, inv.d(inv.due)], s, align="right")
        self.put("due_date", inv.due.isoformat(), inv.d(inv.due), boxes[1], dl)
        items = [it for it in inv.header if it.fld != "due_date"]
        hy = self.header_kv(items, px0, px1, py0 + 76)
        party = "ship_to" if S("utility_party").random() < 0.5 else "bill_to"
        by = self.party_block(m, vb + 10, party, inv.customer, W * 0.45, False)
        y = max(hy, by) + 16
        # account summary
        prev = C.money(rng.uniform(80, 2400))
        sum_rows = [
            (self.lab("previous_balance"), prev, None),
            (self.lab("payment_received"), -prev, None),
            (self.lab("balance_forward"), Decimal("0.00"), None),
            (self.lab("current_charges"), inv.grand, "grand_total"),
        ]
        cv.text(
            m, y, "Account summary" if lang != "fr" else "Sommaire du compte", s * 1.1, bold=True, color=self.accent
        )
        y += 4
        for lbl, a, fld in sum_rows:
            y += self.lh
            cv.text(m, y, lbl, s)
            raw = inv.amt(a)
            box = cv.text(m + 260, y, raw, s, align="right")
            if fld:
                self.put_amount(fld, a, raw, box, lbl)
        inv.extras["previous_balance"] = float(prev)
        y += self.lh * 1.6
        # meter
        if any(li.unit for li in inv.lines):
            meter = S("meter").randint(1000000, 9999999)
            prev_r = rng.randint(10000, 90000)
            usage = next(int(li.quantity) for li in inv.lines if li.unit)
            cv.text(
                m,
                y,
                f"Meter {meter}   Previous reading {prev_r}   Current reading {prev_r + usage}   "
                f"Usage {usage} {next(li.unit for li in inv.lines if li.unit)}",
                s * 0.85,
                color=GREY,
            )
            y += self.lh * 1.4
        cv.text(m, y, "Current charges" if lang != "fr" else "Frais courants", s * 1.1, bold=True, color=self.accent)
        y += 6
        self.k["table"] = S("utility_table").choice(["lines", "plain"])
        y = self.table(m, W - m, y)
        y = self.totals(y)
        self.footer(y)

    # --- minimal handwritten-style ---
    def render_minimal(self) -> None:
        inv, cv, s, S, m, W = self.inv, self.cv, self.s, self.S, self.m, self.W
        y = m + 14
        box = cv.text(m, y, inv.vendor.name, 13, bold=True)
        self.put("vendor_name", inv.vendor.name, inv.vendor.name, box)
        y += 18
        cv.text(m, y, ", ".join(inv.vendor.lines), s)
        y += self.lh
        if inv.vendor.phone:
            cv.text(m, y, inv.vendor.phone, s)
            y += self.lh
        y += 10
        cv.text(W - m, m + 14, "INVOICE" if S("title_case").random() < 0.7 else "Invoice", 16, bold=True, align="right")
        numlbl = S("label:invoice_number").choice(["No.", "Invoice #", "#", "Invoice No:", "Inv"])
        boxes = cv.parts(W - m, m + 34, [numlbl, inv.number], s * 1.1, align="right")
        self.put("invoice_number", inv.number, inv.number, boxes[1], numlbl)
        dl = S("label:invoice_date").choice(["Date:", "Date", "Dated"])
        boxes = cv.parts(W - m, m + 34 + self.lh * 1.3, [dl, inv.d(inv.inv_date)], s * 1.1, align="right")
        self.put("invoice_date", inv.inv_date.isoformat(), inv.d(inv.inv_date), boxes[1], dl)
        yy = m + 34 + self.lh * 2.6
        if inv.po:
            pl = S("label:po_number").choice(["PO:", "P.O.", "Your PO#"])
            boxes = cv.parts(W - m, yy, [pl, inv.po], s * 1.1, align="right")
            self.put("po_number", inv.po, inv.po, boxes[1], pl)
            yy += self.lh * 1.3
        cv.text(m, y, S("label:bill_to").choice(["To:", "Bill to:", "For:"]), s, bold=True)
        cv.text(m + 50, y, inv.customer.name, s)
        y += self.lh
        cv.text(m + 50, y, inv.customer.lines[-1], s)
        y = max(y, yy) + 26
        self.k["table"] = "plain"
        self.k["totals"] = "right"
        y = self.table(m, W - m, y)
        y = self.totals(y)
        y += 8
        if inv.terms:
            boxes = cv.parts(m, y, ["Terms:", inv.terms.raw], s)
            self.put("payment_terms", inv.terms.raw, inv.terms.raw, boxes[1], "Terms:")
            y += self.lh
        if self.show_bn and self.k["bn_where"] != "tax":
            gl = S("label:bn").choice(["GST #", "GST/HST #", "HST #", "GST No."])
            boxes = cv.parts(m, y + 6, [gl, self.bn_raw], s)
            self.put("gst_hst_registration_number", C.reg_value(self.bn_raw), self.bn_raw, boxes[1], gl)
            y += self.lh
        cv.text(m, y + 20, S("thanks").choice(["Thanks!", "Thank you!", "Much appreciated."]), s)

    # --- truth ---
    def truth(self) -> dict[str, Any]:
        def frac(b: PtBox) -> list[float]:
            p, x0, y0, x1, y1 = b
            box = Box(
                int(p), max(0.0, x0 / self.W), max(0.0, y0 / self.H), min(1.0, x1 / self.W), min(1.0, y1 / self.H)
            )
            return box.to_list()

        inv = self.inv
        fields = {}
        for name, e in self.t.fields.items():
            fields[name] = {
                "value": e["value"],
                "raw": e["raw"],
                "label": e["label"],
                "box": frac(e["box"]),
                "also": [{"raw": a["raw"], "box": frac(a["box"])} for a in e["also"]],
            }
        implied: dict[str, Any] = {}
        if "currency" not in fields:
            implied["currency"] = inv.currency
        if "tax_total" not in fields and inv.taxes:
            implied["tax_total"] = float(inv.tax_total)
        return {
            "layout": inv.archetype,
            "doc_type": inv.kind,
            "language": inv.lang,
            "country": inv.country,
            "province": inv.province,
            "page_count": self.cv.pno,
            "page_size": [self.W, self.H],
            "fields": fields,
            "implied": implied,
            "line_items": [{**li, "box": frac(li["box"])} for li in self.t.lines],
            "extras": {
                **inv.extras,
                **({"freight": float(inv.freight)} if inv.freight is not None else {}),
                "font": self.k["family"],
                "date_style": inv.date_style,
            },
        }


# --- scanning ---------------------------------------------------------------------------------


def rotate_box(box: list[float], angle: float, w: float, h: float) -> list[float]:
    """Map a fraction box through PIL's `Image.rotate(angle)` (counter-clockwise, about the centre)."""
    p, x0, y0, x1, y1 = box
    a = math.radians(angle)
    ca, sa = math.cos(a), math.sin(a)
    cx, cy = w / 2, h / 2
    xs, ys = [], []
    for fx, fy in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
        dx, dy = fx * w - cx, fy * h - cy
        xs.append((cx + dx * ca + dy * sa) / w)
        ys.append((cy - dx * sa + dy * ca) / h)
    out = Box(int(p), max(0.0, min(xs)), max(0.0, min(ys)), min(1.0, max(xs)), min(1.0, max(ys)))
    return out.to_list()


def scan(pdf: bytes, rng: random.Random, as_png: bool) -> tuple[bytes, dict[str, Any]]:
    """Print, scan and compress: rasterize, tint, rotate, blur, add noise, JPEG. Returns the bytes
    (an image-only PDF, or a PNG) and the scan settings, including each page's angle."""
    import numpy as np
    from PIL import Image, ImageFilter

    pymupdf = _pymupdf()
    src = pymupdf.open(stream=pdf, filetype="pdf")
    dpi = rng.randint(150, 200)
    quality = rng.randint(55, 85)
    gray = rng.random() < 0.7
    nrng = np.random.default_rng(rng.randrange(2**32))
    angles = []
    images = []
    for page in src:
        pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY if gray else pymupdf.csRGB, alpha=False)
        img = Image.frombytes("L" if gray else "RGB", (pix.width, pix.height), pix.samples)
        arr = np.asarray(img).astype(np.float32)
        paper = rng.uniform(232, 252)
        toner = rng.uniform(0.0, 40.0)
        arr = toner + arr * (paper - toner) / 255.0
        img = Image.fromarray(arr.clip(0, 255).astype(np.uint8))
        angle = round(rng.uniform(-1.5, 1.5), 3)
        angles.append(angle)
        fill = int(paper) if gray else (int(paper),) * 3
        img = img.rotate(angle, resample=Image.BICUBIC, fillcolor=fill, expand=False)
        img = img.filter(ImageFilter.GaussianBlur(rng.uniform(0.2, 0.9)))
        arr = np.asarray(img).astype(np.float32)
        arr = arr + nrng.normal(0, rng.uniform(3, 11), arr.shape)
        speck = nrng.random(arr.shape[:2]) < rng.uniform(0.00005, 0.0006)
        arr[speck] = rng.uniform(0, 90)
        if rng.random() < 0.4:  # the scanner lid's shadow along one edge
            edge = rng.randint(4, 14)
            side = rng.choice(["l", "r", "t", "b"])
            sl = {"l": np.s_[:, :edge], "r": np.s_[:, -edge:], "t": np.s_[:edge, :], "b": np.s_[-edge:, :]}[side]
            arr[sl] = arr[sl] * 0.55
        img = Image.fromarray(arr.clip(0, 255).astype(np.uint8))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        images.append((buf.getvalue(), page.rect.width, page.rect.height))
    meta = {"dpi": dpi, "jpeg_quality": quality, "angles": angles, "gray": gray}
    if as_png:
        img = Image.open(io.BytesIO(images[0][0]))
        out = io.BytesIO()
        img.save(out, format="PNG", optimize=False)
        meta["format"] = "png"
        return out.getvalue(), meta
    doc = pymupdf.open()
    for jpg, w, h in images:
        pg = doc.new_page(width=w, height=h)
        pg.insert_image(pg.rect, stream=jpg)
    doc.set_metadata(
        {"producer": "ap_coder.bench scan", "creator": "ap_coder.bench", "creationDate": "", "modDate": ""}
    )
    meta["format"] = "pdf"
    return doc.tobytes(garbage=3, deflate=True, no_new_id=True), meta


def _rotate_truth(truth: dict[str, Any], angles: list[float]) -> None:
    w, h = truth["page_size"]

    def rot(b: list[float]) -> list[float]:
        return rotate_box(b, angles[int(b[0]) - 1], w, h)

    for e in truth["fields"].values():
        e["box"] = rot(e["box"])
        for a in e["also"]:
            a["box"] = rot(a["box"])
    for li in truth["line_items"]:
        li["box"] = rot(li["box"])


# --- cases ------------------------------------------------------------------------------------


@dataclass
class Case:
    id: str
    path: Path
    truth_path: Path
    truth: dict[str, Any]

    @property
    def scanned(self) -> bool:
        return bool(self.truth.get("scanned"))

    @property
    def layout(self) -> str:
        return self.truth.get("layout", "")


def case_rng(seed: int, index: int) -> random.Random:
    return random.Random(f"ap-bench:{GENERATOR_VERSION}:{seed}:{index}")


def plan(n: int, seed: int, scanned_fraction: float) -> list[tuple[int, str, bool]]:
    """(index, archetype, scanned) for each case: archetypes in a seeded rotation so every one is
    covered; an exact share of scans, spread at random."""
    master = random.Random(f"ap-bench-plan:{seed}")
    order = list(ARCHETYPES)
    master.shuffle(order)
    scanned = set(master.sample(range(n), round(n * max(0.0, min(1.0, scanned_fraction))))) if n else set()
    return [(i, order[i % len(order)], i in scanned) for i in range(n)]


def make_case(out_dir: str | Path, seed: int, index: int, archetype: str, scanned: bool) -> Case:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rng = case_rng(seed, index)
    inv = make_invoice(rng, archetype)
    pdf, truth = Renderer(inv, rng).render()
    return _write_case(out, f"s{seed}-{index:04d}", pdf, truth, scanned, f"ap-bench-scan:{seed}:{index}")


def _write_case(out: Path, cid: str, pdf: bytes, truth: dict[str, Any], scanned: bool, scan_seed: str) -> Case:
    truth = {"id": cid, **truth, "scanned": scanned}
    if scanned:
        srng = random.Random(scan_seed)
        as_png = truth["page_count"] == 1 and srng.random() < 0.4
        data, meta = scan(pdf, srng, as_png)
        _rotate_truth(truth, meta["angles"])
        truth["scan"] = meta
        ext = "png" if as_png else "pdf"
    else:
        data, ext = pdf, "pdf"
    path = out / f"{cid}.{ext}"
    path.write_bytes(data)
    truth["file"] = path.name
    tpath = out / f"{cid}.truth.json"
    tpath.write_text(json.dumps(truth, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return Case(cid, path, tpath, truth)


# --- supplier streams: many invoices from one supplier ---------------------------------------


def supplier_id(seed: int, supplier: int) -> str:
    return f"s{seed}-v{supplier:03d}"


def supplier_archetype(seed: int, supplier: int) -> str:
    """Suppliers take the layouts in a seeded rotation, so every layout has suppliers."""
    order = list(ARCHETYPES)
    random.Random(f"ap-bench-suppliers:{seed}").shuffle(order)
    return order[supplier % len(order)]


def stream_scanned(seed: int, supplier: int, index: int, scanned_fraction: float) -> bool:
    """Whether this invoice of the supplier arrives as a scan (each one on its own: a share, at random)."""
    if scanned_fraction <= 0:
        return False
    return scanned_fraction >= 1 or random.Random(f"ap-bench-stream-scan:{seed}:{supplier}:{index}").random() < (
        scanned_fraction
    )


def make_stream_case(out_dir: str | Path, seed: int, supplier: int, index: int, scanned: bool = False) -> Case:
    """Invoice ``index`` (from 0) of supplier ``supplier``: the supplier's name, address, tax numbers,
    layout, fonts, wording and formats are the same on every one; the number (in sequence), dates (in
    order), PO, lines and amounts are new each time. Deterministic from (seed, supplier, index)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    sid = supplier_id(seed, supplier)
    rng = random.Random(f"ap-bench-stream:{GENERATOR_VERSION}:{seed}:{supplier}:{index}")
    style = Style(rng, key=f"{GENERATOR_VERSION}:{sid}", index=index)
    inv = make_invoice(rng, supplier_archetype(seed, supplier), style)
    pdf, truth = Renderer(inv, rng).render()
    truth = {**truth, "supplier": sid, "stream_index": index}
    return _write_case(out, f"{sid}-{index:04d}", pdf, truth, scanned, f"ap-bench-scan:{sid}:{index}")


def generate(out_dir: str | Path, n: int, seed: int, scanned_fraction: float = 0.3) -> list[Case]:
    """Write n invoices with their truth into out_dir; deterministic for a given seed."""
    return [make_case(out_dir, seed, i, arch, sc) for i, arch, sc in plan(n, seed, scanned_fraction)]


def load_cases(out_dir: str | Path) -> list[Case]:
    out = Path(out_dir)
    cases = []
    for tp in sorted(out.glob("*.truth.json")):
        truth = json.loads(tp.read_text(encoding="utf-8"))
        cases.append(Case(truth["id"], out / truth["file"], tp, truth))
    return cases
