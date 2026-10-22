"""Which sales tax each invoice line carries: the line's tax code (decision D-64).

A line's taxes are kept as they always were, ``LineItem.taxes_applied`` (GST, HST, PST, QST, OTHER), so old invoices,
every reader and every check keep working. A line with no tax also says why, when known: ``LineItem.tax_status`` is
"exempt", "zero_rated" or "out_of_scope" (only with no taxes). The review screen shows the two as one choice, the
line's **tax code**:

* "GST+PST", "GST", "PST", "HST", "GST+QST", "QST", "OTHER", "GST+OTHER"... the taxes charged on the line;
* "EXEMPT" (an exempt supply: no tax, no input tax credit), "ZERO" (zero-rated: taxable at 0%), "OUT" (out of scope:
  not a supply, e.g. a deposit or a government fee);
* "NONE": no tax charged on the line, and nothing said why (a taxable line the vendor did not tax: PST may have to be
  self-assessed).

``options`` lists the codes that make sense where the invoice is supplied (the place of supply, D-61); ``prefill``
works out each line's code when an invoice is read: from the codes printed beside the lines ("G", "GP", "G R", "E",
"*"), read through the invoice's own legend when it prints one ("Tax codes: G = GST 5%, R = Manitoba RST 7%"), else
by finding which lines make each tax charged add up (an exact subset sum of the line amounts, in cents).
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from .tax import CANADIAN_TAX_TYPES, REGIME, TAX_TYPES

EXEMPT, ZERO_RATED, OUT_OF_SCOPE = "exempt", "zero_rated", "out_of_scope"
TAX_STATUSES = ("", EXEMPT, ZERO_RATED, OUT_OF_SCOPE)
CODE_EXEMPT, CODE_ZERO, CODE_OUT, CODE_NONE = "EXEMPT", "ZERO", "OUT", "NONE"
STATUS_CODES = {EXEMPT: CODE_EXEMPT, ZERO_RATED: CODE_ZERO, OUT_OF_SCOPE: CODE_OUT}
CODE_STATUSES = {code: status for status, code in STATUS_CODES.items()}
NO_TAX_CODES = (CODE_EXEMPT, CODE_ZERO, CODE_OUT, CODE_NONE)
# Lines that carry no tax because they say so (exempt, zero-rated, out of scope): never in a self-assessment base.
STATED = frozenset(STATUS_CODES)

# A line's code worked out from the tax charged when several sets of lines would make it add up: marked on the review
# screen for a person to look at (``tax_guessed``), like an account no approval backs (B-463).
GUESS_NOTE = "Tax code worked out from the tax charged: check it."

PST_NAMES = {"MB": "RST"}  # Manitoba calls its PST the retail sales tax


# --- The code of a line ---------------------------------------------------------------------------------------------


def _get(line: Any, name: str, default: Any = None) -> Any:
    return line.get(name, default) if isinstance(line, dict) else getattr(line, name, default)


def ordered(taxes: Iterable[str]) -> list[str]:
    """Tax types once each, in the usual order (GST, HST, PST, QST, OTHER); anything else dropped."""
    got = {str(t).strip().upper() for t in taxes or []}
    return [t for t in TAX_TYPES if t in got]


def code_for(taxes: Iterable[str], status: str = "") -> str:
    """The tax code of (taxes, status): "GST+PST", "HST", "EXEMPT", "ZERO", "OUT" or "NONE"."""
    types = ordered(taxes)
    if types:
        return "+".join(types)
    return STATUS_CODES.get(str(status or "").strip().lower(), CODE_NONE)


def tax_code_of(line: Any) -> str:
    """A line's tax code (a ``LineItem`` or a line as a dict)."""
    return code_for(_get(line, "taxes_applied") or [], _get(line, "tax_status") or "")


def parse_code(code: str) -> tuple[list[str], str] | None:
    """(taxes_applied, tax_status) of a tax code; None when it is not one ("GST+FOO", "")."""
    text = str(code or "").strip().upper().replace(" ", "")
    if text in CODE_STATUSES:
        return [], CODE_STATUSES[text]
    if text == CODE_NONE:
        return [], ""
    parts = [p for p in re.split(r"[+/,]", text) if p]
    if not parts or any(p not in TAX_TYPES for p in parts):
        return None
    return ordered(parts), ""


