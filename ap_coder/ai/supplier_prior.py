"""The supplier prior (D-212): which account strings a supplier's lines went to, counted, and how alike a new line is
to each. It is what AP Coder knows of a supplier before anyone approved an invoice of it here: last year's Transaction
Journal (``core/store/journal.py``), to which the vendor's own approved lines in AP Coder are added as they come.

``prior_for(store, vendor)`` finds the supplier and counts, for each account string (business unit +
object.subsidiary): the lines, the vouchers, the share, the latest G/L date and the commonest shapes of the
department's remark (``remark_shape``: months, quarters, years and long numbers taken out); and for the supplier, the
share of its vouchers that have one coding line and the business units each object.subsidiary was used on.

How the supplier is found. By its JD Edwards number when the vendor list gives the vendor one (its ERP ID): that
number alone, and a number the journal does not have is a supplier not in last year's journal, never looked up by
name. Else by the name as the journal prints it, cut at 26 characters (``NAME_CUT``), both names read without case,
accents or punctuation and "&" as "and":

* a journal name shorter than the cut is whole: it is the vendor's when the two read the same, legal forms at the end
  aside (``memory.vendor_key``: "NORTHERN PAPER LTD." is "Northern Paper Inc."), never when one only starts the
  other ("NORTHERN PAPER" is not "Northern Paper Recycling");
* a journal name as long as the cut may be cut: it is the vendor's when the vendor's name starts with it
  ("RAPIDWAY COURIER CANADA LT" starts "Rapidway Courier Canada Ltd.");
* a whole-name match wins over a cut one, and when two supplier numbers of the journal still fit, neither is taken
  (``Prior.note`` says so): two suppliers whose names start with the same 26 characters cannot be told apart by name.

What counts for how much. A coding line of the journal counts 1 (``JOURNAL_WEIGHT``; so does a line of imported past
coding or one approved in bulk, which nobody looked at); a line a person approved here as proposed counts 3
(``APPROVAL_WEIGHT``); a line a person coded or changed here counts 5 (``CORRECTION_WEIGHT``): a correction outweighs
history. An account's ``share`` is its weight over the supplier's. The remarks AP approved here are counted with the
journal's the same way (``_Counted.remarked``, D-213): one a person typed counts 5, one a person approved as
suggested 3, one nobody looked at 1, so an account's commonest remark (``AccountUse.remarks``) follows what AP types.

``rank(prior, line_text, reference)`` orders the supplier's accounts for one invoice line: the log of the share, plus
how alike the line is to the remarks AP wrote for that account, to the lines AP approved to it here, and to the
account's own name. Each ``Candidate`` says why in plain words, with the counts.

Priors are kept in each process (``_KEPT``, bounded by bytes) while ``Store.prior_stamp`` holds. A database with no
journal loaded has no prior (``EMPTY``): nothing of this is used there.
"""

from __future__ import annotations

import math
import re
import threading
import weakref
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from ..core.caches import MB, Bounded
from ..core.chart import clean, code_of, join_account
from ..core.memory import ACCEPTED, CODED, CORRECTED, _fold, _normalise, vendor_key
from ..core.reference_data import UNASSIGNED, ReferenceData
from ..core.schema import decided
from ..core.store.journal import supplier_key
from .line_match import keys, similarity

JOURNAL_WEIGHT = 1.0  # a coding line of last year's journal (or of imported past coding, or approved in bulk)
APPROVAL_WEIGHT = 3.0  # a line a person approved in AP Coder as it was proposed
CORRECTION_WEIGHT = 5.0  # a line a person coded or changed in AP Coder: a correction outweighs history
WEIGHTS = {ACCEPTED: APPROVAL_WEIGHT, CORRECTED: CORRECTION_WEIGHT, CODED: CORRECTION_WEIGHT}
# Sure enough to code a line with no one picking (``Prior.sure``; measured with ``bench coding --journal``, D-212):
SURE_SHARE = 0.90  # one account has this share of the supplier's lines...
SURE_LINES = 3  # ...and the supplier has this many lines at least
# ``rank``: the log of the share plus these times each likeness (0 to 1).
W_REMARK = 3.0  # to the remarks AP wrote for the account
W_NAME = 2.0  # to the account's own name
W_LINE = 4.0  # to the invoice lines AP approved to the account here (the supplier's own wording)
NAME_CUT = 26  # characters of a supplier's name the journal prints
REMARKS_KEPT = 3  # shapes of remark kept for an account, the commonest
TEXTS_KEPT = 24  # different remarks (and approved lines) of an account a new line is compared with
SAID_ALIKE = 0.2  # a likeness from which the words are named in the reason
DISTINCT = 3  # a word the names of more accounts (or business units) than this have says little of any one of them

