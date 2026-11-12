"""The remark of a G/L line (D-213): the 30 characters AP keys with each line of a JD Edwards voucher, in its own
short words ("Courier charges", "KT May.cellphone", "coffee supplies 3-1-204": often ending in the office it is for).

``suggest_remark(line, invoice, prior, account, reference)`` gives (the remark, why in plain words):

a. the supplier's commonest remark *shape* for that account (``supplier_prior.remark_shape``: last year's journal
   and the remarks approved here, a person's typed one counting most), in its latest wording, with what changes from
   one voucher to the next put back from this invoice (``with_period``): the month, the quarter and the year of its
   period (``period_of``: the month its lines name, else the invoice date), and its invoice number after "INV". The
   office the remark ends with stays as the supplier's usual one: free text, never looked up;
b. no remark for that account yet: the supplier's commonest shape overall, only when one shape is on most of its
   lines (``MOST``) and it has ``supplier_prior.SURE_LINES`` of them;
c. else from the invoice: the line's own description shortened to 30 characters at a word end (``shortened``:
   filler words and long numbers dropped first, a month kept), else the account's name.

Nothing is ever longer than ``REMARK_MOST`` and nothing is cut without saying: ``fit`` returns the text and whether
it was shortened. ``typed`` reads the review screen's remark cell (a person's remark is ``PERSON``, which no
suggestion replaces), ``fill_remarks`` gives each line of a coding its remark (the parts of a split line share the
line's), ``long_remarks`` finds the ones too long for JD Edwards (the check ``REMARK_LONG``).
"""

from __future__ import annotations

import datetime as dt
import re
import unicodedata
from collections import Counter
from collections.abc import Mapping
from typing import Any, NamedTuple

from ..ai.supplier_prior import SURE_LINES, AccountUse, Prior, Remark, account_name, prior_for
from ..core.chart import join_account, split_account
from ..core.memory import _fold
from ..core.reference_data import UNASSIGNED, ReferenceData

REMARK_MOST = 30  # characters of a remark JD Edwards keeps (F0911 GLEXR, the pay item's RPRMK)
SUPPLIER, INVOICE, ACCOUNT, PERSON = "supplier", "invoice", "account", "person"  # ``schema.REMARK_SOURCES``
CLEARED = " "  # the review screen's remark cell of a box a person emptied: no remark, theirs (B-2363, D-229)
MOST = 0.5  # (b): a supplier's one shape is its usual remark when it is on more than this share of its lines

_EN = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
       "November", "December")  # fmt: skip
_FR = ("janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre",
       "décembre")  # fmt: skip