def apply_tax_code(line: Any, code: str) -> Any:
    """``line`` with the taxes and status of ``code`` (a dict is changed in place, a ``LineItem`` copied). An unknown
    code changes nothing."""
    parsed = parse_code(code)
    if parsed is None:
        return line
    taxes, status = parsed
    if isinstance(line, dict):
        line["taxes_applied"], line["tax_status"] = taxes, status
        return line
    return line.model_copy(update={"taxes_applied": taxes, "tax_status": status})


def types_of(code: str) -> list[str]:
    parsed = parse_code(code)
    return parsed[0] if parsed else []


# --- Words for the screen -------------------------------------------------------------------------------------------


def tax_name(tax_type: str, province: str = "") -> str:
    """A tax as the clerk knows it there: PST is RST in Manitoba; OTHER is a foreign tax."""
    if tax_type == "PST":
        return PST_NAMES.get(province, "PST")
    if tax_type == "OTHER":
        return "Foreign tax"
    return tax_type


def code_label(code: str, province: str = "") -> str:
    """ "GST + RST", "GST only", "HST", "Exempt", "No tax charged"... for the line's dropdown."""
    fixed = {CODE_EXEMPT: "Exempt", CODE_ZERO: "Zero-rated", CODE_OUT: "No tax (out of scope)",
             CODE_NONE: "No tax charged"}  # fmt: skip
    if code in fixed:
        return fixed[code]
    types = types_of(code)
    if not types:
        return code
    names = [tax_name(t, province) for t in types]
    if len(names) == 1:
        # "GST only" where it is one of the province's two taxes, so it reads as a choice; "HST", "GST" alone where
        # it is the province's only tax, and a tax the province does not use ("Foreign tax") by its name.
        usual = REGIME.get(province, frozenset())
        choice = (types[0] in usual and len(usual) > 1) or (types[0] == "GST" and usual == {"HST"})
        return f"{names[0]} only" if choice else names[0]
    return " + ".join(names)


def options(province: str, charged: Iterable[str] = (), current: Iterable[str] = ()) -> list[tuple[str, str]]:
    """The (code, label) choices of a line's dropdown for an invoice supplied in ``province``, in order.

    * HST provinces: HST; BC, SK, MB: GST + PST (RST in Manitoba), GST only, PST only; Quebec: GST + QST, GST only,
      QST only; Alberta and the territories: GST.
    * GST only too when GST is charged in an HST province: a vendor that is not registered for the HST of the
      province (or that wrongly charges GST) prints GST alone, and the line must be able to say what was charged.
      The tax check then flags the regime (TAX_REGIME_MISMATCH).
    * Any tax charged that the province does not use (HST charged in BC) as a choice of its own, so the line can
      carry what the invoice charges; "Foreign tax" (and GST + foreign tax) when a non-Canadian tax is charged.
    * Always: Exempt, Zero-rated, No tax (out of scope), No tax charged.
    * ``current``: each line's own code, kept even when it is unusual for the province.
    Unknown province: every Canadian combination (the place of supply decides, and it is not known)."""
    charged = set(ordered(charged))
    usual = REGIME.get(province)
    codes: list[str] = []

    def add(*types: str) -> None:
        code = "+".join(ordered(types))
        if code and code not in codes:
            codes.append(code)

    if usual is None:
        for combo in (("HST",), ("GST", "PST"), ("GST", "QST"), ("GST",), ("PST",), ("QST",)):
            add(*combo)
    elif usual == {"HST"}:
        add("HST")
        if "GST" in charged:
            add("GST")
    elif "PST" in usual:
        add("GST", "PST"), add("GST"), add("PST")
    elif "QST" in usual:
        add("GST", "QST"), add("GST"), add("QST")
    else:
        add("GST")
    for t in ordered(charged - set(usual or ()) - {"OTHER"}):
        add(t)
    if "OTHER" in charged:
        add("OTHER")
        if charged & {"GST", "HST"}:
            add(*(charged & {"GST", "HST"}), "OTHER")
    codes += [CODE_EXEMPT, CODE_ZERO, CODE_OUT, CODE_NONE]
    for code in current:
        code = code_for(*parse_code(code)) if parse_code(code) else ""
        if code and code not in codes:
            codes.append(code)
    return [(c, code_label(c, province)) for c in codes]


