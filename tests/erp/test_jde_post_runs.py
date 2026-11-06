"""A posting run of many vouchers through the stand-in AIS (ap_coder/erp/jde_post.py, D-182): it goes on past a late
answer (each voucher its own outcome, a late one looked up by its key, never sent again blind), within its time, with
the holds and the open period checked just before each voucher is sent; never a voucher posted twice."""

from __future__ import annotations

import io
import json
import time
import zipfile

import pytest

from ap_coder.erp import jde, jde_ais, jde_post
from ap_coder.web.pages import exports
from tests.erp.test_jde import AB, BC, ON, QC
from tests.erp.test_jde_post import batch_of, connect
from tests.fake_ais import PASSWORD, FakeAis


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


def settings(store, **changes) -> None:
    """Settings → JD Edwards E1 with these changed (as saved JSON: what the page keeps)."""
    store.set_setting(jde.SETTING_KEY, json.dumps({**json.loads(jde.load(store).to_json()), **changes}))


# --- A late answer ---------------------------------------------------------------------------------------------------


def test_a_late_answer_never_stops_the_run(tmp_path, ais):
    """B-1857: one voucher answered past the timeout queued every voucher after it, so with R04110ZA often late a
    batch went out one voucher per five minutes. The late one is unknown (looked up by its key later); the run goes
    on."""
    store, batch = batch_of(tmp_path, ais, ON, QC, BC)
    connect(store, ais, timeout=0.2)
    jde_post.dry_run(store, batch)
    ais.script("voucher_create", "slow-after")  # the first is made, its answer comes too late
    result = jde_post.post(store, batch)
    assert statuses(store, batch) == {1: jde_post.UNKNOWN, 2: jde_post.POSTED, 3: jde_post.POSTED}
    assert (result["posted"], result["unknown"], result["queued"], result["failed"]) == (2, 1, 0, 0)
    assert len(finals(ais)) == 3


def test_a_lost_answer_is_never_sent_again_while_jde_may_still_be_making_it(tmp_path, ais):
    """B-1858: a voucher whose answer was lost was looked up and, not found, sent again at once ("Try again now" a few
    seconds later): JD Edwards may still be making the first (R04110ZA running), so it could be made twice. Not found
    is taken as not there only once the send is older than Settings → JD Edwards E1's wait (``resend_minutes``)."""
    store, batch = batch_of(tmp_path, ais)
    connect(store, ais, timeout=0.15)
    settings(store, resend_minutes=10)
    jde_post.dry_run(store, batch)
    ais.script("voucher_create", "late-made")  # still being made when the client gives up
    jde_post.post(store, batch)
    assert statuses(store, batch)[1] == jde_post.UNKNOWN
    jde_post.retry_queue(store)  # a person's "Try again now" at once
    row = jde_post.postings(store, batch)[0]
    assert len(finals(ais)) == 1 and row["status"] == jde_post.UNKNOWN and row["maybe_sent"] == 1
    assert jde_post.undo_problem(store, batch)  # still locked: JD Edwards may have it
    time.sleep(0.7)  # JD Edwards made it meanwhile
    settings(store, resend_minutes=0.01)
    time.sleep(0.7)
    jde_post.retry_queue(store)
    assert statuses(store, batch)[1] == jde_post.POSTED and len(finals(ais)) == 1 and len(ais.vouchers()) == 2


def test_late_answers_are_looked_up_in_the_same_run_once_jde_could_have_finished(tmp_path, ais, monkeypatch):
    """The late ones are asked for by their key after the others were sent (D-182): found, posted in the same run;
    each voucher's outcome is the run's progress as it goes."""
    monkeypatch.setattr(jde_post, "LOOK_AFTER", 0.0)
    store, batch = batch_of(tmp_path, ais, ON, QC, BC)
    connect(store, ais, timeout=0.2)
    jde_post.dry_run(store, batch)
    ais.script("voucher_create", "slow-after")
    seen = []
    result = jde_post.post(store, batch, progress=seen.append)
    assert set(statuses(store, batch).values()) == {jde_post.POSTED} and result["posted"] == 3
    assert len(finals(ais)) == 3 and len(ais.orchestration_calls("voucher_status")) == 1
    assert [(s["done"], s["total"], s["posted"], s["unknown"]) for s in seen] == [
        (1, 3, 0, 1), (2, 3, 1, 1), (3, 3, 2, 1), (3, 3, 3, 0)]  # fmt: skip


