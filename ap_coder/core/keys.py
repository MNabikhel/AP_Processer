"""The values the store keeps and compares by (D-192): a PO number, an invoice number and a GST/HST number as people
mean them, a supplier's key, a vendor's ERP ID in the vendor master, the quick search's names and numbers, a lesson's
kind of line and an account number shown by its last digits. The modules they came from keep their names
(``coding/po.py``, ``coding/vendors.py``, ``coding/rules.py``, ``erp/search.py``, ``capture/supplier.py``,
``ai/lessons.py``): the store uses them from here, below every layer that also does."""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from .memory import vendor_key

# vendors.in_master: 0 a vendor AP only set controls for, 1 in the vendor master imported from the ERP, 2 added to
# the vendor list from the review screen (a new vendor: set up in the ERP before it is paid).
IN_ERP, ADDED_HERE = 1, 2
LINE_GL = "line_gl"  # the field of a line-coding lesson (its GL account; the lessons: ``ai/lessons.py``)
LINE_CC = "line_cc"  # the field of a line's cost center lesson
LINE_FIELDS = (LINE_GL, LINE_CC)


def po_key(value: Any) -> str:
    """Compare PO numbers the way people mean them: "PO-00412", "PO 412" and "412" are the same."""
    text = re.sub(r"[^0-9a-z]", "", str(value or "").lower())
    text = re.sub(r"^(purchaseorder|po|bc|order|number|no|num)+", "", text) or text
    return text.lstrip("0") or text


_NUMBER_PREFIXES = re.compile(r"^(invoice|inv|facture|fact|bill|no|num|nr|n°|#)+")


def norm_invoice_number(value: Any) -> str:
    """Compare invoice numbers the way people mean them: "INV-00123", "#123" and "123" are the same."""
    text = re.sub(r"[^0-9a-z°#]", "", str(value or "").lower())
    text = _NUMBER_PREFIXES.sub("", text)
    text = re.sub(r"[^0-9a-z]", "", text)
    return text.lstrip("0") or text


def norm_tax_number(value: Any) -> str:
    return re.sub(r"[^0-9a-z]", "", str(value or "").lower())


BUSINESS_NUMBER = re.compile(r"(\d{9})(?:rt\d{0,4})?")


def same_tax_number(a: Any, b: Any) -> bool:
    """Whether two GST/HST numbers are the same registration. A vendor master often keeps only the 9-digit Business
    Number ("123456789") while the invoice shows the full account ("123456789 RT0001"): when either side lacks the
    RT program suffix, the 9 digits decide. Two full accounts (RT0001 and RT0002) must match exactly."""
    a, b = norm_tax_number(a), norm_tax_number(b)
    if not a or not b:
        return False
    if a == b:
        return True
    bn_a, bn_b = BUSINESS_NUMBER.fullmatch(a), BUSINESS_NUMBER.fullmatch(b)
    if not bn_a or not bn_b or (len(a) == 15 and len(b) == 15):
        return False
    return bn_a.group(1) == bn_b.group(1)


_RT_ACCOUNT = re.compile(r"(\d{9})rt\d{4}")


def other_tax_account(a: Any, b: Any) -> bool:
    """Whether two GST/HST numbers are two accounts of one business: both a full account ("123456789 RT0001"), the
    same nine digits (the Business Number: the legal entity registered with the CRA) and other four digits after RT
    (which of its GST/HST accounts: a branch or a division can have its own). The same business, so no changed
    number (D-219); ``same_tax_number`` still says they are not the same registration."""
    a, b = _RT_ACCOUNT.fullmatch(norm_tax_number(a)), _RT_ACCOUNT.fullmatch(norm_tax_number(b))
    return bool(a and b) and a.group(1) == b.group(1) and a.group(0) != b.group(0)


