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
