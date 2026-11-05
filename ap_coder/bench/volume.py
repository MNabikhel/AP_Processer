"""``python -m ap_coder.bench volume --data DIR``: one person on big data. Every GET page, report and export of the
dashboard timed on a data folder ``seed`` made (docs/SCALE_GOAL.md, phase 0: "Done means" 2).

* **How**: the app itself in this process (``create_app`` in server mode, as each web worker runs it, without the
  background work), through a TestClient, signed in as an Admin and then as a Clerk by the real login (the password,
  then the authenticator's code from ``users.json``). Each address is asked once to warm it, then ``--repeat`` times
  (the median is its time); one slower than ``--slow`` seconds is not asked again (its warm request is its time).
* **What**: every GET route the app has (listed from it), its path parameters filled with real ids (an invoice in
  the queue, one approved, one waiting for a second approval, an export batch, a GL account table...), and the tabs
  and searches of the main pages. A route that needs what the seed doesn't make (a manager's request, an email in the
  inbox, a backup) or that asks the model server is listed as skipped, with why.
* **The targets**: a page in 300 ms, a report, export or search in 3 s (``TARGETS``).
* **Memory**: each report and export asked once more under ``tracemalloc``, its peak (not when slower than
  ``--memory-limit``).
* **Full scans**: every SQL statement the warm request ran (``sqlite3`` trace callbacks on the store's connections)
  is explained (``EXPLAIN QUERY PLAN``); each ``SCAN`` of a big table (``--big`` rows or more) is listed with the
  routes that caused it.

Writes ``report.json`` and ``report.md`` in ``--out``. It changes little in the data folder: two logins, their
sessions, and what the pages write themselves (a page that checks the queue again writes the checks)."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import platform
import re
import sqlite3
import statistics
import sys
import threading
import time
import tracemalloc
from collections import Counter
from pathlib import Path
from typing import Any

TARGETS = {"page": 0.3, "report": 3.0, "export": 3.0}
# Pages that are reports (the 3 s target): a period's figures over every invoice.
REPORT_PAGES = ("/month-end", "/sales-tax", "/insights", "/insights/business-case", "/spend", "/statements",
                "/activity/controls-report", "/help/report", "/help/report/setup")  # fmt: skip
EXPORT_SUFFIXES = (".csv", ".xlsx", ".zip", ".txt", ".pdf", ".eml")
# Routes not asked: what they need the seed doesn't make, or what they do that is not a page.
SKIP = {
    "/logout": "ends the session",
    "/settings/reading/self-test": "asks the model server (the load bench's fake one)",
    "/process/inbox/{entry_id}/file/{n}": "no email in the inbox",
    "/settings/data/backups/{name}": "no backup made (a backup of a big database fills the disk)",
    "/manager": "a Manager's page (no Manager in the seed)",
    "/manager/{request_id:int}": "no manager request in the seed",
    "/manager/{request_id:int}/email.eml": "no manager request in the seed",
    "/manager/{request_id:int}/page/{number:int}.png": "no manager request in the seed",
    "/manager/{request_id:int}/reply": "no manager request in the seed",
}
BIG_TABLES_MIN = 5000
_BIG_ORDER = ("invoices", "events", "feedback", "erp_invoices", "reader_outcomes", "lesson_approvals",
              "supplier_outcomes", "line_texts", "invoice_capture", "invoice_summary", "summary_spend",
              "summary_codes", "summary_facts", "vendors", "lessons", "page_reads")  # fmt: skip


def add_parser(sub: Any) -> argparse.ArgumentParser:
    """``volume``'s command line, under ``python -m ap_coder.bench``."""
    p = sub.add_parser("volume", help="every page, report and export timed on a seeded data folder (one person)")
    p.add_argument("--data", required=True, help="a data folder made by `python -m ap_coder.bench seed`")
    p.add_argument("--out", default="bench_volume", help="where report.json and report.md go (default bench_volume)")
    p.add_argument("--roles", default="admin,clerk", help="who asks, in turn (default admin,clerk)")
    p.add_argument("--repeat", type=int, default=3, help="timed requests after the warm one (default 3: the median)")
    p.add_argument("--slow", type=float, default=10.0, help="a warm request slower than this is its time (not again)")
    p.add_argument("--only", action="append", default=[], metavar="ROUTE",
                   help="only this route (as the app names it, e.g. /review/{invoice_id:int}) or address; again for "
                        "more")  # fmt: skip
    p.add_argument("--big", type=int, default=BIG_TABLES_MIN, help="a table with this many rows or more is big")
    p.add_argument("--memory-limit", type=float, default=10.0,
                   help="no tracemalloc pass (2-3 times slower) for a report or export slower than this "
                        "(seconds)")  # fmt: skip
    p.set_defaults(run=main)
    return p


