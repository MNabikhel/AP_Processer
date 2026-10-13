"""How alike two invoice line descriptions are: for coding a line from the lines AP coded before.

Suppliers word the same thing differently: "Nitrile gloves, box of 100", "GLOVES, NITRILE", "Nit. glove LG",
"Ntrl gloves". The same words in the same order miss most of these. Here a line is a set of words (``keys``),
and two lines are compared word by word:

* case, accents, punctuation, numbers, units and packaging ("box of 100", "pouces") and month names do not count;
* singular and plural are one word ("glove" and "gloves", "frai" and "frais");
* a shortened word matches the word it shortens, from the same first letter: cut short ("Hydr." for
  "hydraulic") or written without its vowels ("Brg" for "bearing"), a little less for three letters;
* a word spelt nearly the same matches in part ("hydraulique" and "hydraulic" share most letter triples);
* a word AP coded to several accounts ("premium", "charge") counts for less than one that always went to one
  account (``Index``'s weights): it says little about the account.

The score is the weighted share of words the two lines have in common (Jaccard), 1.0 for the same words.
Pure Python, no model: a few thousand past lines are compared in milliseconds through an index of their words.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Callable, Iterable
from functools import lru_cache

from .memory import _STOP_WORDS, _normalise

_MONTHS = {
    "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november",
    "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec",
    "janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet", "aout", "septembre", "octobre", "novembre",
    "decembre", "janv", "fevr", "avr", "juil",
}  # fmt: skip
_FRENCH = {"des", "les", "pour", "avec", "sur", "par", "aux", "une", "dans", "mois", "mensuel", "mensuels",
           "mensuelle", "mensuelles", "facture"}  # fmt: skip
# Units and packaging: how much, not what (an account is about what).
_UNITS = {
    "box", "boxes", "case", "cases", "pack", "packs", "pkg", "roll", "rolls", "pcs", "piece", "pieces", "pair",
    "pairs", "inch", "inches", "feet", "foot", "litre", "litres", "liter", "liters", "gallon", "gallons",
    "boite", "boites", "caisse", "caisses", "paquet", "paquets", "rouleau", "rouleaux", "paire", "paires",
    "pouce", "pouces", "pied", "pieds",
}  # fmt: skip
STOP_WORDS = frozenset(_STOP_WORDS | _MONTHS | _FRENCH | _UNITS)
_VOWELS = set("aeiou")
_DIGIT = re.compile(r"\d")

EXACT, PREFIX, PREFIX_SHORT, SKELETON, SKELETON_SHORT = 1.0, 0.9, 0.7, 0.75, 0.6
SPELLING_MIN = 0.6  # letter-triple overlap (Dice) from which two words are spelt nearly the same


def _singular(word: str) -> str:
    """The singular: "gloves" -> "glove", "frais" -> "frai"; a short word stays ("cons." for consulting)."""
    return word[:-1] if len(word) >= 5 and word.endswith("s") and not word.endswith("ss") else word


@lru_cache(maxsize=131072)
def keys(text: str) -> frozenset[str]:
    """The words of a line description that say what was bought: "Nitrile Gloves, box of 100 - LG" -> {"nitrile",
    "glove"}. Codes with letters and digits ("cat6", "kk74031") are kept; plain numbers are not."""
    out = set()
    for w in _normalise(text).split():
        if len(w) < 3 or w.isdigit() or w in STOP_WORDS:
            continue
        w = _singular(w)
        if w not in STOP_WORDS:
            out.add(w)
    return frozenset(out)


def _trigrams(word: str) -> set[str]:
    return {word[i : i + 3] for i in range(len(word) - 2)}


def _skeleton_of(short: str, long: str) -> bool:
    """``short`` is ``long`` without (some of) its vowels: "brg" in "bearing", "assy" in "assembly"."""
    if any(c in _VOWELS for c in short[1:]):
        return False
    it = iter(long)
    return all(c in it for c in short)


@lru_cache(maxsize=262144)
def word_match(a: str, b: str) -> float:
    """How far two words are the same word: 1.0 the same; less for a shortened word, or a word spelt nearly the
    same; 0.0 otherwise. A word with digits (a code) only matches itself."""
    if a == b:
        return EXACT
    if not a or not b or a[0] != b[0] or _DIGIT.search(a) or _DIGIT.search(b):
        return 0.0
    short, long = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) >= 3 and len(short) < len(long):
        if long.startswith(short):
            return PREFIX if len(short) >= 4 else PREFIX_SHORT
        if _skeleton_of(short, long):
            return SKELETON if len(short) >= 4 else SKELETON_SHORT
    if len(short) >= 5:
        ta, tb = _trigrams(a), _trigrams(b)
        dice = 2 * len(ta & tb) / (len(ta) + len(tb))
        if dice >= SPELLING_MIN:
            return round(dice, 3)
    return 0.0


def similarity(
    a: Iterable[str], b: Iterable[str], weight: dict[str, float] | None = None, own_codes: bool = False
) -> float:
    """Weighted soft Jaccard of two sets of keys: each word of one line paired with at most one word of the other
    (best pairs first); a pair counts ``word_match`` of the pair, the rest of each word as a difference.
    ``own_codes``: the lines come from different vendors, each with its own item codes ("KC31973"): a code that
    is not on both lines is no difference (one on both, like "cat6", still counts)."""
    a, b = list(a), list(b)
    if own_codes:
        shared = set(a) & set(b)
        a = [x for x in a if x in shared or not _DIGIT.search(x)]
        b = [y for y in b if y in shared or not _DIGIT.search(y)]
    if not a or not b:
        return 0.0
    w = weight or {}
    pairs = sorted(((word_match(x, y), x, y) for x in a for y in b), reverse=True)
    used_a: set[str] = set()
    used_b: set[str] = set()
    inter = union = 0.0
    for m, x, y in pairs:
        if m <= 0.0:
            break
        if x in used_a or y in used_b:
            continue
        used_a.add(x)
        used_b.add(y)
        wxy = (w.get(x, 1.0) + w.get(y, 1.0)) / 2
        inter += m * wxy
        union += (2 - m) * wxy
    union += sum(w.get(x, 1.0) for x in a if x not in used_a) + sum(w.get(y, 1.0) for y in b if y not in used_b)
    return inter / union if union else 0.0


class Index:
    """Past lines (their keys) and the account each went to, searchable by words. A word's weight is 1 over the
    number of different accounts the lines it is in went to (1.0 for a word never seen)."""

    def __init__(self, entries: list[frozenset[str]], accounts: list[str]) -> None:
        self.entries = entries
        by_word: dict[str, list[int]] = defaultdict(list)
        word_accounts: dict[str, set[str]] = defaultdict(set)
        for i, (ks, gl) in enumerate(zip(entries, accounts, strict=True)):
            for k in ks:
                by_word[k].append(i)
                word_accounts[k].add(gl)
        self.by_word = dict(by_word)
        self.weight = {k: 1.0 / len(gls) for k, gls in word_accounts.items()}
        self._by_letter: dict[str, list[str]] = defaultdict(list)
        for k in self.by_word:
            self._by_letter[k[0]].append(k)

    def similar(
        self, line: frozenset[str], min_similarity: float = 0.0, own_codes: Callable[[int], bool] | None = None
    ) -> list[tuple[int, float]]:
        """[(entry, similarity)] for the past lines sharing a word (or a near word) with ``line``. ``own_codes(entry)``:
        that entry is another vendor's line (see ``similarity``)."""
        found: set[int] = set()
        for k in line:
            for word in self._by_letter.get(k[0], ()):
                if word_match(k, word) > 0.0:
                    found.update(self.by_word[word])
        out = []
        for i in found:
            sim = similarity(line, self.entries[i], self.weight, own_codes=bool(own_codes and own_codes(i)))
            if sim >= min_similarity:
                out.append((i, sim))
        return out