def test_late_answers_one_after_another_stop_the_run_and_the_rest_wait_saying_why(tmp_path, ais):
    store, batch = batch_of(tmp_path, ais, ON, QC, BC, AB)
    connect(store, ais, timeout=0.15)
    jde_post.dry_run(store, batch)
    ais.plan = lambda key, inputs: "slow" if key == "voucher_create" else None  # R04110ZA hung: nothing answers
    result = jde_post.post(store, batch)
    assert statuses(store, batch) == {1: "unknown", 2: "unknown", 3: "unknown", 4: "queued"}
    assert len(finals(ais)) == jde_post.LATE_STOP == 3 and "3 vouchers in a row late" in result["error"]
    assert "in a row late" in jde_post.postings(store, batch)[3]["error"]  # said on the batch's panel
    ais.plan = None
    jde_post.retry_queue(store)  # JD Edwards answers again: the three looked up (none made), then each sent once
    assert set(statuses(store, batch).values()) == {jde_post.POSTED} and len(finals(ais)) == 7


def test_a_run_keeps_to_its_time_and_the_queue_sends_the_rest(tmp_path, ais):
    store, batch = batch_of(tmp_path, ais, ON, QC, BC)
    settings(store, post_minutes=0.01)  # 0.6 seconds
    jde_post.dry_run(store, batch)
    ais.latency = 0.4  # R04110ZA's run
    result = jde_post.post(store, batch)
    assert (result["posted"], result["queued"]) == (2, 1) and "time (0.01 minutes" in result["error"]
    assert statuses(store, batch)[3] == jde_post.QUEUED and "ran out" in jde_post.postings(store, batch)[2]["error"]
    ais.latency = 0
    jde_post.retry_queue(store)
    assert set(statuses(store, batch).values()) == {jde_post.POSTED} and len(finals(ais)) == 3


# --- Holds ------------------------------------------------------------------------------------------------------------


def test_a_vendor_put_on_hold_after_the_dry_run_is_not_paid(tmp_path, ais):
    """B-1859 (R3): a supplier JD Edwards put on hold after its invoice was approved was posted all the same: the
    hold was not looked at again before sending. Just before each voucher, the hold as the last supplier sync left it
    (or AP Coder's own) is read: held, it is not sent but waits in the queue, saying why, until the hold is lifted."""
    store, batch = batch_of(tmp_path, ais, ON, QC)
    jde_post.dry_run(store, batch)
    with store._conn() as conn:  # the nightly supplier sync: 4001 has hold code H in JD Edwards now
        conn.execute("UPDATE vendors SET erp_hold = 'H' WHERE erp_id = '4001' AND in_master = 1")
    result = jde_post.post(store, batch)
    assert finals(ais) == [] and result["queued"] == 2 and result["posted"] == 0
    row = jde_post.postings(store, batch)[0]
    assert row["status"] == jde_post.QUEUED and "on hold in JD Edwards (hold code H" in row["error"]
    assert row["maybe_sent"] == 0 and any("on hold" in s for s in result["skipped"])
    jde_post.retry_queue(store)
    assert finals(ais) == []  # still on hold
    with store._conn() as conn:
        conn.execute("UPDATE vendors SET erp_hold = '' WHERE erp_id = '4001'")
    jde_post.retry_queue(store)
    assert set(statuses(store, batch).values()) == {jde_post.POSTED} and len(finals(ais)) == 2


def test_a_vendor_on_hold_in_ap_coder_is_not_sent_and_its_dry_run_says_so(tmp_path, ais):
    store, batch = batch_of(tmp_path, ais)
    key = store.get_invoice(1)["vendor_key"]
    store.save_vendor(key, "Northwind IT Solutions Inc.", "on_hold", notes="bank details being checked")
    row = jde_post.dry_run(store, batch)["rows"][0]
    assert row["status"] == jde_post.REFUSED and "on hold in AP Coder's vendor list" in row["error"]
    assert "bank details being checked" in row["error"] and ais.orchestration_calls("voucher_create") == []