def main(a: argparse.Namespace) -> int:
    try:
        report = run(
            Path(a.data),
            Path(a.out),
            roles=tuple(r.strip() for r in a.roles.split(",") if r.strip()),
            repeat=a.repeat,
            slow=a.slow,
            only=tuple(a.only),
            big=a.big,
            memory_limit=a.memory_limit,
        )
    except (FileNotFoundError, ValueError, RuntimeError) as exc:
        print(f"volume: {exc}", file=sys.stderr)
        return 1
    s = report["summary"]
    print(f"{s['asked']} addresses asked as {', '.join(report['config']['roles'])}: {s['over']} over target, "
          f"{s['errors']} errors, {s['scans']} full scans of big tables; slowest {s['slowest']}")  # fmt: skip
    print(f"report: {Path(a.out) / 'report.md'}")
    return 0


# --- The SQL each request runs ----------------------------------------------------------------------------------------


class Tracer:
    """``sqlite3.connect`` wrapped while it is on: every connection opened while ``collecting`` records the statements
    it runs (expanded, values in place)."""

    def __init__(self) -> None:
        self.statements: list[str] = []
        self.connections = 0
        self.collecting = False
        self._lock = threading.Lock()
        self._real = sqlite3.connect

    def _seen(self, sql: str) -> None:
        if self.collecting:
            with self._lock:
                self.statements.append(sql)

    def connect(self, *args: Any, **kwargs: Any) -> sqlite3.Connection:
        conn = self._real(*args, **kwargs)
        with self._lock:
            self.connections += 1
        if self.collecting:
            conn.set_trace_callback(self._seen)
        return conn

    @contextlib.contextmanager
    def on(self) -> Any:
        sqlite3.connect = self.connect  # type: ignore[assignment]
        try:
            yield self
        finally:
            sqlite3.connect = self._real  # type: ignore[assignment]

    def start(self, collect: bool) -> None:
        with self._lock:
            self.statements, self.connections, self.collecting = [], 0, collect

    def stop(self) -> tuple[list[str], int]:
        with self._lock:
            self.collecting = False
            return self.statements, self.connections


_WORDS = {"where", "join", "left", "right", "inner", "outer", "cross", "natural", "on", "using", "group", "order",
          "limit", "union", "select", "set", "values", "as", "with", "and", "or", "not", "full"}  # fmt: skip
_NAMED = re.compile(r"\b(?:from|join|update|into)\s+\"?([a-z_][\w]*)\"?(?:\s+(?:as\s+)?\"?([a-z_]\w*)\"?)?", re.I)
_EXPLAINED = ("select", "with", "insert", "update", "delete", "replace")
_NO_ROWS = re.compile(r"\bLIMIT\s+0\s*$", re.I)
_FIRST_ROW = re.compile(r"\s*SELECT\s+[^\s,]+\s+FROM\s+\w+\s+LIMIT\s+1\s*", re.I)
REPEATED = 20  # a statement one request runs this many times or more (one per row: N+1) is listed with its count


def _aliases(sql: str) -> dict[str, str]:
    """{the name a plan calls a table: the table}, from the statement's FROM and JOIN clauses."""
    found: dict[str, str] = {}
    for table, alias in _NAMED.findall(sql):
        found.setdefault(table.lower(), table.lower())
        if alias and alias.lower() not in _WORDS:
            found.setdefault(alias.lower(), table.lower())
    return found


def shape(sql: str) -> str:
    """A statement with its values taken out (one line per kind of statement, whatever its values)."""
    text = re.sub(r"'(?:[^']|'')*'", "?", sql)
    text = re.sub(r"\b\d+(?:\.\d+)?\b", "?", text)
    return " ".join(text.split())