EXPORT_WORDS = {CODE_EXEMPT: "Exempt", CODE_ZERO: "Zero-rated", CODE_OUT: "Out of scope", CODE_NONE: "No tax"}


def export_code(line: Any) -> str:
    """A line's tax code as an export file says it: "GST+PST", "HST", "Exempt", "Zero-rated", "Out of scope",
    "No tax"."""
    code = tax_code_of(line)
    return EXPORT_WORDS.get(code, code)


def all_labels(province: str = "") -> dict[str, str]:
    """A label for every code there is (every combination of taxes and the no-tax codes), for a line's own unusual
    code the dropdown keeps."""
    out = {}
    for n in range(1, len(TAX_TYPES) + 1):
        for combo in itertools.combinations(TAX_TYPES, n):
            code = "+".join(combo)
            out[code] = code_label(code, province)
    for code in NO_TAX_CODES:
        out[code] = code_label(code, province)
    return out


# --- Codes printed beside the lines ----------------------------------------------------------------------------------

TAXABLE = "TAXABLE"  # "*", "T", "Y": the line is taxable, with the taxes charged
_LEGEND_ENTRY = re.compile(r"(?<![A-Za-z0-9])([A-Z]{1,2}|\*)\s*(?:=|:|-|–)\s*([^,;=\n]{2,48})")
_WORDS = (
    (re.compile(r"(?i)\b(?:gst|tps)\b"), "GST"),
    (re.compile(r"(?i)\b(?:hst|tvh)\b"), "HST"),
    (re.compile(r"(?i)\b(?:pst|rst|tvp)\b|\bretail\s+sales\s+tax\b|\bprovincial\s+(?:sales\s+)?tax\b"), "PST"),
    (re.compile(r"(?i)\b(?:qst|tvq)\b"), "QST"),
)
_EXEMPT_WORDS = re.compile(r"(?i)\b(?:exempt|exon[ée]r[ée]e?|non[- ]?taxable|not\s+taxable|tax[- ]?free)\b")
_ZERO_WORDS = re.compile(r"(?i)\bzero[- ]?rated?\b|\bdétaxée?\b|\bdetaxee?\b|\b0\s*%")
_OUT_WORDS = re.compile(r"(?i)\bout\s+of\s+scope\b|\bnot\s+applicable\b|\bhors\s+champ\b")
_TAXABLE_WORDS = re.compile(r"(?i)^(?:taxable|tax(?:ed)?|yes|oui|y|t|\*+)$")
# Letters many invoices use without printing what they mean: only read when the tax they stand for is charged.
COMMON_LETTERS = {"G": ("GST",), "H": ("HST",), "P": ("PST",), "R": ("PST",), "Q": ("QST",), "E": EXEMPT,
                  "Z": ZERO_RATED, "N": "", "*": TAXABLE, "T": TAXABLE, "Y": TAXABLE}  # fmt: skip
Meaning = tuple[str, ...] | str  # tax types, a status ("exempt"...), "" for no tax, or TAXABLE


def _meaning(text: str) -> Meaning | None:
    """What a legend entry's words, or a cell's own words, say: tax types, a status or taxable."""
    if _EXEMPT_WORDS.search(text):
        return EXEMPT
    if _ZERO_WORDS.search(text):
        return ZERO_RATED
    if _OUT_WORDS.search(text):
        return OUT_OF_SCOPE
    types = tuple(t for rx, t in _WORDS if rx.search(text))
    if re.search(r"(?i)\bgst\s*/\s*hst\b", text):
        types = tuple(t for t in types if t != "GST") or ("HST",)
    if types:
        return types
    if re.search(r"(?i)\btaxable\b", text):
        return TAXABLE
    return None


