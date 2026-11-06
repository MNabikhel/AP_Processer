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


@pytest.mark.slow
def test_500_vouchers_post_in_one_run_through_slow_and_late_answers_none_twice(tmp_path, ais, monkeypatch):
    """The scale goal's target 4 (D-182): 500 vouchers through the stand-in, each answered after R04110ZA's run, one in
    ten late past the voucher's wait (half of those made all the same, half never made) and one in twenty refused.
    One go-ahead sends them all; the late ones are looked up by their key (the made ones posted, the others sent once
    more once JD Edwards could have finished them), the last few by the queue right after. Every voucher ends posted
    or refused, none is made twice, and the times are printed (``-s``)."""
    from ap_coder.core.store import Store
    from tests.erp.test_jde import _invoice

    monkeypatch.setattr(jde_post, "LOOK_AFTER", 2.0)
    store = Store(tmp_path / "ap_coder.db")
    jde.save(store, jde.JdeSettings(default_bu="CC100", bu_from_cost_center=False, voucher_timeout=0.3,
                                    resend_minutes=0.05))  # fmt: skip
    final = _invoice(ON, 1)["final_output"]
    store.import_vendor_master([{"vendor_name": final["vendor_name"], "erp_id": "4001"}])
    began = time.perf_counter()
    ids = []
    for n in range(500):
        doc = {**final, "invoice_number": f"P-{n:04d}"}
        ids.append(store.add_invoice(tmp_path / f"{n}.pdf", doc, {}))
        store.approve_invoice(ids[-1], doc, "Jane")
    batch = store.create_export_batch(ids, jde.FORMAT)
    for line in next(iter(jde_post.payloads(store, batch)[0].values()))["gl_lines"]:
        bu, obj = line["VNANI"].split(".")[:2]
        ais.data["gl_accounts"].append(
            {
                "account_id": line["VNANI"],
                "business_unit": bu,
                "object": obj,
                "subsidiary": "",
                "description": "",
                "posting_edit": "",
                "level": "7",
            }
        )
    connect(store, ais)
    made_at = time.perf_counter()
    assert [r["status"] for r in jde_post.dry_run(store, batch)["rows"]] == [jde_post.CHECKED] * 500
    checked_at = time.perf_counter()

    sent: set[int] = set()

    def plan(key, inputs):
        if key != "voucher_create" or inputs.get("mode") != "FINAL":
            return None
        n = int(inputs["pay_items"][0]["VLVINV"][2:])
        if n % 20 == 3:
            return ("refuse", "0008", "Error: Business Unit CC100 Invalid")
        first, _ = n not in sent, sent.add(n)
        return {7: "slow-after", 13: "slow"}.get(n % 20) if first else None  # late once: made, or never made

    ais.plan, ais.latency, ais.slow_seconds = plan, 0.02, 0.6
    seen = []
    result = jde_post.post(store, batch, actor="sam", progress=seen.append)
    posted_at = time.perf_counter()
    after_run = {s: sum(1 for p in jde_post.postings(store, batch) if p["status"] == s)
                 for s in (jde_post.POSTED, jde_post.FAILED, jde_post.UNKNOWN, jde_post.QUEUED)}  # fmt: skip
    assert after_run[jde_post.QUEUED] == 0 and result["error"] == "" and seen[-1]["done"] == 500
    time.sleep(3.5)  # the last late ones: JD Edwards could have finished them
    jde_post.retry_queue(store)
    done_at = time.perf_counter()
    rows = jde_post.postings(store, batch)
    assert sorted({p["status"] for p in rows}) == [jde_post.FAILED, jde_post.POSTED]
    refused = [p for p in rows if p["status"] == jde_post.FAILED]
    assert len(refused) == 25 and all("Business Unit CC100 Invalid" in p["error"] for p in refused)
    made = [v["invoice_number"] for v in ais.vouchers() if v["invoice_number"].startswith("P-")]
    assert len(made) == len(set(made)) == 475 == len({p["document"] for p in rows if p["document"]})  # none twice
    assert len(finals(ais)) == 500 + 25  # the 25 never made sent once more, after their lookup said so
    print(
        f"\n500 vouchers: approved and batched {made_at - began:.1f} s, dry run {checked_at - made_at:.1f} s, "
        f"go-ahead run {posted_at - checked_at:.1f} s ({after_run}), queue after it {done_at - posted_at:.1f} s "
        f"(3.5 s of it waiting): {sum(p['status'] == jde_post.POSTED for p in rows)} posted, {len(refused)} refused, "
        f"{len(finals(ais))} sends, {len(ais.orchestration_calls('voucher_status'))} lookups"
    )


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


# --- Fiscal periods --------------------------------------------------------------------------------------------------


def _sent_gl_dates(fake: FakeAis) -> dict[str, int]:
    return {c["PayItems"][0]["VLVINV"]: c["PayItems"][0]["VLDGJ"] for c in finals(fake)}


