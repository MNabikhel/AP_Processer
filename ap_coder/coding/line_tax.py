"""Which sales tax each invoice line carries, tax by tax: the line's tax code (decision D-64).

For each tax that applies where the invoice is supplied (the place of supply, D-61), a line is in one of three states:

* **charged**: the vendor charged it on the line (``LineItem.taxes_applied``, as always: old invoices, every reader,
  check and export keep working);
* **not due**: the line is exempt or zero-rated from it (``LineItem.tax_exempt``, tax by tax: "GST only · PST
  exempt"), or the whole line says why it carries no tax (``LineItem.tax_status``: exempt from every tax,
  zero-rated, out of scope);
* **due, not charged**: neither: the tax is owed and the vendor did not collect it (an out-of-province or unregistered
  vendor, or a line it missed): a provincial tax is then self-assessed (BC PSTA s.49, the SK and MB acts alike; QST:
  the QST Act).

An invoice stored before ``tax_exempt`` existed (``None``) is read as the vendor meant it: a tax it charges on other
lines and not on this one is not due on it (the vendor collects it and chose not to: exempt); a tax it charges on no
line is due (``fill_exempt``).

The review screen shows a line's states as one choice, the **tax code**: "GST+PST"; "GST~PST" (GST charged, PST
exempt); "GST" (GST charged; where PST applies, PST due and not charged: self-assess); "PST~GST"; "EXEMPT", "ZERO",
"OUT"; "NONE" (nothing charged, nothing said). ``options`` lists the codes that make sense where the invoice is
supplied, in plain words ("GST only · PST exempt"); ``prefill`` works them out when an invoice is read.
"""

from __future__ import annotations

import itertools
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .tax import CANADIAN_TAX_TYPES, LAWFUL_OTHER_RATES, REGIME, TAX_TYPES

EXEMPT, ZERO_RATED, OUT_OF_SCOPE = "exempt", "zero_rated", "out_of_scope"
TAX_STATUSES = ("", EXEMPT, ZERO_RATED, OUT_OF_SCOPE)
CODE_EXEMPT, CODE_ZERO, CODE_OUT, CODE_NONE = "EXEMPT", "ZERO", "OUT", "NONE"
STATUS_CODES = {EXEMPT: CODE_EXEMPT, ZERO_RATED: CODE_ZERO, OUT_OF_SCOPE: CODE_OUT}
CODE_STATUSES = {code: status for status, code in STATUS_CODES.items()}
NO_TAX_CODES = (CODE_EXEMPT, CODE_ZERO, CODE_OUT, CODE_NONE)
# Lines that carry no tax because they say so (exempt, zero-rated, out of scope): never in a self-assessment base.
STATED = frozenset(STATUS_CODES)
CHARGED, NOT_DUE, DUE = "charged", "not_due", "due"  # a tax's state on a line
PROVINCIAL = ("PST", "QST")  # taxes the buyer self-assesses when the vendor does not collect them
EXEMPT_MARK = "~"  # in a code: the taxes charged, then those the line is exempt from ("GST~PST")

# A line's code worked out from the tax charged when another set of lines could make it add up, or a line left out
# that does not read as untaxed: marked on the review screen for a person to look at (``tax_guessed``) until they
# pick or confirm the code (B-713).
GUESS_NOTE = "Tax code worked out from the tax charged: check it."
# A line's code taken from what AP approved on the vendor's similar lines before (``learned_codes``): said on the review
# screen, and checked by the Tax check like any other (D-70).
LEARNED_NOTE = "Tax code learned from past approvals of this vendor."
MIN_AGREEING = 2  # approvals (of different invoices, on different days) of one code, none for another, before use
# A BC, SK or MB PST (RST) exemption inferred from the vendor charging it on other lines and not this one: exempt, or
# missed and to self-assess? A person says which: approval waits until they pick or confirm it (B-715), and the
# confirmation stays in the approved coding (with the audit trail's who and when).
_INFERRED = re.compile(r"\b(PST|RST) exemption inferred \(charged on the other lines, not this one\): confirm it\.")
_CONFIRMED = "{name} exemption confirmed on review."


def inferred_note(name: str) -> str:
    return f"{name} exemption inferred (charged on the other lines, not this one): confirm it."


def exemption_to_confirm(line: Any) -> str:
    """The provincial tax (its name: "PST", "RST") whose exemption on this line was inferred and not yet confirmed by
    a person, else ""."""
    m = _INFERRED.search(str(_get(line, "reasoning_justification") or ""))
    return m.group(1) if m else ""


_CONFIRMED_RE = re.compile(r"\b(PST|RST) exemption confirmed on review\.")


def confirm_line(reason: str, code: str, inferred: str | None = None) -> str:
    """A line's reasoning once a person picked or confirmed its tax ``code``: the "check it" note goes; an inferred
    exemption still in the code is recorded as confirmed, and goes if the code no longer exempts it. ``inferred``: the
    reasoning as first worked out, so that picking the exemption again after picking another code is still recorded
    as confirmed on review (B-723)."""
    text = str(reason or "").replace(GUESS_NOTE, " ").replace(LEARNED_NOTE, " ")  # the person's code now
    parsed = parse_code(code)
    still = parsed is not None and "PST" in parsed[1]
    m = _INFERRED.search(text) or _CONFIRMED_RE.search(text)
    if m:
        text = text.replace(m.group(0), _CONFIRMED.format(name=m.group(1)) if still else " ")
    elif still and (first := _INFERRED.search(str(inferred or ""))):
        text = f"{text} {_CONFIRMED.format(name=first.group(1))}"
    return " ".join(text.split())


PST_NAMES = {"MB": "RST"}  # Manitoba calls its PST the retail sales tax


# --- The states of a line -------------------------------------------------------------------------------------------


