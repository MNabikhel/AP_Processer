"""Voucher posting safety (ap_coder/erp/jde_post.py, D-105) against the stand-in AIS server: never posted twice, never
someone else's voucher taken for this one, a row that may be in JD Edwards never sent blind, dropped or loaded again
from the batch files, and a restore that never makes AP Coder forget a voucher JDE holds."""

from __future__ import annotations

import csv
import datetime as dt
import io
import threading
import time
import zipfile

import pytest

from ap_coder.erp import jde, jde_ais, jde_post
from ap_coder.web.pages import exports

from .fake_ais import PASSWORD, FakeAis
from .test_jde import ON, QC
from .test_jde_post import batch_of, connect


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(jde_ais, "RETRY_PAUSE", 0)
    monkeypatch.setenv(jde_ais.PASSWORD_ENV, PASSWORD)


@pytest.fixture
def ais():
    with FakeAis(slow_seconds=0.5) as fake:
        yield fake


def finals(fake: FakeAis) -> list[dict]:
    return [c for c in fake.orchestration_calls("voucher_create") if c["ProcessingMode"] == "FINAL"]


def statuses(store, batch) -> dict[int, str]:
    return {p["invoice_id"]: p["status"] for p in jde_post.postings(store, batch)}


def lost_answer(tmp_path, fake, *stems):
    """A batch whose first voucher JDE made, its answer lost (unknown)."""
    store, batch = batch_of(tmp_path, fake, *stems)
    connect(store, fake, timeout=0.15)
    jde_post.dry_run(store, batch)
    fake.script("voucher_create", "slow-after")
    jde_post.post(store, batch)
    assert statuses(store, batch)[1] == jde_post.UNKNOWN
    return store, batch


# --- A1: the idempotency key ----------------------------------------------------------------------------------------


def test_a_second_laptop_never_makes_the_first_ones_key(tmp_path, ais):
    a, ba = batch_of(tmp_path / "laptop1", ais, ON)
    jde_post.dry_run(a, ba)
    jde_post.post(a, ba)
    b, bb = batch_of(tmp_path / "laptop2", ais, QC)  # another data folder: invoice 1 of batch 1 again
    assert jde_post.installation_id(a) != jde_post.installation_id(b)
    assert (tmp_path / "laptop1" / "jde_installation_id").is_file()  # in the data folder, not the database
    assert jde_post.installation_id(a) == jde_post.installation_id(a)  # made once
    jde_post.dry_run(b, bb)
    assert jde_post.post(b, bb)["posted"] == 1
    pa, pb = jde_post.postings(a, ba)[0], jde_post.postings(b, bb)[0]
    assert pa["idem_key"] != pb["idem_key"] and pa["document"] != pb["document"]
    assert pb["idem_key"] == f"APC-{jde_post.installation_id(b)}-1-{bb}"


# --- A2: EXISTS counts only for this voucher ------------------------------------------------------------------------


def _plant(fake, store, batch, **record):
    """JDE already holds a voucher under the batch's first key."""
    key = jde_post.postings(store, batch)[0]["idem_key"]
    fake.by_key[key] = {"document": "77777", "doc_type": "PV", "company": "00001", "batch": "X", **record}
    return key


def test_exists_for_another_vouchers_record_is_failed_and_locked(tmp_path, ais):
    store, batch = batch_of(tmp_path, ais)
    jde_post.dry_run(store, batch)
    _plant(ais, store, batch, an8="4999", invoice_number="OTHER-1", gross=5.0)
    result = jde_post.post(store, batch)
    row = jde_post.postings(store, batch)[0]
    assert result["posted"] == 0 and result["failed"] == 1
    assert row["status"] == jde_post.FAILED and row["maybe_sent"] == 1 and row["document"] == ""
    assert "77777" in row["error"] and "not this invoice" in row["error"] and "supplier 4999" in row["error"]
    assert "jde_voucher" not in (store.get_invoice(1)["meta"] or {})
    assert jde_post.undo_problem(store, batch)  # locked for a person
    assert jde_post.dry_run(store, batch)["rows"][0]["status"] == jde_post.FAILED  # a dry run doesn't unlock it