def scans(conn: sqlite3.Connection, sql: str, big: dict[str, int]) -> list[dict[str, Any]]:
    """The full scans of big tables in ``sql``'s plan: [{table, rows, how}] (``how``: the table itself, or an index
    read whole). A statement that can't be explained (a temporary table gone, a syntax only the app's connection
    knows) has none, nor one that reads no row or stops at the first (``fetch_rows`` naming its columns with ``LIMIT
    0``, ``SELECT 1 FROM t LIMIT 1``)."""
    if not sql.lstrip().lower().startswith(_EXPLAINED) or _NO_ROWS.search(sql) or _FIRST_ROW.fullmatch(sql):
        return []
    try:
        plan = conn.execute("EXPLAIN QUERY PLAN " + sql).fetchall()
    except sqlite3.Error:
        return []
    names = _aliases(sql)
    out = []
    for row in plan:
        detail = str(row[3])
        m = re.match(r"SCAN (\S+)(.*)", detail)
        if not m or "VIRTUAL TABLE" in detail:
            continue
        table = names.get(m.group(1).lower(), m.group(1).lower())
        if table in big:
            how = m.group(2).strip().replace("USING ", "") or "table"
            out.append({"table": table, "rows": big[table], "how": how})
    return out


# --- The addresses ---------------------------------------------------------------------------------------------------


