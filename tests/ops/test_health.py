"""The company server's health (D-187, ``ops/health.py``): the certificate's end read from its PEM file, the business
time zone against this computer's clock, the second backup folder's disk, the background process's heartbeat, what
/readyz says and when it is not ready, and the doctor's lines (and ``doctor --json``)."""

from __future__ import annotations

import base64
import datetime as dt
import json
import shutil
import sqlite3
import ssl
import subprocess
import time
import zoneinfo
from pathlib import Path

import pytest

from ap_coder.core.store import Store
from ap_coder.ops import health, offline, system_log
from tests.webclient import web_settings

FIXTURES = Path(__file__).parents[1] / "fixtures"
LONG_LIVED = FIXTURES / "TEST_ONLY_self_signed_localhost.cert.pem"  # ends 2126-09-30 21:10:42 UTC (GeneralizedTime)
UTC = dt.UTC


def _der(tag: int, *parts: bytes) -> bytes:
    body = b"".join(parts)
    size = len(body)
    length = bytes([size]) if size < 0x80 else bytes([0x80 | 2]) + size.to_bytes(2, "big")
    return bytes([tag]) + length + body


def _certificate(not_after: bytes, *, time_tag: int = health.UTC_TIME, version: bool = True) -> bytes:
    """A certificate's DER as far as its validity (the rest is never read): RFC 5280's order."""
    validity = _der(0x30, _der(health.UTC_TIME, b"260101000000Z"), _der(time_tag, not_after))
    tbs = _der(0x30, *([_der(0xA0, _der(0x02, b"\x02"))] if version else []), _der(0x02, b"\x01\x23"),
               _der(0x30, _der(0x06, b"\x2a\x86\x48")), _der(0x30), validity, _der(0x30))  # fmt: skip
    return _der(0x30, tbs, _der(0x30), _der(0x03, b"\x00"))


def _pem(*ders: bytes) -> bytes:
    blocks = []
    for der in ders:
        text = base64.encodebytes(der).decode("ascii")
        blocks.append(f"-----BEGIN CERTIFICATE-----\n{text}-----END CERTIFICATE-----\n")
    return "".join(blocks).encode("ascii")


def _openssl_cert(folder: Path, days: int) -> Path:
    """A certificate made by ``openssl`` that ends in ``days`` days (skipped where there is no openssl)."""
    openssl = shutil.which("openssl")
    if not openssl:
        pytest.skip("openssl is not on this computer")
    cert, key = folder / f"cert-{days}.pem", folder / f"key-{days}.pem"
    subprocess.run([openssl, "req", "-x509", "-newkey", "ec", "-pkeyopt", "ec_paramgen_curve:prime256v1", "-nodes",
                    "-keyout", str(key), "-out", str(cert), "-days", str(days), "-subj", "/CN=localhost"],
                   check=True, capture_output=True)  # fmt: skip
    return cert


# --- The certificate's end --------------------------------------------------------------------------------------------


def test_the_end_of_a_fixed_certificate_is_read_from_its_pem():
    ends, which = health.certificate_ends(LONG_LIVED)
    assert ends == dt.datetime(2126, 9, 30, 21, 10, 42, tzinfo=UTC) and which == 0
    decode = getattr(getattr(ssl, "_ssl", None), "_test_decode_cert", None)  # (Python's own, private: a cross-check)
    if decode is not None:
        assert ends == dt.datetime.strptime(decode(str(LONG_LIVED))["notAfter"], "%b %d %H:%M:%S %Y GMT").replace(
            tzinfo=UTC
        )


def test_a_certificate_made_by_openssl_near_its_end_is_a_warning_and_past_it_a_failure(tmp_path):
    cert = _openssl_cert(tmp_path, 10)
    ends, _ = health.certificate_ends(cert)
    assert dt.timedelta(days=9) < ends - dt.datetime.now(UTC) <= dt.timedelta(days=10)
    found = health.certificate_check(cert)
    assert found["status"] == health.WARN and found["days_left"] in (9, 10)
    assert "expires on" in found["problem"] and "docs/SERVER.md" in found["problem"]
    assert health.certificate_check(cert, now=ends - dt.timedelta(days=31))["status"] == health.OK
    assert health.certificate_check(cert, now=ends - dt.timedelta(days=30))["status"] == health.WARN  # at 30 days
    past = health.certificate_check(cert, now=ends + dt.timedelta(seconds=1))
    assert past["status"] == health.FAIL and past["days_left"] == -1 and "expired on" in past["problem"]