# A month as written (no case, no accents) -> (its number, how it is written: "long", "short" or "fr"). The same
# words ``supplier_prior.remark_shape`` takes out of a remark.
_MONTH_OF: dict[str, tuple[int, str]] = {
    word: (n, how)
    for n, (en, fr) in enumerate(zip(_EN, _FR, strict=True), 1)
    for word, how in ((_fold(fr), "fr"), (en.lower(), "long"), (en[:3].lower(), "short"))  # ("may": short)
} | {"sept": (9, "short")}
_MONTH = "|".join(sorted({*_MONTH_OF, *_FR}, key=len, reverse=True))
_NOT_AFTER, _NOT_LETTER = r"(?<![^\W_])", r"(?![^\W\d_])"  # not right after a letter or digit; no letter next
_JOINS = "_/\\-\u2010\u2011\u2013\u2014"  # what joins a label to its number besides ":.#": "INV-54001", "INV_54001"
_LABEL_END = rf"\s*(?:[:.#{_JOINS}]\s*)*(?:no\.?|n°|#)?\s*(?:[:.#{_JOINS}]\s*)*"  # "INV.-54001", "INV-No-54001"
# What changes from one voucher to the next in a remark, as ``remark_shape`` takes it out: an invoice number after
# "INV" (five digits or more: "invoice 410 Elm" ends with an office), a month (with the year written after it), a
# quarter, a whole date (left as written: its month and day can't follow), a month as a number beside its year
# ("2025-11", "11/2025"; ``_month_number``), a year, a number of five digits or more. A number of letters and digits
# ("job 2025-07A", "A-11/2025") is none of them.
_VARYING = re.compile(
    rf"{_NOT_AFTER}(?:(?P<label>(?:invoice|inv|facture|fact|fac){_LABEL_END})(?P<labelled>\d{{5,}}[\w-]*)"
    rf"|(?P<month>{_MONTH}){_NOT_LETTER}(?:(?P<gap>\.?\s*[/'-]?\s*)(?P<month_year>(?:20)?\d\d)(?!\d))?"
    r"|(?P<q>q)(?P<quarter>[1-4])(?![^\W_])"
    r"|(?P<date>(?:19|20)\d\d[/-]\d\d?[/-]\d\d?|\d\d?[/-]\d\d?[/-](?:19|20)\d\d)(?!\w)"
    r"|(?P<ym_year>(?:19|20)\d\d)(?P<ym_sep>[/-])(?P<ym_month>\d\d?)(?![\w/-])"
    r"|(?<!\w[/-])(?P<my_month>\d\d?)(?P<my_sep>[/-])(?P<my_year>(?:19|20)\d\d)(?![\w/-])"
    rf"|(?P<year>(?:19|20)\d\d)(?!\d)|(?P<number>\d{{5,}}))",
    re.IGNORECASE,
)
# An invoice number's own label ("INV-55123", "Invoice #55123"): not repeated after the remark's "INV".
_OWN_LABEL = re.compile(rf"^(?:invoice|inv|facture|fact|fac){_LABEL_END}-?\s*(?=\d)", re.IGNORECASE)
_ACCOUNT_LABEL = re.compile(rf"(?:acct|account|a/c|compte|cust|customer|client|contract|contrat){_LABEL_END}$", re.I)
# A month an invoice line names: any month with a year after it ("Apr 30, 2026", "mai 2026", "Mar/26"), or a month
# written in full that is no other word ("may" and "mai" are).
_ALONE = "|".join(sorted({w for w in (*_MONTH_OF, *_FR) if _MONTH_OF[_fold(w)][1] != "short" and w != "mai"},
                         key=len, reverse=True))  # fmt: skip
_LINE_MONTH = re.compile(
    rf"{_NOT_AFTER}(?:(?P<dated>{_MONTH}){_NOT_LETTER}\.?(?:[\s\d,./–-]{{0,12}}?(?<!\d)(?P<y4>20\d\d)|\s*[/'-]\s*"
    rf"(?P<y2>\d\d))(?!\d)|(?P<alone>{_ALONE}){_NOT_LETTER})",
    re.IGNORECASE,
)
_LINE_QUARTER = re.compile(rf"{_NOT_AFTER}q([1-4])(?![^\W_])(?:\s*[/'-]?\s*(20\d\d)(?!\d))?", re.IGNORECASE)
# Words that say nothing in a remark (``shortened`` drops them first).
_FILLER = frozenset(
    "a an and as at by for from in of on or our per the this to with your "
    "au aux avec dans de des du en et la le les par pour sur un une votre".split()
)


# A part of a split line (``review.split_line``): its reason names the line it was split from and its share, the mark
# its description ends with ("Diesel - card 30419 (40%)").
_SPLIT = re.compile(r"^Split from line (\d+) \((.+?)\)\.")


class Period(NamedTuple):
    """The period an invoice is for: its year, month and quarter."""

    year: int
    month: int
    quarter: int


class Suggestion(NamedTuple):
    """A remark suggested for a line: the text, why in plain words, where it came from (``LineItem.remark_from``)."""

    text: str
    why: str
    source: str


NONE = Suggestion("", "", "")


