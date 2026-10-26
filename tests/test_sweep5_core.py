"""The fifth sweep's fixes in ap_coder/core, intake and ops: the pages' shared figures after a restore, the second of
two vendors whose names read alike kept apart by the page reader, emails marked done staying done, and a credit of
less than a cent shown as none."""

from __future__ import annotations

from ap_coder.core import ui
from ap_coder.core.store import Store

from .test_recheck_sweep4 import INC, LTD, _two_great_lakes
from .webclient import SAMPLE_STEM, SAMPLES, gt

# --- B-1310: what the pages share is worked out again after a restore -----------------------------------------------


def _add(store: Store, tmp_path, name: str) -> int:
    path = tmp_path / f"{name}.pdf"
    path.write_bytes(name.encode())
    doc = {"vendor_name": name, "invoice_number": name, "invoice_date": "2026-01-05", "grand_total": 10.0,
           "line_items": []}  # fmt: skip
    return store.add_invoice(path, doc, {"requires_review": True})


def test_the_vendors_invoice_dates_after_a_restore_are_the_restored_databases(tmp_path):
    """B-1310: the figures several pages share (``summary_memo``: the vendors' invoice dates, the batches' totals, the
    counts by status) were kept by the summary's generation. A backup put back has an older generation, so the first
    change after the restore brought it back to the number kept, and the pages showed the database as it was before
    the restore: an invoice the backup doesn't have, and not the new one."""
    store = Store(tmp_path / "ap.db")
    _add(store, tmp_path, "Alpha")
    backup = store.backup_now("manual")
    _add(store, tmp_path, "Beta")
    assert [r["vendor_name"] for r in store.vendor_invoice_dates()] == ["Alpha", "Beta"]
    store.restore_from(backup)
    _add(store, tmp_path, "Gamma")
    assert [r["vendor_name"] for r in store.vendor_invoice_dates()] == ["Alpha", "Gamma"]
    assert store.status_counts() == {"review": 2}


# --- B-1311: the page reader's new reading keeps the second of two vendors by one name its own ----------------------


def test_the_page_readers_reading_of_the_limiteds_invoice_stays_the_limiteds(tmp_path):
    """B-1311: two vendors whose names read alike (the second kept under "<name> #<AN8>", B-930, B-1021): the page
    reader's new reading of the Limited's invoice (``replace_proposal``) gave it the Inc.'s key, so it moved into the
    Inc.'s history and checks; and an invoice's due date when it came in was worked out from the Inc.'s terms."""
    from ap_coder.coding.vendors import master_columns, master_rows

    store = _two_great_lakes(tmp_path)
    records = [{"Name": "Great Lakes Mechanical Inc.", "AN8": "40110", "GST/HST Number": INC, "Terms": "Net 10"},
               {"Name": "Great Lakes Mechanical Limited", "AN8": "40120", "GST/HST Number": LTD,
                "Terms": "Net 60"}]  # fmt: skip
    store.import_vendor_master(master_rows(records, master_columns(list(records[0]))))
    doc = {**gt(), "vendor_name": "Great Lakes Mechanical Limited", "gst_hst_registration_number": LTD,
           "invoice_number": "L-1", "invoice_date": "2026-03-02", "payment_terms": "", "due_date": ""}  # fmt: skip
    ltd = store.add_invoice(SAMPLES / f"{SAMPLE_STEM}.pdf", doc, {"requires_review": True})
    inv = store.get_invoice(ltd)
    assert inv["vendor_key"] == "great lakes mechanical #40120"
    assert inv["due_date"] == "2026-05-01"  # the Limited's Net 60, not the Inc.'s Net 10
    assert store.replace_proposal(ltd, {**doc, "invoice_number": "L-1A"}, {"requires_review": True}, None)
    inv = store.get_invoice(ltd)
    assert inv["invoice_number"] == "L-1A"
    assert inv["vendor_key"] == "great lakes mechanical #40120"
    assert inv["due_date"] == "2026-05-01"
    assert [h["id"] for h in store.vendor_invoices("great lakes mechanical #40120")] == [ltd]
    assert not store.vendor_invoices("great lakes mechanical")


# --- B-1312: an email marked done stays done, however many were marked done after it ------------------------------


def test_an_email_marked_done_long_ago_does_not_come_back(tmp_path):
    """B-1312: the emails marked done were read from the last 20,000 "done" events only: once more had been marked
    done after it, the first one marked done was back in Emails that need a person."""
    import json
    import sqlite3

    from ap_coder.intake import inbox

    store = Store(tmp_path / "ap.db")
    (tmp_path / "emails").mkdir()
    old = {"id": "first01", "kind": "not_for_ap", "needs_person": True, "documents": []}
    (tmp_path / "emails" / "triage.jsonl").write_text(json.dumps(old) + "\n")
    assert [r["id"] for r in inbox.needs_person(store, tmp_path)] == ["first01"]
    assert inbox.mark_done(store, tmp_path, "first01")
    with sqlite3.connect(store.path) as conn:  # 20,000 emails marked done since
        conn.executemany("INSERT INTO events (action, detail, created_at) VALUES (?, ?, ?)",
                         [(inbox.DONE_ACTION, json.dumps({"id": f"later{n}"}), "2026-10-01T09:00:00")
                          for n in range(20_000)])  # fmt: skip
    assert inbox.needs_person(store, tmp_path) == []


# --- B-1313: a credit of less than half a cent is shown as none -------------------------------------------------------


def test_less_than_half_a_cent_either_way_is_shown_as_zero():
    """B-1313: ``ui.money`` showed -0.0045 as "(0.00)", a credit of nothing, while -0.004 was "0.00": every amount
    that rounds to 0.00 is "0.00", and a credit is one of at least half a cent."""
    assert [ui.money(v) for v in (-0.0045, -0.0049, -0.004, 0.0, 0.004)] == ["0.00"] * 5
    assert [ui.money(v) for v in (-0.005, -0.006, -1234.5, 1234.5)] == ["(0.01)", "(0.01)", "(1,234.50)", "1,234.50"]