def parse_legend(text: str) -> dict[str, Meaning]:
    """{letter: meaning} of the tax codes an invoice explains, e.g. "Tax codes: G = GST 5%, R = Manitoba RST 7%",
    "E - Exempt", "* = taxable item". Only lines that are about tax (they say "tax", "code", or name two codes)."""
    out: dict[str, Meaning] = {}
    for line in (text or "").splitlines():
        entries = []
        for m in _LEGEND_ENTRY.finditer(line):
            meaning = _meaning(m.group(2))
            if meaning is not None:
                entries.append((m.group(1).upper(), meaning))
        if not entries:
            continue
        if len(entries) >= 2 or re.search(r"(?i)\btax|\bcode|\btaxe", line):
            for letter, meaning in entries:
                out.setdefault(letter, meaning)
    return out


_CELL_TOKEN = re.compile(r"[A-Za-z]+|\*")
_DESC_CODES = re.compile(r"\(\s*([A-Z*](?:\s*[,/ +]\s*[A-Z*]){0,3})\s*\)\s*$")


def codes_in_description(description: str, legend: dict[str, Meaning]) -> str:
    """The codes a line prints at the end of its description ("HP 26X toner (G, P)"), when every letter is one the
    invoice's legend explains; "" otherwise."""
    m = _DESC_CODES.search(description or "")
    if not m or not legend:
        return ""
    letters = _CELL_TOKEN.findall(m.group(1))
    return m.group(1) if letters and all(x.upper() in legend for x in letters) else ""


def read_flags(cell: str, legend: dict[str, Meaning] | None, charged: Iterable[str]) -> tuple[list[str], str] | None:
    """(taxes, status) a line's printed tax cell says ("G R", "GP", "H", "E", "*", "exempt", "GST/PST"); None when it
    can't be read for sure (an unknown letter, a letter whose tax isn't charged and no legend says what it means)."""
    text = " ".join(str(cell or "").split())
    if not text:
        return None
    charged_types = ordered(charged)
    if re.fullmatch(r"(?i)n\s*/\s*t|n\.?t\.?|non|no|n/a|-+|—", text):
        return [], EXEMPT if text.lower().replace(" ", "") in ("n/t", "nt", "n.t.", "n.t") else ""
    words = _meaning(text)
    if words is not None and len(text) > 2:  # a word or a tax's name ("Exempt", "GST/PST", "HST ON")
        if words == TAXABLE:
            return (charged_types, "") if charged_types else None
        if isinstance(words, tuple):
            return ordered(words), ""
        return [], words
    if _TAXABLE_WORDS.match(text):
        legend_says = (legend or {}).get(text.upper()[:1])
        if legend_says is None or legend_says == TAXABLE:
            return (charged_types, "") if charged_types else None
    tokens = _CELL_TOKEN.findall(text)
    if not tokens or len("".join(tokens)) > 4 or re.search(r"\d", text):
        return None
    letters = [c for tok in tokens for c in (tok if tok != "*" else "*")]
    taxes: list[str] = []
    status = ""
    for letter in (x.upper() for x in letters):
        meaning = (legend or {}).get(letter)
        if meaning is None:
            meaning = COMMON_LETTERS.get(letter)
            if legend and isinstance(meaning, tuple):
                return None  # the invoice explains its tax letters, and not this one: a misread
            if meaning is None:
                return None
            if isinstance(meaning, tuple) and not set(meaning) & set(charged_types):
                return None  # "P" on an invoice that charges no PST: not sure what it means
        if meaning == TAXABLE:
            taxes += charged_types
        elif isinstance(meaning, tuple):
            taxes += list(meaning)
        elif meaning:
            status = meaning
    taxes = ordered(taxes)
    if taxes:
        return taxes, ""
    return [], status


# --- Which lines make the tax charged add up -----------------------------------------------------------------------