def test_exists_with_another_fingerprint_is_failed(tmp_path, ais):
    store, batch = batch_of(tmp_path, ais)
    jde_post.dry_run(store, batch)
    _plant(ais, store, batch, fingerprint="0" * 64)
    jde_post.post(store, batch)
    row = jde_post.postings(store, batch)[0]
    assert row["status"] == jde_post.FAILED and "fingerprint" in row["error"]


def test_exists_without_who_it_is_for_is_not_taken_as_posted(tmp_path, ais):
    store, batch = batch_of(tmp_path, ais)
    jde_post.dry_run(store, batch)
    _plant(ais, store, batch)  # only the document number
    jde_post.post(store, batch)
    row = jde_post.postings(store, batch)[0]
    assert row["status"] == jde_post.FAILED and "did not say whose voucher" in row["error"]
    assert jde_post.undo_problem(store, batch)


def test_exists_for_this_voucher_is_posted(tmp_path, ais):
    store, batch = batch_of(tmp_path, ais)
    jde_post.dry_run(store, batch)
    final = store.get_invoice(1)["final_output"]
    _plant(ais, store, batch, an8="0004001", invoice_number=final["invoice_number"], gross=final["grand_total"])
    jde_post.post(store, batch)
    row = jde_post.postings(store, batch)[0]
    assert row["status"] == jde_post.POSTED and row["document"] == "77777"


def test_a_dry_run_never_counts_exists_as_a_pass(tmp_path, ais):
    store, batch = batch_of(tmp_path, ais)
    jde_post.dry_run(store, batch)
    _plant(ais, store, batch, an8="4001")
    row = jde_post.dry_run(store, batch)["rows"][0]
    assert row["status"] == jde_post.REFUSED and "already in JD Edwards" in row["error"] and row["locked"]
    with pytest.raises(ValueError, match="Nothing in this batch is ready"):
        jde_post.post(store, batch)
    assert finals(ais) == []


# --- A3 / A4: the statuses and the results --------------------------------------------------------------------------


def test_an_unknown_voucher_stays_unknown_while_jde_is_down(tmp_path, ais):
    store, batch = lost_answer(tmp_path, ais, ON, QC)
    connect(store, ais, base_url="http://127.0.0.1:1", timeout=0.15)  # JDE unreachable
    jde_post.retry_queue(store)
    row = jde_post.postings(store, batch)[0]
    assert row["status"] == jde_post.UNKNOWN and row["maybe_sent"] == 1 and row["next_try"]
    assert "can't be undone" in jde_post.undo_problem(store, batch)
    with pytest.raises(jde_post.Locked):
        jde_post.undo_batch(store, batch)


def test_a_changed_unknown_voucher_stays_unknown_while_jde_cant_be_asked(tmp_path, ais):
    store, batch = lost_answer(tmp_path, ais)
    jde.save(store, jde.JdeSettings(default_bu="CC100", bu_from_cost_center=False, user="OTHER"))  # payload changes
    connect(store, ais, base_url="https://jde-ais.invalid:9300", timeout=0.15)  # off the VPN: the name not found
    jde_post.retry_queue(store)
    assert statuses(store, batch)[1] == jde_post.UNKNOWN and jde_post.undo_problem(store, batch)
    connect(store, ais)
    jde_post.retry_queue(store)  # JDE back: it has it, so it is posted, changed or not
    assert statuses(store, batch)[1] == jde_post.POSTED and len(finals(ais)) == 1


def test_created_without_a_document_number_is_looked_up(tmp_path, ais, monkeypatch):
    store, batch = batch_of(tmp_path, ais)
    jde_post.dry_run(store, batch)
    out = ais._out

    def no_document(key, row):
        if key == "voucher_create" and row.get("result") == "CREATED":
            row = {k: v for k, v in row.items() if k != "document"}
        return out(key, row)

    monkeypatch.setattr(ais, "_out", no_document)
    jde_post.post(store, batch)
    row = jde_post.postings(store, batch)[0]
    assert row["status"] == jde_post.POSTED and row["document"] == "10001"  # from the lookup by its key
    assert len(ais.orchestration_calls("voucher_status")) == 1