def master_vendor_id(ids: dict[str, str], vendor_name: str | None, gst_number: Any = None) -> str:
    """The vendor master's ERP vendor ID for an invoice (``ids``: ``Store.vendor_ids()``): by its GST/HST number
    however much of it either side keeps (``same_tax_number``: the master's 9-digit Business Number for the invoice's
    full account, or the master's full account for 9 digits), else by name. The number first: two suppliers whose
    names read the same ("… Inc." and "… Limited") have their own numbers (B-930). "" when the master doesn't know it.
    The supplier key (``Store.supplier_key_for``) and the export files both use this, so they agree."""
    if gst_number:
        number = norm_tax_number(gst_number)
        bn = number[:9] if number[:9].isdigit() else ""
        found = ids.get(f"gst:{number}") or (
            bn and (ids.get(f"bn:{bn}") or (ids.get(f"bnx:{bn}") if len(number) == 9 else None))
        )
        if found:
            return found
    found = ids.get(vendor_key(vendor_name or ""))
    if not found and (number_of := getattr(ids, "number_of", None)) is not None:
        # a name no vendor is kept under as written: by the rule the checks find its vendor by (a name with its
        # supplier number, a retired supplier's marker, a person written the other way round: B-2162)
        found = number_of(vendor_name)
    return found or ""


ACCOUNT_LABEL = re.compile(r"(?i)\b(?:account|acct|acc|a/c|compte|cpte)\b\D{0,12}?(\d[\d\s-]*\d|\d)")


def mask_account(value: Any) -> str:
    """ "…4567": the last 4 digits of the account number (the number after "Account", else the end of the
    details), never the whole account."""
    text = str(value or "")
    labelled = ACCOUNT_LABEL.search(text)
    digits = re.sub(r"\D", "", labelled.group(1)) if labelled else "".join(re.findall(r"\d+", text))
    return f"…{digits[-4:]}" if digits else "(none)"


def supplier_key(vendor_name: str | None, gst_number: str | None = None, vendor_id: str | None = None) -> str:
    """``id:<vendor master id>``, else ``bn:<the 9 digits of the GST/HST business number>``, else
    ``name:<memory.vendor_key(name)>``; "" when there is nothing to go on."""
    vid = str(vendor_id or "").strip()
    if vid:
        return f"id:{vid}"
    digits = re.sub(r"\D", "", str(gst_number or ""))
    if len(digits) >= 9:
        return f"bn:{digits[:9]}"
    name = vendor_key(vendor_name or "")
    return f"name:{name}" if name else ""


def plain(text: str) -> str:
    """Lower case, no accents, single spaces: "Café  Livraison" -> "cafe livraison"."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return " ".join("".join(c for c in decomposed if not unicodedata.combining(c)).lower().split())


def raw_number(text: str) -> str:
    """Letters and digits only, leading zeros kept: "CN-2026-0047" -> "cn20260047"."""
    return re.sub(r"[^0-9a-z]", "", text.lower())


def search_keys(row: dict[str, Any]) -> tuple[str, str, str]:
    """(the vendor and file names, the invoice number as it compares, its letters and digits) of an invoice, as the
    quick search's ``_score`` reads them (``erp/search.py``): what the invoices' summary keeps for it (D-110)."""
    number = row.get("invoice_number") or ""
    return plain(f"{row.get('vendor_name') or ''} {row.get('file_name') or ''}"), norm_invoice_number(number), \
        raw_number(number)  # fmt: skip


def register_keys(vendor_name: str, invoice_number: str) -> tuple[str, str]:
    """(the vendor name, the invoice number's letters and digits) of an ERP register row as ``_score`` reads them:
    what the register keeps for the quick search (``plain_name``, ``raw_number``, D-157)."""
    keys = search_keys({"vendor_name": vendor_name, "invoice_number": invoice_number})
    return keys[0], keys[2]


def line_pattern(description: str) -> str:
    """The words that make a line "the same kind of line" from one invoice to the next: its first four words,
    without numbers, dates or punctuation ("Mobility services Oct 2026 - 555-0101" -> "mobility services")."""
    words = re.findall(r"[a-zà-ÿ]{2,}", str(description or "").lower())
    return " ".join(words[:4])
