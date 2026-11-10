"""Supplier numbers and accounts for JD Edwards E1: a vendor's address number (``resolve_an8``), the E1 account of an
AP Coder GL code and cost center (``account_for``, ``ani``, ``account_text``), and the account mapping read from a
spreadsheet (``gl_map_from_records``).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from typing import Any

from ..core.chart import code_of, join_account, split_code
from ..core.headers import match_columns
from ..core.memory import vendor_key
from ..core.reference_data import UNASSIGNED
from .jde_formats import _norm
from .jde_settings import GlMap, JdeSettings

VendorLookup = Mapping[str, str] | Callable[[dict[str, Any]], str] | None


def resolve_an8(final: dict[str, Any], vendor_lookup: VendorLookup, settings: JdeSettings) -> str:
    """The supplier's address number: Settings → JD Edwards first, then the vendor master's ERP ID
    (``Store.vendor_ids()``: by name, else by GST/HST number) or a callable."""
    from .exports import _vendor_id

    override = settings.an8_overrides.get(vendor_key(final.get("vendor_name") or ""))
    if override:
        return str(override).strip()
    if callable(vendor_lookup):
        return str(vendor_lookup(final) or "").strip()
    return _vendor_id(final, vendor_lookup or {}).strip()  # (read, not copied: once per invoice or vendor)


def _gl_index(settings: JdeSettings) -> dict[tuple[str, str], GlMap]:
    """The account mapping by (GL code, cost center), the first row of each (as read from the top): made once for
    these settings, again once their mapping is another list or has grown (B-1863), not read row by row per line."""
    kept = settings.__dict__.get("_gl_index")
    if kept is None or kept[0] is not settings.gl_map or kept[1] != len(settings.gl_map):
        index: dict[tuple[str, str], GlMap] = {}
        for m in settings.gl_map:
            index.setdefault((_norm(m.gl_code), _norm(m.cost_center)), m)
        kept = settings.__dict__["_gl_index"] = (settings.gl_map, len(settings.gl_map), index)
    return kept[2]


def account_for(gl_code: str, cost_center: str, settings: JdeSettings) -> GlMap | None:
    """The E1 account of an AP Coder GL code (+ cost center), or None when it is not mapped."""
    gl, cc = _norm(gl_code), _norm(cost_center)
    cc = "" if cc == UNASSIGNED else cc  # a cost center still to pick is not a business unit (B-1211)
    if not gl or gl == UNASSIGNED:  # a line still to code has no object account (B-830)
        return None
    index = _gl_index(settings)
    row = index.get((gl, cc)) or index.get((gl, ""))
    if row is None and not settings.use_default_rule:
        return None
    row = row or GlMap(gl_code)
    bu = row.bu.strip() or ((cc if settings.bu_from_cost_center and cc else "") or settings.default_bu.strip())
    obj, sub = row.obj.strip(), row.sub.strip()
    if not obj:  # the default rule: the GL code is the object, or "object.subsidiary" (D-209, B-2085)
        obj, own = split_code(gl)
        sub = sub or own
    if not bu or not obj:
        return None
    return GlMap(gl_code, cost_center, bu, obj, sub, row.sbl.strip(), row.sblt.strip())


def accounts_without_bu(settings: JdeSettings, gl_codes: Iterable[str], tax_codes: Iterable[str],
                        cost_centers: bool) -> list[str]:  # fmt: skip
    """The accounts a JD Edwards export would hold invoices back for, having no BU.Object for them: the tax accounts
    (their lines carry no cost center) and, unless each line's cost center gives the BU (``bu_from_cost_center`` with a
    cost center list), the GL accounts. None with a Default BU and the default rule (B-1300)."""
    if settings.use_default_rule and settings.default_bu.strip():
        return []
    needed = list(tax_codes) + ([] if settings.bu_from_cost_center and cost_centers else list(gl_codes))
    return [code for code in dict.fromkeys(c.strip() for c in needed if c.strip())
            if account_for(code, "", settings) is None]  # fmt: skip


def ani(account: GlMap) -> str:
    """VNANI in format 2: BU.Object.Subsidiary ("CC400.6010", "CC400.6010.01"), the string a person types (D-209)."""
    return join_account(account.bu, code_of(account.obj, account.sub))


def account_text(gl_code: str, cost_center: str, settings: JdeSettings) -> str:
    """The E1 account a GL line posts to, BU.Object.Subsidiary ("CC400.6010.01"), or "" when none is mapped."""
    account = account_for(gl_code, cost_center, settings)
    return ani(account) if account else ""


# --- Mapping import ----------------------------------------------------------------------------------------------

GL_MAP_ALIASES = {
    "gl_code": ("glcode", "gl", "glaccount", "account", "apcodergl", "code"),
    "cost_center": ("costcenter", "costcentre", "cc", "dept", "department"),
    "bu": ("bu", "businessunit", "mcu", "jdebu"),
    "obj": ("obj", "object", "objectaccount", "jdeobject"),
    "sub": ("sub", "subsidiary", "jdesub"),
    "sbl": ("sbl", "subledger"),
    "sblt": ("sblt", "subledgertype"),
}


def gl_map_from_records(records: list[dict[str, Any]]) -> list[GlMap]:
    """GL → BU.Obj.Sub rows from a spreadsheet (columns found by name, e.g. "GL code, Cost center, BU,
    Object, Subsidiary"); rows without a GL code are skipped."""
    if not records:
        return []
    columns = match_columns(list(records[0]), GL_MAP_ALIASES)
    out = []
    for rec in records:

        def get(target: str, rec: dict[str, Any] = rec) -> str:
            value = rec.get(columns[target]) if target in columns else None
            text = "" if value is None else str(value).strip()
            return "" if text.lower() in ("nan", "none") else text

        if get("gl_code"):
            out.append(GlMap(**{k: get(k) for k in GL_MAP_ALIASES}))
    return out