def _get(line: Any, name: str, default: Any = None) -> Any:
    return line.get(name, default) if isinstance(line, dict) else getattr(line, name, default)


def ordered(taxes: Iterable[str]) -> list[str]:
    """Tax types once each, in the usual order (GST, HST, PST, QST, OTHER); anything else dropped."""
    got = {str(t).strip().upper() for t in taxes or []}
    return [t for t in TAX_TYPES if t in got]


def legacy_basis(lines: Iterable[Any], tax_lines: Iterable[Any]) -> list[str]:
    """The taxes a line stored before ``tax_exempt`` is read against: those the invoice charges and that some line is
    marked with (the vendor collects them on those lines)."""
    marked = {t for li in lines for t in (_get(li, "taxes_applied") or [])}
    return [t for t in charged_types(tax_lines) if t in marked]


def legacy_exempt(line: Any, charged: Iterable[str]) -> list[str]:
    """The taxes a line stored before ``tax_exempt`` is not due: of ``charged`` (``legacy_basis``: charged by the
    invoice on other lines), the Canadian ones not on it (the vendor collects them and chose not to here). [] when
    the line says why it carries no tax."""
    taxes = set(ordered(_get(line, "taxes_applied") or []))
    if _get(line, "tax_status"):
        return []
    return [t for t in ordered(charged) if t in CANADIAN_TAX_TYPES and t not in taxes]


def exempt_of(line: Any, charged: Iterable[str] = ()) -> list[str]:
    """The taxes a line is exempt or zero-rated from, tax by tax (``tax_exempt``; a line stored before it: read as
    ``legacy_exempt``)."""
    stored = _get(line, "tax_exempt")
    if stored is None:
        return legacy_exempt(line, charged)
    return [t for t in ordered(stored) if t not in ordered(_get(line, "taxes_applied") or [])]


def tax_state(line: Any, tax_type: str, charged: Iterable[str] = ()) -> str:
    """CHARGED, NOT_DUE or DUE: a tax on a line (``charged``: the taxes the invoice charges, for a line stored before
    ``tax_exempt``)."""
    if tax_type in (_get(line, "taxes_applied") or []):
        return CHARGED
    if _get(line, "tax_status") in STATUS_CODES:
        return NOT_DUE
    return NOT_DUE if tax_type in exempt_of(line, charged) else DUE


def fill_exempt(lines: Sequence[Any], tax_lines: Iterable[Any]) -> list[Any]:
    """``lines`` with ``tax_exempt`` set where it is not (an invoice stored before it): ``legacy_exempt``."""
    charged = legacy_basis(lines, tax_lines)
    out = []
    for li in lines:
        if _get(li, "tax_exempt") is None:
            update: dict[str, Any] = {"tax_exempt": legacy_exempt(li, charged)}
            if update["tax_exempt"] and not (_get(li, "taxes_applied") or []):
                update = {"tax_exempt": [], "tax_status": EXEMPT}  # nothing charged on it, all charged elsewhere
            li = {**li, **update} if isinstance(li, dict) else li.model_copy(update=update)
        out.append(li)
    return out


# --- The code of a line ---------------------------------------------------------------------------------------------


def code_for(taxes: Iterable[str], status: str = "", exempt: Iterable[str] = ()) -> str:
    """The tax code of (taxes, status, exempt): "GST+PST", "GST~PST", "HST", "EXEMPT", "ZERO", "OUT" or "NONE"."""
    types = ordered(taxes)
    status = str(status or "").strip().lower()
    if not types and status in STATUS_CODES:
        return STATUS_CODES[status]
    free = [t for t in ordered(exempt) if t not in types]
    main = "+".join(types) or CODE_NONE
    return f"{main}{EXEMPT_MARK}{'+'.join(free)}" if free else main


def tax_code_of(line: Any, charged: Iterable[str] = ()) -> str:
    """A line's tax code (a ``LineItem`` or a line as a dict; ``charged`` for a line stored before ``tax_exempt``)."""
    return code_for(_get(line, "taxes_applied") or [], _get(line, "tax_status") or "", exempt_of(line, charged))


def parse_code(code: str) -> tuple[list[str], list[str], str] | None:
    """(taxes_applied, tax_exempt, tax_status) of a tax code; None when it is not one ("GST+FOO", "")."""
    text = str(code or "").strip().upper().replace(" ", "")
    if text in CODE_STATUSES:
        return [], [], CODE_STATUSES[text]
    main, _, free = text.partition(EXEMPT_MARK)

    def types(part: str) -> list[str] | None:
        parts = [p for p in re.split(r"[+/,]", part) if p]
        return ordered(parts) if all(p in TAX_TYPES for p in parts) else None

    charged = [] if main == CODE_NONE else types(main)
    exempt = types(free) if free else []
    if charged is None or exempt is None or (not main):
        return None
    return charged, [t for t in exempt if t not in charged], ""


def apply_tax_code(line: Any, code: str) -> Any:
    """``line`` with the taxes, exemptions and status of ``code`` (a dict is changed in place, a ``LineItem``
    copied). An unknown code changes nothing."""
    parsed = parse_code(code)
    if parsed is None:
        return line
    taxes, exempt, status = parsed
    if isinstance(line, dict):
        line.update(taxes_applied=taxes, tax_exempt=exempt, tax_status=status)
        return line
    return line.model_copy(update={"taxes_applied": taxes, "tax_exempt": exempt, "tax_status": status})


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


def _regime(province: str) -> list[str]:
    return ordered(REGIME.get(province, frozenset()))


