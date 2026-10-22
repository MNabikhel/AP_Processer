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