def one_line(text: Any) -> str:
    """A remark as it is kept: one line, single spaces, accented letters as one character each."""
    return " ".join(unicodedata.normalize("NFC", str(text or "")).split())


def fit(text: Any) -> tuple[str, bool]:
    """(the remark in ``REMARK_MOST`` characters at most, whether it was shortened): the whole words that fit; a
    first word longer than that has no word end to cut at, and is cut at ``REMARK_MOST``."""
    text = one_line(text)
    if len(text) <= REMARK_MOST:
        return text, False
    kept = text[: REMARK_MOST + 1].rsplit(" ", 1)[0] if " " in text[: REMARK_MOST + 1] else text[:REMARK_MOST]
    return kept.rstrip(" ,;:-–/&"), True


def _get(thing: Any, name: str) -> Any:
    return thing.get(name) if isinstance(thing, Mapping) else getattr(thing, name, None)


def described(line: Any) -> str:
    """A line's description as the invoice prints it, on one line: a part of a split line without the split's mark."""
    return one_line(unsplit(line))


def unsplit(line: Any) -> str:
    """A line's description as it is, a part of a split line's without the split's mark (``review.split_line``'s
    "Diesel - card 30419 (40%)" is "Diesel - card 30419"): what the line memory learns of it (B-2519)."""
    text = str(_get(line, "description") or "")
    part = _SPLIT.match(str(_get(line, "reasoning_justification") or ""))
    return text.rstrip().removesuffix(f"({part[2]})").rstrip() if part else text


def _first_part(line: Any, coding: Any) -> Any:
    """The first part of the split line ``line`` is another part of (it keeps the line's number, and comes before
    it); None for a first part and for a line that is no part."""
    part = _SPLIT.match(str(line.reasoning_justification or ""))
    if not part or int(part[1]) == line.line_number:
        return None
    return next((li for li in coding.line_items if li.line_number == int(part[1])), None)


def _date(value: Any) -> dt.date | None:
    if isinstance(value, dt.date):
        return value
    try:
        return dt.date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _named(text: str, on: dt.date | None) -> tuple[int | None, int | None, int | None]:
    """(year, month, quarter) a line's words name, each None when they name none. A month with no year is the one
    nearest the invoice date ("December" on a January invoice is last year's), so is a quarter (by its middle month:
    "Q4" on a January invoice is last year's, B-2373)."""
    year = month = quarter = None
    for found in _LINE_MONTH.finditer(text):  # (of "January to March 2026", the last one)
        month = _MONTH_OF[_fold(found["dated"] or found["alone"])][0]
        year = int(found["y4"]) if found["y4"] else 2000 + int(found["y2"]) if found["y2"] else None
    if month and year is None and on is not None:
        year = _nearest(month, on)
    q = _LINE_QUARTER.search(text)
    if q:
        quarter, year = int(q[1]), int(q[2]) if q[2] else year
        if year is None and on is not None:
            year = _nearest(3 * quarter - 1, on)
    return year, month, quarter


def _nearest(month: int, on: dt.date) -> int:
    """The year of month ``month`` nearest the date ``on``."""
    return on.year - 1 if month > on.month + 6 else on.year + 1 if month < on.month - 6 else on.year


