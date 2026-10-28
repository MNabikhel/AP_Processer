"""A supplier's line descriptions as AP approves them, and the next invoice's lines read the same way (D-120).

A scan's lines come out with their spaces lost ("Palletwrap18in.x1500ft"), a letter for a digit ("Hexbo1t") or the
item-code column run into the description ("PB-93930 Bearing 6205-2RS"). AP fixes the description when approving;
the next invoice of the supplier lists the same items again, and they would read the same way. Each approved line is
remembered with the text it was read as (``Store.remember_line_texts``); a line of a later invoice of the supplier that
reads so is shown as AP approved it (``apply``), the text as printed kept on the line (``LineReading.read_as``) for the
review screen to say. A right description is also what the coding memory knows the line by: its GL account follows.

Read the same way: the text ``fold``-ed, so that spacing, punctuation, case and the letters OCR takes for one another
(1 l I, 0 O, 5 S, rn m) do not count; a part number that differs in a digit is another item. AP's latest decision on a
text wins (D-118): approving it as read again takes back the renaming. An item code AP drops (``item_code``): a
description that only differs from one AP approved by its leading code is that line; once AP has dropped the code from
two lines and not kept it since, a new item's code is left out too. What the columns beside the description ran into it
(a unit of measure, the line's quantity, a stray mark) and a letter or two OCR dropped are forgiven too (``recall``),
never a digit."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from .types import LineReading

# The characters OCR reads for one another, each mapped to one of them (after casefolding).
_CONFUSABLE = str.maketrans({"1": "l", "i": "l", "|": "l", "!": "l", "0": "o", "5": "s", "×": "x"})
# An item code before the description: letters and digits with at least three digits ("PB-93930", "A1234", "100234"),
# then the words. A quantity ("2 x Widget"), a size ("8.5x11") or a year ("2026 Annual plan") is no code: a code of
# digits alone has five or more.
_CODE = re.compile(r"^\s*(?=[A-Z0-9-]*\d{3})([A-Z]{0,4}[-/]?\d[\dA-Z-]{2,15})\s+(?=\S*[A-Za-z]{2})(.+)$")
CODE_DROPS = 2  # lines AP dropped the code from (and none kept since) before a new item's code is left out too
RECENT = 2000  # the supplier's latest remembered lines looked at


def fold(text: Any) -> str:
    """``text`` as read the same way: casefolded, without spaces or punctuation, "rn" as "m", each confusable letter
    as one ("Hexbo1t 3/8-16x2 in.,zinc" and "Hex bolt 3/8-16 x 2 in., zinc" fold alike)."""
    plain = unicodedata.normalize("NFKC", str(text or "")).casefold().replace("rn", "m")
    return "".join(ch for ch in plain if ch.isalnum()).translate(_CONFUSABLE)


def item_code(text: Any) -> tuple[str, str] | None:
    """(code, the rest) of a description that starts with an item code, else None."""
    match = _CODE.match(str(text or ""))
    if match is None or (match.group(1).isdigit() and len(match.group(1)) < 5):
        return None
    return match.group(1), match.group(2).strip()


def same_text(a: Any, b: Any) -> bool:
    """Written alike: the same words, whatever the spacing."""
    return " ".join(str(a or "").split()) == " ".join(str(b or "").split())


def observations(read: list[tuple[str, str, str]]) -> list[dict[str, Any]]:
    """What an approval says about its lines: for each (text as read, description proposed, description approved), a
    row to remember {"read_key", "printed", "description", "corrected"}: ``corrected`` when AP changed what was
    proposed. A line whose approved text reads otherwise is remembered under its approved text too, so that a later
    reading of the approved text itself (spaced otherwise) is known."""
    rows: list[dict[str, Any]] = []
    for printed, proposed, approved in read:
        printed, approved = " ".join(str(printed or "").split()), " ".join(str(approved or "").split())
        if not approved or not fold(approved):
            continue
        corrected = not same_text(proposed, approved)
        if printed and fold(printed):
            rows.append({"read_key": fold(printed), "printed": printed, "description": approved,
                         "corrected": corrected})  # fmt: skip
        if fold(approved) != fold(printed):
            rows.append({"read_key": fold(approved), "printed": approved, "description": approved,
                         "corrected": False})  # fmt: skip
    return rows


def _latest(rows: list[dict[str, Any]]) -> dict[str, str]:
    """{read key: the description of its latest approval} (``rows`` newest first)."""
    out: dict[str, str] = {}
    for r in rows:
        out.setdefault(r["read_key"], r["description"])
    return out


def drops_codes(rows: list[dict[str, Any]]) -> bool:
    """Whether AP leaves this supplier's item codes out of the description: the latest ``CODE_DROPS`` approved lines
    read with a code all dropped it (``rows`` newest first)."""
    decided = 0
    for r in rows:
        found = item_code(r.get("printed"))
        if found is None:
            continue
        if fold(r["description"]) == fold(found[1]):
            decided += 1
            if decided >= CODE_DROPS:
                return True
        elif fold(r["description"]) == fold(r["printed"]):
            return False  # kept: the code is part of this supplier's descriptions
    return False


# Units of measure a scan runs into the description from the column beside it ("Disinfectant 4 L ea").
UNITS = frozenset(
    """ea each pc pcs piece box bx bxs pk pkg pack roll rl rolls hr hrs h hour hours visit visits mo month
    months kg lb lbs ft m l set sets bdl case cs unit units un lot day days wk week weeks yr""".split()
)
MIN_FUZZY = 12  # characters (folded) of a description before a letter OCR dropped is forgiven
MAX_DROPPED = 2  # letters OCR may drop from a description AP approved, its digits all there


def _junk(token: str, numbers: tuple[float, ...]) -> bool:
    """A token a scan runs into a description from the columns beside it: a unit of measure, the line's own quantity
    or price, or a stray mark of one or two characters with at most one letter ("I", "2:", "|")."""
    bare = token.strip(".,:;|!()[]")
    if bare.casefold() in UNITS:
        return True
    try:
        if numbers and float(bare.replace(",", "")) in numbers:
            return True
    except ValueError:
        pass
    return len(token) <= 2 and sum(ch.isalpha() for ch in token) <= 1


def trimmed(description: str, numbers: tuple[float, ...] = ()) -> str:
    """``description`` without the tokens at either end that came from the columns beside it (``_junk``); itself when
    there are none, or when nothing would be left."""
    words = str(description or "").split()
    while len(words) > 1 and _junk(words[0], numbers):
        words = words[1:]
    while len(words) > 1 and _junk(words[-1], numbers):
        words = words[:-1]
    return " ".join(words)


def _dropped_letters(read: str, known: str) -> bool:
    """Whether ``read`` (folded) is ``known`` (folded) with at most ``MAX_DROPPED`` letters left out by OCR and every
    digit there: never another part number (a digit differs) or another model ("58A" is no reading of "58X")."""
    if not 0 < len(known) - len(read) <= MAX_DROPPED or len(known) < MIN_FUZZY:
        return False
    if [c for c in read if c.isdigit()] != [c for c in known if c.isdigit()]:
        return False
    it = iter(known)
    return all(c in it for c in read)


def recall(description: str, known: dict[str, str], drop: bool, numbers: tuple[float, ...] = ()) -> str | None:
    """The description AP approved for a line read ``description`` (``known``: read key -> approved), or the text
    without its item code (``drop``), or None to show it as read. Tried in turn: the text as read, without its item
    code, without what the columns beside it ran into it (``trimmed``: ``numbers`` are the line's quantity and
    price), and with a letter or two OCR dropped (``_dropped_letters``, one description only)."""
    found = item_code(description)
    texts = [description, *([found[1]] if found else [])]
    texts += [t for t in (trimmed(x, numbers) for x in list(texts)) if t not in texts]
    for text in texts:
        approved = known.get(fold(text))
        if approved is not None:
            return approved
    for text in texts:
        key = fold(text)
        near = {approved for k, approved in known.items() if _dropped_letters(key, k)}
        if len(near) == 1:
            return near.pop()
    return trimmed(found[1], numbers) if found and drop else None


def apply(lines: list[LineReading], rows: list[dict[str, Any]]) -> int:
    """Each line of ``lines`` read as a line AP approved before (``rows``: the supplier's remembered lines, newest
    first) shown as AP approved it, its text as printed in ``read_as``. Returns how many were renamed."""
    if not rows or not lines:
        return 0
    known, drop = _latest(rows), drops_codes(rows)
    renamed = 0
    for li in lines:
        printed = li.read_as or li.description
        numbers = tuple(float(x) for x in (li.quantity, li.unit_price) if isinstance(x, (int, float)))
        approved = recall(printed, known, drop, numbers)
        if approved is None or same_text(approved, printed):
            continue
        li.read_as, li.description = printed, approved
        renamed += 1
    return renamed


def as_read(capture_lines: list[dict[str, Any]], description: str, amount: Any) -> str:
    """The text a proposed line (``description``, ``amount``) was read as: the capture's line shown so, its text as
    printed when it was renamed (``read_as``); ``description`` itself when no line of the capture shows it."""
    want = " ".join(str(description or "").split())
    for li in capture_lines:
        if " ".join(str(li.get("description") or "").split()) != want:
            continue
        try:
            # (a credit note's lines may be coded with the sign the reader did not print)
            if amount is not None and li.get("amount") is not None and abs(abs(float(li["amount"])) - abs(
                    float(amount))) > 0.01:  # fmt: skip
                continue
        except (TypeError, ValueError):
            continue
        return str(li.get("read_as") or li.get("description") or "")
    return want