def test_both_kinds_of_time_and_a_version_1_certificate_are_read():
    assert health.not_after(_certificate(b"491231235959Z")) == dt.datetime(2049, 12, 31, 23, 59, 59, tzinfo=UTC)
    assert health.not_after(_certificate(b"500101000000Z")) == dt.datetime(1950, 1, 1, tzinfo=UTC)
    long = _certificate(b"20510601120000Z", time_tag=health.GENERALIZED_TIME)
    assert health.not_after(long) == dt.datetime(2051, 6, 1, 12, tzinfo=UTC)
    assert health.not_after(_certificate(b"300101000000Z", version=False)).year == 2030
    for broken in (b"", b"\x30\x05\x30", _certificate(b"30010100000Z"), _certificate(b"2051060112Z", time_tag=0x18),
                   b"\x31\x00", _certificate(b"300101000000Z")[:-12]):  # fmt: skip
        with pytest.raises(ValueError):
            health.not_after(broken)


def test_the_first_certificate_of_a_chain_to_end_is_the_one_said(tmp_path):
    chain = tmp_path / "chain.pem"
    chain.write_bytes(LONG_LIVED.read_bytes() + _pem(_certificate(b"270101000000Z")))
    ends, which = health.certificate_ends(chain)
    assert ends == dt.datetime(2027, 1, 1, tzinfo=UTC) and which == 1
    found = health.certificate_check(chain, now=dt.datetime(2026, 12, 20, tzinfo=UTC))
    assert found["status"] == health.WARN and found["problem"].startswith("A CA certificate")


def test_a_file_with_no_certificate_says_its_end_is_not_known(tmp_path):
    (tmp_path / "key.pem").write_text("-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n")
    assert health.certificate_check(tmp_path / "key.pem")["status"] == health.WARN
    assert "can't be read" in health.certificate_check(tmp_path / "none.pem")["problem"]


def test_the_admins_banner_says_a_certificate_near_its_end_and_follows_its_file(tmp_path):
    cert = tmp_path / "cert.pem"
    cert.write_bytes(LONG_LIVED.read_bytes())
    assert health.certificate_notice(str(cert)) is None and health.certificate_notice(None) is None
    soon = (dt.datetime.now(UTC) + dt.timedelta(days=5)).strftime("%y%m%d%H%M%SZ").encode()
    cert.write_bytes(_pem(_certificate(soon)))
    notice = health.certificate_notice(str(cert))  # (a new file: read again at once)
    assert notice["tone"] == "warn" and "expires on" in notice["text"]
    cert.write_bytes(_pem(_certificate(b"200101000000Z")))
    assert health.certificate_notice(str(cert))["tone"] == "err"


def test_the_certificate_is_told_to_the_system_log_on_the_server_only(monkeypatch, tmp_path, no_system_log):
    soon = tmp_path / "soon.pem"
    soon.write_bytes(_pem(_certificate((dt.datetime.now(UTC) + dt.timedelta(days=3)).strftime("%y%m%d%H%M%SZ")
                                       .encode())))  # fmt: skip
    assert health.watch_certificate(str(soon)) is None  # a laptop: nothing watched
    monkeypatch.setenv(offline.SERVER_ENV, "1")
    assert health.watch_certificate(None) is None
    thread = health.watch_certificate(str(soon), every=3600)
    deadline = time.monotonic() + 5
    while not no_system_log and time.monotonic() < deadline:
        time.sleep(0.01)
    assert thread.daemon and no_system_log == [(system_log.CERTIFICATE, no_system_log[0][1], True)]
    health.tell_certificate(health.certificate_check(str(soon)))  # the same within the hour: told once
    assert len(no_system_log) == 1
    health.tell_certificate({"status": health.FAIL, "problem": "can't be read"})  # (no date: not this event)
    assert len(no_system_log) == 1


# --- The business time zone ---------------------------------------------------------------------------------------