_MONTHS = (
    "january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|"
    "aug|sept|sep|oct|nov|dec|janvier|fevrier|mars|avril|mai|juin|juillet|aout|septembre|octobre|novembre|decembre"
)
_PERIOD = re.compile(
    rf"(?<![a-z0-9])(?:(?:{_MONTHS})(?![a-z])\.?(?:\s*[/'-]?\s*(?:20)?\d\d(?!\d))?|q[1-4](?![a-z0-9])|"
    r"(?:19|20)\d\d(?!\d)|\d{5,})"
)


def remark_shape(remark: Any) -> str:
    """A remark without what changes from one voucher to the next: lower case, the month ("Jul", "July 24"), the
    quarter ("Q3"), the year and numbers of five digits or more taken out, the rest as written: "KT Jul.cellphone"
    and "KT Aug.cellphone" are both "kt cellphone"; the office a remark ends with stays ("janitorial 410 elm")."""
    text = _PERIOD.sub(" ", _fold(str(remark or "")))
    return " ".join(re.sub(r"[^0-9a-z&/#-]+", " ", text).split()).strip(" -/&#")


def _plain(name: Any) -> str:
    """A name as two are compared: no case, accents or punctuation, "&" as "and"."""
    return _normalise(_fold(str(name or "")).replace("&", " and "))


@dataclass(frozen=True)
class Remark:
    """One shape of the remarks AP wrote for an account: how many lines, and the latest one as written."""

    shape: str
    lines: int
    example: str
    typed: bool = False  # a person typed it here for a line approved (D-213)


@dataclass(frozen=True)
class AccountUse:
    """How much of a supplier's coding went to one account string."""

    account: str  # "0021061010.7340.2210"; the GL code alone when the line was approved without a business unit
    business_unit: str
    code: str  # "7340.2210"
    lines: int  # the journal's coding lines and the lines approved here
    vouchers: int  # the journal's vouchers and the invoices approved here
    journal_lines: int
    approved: int  # of ``lines``, those a person approved here (as proposed, coded or changed)
    weight: float
    share: float  # ``weight`` over the supplier's
    last_date: str  # the latest G/L date or approval, YYYY-MM-DD
    remarks: tuple[Remark, ...] = ()  # the commonest shapes of AP's remark, ``REMARKS_KEPT`` at most
    remarked: int = 0  # the lines that have a remark: the journal's and the ones approved here with one
    remark_keys: tuple[frozenset[str], ...] = ()  # the words of each different remark (``line_match.keys``)
    line_keys: tuple[frozenset[str], ...] = ()  # the words of each different invoice line approved to it here
    # Each of those lines: (its words, what it counts for, the id of its latest lesson), for ``Prior.sure``.
    approved_lines: tuple[tuple[frozenset[str], float, int], ...] = ()
    newest: int = 0  # the id of the latest lesson of a line a person approved to it here (0: none)


