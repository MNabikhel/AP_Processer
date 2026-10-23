"""GL coding with no AI model: how often invoice lines get the right account from what AP approved before.

``run_coding(vendors, invoices, seed, out)`` simulates one company. ``vendors`` suppliers (seeded) each sell their
own mix of the catalog's items (``content.GOODS``; utilities bill ``content.UTILITY_LINES``), worded their own way:
abbreviations, word order, SKU and size text, plural or singular, English, French or both. A few have a default
GL account in the vendor master. Their invoices arrive over time, interleaved, and each one is played through the
code the app runs when there is no AI model (no LM Studio chat model, no Azure):

1. the lines are coded by ``offline_coder.code_from_capture`` from ``Store.feedback_rows()`` (what AP approved so
   far), the chart of accounts in ``data/`` and the vendor master's default account, then the fixed coding rules
   are applied (``rules.apply``), as ``pipeline.process`` does. For the lines left without an account, the review
   screen's suggestions (``invoice_view.suggestions``) are recorded too;
2. AP approves the true account of every line (``Store.approve_invoice``), which records the feedback rows the
   next invoice learns from.

Every line is scored: the account proposed (or UNASSIGNED) against the truth. *Precision* is the share of the
accounts proposed that were right; *coverage* the share of lines given an account; *accuracy* the share of all
lines given the right one. Writes ``report.json``, ``report.md`` and ``lines.jsonl``.
"""

from __future__ import annotations

import datetime as dt
import json
import random
import re
import tempfile
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ap_coder.bench import content as C
from ap_coder.bench.harness import _pct, _table

DATA = Path(__file__).resolve().parent.parent.parent / "data"
REVIEWER = "AP clerk (benchmark)"

# The true account of each catalog item in data/chart_of_accounts.csv, as an AP clerk would code it.
ITEM_GL = {
    "Nitrile gloves, box of 100": "5000",  # consumables used up on the job
    "Copy paper 8.5x11, case of 10": "6000",
    "Hex bolt 3/8-16 x 2 in., zinc": "5000",  # components
    "LED panel 2x4 ft, 40W": "6220",  # building repairs
    "Hydraulic hose assembly 1/2 in.": "6220",  # equipment repairs
    "Safety glasses, clear, ANSI Z87": "5000",
    "Toner cartridge HP 58X": "6000",
    "Pallet wrap 18 in. x 1500 ft": "5000",  # packaging
    "Electrician - labour": "6220",
    "Technician on site": "6220",
    "Consulting services - September": "6110",
    "Monthly maintenance contract": "6220",
    "Forklift rental (weekly)": "6200",  # a rental: the chart's only rent account
    "Janitorial service - Unit 4": "6230",
    "Cable Cat6 plenum, 1000 ft": "6010",
    "Shipping cartons 18x12x12": "5000",  # packaging
    "Disinfectant 4 L": "6230",
    "Diesel exhaust fluid 9.46 L": "6220",  # fleet upkeep
    "Software subscription - 12 users": "6020",
    "Printing - brochures 8 pages": "6400",
    "Calibration of pressure gauges": "6220",
    "Bearing 6205-2RS": "6220",  # a spare part
    "Lab beakers 250 mL, pack of 12": "5000",
    "Fleet vehicle inspection": "6220",
}
# A utility's lines: electricity, gas and water go to Utilities, a phone company's to Telecommunications. The
# same words ("Basic monthly charge") mean a different account from a different kind of vendor.
UTILITY_GL = {"hydro": "6210", "gas": "6210", "water": "6210", "telecom": "6040"}
UTILITY_ITEMS = {
    "hydro": ["Basic monthly charge", "Delivery charge", "Energy charge", "Regulatory charges"],
    "gas": ["Basic monthly charge", "Delivery charge", "Energy charge", "Regulatory charges"],
    "water": ["Basic monthly charge", "Water consumption", "Wastewater charge"],
    "telecom": ["Basic monthly charge", "Long distance & features", "Regulatory charges"],
}
UTILITY_NAMES = {"hydro": ["Hydro", "Power", "Energy"], "gas": ["Gas"], "water": ["Water & Wastewater"],
                 "telecom": ["Telecom", "Communications"]}  # fmt: skip
COST_CENTER = {"5000": "CC600", "6000": "CC200", "6010": "CC400", "6020": "CC400", "6040": "CC400", "6110": "CC400",
               "6200": "CC600", "6210": "CC700", "6220": "CC700", "6230": "CC700", "6400": "CC510",
               "1550": "CC700"}  # fmt: skip
# What a supplier sells: its trade's items (vendors of one trade overlap), plus a few others.
TRADES = {
    "office": ["Copy paper 8.5x11, case of 10", "Toner cartridge HP 58X", "Shipping cartons 18x12x12"],
    "safety": ["Nitrile gloves, box of 100", "Safety glasses, clear, ANSI Z87", "Disinfectant 4 L"],
    "janitorial": ["Janitorial service - Unit 4", "Disinfectant 4 L", "Nitrile gloves, box of 100"],
    "industrial": ["Hex bolt 3/8-16 x 2 in., zinc", "Bearing 6205-2RS", "Hydraulic hose assembly 1/2 in.",
                   "Pallet wrap 18 in. x 1500 ft", "Nitrile gloves, box of 100", "Safety glasses, clear, ANSI Z87"],
    "electrical": ["LED panel 2x4 ft, 40W", "Cable Cat6 plenum, 1000 ft", "Electrician - labour"],
    "mechanical": ["Technician on site", "Monthly maintenance contract", "Calibration of pressure gauges",
                   "Hydraulic hose assembly 1/2 in."],
    "fleet": ["Diesel exhaust fluid 9.46 L", "Fleet vehicle inspection", "Forklift rental (weekly)"],
    "it": ["Software subscription - 12 users", "Consulting services - September", "Cable Cat6 plenum, 1000 ft",
           "Technician on site"],
    "print": ["Printing - brochures 8 pages", "Copy paper 8.5x11, case of 10"],
    "lab": ["Lab beakers 250 mL, pack of 12", "Nitrile gloves, box of 100", "Disinfectant 4 L",
            "Calibration of pressure gauges"],
    "packaging": ["Pallet wrap 18 in. x 1500 ft", "Shipping cartons 18x12x12"],
}  # fmt: skip
TRADE_NAMES = {"office": "Office Products", "safety": "Safety Equipment", "janitorial": "Janitorial Services",
               "industrial": "Industrial Supply", "electrical": "Electrical", "mechanical": "Mechanical",
               "fleet": "Fleet Services", "it": "IT Solutions", "print": "Printing", "lab": "Lab Supplies",
               "packaging": "Packaging"}  # fmt: skip

