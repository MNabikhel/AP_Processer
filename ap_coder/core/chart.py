"""One JD Edwards account string, ``business unit.object.subsidiary`` (D-209).

A line keeps its two codes as before: the cost center is the business unit and the GL code is the object, or
``object.subsidiary`` when the account has one. This module alone joins them into the string a person types into
JD Edwards ("0880133142.6215.1170") and splits such a string back, so nothing else has to know the order or the dot.

Entry points: ``join_account``, ``split_account``, ``split_code`` and ``clean``.
"""

from __future__ import annotations

from typing import Any

SEPARATOR = "."


def clean(value: Any) -> str:
    """A code as the store keeps it: the padding of an export (leading spaces, a lone space for blank) gone."""
    text = "" if value is None else str(value).strip()
    return "" if text.lower() in ("nan", "none") else text


def code_of(obj: Any, subsidiary: Any = "") -> str:
    """The GL code of an object and its subsidiary: "6215.1170", or "3005" when there is no subsidiary."""
    obj, subsidiary = clean(obj), clean(subsidiary)
    return f"{obj}{SEPARATOR}{subsidiary}" if obj and subsidiary else obj


def split_code(code: Any) -> tuple[str, str]:
    """A GL code as (object, subsidiary): "6215.1170" is ("6215", "1170"), "3005" is ("3005", "")."""
    obj, _, subsidiary = clean(code).partition(SEPARATOR)
    return obj.strip(), subsidiary.strip()


def join_account(business_unit: Any, code: Any) -> str:
    """The account string of a business unit and a GL code; the code alone when there is no business unit."""
    business_unit, code = clean(business_unit), code_of(*split_code(code))
    return f"{business_unit}{SEPARATOR}{code}" if business_unit and code else business_unit or code


def split_account(text: Any) -> tuple[str, str]:
    """An account string as (business unit, GL code), split at its first dot: "  0880133142.6215.1170" is
    ("0880133142", "6215.1170"). With no dot the text is the business unit and the code is blank: whether a short
    text is a business unit or an account is for the caller, who knows the chart, to say."""
    business_unit, _, code = clean(text).partition(SEPARATOR)
    return business_unit.strip(), code_of(*split_code(code))
