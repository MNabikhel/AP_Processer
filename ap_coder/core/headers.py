"""Column headers compared as people read them, in English and French."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# Letters Unicode does not decompose, and "N°" (numéro), which reads "No".
_SPELLED = str.maketrans({"œ": "oe", "Œ": "OE", "æ": "ae", "Æ": "AE", "ß": "ss", "°": "o"})


def header_key(value: Any) -> str:
    """A column header as a plain key: accents folded, then only letters and digits, lower case ("Numéro de
    facture" -> "numerodefacture", "Reçu" -> "recu", "N° TPS" -> "notps"). Without the folding, an accented letter
    was dropped ("Reçu" -> "rcu") and a French header never matched its alias (B-657)."""
    text = unicodedata.normalize("NFKD", str(value or "").translate(_SPELLED))
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    return re.sub(r"[^a-z0-9]", "", text)


def match_columns(headers: list[str], aliases: dict[str, Any]) -> dict[str, str]:
    """{field: the file's column} for each field whose name or an alias is one of ``headers`` (compared as
    ``header_key``); a column is used for one field only, the first field listed that names it."""
    found: dict[str, str] = {}
    keyed = {header_key(h): h for h in headers}
    for target, names in aliases.items():
        for alias in (target, *names):
            column = keyed.get(header_key(alias))
            if column is not None and column not in found.values():
                found[target] = column
                break
    return found
