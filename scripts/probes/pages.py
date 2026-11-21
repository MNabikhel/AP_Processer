"""Probe ``pages``: the dashboard's pages as a browser on this computer gets them, on two new databases: the demo
(``ops.demo.load_demo``) and the made-up JD Edwards company (``ops.demo.load_company``, its invoices dated before a
fixed day). Every page of the rail, the review screen of each invoice (N at most a database), its words and reader,
and the review screen's vendor and account lists; each answer's status and text, one JSON line each, for
``scripts/same_behaviour.py``. What differs run to run is masked: the work folder, the review screen's own token
(``screen``), the clock's times, and the backups line of the data folder.

    PYTHONPATH=<tree> python scripts/probes/pages.py N WORK_DIR > out.jsonl"""

import datetime as dt
import json
import os
import re
import sys
from dataclasses import replace
from pathlib import Path

ON = dt.date(2026, 11, 2)  # the made-up company's invoices are dated in the days before
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


def main() -> None:
    n, work = int(sys.argv[1]), Path(sys.argv[2]).resolve()
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
