"""The daily backup at a set time of day (Settings → Data & backups, D-218): made when the clock passes it while AP
Coder runs, at the next start when it did not, and why it was made then. The clock is the tests' own: nothing waits."""

import datetime as dt
import os
import zoneinfo

from ap_coder.core.store import Store
from ap_coder.core.store.backups import BACKUP_AT, backup_due, backup_time, next_backup_due
from ap_coder.web.app import DailyBackup
from tests.webclient import PAGE, client_for

DAY = dt.datetime(2026, 11, 10)  # a Tuesday
HALF_PAST_FIVE = (17, 30)


def at(hour: int, minute: int = 0, day: int = 0) -> dt.datetime:
    return DAY + dt.timedelta(days=day, hours=hour, minutes=minute)


def made_at(backup, when: dt.datetime) -> None:
    """A backup just made, as if made at the test's own time."""
    os.utime(backup, (when.timestamp(), when.timestamp()))


class Clock:
    """A clock a test sets: the time of day, and the seconds that passed."""

    def __init__(self, now: dt.datetime) -> None:
        self.now, self.start = now, now

    def wall(self) -> dt.datetime:
        return self.now

    def monotonic(self) -> float:
        return (self.now - self.start).total_seconds()


def test_a_time_of_day_is_read_as_written():
    assert backup_time("17:30") == (17, 30) and backup_time(" 7:05 ") == (7, 5) and backup_time("00:00") == (0, 0)
    assert backup_time("") is None and backup_time("24:00") is None and backup_time("17:60") is None
    assert backup_time("half past five") is None and backup_time("off") is None
    assert backup_due(at(17, 30), HALF_PAST_FIVE) == at(17, 30)
    assert backup_due(at(17, 29), HALF_PAST_FIVE) == at(17, 30, day=-1)
    assert next_backup_due(at(17, 30), HALF_PAST_FIVE) == at(17, 30, day=1)
    assert next_backup_due(at(8), HALF_PAST_FIVE) == at(17, 30)


def test_a_new_install_backs_up_at_half_past_five_and_an_emptied_time_is_no_set_time(tmp_path):
    store = Store(tmp_path / "ap.db")
    assert store.backup_at() == HALF_PAST_FIVE and store.last_timed_backup() == {}
    store.set_setting(BACKUP_AT, "off")
    assert store.backup_at() is None
    store.set_setting(BACKUP_AT, "06:15")
    assert store.backup_at() == (6, 15)


def test_the_days_backup_is_made_at_its_time_once_a_day(tmp_path):
    store = Store(tmp_path / "ap.db")
    first = store.auto_backup(at=HALF_PAST_FIVE, now=at(9), started=True)
    assert first is not None and store.last_timed_backup()["why"] == "made at start: there was no backup yet"
    made_at(first, at(9))
    # Its time has not come since: the morning's start of the next hours makes none, as today's start-up backup.
    assert store.auto_backup(at=HALF_PAST_FIVE, now=at(12)) is None
    assert store.auto_backup(at=HALF_PAST_FIVE, now=at(17, 29)) is None
    second = store.auto_backup(at=HALF_PAST_FIVE, now=at(17, 30))
    assert second is not None and second != first
    last = store.last_timed_backup()
    assert last == {"at": "2026-11-10 17:30", "file": second.name, "why": "made at its time, 17:30"}
    made_at(second, at(17, 30))
    assert store.auto_backup(at=HALF_PAST_FIVE, now=at(17, 45)) is None  # the day's is made
    assert store.auto_backup(at=HALF_PAST_FIVE, now=at(8, day=1), started=True) is None  # nor at the next start
    assert store.auto_backup(at=HALF_PAST_FIVE, now=at(17, 31, day=1)) is not None  # the next day's