def _ids(store: Any) -> dict[str, Any]:
    """Real ids for the routes' parameters: an invoice in the queue, approved, waiting for a second approval, parked;
    an export batch; a vendor's name and an invoice number to search for."""
    from ap_coder.core.store import APPROVED, PARKED, PENDING, REVIEW

    queue = store.list_invoices(REVIEW)
    approved = store.recently_approved(1)
    pending = store.list_invoices(PENDING)
    parked = store.list_invoices(PARKED)
    batches = store.export_batches()
    if not queue or not approved:
        raise ValueError("the data folder has no invoice in the queue or approved: make it with `bench seed`")
    one = queue[len(queue) // 2]
    word = next((w for w in str(one.get("vendor_name") or "").split() if len(w) >= 4), "Supply")
    return {
        "review": one["id"], "approved": approved[0]["id"], "pending": pending[0]["id"] if pending else None,
        "parked": parked[0]["id"] if parked else None,
        "batch": next((b["id"] for b in batches if not b.get("undone_at")), None),
        "vendor_word": word, "number": str(one.get("invoice_number") or ""), "status": APPROVED,
    }  # fmt: skip


def addresses(app: Any, ids: dict[str, Any]) -> tuple[list[tuple[str, str]], dict[str, str]]:
    """([(route, address)], {route: why skipped}) for every GET route of ``app``."""
    from ap_coder.web import roles
    from ap_coder.web.pages import accounts, learning, review, settings

    variants: dict[str, list[str]] = {
        "/review": ["", *(f"?tab={t}" for t in review.TABS if t != "review"), "?show=attention", "?show=ready",
                    "?sort=amount"],
        "/learning": [f"?tab={t}" for t in learning.TABS],
        "/accounts": [f"?tab={t}" for t in accounts.TABS],
        "/search": ["", f"?q={ids['vendor_word']}", f"?q={ids['number']}", "?page=40"],
        "/vendors": ["", f"?q={ids['vendor_word']}"],
        "/exports": ["", "?fmt=jde"],
        "/purchase-orders": ["", "?status=all"],
        "/activity": ["", f"?q={ids['vendor_word']}"],
        "/settings/{tab}": [f"/settings/{t}" for t in settings.TAB_KEYS],
        "/review/{invoice_id:int}": [f"/review/{ids[k]}" for k in ("review", "approved", "pending", "parked")
                                     if ids[k] is not None],
        "/accounts/{table}.csv": [f"/accounts/{t}.csv" for t in accounts.TABLES],
        "/jobs/{name}": ["/jobs/process"],
    }  # fmt: skip
    fill = {"invoice_id": ids["review"], "number": 1, "batch": ids["batch"]}
    approved_only = ("/approved.pdf",)
    found, skipped = [], {}
    for route in roles.leaf_routes(app.routes):
        if "GET" not in (getattr(route, "methods", None) or ()):
            continue
        path = route.path
        if path in SKIP:
            skipped[path] = SKIP[path]
            continue
        if path in variants:
            listed = variants[path]
            found += [(path, v if v.startswith("/") else path + v) for v in listed]
            continue
        params = re.findall(r"\{(\w+)(?::\w+)?\}", path)
        values = dict(fill)
        if path.endswith(approved_only):
            values["invoice_id"] = ids["approved"]
        missing = [p for p in params if values.get(p) is None]
        if missing:
            skipped[path] = f"no {', '.join(missing)} in the data folder"
            continue
        address = path
        for p in params:
            address = re.sub(r"\{" + p + r"(?::\w+)?\}", str(values[p]), address)
        found.append((path, address))
    return found, skipped


def kind_of(route: str, address: str) -> str:
    """What the address is: "export" (a file), "report" (a period's figures, the quick search) or "page"."""
    path = address.split("?")[0]
    if path.endswith(EXPORT_SUFFIXES) or route.endswith(EXPORT_SUFFIXES):
        return "export"
    if route in REPORT_PAGES or (route == "/search" and "q=" in address):
        return "report"
    return "page"


# --- Signing in and asking -------------------------------------------------------------------------------------------

PAGE = {"X-AP-Coder": "1"}  # what AP Coder's own page sends with every action


def sign_in(client: Any, person: dict[str, Any]) -> None:
    """The real login: the password, then the authenticator's code (the step after when this one was used already,
    by an earlier run within 30 seconds). RuntimeError when it is refused."""
    from ap_coder.core import authenticator

    answer = client.post("/login", data={"username": person["username"], "password": person["password"],
                                         "next": "/review"}, headers=PAGE)  # fmt: skip
    if answer.status_code != 200 or not str(answer.json().get("redirect", "")).startswith("/login/code"):
        raise RuntimeError(f"{person['username']}: the password was refused ({answer.status_code} {answer.text[:200]})")
    for ahead in (0, 30, 60):
        code = authenticator.code(person["totp_secret"], time.time() + ahead)
        answer = client.post("/login/code", data={"code": code, "next": "/review"}, headers=PAGE)
        if answer.status_code == 200 and answer.json().get("ok", True) and "error" not in answer.json():
            return
    raise RuntimeError(f"{person['username']}: the authenticator's code was refused ({answer.text[:200]})")


def _ask(client: Any, address: str) -> tuple[float, int, int]:
    """(seconds, status, bytes) of one GET."""
    start = time.perf_counter()
    answer = client.get(address, headers={"Accept": "text/html"})
    took = time.perf_counter() - start
    return took, answer.status_code, len(answer.content)


def _peak(client: Any, address: str) -> int:
    """The peak of Python's memory while ``address`` is answered (``tracemalloc``), in bytes."""
    tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        client.get(address, headers={"Accept": "text/html"})
        return tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()


def run(
    data: Path,
    out: Path,
    *,
    roles: tuple[str, ...] = ("admin", "clerk"),
    repeat: int = 3,
    slow: float = 10.0,
    only: tuple[str, ...] = (),
    big: int = BIG_TABLES_MIN,
    memory_limit: float = 10.0,
) -> dict[str, Any]:
    """Time every address on ``data`` (see the module's docstring); writes and returns the report."""
    data = data.resolve()
    users_file, db = data / "users.json", data / "ap_coder.db"
    if not users_file.is_file() or not db.is_file():
        raise FileNotFoundError(f"{data} has no ap_coder.db and users.json: make it with `bench seed`")
    people = json.loads(users_file.read_text(encoding="utf-8"))
    chosen = {}
    for role in roles:
        person = next((p for p in people if p["role"] == role), None)
        if person is None:
            raise ValueError(f"no {role} in {users_file}")
        chosen[role] = person
    out.mkdir(parents=True, exist_ok=True)
    old = {k: os.environ.get(k) for k in ("AP_PRIVATE_DIR", "AP_SERVER_MODE")}
    os.environ["AP_PRIVATE_DIR"], os.environ["AP_SERVER_MODE"] = str(data), "1"
    import logging

    logging.disable(logging.WARNING)
    try:
        return _run(data, db, out, chosen, repeat, slow, only, big, memory_limit)
    finally:
        logging.disable(logging.NOTSET)
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _run(data: Path, db: Path, out: Path, chosen: dict[str, dict[str, Any]], repeat: int, slow: float,
         only: tuple[str, ...], big: int, memory_limit: float) -> dict[str, Any]:  # fmt: skip
    from fastapi.testclient import TestClient

    from ap_coder.core.store import Store
    from ap_coder.web import roles as rules
    from ap_coder.web.app import create_app

    store = Store(db)
    with store._conn() as conn:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
        rows = {t: int(conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]) for t in tables}
    big_tables = {t: n for t, n in rows.items() if n >= big and not t.startswith(("invoice_search", "sqlite_"))}
    app = create_app(None, store, start_reader=False, server=True, insecure_http=True, background=False)
    ids = _ids(store)
    listed, skipped = addresses(app, ids)
    if only:
        listed = [(r, a) for r, a in listed if r in only or a in only]
    tracer = Tracer()
    results: list[dict[str, Any]] = []
    plans: dict[str, list[dict[str, Any]]] = {}
    scanned: dict[tuple[str, str, str], dict[str, Any]] = {}
    explain = store._open()
    try:
        with tracer.on():
            for role, person in chosen.items():
                # raise_server_exceptions: a page that fails is timed with its 500, the run goes on
                with TestClient(app, base_url="http://localhost", client=("10.0.0.5", 50000), follow_redirects=False,
                                raise_server_exceptions=False) as client:  # fmt: skip
                    sign_in(client, person)
                    for route, address in listed:
                        if not rules.allowed(person["role"], rules.RULES.get(("GET", route), rules.ADMIN)):
                            continue
                        result = _measure(client, tracer, route, address, role, repeat, slow, memory_limit)
                        statements = result.pop("sql")
                        for sql in dict.fromkeys(statements):
                            key = shape(sql)
                            if key not in plans:
                                plans[key] = scans(explain, sql, big_tables)
                            for s in plans[key]:
                                hit = scanned.setdefault(
                                    (s["table"], s["how"], key), {**s, "sql": key[:400], "routes": []}
                                )
                                if address not in hit["routes"]:
                                    hit["routes"].append(address)
                        result["scans"] = sorted({s["table"] for sql in statements for s in plans[shape(sql)]})
                        counted = Counter(shape(sql) for sql in statements).most_common(5)
                        result["repeated"] = [[sql[:300], n] for sql, n in counted if n >= REPEATED]
                        results.append(result)
                        print(f"  volume: {role} {address} {result['median']:.3f} s ({result['status']})",
                              file=sys.stderr, flush=True)  # fmt: skip
    finally:
        explain.close()
    report = _report(data, rows, big_tables, results, scanned, skipped, chosen, repeat)
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (out / "report.md").write_text(markdown(report), encoding="utf-8")
    return report