def _server_json(**values) -> Path:
    from ap_coder.core import paths
    from ap_coder.ops import service

    paths.private_dir().mkdir(parents=True, exist_ok=True)
    return service.write_config(paths.private_dir(), **values)


def _clock_in(name: str):
    zone = zoneinfo.ZoneInfo(name)
    return lambda at: at.astimezone(zone).utcoffset()


def test_a_computer_left_on_utc_is_told_apart_from_the_business_time_zone(monkeypatch):
    january = dt.datetime(2027, 1, 20, 15, tzinfo=UTC)
    assert health.zone_check(now=january)["status"] == health.OK  # none set: nothing to compare
    _server_json(time_zone="America/Toronto")
    assert health.business_zone() == zoneinfo.ZoneInfo("America/Toronto")
    monkeypatch.setattr(health, "_machine_offset", lambda at: dt.timedelta(0))
    found = health.zone_check(now=january)
    assert found["status"] == health.WARN and found["business"] == "America/Toronto"
    assert "UTC-05:00 now" in found["problem"] and "today" in found["problem"]
    monkeypatch.setattr(health, "_machine_offset", _clock_in("America/Toronto"))
    assert health.zone_check(now=january)["status"] == health.OK
    monkeypatch.setattr(health, "_machine_offset", _clock_in("America/New_York"))  # the same clock all year
    assert health.zone_check(now=january)["status"] == health.OK


def test_a_zone_that_keeps_the_same_time_now_but_not_all_year_is_a_mismatch(monkeypatch):
    _server_json(time_zone="America/Regina")  # no daylight saving: UTC-6 all year
    monkeypatch.setattr(health, "_machine_offset", _clock_in("America/Winnipeg"))  # UTC-6 in winter, -5 in summer
    assert health.zone_check(now=dt.datetime(2027, 1, 20, tzinfo=UTC))["status"] == health.WARN


def test_a_time_zone_name_that_is_not_one_is_said(monkeypatch):
    for name in ("Eastern Standard Time", "America/Nowhere", "../../etc/passwd"):
        _server_json(time_zone=name)
        assert health.business_zone() is None
        found = health.zone_check()
        assert found["status"] == health.WARN and "IANA name" in found["problem"], name


def test_the_daily_backup_hours_are_the_business_time_zones(tmp_path):
    """AP_BACKUP_HOURS=1-5 on a server left on UTC started the 7-9 GB copy at 1 in the morning UTC: 8 in the evening
    in Toronto, people still at work. With server.json's time_zone the hours are the business's."""
    from ap_coder.core.store import backups

    def local(hour_utc: int) -> dt.datetime:  # that instant on this computer's own clock, as auto_backup is given it
        return dt.datetime(2026, 11, 10, hour_utc, tzinfo=UTC).astimezone().replace(tzinfo=None)

    assert backups._business_hour(dt.datetime(2026, 11, 10, 7, tzinfo=UTC)) == 7  # none set: the computer's clock
    _server_json(time_zone="America/Toronto")
    assert backups._business_hour(dt.datetime(2026, 11, 10, 7, tzinfo=UTC)) == 2
    store = Store(tmp_path / "ap.db")
    made = store.auto_backup()
    stamp = (local(12) - dt.timedelta(hours=25)).timestamp()
    import os

    os.utime(made, (stamp, stamp))
    assert store.auto_backup(window=(1, 5), now=local(12)) is None  # 7 in the morning in Toronto: it waits
    stamp = (local(7) - dt.timedelta(hours=25)).timestamp()
    os.utime(made, (stamp, stamp))
    assert store.auto_backup(window=(1, 5), now=local(7)) is not None  # 2 in the morning in Toronto


# --- The second backup folder, the heartbeat, the queues -------------------------------------------------------------


def test_a_second_backup_folder_on_the_databases_disk_is_a_warning(tmp_path, monkeypatch):
    store = Store(tmp_path / "data" / "ap_coder.db")
    found = health.second_folder(store)
    assert found["status"] == health.WARN and "no second backup folder" in found["problem"]
    share = tmp_path / "share"
    share.mkdir()
    store.set_setting("backup_copy_dir", str(share))
    found = health.second_folder(store)
    assert found["status"] == health.WARN and found["same_disk_as_data"] and found["problem"] == health.SAME_DISK
    health.forget()
    monkeypatch.setattr(health, "_device", lambda path: 7 if Path(path) == share else 3)  # a share on another server
    found = health.second_folder(store)
    assert found["status"] == health.OK and found["same_disk_as_data"] is False and found["free_bytes"] > 0
    store.set_setting("backup_copy_dir", str(tmp_path / "gone"))
    assert health.second_folder(store)["problem"] == "The second backup folder can't be reached from the server."