def test_created_without_a_document_number_and_no_lookup_is_unknown(tmp_path, ais, monkeypatch):
    store, batch = batch_of(tmp_path, ais)
    jde_post.dry_run(store, batch)
    out = ais._out
    monkeypatch.setattr(ais, "_out", lambda key, row: out(key, {k: v for k, v in row.items() if k != "document"}))
    jde_post.post(store, batch)
    row = jde_post.postings(store, batch)[0]
    assert row["status"] == jde_post.UNKNOWN and row["maybe_sent"] == 1 and jde_post.undo_problem(store, batch)


def test_an_answer_not_understood_is_unknown(tmp_path, ais, monkeypatch):
    store, batch = batch_of(tmp_path, ais)
    jde_post.dry_run(store, batch)
    out = ais._out

    def odd(key, row):
        if key == "voucher_create" and row.get("result") == "CREATED":
            row = {**row, "result": "MAYBE"}
        return out(key, row)

    monkeypatch.setattr(ais, "_out", odd)
    jde_post.post(store, batch)
    row = jde_post.postings(store, batch)[0]
    assert row["status"] == jde_post.UNKNOWN and "MAYBE" in row["error"]


def test_a_jde_error_on_create_is_looked_up_before_failed(tmp_path, ais):
    store, batch = batch_of(tmp_path, ais)
    jde_post.dry_run(store, batch)
    ais.data["suppliers"] = [s for s in ais.data["suppliers"] if s["an8"] != "4001"]  # refused in FINAL
    jde_post.post(store, batch)
    row = jde_post.postings(store, batch)[0]
    assert row["status"] == jde_post.FAILED and "Address Number 4001 Invalid" in row["error"]
    assert len(ais.orchestration_calls("voucher_status")) == 1 and row["maybe_sent"] == 0
    assert jde_post.undo_problem(store, batch) == ""  # proved absent from JDE: fix, dry run again
    assert jde_post.dry_run(store, batch)["rows"][0]["status"] == jde_post.REFUSED


def test_a_jde_error_on_create_without_a_lookup_is_unknown(tmp_path, ais):
    store, batch = batch_of(tmp_path, ais)
    jde_post.dry_run(store, batch)
    ais.data["suppliers"] = [s for s in ais.data["suppliers"] if s["an8"] != "4001"]
    ais.denied.add("voucher_status")  # the status orchestration can't be run
    jde_post.post(store, batch)
    row = jde_post.postings(store, batch)[0]
    assert row["status"] == jde_post.UNKNOWN and jde_post.undo_problem(store, batch)


def test_a_status_lookup_that_cant_be_read_is_not_taken_as_not_found(tmp_path, ais, monkeypatch):
    store, batch = lost_answer(tmp_path, ais)
    out = ais._out
    monkeypatch.setattr(ais, "_out", lambda key, row: {} if key == "voucher_status" else out(key, row))
    jde_post.retry_queue(store)
    assert statuses(store, batch)[1] == jde_post.UNKNOWN and len(finals(ais)) == 1


# --- A5: no retry of a voucher on 502 / 504 -------------------------------------------------------------------------


@pytest.mark.parametrize("busy", [502, 504])
def test_502_or_504_on_a_voucher_is_never_sent_again_blind(tmp_path, ais, busy):
    store, batch = batch_of(tmp_path, ais)
    jde_post.dry_run(store, batch)
    ais.script("voucher_create", busy, busy, busy)
    result = jde_post.post(store, batch)
    assert len(finals(ais)) == 1 and result["unknown"] == 1
    assert statuses(store, batch)[1] == jde_post.UNKNOWN and jde_post.undo_problem(store, batch)
    ais.script("voucher_create")  # (nothing scripted any more)
    ais.scripts.clear()
    jde_post.retry_queue(store)  # looked up first: JDE has none under the key, so it is sent, once
    assert statuses(store, batch)[1] == jde_post.POSTED and len(finals(ais)) == 2
    assert len(ais.orchestration_calls("voucher_status")) == 1


