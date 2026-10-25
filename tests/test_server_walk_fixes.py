"""The fixes from an end-to-end walk of the company server, the JD Edwards API and the finance hub (B-1197 on): the
batch's dry run, the retry schedule, the secret scrubber, the AIS address, the reasons given, the undo and the release
of a voucher, the hub's links and files, the paused usernames, the dry run's proof and the sync's descriptions."""

from __future__ import annotations

import io
import zipfile

from ap_coder.erp import jde_post
from ap_coder.web.pages import exports

from .test_jde import ON, QC
from .test_jde_post_safety import ais, lost_answer, quick, statuses

__all__ = ["ais", "quick"]  # the fixtures, shared


# --- S1. The batch's DRY_RUN.txt --------------------------------------------------------------------------------


def test_the_dry_run_names_the_vouchers_held_back_for_the_api(tmp_path, ais):
    """B-1197: the batch ZIP's DRY_RUN.txt listed the vouchers held back for the API (JD Edwards has them, or may)
    with no vendor, invoice number or date."""
    store, batch = lost_answer(tmp_path, ais, ON, QC)
    assert statuses(store, batch)[1] == jde_post.UNKNOWN
    with zipfile.ZipFile(io.BytesIO(exports.jde_file(store, batch)[0])) as zf:
        dry = zf.read("DRY_RUN.txt").decode("utf-8")
    final = store.get_invoice(1)["final_output"]
    line = next(text for text in dry.splitlines() if "#1" in text)
    assert final["vendor_name"] in line and final["invoice_number"] in line and final["invoice_date"] in line, line


# --- S2. The retry schedule ---------------------------------------------------------------------------------------


def test_vouchers_never_sent_stay_on_the_short_schedule_and_one_never_reached_keeps_its_place(tmp_path, ais):
    """B-1198: a voucher never sent went to hourly retries after three runs, and one a run never reached (JD Edwards
    busy on the voucher before it, the rest skipped) was counted as tried. A voucher never sent is retried after 5,
    then 15 minutes; one not reached keeps its tries."""
    import datetime as dt

    from .test_jde_post import batch_of

    store, batch = batch_of(tmp_path, ais, ON, QC)
    jde_post.dry_run(store, batch)
    ais.script("voucher_create", 503, 503, 503)
    jde_post.post(store, batch)
    rows = {p["invoice_id"]: p for p in jde_post.postings(store, batch)}
    assert (rows[1]["attempts"], rows[2]["attempts"]) == (1, 0)  # the second never reached
    for _ in range(4):  # JD Edwards still busy: four more runs of the queue
        with store._conn() as conn:
            conn.execute("UPDATE jde_postings SET next_try = NULL")
        ais.script("voucher_status", 503, 503, 503)
        jde_post.retry_queue(store)
    rows = {p["invoice_id"]: p for p in jde_post.postings(store, batch)}
    assert rows[2]["attempts"] == 0, rows[2]
    latest = dt.datetime.now() + dt.timedelta(minutes=15, seconds=5)
    assert all(dt.datetime.fromisoformat(r["next_try"]) <= latest for r in rows.values()), rows


# --- S3. The secret scrubber ---------------------------------------------------------------------------------------


def test_a_short_secret_is_scrubbed_as_a_whole_word_never_inside_other_words():
    """B-1199: with the password ``x`` the scrubber made "e••••ample.com" of "example.com"; a secret is cut out as a
    whole word, or after password= / token: / Authorization, whatever its length, never inside another word."""
    from ap_coder.erp.jde_ais import scrub

    assert scrub("AIS at ais.example.com refused", "x") == "AIS at ais.example.com refused"
    assert scrub("the password x was refused", "x") == "the password •••• was refused"
    assert scrub("sent password=x&user=jde", "x") == "sent password=••••&user=jde"
    assert scrub("Authorization: Basic eA==", "eA==") == "Authorization: Basic ••••"
    assert scrub('{"token": "ab1"} for ab12', "ab1") == '{"token": "••••"} for ab12'


# --- S4. An AIS address on the internet is refused at save -------------------------------------------------------