def test_a_second_backup_folder_that_hangs_holds_nothing_up(tmp_path, monkeypatch):
    import threading

    release = threading.Event()
    monkeypatch.setattr(health, "_look", lambda folder, data: release.wait(5) and {"reachable": True})
    store = Store(tmp_path / "ap.db")
    store.set_setting("backup_copy_dir", str(tmp_path))
    started = time.monotonic()
    assert health.second_folder(store, wait=0.2)["reachable"] is None
    assert health.second_folder(store, wait=0.2)["status"] == health.WARN  # still one look under way, not two
    assert time.monotonic() - started < 2
    release.set()


def test_the_heartbeat_says_how_long_ago_a_process_beat(tmp_path):
    store = Store(tmp_path / "ap.db")
    assert store.heartbeat_age(health.HEARTBEAT) is None
    store.beat(health.HEARTBEAT)
    assert 0 <= store.heartbeat_age(health.HEARTBEAT) < 5
    with store._conn() as conn:
        conn.execute("UPDATE ephemeral SET created_at = created_at - 600 WHERE kind = 'heartbeat'")
    assert 595 < store.heartbeat_age(health.HEARTBEAT) < 605
    assert health.background(store, processes=4)["status"] == health.FAIL
    store.beat(health.HEARTBEAT)
    assert health.background(store, processes=4)["status"] == health.OK
    assert health.background(store, processes=1) == {"in_this_process": True, "status": health.OK}


def test_the_servers_background_process_beats_as_it_starts(tmp_path, monkeypatch):
    from ap_coder.web import app, server

    monkeypatch.setattr(server, "until_input_closes", lambda on_close, poll=0.5: time.sleep(0.5) or on_close())
    monkeypatch.setattr(app, "start_background_work", lambda settings, store: ())  # (its work is tested on its own)
    monkeypatch.setattr(server, "background_log", lambda folder, name="": None)  # (no handler left on the logger)
    store = Store(tmp_path / "ap.db")
    assert server.run_background(store) == 0
    assert store.heartbeat_age(health.HEARTBEAT) < 30


def test_the_queues_depths_and_oldest_waits(tmp_path, monkeypatch):
    store = Store(tmp_path / "ap.db")
    assert health.imports(store) == {"waiting": 0, "reading": 0, "oldest_wait_seconds": None}
    assert health.page_reader(store) == {"waiting": 0, "reading": 0, "oldest_wait_seconds": None}
    old = (dt.datetime.now() - dt.timedelta(minutes=10)).isoformat(timespec="seconds")
    with store._conn() as conn:
        conn.execute("INSERT INTO import_batches (source, created_at) VALUES ('upload', ?)", (old,))
        for n, status in enumerate(("waiting", "waiting", "reading", "done")):
            conn.execute("INSERT INTO import_files (batch, path, name, sha256, status, created_at) VALUES (1, ?, ?, ?, "
                         "?, ?)", (f"a{n}.pdf", f"a{n}.pdf", f"{n}" * 64, status, old))  # fmt: skip
        for n, status in enumerate(("waiting", "reading", "done")):
            conn.execute("INSERT INTO page_reads (invoice_id, status, created_at, updated_at) VALUES (?, ?, ?, ?)",
                         (n + 1, status, old, old))  # fmt: skip
    found = health.imports(store)
    assert found["waiting"] == 2 and found["reading"] == 1 and 590 <= found["oldest_wait_seconds"] <= 660
    found = health.page_reader(store)
    assert found["waiting"] == 1 and found["reading"] == 1 and 590 <= found["oldest_wait_seconds"] <= 660