MAX_SOLVER_LINES = 40
MAX_SOLUTIONS = 2000  # beyond this many ways, the most plausible is looked for among the fewest lines left out
# A line one expects to be left out of a tax: freight, delivery, a deposit, something said to be exempt.
LIKELY_UNTAXED = re.compile(
    r"(?i)\b(?:freight|delivery|deliveries|shipping|shipment|cartage|courier|postage|deposit|exempt|"
    r"livraison|transport|port|d[ée]p[ôo]t|consign|bottle|recycl|eco[- ]?fee|environmental)\w*"
)


def tolerance_cents(n_lines: int, rate: float) -> int:
    """How far the lines may be from a tax's base and still be its base: a cent a line (the vendor rounds line by
    line), at least two, and the base's own uncertainty (a tax rounded to the cent is a base known to half a cent
    divided by the rate: 10 cents at 5%)."""
    rounding = 0.5 / rate if rate > 0 else 0.0
    return int(round(max(2.0, 1.0 * n_lines) + rounding))


@dataclass
class Solution:
    """One way the lines add up to a tax's base: the positions of the lines in it."""

    lines: frozenset[int]
    off: int  # cents from the target


@dataclass
class Solved:
    """What the solver found for one tax: ``kind`` "all" (every line), "unique", "several" or "none"; ``lines`` the
    positions it assigns; ``doubt`` the positions in some of the ways and not in others (when several)."""

    kind: str
    lines: frozenset[int]
    doubt: frozenset[int] = frozenset()
    count: int = 0


def _sums(values: Sequence[int]) -> tuple[Any, Any]:
    """Every subset sum of ``values`` and its bit mask (numpy arrays, 2**len(values) each)."""
    import numpy as np

    sums = np.zeros(1, dtype=np.int64)
    masks = np.zeros(1, dtype=np.int64)
    for i, v in enumerate(values):
        sums = np.concatenate([sums, sums + v])
        masks = np.concatenate([masks, masks | (1 << i)])
    return sums, masks


def subset_solutions(
    amounts: Sequence[int], target: int, tol: int, cap: int = MAX_SOLUTIONS
) -> tuple[list[Solution], int]:
    """(up to ``cap`` subsets of ``amounts`` (cents) whose sum is within ``tol`` of ``target``, how many there are in
    all), by meeting in the middle: the sums of each half, sorted, matched. Exact: every subset is counted. Negative
    amounts (a discount, a credit line) are lines like any other."""
    import numpy as np

    n = len(amounts)
    half = n // 2
    a_sums, a_masks = _sums(amounts[:half])
    b_sums, b_masks = _sums(amounts[half:])
    order = np.argsort(b_sums, kind="stable")
    b_sums, b_masks = b_sums[order], b_masks[order]
    lo = np.searchsorted(b_sums, target - tol - a_sums, side="left")
    hi = np.searchsorted(b_sums, target + tol - a_sums, side="right")
    counts = hi - lo
    total = int(counts.sum())
    found: list[Solution] = []
    for i in np.nonzero(counts)[0]:
        for j in range(int(lo[i]), int(hi[i])):
            mask = int(a_masks[i]) | (int(b_masks[j]) << half)
            found.append(Solution(frozenset(k for k in range(n) if mask >> k & 1),
                                  int(abs(int(a_sums[i]) + int(b_sums[j]) - target))))  # fmt: skip
            if len(found) >= cap:
                return found, total
    return found, total


def _plausibility(solution: Solution, n: int, descriptions: Sequence[str]) -> tuple[int, int, int]:
    """Lower is likelier: lines left out that don't read as freight, delivery, a deposit or exempt; then the fewest
    lines left out; then the closest to the target."""
    out = [k for k in range(n) if k not in solution.lines]
    unlikely = sum(1 for k in out if not LIKELY_UNTAXED.search(descriptions[k] or ""))
    return unlikely, len(out), solution.off