# --- The Z-tables' keys ----------------------------------------------------------------------------------------------


def _zkeys(store, batch) -> list[tuple[str, str]]:
    found = jde_post.payloads(store, batch)[0].values()
    return [(p["pay_items"][0]["VLEDBT"], p["pay_items"][0]["VLEDTN"]) for p in found]


def test_two_data_folders_and_a_restore_never_send_the_same_z_table_keys(tmp_path, ais):
    """B-1860 (R6): VLEDBT (prefix + batch) and VLEDTN (APC + invoice id) carried nothing of the data folder, so a
    pilot laptop and the server posting to one JD Edwards, or a database put back from a backup handing out its
    numbers again, sent the same F0411Z1 keys for other vouchers: route (a) found the other's rows (EXISTS, then
    "not this invoice") and a batch file load hit duplicate keys. Each now carries a code of its batch and invoice
    (its file and when it was added), within JD Edwards' 15 and 22 characters, the same each time it is sent."""
    for laptop in ("laptop1", "laptop2"):
        (tmp_path / laptop).mkdir()
        (tmp_path / laptop / "1.pdf").write_bytes(f"{laptop}'s invoice".encode())
    a, ba = batch_of(tmp_path / "laptop1", ais, ON)
    b, bb = batch_of(tmp_path / "laptop2", ais, ON)  # invoice 1 of batch 1 there too
    (ka,), (kb,) = _zkeys(a, ba), _zkeys(b, bb)
    assert ka[0] != kb[0] and ka[1] != kb[1], (ka, kb)
    assert ka[0].startswith("APC1-") and len(ka[0]) <= 15 and ka[1].startswith("APC1-") and len(ka[1]) <= 22
    assert _zkeys(a, ba) == [ka]  # the same each time (the dry run, the go-ahead, the queue, the ZIP)
    with zipfile.ZipFile(io.BytesIO(exports.jde_file(a, ba)[0])) as zf:
        header = zf.read(jde.HEADER_FILE).decode().splitlines()[1]
        readme = zf.read("README.txt").decode()
    assert ka[0] in header and ka[1] in header and f"Batch (EDBT): {ka[0]}" in readme
    backup = a.backup_now("before batch 2")
    (tmp_path / "laptop1" / "2.pdf").write_bytes(b"the second invoice")
    final = a.get_invoice(1)["final_output"]
    second = a.add_invoice(tmp_path / "laptop1" / "2.pdf", {**final, "invoice_number": "NW-2"}, {})
    a.approve_invoice(second, {**final, "invoice_number": "NW-2"}, "Jane")
    before = _zkeys(a, a.create_export_batch([second], jde.FORMAT))
    a.restore_from(backup)
    (tmp_path / "laptop1" / "2b.pdf").write_bytes(b"another invoice, after the restore")
    again = a.add_invoice(tmp_path / "laptop1" / "2b.pdf", {**final, "invoice_number": "NW-3"}, {})
    a.approve_invoice(again, {**final, "invoice_number": "NW-3"}, "Jane")
    batch = a.create_export_batch([again], jde.FORMAT)
    assert (again, batch) == (second, 2)  # the same numbers handed out again
    (after,) = _zkeys(a, batch)
    assert after[0] != before[0][0] and after[1] != before[0][1], (before, after)


def test_a_voucher_waits_as_long_as_r04110za_may_take_not_the_connections_timeout(tmp_path, ais):
    store, batch = batch_of(tmp_path, ais)
    connect(store, ais, timeout=0.2)
    settings(store, voucher_timeout=3)  # R04110ZA takes longer than a read
    jde_post.dry_run(store, batch)
    ais.latency = 0.5
    assert jde_post.post(store, batch)["posted"] == 1 and jde.load(store).voucher_timeout == 3
    assert jde.JdeSettings().voucher_timeout == 120
    assert jde.JdeSettings.from_json('{"voucher_timeout": "x", "post_minutes": -1}').voucher_timeout == 120