def test_the_queues_are_counted_through_their_indexes(tmp_path):
    store = Store(tmp_path / "ap.db")
    with store._conn() as conn:
        for sql in ("SELECT status, COUNT(*) FROM import_files WHERE status IN ('waiting', 'reading') GROUP BY status",
                    "SELECT MIN(id) FROM import_files WHERE status = 'waiting'",
                    "SELECT status, COUNT(*) FROM page_reads WHERE status IN ('waiting', 'reading') GROUP BY status",
                    "SELECT MIN(created_at) FROM page_reads WHERE status = 'waiting'"):  # fmt: skip
            plan = " ".join(row[-1] for row in conn.execute(f"EXPLAIN QUERY PLAN {sql}"))
            assert "INDEX" in plan and "SCAN" not in plan, (sql, plan)


# --- What /readyz says ----------------------------------------------------------------------------------------------


def test_readiness_on_a_laptop_says_each_check_without_the_servers_own(tmp_path):
    store = Store(tmp_path / "ap.db")
    store.backup_now()
    found = health.readiness(store, web_settings(), server=False)
    assert found["ready"] is True and found["status"] == health.OK and found["problems"] == []
    assert {"database", "data_disk", "backups_disk", "backup", "background", "capture", "imports", "page_reader",
            "model_servers"} <= set(found)  # fmt: skip
    assert not {"certificate", "second_backup_folder", "time_zone"} & set(found)
    assert found["backup"]["age_hours"] < 1 and found["data_disk"]["reserve_bytes"] == 0  # (the tests keep none)


def _ready_server(tmp_path, monkeypatch) -> Store:
    """A server's data folder where everything is fine: a backup, a second folder elsewhere, the background process and
    its two capture processes alive."""
    monkeypatch.setenv(offline.SERVER_ENV, "1")
    monkeypatch.setenv("AP_CAPTURE_PROCESSES", "2")
    store = Store(tmp_path / "data" / "ap_coder.db")
    share = tmp_path / "share"
    share.mkdir()
    _server_json(allowed_folders=[str(share)])
    store.set_setting("backup_copy_dir", str(share))
    monkeypatch.setattr(health, "_device", lambda path: 7 if Path(path) == share else 3)
    store.backup_now()
    assert store.get_setting("backup_copy_status").startswith("ok")
    store.beat(health.HEARTBEAT)
    for n in (1, 2):
        store.take_lease(f"capture:{n}", str(n), 30)
    return store


def test_readiness_on_the_server_with_every_check_fine(tmp_path, monkeypatch, no_system_log):
    store = _ready_server(tmp_path, monkeypatch)
    found = health.readiness(store, web_settings(), server=True, processes=4, certfile=str(LONG_LIVED))
    assert found["ready"] is True and found["problems"] == [], found["problems"]
    assert found["certificate"]["days_left"] > 36000 and found["capture"] == {"alive": 2, "started": 2, "status": "ok"}
    assert found["second_backup_folder"]["same_disk_as_data"] is False and found["time_zone"]["status"] == health.OK
    assert found["background"]["heartbeat_age_seconds"] < 5 and no_system_log == []
    text = json.dumps(found)
    assert str(tmp_path) not in text and "share" not in text  # no folder, file or name is said


@pytest.mark.parametrize("case", ["heartbeat stale", "no capture process", "full disk", "certificate expired",
                                  "database unreadable"])  # fmt: skip
def test_readiness_fails_when_the_server_cannot_do_its_work(tmp_path, monkeypatch, no_system_log, case):
    from ap_coder.core.store import backups

    store = _ready_server(tmp_path, monkeypatch)
    certfile = str(LONG_LIVED)
    if case == "heartbeat stale":
        with store._conn() as conn:
            conn.execute("UPDATE ephemeral SET created_at = created_at - ? WHERE kind = 'heartbeat'",
                         (health.STALE_SECONDS + 5,))  # fmt: skip
    elif case == "no capture process":
        for n in (1, 2):
            store.drop_lease(f"capture:{n}", None)
    elif case == "full disk":
        monkeypatch.setattr(backups, "BACKUP_RESERVE", 10**18)
    elif case == "certificate expired":
        (tmp_path / "old.pem").write_bytes(_pem(_certificate(b"200101000000Z")))
        certfile = str(tmp_path / "old.pem")
    else:

        def unreadable(*args, **kwargs):
            raise sqlite3.OperationalError("database disk image is malformed")

        monkeypatch.setattr(store, "get_setting", unreadable)
    found = health.readiness(store, web_settings(), server=True, processes=4, certfile=certfile)
    assert found["ready"] is False and found["status"] == health.FAIL
    failed = {p["check"] for p in found["problems"] if p["status"] == health.FAIL}
    expected = {"heartbeat stale": {"background"}, "no capture process": {"capture"}, "full disk": {"data_disk"},
                "certificate expired": {"certificate"},
                "database unreadable": {"database", "backup"}}[case]  # fmt: skip
    assert expected <= failed, found["problems"]
    kinds = {"heartbeat stale": system_log.PROCESS_STOPPED, "no capture process": system_log.PROCESS_STOPPED,
             "full disk": system_log.DISK_FULL, "certificate expired": system_log.CERTIFICATE}  # fmt: skip
    assert [kind for kind, _, _ in no_system_log] == ([kinds[case]] if case in kinds else [])