@dataclass(frozen=True)
class Prior:
    """What is known of how one supplier is coded. False when it has no line to go by."""

    journal: bool = False  # a journal is loaded (without one there is no prior at all)
    supplier_number: str = ""  # the journal's supplier it was counted from ("" when not found)
    supplier_name: str = ""  # as the journal prints it
    found_by: str = ""  # "number", "name", or "" when the journal has no such supplier
    note: str = ""  # why the supplier was not taken, when that needs saying (two suppliers print the name)
    accounts: tuple[AccountUse, ...] = ()  # the most used first
    lines: int = 0
    journal_lines: int = 0
    approved: int = 0
    vouchers: int = 0
    weight: float = 0.0
    one_line_share: float = 0.0  # the share of the supplier's journal vouchers that have one coding line
    units_of: Mapping[str, tuple[str, ...]] = field(default_factory=dict)  # object.subsidiary -> business units used
    latest_approved: str = ""  # the account string of the latest line a person approved here ("" for none)
    latest_approved_on: str = ""
    word_lines: Mapping[str, Mapping[str, int]] = field(default_factory=dict)  # a word -> {account: lines with it}
    word_weight: Mapping[str, float] = field(default_factory=dict)  # a word several accounts share counts for less

    def __bool__(self) -> bool:
        return bool(self.accounts)

    def use(self, account: str) -> AccountUse | None:
        return next((u for u in self.accounts if u.account == account), None)

    def sure(self, line_text: str | None = None) -> AccountUse | None:
        """The one account a line of this supplier may be coded to with no one picking, or None: it has
        ``SURE_SHARE`` of the supplier's lines (by weight), of ``SURE_LINES`` lines at least, and the latest line a
        person approved here did not go elsewhere. For the line ``line_text``, the lines a person approved here to
        another account that are not alike to it (under ``SAID_ALIKE``) are left out of both: a lunch coded by hand
        on a courier's invoice is evidence about the next lunch, not about the supplier's ordinary lines (B-2252)."""
        top = self.accounts[0] if self.accounts else None
        if top is None or self.lines < SURE_LINES:
            return None
        line = None if line_text is None else keys(line_text)
        weight, latest = self.weight, 0  # (the latest lesson of a line approved to another account that counts)
        for use in self.accounts[1:]:
            lessons = {n: w for _, w, n in use.approved_lines}
            unlike = {n for k, _, n in use.approved_lines if line is not None and similarity(line, k) < SAID_ALIKE}
            weight -= sum(lessons[n] for n in unlike)
            latest = max([latest, *(n for n in (use.newest, *lessons) if n not in unlike)])
        if top.weight / weight < SURE_SHARE or latest > top.newest:
            return None
        return top

    def shares(self, most: int = 4) -> str:
        """The accounts' shares, the largest first: "60% / 25% / 15%"."""
        return " / ".join(f"{100 * u.share:.0f}%" for u in self.accounts[:most])

    def total_said(self) -> str:
        """How many lines it is counted from: "24 lines last year", "27 lines (24 last year, 3 approved here)", "1
        line last year"."""
        if self.approved and self.journal_lines:
            return f"{self.lines} lines ({self.journal_lines} last year, {self.approved} approved here)"
        lines = "1 line" if self.lines == 1 else f"{self.lines} lines"
        return f"{lines} {'approved here' if self.approved else 'last year'}"


EMPTY = Prior()


@dataclass
class Candidate:
    """One account offered for a line. ``account`` is the whole string, ``reason`` says why in plain words."""

    account: str
    business_unit: str
    code: str
    score: float = 0.0
    reason: str = ""
    source: str = ""  # vendor, supplier, others, words (or master: the vendor master's default account)
    lines: int = 0  # the lines behind it...
    of: int = 0  # ...of this many of the supplier's (0: not counted that way)
    share: float = 0.0
    guess: bool = False  # nothing says which business unit: the home one, for a person to confirm
    auto: bool = False  # strong enough to code the line with no one picking
    check: str = ""  # why a person must check it, said plainly
    name: str = ""  # the account's name
    example: str = ""  # a remark AP usually wrote for it, as written
    reasons: list[str] = field(default_factory=list)

    def decided(self) -> dict[str, Any]:
        """What decided a line coded to this candidate with no one picking (``LineItem.decided_by``): where the
        evidence came from, with the supplier's counts when it has them. The vendor's usual account is this
        vendor's own approvals."""
        source = "vendor" if self.source == "usual" else self.source
        counts = {"lines": self.lines, "of": self.of, "share": round(self.share, 4)} if self.of else {}
        return decided(source, usual=True if self.source == "usual" else None, **counts)

    def kept(self) -> dict[str, Any]:
        """The candidate as a line keeps it (``LineItem.candidates``): plain values only."""
        out: dict[str, Any] = {"account": self.account, "business_unit": self.business_unit, "code": self.code,
                               "name": self.name, "reason": self.reason, "source": self.source}  # fmt: skip
        if self.of:
            out.update(lines=self.lines, of=self.of, share=round(self.share, 4))
        if self.guess:
            out["guess"] = True
        return out


# --- Finding the supplier, and counting --------------------------------------------------------------------------

# What each process keeps (D-195): a supplier's prior by vendor, the journal's names by database, and the words of
# each chart's account names; each while its stamp holds, the ones used longest ago let go first.
_KEPT = Bounded("supplier priors", 6 * MB)
_LOCK = threading.Lock()


def _kept(key: tuple[Any, ...], stamp: Any, make: Any) -> Any:
    with _LOCK:
        hit = _KEPT.get(key)
    if hit is not None and (hit[0] is stamp or hit[0] == stamp):
        return hit[1]
    made = make()
    with _LOCK:
        _KEPT[key] = (stamp, made)
    return made