def test_a_laptop_closed_or_asleep_at_its_time_backs_up_at_the_next_start_and_says_why(tmp_path):
    store = Store(tmp_path / "ap.db")
    made_at(store.auto_backup(at=HALF_PAST_FIVE, now=at(17, 30, day=-1)), at(17, 30, day=-1))
    # Closed at 17:30 on Tuesday: Wednesday morning's start makes Tuesday's.
    late = store.auto_backup(at=HALF_PAST_FIVE, now=at(8, 2, day=1), started=True)
    assert late is not None
    assert store.last_timed_backup() == {"at": "2026-11-11 08:02", "file": late.name,
                                         "why": "made at start: AP Coder was not running at 17:30"}  # fmt: skip
    made_at(late, at(8, 2, day=1))
    assert store.auto_backup(at=HALF_PAST_FIVE, now=at(12, day=1)) is None
    made_at(store.auto_backup(at=HALF_PAST_FIVE, now=at(17, 30, day=1)), at(17, 30, day=1))  # Wednesday's own
    # Left running but asleep over Thursday's time: made when it wakes, said as late.
    woke = store.auto_backup(at=HALF_PAST_FIVE, now=at(7, 40, day=3))
    assert woke is not None
    assert store.last_timed_backup()["why"] == "made late: this computer was asleep or busy at 17:30"


def test_the_time_is_the_business_time_zones_where_the_server_has_one(tmp_path, monkeypatch):
    from ap_coder.ops import health

    zone = zoneinfo.ZoneInfo("America/Edmonton")
    monkeypatch.setattr(health, "business_zone", lambda data=None: zone)

    def here(hour: int, minute: int, day: int = 10) -> dt.datetime:  # that time there, on this computer's clock
        return dt.datetime(2026, 11, day, hour, minute, tzinfo=zone).astimezone().replace(tzinfo=None)

    store = Store(tmp_path / "ap.db")
    made_at(store.auto_backup(at=HALF_PAST_FIVE, now=here(9, 0)), here(9, 0))
    assert store.auto_backup(at=HALF_PAST_FIVE, now=here(17, 29)) is None
    assert store.auto_backup(at=HALF_PAST_FIVE, now=here(17, 30)) is not None
    assert backup_due(here(17, 45), HALF_PAST_FIVE) == here(17, 30)
    assert next_backup_due(here(17, 45), HALF_PAST_FIVE) == here(17, 30, day=11)


def test_its_hours_set_by_it_decide_and_the_time_is_not_used(tmp_path):
    store = Store(tmp_path / "ap.db")
    made_at(store.auto_backup(window=(1, 5), at=HALF_PAST_FIVE, now=at(2)), at(2))
    assert store.last_timed_backup() == {}
    assert store.auto_backup(window=(1, 5), at=HALF_PAST_FIVE, now=at(17, 30)) is None  # outside its hours
    assert store.auto_backup(window=(1, 5), at=HALF_PAST_FIVE, now=at(2, day=1)) is not None


def test_the_running_dashboard_backs_up_as_the_clock_passes_the_time(tmp_path):
    """While AP Coder runs, each look at the clock (a page asked for, the half-minute watch, the server's background
    process) makes the day's backup once its time has come; a time saved meanwhile is followed within a minute."""
    store = Store(tmp_path / "ap.db")
    now = dt.datetime.now().replace(second=0, microsecond=0)
    soon = now + dt.timedelta(minutes=2)
    store.set_setting(BACKUP_AT, f"{soon.hour:02d}:{soon.minute:02d}")
    made_at(store.backup_now(), now - dt.timedelta(hours=3))  # the day's, made before its start
    clock = Clock(now)
    backup = DailyBackup(store, clock=clock.wall, monotonic=clock.monotonic)
    backup.check(look=True)
    clock.now = now + dt.timedelta(minutes=1)
    backup.check(look=True)
    assert backup.thread is None and len(store.list_backups()) == 1  # not yet
    clock.now = soon + dt.timedelta(seconds=20)
    backup.check(look=True)
    backup.thread.join(30)
    assert len(store.list_backups()) == 2
    assert store.last_timed_backup()["why"] == f"made at its time, {soon.hour:02d}:{soon.minute:02d}"
    first = backup.thread
    clock.now = soon + dt.timedelta(minutes=5)
    backup.check(look=True)
    assert backup.thread is first  # the day's is made: nothing to look at before the hour is up
    # The time is moved to a quarter of an hour from now: followed, with no restart.
    later = clock.now + dt.timedelta(minutes=15)
    store.set_setting(BACKUP_AT, f"{later.hour:02d}:{later.minute:02d}")
    clock.now += dt.timedelta(minutes=2)
    backup.check(look=True)
    assert backup.thread is first
    clock.now = later + dt.timedelta(seconds=5)
    backup.check(look=True)
    assert backup.thread is not first
    backup.thread.join(30)
    assert len(store.list_backups()) == 3