def period_of(line: Any, invoice: Any) -> Period | None:
    """The period a line is for: the month (or quarter) its description names, else the one the invoice's other
    lines name when they name only one, else the invoice date's. None when nothing says (no date was read)."""
    on = _date(_get(invoice, "invoice_date"))
    named = _named(one_line(_get(line, "description")), on)
    if not (named[1] or named[2]):
        others = {found for li in _get(invoice, "line_items") or []
                  if (found := _named(one_line(_get(li, "description")), on))[1] or found[2]}  # fmt: skip
        named = others.pop() if len(others) == 1 else named
    year, month, quarter = named
    if month or quarter:
        year = year or (on.year if on else None)
        if year is None:
            return None
        month = month or (on.month if on and (on.month - 1) // 3 + 1 == quarter else quarter * 3)
        return Period(year, month, quarter or (month - 1) // 3 + 1)
    return Period(on.year, on.month, (on.month - 1) // 3 + 1) if on else None


def _cased(word: str, like: str) -> str:
    return (
        word.upper() if like.isupper() and len(like) > 1 else word.capitalize() if like[:1].isupper() else word.lower()
    )


def _month_like(written: str, month: int, plain: bool) -> str:
    """Month ``month`` as ``written`` writes its own: in full, in three letters or in French, in its case."""
    how = _MONTH_OF[_fold(written)][1]
    name = _EN[month - 1] if how == "long" else _EN[month - 1][:3] if how == "short" else _FR[month - 1]
    return _cased(_fold(name) if plain else name, written)


def _month_number(found: re.Match[str], period: Period | None, year: bool) -> str:
    """A month written as a number beside its year ("2025-11", "11/2025", "3/2025"), as ``period``'s, in as many
    digits; taken out with no ``period``. Left as written when that is no year (``year``), no month (13), or one digit
    but in "3/2025" ("lot 3-2025", "ref 2025/1": a lot or a reference as likely)."""
    digits = found["ym_month"] or found["my_month"]
    one_digit = len(digits) == 1 and not (found["my_month"] and found["my_sep"] == "/")
    if not year or not 1 <= int(digits) <= 12 or one_digit:
        return found[0]
    if period is None:
        return ""
    month = f"{period.month:0{len(digits)}d}"
    return f"{period.year}{found['ym_sep']}{month}" if found["ym_year"] else f"{month}{found['my_sep']}{period.year}"


def with_period(remark: str, period: Period | None, invoice_number: Any = "", written: int | None = None) -> str:
    """A remark as the supplier's was last written, for this invoice: its month, quarter and year are ``period``'s
    (each written as it was: "Jul" gives "May", "juillet" gives "mai", "2025-11" gives "2026-05"; a whole date stays),
    the number after "INV" is ``invoice_number`` (without its own "INV"); the rest stays, the office it ends with too.
    A long number nothing says is the supplier's account is taken out: it was that voucher's. ``written``: the year
    the remark was written, which tells a year ("2025 audit", "Jul 25") from a street number. With no ``period`` the
    month is taken out, not kept stale."""
    remark = one_line(remark)
    number = _OWN_LABEL.sub("", one_line(invoice_number))
    plain = remark.isascii()

    def a_year(digits: str) -> bool:
        return written is None or abs(int(digits) - written) <= 1

    def put(found: re.Match[str]) -> str:
        if found["labelled"]:
            return f"{found['label']}{number}" if number else re.sub(rf"[{_JOINS}][\s{_JOINS}]*$", " ", found["label"])
        if found["month"]:
            if period is None:
                return ""
            out = _month_like(found["month"], period.month, plain)
            digits = found["month_year"]
            if not digits:
                return out
            year = (
                a_year(digits)
                if len(digits) == 4
                else bool(found["gap"].strip(" .")) or (written is not None and a_year(f"20{digits}"))
            )
            return out + found["gap"] + (str(period.year)[-len(digits) :] if year else digits)
        if found["quarter"]:
            return f"{found['q']}{period.quarter}" if period else ""
        if found["date"]:
            return found["date"]
        if found["ym_year"] or found["my_year"]:
            return _month_number(found, period, written is not None and a_year(found["ym_year"] or found["my_year"]))
        if found["year"]:
            if written is None or not a_year(found["year"]):
                return found["year"]
            return str(period.year) if period else ""
        return found["number"] if _ACCOUNT_LABEL.search(remark[: found.start()]) else ""

    return one_line(_VARYING.sub(put, remark)).strip(" -/,;:")


def shortened(description: Any) -> str:
    """An invoice line's own words as a remark: as written when they fit; else without the words that say nothing
    and the long numbers, then the first words that fit, with the month the line names kept at the end."""
    text = one_line(description)
    if len(text) <= REMARK_MOST:
        return text
    words = [w for w in text.split() if re.search(r"\w", w) and _fold(w).strip(".,;:()") not in _FILLER]
    for drop_numbers in (False, True):
        if drop_numbers:
            words = [w for w in words if not re.search(r"\d{5,}", w)]
        if len(" ".join(words)) <= REMARK_MOST:
            return " ".join(words).strip(" ,;:-–")
    named = _LINE_MONTH.search(" ".join(words))
    month = (named["dated"] or named["alone"]) if named else ""
    room = REMARK_MOST - (len(month) + 1 if month else 0)
    kept: list[str] = []
    for word in words:
        if len(" ".join([*kept, word])) > room:
            break
        kept.append(word)
    if month and not any(_fold(w).strip(".,;:()") == _fold(month) for w in kept):
        kept.append(month)
    return fit(" ".join(kept) if kept else " ".join(words))[0].rstrip(" ,;:-–")


def _usual(prior: Prior | None, account: str) -> tuple[AccountUse, Remark, str] | None:
    """(the account's use, the supplier's usual remark for a line coded to ``account``, why), or None. The account
    as a whole string; a line with no business unit yet takes the most used account of its GL code."""
    if not prior or not account:
        return None
    use = prior.use(account)
    if use is None:  # (``account`` is a GL code alone: the line has no business unit yet)
        use = next((u for u in prior.accounts if u.code == account and u.remarks), None)
    if use is not None and use.remarks:
        usual = use.remarks[0]
        times = "the one time" if use.remarked == 1 else f"{usual.lines} of {use.remarked} times"
        said = "the remark AP typed for this supplier's account here" if usual.typed else (
            "this supplier's usual remark for this account")  # fmt: skip
        return use, usual, f"{said}, {times}"
    said: Counter[str] = Counter()
    for u in prior.accounts:
        for remark in u.remarks:
            said[remark.shape] += remark.lines
    total = sum(u.remarked for u in prior.accounts)
    if not said or total < SURE_LINES:
        return None
    shape, lines = said.most_common(1)[0]
    if lines <= MOST * total:
        return None
    use, usual = max(((u, r) for u in prior.accounts for r in u.remarks if r.shape == shape),
                     key=lambda found: found[0].last_date)  # fmt: skip
    return use, usual, f"this supplier's usual remark, {lines} of its {total} lines (none for this account yet)"


def suggested(line: Any, invoice: Any, prior: Prior | None, account: str,
              reference: ReferenceData | None = None) -> Suggestion:  # fmt: skip
    """``suggest_remark`` with where the remark came from."""
    usual = _usual(prior, account)
    if usual is not None:
        use, remark, why = usual
        written = _date(use.last_date)
        text, cut = fit(with_period(remark.example, period_of(line, invoice), _get(invoice, "invoice_number"),
                                    written.year if written else None))  # fmt: skip
        if text:
            return Suggestion(text, why + (f", shortened to {REMARK_MOST} characters" if cut else ""), SUPPLIER)
    cut = f", shortened to {REMARK_MOST} characters"
    text = shortened(described(line))
    if text:
        return Suggestion(text, "from the invoice's line" + (cut if text != described(line) else ""), INVOICE)
    name = ""
    if reference is not None and account:  # (a GL code alone, or a business unit and its code)
        name = account_name(reference, "", account) or account_name(reference, *split_account(account))
    text, was_cut = fit(name)
    return Suggestion(text, "the account's name" + (cut if was_cut else ""), ACCOUNT) if text else NONE


def suggest_remark(line: Any, invoice: Any, prior: Prior | None, account: str,
                   reference: ReferenceData | None = None) -> tuple[str, str]:  # fmt: skip
    """(the remark suggested for ``line``, why in plain words), by the rules at the top of this module. ``line`` and
    ``invoice``: a ``LineItem`` and its ``InvoiceCoding``, or their stored dicts; ``prior``: the supplier's
    (``supplier_prior.prior_for``; None or empty: no history); ``account``: the whole account string the line is
    coded to ("0021061010.7340.2210"; the GL code alone without business units; "" for a line with no account,
    which gets the invoice's wording). ("", "") when nothing says anything."""
    return suggested(line, invoice, prior, account, reference)[:2]


def account_of(line: Any) -> str:
    """The account string a line is coded to, "" when it has no GL account yet."""
    code = str(_get(line, "predicted_gl_code") or "").strip()
    unit = str(_get(line, "predicted_cost_center") or "").strip()
    return "" if code in ("", UNASSIGNED) else join_account("" if unit == UNASSIGNED else unit, code)


def typed(posted: Any, before: Mapping[str, Any] | None = None) -> tuple[str, str]:
    """(the remark, where it is from) of a line whose remark cell the review screen posted, ``before`` being the
    line as the screen had it: left as it was, it is what it was; anything else is a person's (``PERSON``); blank
    asks for the suggestion again, and ``CLEARED`` (the box emptied) is a person's empty remark (D-229)."""
    text = one_line(posted)
    if not text:
        return ("", PERSON) if posted == CLEARED else ("", "")
    if before and text == one_line(before.get("remark")):
        return text, str(before.get("remark_from") or "")
    return text, PERSON


def remarks_typed(store: Any) -> bool:
    """Whether the remarks go to JD Edwards as typed (Settings → JD Edwards E1 → Remarks), JD Edwards in use."""
    from ..erp import jde_settings

    return (
        store is not None
        and jde_settings.in_use(store)
        and jde_settings.load(store).remarks == jde_settings.REMARKS_TYPED
    )


def fill_remarks(coding: Any, store: Any, reference: ReferenceData, wanted: bool | None = None) -> None:
    """Give each line of ``coding`` its remark (``LineItem.remark`` and ``remark_from``): the one suggested for the
    account it is coded to now, so a line whose account changes gets that account's; a line with no account gets
    the invoice's wording. A part of a split line with no remark of the supplier's for its own account takes the
    line's remark as the first part has it. A remark a person typed (``remark_from`` "person"), or emptied, is never
    replaced; typed exactly as suggested, it is the suggestion. Only where remarks are used (``wanted``, default: a
    chart of accounts is imported, or the remarks go to JD Edwards as typed): any other install stores its invoices
    as before."""
    if wanted is None:
        wanted = reference.pairs is not None or remarks_typed(store)
    if not wanted:
        return
    prior = None
    if store is not None and hasattr(store, "prior_stamp"):
        prior = prior_for(store, coding.vendor_name, always=reference.pairs is not None)
    for line in coding.line_items:
        found = suggested(line, coding, prior, account_of(line), reference)
        first = _first_part(line, coding)
        if first is not None and found.source != SUPPLIER and one_line(first.remark):
            # (typed on the first part, it is no person's remark here: this part follows the first)
            source = INVOICE if first.remark_from == PERSON else first.remark_from
            found = Suggestion(one_line(first.remark), "the remark of the line's first part", source)
        if line.remark_from == PERSON and one_line(line.remark) != found.text:
            continue
        line.remark, line.remark_from = found.text, found.source


def long_remarks(coding: Any) -> list[tuple[int, str]]:
    """The lines whose remark is longer than JD Edwards keeps: (line number, what to tell the person)."""
    out = []
    for line in coding.line_items:
        text = one_line(line.remark)
        if len(text) > REMARK_MOST:
            out.append((line.line_number, f"The remark has {len(text)} characters and JD Edwards keeps "
                        f"{REMARK_MOST}: shorten it, or it is keyed as “{fit(text)[0]}”"))  # fmt: skip
    return out