def solve(amounts: Sequence[float], target: float, rate: float, descriptions: Sequence[str] | None = None) -> Solved:
    """Which lines (by position) a tax whose base is ``target`` was charged on: every line when they add up to it;
    else the one set of lines that does (an exact subset sum in cents, within ``tolerance_cents``); of several, the
    likeliest (``_plausibility``), its doubtful lines in ``doubt``; none: every line (the check then says the tax
    does not add up). More than ``MAX_SOLVER_LINES`` lines: every line, not solved."""
    n = len(amounts)
    descriptions = list(descriptions or [""] * n)
    everything = frozenset(range(n))
    cents = [round(a * 100) for a in amounts]
    goal = round(target * 100)
    tol = tolerance_cents(n, rate)
    if abs(sum(cents) - goal) <= tol:
        return Solved("all", everything, count=1)
    if abs(goal) <= tol:  # nothing left to carry it (the lines printed with it carry it all): none of these
        return Solved("unique", frozenset(), count=1)
    # Lines of no amount change no sum: left out of the search, and carry the tax with the lines (taxable as usual).
    live = [k for k in range(n) if cents[k] != 0]
    if not live or len(live) > MAX_SOLVER_LINES:
        return Solved("none", everything)
    found, total = subset_solutions([cents[k] for k in live], goal, tol)
    zeros = frozenset(k for k in range(n) if cents[k] == 0)
    ways = [Solution(frozenset(live[k] for k in s.lines) | zeros, s.off) for s in found if s.lines]
    if not ways:
        return Solved("none", everything)
    ways.sort(key=lambda s: _plausibility(s, n, descriptions))
    best = ways[0]
    if total == 1:
        return Solved("unique", best.lines, count=1)
    doubt = frozenset(k for s in ways[1:] for k in best.lines ^ s.lines)
    return Solved("several", best.lines, doubt, total)


# --- Pre-filling the codes of an invoice's lines ---------------------------------------------------------------------


@dataclass
class Prefill:
    """The lines with their codes, how each got it ("printed", "solver", "all", "kept", "none"), and the positions to
    check (worked out from several possible sets of lines)."""

    lines: list[Any]
    how: list[str] = field(default_factory=list)
    check: set[int] = field(default_factory=set)


def _tax_base(tl: Any, rate: float) -> float:
    """What a tax line was charged on: its taxable amount when that and the rate make its tax, else tax ÷ rate."""
    taxable = float(_get(tl, "taxable_amount") or 0)
    tax = float(_get(tl, "tax_amount") or 0)
    if rate <= 0:
        return taxable
    if taxable and abs(round(taxable * rate, 2) - tax) <= 0.05:  # printed, or the subtotal when it makes the tax
        return taxable
    return round(tax / rate, 2)


def charged_types(tax_lines: Iterable[Any]) -> list[str]:
    """The taxes an invoice charges (a printed "GST 0.00" is no charge)."""
    return ordered(_get(t, "tax_type") for t in tax_lines or [] if abs(float(_get(t, "tax_amount") or 0)) > 0.004)