def _vendor(store: Any, vendor: Any) -> tuple[str, str, str]:
    """(the name, the vendor's key, its supplier number or "") of ``vendor``: the vendor list's row, or the name an
    invoice prints. Of two vendors whose names read alike, the one whose name is exactly the name printed."""
    if isinstance(vendor, Mapping):
        name = str(vendor.get("display_name") or vendor.get("vendor_name") or "")
        return name, str(vendor.get("vendor_key") or vendor_key(name)), str(vendor.get("erp_id") or "").strip()
    name = str(vendor or "")
    key = vendor_key(name)
    if not key:
        return name, "", ""
    rows = store.vendors_named(key)
    if not rows:
        found = store.find_vendor(name)  # (a name AP corrected to this vendor's, a person's name written another way)
        rows = [found] if found else []
    if not rows:
        return name, key, ""
    exact = [r for r in rows if _plain(r.get("display_name")) == _plain(name)]
    row = (exact or [r for r in rows if r["vendor_key"] == key] or rows)[0]
    return name, str(row["vendor_key"]), str(row.get("erp_id") or "").strip()


@dataclass(frozen=True)
class _Names:
    """The journal's suppliers by name: whole names by their key, and the names long enough to have been cut."""

    whole: Mapping[str, tuple[tuple[str, str], ...]]  # vendor key -> ((supplier number, name as printed), ...)
    cut: tuple[tuple[str, str, str], ...]  # (the name read plainly, supplier number, name as printed)

    def __bool__(self) -> bool:
        return bool(self.whole or self.cut)


def _journal_names(store: Any, journal_stamp: int) -> _Names:
    def read() -> _Names:
        whole: dict[str, dict[str, tuple[str, str]]] = {}
        cut = []
        for row in store.journal_supplier_names():
            number, name = str(row["supplier_number"]).strip(), str(row["supplier_name"] or "").strip()
            if not name:
                continue
            if len(name) >= NAME_CUT - 1:  # (25: the 26th character was a space, lost with the padding)
                cut.append((_plain(name), number, name))
            else:
                whole.setdefault(vendor_key(name), {})[row["supplier_key"]] = (number, name)
        return _Names({k: tuple(v.values()) for k, v in whole.items()}, tuple(cut))

    return _kept(("names", str(store.path)), journal_stamp, read)


def suppliers_by_name(names: _Names, name: str) -> tuple[list[tuple[str, str]], bool, str]:
    """(the journal's suppliers that go by ``name``, each (supplier number, the name printed); whether their whole
    name reads as it; a note). By the rules at the top of this module: every supplier whose whole name reads as
    ``name``, else the one supplier whose cut name starts it; none, and the note says why, when two cut names do."""
    key, plain = vendor_key(name), _plain(name)
    found = list(names.whole.get(key, ()))
    # A name as long as the cut is also whole when nothing was cut off it but a legal form ("... LTD").
    found += [(number, printed) for text, number, printed in names.cut if vendor_key(printed) == key]
    if found:
        return list(dict.fromkeys(found)), True, ""
    found = [(number, printed) for text, number, printed in names.cut if plain.startswith(text)]
    numbers = sorted({supplier_key(number) for number, _ in found})
    if len(numbers) > 1:
        return [], False, (f"{len(numbers)} suppliers of last year's journal print the start of this name "
                           f"({', '.join(numbers[:4])}): give the vendor its supplier number")  # fmt: skip
    return found[:1], False, ""


def _journal_lines(store: Any, names: _Names, name: str, number: str) -> tuple[list[dict[str, Any]], str, str]:
    """(the supplier's coding lines in the journal, the oldest first; how it was found: "number", "name" or ""; a
    note). Under the vendor's supplier number when it has one, and under any other number the journal prints with
    the same whole name: a supplier set up twice is one vendor in the vendor list, which keeps one number."""
    by_name, whole, note = suppliers_by_name(names, name)
    numbers = [number] if number else []
    if whole or not number:
        numbers += [n for n, _ in by_name]
    read: dict[str, list[dict[str, Any]]] = {}
    for n in numbers:
        if supplier_key(n) not in read:
            read[supplier_key(n)] = store.journal_lines_for(n)
    used = [n for n, lines in read.items() if lines]
    lines = sorted((line for found in read.values() for line in found),
                   key=lambda line: (line["gl_date"], line["voucher_id"], line["n"]))  # fmt: skip
    if len(used) > 1:
        note = f"{len(used)} supplier numbers of last year's journal go by this name ({', '.join(used[:4])})"
    found_by = "" if not lines else "number" if number and read.get(supplier_key(number)) else "name"
    return lines, found_by, note