def test_a_backup_failed_old_or_a_model_server_on_its_backup_is_a_warning_not_a_failure(tmp_path, monkeypatch):
    from dataclasses import replace

    from ap_coder.ai import local_llm
    from ap_coder.core.store.backups import BACKUP_STATUS

    store = _ready_server(tmp_path, monkeypatch)
    store.set_setting(BACKUP_STATUS, "failed 2026-11-06T01:00: [Errno 13] Permission denied: 'Northwind Ltd 22.pdf'")
    settings = web_settings()
    settings = replace(settings, llm=replace(settings.llm, provider="local", base_url="http://10.0.0.8:1234/v1",
                                             backup_url="http://10.0.0.9:1234/v1"))  # fmt: skip
    breaker = local_llm.chat_failover(settings.llm)
    for _ in range(local_llm.FAILOVER_FAILURES):
        breaker.failed(breaker.primary, "refused")
    breaker.forgotten = True  # (its probe of the primary stops)
    try:
        found = health.readiness(store, settings, server=True, processes=4, certfile=str(LONG_LIVED))
    finally:
        local_llm.forget_failovers()
    assert found["ready"] is True and found["status"] == health.WARN
    warned = {p["check"]: p["text"] for p in found["problems"]}
    assert set(warned) == {"backup", "model_servers"}
    assert "2026-11-06 01:00" in warned["backup"] and "Northwind" not in json.dumps(found)  # never the reason
    assert found["model_servers"]["model_server"]["on"] == "backup" and "10.0.0" not in json.dumps(found)