def test_a_dashboard_started_after_its_time_was_missed_says_so(tmp_path):
    store = Store(tmp_path / "ap.db")
    now = dt.datetime.now().replace(second=0, microsecond=0)
    set_time = now - dt.timedelta(hours=2)
    said = f"{set_time.hour:02d}:{set_time.minute:02d}"
    store.set_setting(BACKUP_AT, said)
    made_at(store.backup_now(), now - dt.timedelta(hours=20))
    clock = Clock(now)
    backup = DailyBackup(store, clock=clock.wall, monotonic=clock.monotonic)
    backup.start()
    backup.thread.join(30)
    assert store.last_timed_backup()["why"] == f"made at start: AP Coder was not running at {said}"
    assert len(store.list_backups()) == 2


def test_with_no_set_time_the_backup_is_made_once_a_day_at_start_as_before(tmp_path):
    store = Store(tmp_path / "ap.db")
    store.set_setting(BACKUP_AT, "off")
    clock = Clock(dt.datetime.now())
    backup = DailyBackup(store, clock=clock.wall, monotonic=clock.monotonic)
    backup.start()
    backup.thread.join(30)
    first = backup.thread
    assert len(store.list_backups()) == 1 and store.last_timed_backup() == {}
    clock.now += dt.timedelta(hours=5)
    backup.check(look=True)  # the hour is up: looked at, and the day's is there
    backup.thread.join(30)
    assert backup.thread is not first and len(store.list_backups()) == 1


def test_the_time_is_set_on_settings_and_the_page_says_when_the_last_one_was_made_and_why(store):
    client = client_for(store)
    page = client.get("/settings/data").text
    assert 'action="/settings/data/backup-time"' in page and 'name="at" value="17:30"' in page
    assert "Back up every day at" in page
    refused = client.post("/settings/data/backup-time", data={"at": "25:99"}, headers=PAGE)
    assert refused.status_code == 400 and "17:30" in refused.json()["error"]
    assert client.post("/settings/data/backup-time", data={"at": "12:15"}, headers=PAGE).json()["ok"]
    assert store.backup_at() == (12, 15)
    assert any(e["detail"] == {"keys": ["backup_at"]} for e in store.events(None, ["settings_changed"]))
    store.auto_backup(at=(12, 15), now=at(8, 2), started=True)
    page = client.get("/settings/data").text
    assert 'name="at" value="12:15"' in page
    assert "Last daily backup: 2026-11-10 08:02, made at start: there was no backup yet." in page
    assert client.post("/settings/data/backup-time", data={"at": ""}, headers=PAGE).json()["ok"]
    assert store.backup_at() is None
    assert 'name="at" value=""' in client.get("/settings/data").text


def test_a_page_asked_for_never_reads_the_time_from_the_database(tmp_path):
    """Each request lets the daily backup look at the clock (``app.BackupCheck``): it goes by the time as the
    half-minute watch last read it, and opens no connection of its own."""
    store = Store(tmp_path / "ap.db")
    clock = Clock(dt.datetime.now())
    backup = DailyBackup(store, clock=clock.wall, monotonic=clock.monotonic)
    read = []
    store.backup_at = lambda: read.append(1) or HALF_PAST_FIVE
    backup.check()
    clock.now += dt.timedelta(minutes=5)
    backup.check()
    assert read == [] and backup.thread is None
    backup.check(look=True)
    assert read == [1] and backup.at == HALF_PAST_FIVE