FRENCH = {en: fr for en, fr, *_ in C.GOODS} | {en: fr for en, fr, *_ in C.UTILITY_LINES}
PRICES = {en: (lo, hi, qdec) for en, _fr, _u, _uf, lo, hi, qdec in C.GOODS} | {
    en: (lo, hi, 0) for en, _fr, lo, hi in C.UTILITY_LINES
}
MONTHS = {"en": C.MONTHS_EN, "fr": C.MONTHS_FR}
_MONTH_WORD = re.compile(r"\b(?:" + "|".join(C.MONTHS_EN + C.MONTHS_FR) + r")\b", re.IGNORECASE)
# Words a supplier adds to its own description of an item (a size, a colour, a grade).
EXTRAS = {
    "en": ["- LG", "(blue)", "HD", "Std", "Grade A", "Premium", "XL", "Black", "Heavy duty", "Type B", "Rev. 3"],
    "fr": ["- G", "(bleu)", "Robuste", "Format standard", "Noir", "Qualité supérieure", "Type B", "TG"],
}
VOWELS = set("aeiouyàâäéèêëîïôöùûü")
VARIANT_RATE = 0.15  # an invoice line worded differently from the supplier's usual wording


# The sales tax each item carries, bought in British Columbia (GST 5%, PST 7%): goods and rentals carry both, services
# GST only (exempt from the PST), as AP approves each line's tax code. Utilities: electricity and natural gas are
# exempt from the PST, municipal water from both, telephone service carries both.
SERVICES = {
    "Electrician - labour", "Technician on site", "Consulting services - September", "Monthly maintenance contract",
    "Janitorial service - Unit 4", "Calibration of pressure gauges", "Fleet vehicle inspection",
}  # fmt: skip
UTILITY_TAX = {"hydro": "GST~PST", "gas": "GST~PST", "water": "EXEMPT", "telecom": "GST+PST"}
TAX_RATES = {"GST": 0.05, "PST": 0.07}
PRINTED_FLAGS = {"GST+PST": "G P", "GST~PST": "G", "EXEMPT": "E"}  # the tax column of a vendor that prints one
TAX_PRINTERS = 0.3  # the share of vendors that print each line's tax code
PLAUSIBLE_TAX = ["GST+PST", "GST~PST", "GST"]  # a tax code a clerk might pick by mistake
# Our address, printed on every invoice: the place of supply (BC) for the taxes.
BILL_TO = "Bill to: Bench Company Ltd.\n100 Main Street\nVancouver BC V6B 1A1"


def true_tax(item: str, kind: str) -> str:
    """The tax code AP approves on a line of ``item`` sold by a vendor of ``kind``."""
    if kind in UTILITY_TAX:
        return UTILITY_TAX[kind]
    return "GST~PST" if item in SERVICES else "GST+PST"


def invoice_taxes(lines: list[Any], codes: list[str]) -> dict[str, float]:
    """The taxes printed at the foot of an invoice whose lines carry ``codes``: each tax at its rate on the lines that
    carry it ({"gst_amount": ..., "pst_amount": ...}, a tax charged on no line left out)."""
    from ap_coder.coding.line_tax import types_of

    out = {}
    for tax, rate in TAX_RATES.items():
        base = sum(li.amount for li, code in zip(lines, codes, strict=True) if tax in types_of(code))
        if base:
            out[f"{tax.lower()}_amount"] = round(base * rate, 2)
    return out


# AP changes its mind: from halfway through, spare parts kept in stock are coded to materials.
RECODED = {"Bearing 6205-2RS": (0.5, "5000"), "Hydraulic hose assembly 1/2 in.": (0.5, "5000")}
# One item truly split between two accounts (``Realism.split``): a maintenance contract billed for more than 3 months
# ahead is a prepaid expense, a shorter one repairs. Its words alone cannot tell which.
SPLIT = {"Monthly maintenance contract": (3, "1550")}  # item -> (months up to which it is not split, the other one)


@dataclass(frozen=True)
class Realism:
    """What the default benchmark leaves out, each off unless asked for (``--realistic`` turns them all on):

    * ``mistakes``: the share of lines AP approves with a wrong account by mistake (a plausible one), right again
      on the vendor's later invoices;
    * ``history``: the share of vendors whose past coding is imported from the ERP before their first invoice
      (``Store.import_history``): each of their items one to four times, in their wording, at the account it had
      then, ``history_noise`` of them on a wrong account;
    * ``guesses``: the chat model guesses an account for every line left blank (``guess_right`` of them right), and
      the reviewer keeps it (outcome *coded*), a wrong one too ``guess_kept_wrong`` of the time;
    * ``split``: the maintenance contract goes to two accounts by its quantity (``SPLIT``).
    """

    mistakes: float = 0.0
    history: float = 0.0
    history_noise: float = 0.0
    guesses: bool = False
    guess_right: float = 0.7
    guess_kept_wrong: float = 0.25
    split: bool = False

    @property
    def any(self) -> bool:
        return bool(self.mistakes or self.history or self.guesses or self.split)


REALISTIC = Realism(mistakes=0.02, history=0.4, history_noise=0.02, guesses=True, split=True)
# The wrong account a person (or the chat model) might plausibly pick: one of the accounts the catalog's items go to.
PLAUSIBLE = sorted(set(ITEM_GL.values()) | set(UTILITY_GL.values()) | {gl for _, gl in SPLIT.values()})


def true_gl(item: str, kind: str, t: float = 0.0, quantity: float = 1.0, split: bool = False) -> str:
    """The account of ``item`` sold by a vendor of ``kind``, at time ``t`` (0 to 1) of the stream, for
    ``quantity`` of it (a split item, ``split``, goes by its quantity)."""
    if kind in UTILITY_GL:
        return UTILITY_GL[kind]
    if split and item in SPLIT and quantity > SPLIT[item][0]:
        return SPLIT[item][1]
    since, gl = RECODED.get(item, (2.0, ""))
    return gl if t >= since else ITEM_GL[item]