def code_label(code: str, province: str = "") -> str:
    """The plain words of a code where the invoice is supplied: "GST + PST", "GST only · PST exempt", "GST only · PST
    not charged (self-assess)", "PST only · GST exempt", "Exempt from both", "Zero-rated", "No tax (out of scope)"."""
    regime = _regime(province)
    if code == CODE_EXEMPT:
        return "Exempt from both" if len(regime) == 2 else "Exempt"
    if code == CODE_ZERO:
        return "Zero-rated"
    if code == CODE_OUT:
        return "No tax (out of scope)"
    parsed = parse_code(code)
    if parsed is None:
        return code
    charged, exempt, _ = parsed
    names = [tax_name(t, province) for t in charged]
    due = [t for t in regime if t in PROVINCIAL and t not in charged and t not in exempt and "HST" not in charged]
    if charged:
        main = " + ".join(names)
        # "GST only" where it is one of the province's two taxes (or GST where HST applies), so it reads as a choice
        partial = (len(regime) > 1 and set(charged) < set(regime)) or (charged == ["GST"] and regime == ["HST"])
        main += " only" if partial and len(charged) == 1 else ""
    else:
        main = "No tax charged"
    parts = [f"{tax_name(t, province)} exempt" for t in exempt]
    # A PST due and not charged is self-assessed (BC PSTA s.49...); a QST is too in law, but a registrant buying for
    # its commercial activities claims it back and generally owes nothing net: said without "self-assess" (B-718).
    parts += [f"{tax_name(t, province)} not charged" + (" (self-assess)" if t == "PST" else "") for t in due]
    if not charged and not exempt and due:
        if "PST" in due:
            return f"No tax charged (self-assess {' and '.join(tax_name(t, province) for t in due)})"
        return f"No tax charged · {' and '.join(tax_name(t, province) for t in due)} not charged"
    return " · ".join([main, *parts])


def options(province: str, charged: Iterable[str] = (), current: Iterable[str] = ()) -> list[tuple[str, str]]:
    """The (code, label) choices of a line's dropdown for an invoice supplied in ``province``, in order.

    * HST provinces: HST, Exempt, Zero-rated, No tax (out of scope); GST only too when GST is charged there (a
      vendor not registered for the HST charges GST alone: the line says what was charged, and ITC_AT_RISK says only
      the tax properly payable is claimable).
    * BC, SK, MB: GST + PST, GST only · PST exempt, GST only · PST not charged (self-assess), PST only · GST exempt,
      Exempt from both, Zero-rated, No tax (out of scope); RST in Manitoba. Quebec: the same with QST (QST mostly
      follows the GST's exemptions).
    * Alberta and the territories: GST, Exempt, Zero-rated, No tax (out of scope).
    * Any tax charged that the province does not use (HST charged in BC), "Foreign tax" (and GST + foreign tax) when a
      non-Canadian tax is charged, and each line's own code (``current``), whatever it is.
    Unknown province: the Canadian combinations."""
    charged = set(ordered(charged))
    usual = REGIME.get(province)
    codes: list[str] = []

    def add(code: str) -> None:
        if code and code not in codes:
            codes.append(code)

    if usual is None:
        for code in ("HST", "GST+PST", "GST+QST", "GST"):
            add(code)
    elif usual == {"HST"}:
        add("HST")
        if "GST" in charged:
            add("GST")
    elif len(usual) == 2:
        prov = "PST" if "PST" in usual else "QST"
        for code in (f"GST+{prov}", f"GST~{prov}", "GST", f"{prov}~GST"):
            add(code)
    else:
        add("GST")
    for t in ordered(charged - set(usual or ()) - {"OTHER"}):
        add(t)
    if "OTHER" in charged:
        add("OTHER")
        if charged & {"GST", "HST"}:
            add("+".join(ordered([*(charged & {"GST", "HST"}), "OTHER"])))
    for code in (CODE_EXEMPT, CODE_ZERO, CODE_OUT):
        add(code)
    for code in current:
        parsed = parse_code(code)
        add(code_for(parsed[0], parsed[2], parsed[1]) if parsed else "")
    return [(c, code_label(c, province)) for c in codes]


def export_code(line: Any, province: str = "", charged: Iterable[str] = ()) -> str:
    """A line's tax code as an export file says it, in the words of the screen: "GST + PST", "GST only · PST exempt",
    "Exempt", "Zero-rated"."""
    return code_label(tax_code_of(line, charged), province)


