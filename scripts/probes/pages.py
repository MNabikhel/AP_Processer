"""Probe ``pages``: the dashboard's pages as a browser on this computer gets them, on two new databases: the demo
(``ops.demo.load_demo``) and the made-up JD Edwards company (``ops.demo.load_company``, its invoices dated before a
fixed day). Every page of the rail, the review screen of each invoice (N at most a database), its words and reader,
and the review screen's vendor and account lists; each answer's status and text, one JSON line each, for
``scripts/same_behaviour.py``. The app's clock reads a fixed day (``DAY``, noon, then the run's own seconds), so the
dates and day counts a page prints do not depend on the day it runs. What differs run to run is masked: the work
folder, the review screen's own token (``screen``), the clock's times, and the backups line of the data folder.

    PYTHONPATH=<tree> python scripts/probes/pages.py N WORK_DIR > out.jsonl"""

import datetime as dt
import json
import os
import re
import sys
import time
from dataclasses import replace
from pathlib import Path

ON = dt.date(2026, 11, 2)  # the made-up company's invoices are dated in the days before
DAY = ON  # the day the app's clock reads (a page's today, "in the review queue since", a due date's days ago)
MASKS = (
    (re.compile(r'(name="screen" value=")[^"]*'), r"\1(screen)"),
    (re.compile(r"(/page/\d+\.png\?v=)[0-9a-f]+"), r"\1(file)"),  # a page's picture: its file's size and time
    (re.compile(r"(<dt>Backups</dt><dd>).*?(</dd>)", re.S), r"\1(backups)\2"),
    (re.compile(r"\b\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?([+-]\d{2}:\d{2}|Z)?"), "(time)"),
    (re.compile(r"\b\d{1,2}:\d{2}(:\d{2})?(\s?[AaPp]\.?[Mm]\.?)?(?![\d:])"), "(time)"),
)


def masked(text: str, work: Path) -> str:
    for folder in {str(work), str(work).replace("\\", "\\\\"), work.as_posix()}:
        text = text.replace(folder, "(work)")
    for pattern, said in MASKS:
        text = pattern.sub(said, text)
    return text


def answers(client, store, n: int) -> list[tuple[str, str]]:
    """(what is asked, its path): the rail's pages, then each invoice's review screen, words and reader, then the
    lists the review screen fetches."""
    from ap_coder.web.pages import PAGES

    asked = [("page", p.path) for p in PAGES]
    for inv in sorted(store.list_invoices(), key=lambda i: i["id"])[:n]:
        asked += [("review", f"/review/{inv['id']}"), ("words", f"/review/{inv['id']}/words"),
                  ("reader", f"/review/{inv['id']}/reader")]  # fmt: skip
    asked += [("lookup", "/review/vendors.json"), ("lookup", "/review/accounts.json"),
              ("lookup", "/review/accounts.json?q=office"), ("lookup", "/review/accounts.json?q=7420")]  # fmt: skip
    return asked


def fix_the_clock(day: dt.date = DAY) -> None:
    """The app's ``date.today()`` and ``datetime.now()`` read ``day`` at noon, then as many seconds as the run has
    taken, so two runs on either side of midnight print the same dates (B-3016). Every module of the app is imported
    first, then each name it holds for ``datetime`` (the module, its ``date``, its ``datetime``) is the probe's: the
    libraries keep theirs (pandas' compiled code is built on the real classes). A date made elsewhere is still a date
    to the app (``isinstance`` holds both ways)."""
    import importlib
    import pkgutil
    import types

    import ap_coder

    for found in pkgutil.walk_packages(ap_coder.__path__, "ap_coder."):
        if not found.name.endswith("__main__"):
            try:
                importlib.import_module(found.name)
            except Exception:  # noqa: BLE001 - a part that needs what this computer lacks (Azure's, Windows')
                pass
    real_date, real_datetime = dt.date, dt.datetime
    start, began = real_datetime.combine(day, dt.time(12)), time.monotonic()

    class Either(type):
        def __instancecheck__(cls, value):
            return isinstance(value, cls.real)

        def __subclasscheck__(cls, other):
            return issubclass(other, cls.real)

    class Date(real_date, metaclass=Either):
        real = real_date

        @classmethod
        def today(cls):
            moment = DateTime.now()
            return cls(moment.year, moment.month, moment.day)

    class DateTime(real_datetime, metaclass=Either):
        real = real_datetime

        @classmethod
        def now(cls, tz=None):
            moment = start + dt.timedelta(seconds=time.monotonic() - began)
            local = cls(*moment.timetuple()[:6], moment.microsecond)
            return local if tz is None else local.astimezone(tz)

        @classmethod
        def today(cls):
            return cls.now()

    shim = types.ModuleType("datetime")
    shim.__dict__.update(vars(dt))
    shim.date, shim.datetime = Date, DateTime
    for name, module in list(sys.modules.items()):
        if name == "ap_coder" or name.startswith("ap_coder."):
            for attr, value in list(vars(module).items()):
                if value is dt or value is real_date or value is real_datetime:
                    setattr(module, attr, {id(dt): shim, id(real_date): Date, id(real_datetime): DateTime}[id(value)])


def main() -> None:
    n, work = int(sys.argv[1]), Path(sys.argv[2]).resolve()
    fix_the_clock()
    work.mkdir(parents=True, exist_ok=True)
    os.chdir(work)  # a data folder under the working folder is shown by its path from there: the same for both trees
    os.environ["AP_PRIVATE_DIR"] = str(work / "private")
    os.environ["AP_USER_SETTINGS"] = str(work / "user.json")
    os.environ["HOME"] = os.environ["USERPROFILE"] = str(work / "home")
    from fastapi.testclient import TestClient

    from ap_coder.core.config import Settings
    from ap_coder.core.store import Store
    from ap_coder.ops.demo import load_company, load_demo
    from ap_coder.web.app import create_app

    settings = Settings()
    settings = replace(settings, llm=replace(settings.llm, provider="off"))  # no chat model is asked
    for name, load in (("demo", load_demo), ("company", lambda s, c: load_company(s, c, on=ON))):
        store = Store(work / name / "ap_coder.db")
        load(store, settings)
        client = TestClient(create_app(settings, store, start_reader=False), base_url="http://localhost")
        for kind, path in answers(client, store, n):
            got = client.get(path)
            print(json.dumps({"db": name, "kind": kind, "path": path, "status": got.status_code,
                              "type": got.headers.get("content-type", ""),
                              "body": masked(got.text, work)}, sort_keys=True))  # fmt: skip


if __name__ == "__main__":
    main()