def plausible_wrong(rng: random.Random, truth: str) -> str:
    return rng.choice([gl for gl in PLAUSIBLE if gl != truth])


# --- the company ------------------------------------------------------------------------------------------------


@dataclass
class Vendor:
    name: str
    kind: str  # "supply" or a utility: "hydro", "gas", "water", "telecom"
    lang: str  # "en", "fr" or "bi" (an invoice in either language, sometimes both on one line)
    items: list[str]  # catalog items (English wording), most sold first
    weights: list[float]
    style: dict[str, Any]
    default_gl: str = ""  # the vendor master's default account ("" for most)
    house: dict[tuple[str, str], str] = field(default_factory=dict)  # (item, language) -> its usual wording
    sku: dict[str, str] = field(default_factory=dict)  # item -> the vendor's code for it

    def gl(self, item: str, t: float = 0.0, quantity: float = 1.0, split: bool = False) -> str:
        return true_gl(item, self.kind, t, quantity, split)


def _split(text: str) -> tuple[str, str]:
    """(the item's name, the rest): "Hex bolt" and "3/8-16 x 2 in., zinc"; "Nitrile gloves" and ", box of 100"."""
    m = re.search(r",| - |\(|\s\d", text)
    return (text, "") if m is None else (text[: m.start()].strip(), text[m.start() :].rstrip())


def _abbreviate(rng: random.Random, word: str) -> str:
    """ "Hydraulic" -> "Hydr." or "Hdrl": cut short, or its consonants (as ERP descriptions shorten words)."""
    if len(word) < 6 or not word.isalpha():
        return word
    if rng.random() < 0.65:
        return word[: rng.randint(3, min(5, len(word) - 2))] + "."
    return (word[0] + "".join(c for c in word[1:] if c.lower() not in VOWELS))[: rng.randint(3, 5)]


def _toggle_plural(word: str) -> str:
    if not word.isalpha() or word.endswith("y"):
        return word
    if word.endswith("s") and not word.endswith("ss") and len(word) > 4:
        return word[:-1]
    return word + "s"


def wording(rng: random.Random, text: str, lang: str, style: dict[str, Any], sku: str = "") -> str:
    """One supplier's way of printing a catalog item: words shortened, singular or plural, the name turned
    round ("Gloves, nitrile"), the size dropped, a size or grade added, its SKU. "{month}" stays for the month."""
    text = _MONTH_WORD.sub("{month}", text)
    name, rest = _split(text)
    words = name.split()
    words = [_abbreviate(rng, w) if rng.random() < style["abbrev"] else w for w in words]
    if words and rng.random() < 0.3:
        at = -1 if lang == "en" else 0
        words[at] = _toggle_plural(words[at])
    if lang == "en" and len(words) >= 2 and rng.random() < style["reorder"]:
        words = [words[-1] + ","] + words[:-1]
    if rest and "{month}" not in rest and rng.random() < 0.35:
        rest = ""
    out = " ".join(words) + ("" if not rest else rest if rest[0] in ", " else f" {rest}")
    if rng.random() < style["extras"]:
        out += " " + rng.choice(EXTRAS[lang])
    return style["sku"].format(text=out, sku=sku) if style["sku"] and sku else out


def _vendor_name(rng: random.Random, kind: str, trade: str, lang: str) -> str:
    if kind != "supply":
        return f"{rng.choice(C.EN_NAMES)} {rng.choice(UTILITY_NAMES[kind])}" + rng.choice([" Inc.", " Corp.", ""])
    if lang == "fr":
        return f"{rng.choice(C.FR_KINDS)} {rng.choice(C.FR_NAMES)} {rng.choice(C.FR_SUFFIX)}"
    return f"{rng.choice(C.EN_NAMES)} {TRADE_NAMES[trade]} {rng.choice(C.EN_SUFFIX)}"


def make_company(seed: int, n: int) -> list[Vendor]:
    """``n`` vendors, the same for a given seed: about one in seven a utility, the rest suppliers of one trade
    (vendors of a trade overlap) plus a few other items; about a quarter print French or both languages."""
    from ap_coder.core.memory import vendor_key

    rng = random.Random(f"ap-bench-coding:{seed}:company")
    utilities = max(1, round(n / 7)) if n >= 3 else 0
    kinds = [list(UTILITY_GL)[i % len(UTILITY_GL)] for i in range(utilities)] + ["supply"] * (n - utilities)
    rng.shuffle(kinds)
    trades = list(TRADES)
    rng.shuffle(trades)
    vendors: list[Vendor] = []
    keys: set[str] = set()
    for i, kind in enumerate(kinds):
        lang = rng.choices(["en", "fr", "bi"], [0.55, 0.25, 0.2])[0]
        trade = trades[i % len(trades)]
        name, tries = _vendor_name(rng, kind, trade, lang), 0
        while vendor_key(name) in keys:  # every vendor is a different vendor (numbered once the names run out)
            tries += 1
            name = _vendor_name(rng, kind, trade, lang) + (f" {tries}" if tries >= 50 else "")
        keys.add(vendor_key(name))
        if kind == "supply":
            items = list(TRADES[trade])
            rng.shuffle(items)
            others = [en for en in ITEM_GL if en not in items]
            items += rng.sample(others, rng.choice([0, 1, 1, 2, 3]))
            weights = [1 / (rank + 1) ** 0.8 for rank in range(len(items))]
        else:
            items = list(UTILITY_ITEMS[kind])
            weights = [1.0] * len(items)
        style = {
            "abbrev": rng.choice([0.0, 0.0, 0.25, 0.5]),
            "reorder": rng.choice([0.0, 0.0, 0.5]),
            "sku": rng.choice(["", "", "", "{sku} {text}", "{text} #{sku}", "[{sku}] {text}", "SKU {sku} - {text}"])
            if kind == "supply"
            else "",
            "upper": rng.random() < 0.3,
            "extras": 0.3 if kind == "supply" else 0.0,
        }
        v = Vendor(name, kind, lang, items, weights, style)
        share: Counter[str] = Counter()
        for item, w in zip(items, weights, strict=True):
            share[v.gl(item)] += w
        if rng.random() < (0.5 if kind != "supply" else 0.2):
            v.default_gl = share.most_common(1)[0][0]  # set up in the ERP as the account most of it goes to
        for item in items:
            v.sku[item] = f"{rng.choice('ABCDEHKMPRST')}{rng.choice('ABCDEHKMPRST')}{rng.randint(1000, 99999)}"
            for lg in ("en", "fr"):
                v.house[(item, lg)] = wording(rng, item if lg == "en" else FRENCH[item], lg, style, v.sku[item])
        vendors.append(v)
    return vendors