def all_labels(province: str = "") -> dict[str, str]:
    """A label for every code there is (every combination of taxes charged, of the Canadian taxes exempt, and the
    no-tax codes), for a line's own unusual code the dropdown keeps."""
    out = {}
    mains = [""] + ["+".join(c) for n in range(1, len(TAX_TYPES) + 1) for c in itertools.combinations(TAX_TYPES, n)]
    for main in mains:
        for n in range(0, 3):
            for free in itertools.combinations(CANADIAN_TAX_TYPES, n):
                if set(free) & set(main.split("+")):
                    continue
                parsed = parse_code(f"{main or CODE_NONE}{EXEMPT_MARK + '+'.join(free) if free else ''}")
                if parsed is not None:
                    code = code_for(parsed[0], parsed[2], parsed[1])
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
# More ways than this for the lines to make a tax, or several ways among more than ``MAX_DOUBTFUL_LINES`` lines: the
# amounts can't say which lines carry it, and a pick would be noise presented as a reading (B-706).
MAX_WAYS = 20
MAX_DOUBTFUL_LINES = 20
# A line one expects to carry no tax: a deposit, a gift card, a tip, interest or a late payment charge (not
# consideration for a supply: ETA s.161), or one the invoice says is exempt or zero-rated. Not freight, delivery,
# shipping or an environmental fee: charged by the vendor they follow the goods for GST/HST (one supply, CRA
# P-077R2), are part of the taxable price for BC, SK and MB PST, and an eco fee is part of the consideration (B-711).
LIKELY_UNTAXED = re.compile(
    r"(?i)\b(?:deposits?|d[ée]p[ôo]ts?|gift\s*cards?|cartes?[- ]cadeaux?|tips?|gratuit(?:y|ies)|pourboires?|"
    r"interest|int[ée]r[êe]ts?|late\s+(?:payment\s+)?(?:fees?|charges?)|frais\s+de\s+retard|exempt\w*|"
    r"zero[- ]?rated|non[- ]?taxable|tax[- ]?exempt|bottle\s+deposit|container\s+deposit)\b"
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
    """What the solver found for one tax: ``kind`` "all" (every line), "unique", "several", "none" (no set of lines
    makes it: every line, the check says it does not add up) or "unsure" (too many ways: every line, nothing picked);
    ``lines`` the positions it assigns; ``doubt`` the positions to check: in some of the ways and not in others, or
    left out while their description does not read as untaxed."""

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


def subset_solutions(amounts: Sequence[int], target: int, tol: int, cap: int = 2000) -> tuple[list[Solution], int]:
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
    """Lower is likelier: lines left out that don't read as untaxed (``LIKELY_UNTAXED``); then the fewest lines left
    out; then the closest to the target."""
    out = [k for k in range(n) if k not in solution.lines]
    unlikely = sum(1 for k in out if not LIKELY_UNTAXED.search(descriptions[k] or ""))
    return unlikely, len(out), solution.off


def solve(amounts: Sequence[float], target: float, rate: float, descriptions: Sequence[str] | None = None) -> Solved:
    """Which lines (by position) a tax whose base is ``target`` was charged on.

    * Every line when they add up to it.
    * A credit note (its lines add up to less than nothing) is solved in the size of its amounts: it reverses an
      invoice line for line (ETA s.232).
    * A line below zero (a trade discount, a credit) is never left out on its own: it lowers the taxable price of
      what it discounts (ETA s.165; only an early-payment discount is not, s.161), so it carries the tax with the
      others (B-707).
    * Else the sets of lines that do (an exact subset sum in cents, within ``tolerance_cents``, counted exactly by
      meeting in the middle, without listing them all): one set, its lines left out to check unless they read as
      untaxed (``LIKELY_UNTAXED``); a few (``MAX_WAYS`` among at most ``MAX_DOUBTFUL_LINES`` lines), the likeliest,
      the lines in some and not others to check; more: "unsure", every line, nothing picked (B-706); none: every line.
    More than ``MAX_SOLVER_LINES`` lines: every line, not solved."""
    n = len(amounts)
    descriptions = list(descriptions or [""] * n)
    everything = frozenset(range(n))
    sign = -1 if sum(amounts) < 0 else 1
    cents = [sign * round(a * 100) for a in amounts]
    goal = sign * round(target * 100)
    tol = tolerance_cents(n, rate)
    if abs(sum(cents) - goal) <= tol:
        return Solved("all", everything, count=1)
    if abs(goal) <= tol:  # nothing left to carry it (the lines printed with it carry it all): none of these
        return Solved("unique", frozenset(), count=1)
    # Lines of no amount change no sum, and lines below zero always go with the others: both are left out of the
    # search and carry the tax.
    forced = frozenset(k for k in range(n) if cents[k] <= 0)
    live = [k for k in range(n) if cents[k] > 0]
    goal -= sum(cents[k] for k in forced)
    if not live or len(live) > MAX_SOLVER_LINES:
        return Solved("none", everything)
    found, total = subset_solutions([cents[k] for k in live], goal, tol, cap=MAX_WAYS + 1)
    ways = [Solution(frozenset(live[k] for k in s.lines) | forced, s.off) for s in found if s.lines]
    total -= sum(1 for s in found if not s.lines)
    if not ways:
        return Solved("none", everything)
    if total > MAX_WAYS or (total > 1 and len(live) > MAX_DOUBTFUL_LINES):
        # every line carries it and every line is to check: the amounts can't say which carry it (B-714)
        return Solved("unsure", everything, everything, count=total)
    ways.sort(key=lambda s: _plausibility(s, n, descriptions))
    best = ways[0]
    left_out = frozenset(k for k in everything - best.lines if not LIKELY_UNTAXED.search(descriptions[k] or ""))
    if total == 1:
        return Solved("unique", best.lines, left_out, count=1)
    doubt = frozenset(k for s in ways[1:] for k in best.lines ^ s.lines) | left_out
    return Solved("several", best.lines, doubt, total)


# --- Pre-filling the codes of an invoice's lines ---------------------------------------------------------------------


@dataclass
class Prefill:
    """The lines with their codes, how each got it ("printed", "solver", "all", "kept", "none"), and the positions to
    check (worked out from several possible sets of lines)."""

    lines: list[Any]
    how: list[str] = field(default_factory=list)
    check: set[int] = field(default_factory=set)
    # The taxes charged that no set of the lines left to work out could carry, and the positions the amounts could
    # not decide between (several ways, or too many): what ``prefill`` compares with and without the learned codes.
    unallocated: set[str] = field(default_factory=set)
    ambiguous: set[int] = field(default_factory=set)


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
    vendor_collects: bool | Mapping[str, bool] = True,
    learned: Sequence[str] | None = None,
    learned_alike: bool = False,
) -> Prefill:
    """Each line's tax code (``_prefill``), with what AP approved before (``learned``) used only where it is safe
    (D-80): a learned code that leaves a tax charged on no set of the other lines (the vendor charged it on this
    line this time) is dropped and the lines worked out again; and a line the amounts alone could not decide on keeps
    its "check it" note (``GUESS_NOTE``), learned code or not: the history may say which lines, a person confirms."""
    args = dict(printed=printed, legend=legend, keep=keep, rate_for=rate_for, vendor_collects=vendor_collects,
                learned_alike=learned_alike)  # fmt: skip
    if not any(learned or ()):
        return _prefill(lines, tax_lines, **args)
    alone = _prefill(lines, tax_lines, **args)
    done = _prefill(lines, tax_lines, **args, learned=learned)
    if done.unallocated - alone.unallocated:
        dropped = {k for k, how in enumerate(done.how) if how == "learned"}
        learned = [code if k not in dropped else "" for k, code in enumerate(learned)]
        done = _prefill(lines, tax_lines, **args, learned=learned)
        for k in sorted(dropped):  # the history and this invoice's tax disagree on these lines: a person looks
            if done.how[k] != "kept":
                done.lines[k] = _with_note(done.lines[k], GUESS_NOTE)
                done.check.add(k)
    for k in sorted(alone.ambiguous):
        if done.how[k] in ("learned", "solver", "all") and not tax_guessed(done.lines[k]):
            done.lines[k] = _with_note(done.lines[k], GUESS_NOTE)
            done.check.add(k)
    return done


