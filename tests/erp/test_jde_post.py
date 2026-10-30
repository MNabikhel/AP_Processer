"""JD Edwards API step 2 (ap_coder/erp/jde_post.py, D-104) against the stand-in AIS server: the dry run, the approval,
the result kept per invoice, the queue when JDE is down or the answer is lost, and never a voucher posted twice."""

from __future__ import annotations

import pytest

from ap_coder.core.store import Store
from ap_coder.erp import jde, jde_ais, jde_post
from tests.erp.test_jde import ON, QC, _invoice
from tests.fake_ais import PASSWORD, FakeAis


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setattr(jde_ais, "RETRY_PAUSE", 0)
    monkeypatch.setenv(jde_ais.PASSWORD_ENV, PASSWORD)


@pytest.fixture
def ais():
    with FakeAis(slow_seconds=0.3) as fake:
        yield fake


def connect(store: Store, fake: FakeAis, **changes) -> None:
    store.set_setting(jde_ais.SETTING_KEY, fake.settings(**changes).to_json())


def batch_of(tmp_path, fake: FakeAis, *stems: str) -> tuple[Store, int]:
    """A JD Edwards batch of these approved samples, their supplier and accounts known to the stand-in's JDE."""
    store = Store(tmp_path / "ap_coder.db")
    jde.save(store, jde.JdeSettings(default_bu="CC100", bu_from_cost_center=False))
    ids = []
    for n, stem in enumerate(stems or (ON,), 1):
        final = _invoice(stem, n)["final_output"]
        store.import_vendor_master([{"vendor_name": final["vendor_name"], "erp_id": "4001"}])
        inv = store.add_invoice(tmp_path / f"{n}.pdf", final, {})
        store.approve_invoice(inv, final, "Jane")
        ids.append(inv)
    batch = store.create_export_batch(ids, jde.FORMAT)
    for payload in jde_post.payloads(store, batch)[0].values():
        for line in payload["gl_lines"]:
            bu, obj = line["VNANI"].split(".")[:2]
            fake.data["gl_accounts"].append(
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
    connect(store, fake)
    return store, batch


def test_dry_run_then_post_keeps_the_voucher_number(tmp_path, ais):
    store, batch = batch_of(tmp_path, ais)
    found = jde_post.dry_run(store, batch, actor="jane")
    assert found["jde"] == "" and [r["status"] for r in found["rows"]] == [jde_post.CHECKED]
    assert found["report"]["vouchers"][0]["an8"] == "4001"
    proof = ais.orchestration_calls("voucher_create")
    assert proof[0]["ProcessingMode"] == "PROOF" and proof[0]["IdempotencyKey"] == jde_post.idem_key(
        1, 1, jde_post.installation_id(store)
    )
    assert proof[0]["PayItems"][0]["VLEDBT"] == f"APC{batch}" and proof[0]["PayItems"][0]["VLEDTN"] == "APC1"
    assert proof[0]["GLLines"] and ais.vouchers()[-1]["invoice_number"] == "INV-7001"  # nothing posted by the proof
    result = jde_post.post(store, batch, actor="sam")
    assert result["posted"] == 1 and result["error"] == ""
    row = jde_post.postings(store, batch)[0]
    assert row["status"] == jde_post.POSTED and row["document"] == "10001" and row["doc_type"] == "PV"
    assert row["approved_by"] == "sam" and row["checked_by"] == "jane"
    assert ais.vouchers()[-1]["invoice_number"] == "NW-2026-0912"
    assert jde_post.status_rows(store, batch)[0]["voucher"] == "10001 PV 00001"
    voucher = store.get_invoice(1)["meta"]["jde_voucher"]  # on the invoice, for the finance hub feed
    assert {k: voucher[k] for k in ("document", "doc_type", "company")} == {
        "document": "10001",
        "doc_type": "PV",
        "company": "00001",
    } and voucher["posted_at"]
    assert store.events(invoice_id=1, actions=["jde_voucher_posted"])[0]["detail"]["voucher"] == "10001 PV"
    with pytest.raises(ValueError, match="Nothing in this batch is ready"):
        jde_post.post(store, batch)  # posted: never again
    assert jde_post.dry_run(store, batch)["rows"][0]["status"] == jde_post.POSTED  # a dry run doesn't touch it
    assert len([c for c in ais.orchestration_calls("voucher_create") if c["ProcessingMode"] == "FINAL"]) == 1
    assert "can't be undone" in jde_post.undo_problem(store, batch)


def test_jde_refusing_a_voucher_is_kept_in_its_words(tmp_path, ais):
    store, batch = batch_of(tmp_path, ais)
    ais.data["suppliers"] = [s for s in ais.data["suppliers"] if s["an8"] != "4001"]
    found = jde_post.dry_run(store, batch)
    row = found["rows"][0]
    assert row["status"] == jde_post.REFUSED and row["error"] == "Error: Address Number 4001 Invalid (0002)"
    with pytest.raises(ValueError):
        jde_post.post(store, batch)


def test_an_invoice_held_back_here_is_refused_without_asking_jde(tmp_path, ais):
    store, batch = batch_of(tmp_path, ais)
    store.import_vendor_master([{"vendor_name": "Northwind IT Solutions Inc.", "erp_id": ""}])
    with store._conn() as conn:
        conn.execute("UPDATE vendors SET erp_id = ''")
    found = jde_post.dry_run(store, batch)
    assert found["rows"][0]["status"] == jde_post.REFUSED and "AN8" in found["rows"][0]["error"]
    assert ais.orchestration_calls("voucher_create") == []


def test_a_change_after_the_dry_run_needs_a_new_one(tmp_path, ais):
    store, batch = batch_of(tmp_path, ais)
    jde_post.dry_run(store, batch)
    jde.save(store, jde.JdeSettings(default_bu="CC100", bu_from_cost_center=False, user="OTHER"))
    with pytest.raises(ValueError):
        jde_post.post(store, batch)


def test_jde_down_queues_and_the_queue_posts_once_it_is_back(tmp_path, ais):
    store, batch = batch_of(tmp_path, ais, ON, QC)
    jde_post.dry_run(store, batch)
    ais.script("voucher_create", 503, 503, 503)
    result = jde_post.post(store, batch, actor="sam")
    assert result["queued"] == 2 and result["posted"] == 0 and "busy" in result["error"]
    assert len(jde_post.waiting(store)) == 2
    finals = [c for c in ais.orchestration_calls("voucher_create") if c["ProcessingMode"] == "FINAL"]
    assert len(finals) == 3  # the first tried (3 times: busy is retried), the second not
    found = jde_post.retry_queue(store)
    assert found["posted"] == 2
    statuses = {p["invoice_id"]: (p["status"], p["document"]) for p in jde_post.postings(store, batch)}
    assert statuses == {1: ("posted", "10001"), 2: ("posted", "10002")}
    assert jde_post.waiting(store) == []


def test_an_answer_lost_after_sending_is_looked_up_never_sent_twice(tmp_path, ais):
    store, batch = batch_of(tmp_path, ais)
    connect(store, ais, timeout=0.15)
    jde_post.dry_run(store, batch)
    ais.script("voucher_create", "slow-after")  # JDE makes the voucher, the answer comes too late
    result = jde_post.post(store, batch)
    assert result["unknown"] == 1, result
    assert jde_post.postings(store, batch)[0]["status"] == jde_post.UNKNOWN
    assert len(ais.vouchers()) == 2  # JDE has it
    found = jde_post.retry_queue(store)
    assert found["posted"] == 1
    row = jde_post.postings(store, batch)[0]
    assert row["status"] == jde_post.POSTED and row["document"] == "10001"
    finals = [c for c in ais.orchestration_calls("voucher_create") if c["ProcessingMode"] == "FINAL"]
    assert len(finals) == 1 and len(ais.orchestration_calls("voucher_status")) == 1
    assert len(ais.vouchers()) == 2


def test_jde_answers_exists_for_a_key_it_has_seen(tmp_path, ais):
    store, batch = batch_of(tmp_path, ais)
    jde_post.dry_run(store, batch)
    payload = jde_post.payloads(store, batch)[0][1]
    with jde_ais.AisClient(jde_ais.load(store)) as c:
        first = jde_post._send(c, payload, "final")
        again = jde_post._send(c, payload, "final")
    assert first["result"] == "created" and again["result"] == "exists" and again["document"] == first["document"]
    assert len(ais.vouchers()) == 2


def test_step_2_off_sends_nothing(tmp_path, ais):
    store, batch = batch_of(tmp_path, ais)
    connect(store, ais, step2=False)
    found = jde_post.dry_run(store, batch)
    assert "off" in found["jde"] and found["rows"][0]["status"] == jde_post.CHECKED  # checked here only
    with pytest.raises(jde_ais.AisOff):
        jde_post.post(store, batch)
    assert ais.orchestration_calls("voucher_create") == []


def test_undoing_a_batch_before_posting_forgets_its_dry_run(tmp_path, ais):
    store, batch = batch_of(tmp_path, ais)
    jde_post.dry_run(store, batch)
    assert jde_post.undo_problem(store, batch) == ""
    jde_post.forget_batch(store, batch)
    assert jde_post.postings(store, batch) == []


def _finals(fake: FakeAis) -> list:
    return [c for c in fake.orchestration_calls("voucher_create") if c["ProcessingMode"] == "FINAL"]


def test_a_batch_whose_file_was_downloaded_is_posted_only_on_a_persons_word(tmp_path, ais):
    """D-134: a batch's JD Edwards ZIP downloaded, then posted through the API: its vouchers twice in JD Edwards if the
    file was loaded. The post asks for a person's word that it was not, kept in the audit trail."""
    store, batch = batch_of(tmp_path, ais)
    jde_post.dry_run(store, batch)
    jde_post.file_downloaded(store, batch, "sam")
    (seen,) = jde_post.downloads(store, batch)
    assert seen["actor"] == "sam" and seen["at"]
    with pytest.raises(jde_post.FileDownloaded, match="downloaded by sam"):
        jde_post.post(store, batch, actor="jane")
    with pytest.raises(jde_post.FileDownloaded):
        jde_post.post(store, batch, actor="jane", not_loaded="  ")
    assert _finals(ais) == [] and jde_post.postings(store, batch)[0]["status"] == jde_post.CHECKED
    why = "No batch APC1 in JD Edwards' voucher entries (P0411Z1): the file was not loaded"
    assert jde_post.post(store, batch, actor="jane", not_loaded=why)["posted"] == 1
    (approved,) = store.events(actions=["jde_api_approved"])
    assert approved["actor"] == "jane" and approved["detail"]["not_loaded"] == why
    assert approved["detail"]["downloaded"] == f"sam {seen['at']}"


def test_vouchers_waiting_for_jde_are_not_sent_once_the_batch_file_was_downloaded(tmp_path, ais):
    """D-134: JD Edwards down, the vouchers wait in the queue; the batch's file downloaded meanwhile (to load it
    instead) holds them, so the queue would post them a second time: they wait for a new go-ahead instead."""
    store, batch = batch_of(tmp_path, ais)
    jde_post.dry_run(store, batch)
    ais.script("voucher_create", 503, 503, 503)
    assert jde_post.post(store, batch, actor="sam")["queued"] == 1
    jde_post.file_downloaded(store, batch, "sam")
    assert jde_post.waiting(store) == [] and jde_post.retry_queue(store)["posted"] == 0
    (row,) = jde_post.postings(store, batch)
    assert row["status"] == jde_post.CHECKED and not row["approved_at"] and "downloaded" in row["error"]
    assert len(_finals(ais)) == 3  # the three tries while JD Edwards was busy, none since
    with pytest.raises(jde_post.FileDownloaded):
        jde_post.post(store, batch, actor="sam")