@dataclass
class Line:
    item: str
    text: str
    lang: str  # "en", "fr" or "bi" (both on the line)
    quantity: float
    unit_price: float
    amount: float


def invoice_lines(rng: random.Random, v: Vendor, month: int) -> list[Line]:
    if v.kind == "supply":
        n = min(len(v.items), rng.choices([1, 2, 3, 4, 5, 6], [30, 25, 20, 12, 8, 5])[0])
        picked: list[str] = []
        while len(picked) < n:
            item = rng.choices(v.items, v.weights)[0]
            if item not in picked:
                picked.append(item)
    else:
        picked = [it for it in v.items if rng.random() < 0.9] or list(v.items)
    lang = v.lang if v.lang != "bi" else rng.choices(["en", "fr", "bi"], [0.4, 0.4, 0.2])[0]
    out = []
    for item in picked:

        def text(lg: str, item: str = item) -> str:
            base = item if lg == "en" else FRENCH[item]
            fresh = rng.random() < VARIANT_RATE
            said = wording(rng, base, lg, v.style, v.sku.get(item, "")) if fresh else v.house[(item, lg)]
            return said.replace("{month}", MONTHS[lg][month - 1])

        said = f"{text('en')} / {text('fr')}" if lang == "bi" else text(lang)
        lo, hi, qdec = PRICES[item]
        qty = rng.randint(1, 32) / 2 if qdec else float(rng.choice([1, 1, 1, 2, 3, 4, 5, 6, 10, 12, 24]))
        price = round(rng.uniform(lo, hi), 2)
        out.append(Line(item, said.upper() if v.style["upper"] else said, lang, qty, price, round(qty * price, 2)))
    return out


def arrivals(seed: int, vendors: int, invoices: int) -> list[tuple[float, int, int]]:
    """(time from 0 to 1, vendor, its invoice number from 0) in the order invoices arrive: each vendor starts
    at some point in the first half and sends ``invoices`` invoices from then on."""
    rng = random.Random(f"ap-bench-coding:{seed}:arrivals")
    events = []
    for v in range(vendors):
        start = rng.uniform(0.0, 0.5)
        times = sorted(rng.uniform(start, 1.0) for _ in range(invoices))
        events += [(t, v, i) for i, t in enumerate(times)]
    return sorted(events)


# --- playing it through the app ---------------------------------------------------------------------------------


def _capture(vendor: str, number: str, on: dt.date, lines: list[Line], taxes: dict[str, float] | None = None,
             flags: list[str] | None = None) -> Any:  # fmt: skip
    """The invoice as the local reader would have read it, every value right (this measures coding, not reading):
    its ``taxes`` at the foot, and the tax column's ``flags`` when the vendor prints one."""
    from ap_coder.capture.types import VERIFIED, CaptureResult, FieldResult, LineReading

    taxes = taxes or {}
    total = round(sum(li.amount for li in lines) + sum(taxes.values()), 2)
    fields = {
        "vendor_name": FieldResult("vendor_name", vendor, 0.99, VERIFIED),
        "invoice_number": FieldResult("invoice_number", number, 0.99, VERIFIED),
        "invoice_date": FieldResult("invoice_date", on.isoformat(), 0.99, VERIFIED),
        "currency": FieldResult("currency", "CAD", 0.99, VERIFIED),
        "grand_total": FieldResult("grand_total", total, 0.99, VERIFIED),
        **{name: FieldResult(name, amount, 0.99, VERIFIED) for name, amount in taxes.items()},
    }
    flags = flags or [""] * len(lines)
    readings = [LineReading(li.text, li.quantity, li.unit_price, li.amount, tax_flags=flag)
                for li, flag in zip(lines, flags, strict=True)]  # fmt: skip
    return CaptureResult(fields=fields, line_items=readings)


def tax_sure(line: Any) -> bool:
    """Whether a line's tax code is proposed for sure: a code (not "NONE"), neither worked out from several possible
    sets of lines nor an inferred exemption waiting for a person to confirm it."""
    from ap_coder.coding.line_tax import CODE_NONE, exemption_to_confirm, tax_code_of, tax_guessed

    return tax_code_of(line) != CODE_NONE and not tax_guessed(line) and not exemption_to_confirm(line)


def _source(suggestion: Any) -> str:
    """Where a suggestion's account came from: this vendor's history, other vendors', the account's own words, the
    vendor master (or "")."""
    if suggestion is None:
        return ""
    if getattr(suggestion, "source", ""):
        return suggestion.source
    first = " ".join(suggestion.reasons[:1])
    for words, source in (("from this vendor", "vendor"), ("other vendor", "others"),
                          ("account description", "words"), ("vendor master", "master")):  # fmt: skip
        if words in first:
            return source
    return "other"


def _history_rows(seed: int, company: list[Vendor], realism: Realism) -> dict[int, list[dict[str, Any]]]:
    """{vendor: its past coding as the ERP exports it}, for ``realism.history`` of the vendors: each item one to four
    times, in the vendor's wording, at the account it had before the period."""
    rng = random.Random(f"ap-bench-coding:{seed}:history")
    out: dict[int, list[dict[str, Any]]] = {}
    for vi, v in enumerate(company):
        if rng.random() >= realism.history:
            continue
        rows = []
        for item in v.items:
            for _ in range(rng.randint(1, 4)):
                lg = v.lang if v.lang != "bi" else rng.choice(["en", "fr"])
                month = rng.randint(1, 12)
                text = v.house[(item, lg)].replace("{month}", MONTHS[lg][month - 1])
                quantity = float(rng.choice([1, 1, 2, 6, 12]))
                gl = v.gl(item, 0.0, quantity, realism.split)
                if rng.random() < realism.history_noise:
                    gl = plausible_wrong(rng, gl)
                rows.append({"vendor_name": v.name, "description": text.upper() if v.style["upper"] else text,
                             "gl_code": gl, "cost_center": COST_CENTER[gl], "amount": 100.0,
                             "date": f"2024-{month:02d}-15"})  # fmt: skip
        out[vi] = rows
    return out