# --- A6: compare-and-set --------------------------------------------------------------------------------------------


def test_the_queue_never_picks_up_a_voucher_being_sent(tmp_path, ais):
    store, batch = batch_of(tmp_path, ais)
    jde_post.dry_run(store, batch)
    ais.slow_seconds = 1.5  # made at once, answered after 1.5 s (the client waits 2 s)
    ais.script("voucher_create", "slow-after")
    posting = threading.Thread(target=jde_post.post, args=(store, batch))
    posting.start()
    deadline = time.monotonic() + 1.5  # a slow computer may take a while to start sending (B-1307)
    while statuses(store, batch)[1] != jde_post.SENDING and time.monotonic() < deadline:
        time.sleep(0.01)
    assert statuses(store, batch)[1] == jde_post.SENDING
    assert jde_post.waiting(store, due_only=True) == []
    jde_post.retry_queue(store)
    posting.join()
    assert len(finals(ais)) == 1 and ais.orchestration_calls("voucher_status") == []
    assert statuses(store, batch)[1] == jde_post.POSTED


def test_a_voucher_left_sending_by_a_stop_is_looked_up_never_resent(tmp_path, ais):
    store, batch = batch_of(tmp_path, ais)
    jde_post.dry_run(store, batch)
    jde_post.post(store, batch)
    stale = (dt.datetime.now() - dt.timedelta(minutes=jde_post.SENDING_MINUTES + 1)).isoformat(timespec="seconds")
    with store._conn() as conn:  # AP Coder stopped after sending, before the answer was kept
        conn.execute("UPDATE jde_postings SET status = 'sending', document = '', sending_at = ?", (stale,))
    assert len(jde_post.waiting(store)) == 1
    jde_post.retry_queue(store)
    row = jde_post.postings(store, batch)[0]
    assert row["status"] == jde_post.POSTED and row["document"] == "10001" and len(finals(ais)) == 1


def test_an_undo_while_the_queue_runs_never_posts_the_undone_batch(tmp_path, ais, monkeypatch):
    store, batch = batch_of(tmp_path, ais, ON, QC)
    jde_post.dry_run(store, batch)
    ais.script("voucher_create", 503, 503, 503)
    jde_post.post(store, batch)
    assert set(statuses(store, batch).values()) == {jde_post.QUEUED}
    sent_before = len(finals(ais))
    real = jde_post._status
    undone = []

    def status(client, key):
        if not undone:  # someone undoes the batch while the queue looks up its first voucher
            undone.append(jde_post.undo_batch(store, batch, actor="x"))
        return real(client, key)

    monkeypatch.setattr(jde_post, "_status", status)
    result = jde_post.retry_queue(store)
    assert undone == [2] and result["posted"] == 0
    assert len(finals(ais)) == sent_before and len(ais.vouchers()) == 1  # nothing sent for the undone batch
    assert jde_post.postings(store, batch) == []


def test_an_undo_is_refused_while_a_voucher_is_being_sent(tmp_path, ais):
    store, batch = batch_of(tmp_path, ais)
    jde_post.dry_run(store, batch)
    row = jde_post.postings(store, batch)[0]
    assert jde_post._claim(store, row, 1) is not None
    with pytest.raises(jde_post.Locked):
        jde_post.undo_batch(store, batch)
    assert store.batch_invoice_ids(batch) == [1]  # still exported
    assert jde_post._claim(store, row, 1) is None  # claimed once only


def test_no_row_is_made_again_for_an_undone_batch(tmp_path, ais):
    store, batch = batch_of(tmp_path, ais)
    jde_post.dry_run(store, batch)
    row = jde_post.postings(store, batch)[0]
    jde_post.undo_batch(store, batch)
    jde_post._put(store, row["idem_key"], 1, batch, status=jde_post.QUEUED)
    assert jde_post.postings(store, batch) == []