class _Count:
    """One account's use while it is counted."""

    def __init__(self) -> None:
        self.journal_lines = self.approved = self.other = self.invoices = self.newest = 0
        self.weight = 0.0
        self.vouchers: set[Any] = set()
        self.last = ""
        self.shapes: Counter[str] = Counter()
        self.example: dict[str, str] = {}
        self.kept: Counter[str] = Counter()  # the remarks approved here, by shape: lines...
        self.kept_weight: Counter[str] = Counter()  # ...and what they count for
        self.typed: set[str] = set()  # the shapes a person typed here
        self.line_texts: dict[str, list[float]] = {}  # a line a person approved: [what it counts for, its lesson]


class _Counted:
    """A supplier's coding while it is counted: each (business unit, GL code)'s use, and what is said of the supplier
    as a whole."""

    def __init__(self) -> None:
        self.uses: dict[tuple[str, str], _Count] = {}
        self.vouchers: dict[Any, int] = {}  # a journal voucher -> its coding lines
        self.words: dict[str, Counter[str]] = {}  # a word -> {account: lines with it}
        self.latest: tuple[int, str, str] = (0, "", "")  # the latest line a person approved: (id, account, day)

    def said(self, pair: tuple[str, str], text: Any, lines: int = 1) -> None:
        for word in keys(str(text or "")):
            self.words.setdefault(word, Counter())[join_account(*pair)] += lines

    def journal(self, lines: list[dict[str, Any]]) -> None:
        """The journal's coding lines of the supplier, the oldest first (``Store.journal_lines_for``)."""
        for line in lines:
            pair = (clean(line["business_unit"]), code_of(line["object"], line["subsidiary"]))
            use = self.uses.setdefault(pair, _Count())
            use.journal_lines += 1
            use.weight += JOURNAL_WEIGHT
            use.vouchers.add(line["voucher_id"])
            use.last = max(use.last, str(line["gl_date"] or ""))
            self.vouchers[line["voucher_id"]] = int(line["voucher_coding_lines"] or 1)
            shape = remark_shape(line["remark"])
            if shape:
                use.shapes[shape] += 1
                use.example[shape] = str(line["remark"]).strip()  # (the latest one as written)
                self.said(pair, line["remark"])

    def approved(self, counted: dict[str, Any]) -> None:
        """The vendor's lessons, counted (``Store.approved_coding``)."""

        def pair_of(row: dict[str, Any]) -> tuple[str, str]:
            unit = clean(row["final_cc"])
            return ("" if unit == UNASSIGNED else unit, clean(row["final_gl"]))

        for row in counted["accounts"]:
            pair = pair_of(row)
            use = self.uses.setdefault(pair, _Count())
            n, by_person = int(row["lines"]), row["outcome"] in WEIGHTS
            use.weight += n * WEIGHTS.get(row["outcome"], JOURNAL_WEIGHT)
            use.approved += n if by_person else 0
            use.other += 0 if by_person else n
            use.invoices += int(row["invoices"] or 0)
            use.last = max(use.last, str(row["last_at"] or "")[:10])
            use.newest = max(use.newest, row["newest"] if by_person else 0)
            if by_person and row["newest"] > self.latest[0]:
                self.latest = (row["newest"], join_account(*pair), str(row["last_at"] or "")[:10])
        for row in counted["descriptions"]:
            pair = pair_of(row)
            use = self.uses.get(pair)
            if use is None:
                continue
            self.said(pair, row["description"], int(row["lines"]))
            if row["outcome"] in WEIGHTS:
                had = use.line_texts.setdefault(row["description"], [0.0, 0])
                had[0] += int(row["lines"]) * WEIGHTS[row["outcome"]]
                had[1] = max(had[1], row["newest"])
                continue
            shape = remark_shape(row["description"])  # imported past coding: the department's words, as a remark is
            if shape:
                use.shapes[shape] += int(row["lines"])
                use.example.setdefault(shape, str(row["description"]).strip())
        self.remarked(counted.get("remarks") or [], pair_of)

    def remarked(self, rows: list[dict[str, Any]], pair_of: Any) -> None:
        """The remarks approved here with the vendor's lines (the newest first), counted with the journal's: one a
        person typed counts as a correction does, one a person approved as suggested as an approval, one nobody
        looked at (approved in bulk) as a line of the journal. The latest wording is the shape's example."""
        for row in reversed(rows):
            use, shape = self.uses.get(pair_of(row)), remark_shape(row["final_remark"])
            if use is None or not shape:
                continue
            by_person, typed = row["outcome"] in WEIGHTS, row["remark_from"] == "person"
            weight = CORRECTION_WEIGHT if typed else APPROVAL_WEIGHT if by_person else JOURNAL_WEIGHT
            use.kept[shape] += int(row["lines"])
            use.kept_weight[shape] += int(row["lines"]) * weight
            use.example[shape] = str(row["final_remark"]).strip()
            if typed:
                use.typed.add(shape)


