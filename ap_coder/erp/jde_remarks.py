"""The remarks JD Edwards E1 holds, 30 characters each: a G/L line's (``line_remark``), and the pay item's
(``voucher_remark``; ``_pay_item_remark`` for VLRMK). The batch files and the keying sheet both ask here; how a remark
is suggested and fitted is ``coding/remark.py``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..coding.remark import fit
from .jde_formats import _text
from .jde_settings import REMARKS_TYPED, JdeSettings
from .jde_voucher import _Voucher, po_not_sent

REMARK_WIDTH = 30  # VLRMK


def line_remark(line: Mapping[str, Any]) -> str:
    """The remark of a G/L line as JD Edwards holds it (30 characters): the line's own remark (suggested or typed,
    D-213; ``remark.fit``: the whole words that fit), else the line's description cut at 30. The batch files and
    the keying sheet (``jde_keying``) both ask here. A remark the person emptied on purpose stays empty (D-229)."""
    remark = fit(line.get("remark"))[0]
    if remark or line.get("remark_from") == "person":
        return remark
    return _text(line.get("description"), REMARK_WIDTH)


def voucher_remark(invoice: Mapping[str, Any]) -> str:
    """The remark of a voucher's pay item: its first line's remark (the first line that has one)."""
    lines = (invoice.get("final_output") or {}).get("line_items") or []
    return next((remark for remark in map(line_remark, lines) if remark), "")


def _pay_item_remark(v: _Voucher, settings: JdeSettings) -> str:
    """VLRMK: AP Coder's invoice number, or the voucher's remark (Settings' Remarks, D-210); a PO number too long for
    VLPO is added when it fits in the remark's 30 characters (D-87)."""
    typed = settings.remarks == REMARKS_TYPED
    remark = voucher_remark({"final_output": v.final}) if typed else f"AP Coder #{v.inv_id}"
    with_po = f"{remark} PO {str(v.final.get('po_number') or '').strip()}".strip()
    return (with_po if po_not_sent(v.final, settings) and len(with_po) <= REMARK_WIDTH else remark)[:REMARK_WIDTH]