def test_a_dry_run_never_writes_over_a_voucher_that_went_out_meanwhile(tmp_path, ais, monkeypatch):
    store, batch = batch_of(tmp_path, ais)
    jde_post.dry_run(store, batch)
    send = jde_post._send

    def meanwhile(client, payload, mode, key=""):
        answer = send(client, payload, mode, key)
        with store._conn() as conn:  # the post claimed it while the dry run was asking JDE
            conn.execute("UPDATE jde_postings SET status = 'sending', maybe_sent = 1")
        return answer

    monkeypatch.setattr(jde_post, "_send", meanwhile)
    jde_post.dry_run(store, batch)
    assert statuses(store, batch)[1] == jde_post.SENDING
    for status in (jde_post.UNKNOWN, jde_post.POSTED):
        with store._conn() as conn:
            conn.execute("UPDATE jde_postings SET status = ?", (status,))
        jde_post.dry_run(store, batch)
        assert statuses(store, batch)[1] == status


# --- A7: the batch files --------------------------------------------------------------------------------------------


def test_the_batch_files_leave_out_every_voucher_jde_may_hold(tmp_path, ais):
    store, batch = lost_answer(tmp_path, ais, ON, QC)
    data, _name, _kind = exports.jde_file(store, batch)
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        readme = zf.read("README.txt").decode("utf-8")
        headers = zf.read(next(n for n in zf.namelist() if "F0411Z1" in n)).decode("utf-8")
    assert "check in JD Edwards before loading" in readme
    loaded = {r["VLVINV"] for r in csv.DictReader(io.StringIO(headers))}
    assert store.get_invoice(1)["final_output"]["invoice_number"] not in loaded  # unknown: JDE may have it
    assert store.get_invoice(2)["final_output"]["invoice_number"] in loaded  # queued, never reached JDE
    assert set(jde_post.left_out_of_files(store, batch)) == {1}


def test_the_batch_files_keep_a_voucher_never_sent(tmp_path, ais):
    store, batch = batch_of(tmp_path, ais, ON, QC)
    jde_post.dry_run(store, batch)
    assert jde_post.left_out_of_files(store, batch) == {}


# --- A8: a restore --------------------------------------------------------------------------------------------------


def test_a_restore_never_forgets_a_voucher_jde_holds(tmp_path, ais):
    store, batch = batch_of(tmp_path, ais, ON, QC)
    jde_post.dry_run(store, batch)
    backup = store.backup_now("before-posting")  # holds the batch, both vouchers only checked
    key_before = jde_post.installation_id(store)
    ais.script("voucher_create", 200)  # the first is posted, the second's answer is lost
    connect(store, ais, timeout=0.15)
    ais.scripts.clear()
    ais.script("voucher_create", None, "slow-after")
    jde_post.post(store, batch)
    assert statuses(store, batch) == {1: jde_post.POSTED, 2: jde_post.UNKNOWN}
    store.restore_from(backup)
    rows = {p["invoice_id"]: p for p in jde_post.postings(store, batch)}
    assert rows[1]["status"] == jde_post.POSTED and rows[1]["document"] == "10001"
    assert rows[2]["status"] == jde_post.UNKNOWN and rows[2]["maybe_sent"] == 1
    assert all(p["verify"] for p in rows.values())
    assert store.get_invoice(1)["meta"]["jde_voucher"]["document"] == "10001"
    assert "can't be undone" in jde_post.undo_problem(store, batch)
    assert jde_post.installation_id(store) != key_before  # new keys from here on
    with pytest.raises(ValueError, match="Nothing in this batch is ready"):
        jde_post.post(store, batch)
    connect(store, ais)
    jde_post.retry_queue(store)
    assert statuses(store, batch) == {1: jde_post.POSTED, 2: jde_post.POSTED}
    assert len(finals(ais)) == 2  # each sent once


def test_after_a_restore_a_checked_voucher_is_looked_up_before_it_is_sent(tmp_path, ais):
    store, batch = batch_of(tmp_path, ais)
    jde_post.dry_run(store, batch)
    backup = store.backup_now("checked")
    store.restore_from(backup)
    assert jde_post.postings(store, batch)[0]["verify"] == 1
    jde_post.post(store, batch)
    assert len(ais.orchestration_calls("voucher_status")) == 1 and statuses(store, batch)[1] == jde_post.POSTED