def test_an_ais_address_on_the_internet_is_refused_when_it_is_saved(tmp_path, monkeypatch):
    """B-1200: an AIS address on the internet was saved without a word (refused only when connecting); it is refused
    at save with the connection's own check. A name this computer can't look up now is saved (checked when used)."""
    import pytest

    from ap_coder.core.store import Store
    from ap_coder.erp import jde_ais

    monkeypatch.delenv("AP_ALLOW_INTERNET", raising=False)
    monkeypatch.setattr(jde_ais, "env_path", lambda: tmp_path / ".env")
    store = Store(tmp_path / "a.db")
    looked_up = {"ais.example.com": ["93.184.216.34"], "ais.company.local": ["10.1.2.3"], "ais.vpn.local": []}
    monkeypatch.setattr(jde_ais, "_resolve", lambda host: looked_up.get(host, [host]))  # an IP: itself
    with pytest.raises(ValueError, match="on the internet"):
        jde_ais.save(store, base_url="https://ais.example.com:9300", environment="JPD920", role="", username="JDE")
    with pytest.raises(ValueError, match="on the internet"):
        jde_ais.save(store, base_url="https://8.8.8.8:9300", environment="JPD920", role="", username="JDE")
    assert jde_ais.load(store).base_url == ""  # refused: nothing saved
    for host in ("ais.company.local", "ais.vpn.local"):
        jde_ais.save(store, base_url=f"https://{host}:9300", environment="JPD920", role="", username="JDE")
        assert jde_ais.load(store).host == host


# --- S5. The real reason an exported or posted invoice isn't reopened or deleted --------------------------------


def test_deleting_or_reopening_an_exported_or_posted_invoice_says_why_not(tmp_path, ais):
    """B-1201: deleting (or reopening) an exported invoice, or one posted to JD Edwards, said "someone else changed this
    invoice meanwhile": it says it was exported in its batch, or posted to JD Edwards."""
    from .test_jde_post import batch_of
    from .webclient import client_for, follow, post

    store, batch = batch_of(tmp_path, ais, ON, QC)
    client = client_for(store)
    said = follow(client, post(client, "/review/2/delete", {"back": "/review"}))
    assert "someone else changed" not in said and f"it was exported in batch {batch}" in said
    jde_post.dry_run(store, batch)
    jde_post.post(store, batch)
    assert statuses(store, batch)[1] == jde_post.POSTED
    said = follow(client, post(client, "/review/1/delete", {"back": "/review"}))
    assert "someone else changed" not in said and "it was posted to JD Edwards" in said
    said = follow(client, post(client, "/review/1/reopen"))  # a Reopen button drawn before it was approved
    assert "someone else changed" not in said and "it was posted to JD Edwards" in said
    said = follow(client, post(client, "/review/1/correct", {"reason": "wrong GL"}))  # the review screen's
    assert "changed meanwhile" not in said and "it was posted to JD Edwards" in said


# --- S6. The undo refusal, word for word per status --------------------------------------------------------------


def test_the_undo_refusal_says_what_each_voucher_is(tmp_path, ais):
    """B-1202: the undo refusal said every voucher was "in JD Edwards … void the voucher", one whose answer was lost
    or that JD Edwards refused too: it says posted (void it there), answer not received (being checked), or may be
    in JD Edwards (check there)."""
    store, batch = lost_answer(tmp_path, ais, ON, QC)
    said = jde_post.undo_problem(store, batch)
    assert "void" not in said and "answer not received" in said, said
    jde_post.retry_queue(store)
    assert set(statuses(store, batch).values()) == {jde_post.POSTED}
    said = jde_post.undo_problem(store, batch)
    assert "2 vouchers posted to JD Edwards" in said and "void" in said, said
    with store._conn() as conn:
        conn.execute("UPDATE jde_postings SET status = ?, maybe_sent = 1 WHERE invoice_id = 1", (jde_post.FAILED,))
    said = jde_post.undo_problem(store, batch)
    assert "1 voucher posted to JD Edwards" in said and "1 voucher may be in JD Edwards" in said, said


# --- S7. The batch README's undo ----------------------------------------------------------------------------------