def simulate(
    vendors: int = 30, invoices: int = 30, seed: int = 1, realism: Realism | None = None
) -> list[dict[str, Any]]:
    """Every line of every invoice, in the order they arrived, with what was proposed and the truth. ``realism``:
    what the default run leaves out (reviewer mistakes, ERP history, kept chat-model guesses, a split account)."""
    from ap_coder.ai import lessons
    from ap_coder.ai.inference import MODEL_SUGGESTED
    from ap_coder.ai.offline_coder import code_from_capture
    from ap_coder.ai.suggest import suggest_gl
    from ap_coder.coding import rules
    from ap_coder.coding.line_tax import apply_tax_code, confirm_line, tax_code_of, tax_learned
    from ap_coder.coding.validation import coding_confidence, line_coding_confidence
    from ap_coder.core.memory import _normalise, compare_with_history
    from ap_coder.core.reference_data import UNASSIGNED, load_reference_data
    from ap_coder.core.store import Store
    from ap_coder.web.invoice_view import suggestions

    realism = realism or Realism()
    reference = load_reference_data(
        DATA / "chart_of_accounts.csv", DATA / "cost_centers.csv", DATA / "tax_gl_mapping.csv",
        DATA / "coding_policy.md",
    )  # fmt: skip
    company = make_company(seed, vendors)
    seen_items: dict[int, set[str]] = defaultdict(set)  # vendor -> items AP approved for it
    seen_texts: dict[int, set[str]] = defaultdict(set)  # vendor -> line wordings AP approved for it
    sold_by: dict[str, set[int]] = defaultdict(set)  # item -> vendors AP approved it for
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="ap-coding-") as tmp:
        store = Store(Path(tmp) / "coding.db")
        store.import_vendor_master(
            [{"vendor_name": v.name, "default_gl": v.default_gl, "status": "active"} for v in company]
        )
        if realism.history:
            history = _history_rows(seed, company, realism)
            store.import_history([r for vi in sorted(history) for r in history[vi]])
            for vi, past in history.items():
                for r in past:
                    seen_texts[vi].add(_normalise(r["description"]))
                for item in company[vi].items:
                    seen_items[vi].add(item)
                    sold_by[item].add(vi)
        for n, (t, vi, position) in enumerate(arrivals(seed, vendors, invoices)):
            v = company[vi]
            rng = random.Random(f"ap-bench-coding:{seed}:{vi}:{position}")
            act = random.Random(f"ap-bench-coding:{seed}:{vi}:{position}:reviewer")  # the realism's own draws
            on = dt.date(2025, 1, 6) + dt.timedelta(days=int(t * 540))
            lines = invoice_lines(rng, v, on.month)
            number = f"INV-{vi:03d}-{position + 1:04d}"
            truths = [v.gl(line.item, t, line.quantity, realism.split) for line in lines]
            tax_truths = [true_tax(line.item, v.kind) for line in lines]
            printer = random.Random(f"ap-bench-coding:{seed}:{vi}:tax-column").random() < TAX_PRINTERS
            flags = [PRINTED_FLAGS.get(code, "") for code in tax_truths] if printer else None
            # The lessons the AI and the review screen are shown for these lines (``lessons.prompt_lines``).
            hints = []
            for line in lines:
                lesson = store.lesson(lessons.situation(v.name, lessons.INVOICE, lessons.LINE_GL,
                                                        lessons.line_pattern(line.text)))  # fmt: skip
                hints.append(
                    (
                        lesson["value"] if lesson and lessons.on_screen(lesson) else "",
                        lesson["value"] if lesson and lessons.reaches_ai(lesson) else "",
                    )
                )

            # 1. What the app proposes with no AI model (pipeline.process: code from capture, then the rules).
            feedback = store.feedback_rows()
            captured = _capture(v.name, number, on, lines, invoice_taxes(lines, tax_truths), flags)
            coding = code_from_capture(captured, reference, feedback, store, text=BILL_TO).coding
            tax_proposed = [(tax_code_of(li), tax_sure(li), tax_learned(li))
                            for li in coding.line_items]  # fmt: skip
            coding, rule_changes = rules.apply(coding, store.coding_rules())
            shown = {s["position"]: [o["gl"] for o in s["options"]] for s in suggestions(store, reference, coding)}
            proposals = [li.predicted_gl_code for li in coding.line_items]

            # The chat model's guess for each line left blank (``pipeline._local_accounts``), marked as a guess.
            if realism.guesses:
                for li, truth in zip(coding.line_items, truths, strict=True):
                    if li.predicted_gl_code == UNASSIGNED:
                        li.predicted_gl_code = truth if act.random() < realism.guess_right else plausible_wrong(
                            act, truth
                        )  # fmt: skip
                        li.predicted_cost_center = COST_CENTER[li.predicted_gl_code]
                        li.reasoning_justification = f"{MODEL_SUGGESTED}: a line like the ones it was shown"
            # The Coding the review screen shows for these lines, each line's share of it (D-89).
            coded = coding_confidence(coding, reference)
            taxed = any(t.tax_amount for t in coding.tax_lines)
            sure = [line_coding_confidence(li, reference.cost_centers is not None, taxed, reference.chart_of_accounts,
                                           reference.cost_centers, reference.tax.tax_gl_codes())
                    for li in coding.line_items]  # fmt: skip
            # The HISTORY_CONFLICT check on the lines as the reviewer sees them (``pipeline.finalise_coding``).
            warned = {h["line_number"] for h in compare_with_history(coding, feedback) if h["status"] == "conflict"}

            # 2. AP approves the true account of every line: a guess that was wrong is sometimes kept, and a few
            # lines get a wrong account by mistake (right again on a later invoice).
            output = coding.model_dump(mode="json")
            invoice_id = store.add_invoice(Path(tmp) / f"{number}.pdf", output, None,
                                           meta={"rules_applied": rule_changes})  # fmt: skip
            final = json.loads(json.dumps(output))
            approved = []
            for li, ai, truth in zip(final["line_items"], coding.line_items, truths, strict=True):
                gl = truth
                guess = realism.guesses and ai.reasoning_justification.startswith(MODEL_SUGGESTED)
                if guess and ai.predicted_gl_code != truth and act.random() < realism.guess_kept_wrong:
                    gl = ai.predicted_gl_code
                elif realism.mistakes and act.random() < realism.mistakes:
                    gl = plausible_wrong(act, truth)
                li["predicted_gl_code"] = gl
                li["predicted_cost_center"] = COST_CENTER[gl]
                approved.append(gl)
            # Each line's tax code as AP approves it (picked or confirmed on the review screen); a few by mistake.
            taxr = random.Random(f"ap-bench-coding:{seed}:{vi}:{position}:tax")
            approved_tax = []
            for li, code in zip(final["line_items"], tax_truths, strict=True):
                if realism.mistakes and taxr.random() < realism.mistakes:
                    code = taxr.choice([c for c in PLAUSIBLE_TAX if c != code])
                apply_tax_code(li, code)
                li["reasoning_justification"] = confirm_line(li["reasoning_justification"], code,
                                                             li["reasoning_justification"])  # fmt: skip
                approved_tax.append(code)

            for pos, (li, line) in enumerate(zip(coding.line_items, lines, strict=True)):
                truth = truths[pos]
                ranked = suggest_gl(line.text, v.name, feedback, reference, limit=3, default_gl=v.default_gl)
                top = ranked[0] if ranked else None
                norm = _normalise(line.text)
                seen = (
                    "vendor, same words" if norm in seen_texts[vi]
                    else "vendor, other words" if line.item in seen_items[vi]
                    else "other vendors only" if sold_by[line.item] - {vi}
                    else "never"
                )  # fmt: skip
                proposed = proposals[pos]
                row = {
                    "invoice": n + 1,
                    "vendor": v.name,
                    "vendor_kind": v.kind,
                    "position": position + 1,
                    "line": pos + 1,
                    "text": line.text,
                    "item": line.item,
                    "lang": line.lang,
                    "truth": truth,
                    "proposed": proposed,
                    "assigned": proposed != UNASSIGNED,
                    "correct": proposed == truth,
                    "source": _source(top) if proposed != UNASSIGNED else "",
                    "top": [s.gl_code for s in ranked],
                    "top_score": round(top.score, 4) if top else 0.0,
                    "top_source": _source(top),
                    "top_auto": bool(getattr(top, "auto", True)) if top else False,
                    "shown": shown.get(pos, []),
                    "seen": seen,
                    "default_gl": bool(v.default_gl),
                    "recoded": line.item in RECODED and t >= RECODED[line.item][0],
                    "in_line": li.predicted_gl_code,  # what the reviewer saw: the proposal, or the chat model's guess
                    "warned": li.line_number in warned,  # HISTORY_CONFLICT on the line
                    "tax_truth": tax_truths[pos],
                    "tax_proposed": tax_proposed[pos][0],
                    "tax_sure": tax_proposed[pos][1],
                    "tax_learned": tax_proposed[pos][2],
                    "tax_printed": printer,
                    "lesson": hints[pos][1],  # the lesson's account the AI reads for the line ("" when none)
                    "lesson_screen": hints[pos][0],  # the lesson's account the reviewer sees as a hint
                    "coding": coded,  # the invoice's Coding (D-89)
                    "line_sure": sure[pos],
                    # the line's account, cost center and tax code all right, as the reviewer saw them
                    "line_right": li.predicted_gl_code == truth
                    and tax_proposed[pos][0] == tax_truths[pos]
                    and (reference.cost_centers is None or li.predicted_cost_center == COST_CENTER[truth]),
                }
                if realism.any:
                    row["approved"] = approved[pos]
                    row["approved_tax"] = approved_tax[pos]
                    row["split"] = line.item in SPLIT and realism.split
                rows.append(row)
            store.approve_invoice(invoice_id, final, REVIEWER)
            # AP approves each invoice the day it arrives: a tax code's votes need different days (D-80).
            with store._conn() as conn:
                conn.execute("UPDATE feedback SET created_at = ? WHERE invoice_id = ?",
                             (f"{on.isoformat()}T12:00:00", invoice_id))  # fmt: skip
            lessons.record(store, invoice_id, output, final, REVIEWER)  # as workflow.learn_from_approval does
            for line in lines:
                seen_items[vi].add(line.item)
                seen_texts[vi].add(_normalise(line.text))
                sold_by[line.item].add(vi)
    return rows