def _with_note(line: Any, note: str) -> Any:
    """``line`` with ``note`` added to its reasoning (once): a dict changed in place, a ``LineItem`` copied."""
    reason = str(_get(line, "reasoning_justification") or "")
    reason = reason if note in reason else f"{reason} {note}".strip()
    if isinstance(line, dict):
        line["reasoning_justification"] = reason
        return line
    return line.model_copy(update={"reasoning_justification": reason})


def _prefill(
    lines: Sequence[Any],
    tax_lines: Sequence[Any],
    printed: Sequence[str] | None = None,
    legend: dict[str, Meaning] | None = None,
    *,
    keep: Iterable[int] = (),
    rate_for: Callable[[str, str], float | None] | None = None,
    vendor_collects: bool | Mapping[str, bool] = True,
    learned: Sequence[str] | None = None,
    learned_alike: bool = False,
) -> Prefill:
    """Each line's tax code, worked out (``LineItem``s or dicts; new ones returned, the given ones untouched):

    1. A line in ``keep`` (a clerk's choice) keeps its code, always.
    2. The code printed beside it (``printed``, the tax column's text; or codes the legend explains at the end of its
       description), read through the invoice's ``legend`` (``parse_legend``), else the common letters
       (``read_flags``).
    3. The code the vendor's history agrees on (``learned``, from ``learned_codes``: AP picked it, or approved it on
       two days, on lines of the same place of supply worded alike, never another), when the taxes it charges are
       all charged on this invoice, and not the exemption of a tax the vendor does not collect: marked as learned
       (``LEARNED_NOTE``). A code printed with the same taxes takes the exemptions from it (a printed "G" where the
       PST is not charged: exempt, as AP approved it, rather than due).
    4. The others: for each tax charged, the lines that make it add up (``solve``), after taking off what the lines
       of 1 to 3 already carry. A line in no tax charged is "NONE" (no tax charged); exempt or zero-rated only when
       printed so.

    The Tax check (``taxmath.math_check``) checks the codes whichever way they came: a learned code that does not
    make this invoice's tax add up is flagged like any other.

    ``rate_for(tax_type, province)``: the official rate (effective-dated, for the place of supply), else the tax
    line's own rate is used. ``vendor_collects``: False for a vendor out of the province of supply or without a PST
    number: a PST it charges on other lines and not on a line is then due on it (self-assess), not exempt (B-715);
    nor is an exemption of it learned from the history used (B-760), unless ``learned_alike``: the codes were learned
    from approvals where the vendor did not collect it either (``learned_codes`` with ``uncollected``). Tax by tax
    ({"PST": False}), or one for both provincial taxes."""

    def collects(tax_type: str) -> bool:
        return learned_alike or collects_now(tax_type)

    def collects_now(tax_type: str) -> bool:
        if isinstance(vendor_collects, Mapping):
            return bool(vendor_collects.get(tax_type, True))
        return bool(vendor_collects)

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
    # What AP approved on the vendor's similar lines, for the lines nothing printed says (D-70).
    learned_exempt: dict[int, list[str]] = {}
    disputed: set[int] = set()
    for k, code in enumerate(list(learned or [])[:n]):
        parsed = parse_code(code) if code and k not in keep else None
        if parsed is None or not set(parsed[0]) <= set(charged):
            continue
        taxes, exempt, status = parsed
        # A vendor that does not collect a provincial tax: it is due where it is not charged (B-715), whatever was
        # approved on the vendor's lines before, when it did (or seemed to): the exemption of it is not used (B-760).
        # The history and that rule disagree: a person looks ("check it").
        if status in (EXEMPT, ZERO_RATED) and not all(collects(t) for t in PROVINCIAL):
            disputed.add(k)
            continue
        if any(t in PROVINCIAL and not collects(t) for t in exempt):
            disputed.add(k)
        exempt = [t for t in exempt if t not in PROVINCIAL or collects(t)]
        if codes[k] is None:
            codes[k], how[k] = (taxes, status), "learned"
            learned_exempt[k] = exempt
        elif how[k] == "printed" and codes[k] == (taxes, status):
            learned_exempt[k] = exempt  # printed "G": the PST not charged is exempt, as AP approved it
    check: set[int] = set(disputed)
    amounts = [float(_get(li, "amount") or 0) for li in items]
    descriptions = [str(_get(li, "description") or "") for li in items]
    free = [k for k in range(n) if codes[k] is None]
    assigned: dict[int, set[str]] = {k: set() for k in free}
    solved_any = False
    unallocated: set[str] = set()
    ambiguous: set[int] = set()
    for tax_type in charged:
        taxes = [
            t for t in tax_lines if _get(t, "tax_type") == tax_type and abs(float(_get(t, "tax_amount") or 0)) > 0.004
        ]
        tax = sum(float(_get(t, "tax_amount") or 0) for t in taxes)
        province = str(_get(taxes[0], "province") or "")
        rate = None
        one_rate = len({round(float(_get(t, "rate") or 0), 6) for t in taxes}) == 1
        printed_rate = round(float(_get(taxes[0], "rate") or 0), 6)
        lawful = printed_rate in LAWFUL_OTHER_RATES.get((tax_type, province), ())  # BC PST 10% on liquor
        if rate_for is not None and tax_type != "OTHER" and one_rate and not lawful:
            rate = rate_for(tax_type, province)
        rate = rate or printed_rate
        base = sum(_tax_base(t, rate if len(taxes) == 1 else float(_get(t, "rate") or 0)) for t in taxes)
        if not rate and tax:
            base = sum(float(_get(t, "taxable_amount") or 0) for t in taxes)
        carried = sum(amounts[k] for k in range(n) if codes[k] is not None and tax_type in codes[k][0])
        target = round(base - carried, 2)
        if not free:
            if abs(target) * 100 > tolerance_cents(n, rate or 0.05):
                unallocated.add(tax_type)
            continue
        solved = solve([amounts[k] for k in free], target, rate or 0.05, [descriptions[k] for k in free])
        if solved.kind == "none":
            unallocated.add(tax_type)
        elif solved.kind in ("several", "unsure"):
            ambiguous |= {free[j] for j in solved.doubt}
        for j in solved.lines:
            assigned[free[j]].add(tax_type)
        check |= {free[j] for j in solved.doubt}
        # A taxable amount printed that says otherwise (the subtotal, while the tax is on less): the lines left out
        # are what the vendor's figures disagree on, to check (B-707).
        printed_taxable = sum(float(_get(t, "taxable_amount") or 0) for t in taxes)
        if printed_taxable and abs(printed_taxable - base) > tolerance_cents(len(free), rate or 0.05) / 100:
            check |= {free[j] for j in range(len(free)) if j not in solved.lines}
        solved_any = solved_any or solved.kind in ("unique", "several")
    for k in free:
        codes[k] = (ordered(assigned[k]), "")
        how[k] = ("solver" if solved_any else "all") if charged else "none"
    canadian = [t for t in charged if t in CANADIAN_TAX_TYPES]
    pst_name = tax_name(
        "PST", next((str(_get(t, "province") or "") for t in tax_lines if _get(t, "tax_type") == "PST"), "")
    )
    for k in range(n):
        taxes, status = codes[k] or ([], "")
        if k in keep:
            exempt = exempt_of(items[k], charged)
        elif k in learned_exempt:
            exempt = [] if status else [t for t in learned_exempt[k] if t not in taxes]
            if how[k] == "learned":
                reason = str(_get(items[k], "reasoning_justification") or "")
                reason = reason if LEARNED_NOTE in reason else f"{reason} {LEARNED_NOTE}".strip()
                if isinstance(items[k], dict):
                    items[k]["reasoning_justification"] = reason
                else:
                    items[k] = items[k].model_copy(update={"reasoning_justification": reason})
        elif status:
            exempt = []
        else:
            # A tax the vendor charges on other lines and not on this one is not due on it (it collects it and chose
            # not to: exempt); a tax charged on no line stays due (D-64). Nothing charged on a line while the vendor
            # charges tax on others: an exempt line.
            exempt = [t for t in canadian if t not in taxes]
            if "PST" in exempt and not collects_now("PST"):  # out of province, or no PST number: due, not exempt
                exempt.remove("PST")
            if not taxes and canadian and set(exempt) == set(canadian):
                if all(collects_now(t) for t in PROVINCIAL):
                    status, exempt = EXEMPT, []
                else:  # no PST/QST charged says nothing of a tax the vendor never collects: due on it (B-788)
                    check.add(k)
            # A PST exemption inferred, not printed: a person confirms it (exempt, or missed and to self-assess).
            if "PST" in canadian and "PST" not in taxes and collects_now("PST") and how[k] != "printed":
                reason = str(_get(items[k], "reasoning_justification") or "")
                note = inferred_note(pst_name)
                if note not in reason:
                    reason = f"{reason} {note}".strip()
                if isinstance(items[k], dict):
                    items[k]["reasoning_justification"] = reason
                else:
                    items[k] = items[k].model_copy(update={"reasoning_justification": reason})
        update = {"taxes_applied": taxes, "tax_status": status, "tax_exempt": exempt}
        if isinstance(items[k], dict):
            items[k].update(update)
        else:
            items[k] = items[k].model_copy(update=update)
        if k in check:
            reason = str(_get(items[k], "reasoning_justification") or "")
            note = f"{reason} {GUESS_NOTE}".strip() if GUESS_NOTE not in reason else reason
            if isinstance(items[k], dict):
                items[k]["reasoning_justification"] = note
            else:
                items[k] = items[k].model_copy(update={"reasoning_justification": note})
    return Prefill(items, how, check, unallocated, ambiguous)