def _measure(client: Any, tracer: Tracer, route: str, address: str, role: str, repeat: int, slow: float,
             memory_limit: float) -> dict[str, Any]:  # fmt: skip
    """One address: warmed (its SQL recorded), then timed; its peak memory for a report or export."""
    tracer.start(collect=True)
    warm, status, size = _ask(client, address)
    statements, connections = tracer.stop()
    times = [warm] if warm > slow else []  # slow already: its warm request is its time (a minute at 100,000 invoices)
    for _ in range(0 if warm > slow else max(1, repeat)):
        tracer.start(collect=False)
        took, status, size = _ask(client, address)
        tracer.stop()
        times.append(took)
    kind = kind_of(route, address)
    median = statistics.median(times)
    peak = None
    if kind != "page" and median <= memory_limit and status < 400:
        peak = _peak(client, address)
    return {
        "role": role, "route": route, "address": address, "kind": kind, "status": status, "bytes": size,
        "warm": round(warm, 4), "times": [round(t, 4) for t in times], "median": round(median, 4),
        "target": TARGETS[kind], "over": median > TARGETS[kind], "statements": len(statements),
        "connections": connections, "peak_mb": round(peak / 1e6, 1) if peak is not None else None,
        "sql": statements,
    }  # fmt: skip


def _report(data: Path, rows: dict[str, int], big: dict[str, int], results: list[dict[str, Any]],
            scanned: dict[tuple[str, str, str], dict[str, Any]], skipped: dict[str, str],
            chosen: dict[str, dict[str, Any]], repeat: int) -> dict[str, Any]:  # fmt: skip
    seeded = {}
    with contextlib.suppress(OSError, ValueError):
        seeded = json.loads((data / "seed.json").read_text(encoding="utf-8")).get("config", {})
    order = {t: n for n, t in enumerate(_BIG_ORDER)}
    full = sorted(scanned.values(), key=lambda s: (order.get(s["table"], 99), s["table"], s["how"] != "table"))
    slowest = max(results, key=lambda r: r["median"]) if results else None
    return {
        "config": {"data": str(data), "roles": list(chosen), "people": {r: p["username"] for r, p in chosen.items()},
                   "repeat": repeat, "seed": seeded},
        "computer": {"cores": os.cpu_count(), "python": platform.python_version(), "sqlite": sqlite3.sqlite_version,
                     "system": platform.system()},
        "made_at": dt.datetime.now().isoformat(timespec="seconds"),
        "rows": rows,
        "big_tables": big,
        "summary": {
            "asked": len(results), "over": sum(r["over"] for r in results),
            "errors": sum(r["status"] >= 500 for r in results), "scans": len(full),
            "slowest": f"{slowest['address']} {slowest['median']:.2f} s ({slowest['role']})" if slowest else "",
        },
        "results": results,
        "scans": full,
        "skipped": skipped,
    }  # fmt: skip