# --- the report -------------------------------------------------------------------------------------------------


def _ratio(a: float, b: float) -> float | None:
    return round(a / b, 4) if b else None


def line_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    assigned = [r for r in rows if r["assigned"]]
    left = [r for r in rows if not r["assigned"]]
    correct = sum(r["correct"] for r in rows)
    return {
        "lines": len(rows),
        "assigned": len(assigned),
        "correct": correct,
        "accuracy": _ratio(correct, len(rows)),
        "precision": _ratio(correct, len(assigned)),
        "coverage": _ratio(len(assigned), len(rows)),
        "wrong": len(assigned) - correct,
        # Lines left for AP: the right account is one of the suggestions the review screen shows (one click).
        "left_with_right_suggestion": _ratio(sum(r["truth"] in r["shown"] for r in left), len(left)),
        # The best guess, assigned or not: what taking the top suggestion every time would score.
        "top_suggestion_right": _ratio(sum(bool(r["top"]) and r["top"][0] == r["truth"] for r in rows), len(rows)),
    }


def _split_by(rows: list[dict[str, Any]], key: Any, order: list[str] | None = None) -> dict[str, Any]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        groups[key(r)].append(r)
    names = [k for k in order if k in groups] if order else sorted(groups)
    return {k: line_stats(groups[k]) for k in names}


POSITION_BUCKETS = ((1, 1), (2, 5), (6, 10), (11, 20), (21, 10**9))
SEEN_ORDER = ["vendor, same words", "vendor, other words", "other vendors only", "never"]


def _bucket(position: int) -> str:
    for lo, hi in POSITION_BUCKETS:
        if lo <= position <= hi:
            return str(lo) if lo == hi else f"{lo}+" if hi >= 10**9 else f"{lo}-{hi}"
    return "?"