def test_a_time_moved_to_one_already_passed_today_is_followed_within_a_minute_and_says_why(tmp_path):
    """B-2295: at 15:00 the time was moved from 17:30 to 14:00; today's backup came only from the hourly check, up
    to an hour later, and the page said "made late: this computer was asleep or busy at 14:00"."""
    store = Store(tmp_path / "ap.db")
    store.set_setting(BACKUP_AT, "17:30")
    made_at(store.backup_now(), at(17, 30, day=-1))  # yesterday's, at its time
    clock = Clock(at(15))
    backup = DailyBackup(store, clock=clock.wall, monotonic=clock.monotonic)
    backup.check(look=True)
    assert backup.thread is None and backup.next_at == at(17, 30)
    store.save_backup_at((14, 0), actor="Dana", now=at(15, 0))  # as Settings saves it
    assert store.backup_at() == (14, 0)
    clock.now = at(15, 1)
    backup.check(look=True)  # the watch's look a minute later
    assert backup.thread is not None, "the time moved earlier waits for the hourly check"
    backup.thread.join(30)
    assert len(store.list_backups()) == 2 and backup.next_at == at(14, day=1)
    last = store.last_timed_backup()
    assert last["at"] == "2026-11-10 15:01" and last["why"] == "made after the time was changed to 14:00 at 15:00"
    made_at(store.backup_dir() / last["file"], at(15, 1))
    # Nothing more today: the day's backup is made.
    first = backup.thread
    for minutes in (2, 30, 59, 61, 125):
        clock.now = at(15, minutes)
        backup.check(look=True)
        if backup.thread is not first:
            backup.thread.join(30)
    assert len(store.list_backups()) == 2
    # The next day at its time, the reason is the time's own again.
    assert store.auto_backup(at=(14, 0), now=at(14, 0, day=1)) is not None
    assert store.last_timed_backup()["why"] == "made at its time, 14:00"


def test_a_start_just_before_the_time_does_not_wait_a_day_for_it(tmp_path):
    """The watch first reads the time half a minute after the start: a time passed in between is still today's."""
    store = Store(tmp_path / "ap.db")
    store.set_setting(BACKUP_AT, "17:30")
    made_at(store.backup_now(), at(17, 30, day=-1))
    clock = Clock(at(17, 29) + dt.timedelta(seconds=45))
    backup = DailyBackup(store, clock=clock.wall, monotonic=clock.monotonic)
    clock.now = at(17, 30) + dt.timedelta(seconds=15)
    backup.check(look=True)
    assert backup.thread is not None
    backup.thread.join(30)
    assert store.last_timed_backup()["why"] == "made at its time, 17:30" and backup.next_at == at(17, 30, day=1)


def _server_page(store, monkeypatch, hours: str | None) -> str:
    """Settings → Data & backups as an Admin of the company server sees it, with IT's backup hours set or not."""
    from fastapi.testclient import TestClient

    from ap_coder.web.app import create_app
    from ap_coder.web.identity import SESSION_COOKIE
    from tests.webclient import enrol, web_settings

    if hours is None:
        monkeypatch.delenv("AP_BACKUP_HOURS", raising=False)
    else:
        monkeypatch.setenv("AP_BACKUP_HOURS", hours)
    if store.find_user("pat") is None:
        enrol(store, store.add_user("pat", "correct horse battery", role="admin", must_change=False))
    session = store.start_session(store.find_user("pat")["id"])
    app = create_app(web_settings(), store, start_reader=False, server=True)
    client = TestClient(app, base_url="http://localhost", client=("10.0.0.5", 50000), follow_redirects=False)
    answer = client.get("/settings/data", headers={**PAGE, "Cookie": f"{SESSION_COOKIE}={session}"})
    assert answer.status_code == 200, answer.text[:300]
    return " ".join(answer.text.split())


def test_on_the_company_server_the_page_shows_no_time_that_nobody_chose(store, monkeypatch):
    """B-2296: a server showed "17:30" (greyed with IT's hours set, as its own without) though nobody had chosen it."""
    page = _server_page(store, monkeypatch, "1-5")  # IT's hours decide: said, with where they are set
    assert "17:30" not in page and 'action="/settings/data/backup-time"' not in page
    assert "On this server the daily backup is made from 1:00 to 5:00" in page
    assert "IT set those hours (AP_BACKUP_HOURS in the server's .env file" in page
    page = _server_page(store, monkeypatch, None)  # no hours, no time saved: AP Coder's own time applies, and is said
    assert 'name="at" value=""' in page and 'value="17:30"' not in page
    assert "Nobody has chosen a time on this server: until one is saved here, the daily backup is made at 17:30" in page
    assert "AP_BACKUP_HOURS" in page
    store.save_backup_at((22, 0), actor="Pat")
    page = _server_page(store, monkeypatch, None)  # chosen: shown as any saved time
    assert 'name="at" value="22:00"' in page and "Nobody has chosen a time" not in page
    # On a laptop the time is AP Coder's until changed, as before.
    assert 'name="at" value="17:30"' in client_for(Store(store.path.parent / "laptop.db")).get("/settings/data").text