def test_the_batch_readme_offers_no_undo_when_the_batch_cant_be_undone(tmp_path, ais):
    """B-1203: the batch README said "Undo this batch, fix, and export again" when vouchers of it were posted (or may
    be in JD Edwards) and the undo is refused: it says why it can't be undone instead."""
    from .test_jde_post import batch_of

    store, batch = batch_of(tmp_path, ais, ON, QC)

    def readme() -> str:
        with zipfile.ZipFile(io.BytesIO(exports.jde_file(store, batch)[0])) as zf:
            return zf.read("README.txt").decode("utf-8")

    assert "Undo this batch" in readme()  # nothing sent: it can be
    jde_post.dry_run(store, batch)
    jde_post.post(store, batch)
    text = readme()
    assert "Undo this batch" not in text and "can't be undone" in text and "void the voucher" in text


# --- S8. An Admin releases a voucher checked in JD Edwards -------------------------------------------------------


def test_an_admin_releases_a_voucher_jde_holds_another_invoice_under_after_checking(tmp_path, ais):
    """B-1204: a voucher locked because JD Edwards answered EXISTS for another invoice had no way out: neither undone
    nor exported again. An Admin who checked in JD Edwards releases it with a note (D-107): the row is not sent,
    the batch can be undone, and the event says who and why. Never a voucher posted."""
    import re

    from ap_coder.web import roles

    from .test_jde_post import batch_of
    from .test_jde_post_safety import _plant
    from .webclient import client_for, follow, post

    store, batch = batch_of(tmp_path, ais, ON, QC)
    jde_post.dry_run(store, batch)
    _plant(ais, store, batch, an8="4999", invoice_number="OTHER-1", gross=5.0)
    jde_post.post(store, batch)
    assert statuses(store, batch) == {1: jde_post.FAILED, 2: jde_post.POSTED}
    client = client_for(store)
    page = client.get("/exports", params={"batch": batch}).text
    release = f"/exports/batches/{batch}/jde-api/release"
    forms = re.findall(rf'action="{release}".*?name="invoice_id" value="(\d+)"', page, re.S)
    assert forms == ["1"] and "I've checked in JD Edwards: release this voucher" in page  # not the posted one
    assert not post(client, release, {"invoice_id": "1", "note": ""}).json()["ok"]  # a note is needed
    assert not post(client, release, {"invoice_id": "2", "note": "checked"}).json()["ok"]  # never a posted voucher
    follow(client, post(client, release, {"invoice_id": "1", "note": "77777 is invoice OTHER-1's, ours isn't there"}))
    row = next(p for p in jde_post.postings(store, batch) if p["invoice_id"] == 1)
    assert not row["maybe_sent"] and row["status"] != jde_post.POSTED
    assert 1 not in jde_post.left_out_of_files(store, batch)  # the files hold it again
    (event,) = store.events(actions=["jde_voucher_released"])
    assert event["invoice_id"] == 1 and event["detail"]["note"].startswith("77777") and event["actor"]
    assert roles.RULES[("POST", "/exports/batches/{batch:int}/jde-api/release")] == roles.ADMIN


# --- S9. The hub's invoice links on the server --------------------------------------------------------------------


def test_on_the_server_the_hubs_invoice_links_default_to_the_servers_address(tmp_path, monkeypatch):
    """B-1205: on the company server the finance hub's invoice links defaulted to http://127.0.0.1:8501, a page no one
    else can open: they default to the server's own address (its name staff type, its port, https with a
    certificate). On a laptop, as before."""
    from ap_coder.core.store import Store
    from ap_coder.erp.hub import run
    from ap_coder.ops import service

    monkeypatch.delenv("AP_SERVER_MODE", raising=False)
    data = tmp_path / "APCoderData"
    data.mkdir()
    store = Store(data / "ap_coder.db")
    assert run.HubConfig.load(store).link_base == "http://127.0.0.1:8501"
    service.write_config(data, certfile="c.pem", keyfile="k.pem", allowed_hosts=["apcoder.company.local"], port=8443)
    assert run.HubConfig.load(store).link_base == "https://apcoder.company.local:8443"
    store.set_setting(run.KEYS["link_base"], "https://ap.example.local")
    assert run.HubConfig.load(store).link_base == "https://ap.example.local"  # one set in Settings stays