def can_explain(amounts: Sequence[float], tax: float, rate: float) -> bool | None:
    """Whether some set of the lines (discounts always with them) makes ``tax`` at ``rate`` (to a cent a line and the
    tax's rounding): True, a choice of lines can explain it; False, no set of lines can (the vendor's arithmetic);
    None when there are too many lines to tell."""
    if rate <= 0 or not amounts:
        return None
    if sum(1 for a in amounts if abs(a) > 0.004) > MAX_SOLVER_LINES:
        return None
    return solve(list(amounts), round(tax / rate, 2), rate).kind != "none"


def model_gave_no_taxes(lines: Sequence[Any]) -> bool:
    """Whether a coding says nothing of its lines' taxes (no line has a tax or a status): the chat model left them
    out, and they are worked out (``prefill``)."""
    return all(not (_get(li, "taxes_applied") or []) and not (_get(li, "tax_status") or "") for li in lines)


def without_guess_note(text: str) -> str:
    """A line's reasoning without the "check it" note (``GUESS_NOTE``): the person confirmed the code."""
    return " ".join(str(text or "").replace(GUESS_NOTE, " ").split())


def confirm_codes(output: dict[str, Any]) -> dict[str, Any]:
    """An approved coding (``finalise_coding``'s output): approving confirms every line's tax code, so no line keeps
    the note that it was to check (B-713)."""
    for li in output.get("line_items") or []:
        if GUESS_NOTE in str(li.get("reasoning_justification") or ""):
            li["reasoning_justification"] = without_guess_note(li["reasoning_justification"])
    return output


