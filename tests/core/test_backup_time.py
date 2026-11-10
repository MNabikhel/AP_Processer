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
    backup.check()
    clock.now = now + dt.timedelta(minutes=1)
    backup.check()
    assert backup.thread is None and len(store.list_backups()) == 1  # not yet
    clock.now = soon + dt.timedelta(seconds=20)
    backup.check()
    backup.thread.join(30)
    assert len(store.list_backups()) == 2
    assert store.last_timed_backup()["why"] == f"made at its time, {soon.hour:02d}:{soon.minute:02d}"
    first = backup.thread
    clock.now = soon + dt.timedelta(minutes=5)
    backup.check()
    assert backup.thread is first  # the day's is made: nothing to look at before the hour is up
    # The time is moved to a quarter of an hour from now: followed, with no restart.
    later = clock.now + dt.timedelta(minutes=15)
    store.set_setting(BACKUP_AT, f"{later.hour:02d}:{later.minute:02d}")
    clock.now += dt.timedelta(minutes=2)
    backup.check()
    assert backup.thread is first
    clock.now = later + dt.timedelta(seconds=5)
    backup.check()
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
    backup.check()  # the hour is up: looked at, and the day's is there
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