def _use(pair: tuple[str, str], u: _Count, weight: float) -> AccountUse:
    shapes = u.shapes.most_common(TEXTS_KEPT)
    said = u.shapes + u.kept  # the journal's remarks and the ones approved here, the heaviest shape first
    usual = sorted(said, key=lambda shape: u.shapes[shape] * JOURNAL_WEIGHT + u.kept_weight[shape], reverse=True)
    lines: dict[frozenset[str], list[float]] = {}  # the lines a person approved to it, by their words
    for text, (counts, lesson) in list(u.line_texts.items())[:TEXTS_KEPT]:
        if k := keys(text):
            had = lines.setdefault(k, [0.0, 0])
            had[0], had[1] = had[0] + counts, max(had[1], lesson)
    return AccountUse(
        account=join_account(*pair), business_unit=pair[0], code=pair[1],
        lines=u.journal_lines + u.approved + u.other, vouchers=len(u.vouchers) + u.invoices,
        journal_lines=u.journal_lines, approved=u.approved, weight=u.weight, share=u.weight / weight,
        last_date=u.last, remarks=tuple(Remark(shape, said[shape], u.example[shape], shape in u.typed)
                                        for shape in usual[:REMARKS_KEPT]), remarked=sum(said.values()),
        remark_keys=tuple(dict.fromkeys(k for shape, _ in shapes if (k := keys(shape)))),
        line_keys=tuple(lines), approved_lines=tuple((k, counts, int(lesson)) for k, (counts, lesson) in lines.items()),
        newest=u.newest,
    )  # fmt: skip


def _built(number: str, name: str, found_by: str, note: str, lines: list[dict[str, Any]],
           approved: dict[str, Any]) -> Prior:  # fmt: skip
    """The prior from a supplier's journal lines and the vendor's lessons counted."""
    counted = _Counted()
    counted.journal(lines)
    counted.approved(approved)
    weight = sum(u.weight for u in counted.uses.values())
    if weight <= 0:
        return Prior(journal=True, supplier_number=number, supplier_name=name, found_by=found_by, note=note)
    accounts = sorted((_use(pair, u, weight) for pair, u in counted.uses.items()),
                      key=lambda a: (a.weight, a.last_date), reverse=True)  # fmt: skip
    units: dict[str, Counter[str]] = {}
    for a in accounts:
        if a.business_unit:
            units.setdefault(a.code, Counter())[a.business_unit] += a.lines
    vouchers = counted.vouchers
    return Prior(
        journal=True, supplier_number=number, supplier_name=name, found_by=found_by, note=note,
        accounts=tuple(accounts), lines=sum(a.lines for a in accounts),
        journal_lines=sum(a.journal_lines for a in accounts), approved=sum(a.approved for a in accounts),
        vouchers=len(vouchers) + sum(u.invoices for u in counted.uses.values()), weight=weight,
        one_line_share=sum(1 for n in vouchers.values() if n == 1) / len(vouchers) if vouchers else 0.0,
        units_of={code: tuple(u for u, _ in c.most_common()) for code, c in units.items()},
        latest_approved=counted.latest[1], latest_approved_on=counted.latest[2],
        word_lines={w: dict(c) for w, c in counted.words.items()},
        word_weight={w: 1.0 / len(c) for w, c in counted.words.items() if len(c) > 1},
    )  # fmt: skip


def prior_for(store: Any, vendor: Any, always: bool = False) -> Prior:
    """The prior of the supplier ``vendor`` is: the vendor list's row of an invoice's vendor (``Store.find_vendor``),
    or the name the invoice prints. ``EMPTY`` when no journal is loaded, unless ``always`` (the caller codes to whole
    account strings: a chart of accounts was imported): then it is counted from the vendor's approved lines alone,
    and ``Prior.journal`` is False. A supplier the journal does not have still has a prior once lines of it were
    approved here; ``Prior.found_by`` and ``note`` say how it was looked for. Kept in this process until the
    journal, the vendor's lessons or the vendor list change. Shared: read it, don't change it."""
    name, key, number = _vendor(store, vendor)
    if not key:
        return EMPTY
    stamp = store.prior_stamp(key)
    if not stamp[0] and not always:  # no journal was ever loaded
        return EMPTY

    def make() -> Prior:
        names = _journal_names(store, stamp[0])
        if not names and not always:  # every journal was taken out again
            return EMPTY
        lines, found_by, note = _journal_lines(store, names, name, number) if names else ([], "", "")
        return replace(_built(str(lines[-1]["supplier_number"]).strip() if lines else "",
                              str(lines[-1]["supplier_name"]) if lines else "", found_by, note, lines,
                              store.approved_coding(key)), journal=bool(names))  # fmt: skip

    return _kept(("prior", str(store.path), key, number, "" if number else _plain(name), always), stamp, make)