# --- S12. The finance hub: no address, settings events out, the old format's files cleared, timestamps in ms ------


def test_the_hubs_activity_has_no_address_and_no_tax_or_policy_events(tmp_path):
    """B-1206: the hub's ``activity`` sent the staff PC's address (``detail_json`` "address"), and the
    tax_setup_changed and policy_changed events, settings events like those already left out."""
    from ap_coder.core.store import Store
    from ap_coder.erp.hub import tables

    store = Store(tmp_path / "a.db")
    store.log_event("note", 1, "Jane", {"text": "kept", "address": "10.0.0.5"})
    store.log_event("tax_setup_changed", actor="Jane", detail={"tax_type": "GST"})
    store.log_event("policy_changed", actor="Jane", detail={})
    rows = tables.build(store, "activity", tables.Context())
    assert [r["action"] for r in rows] == ["note"] and "10.0.0.5" not in rows[0]["detail_json"]
    assert "kept" in rows[0]["detail_json"]


def test_a_new_hub_format_clears_the_old_formats_files_and_parquet_times_are_milliseconds(tmp_path):
    """B-1207: switching the hub between Parquet and CSV left the old format's files in the table folders beside the
    full rebuild in the new one (read twice, or not at all); they are cleared first. (Parquet's timestamps are
    milliseconds, as FINANCE_HUB.md says.)"""
    import json as _json

    import pyarrow.parquet as pq

    from ap_coder.core.store import Store
    from ap_coder.erp.hub.landing import CSV, PARQUET
    from ap_coder.erp.hub.run import HubConfig, export

    from .webclient import gt

    store = Store(tmp_path / "data" / "a.db")
    invoice = store.add_invoice(tmp_path / "a.pdf", gt(), {"requires_review": False})
    store.approve_invoice(invoice, gt(), "Jane")
    share = tmp_path / "share"
    assert export(store, HubConfig(enabled=True, folder=str(share), format=PARQUET)).ok
    stamp = pq.read_table(next((share / "invoices").glob("*.parquet"))).schema.field("received_at").type
    assert str(stamp) == "timestamp[ms]"
    assert export(store, HubConfig(enabled=True, folder=str(share), format=CSV)).ok
    for table in ("invoices", "activity"):
        names = sorted(p.name for p in (share / table).iterdir())
        assert not [n for n in names if n.endswith(".parquet")] and [n for n in names if n.endswith(".csv")], names
        assert _json.loads((share / table / "_metadata.json").read_text())["fileFormat"] == "DelimitedText"


# --- S10. Paused usernames on the Users page ----------------------------------------------------------------------


def test_the_users_page_lists_paused_usernames_and_lifts_their_pause(tmp_path):
    """B-1208: the Users page listed the paused addresses only: a username paused from one address (5 tries that
    didn't match) was nowhere to see or lift. It is listed beside them, with "Lift the pause"."""
    from ap_coder.core.store import Store, load_sample_setup

    from .test_server_security import GOOD, WRONG_PASSWORD, account, app_for, client_of, log_in
    from .webclient import PAGE

    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    account(store, "boss", "admin")
    account(store, "ann")
    app = app_for(store)
    for _ in range(5):
        log_in(client_of(app, "10.6.6.6"), "ann", WRONG_PASSWORD)
    assert "paused" in log_in(client_of(app, "10.6.6.6"), "ann", GOOD).json()["error"]
    boss = client_of(app, "10.0.0.7")
    log_in(boss, "boss")
    page = boss.get("/admin/users").text
    assert "Paused usernames" in page and "ann" in page and "10.6.6.6" in page
    lifted = boss.post("/admin/users/usernames/unlock", data={"username": "ann", "address": "10.6.6.6"},
                       headers=PAGE)  # fmt: skip
    assert lifted.status_code == 200, lifted.text
    assert log_in(client_of(app, "10.6.6.6"), "ann", GOOD).status_code == 200
    assert "Paused usernames" not in boss.get("/admin/users").text
    event = store.events(actions=["address_unlocked"])[0]
    assert (event["detail"]["username"], event["detail"]["unpaused"]) == ("ann", "10.6.6.6")