def markdown(report: dict[str, Any]) -> str:
    """report.md: the addresses slowest first, marked against their targets; the full scans; what was skipped."""
    c, s, rows = report["config"], report["summary"], report["rows"]
    lines = [
        "# Volume benchmark: one person on big data",
        "",
        f"{rows.get('invoices', 0):,} invoices, {rows.get('events', 0):,} events, {rows.get('feedback', 0):,} lines "
        f"of feedback, {rows.get('erp_invoices', 0):,} JD Edwards register rows; asked as "
        f"{', '.join(f'{r} ({u})' for r, u in c['people'].items())}, the median of {c['repeat']} after a warm "
        f"request; {report['computer']['cores']} cores, Python {report['computer']['python']}, SQLite "
        f"{report['computer']['sqlite']}.",
        "",
        f"**{s['asked']} addresses, {s['over']} over target, {s['errors']} errors (5xx), {s['scans']} full scans of "
        f"big tables.** Targets: a page {TARGETS['page'] * 1000:.0f} ms, a report, export or search "
        f"{TARGETS['report']:.0f} s.",
        "",
        "| address | role | kind | status | median s | warm s | KB | SQL | peak MB | target | |",
        "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for r in sorted(report["results"], key=lambda r: -r["median"]):
        lines.append(
            f"| `{r['address']}` | {r['role']} | {r['kind']} | {r['status']} | {r['median']:.3f} | {r['warm']:.3f} | "
            f"{r['bytes'] / 1000:,.0f} | {r['statements']} | {r['peak_mb'] if r['peak_mb'] is not None else ''} | "
            f"{r['target']:g} | {'**over**' if r['over'] else 'ok'} |"
        )
    lines += ["", "## Full scans of big tables", "",
              "Each `SCAN` in the plan of a statement a warm request ran, on a table with "
              f"{min(report['big_tables'].values(), default=0):,} rows or more; `how`: the table itself or an index "
              "read whole.", "", "| table | rows | how | routes | statement |", "|---|---:|---|---|---|"]  # fmt: skip
    for scan in report["scans"]:
        routes = ", ".join(f"`{a}`" for a in scan["routes"][:6]) + (" ..." if len(scan["routes"]) > 6 else "")
        statement = scan["sql"][:220].replace("|", "\\|")
        lines.append(f"| {scan['table']} | {scan['rows']:,} | {scan['how']} | {routes} | `{statement}` |")
    repeated = [r for r in report["results"] if r.get("repeated") and r["role"] == next(iter(c["people"]), "")]
    if repeated:
        lines += ["", "## Statements one request runs many times", "",
                  f"The statements a warm request ran {REPEATED} times or more (one per row: an N+1), as "
                  f"{next(iter(c['people']))}.", "", "| address | times | statement |", "|---|---:|---|"]  # fmt: skip
        for r in sorted(repeated, key=lambda r: -r["statements"]):
            for sql, n in r["repeated"]:
                lines.append(f"| `{r['address']}` | {n:,} | `{sql[:200].replace('|', chr(92) + '|')}` |")
    if report["skipped"]:
        lines += ["", "## Not asked", ""] + [f"- `{route}`: {why}" for route, why in report["skipped"].items()]
    return "\n".join(lines) + "\n"