def test_an_answer_is_worked_out_once_every_few_seconds(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(health.time, "monotonic", lambda: clock[0])
    kept, made = health.Kept(5.0), []
    assert kept.get(lambda: made.append(1) or len(made)) == 1
    clock[0] += 4.9
    assert kept.get(lambda: made.append(1) or len(made)) == 1
    clock[0] += 0.2
    assert kept.get(lambda: made.append(1) or len(made)) == 2


def test_who_may_ask_the_server(tmp_path):
    allowed = health.monitor_allowed
    assert allowed("127.0.0.1", {}, proxy=None) and allowed("::1", {}, proxy=None)
    assert allowed("::ffff:127.0.0.1", {}, proxy=None)
    assert not allowed("127.0.0.1", {}, proxy="127.0.0.1")  # behind a proxy on the server: everyone is 127.0.0.1
    assert not allowed("10.0.0.5", {}, proxy=None) and not allowed("testclient", {}, proxy=None)
    token = "k" * health.MIN_TOKEN
    _server_json(monitor_token=token, monitor_addresses=["10.1.2.0/24", "10.9.9.9", "not an address"])
    assert allowed("10.0.0.5", {health.TOKEN_HEADER: token}, proxy=None)
    assert allowed("10.0.0.5", {"authorization": f"Bearer {token}"}, proxy="127.0.0.1")
    assert not allowed("10.0.0.5", {health.TOKEN_HEADER: token[:-1] + "x"}, proxy=None)
    assert allowed("10.1.2.77", {}, proxy=None) and allowed("10.9.9.9", {}, proxy=None)
    assert not allowed("10.1.3.1", {}, proxy=None) and not allowed("fe80::1", {}, proxy=None)
    _server_json(monitor_token="short", monitor_addresses="10.1.2.0/24, 10.3.0.1")  # (a short token is not taken)
    assert not allowed("10.0.0.5", {health.TOKEN_HEADER: "short"}, proxy=None) and allowed("10.3.0.1", {}, proxy=None)


def test_the_monitors_settings_are_read_again_only_once_server_json_changes(monkeypatch):
    """A monitor's /readyz opened server.json at each request: on Windows, a file open in another process can't be
    replaced, so server-update's record could fail while a monitor asked (B-1962). It is read again only once the
    file changes."""
    from ap_coder.ops import service

    first, second = "k" * health.MIN_TOKEN, "m" * health.MIN_TOKEN
    _server_json(monitor_token=first)
    reads = []
    real = service.read_config
    monkeypatch.setattr(service, "read_config", lambda data: reads.append(data) or real(data))
    assert [health.monitor_config()[0] for _ in range(3)] == [first] * 3 and len(reads) == 1
    _server_json(monitor_token=second, monitor_addresses=["10.1.2.0/24"])
    reads.clear()  # (the write's own)
    assert [health.monitor_config()[0] for _ in range(2)] == [second] * 2 and len(reads) == 1


# --- The doctor on the server, and as JSON ----------------------------------------------------------------------


def _doctor(tmp_path, *more: str) -> list[str]:
    return ["--env-file", str(tmp_path / "none.env"), "--db", str(tmp_path / "data" / "ap_coder.db"), "doctor", *more]


def test_doctor_json_has_the_same_checks_and_exit_code_as_its_text(tmp_path, monkeypatch, capsys):
    from ap_coder.ops import cli

    (tmp_path / "data").mkdir()
    Store(tmp_path / "data" / "ap_coder.db")
    code = cli.main(_doctor(tmp_path))
    text = capsys.readouterr().out
    assert cli.main(_doctor(tmp_path, "--json")) == code
    found = json.loads(capsys.readouterr().out)
    assert found["app"] == "ap-coder" and found["reference_data_source"]
    lines = [line for line in text.splitlines() if line.startswith("[")]
    assert [f"[{c['status']}] {c['area']}" for c in found["checks"]] == [line.split("  ")[0] for line in lines]
    assert all(set(c) == {"area", "status", "detail"} for c in found["checks"])
    assert found["failures"] == sum(c["status"] == "FAIL" for c in found["checks"])
    assert not any(c["area"] in ("server time zone", "second backup folder") for c in found["checks"])  # a laptop


def test_doctor_on_the_server_says_the_certificate_the_zone_the_disk_and_the_backups(tmp_path, monkeypatch, capsys):
    from ap_coder.ops import cli

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("AP_PRIVATE_DIR", str(data))
    monkeypatch.setenv(offline.SERVER_ENV, "1")
    soon = data / "soon.pem"
    soon.write_bytes(_pem(_certificate((dt.datetime.now(UTC) + dt.timedelta(days=12)).strftime("%y%m%d%H%M%SZ")
                                       .encode())))  # fmt: skip
    (data / "key.pem").write_text("a key")
    _server_json(certfile=str(soon), keyfile=str(data / "key.pem"), time_zone="Mars/Olympus")
    store = Store(data / "ap_coder.db")
    (data / "copies").mkdir()
    store.set_setting("backup_copy_dir", str(data / "copies"))  # (a folder of the data folder: its disk)
    assert cli.main(_doctor(tmp_path, "--json")) == 1  # (no account: a failure, as before)
    checks = {c["area"]: c for c in json.loads(capsys.readouterr().out)["checks"]}
    assert checks["server HTTPS certificate expiry"]["status"] == "WARN"
    assert "days" in checks["server HTTPS certificate expiry"]["detail"]
    assert checks["server time zone"]["status"] == "WARN" and "Mars/Olympus" in checks["server time zone"]["detail"]
    assert checks["data folder disk"]["status"] == "PASS" and "free of" in checks["data folder disk"]["detail"]
    assert checks["backups"]["status"] == "WARN" and checks["backups"]["detail"] == "There is no backup yet."
    assert checks["second backup folder"]["status"] == "WARN"
    assert checks["second backup folder"]["detail"] == health.SAME_DISK