def approved_code(line: Any, lines: Sequence[Any] = (), tax_lines: Iterable[Any] = ()) -> str:
    """The tax code an approved line is remembered with (``feedback.final_tax_code``): its code, "" for "NONE" (nothing
    charged, nothing said: nothing to learn). ``lines`` and ``tax_lines``: the invoice's, for a line stored before
    ``tax_exempt``."""
    code = tax_code_of(line, legacy_basis(lines, tax_lines) if _get(line, "tax_exempt") is None else ())
    return "" if code == CODE_NONE else code


def tax_learned(line: Any) -> bool:
    """Whether a line's code was taken from what AP approved on the vendor's similar lines (``LEARNED_NOTE``)."""
    return LEARNED_NOTE in str(_get(line, "reasoning_justification") or "")


# Who decided an approved line's code (``feedback.tax_source``, D-80), beside the code itself (``final_tax_code``):
# a person picked it (changed what was proposed, confirmed an inferred exemption, or added the line); approved as
# proposed for sure; or from an approval made before ``tax_exempt`` existed, whose exemption was only inferred: no
# vote, a contradiction all the same. No code is kept (no vote, no veto) for a learned code or a guess left as it
# was, or a bulk approval (nobody opened the invoice).
PICKED, APPROVED, LEGACY = "picked", "approved", "legacy"
KEPT, GUESSED_KEPT, BULK = "kept", "guess", "bulk"
VOTING = (PICKED, APPROVED, "")  # "": a row written without it (imported, tests): approved as proposed
# Words that say what a line is not: "Non-prescription drugs" is not "Prescription drugs".
NEGATIONS = frozenset({"non", "not", "no", "without", "sans", "excluding", "except"})
# Words that join others and say nothing of the supply (numbers and units go too: "labour, 4 h" is "labour 2 h").
_FILLER = frozenset({"a", "an", "and", "the", "of", "for", "with", "per", "each", "to", "on", "in", "at", "by",
                     "de", "du", "des", "la", "le", "les", "et", "en", "au", "aux", "pour"})  # fmt: skip
# Descriptions that say nothing of what was supplied: never learned from, never a line a code is learned for.
GENERIC_WORDS = frozenset({
    "misc", "miscellaneous", "item", "items", "service", "services", "goods", "product", "products", "sundry",
    "sundries", "other", "others", "various", "general", "supplies", "supply", "material", "materials", "part",
    "parts", "charge", "charges", "fee", "fees", "expense", "expenses", "line", "order", "purchase", "sale", "sales",
    "unit", "units", "divers", "article", "articles", "produit", "produits", "biens", "frais", "autre", "autres",
    "fournitures", "work", "job", "invoice", "total", "amount", "qty", "quantity", "description",
})  # fmt: skip


def tax_words(text: str) -> frozenset[str]:
    """The words of a line's description that say which supply it is, for its tax: lower case, no accents, no numbers
    or units, no joining words; every other word kept ("service", "non", "chocolate": they make it another supply)."""
    from ..core.memory import _normalise

    return frozenset(w for w in _normalise(text).split()
                     if w not in _FILLER and not any(c.isdigit() for c in w) and len(w) > 1)  # fmt: skip


def generic(words: frozenset[str]) -> bool:
    """Whether a description says nothing of what was supplied ("Misc", "Services", "Goods"...)."""
    return not words or words <= GENERIC_WORDS


def _exemption(code: str) -> bool:
    """A code that says a tax is not due (exempt, zero-rated, out of scope, "GST~PST"): learned only from lines worded
    the same."""
    return EXEMPT_MARK in code or code in CODE_STATUSES


def supply_province(coding: Any, ours: str = "") -> str:
    """Where an invoice is supplied, for its learned codes: its ship-to, else our province (Settings), else the
    vendor's, as ``offline_coder.code_from_capture`` works it out; "" when none is known."""
    from .tax import PROVINCES

    for province in (_get(coding, "ship_to_province"), ours, _get(coding, "supplier_province")):
        province = str(province or "").strip().upper()
        if province in PROVINCES:
            return province
    return ""


def tax_vote(
    suggestion: Any,
    line: Any,
    proposed: Sequence[Any] = (),
    proposed_taxes: Iterable[Any] = (),
    lines: Sequence[Any] = (),
    tax_lines: Iterable[Any] = (),
    *,
    bulk: bool = False,
) -> tuple[str, str]:
    """(``final_tax_code``, ``tax_source``) of an approved line: its code and who decided it, or "" for the code when
    the approval is no vote on it: a code learned or guessed and left as it was (it would only count itself again,
    and a mistake would entrench itself), or a bulk approval (B-764). ``suggestion``: the line as proposed (None: a
    person added it), ``proposed`` and ``proposed_taxes`` its invoice's lines and tax lines; ``lines`` and
    ``tax_lines``: the approved invoice's."""
    code = approved_code(line, lines, tax_lines)
    if not code:
        return "", ""
    if bulk:
        return "", BULK
    if _get(line, "tax_exempt") is None and EXEMPT_MARK in code:
        return code, LEGACY  # an exemption read from a line stored before tax_exempt: inferred, not confirmed
    was = approved_code(suggestion, proposed, proposed_taxes) if suggestion is not None else None
    if was != code or _CONFIRMED_RE.search(str(_get(line, "reasoning_justification") or "")):
        return code, PICKED
    if tax_learned(suggestion):
        return "", KEPT
    if tax_guessed(suggestion) or exemption_to_confirm(suggestion):
        return "", GUESSED_KEPT
    return code, APPROVED