def test_an_invoice_dated_in_a_closed_period_is_posted_in_the_open_one_said_and_confirmed(tmp_path, ais):
    """B-1861 (R5): AP Coder knew nothing of JD Edwards' periods: a September invoice approved after September was
    closed went with its invoice date as the G/L date, and R04110ZA refused it (or it landed in a year still open).
    With the open period set (Settings → JD Edwards E1), its G/L date is moved to the first day of it: said on the
    review screen and in the dry run, and the go-ahead that posts it is kept with the vouchers moved."""
    store, batch = batch_of(tmp_path, ais, ON, QC)  # dated 14 September and 1 October
    settings(store, open_from="2026-10-01")
    rows = {r["invoice_id"]: r for r in jde_post.dry_run(store, batch)["rows"]}
    assert rows[1]["status"] == jde_post.CHECKED and rows[1]["error"].startswith("G/L date moved to 2026-10-01")
    assert "2026-09-14" in rows[1]["error"] and rows[2]["error"] == ""
    jde_post.post(store, batch, actor="sam")
    assert _sent_gl_dates(ais) == {"NW-2026-0912": jde.julian("2026-10-01"), "SIL-4471": jde.julian("2026-10-01")}
    assert finals(ais)[0]["PayItems"][0]["VLDIVJ"] == jde.julian("2026-09-14")  # the invoice date stays
    assert {line["VNDGJ"] for line in finals(ais)[0]["GLLines"]} == {jde.julian("2026-10-01")}
    (approved,) = store.events(actions=["jde_api_approved"])
    assert approved["detail"]["gl_moved"] == [1]


def test_with_closed_periods_held_an_invoice_in_one_is_never_posted(tmp_path, ais):
    store, batch = batch_of(tmp_path, ais, ON, QC)
    settings(store, open_from="2026-10-01", closed_period=jde.CLOSED_HOLD)
    rows = {r["invoice_id"]: r for r in jde_post.dry_run(store, batch)["rows"]}
    assert rows[1]["status"] == jde_post.REFUSED and "closed period" in rows[1]["error"]
    assert rows[2]["status"] == jde_post.CHECKED
    jde_post.post(store, batch)
    assert list(_sent_gl_dates(ais)) == ["SIL-4471"]


def test_a_period_closed_while_a_batch_is_posted_holds_the_rest_back_saying_why(tmp_path, ais):
    store, batch = batch_of(tmp_path, ais, QC, BC)  # 1 and 5 October
    settings(store, open_from="2026-10-01")
    jde_post.dry_run(store, batch)

    def close_october(key, inputs):  # the first voucher goes; meanwhile October is closed in JD Edwards and here
        if inputs.get("mode") == "FINAL":
            settings(store, open_from="2026-11-01")
        return None

    ais.plan = close_october
    result = jde_post.post(store, batch)
    rows = {p["invoice_id"]: p for p in jde_post.postings(store, batch)}
    assert rows[1]["status"] == jde_post.POSTED and len(finals(ais)) == 1 and result["posted"] == 1
    assert rows[2]["status"] == jde_post.CHECKED and jde_post.waiting(store) == []  # waits for a new dry run
    assert "closed" in rows[2]["error"] and "dry run again" in rows[2]["error"]
    assert any("closed" in s for s in result["skipped"])
    ais.plan = None
    jde_post.dry_run(store, batch)  # its G/L date then moves to 1 November
    jde_post.post(store, batch)
    assert _sent_gl_dates(ais)["PO-77120"] == jde.julian("2026-11-01")


def test_jd_edwards_refusing_a_g_l_date_in_a_closed_period_holds_back_the_others_of_that_month(tmp_path, ais):
    store, batch = batch_of(tmp_path, ais, QC, BC, ON)  # 1 and 5 October, 14 September
    jde_post.dry_run(store, batch)
    ais.plan = lambda key, inputs: ("refuse", "PBCO", "Error: G/L Date Before Cut Off (PBCO)") if inputs.get(
        "mode") == "FINAL" and inputs["pay_items"][0]["VLVINV"] == "SIL-4471" else None  # fmt: skip
    result = jde_post.post(store, batch)
    rows = {p["invoice_id"]: p for p in jde_post.postings(store, batch)}
    assert rows[1]["status"] == jde_post.FAILED and "PBCO" in rows[1]["error"]
    assert rows[2]["status"] == jde_post.CHECKED and "AP Coder #1" in rows[2]["error"]  # October: not sent
    assert rows[3]["status"] == jde_post.CHECKED  # September: before it, closed too
    assert len(finals(ais)) == 1 and result["failed"] == 1


def test_the_open_period_on_the_files_and_the_review_screen():
    from tests.erp.test_jde import AN8, _invoice

    sept, nov = _invoice(ON, 1), _invoice(QC, 2, invoice_date="2026-11-02")
    moved = jde.JdeSettings(amount_mode=jde.AMOUNT_GROSS, open_from="2026-10-01", open_to="2026-10-31")
    files = jde.export_batch([sept], moved, AN8, batch=7)
    head = files[jde.HEADER_FILE].decode().splitlines()
    assert str(jde.julian("2026-10-01")) in head[1] and str(jde.julian("2026-09-14")) in head[1]
    found = {code: (severity, text) for severity, code, text, _ in jde.voucher_findings(sept["final_output"], moved,
                                                                                         "4001")}  # fmt: skip
    assert found["JDE_GL_DATE_MOVED"][0] == jde.WARNING and "2026-10-01" in found["JDE_GL_DATE_MOVED"][1]
    assert "after the open period" in " ".join(jde.validate([nov], moved, AN8)[2])  # never moved forward or back
    held = jde.JdeSettings.from_json(json.dumps({**json.loads(moved.to_json()), "closed_period": jde.CLOSED_HOLD}))
    assert "closed period" in " ".join(jde.validate([sept], held, AN8)[1])
    found = {code: severity for severity, code, _, _ in jde.voucher_findings(sept["final_output"], held, "4001")}
    assert found["JDE_PERIOD_CLOSED"] == jde.ERROR and "JDE_GL_DATE_MOVED" not in found
    assert jde.JdeSettings.from_json('{"open_from": "soon", "closed_period": "x"}').open_from == ""
    assert jde.settings_problems(jde.JdeSettings(open_from="2026-11-01", open_to="2026-10-31"))


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