def prefill(
    lines: Sequence[Any],
    tax_lines: Sequence[Any],
    printed: Sequence[str] | None = None,
    legend: dict[str, Meaning] | None = None,
    *,
    keep: Iterable[int] = (),
    rate_for: Callable[[str, str], float | None] | None = None,
) -> Prefill:
    """Each line's tax code, worked out (``LineItem``s or dicts; new ones returned, the given ones untouched):

    1. A line in ``keep`` (a clerk's choice) keeps its code, always.
    2. The code printed beside it (``printed``, the tax column's text; or codes the legend explains at the end of its
       description), read through the invoice's ``legend`` (``parse_legend``), else the common letters
       (``read_flags``).
    3. The others: for each tax charged, the lines that make it add up (``solve``), after taking off what the lines
       of 1 and 2 already carry. A line in no tax charged is "NONE" (no tax charged); exempt or zero-rated only when
       printed so.

    ``rate_for(tax_type, province)``: the official rate (effective-dated, for the place of supply), else the tax
    line's own rate is used."""
    items = [dict(li) if isinstance(li, dict) else li.model_copy() for li in lines]
    n = len(items)
    printed = list(printed or [""] * n) + [""] * max(0, n - len(printed or []))
    keep = {k for k in keep if 0 <= k < n}
    charged = charged_types(tax_lines)
    how = [""] * n
    codes: list[tuple[list[str], str] | None] = [None] * n
    for k in range(n):
        if k in keep:
            codes[k] = (ordered(_get(items[k], "taxes_applied") or []), str(_get(items[k], "tax_status") or ""))
            how[k] = "kept"
            continue
        cell = printed[k] or codes_in_description(str(_get(items[k], "description") or ""), legend or {})
        read = read_flags(cell, legend, charged) if cell else None
        if read is not None:
            codes[k], how[k] = read, "printed"
    check: set[int] = set()
    amounts = [float(_get(li, "amount") or 0) for li in items]
    descriptions = [str(_get(li, "description") or "") for li in items]
    free = [k for k in range(n) if codes[k] is None]
    assigned: dict[int, set[str]] = {k: set() for k in free}
    solved_any = False
    for tax_type in charged:
        taxes = [
            t for t in tax_lines if _get(t, "tax_type") == tax_type and abs(float(_get(t, "tax_amount") or 0)) > 0.004
        ]
        tax = sum(float(_get(t, "tax_amount") or 0) for t in taxes)
        province = str(_get(taxes[0], "province") or "")
        rate = None
        one_rate = len({round(float(_get(t, "rate") or 0), 6) for t in taxes}) == 1
        if rate_for is not None and tax_type != "OTHER" and one_rate:
            rate = rate_for(tax_type, province)
        rate = rate or float(_get(taxes[0], "rate") or 0)
        base = sum(_tax_base(t, rate if len(taxes) == 1 else float(_get(t, "rate") or 0)) for t in taxes)
        if not rate and tax:
            base = sum(float(_get(t, "taxable_amount") or 0) for t in taxes)
        carried = sum(amounts[k] for k in range(n) if codes[k] is not None and tax_type in codes[k][0])
        if not free:
            continue
        target = round(base - carried, 2)
        solved = solve([amounts[k] for k in free], target, rate or 0.05, [descriptions[k] for k in free])
        for j in solved.lines:
            assigned[free[j]].add(tax_type)
        if solved.kind == "several":
            check |= {free[j] for j in solved.doubt}
        solved_any = solved_any or solved.kind in ("unique", "several")
    for k in free:
        codes[k] = (ordered(assigned[k]), "")
        how[k] = ("solver" if solved_any else "all") if charged else "none"
    for k in range(n):
        taxes, status = codes[k] or ([], "")
        if isinstance(items[k], dict):
            items[k]["taxes_applied"], items[k]["tax_status"] = taxes, status
        else:
            items[k] = items[k].model_copy(update={"taxes_applied": taxes, "tax_status": status})
        if k in check:
            reason = str(_get(items[k], "reasoning_justification") or "")
            note = f"{reason} {GUESS_NOTE}".strip() if GUESS_NOTE not in reason else reason
            if isinstance(items[k], dict):
                items[k]["reasoning_justification"] = note
            else:
                items[k] = items[k].model_copy(update={"reasoning_justification": note})
    return Prefill(items, how, check)


def model_gave_no_taxes(lines: Sequence[Any]) -> bool:
    """Whether a coding says nothing of its lines' taxes (no line has a tax or a status): the chat model left them
    out, and they are worked out (``prefill``)."""
    return all(not (_get(li, "taxes_applied") or []) and not (_get(li, "tax_status") or "") for li in lines)


def tax_guessed(line: Any) -> bool:
    """Whether a line's code was worked out from several possible sets of lines (``GUESS_NOTE``), to check."""
    return GUESS_NOTE in str(_get(line, "reasoning_justification") or "")


__all__ = [
    "CANADIAN_TAX_TYPES", "CODE_EXEMPT", "CODE_NONE", "CODE_OUT", "CODE_ZERO", "EXEMPT", "OUT_OF_SCOPE", "TAX_STATUSES",
    "ZERO_RATED", "all_labels", "apply_tax_code", "code_for", "code_label", "options", "parse_code", "parse_legend",
    "prefill", "read_flags", "solve", "tax_code_of",
]  # fmt: skip