def uncollected(province: str, supplier_province: str | None, pst_number: str | None, qst_number: str | None,
                tax_lines: Iterable[Any]) -> list[str]:  # fmt: skip
    """The provincial taxes (of "PST", "QST") a vendor does not collect on an invoice supplied in ``province``: it is
    out of the province of supply (B-715), or prints no PST (QST) number and charges none of it (B-719: a number not
    printed says nothing when the vendor charges the tax on other lines). A number ``None``: not known, says nothing.
    The PST is looked at where the province is not known too."""
    regime = REGIME.get(province, frozenset())
    away = bool(supplier_province) and supplier_province != province
    out = []
    for tax_type, number in (("PST", pst_number), ("QST", qst_number)):
        if tax_type not in regime and (regime or tax_type == "QST"):
            continue
        charged = any(_get(t, "tax_type") == tax_type and abs(float(_get(t, "tax_amount") or 0)) > 0.004
                      for t in tax_lines or ())  # fmt: skip
        if away or (number is not None and not str(number).strip() and not charged):
            out.append(tax_type)
    return out


def invoice_uncollected(coding: Any, province: str) -> list[str]:
    """``uncollected`` of an invoice (a dict or an ``InvoiceCoding``) supplied in ``province``."""
    return uncollected(province, str(_get(coding, "supplier_province") or ""),
                       str(_get(coding, "pst_registration_number") or ""),
                       str(_get(coding, "qst_registration_number") or ""), _get(coding, "tax_lines") or [])  # fmt: skip


def learned_codes(lines: Sequence[Any], feedback: Iterable[dict[str, Any]], vendor: str, province: str,
                  uncollected: Iterable[str] = (), key: str | None = None) -> list[str]:  # fmt: skip
    """For each line, the tax code the vendor's history agrees on, else "" (D-70, with the safety rules of D-80).

    * Only rows of this vendor supplied in the same ``province`` (``tax_province``): a BC exemption says nothing of a
      Manitoba supply; none at all when the place of supply is unknown. And only rows where the vendor collected the
      provincial taxes as it does now (``tax_uncollected``, ``uncollected``): an exemption approved while it charged
      PST on other lines says nothing once it no longer collects it (B-760).
    * Lines worded alike: the same words (``tax_words``) for a code that says a tax is not due (exempt, zero-rated,
      "GST~PST"); words alike (the coding memory's similarity) for a taxed code, the same negations ("non",
      "not") on both. Never a generic description ("Misc", "Services").
    * Votes: a person's pick on review is enough alone; approvals as proposed must be of at least ``MIN_AGREEING``
      different invoices approved on different days. A learned code or a guess kept, or a bulk approval, is no vote
      (no code recorded); an approval made before exemptions were recorded (``LEGACY``) is no vote.
    * One contradiction (another code on a line worded alike, whoever approved it) and the line is worked out from
      the tax charged again."""
    from ..core.memory import VENDOR_LINE_SIMILARITY, similarity, vendor_key

    key = key or vendor_key(vendor or "")  # ``key``: the vendor's own, of two whose names read alike (B-1021)
    province = str(province or "").strip().upper()
    context = "+".join(ordered(uncollected))
    past = [r for r in feedback or () if key and province and r.get("vendor_key") == key and r.get("final_tax_code")
            and str(r.get("tax_province") or "").strip().upper() == province
            and "+".join(ordered(str(r.get("tax_uncollected") or "").split("+"))) == context
            and r.get("tax_source", "") not in (KEPT, GUESSED_KEPT, BULK)]  # fmt: skip
    if not past:
        return [""] * len(lines)
    seen = [(tax_words(r.get("description") or ""), r) for r in past]
    seen = [(w, r) for w, r in seen if not generic(w)]
    out = []
    for li in lines:
        words = tax_words(str(_get(li, "description") or ""))
        if generic(words):
            out.append("")
            continue
        near = [(w, r) for w, r in seen if w == words or similarity(words, w) >= VENDOR_LINE_SIMILARITY]
        codes = {str(r["final_tax_code"]) for _, r in near}
        if len(codes) != 1:  # none, or a contradiction
            out.append("")
            continue
        code = next(iter(codes))
        strict, negations = _exemption(code), words & NEGATIONS
        voters = [r for w, r in near if (w == words if strict else w & NEGATIONS == negations)
                  and r.get("tax_source", "") in VOTING]  # fmt: skip
        invoices = {r.get("invoice_id") or ("row", r.get("id")) for r in voters}
        days = {str(r.get("created_at") or "")[:10] for r in voters}
        picked = any(r.get("tax_source") == PICKED for r in voters)
        out.append(code if picked or (len(invoices) >= MIN_AGREEING and len(days) >= MIN_AGREEING) else "")
    return out


def tax_guessed(line: Any) -> bool:
    """Whether a line's code was worked out from several possible sets of lines (``GUESS_NOTE``), to check."""
    return GUESS_NOTE in str(_get(line, "reasoning_justification") or "")


__all__ = [
    "CANADIAN_TAX_TYPES", "CODE_EXEMPT", "CODE_NONE", "CODE_OUT", "CODE_ZERO", "EXEMPT", "OUT_OF_SCOPE", "TAX_STATUSES",
    "ZERO_RATED", "all_labels", "apply_tax_code", "code_for", "code_label", "learned_codes", "options", "parse_code",
    "parse_legend", "prefill", "read_flags", "solve", "tax_code_of", "tax_learned",
]  # fmt: skip