# --- Ranking a line ----------------------------------------------------------------------------------------------


def _listed_names(reference: ReferenceData) -> Mapping[str, tuple[str, str]]:
    """{GL code: (its description, its description and category)} of the GL account list, worked out once for a list
    (kept while the list lives: by a weak reference, so the cache holds no chart)."""
    table = reference.chart_of_accounts
    kept_key = ("chart", id(table))
    with _LOCK:
        hit = _KEPT.get(kept_key)
    if hit is not None and hit[0]() is table:
        return hit[1]
    names = {row[table.key_column]: (str(row.get("description") or ""),
                                     " ".join(str(v) for k, v in row.items() if k != table.key_column))
             for row in table.rows}  # fmt: skip
    with _LOCK:
        _KEPT[kept_key] = (weakref.ref(table), names)
    return names


def account_name(reference: ReferenceData, business_unit: str, code: str) -> str:
    """An account's name as shown: its description on that business unit when a chart of accounts was imported, else
    the GL account list's ("" for a code the list does not have)."""
    own = reference.pairs.description(business_unit, code) if reference.pairs is not None and business_unit else ""
    return own or _listed_names(reference).get(code, ("", ""))[0]


def name_words(reference: ReferenceData, business_unit: str, code: str) -> frozenset[str]:
    """The words of an account's name a line is compared with: its description on that business unit when a chart of
    accounts was imported, and the GL account list's description and category (the title account above it, which
    says what a description cut at 30 characters no longer does)."""
    listed = _listed_names(reference).get(code, ("", ""))[1]
    own = reference.pairs.description(business_unit, code) if reference.pairs is not None and business_unit else ""
    return keys(f"{own} {listed}" if own else listed)


_LISTED = re.compile(r"\s*(?:[&/,;+]|\band\b|\bet\b)\s*", re.I)  # between the things an account's name lists


def _told(reference: ReferenceData) -> tuple[Mapping[str, tuple[str, ...]], Mapping[str, tuple[Any, ...]]]:
    """What the names of the GL account list say, for ``reads_like``: ({a telling word: the codes whose name has
    it}, {a code: (the words of its name, the things the name lists)}). A word tells when at most ``DISTINCT`` names
    have it ("Laptops", not "Fees"). What a name lists is the name before a dash ("Courier - Domestic" is a courier
    account), cut at "&", "/", "," and "and" ("Laptops & Desktops" lists two things), each as its words; a thing
    with no telling word is left out."""
    names = {code: name for code, (name, _) in _listed_names(reference).items()}
    words = {code: keys(name) for code, name in names.items()}
    common = Counter(word for found in words.values() for word in found)
    by_word: dict[str, list[str]] = {}
    told = {}
    for code, name in names.items():
        telling = {word for word in words[code] if common[word] <= DISTINCT}
        things = (keys(thing) for thing in _LISTED.split(name.partition(" - ")[0]))
        told[code] = (words[code], tuple(thing for thing in things if thing & telling))
        for word in telling:
            by_word.setdefault(word, []).append(code)
    return {word: tuple(codes) for word, codes in by_word.items()}, told


def reads_like(reference: ReferenceData, line_text: str) -> list[str]:
    """The GL codes of the GL account list whose name a line says, the name with most of the line's words first: the
    line has every word of one of the things the name lists ("Dell Latitude 5540 laptop" has the "Laptops" of
    "Laptops & Desktops", "Annual software subscription renewal" both words of "Software Subscriptions"), or says
    nothing but words of the name ("Consulting services - October" for "Management Consulting"); and one of those
    words is a telling one (``_told``). One word of two is not the name: "Ground shipment waybill" is not "Ground
    Transport", nor "Fuel surcharge" "Fleet Fuel", nor "Duties and brokerage" "Brokerage Fees". Singular and plural
    are one word (``line_match.keys``). The names are read once for a list, as ``_listed_names``."""
    table = reference.chart_of_accounts
    by_word, told = _kept(("told", id(table)), weakref.ref(table), lambda: _told(reference))
    line = keys(line_text)
    found = []
    for code in dict.fromkeys(code for word in sorted(line) for code in by_word.get(word, ())):
        words, things = told[code]
        if line <= words or any(thing <= line for thing in things):
            found.append((len(line & words), code))
    return [code for _, code in sorted(found, key=lambda f: -f[0])]