def history_warnings(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The HISTORY_CONFLICT warning on the lines that had an account (the proposal or the chat model's guess): how
    often it was a false alarm (the account was right), and how many of the wrong accounts it caught."""
    shown = [r for r in rows if r.get("in_line", r["proposed"]) != "UNASSIGNED"]
    right = [r for r in shown if r.get("in_line", r["proposed"]) == r["truth"]]
    wrong = [r for r in shown if r.get("in_line", r["proposed"]) != r["truth"]]
    after_recode = [r for r in right if r["recoded"]]
    return {
        "lines": len(shown),
        "false_alarms": sum(r.get("warned", False) for r in right),
        "false_alarms_after_recode": sum(r.get("warned", False) for r in after_recode),
        "wrong": len(wrong),
        "caught": sum(r.get("warned", False) for r in wrong),
    }


def tax_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Line tax codes: *precision*, the codes proposed for sure (``tax_sure``) that were right; *coverage*, the share
    of lines with a code proposed for sure (the others wait for a person: worked out from several possible sets of
    lines, an exemption to confirm, or nothing said)."""
    sure = [r for r in rows if r.get("tax_sure")]
    right = sum(r["tax_proposed"] == r["tax_truth"] for r in sure)
    return {
        "lines": len(rows),
        "sure": len(sure),
        "correct": right,
        "precision": _ratio(right, len(sure)),
        "coverage": _ratio(len(sure), len(rows)),
        "accuracy": _ratio(right, len(rows)),
        "wrong": len(sure) - right,
        "learned": sum(bool(r.get("tax_learned")) for r in rows),
        "any_right": _ratio(sum(r["tax_proposed"] == r["tax_truth"] for r in rows), len(rows)),
    }


def lesson_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The line lessons the AI reads (``lessons.reaches_ai``, "shown") and the reviewer's hints
    (``lessons.on_screen``, "screen"): how many lines had one, how many said an account other than the line's true
    one (noise, from a clerk's slip)."""
    shown = [r for r in rows if r.get("lesson")]
    wrong = sum(r["lesson"] != r["truth"] for r in shown)
    screen = [r for r in rows if r.get("lesson_screen")]
    screen_wrong = sum(r["lesson_screen"] != r["truth"] for r in screen)
    return {"lines": len(rows), "shown": len(shown), "wrong": wrong, "noise": _ratio(wrong, len(shown)),
            "screen": len(screen), "screen_wrong": screen_wrong}  # fmt: skip


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    wrong = Counter((r["truth"], r["proposed"], r["source"]) for r in rows if r["assigned"] and not r["correct"])
    return {
        "overall": line_stats(rows),
        "first_or_later": _split_by(
            rows,
            lambda r: "first invoice" if r["position"] == 1 else "later invoices",
            ["first invoice", "later invoices"],
        ),  # fmt: skip
        "by_position": _split_by(
            rows, lambda r: _bucket(r["position"]), [_bucket(lo) for lo, _ in POSITION_BUCKETS]
        ),  # fmt: skip
        "by_seen": _split_by(rows, lambda r: r["seen"], SEEN_ORDER),
        "by_language": _split_by(rows, lambda r: r["lang"], ["en", "fr", "bi"]),
        "by_vendor_kind": _split_by(
            rows, lambda r: "utility" if r["vendor_kind"] != "supply" else "supplier", ["supplier", "utility"]
        ),  # fmt: skip
        "by_default_gl": _split_by(
            rows, lambda r: "default GL" if r["default_gl"] else "no default GL", ["default GL", "no default GL"]
        ),  # fmt: skip
        "by_source": {
            s or "-": {**line_stats([r for r in rows if r["source"] == s])}
            for s in sorted({r["source"] for r in rows if r["assigned"]})
        },
        "recoded": line_stats([r for r in rows if r["recoded"]]),
        "split": line_stats([r for r in rows if r.get("split")]),
        "history_warnings": history_warnings(rows),
        "tax_codes": tax_stats(rows),
        "tax_codes_by_column": _tax_split(rows),
        "lessons": lesson_stats(rows),
        "top_errors": [{"truth": t, "proposed": p, "source": s, "lines": n} for (t, p, s), n in wrong.most_common(12)],
        "calibration": coding_calibration(rows),
    }


def coding_calibration(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Is the Coding honest (D-89)? Per vendor-invoice position (1, 2-5, 6-20, 21+), the mean Coding of the invoices
    against the share of their lines whose account, cost center and tax code were all right as proposed."""
    from ap_coder.bench.learning import calibration

    invoices: dict[int, dict[str, Any]] = {}
    for r in rows:
        if "coding" not in r:
            continue
        inv = invoices.setdefault(r["invoice"], {"position": r["position"], "coding": r["coding"], "lines": 0,
                                                 "right": 0})  # fmt: skip
        inv["lines"] += 1
        inv["right"] += bool(r["line_right"])
    per = [{**i, "share": i["right"] / i["lines"]} for i in invoices.values()]
    return calibration(per, "coding", "share", position="position", weight="lines")


def _tax_split(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups = {"tax column printed": [r for r in rows if r.get("tax_printed")],
              "no tax column": [r for r in rows if not r.get("tax_printed")]}  # fmt: skip
    return {k: tax_stats(v) for k, v in groups.items() if v}


def _stats_table(split: dict[str, Any], label: str) -> str:
    return _table(
        [label, "lines", "accuracy", "precision", "coverage", "wrong", "left: right one suggested"],
        [
            [k, m["lines"], _pct(m["accuracy"]), _pct(m["precision"]), _pct(m["coverage"]), m["wrong"],
             _pct(m["left_with_right_suggestion"])]
            for k, m in split.items()
        ],
    )  # fmt: skip


def render_markdown(report: dict[str, Any]) -> str:
    cfg, s = report["config"], report["summary"]
    o = s["overall"]
    out = [
        "# GL coding without an AI model\n",
        f"{cfg['vendors']} vendors x {cfg['invoices']} invoices (seed {cfg['seed']}): {o['lines']} lines, coded as "
        "they arrive from what AP approved before (no chat model, no Azure), then approved with the true account.\n",
        *([f"Realistic run: {cfg['realism']}\n"] if cfg.get("realism") else []),
        f"- **Accuracy** (right account proposed, of all lines): **{_pct(o['accuracy'])}**",
        f"- **Precision** (proposed accounts that were right): **{_pct(o['precision'])}** ({o['wrong']} wrong)",
        f"- **Coverage** (lines given an account, not left UNASSIGNED): **{_pct(o['coverage'])}**",
        f"- Lines left for AP whose right account is among the review screen's suggestions: "
        f"{_pct(o['left_with_right_suggestion'])}",
        f"- The top suggestion, assigned or not: right on {_pct(o['top_suggestion_right'])} of lines\n",
        "## First invoice from a vendor, and later ones\n",
        _stats_table(s["first_or_later"], "invoice"),
        "## By the vendor's invoice number\n",
        _stats_table(s["by_position"], "invoice #"),
        "## Has AP coded this item before?\n",
        "*Vendor, same words*: this vendor, worded the same (case and punctuation aside); *other words*: this vendor, "
        "worded differently; *other vendors only*: approved for other vendors, never this one; *never*: a new item.\n",
        _stats_table(s["by_seen"], "seen before"),
        "## Language of the line\n",
        _stats_table(s["by_language"], "language"),
        "## Vendor\n",
        _stats_table(s["by_vendor_kind"], "vendor"),
        _stats_table(s["by_default_gl"], "vendor master"),
        "## Where the proposed account came from\n",
        "*vendor*: this vendor's similar lines; *others*: other vendors' similar lines; *usual*: the account all of "
        "this vendor's lines went to; *master*: the vendor master's default; *words*: words of the account's name.\n",
        _table(
            ["source", "lines", "precision", "wrong"],
            [[k, m["lines"], _pct(m["precision"]), m["wrong"]] for k, m in s["by_source"].items()],
        ),
    ]
    rc = s["recoded"]
    if rc["lines"]:
        out.append(
            f"AP recoded spare parts (bearings, hose assemblies) to materials halfway through: on the {rc['lines']} "
            f"such lines after the change, accuracy {_pct(rc['accuracy'])}, precision {_pct(rc['precision'])} "
            f"({rc['wrong']} wrong).\n"
        )
    sp = s.get("split") or {}
    if sp.get("lines"):
        out.append(
            f"The maintenance contract goes to prepaid expenses when billed for more than 3 months, else repairs: on "
            f"its {sp['lines']} lines, accuracy {_pct(sp['accuracy'])}, precision {_pct(sp['precision'])} "
            f"({sp['wrong']} wrong).\n"
        )
    hw = s.get("history_warnings") or {}
    if hw.get("lines"):
        out.append(
            f"The *reviewers coded similar lines ... to GL* warning (HISTORY_CONFLICT), on the {hw['lines']} lines "
            f"that had an account: {hw['false_alarms']} false alarms on a right account "
            f"({hw['false_alarms_after_recode']} of them after AP recoded spare parts); it caught {hw['caught']} "
            f"of the {hw['wrong']} wrong accounts.\n"
        )
    tc = s.get("tax_codes") or {}
    if tc.get("lines"):
        out.append("## Line tax codes\n")
        out.append(
            "Each line's tax code (GST and PST in British Columbia: goods carry both, services GST only), as proposed "
            "when the invoice is read: *sure* when it is neither worked out from several possible sets of lines nor an "
            "exemption waiting for a person to confirm it.\n"
        )
        out.append(_table(
            ["lines", "precision", "coverage", "accuracy", "wrong", "learned from past approvals"],
            [[tc["lines"], _pct(tc["precision"]), _pct(tc["coverage"]), _pct(tc["accuracy"]), tc["wrong"],
              tc["learned"]]],
        ))  # fmt: skip
        out.append(_table(
            ["invoices", "lines", "precision", "coverage", "accuracy", "wrong"],
            [[k, m["lines"], _pct(m["precision"]), _pct(m["coverage"]), _pct(m["accuracy"]), m["wrong"]]
             for k, m in (s.get("tax_codes_by_column") or {}).items()],
        ))  # fmt: skip
    ls = s.get("lessons") or {}
    if ls.get("lines"):
        out.append(
            f"Line lessons the AI reads: on {ls['shown']} of {ls['lines']} lines, {ls['wrong']} of them for another "
            f"account than the line's true one (noise {_pct(ls['noise'])}). Hints on the review screen (from the "
            f"first correction): on {ls['screen']} lines, {ls['screen_wrong']} wrong.\n"
        )
    if s["top_errors"]:
        out.append("## Most frequent wrong accounts\n")
        out.append(_table(["true", "proposed", "source", "lines"],
                          [[e["truth"], e["proposed"], e["source"], e["lines"]] for e in s["top_errors"]]))  # fmt: skip
    if s.get("calibration"):
        from ap_coder.bench.learning import calibration_table

        out.append("## Calibration of the Coding\n")
        out.append(
            "The Coding the review screen shows (before its checks take anything off: D-89), the mean over the "
            "invoices of each position in their vendor's stream, against the share of their lines whose "
            "account, cost center and tax code were all right as proposed (a line left to code is not).\n"
        )
        out.append(calibration_table(s["calibration"], "mean Coding", "lines right"))
    t = report["timing"]
    out.append(f"\nTime: {t['seconds']} s ({t['ms_per_line']} ms per line).\n")
    return "\n".join(out)


def run_coding(
    vendors: int = 30,
    invoices: int = 30,
    seed: int = 1,
    out: str | Path = "bench_coding",
    realism: Realism | None = None,
) -> dict[str, Any]:
    """Simulate the company, then write report.json, report.md and lines.jsonl into ``out``."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    rows = simulate(vendors, invoices, seed, realism)
    seconds = time.perf_counter() - t0
    config: dict[str, Any] = {"vendors": vendors, "invoices": invoices, "seed": seed}
    if realism is not None and realism.any:
        config["realism"] = {k: v for k, v in vars(realism).items() if v}
    report = {
        "config": config,
        "summary": summarize(rows),
        "timing": {"seconds": round(seconds, 1), "ms_per_line": round(1000 * seconds / max(1, len(rows)), 1)},
    }
    (out / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "report.md").write_text(render_markdown(report), encoding="utf-8")
    with (out / "lines.jsonl").open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return report
