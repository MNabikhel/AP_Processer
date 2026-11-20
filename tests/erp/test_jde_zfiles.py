"""The Z-files of a PO-matched voucher (``erp.jde_zfiles``, D-284): F0411Z1's PO and F0411Z1T's rows say what the
keying sheet keys (``jde_keying``, ``jde_match.keyed_match``)."""

import csv
import io
import zipfile

from ap_coder.core.memory import vendor_key
from ap_coder.core.store import Store
from ap_coder.erp import jde_keying, jde_settings, jde_zfiles
from tests.erp.test_jde_keying import PROMO, po_invoice, promo_lookup

PO_MATCHED = jde_settings.JdeSettings(amount_mode=jde_settings.AMOUNT_GROSS, po_matched=True,
                                      an8_overrides={vendor_key(PROMO): "4471001"})  # fmt: skip


def _csv(data: bytes) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(data.decode("utf-8"))))


def _approved(store: Store, final: dict, tmp_path) -> int:
    invoice_id = store.add_invoice(tmp_path / "118204.pdf", final, {})
    store.approve_invoice(invoice_id, final, "Pat Tester")
    return invoice_id


def test_a_po_matched_voucher_whose_lines_number_again_from_1_keys_each_line_on_its_own_po_line(tmp_path):
    """B-2920: F0411Z1T's LNID was looked up by the invoice line's number, so on an invoice that numbers its lines
    again from 1 (page 2), the cooler bags (line 1 of page 1) went to PO line 13.000, the setup's (line 1 of page 2):
    both rows named 13.000 while the keying sheet keys the bags on 12.000. Each line now takes the row of the voucher
    match made from it, as the sheet does, and a line with none (on no PO line, the match held) none."""
    from dataclasses import replace

    from ap_coder.web.pages.exports import jde_file
    from tests.coding.test_po import _jde_pos

    store = Store(tmp_path / "ap.db")
    _jde_pos(store)
    jde_settings.save(store, PO_MATCHED)
    final = po_invoice()["final_output"]
    for li, n in zip(final["line_items"], (1, 1, 2), strict=True):  # page 2 starts again at 1: the setup, the freight
        li["line_number"] = n
    invoice_id = _approved(store, final, tmp_path)
    batch = store.create_export_batch([invoice_id], jde_settings.FORMAT, actor="Pat Tester")
    with zipfile.ZipFile(io.BytesIO(jde_file(store, batch)[0])) as zf:
        sent = _csv(zf.read(jde_zfiles.MATCH_FILE))
    assert [(m["DSC1"], m["LNID"], m["UORG"], m["AEXP"]) for m in sent] == [
        ("CB-220 Non-Woven Lunch Cooler", "12.000", "100", "59900"),
        ("SETUP Lunch Cooler Setup", "13.000", "1", "5750"),
        ("Charges not in the line table", "14.000", "1", "3200")]  # fmt: skip
    invoices = store.get_invoices([invoice_id])
    matches = jde_keying.po_matches(store, invoices)
    (keyed,), held = jde_keying.vouchers(invoices, PO_MATCHED, promo_lookup, po_matches=matches)
    assert held == [] and [m.line for m in keyed.matches] == [m["LNID"] for m in sent]
    # The bags on no PO line (their row left out, the match held): none for them, the setup still on its own
    matched = matches[invoice_id]
    held_match = replace(matched, rows=matched.rows[1:], held=["line 1 matches no line of PO 40917"])
    _, _, rows = jde_zfiles.voucher_rows(invoices[0], "4471001", PO_MATCHED, matched=held_match)
    assert [m["LNID"] for m in rows] == ["", "13.000", "14.000"]