def units_named(reference: ReferenceData, line_text: str) -> dict[str, int]:
    """{a business unit a line names: how many words of its name the line has}: the line has every word of the
    unit's name, the words more than ``DISTINCT`` business units' names have aside ("Smartphone device balance - IT
    - Data Centre" names IT - Data Centre; "Prairie" is Prairie Administration where four units are an
    Administration). Of the business units with an account offered; none with no chart imported."""
    pairs = reference.pairs
    if pairs is None:
        return {}

    def read() -> dict[str, frozenset[str]]:
        names = {unit: keys(pairs.name_of(unit)) for unit in pairs.business_units()}
        common = Counter(word for found in names.values() for word in found)
        told = {unit: frozenset(w for w in found if common[w] <= DISTINCT) for unit, found in names.items()}
        return {unit: words for unit, words in told.items() if words}

    line = keys(line_text)
    told = _kept(("units", id(pairs)), weakref.ref(pairs), read)
    return {unit: len(words) for unit, words in told.items() if words <= line}


def _shared(line: frozenset[str], words: frozenset[str]) -> list[str]:
    return sorted(line & words)


def _why(prior: Prior, use: AccountUse, line: frozenset[str], remark: float, own: float, name: float,
         name_keys: frozenset[str]) -> list[str]:  # fmt: skip
    """Why this account is offered, in plain words with the counts: its share of the supplier's lines, then the
    words of the line that go with it."""
    said = [f"{use.lines} of this supplier's {prior.total_said()}"]
    best: tuple[float, int, int, str] | None = None
    for word in sorted(line):  # (of two words that say as much, the first in the alphabet: the same every time)
        counts = prior.word_lines.get(word)
        if counts and counts.get(use.account):
            n, of = counts[use.account], sum(counts.values())
            if best is None or (n / of, n) > best[:2]:
                best = (n / of, n, of, word)
    if best is not None and max(remark, own) >= SAID_ALIKE:
        said.append(f"{best[1]} of its {best[2]} “{best[3]}” lines")
    shared = _shared(line, name_keys)
    if shared and name >= SAID_ALIKE:
        said.append(("the word " if len(shared) == 1 else "the words ") + ", ".join(f"“{w}”" for w in shared[:3])
                    + " in the account's name")  # fmt: skip
    return said


def alike(prior: Prior, use: AccountUse, line_text: str, reference: ReferenceData) -> float:
    """How alike a line is to what is known of one of the supplier's accounts: the most of its likeness to a remark
    AP wrote for it, to a line AP approved to it here and to the account's name (the three ``rank`` scores)."""
    line = keys(line_text)
    if not line:
        return 0.0
    return max(*(similarity(line, k, prior.word_weight) for k in use.remark_keys),
               *(similarity(line, k) for k in use.line_keys),
               similarity(line, name_words(reference, use.business_unit, use.code)))  # fmt: skip


def rank(prior: Prior, line_text: str, reference: ReferenceData) -> list[Candidate]:
    """The supplier's accounts for one invoice line, the likeliest first: each scored by the log of its share plus
    ``W_REMARK`` times how alike the line is to a remark AP wrote for it, ``W_LINE`` times how alike it is to a line
    AP approved to it here, and ``W_NAME`` times how alike it is to the account's name (``line_match.similarity``; a
    word the supplier's remarks use for several accounts counts for less). Among equals, the account used more, then
    the one used last. Nothing is checked against the chart here (``suggest.candidates`` does)."""
    line = keys(line_text)
    scored = []
    for use in prior.accounts:
        name_keys = name_words(reference, use.business_unit, use.code)
        remark = max((similarity(line, k, prior.word_weight) for k in use.remark_keys), default=0.0) if line else 0.0
        own = max((similarity(line, k) for k in use.line_keys), default=0.0) if line else 0.0
        name = similarity(line, name_keys) if line else 0.0
        score = math.log(use.share) + W_REMARK * remark + W_NAME * name + W_LINE * own
        scored.append((score, use, _why(prior, use, line, remark, own, name, name_keys)))
    scored.sort(key=lambda found: (found[0], found[1].weight, found[1].last_date), reverse=True)
    return [
        Candidate(account=use.account, business_unit=use.business_unit, code=use.code, score=score,
                  reason="; ".join(why), source="supplier", lines=use.lines, of=prior.lines, share=use.share,
                  example=use.remarks[0].example if use.remarks else "", reasons=why)
        for score, use, why in scored
    ]  # fmt: skip
