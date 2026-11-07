"""The company server under load, as many people use it at once: ``python -m ap_coder.bench load``.

    python -m ap_coder.bench load --users 10 --duration 60 --workers 2 [--ramp 10] [--think 10] [--out bench_load]
        [--data DIR [--users-file FILE]] [--seed-invoices 2000] [--model-url URL] [--host ADDRESS] [--keep-alive 5]
        [--baseline FILE [--max-regression 1.5]] [--sample-every 2] [--max-slope MB] [--quick]

What it runs:
- **The server under test**: the real ``python -m ap_coder serve --server --insecure-http --workers W`` (with its
  background process), started on a data folder as a subprocess, with its models answered by a stand-in model server
  (``ap_coder/bench/fake_model.py`` when it is there, else ``scripts/ci/fake_lm_studio.py``; ``--model-url`` names one
  already running). It is stopped at the end through ``/shutdown``, as the service stops it, and the stop is timed.
- **The data**: ``--data DIR`` is used as it is (and changed by the run: approvals, batches), its accounts read from
  ``--users-file`` (default ``DIR/users.json``: a list of {username, password, totp_secret, role}); a folder without a
  database is seeded first and kept. Without ``--data``, a fresh small folder is seeded in ``OUT/data`` (the sample
  invoices, ``--seed-invoices`` of them, each a file of its own, in every state; accounts in every role with their
  authenticator set up) and removed after the run (``--keep-data`` keeps it).
- **The people**: ``--users N`` of them, 70% Clerks, 20% Approvers, 5% Admins and 5% Read-only, started over
  ``--ramp`` seconds. Each is one browser (an httpx client with its own cookies, six connections, each closed after
  ``--keep-alive`` seconds unused, and a cache that keeps the static files as the headers say: ``max-age``/
  ``immutable`` kept, ``no-cache`` revalidated with ``If-None-Match``/``If-Modified-Since``, ``no-store`` never
  kept). Each logs in (the password, then a code worked out
  now by the app's own ``authenticator.code``), then works in weighted scenarios: the queue (tabs, filters, sorts,
  search, "show more"), an invoice opened as a browser opens it (the page, its styles, scripts and fonts, one to three
  page images, the vendor list through the cache, the reader's poll while it is open), 3 to 6 edits each checked,
  then approve (with "approve anyway" when only errors keep it closed), park or reject; second approvals; the
  directory, its CSV and Excel exports (each a job of the background process, followed as the page follows it, then
  the file downloaded); Insights, Spend, Month-end and the other reports; an upload with its job polled; a bulk
  approval (at most 50); an export batch (JD Edwards when it is set up). POSTs carry what the page's fetch sends (the
  ``X-AP-Coder`` header, the Origin, ``Sec-Fetch-Site``) as multipart form data.
- **Think time**: lognormal around ``--think`` seconds between scenarios, a third of it between the steps of one.
  Each person keeps a schedule (scheduled start, against coordinated omission): a step is due a think time after the
  previous one was due, and its time is counted from when it was due, so a slow server's waits are in the figures,
  not hidden by people who click less. A person over a minute behind starts again from now (people don't queue
  clicks); how often that happened is reported.

What it measures: for every route (ids taken out), the count, p50/p95/p99/max (a request without an answer counted at
its time, at least ``--timeout``), the status codes, refusals (4xx), the requests over 30 seconds, errors (5xx, no
answer, cut off: still under way when the run stopped its people); the server's own milliseconds from its access
logs; throughput over time; each server process's CPU and memory over time (``/proc``, or psutil when it is
installed; every ``--sample-every`` seconds), with how fast its memory grew (MB a 10 minutes over the run and over its
second half: ``rss_slope``; ``--max-slope`` fails a run whose process grew faster, D-195); "database is locked",
tracebacks and 5xx lines in the server's logs; the harness's own CPU (it must stay well under one core for the
figures to be the server's). After the run, with the server stopped: no invoice approved or exported twice, one
approval event per approved invoice, the invoices' summary equal to a recomputation, no login paused and no upload
refused for these honest people.

Output: ``OUT/report.json`` and ``OUT/report.md``, plus the server's output (``server.log``). The exit code is 1 when
anything failed (an error, a correctness check) or, with ``--baseline`` (an earlier ``report.json``), when a route's
p95 is over ``--max-regression`` times its baseline; 2 when the run could not be made (the server did not start).
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import copy
import datetime as dt
import html as htmllib
import json
import math
import os
import platform
import posixpath
import random
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

ROLE_MIX = (("clerk", 0.70), ("approver", 0.20), ("admin", 0.05), ("readonly", 0.05))
SCENARIOS = {  # each role's scenarios, by weight
    "clerk": {"work": 40, "look": 8, "queue": 15, "search": 12, "export_search": 3, "reports": 6, "upload": 3,
              "bulk": 3, "batch": 2},
    "approver": {"second": 30, "work": 20, "look": 5, "queue": 12, "search": 10, "export_search": 3, "reports": 12,
                 "bulk": 3, "batch": 5},
    "admin": {"admin": 25, "reports": 20, "queue": 15, "search": 15, "work": 10, "export_search": 5, "batch": 5,
              "second": 5},
    "readonly": {"queue": 30, "look": 30, "search": 20, "reports": 15, "export_search": 5},
}  # fmt: skip
REPORTS = ("/insights", "/spend", "/month-end", "/sales-tax", "/exports")  # anyone signed in
CLERK_REPORTS = ("/vendors", "/learning", "/purchase-orders")  # a Clerk and above
ADMIN_PAGES = ("/activity", "/admin/users", "/settings", "/learning", "/vendors")
QUEUE_VIEWS = ("", "?show=attention", "?show=ready", "?sort=due", "?sort=amount", "?sort=vendor", "?sort=newest",
               "?tab=second", "?tab=parked", "?tab=approved", "?tab=other")  # fmt: skip
EDIT_WORDS = ("(checked)", "per PO", "as quoted", "see note")
QUICK = {"users": 3, "duration": 20.0, "ramp": 5.0, "workers": 1, "think": 3.0, "seed_invoices": 200}

SLOW_SECONDS = 30.0  # a request answered later than this counts as a timeout
STOP_GRACE = 15.0  # seconds past the run's end and every timeout before the people still under way are stopped
CUT_OFF = "cut off"  # a request still under way then: recorded with its time so far
STEP_THINK = 1 / 3  # the think time between the steps of one scenario: this share of --think
THINK_SIGMA = 0.6  # the lognormal's spread: its p95 is about 2.7 times its median
MAX_LAG = 60.0  # a person this far behind their schedule starts again from now
JOB_POLL = 0.8  # app.js pollJob's interval
# How long a person keeps a connection unused (--keep-alive): httpx's 5 s by default, so earlier runs compare. A browser
# keeps its own for minutes (Chrome up to 5, Firefox about 2), which keeps people on the worker that took them (D-161).
KEEP_ALIVE = 5.0
JOB_MOST = 180.0  # an upload's job polled for at most this long; a download's too
DOWNLOAD_POLL, DOWNLOAD_POLL_MOST = 0.5, 5.0  # app.js: a download's job looked at every 0.5 s, up to 5 s apart
NOT_MADE = "search exports not made"  # its job failed or is gone, or its file was not there: the gate fails (B-1813)
BULK_MOST = 50  # invoices in one bulk approval (the page would post every candidate)
EXPORT_MOST = 50  # invoices in one export batch
REGRESSION_FLOOR = 0.05  # seconds: a p95 within this of its baseline is never a regression (noise on quick routes)
MIN_COUNT = 10  # requests a route needs, in both runs, for its p95 to be compared with the baseline
PAGE_TARGETS = {"p50": 0.3, "p95": 1.5, "p99": 3.0}  # docs/SCALE_GOAL.md, "Done means" 1
ACTION_TARGET_P95 = 1.0
ACTIONS = ("/check", "/approve", "/park", "/reject", "/final-approve")
STATIC_PREFIXES = ("/static/", "/fonts/", "/assets/")
UNDOING = ("reopened", "sent_back")  # events that take an approval back (the next approval is no second one)
AUDITED = ("approved", "final_approved", "exported", *UNDOING)
ACTED = {"park": "parked", "reject": "rejected"}
SUMMARY_SAMPLE = 2000  # invoices the run didn't touch, worked out again too (the touched ones always are)
SAMPLE_EVERY = 2.0  # seconds between two looks at each server process's CPU and memory (--sample-every)
SLOPE_SECONDS = 600.0  # a process's memory slope is said in MB per this long (``rss_slope``)


class StepFailed(Exception):
    """A request got no answer, or a 5xx: the scenario stops there (the person carries on with the next)."""


class LoggedOut(Exception):
    """The server sent the person to the login page: they log in again."""


class LoginFailed(Exception):
    """The login itself was refused."""


class Done(Exception):
    """The run's time is up for this person."""


@dataclass
class Account:
    username: str
    password: str
    secret: str
    role: str


# --- The data folder ----------------------------------------------------------------------------------------------


def role_counts(n: int) -> dict[str, int]:
    """``n`` people in ``ROLE_MIX`` (largest remainders, so they add up to ``n``)."""
    exact = {role: n * share for role, share in ROLE_MIX}
    counts = {role: int(v) for role, v in exact.items()}
    for role in sorted(exact, key=lambda r: exact[r] - counts[r], reverse=True)[: n - sum(counts.values())]:
        counts[role] += 1
    return counts


def seed_folder(folder: Path, invoices: int, people: int, seed: int = 1) -> Path:
    """A data folder for a load run: the sample setup (GL accounts, tax, policy, vendor master, POs, an approval limit
    of 15,000 CAD), ``invoices`` sample invoices with a file each (about half in review, the rest approved, waiting
    for a second approval, exported, parked or rejected; one in seven from a vendor of its own, so bulk approval has
    candidates), and ``people`` accounts in ``ROLE_MIX`` with their authenticator set up. Returns its users.json."""
    from ap_coder.ai.pipeline import finalise_coding
    from ap_coder.capture.workflow import capture_invoice
    from ap_coder.core import authenticator
    from ap_coder.core.config import Settings
    from ap_coder.core.schema import InvoiceCoding
    from ap_coder.core.store import (
        PENDING,
        Store,
        load_sample_purchase_orders,
        load_sample_setup,
        load_sample_vendor_master,
    )
    from ap_coder.ops.demo import SAMPLES_DIR

    rng = random.Random(seed)
    folder.mkdir(parents=True, exist_ok=True)
    files = folder / "invoices"
    files.mkdir(exist_ok=True)
    store = Store(folder / "ap_coder.db")
    load_sample_setup(store)
    load_sample_vendor_master(store)
    load_sample_purchase_orders(store)
    store.set_setting("approval_limit", "15000")
    store.set_setting("fx_rates", "USD=1.37")
    own = max(1, invoices // 7)  # invoices from a vendor of their own: clean, so bulk approval takes them
    store.import_vendor_master([{"vendor_name": f"Supplier {k:04d}", "erp_id": f"S{k:04d}"} for k in range(own)])
    reference, settings = store.reference_data(), Settings()
    stems = sorted(
        p.stem for p in (SAMPLES_DIR / "ground_truth").glob("*.json") if (SAMPLES_DIR / f"{p.stem}.pdf").exists()
    )
    samples = []
    for stem in stems:
        truth = json.loads((SAMPLES_DIR / "ground_truth" / f"{stem}.json").read_text(encoding="utf-8"))
        md = SAMPLES_DIR / f"{stem}.md"
        pdf = SAMPLES_DIR / f"{stem}.pdf"
        coding = InvoiceCoding.model_validate({k: v for k, v in truth.items() if k != "gl_distribution"})
        output, _ = finalise_coding(coding, reference, settings, store=store)
        capture, _, _ = capture_invoice(pdf, output, store=store)
        samples.append((stem, truth, md.read_text(encoding="utf-8") if md.exists() else "", pdf.read_bytes(),
                        capture.to_dict() if capture is not None else None))  # fmt: skip
    today, outputs, ids = dt.date.today(), {}, []
    with store.session():
        for i in range(invoices):
            stem, truth, md, pdf, capture = samples[i % len(samples)]
            doc = copy.deepcopy(truth)
            doc["invoice_number"] = f"{truth['invoice_number']}-{i:05d}"
            if i % 7 == 6:
                doc["vendor_name"] = f"Supplier {(i // 7) % own:04d}"
            day = dt.date.fromisoformat(truth["invoice_date"])
            new = today - dt.timedelta(days=rng.randint(0, 364))
            doc["invoice_date"] = new.isoformat()
            if truth.get("due_date"):
                doc["due_date"] = (new + (dt.date.fromisoformat(truth["due_date"]) - day)).isoformat()
            coding = InvoiceCoding.model_validate({k: v for k, v in doc.items() if k != "gl_distribution"})
            output, report = finalise_coding(coding, reference, settings, store=store)
            path = files / f"{stem}-{i:05d}.pdf"
            path.write_bytes(pdf + f"\n% load seed {i}\n".encode())
            invoice_id = store.add_invoice(path, output, report.to_dict(), extraction_md=md, meta={"load_seed": i})
            if capture is not None:
                store.save_capture(invoice_id, capture)
            outputs[invoice_id] = output
            ids.append(invoice_id)
        rng.shuffle(ids)
        cut = [int(invoices * share) for share in (0.40, 0.44, 0.47)]
        approved = ids[: cut[0]]
        for invoice_id in approved:
            store.approve_invoice(invoice_id, outputs[invoice_id], "Seed clerk", login="seed.clerk")
        for invoice_id in ids[cut[0] : cut[1]]:
            store.park_invoice(invoice_id, "Seed clerk", "Waiting for the purchase order")
        for invoice_id in ids[cut[1] : cut[2]]:
            store.reject_invoice(invoice_id, "Seed clerk", "Not ours")
        statuses = {r["id"]: r["status"] for r in store.invoice_columns(("id", "status"), ids=approved)}
        waiting = [i for i in approved if statuses.get(i) == PENDING]
        for invoice_id in waiting[: len(waiting) // 2]:
            shown = store.latest_approval_id(invoice_id)
            store.final_approve(invoice_id, "Seed approver", login="seed.approver", shown=shown)
        still = set(waiting[len(waiting) // 2 :])
        done = [i for i in approved if i not in still]
        exported = done[: len(done) // 2]
        for start in range(0, len(exported), 100):
            store.create_export_batch(exported[start : start + 100], "csv", actor="Seed clerk")
    accounts = []
    for role, count in role_counts(people).items():
        for k in range(1, count + 1):
            username = f"{role}{k:03d}"
            password = f"Load test {secrets.token_hex(6)} pass"
            user_id = store.add_user(username, password, role, f"{role.title()} {k}", must_change=False)
            secret = store.authenticator_secret(user_id)
            # Set up with the code of the step before this one: the person's first login (now or later) uses a newer
            # step, never one used already (a code is good once).
            store.check_code(user_id, authenticator.code(secret, time.time() - authenticator.STEP), "seed")
            accounts.append({"username": username, "password": password, "totp_secret": secret, "role": role})
    users = folder / "users.json"
    users.write_text(json.dumps(accounts, indent=1), encoding="utf-8")
    return users


def read_accounts(path: Path) -> list[Account]:
    """The accounts of a users.json: a list (or {"users": [...]}) of {username, password, totp_secret, role}."""
    found = json.loads(path.read_text(encoding="utf-8-sig"))
    rows = found.get("users", []) if isinstance(found, dict) else found
    accounts = []
    for row in rows:
        secret = row.get("totp_secret") or row.get("totp") or row.get("secret") or ""
        accounts.append(Account(str(row.get("username") or row.get("user") or ""), str(row.get("password") or ""),
                                str(secret), str(row.get("role") or "clerk").lower()))  # fmt: skip
    return [a for a in accounts if a.username and a.password]


def choose_people(accounts: list[Account], n: int) -> list[Account]:
    """``n`` accounts in ``ROLE_MIX``, each used by one person only (an authenticator code is good once)."""
    wanted, chosen = role_counts(n), []
    for role, count in wanted.items():
        mine = [a for a in accounts if a.role == role]
        if len(mine) < count:
            have = ", ".join(f"{len([a for a in accounts if a.role == r])} {r}" for r, _ in ROLE_MIX)
            raise SystemExit(f"{n} people need {count} {role} accounts; the users file has {have}. Seed a bigger "
                             "folder, or give fewer --users.")  # fmt: skip
        chosen += mine[:count]
    random.Random(n).shuffle(chosen)  # the roles mixed over the ramp
    return chosen


# --- The stand-in model server and the server under test ----------------------------------------------------------


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def network_address() -> str:
    """This computer's address on its network (no packet is sent: a UDP socket only picks its route), else
    127.0.0.1. People reach the server through it, so it sees them as other computers (not its own console, which
    logins are never paused for)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            address = s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    return address if address and not address.startswith("127.") else "127.0.0.1"


def _ready(url: str, deadline: float, proc: subprocess.Popen | None = None) -> bool:
    import urllib.request

    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            return False
        try:
            with direct.open(url, timeout=2) as answer:
                if answer.status < 500:
                    return True
        except OSError:
            time.sleep(0.3)
    return False


def start_model(host: str, latency: tuple[float, float], slots: int, log: Path) -> tuple[str, subprocess.Popen, str]:
    """The stand-in model server, in a process of its own (its CPU is not the harness's): ``FakeModel`` from
    ap_coder/bench/fake_model.py when it is there and starts, else scripts/ci/fake_lm_studio.py (on 127.0.0.1, no
    latency). (url, process, which)."""
    import importlib.util

    from ap_coder.core.paths import PROJECT_DIR

    script = PROJECT_DIR / "scripts" / "ci" / "fake_lm_studio.py"
    tries = []
    if importlib.util.find_spec("ap_coder.bench.fake_model") is not None:
        port = free_port()
        code = ("import sys\nfrom ap_coder.bench.fake_model import FakeModel\n"
                f"m = FakeModel({host!r}, {port}, latency=({latency[0]!r}, {latency[1]!r}), slots={slots})\n"
                "m.start()\nsys.stdin.read()\nm.stop()\n")  # fmt: skip
        which = f"ap_coder/bench/fake_model.py, {latency[0]:g}-{latency[1]:g} s an answer, {slots} at once"
        tries.append(([sys.executable, "-c", code], f"http://{host}:{port}/v1", which))
    port = free_port()
    tries.append(([sys.executable, str(script), "serve", "--port", str(port)], f"http://127.0.0.1:{port}/v1",
                  "scripts/ci/fake_lm_studio.py (answers at once, no latency or slots)"))  # fmt: skip
    with log.open("w", encoding="utf-8") as out:
        for command, url, which in tries:
            proc = subprocess.Popen(command, cwd=PROJECT_DIR, stdin=subprocess.PIPE, stdout=out,
                                    stderr=subprocess.STDOUT)  # fmt: skip
            if _ready(f"{url}/models", time.monotonic() + 30, proc):
                return url, proc, which
            stop_process(proc)
            print(f"the stand-in model server ({which}) did not start: see {log}", file=sys.stderr)
    raise RuntimeError(f"no stand-in model server started: see {log}")


def stop_process(proc: subprocess.Popen, wait: float = 10) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(wait)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(wait)


class Server:
    """``python -m ap_coder serve --server --insecure-http --workers W`` on ``data``, as a subprocess."""

    def __init__(self, data: Path, out: Path, workers: int, model_url: str, host: str, port: int) -> None:
        self.data, self.out, self.workers, self.port = data, out, workers, port
        self.log = out / "server.log"
        env_file = out / "server.env"
        lines = []
        if (data / ".env").is_file():  # the folder's own settings first, the run's after them
            lines.append((data / ".env").read_text(encoding="utf-8-sig"))
        lines += [f"AP_LLM_BASE_URL={model_url}", f"AP_PAGE_READER_BASE_URL={model_url}"]
        env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.env = {k: v for k, v in os.environ.items() if k not in ("AP_DB_PATH", "AP_SERVER_WORKERS", "AP_ENV_FILE")}
        self.env.update({"AP_PRIVATE_DIR": str(data), "AP_USER_SETTINGS": str(out / "user" / "settings.json"),
                         "BROWSER": "none", "AP_LLM_BASE_URL": model_url, "AP_PAGE_READER_BASE_URL": model_url,
                         "AP_ALLOWED_HOSTS": f"127.0.0.1,{host}", "PYTHONUNBUFFERED": "1"})  # fmt: skip
        self.command = [sys.executable, "-m", "ap_coder", "--env-file", str(env_file), "--db",
                        str(data / "ap_coder.db"), "serve", "--server", "--insecure-http", "--port", str(port),
                        "--workers", str(workers)]  # fmt: skip
        self.proc: subprocess.Popen | None = None
        self.started_in = self.stopped_in = None
        self.stop_how = ""
        self.logs_before = log_sizes(data / "logs")

    def start(self) -> None:
        from ap_coder.core.paths import PROJECT_DIR

        t0 = time.monotonic()
        (self.out / "user").mkdir(parents=True, exist_ok=True)
        out = self.log.open("w", encoding="utf-8")
        self.proc = subprocess.Popen(self.command, cwd=PROJECT_DIR, env=self.env, stdout=out, stderr=subprocess.STDOUT)
        if not _ready(f"http://127.0.0.1:{self.port}/healthz", time.monotonic() + 180, self.proc):
            self.stop()
            raise RuntimeError(f"the server did not start: see {self.log}")
        self.started_in = round(time.monotonic() - t0, 2)

    def stop(self) -> None:
        """The stop the service makes (POST /shutdown with the server's token), timed; killed if it takes a minute."""
        import urllib.request

        if self.proc is None or self.proc.poll() is not None:
            return
        t0 = time.monotonic()
        try:
            from ap_coder.web.server import SERVER_FILE

            token = json.loads((self.data / SERVER_FILE).read_text(encoding="utf-8"))["token"]
            stop = urllib.request.Request(f"http://127.0.0.1:{self.port}/shutdown", data=b"", method="POST",
                                          headers={"X-AP-Coder-Token": token})  # fmt: skip
            urllib.request.build_opener(urllib.request.ProxyHandler({})).open(stop, timeout=10).close()
            self.proc.wait(60)
            self.stop_how = "shutdown"
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
            self.stop_how = f"killed ({type(exc).__name__})"
            self.proc.send_signal(signal.SIGTERM)
            try:
                self.proc.wait(20)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(10)
        self.stopped_in = round(time.monotonic() - t0, 2)


# --- Each server process's CPU and memory ---------------------------------------------------------------------------


class Sampler(threading.Thread):
    """Every ``every`` seconds, each process of the server (its supervisor, workers and background process) and the
    model server: CPU (cores used since the last sample) and resident memory, from /proc (psutil elsewhere)."""

    def __init__(self, roots: dict[str, int], t0: float, every: float = 2.0) -> None:
        super().__init__(name="load-sampler", daemon=True)
        self.roots, self.t0, self.every = roots, t0, every
        self.series: dict[int, dict[str, Any]] = {}
        self.halt = threading.Event()
        self.proc = Path("/proc")
        try:
            import psutil  # noqa: F401  (optional: only where there is no /proc)

            self.psutil = not (self.proc / "self" / "stat").exists()
        except ImportError:
            self.psutil = False
        self.tick = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100
        self.page = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096

    def run(self) -> None:
        while not self.halt.wait(self.every):
            self.sample()

    def stop(self) -> None:
        self.halt.set()
        self.join(self.every * 3)
        self.sample()

    def _tree(self) -> dict[int, tuple[str, int, float, float]]:
        """{pid: (label, ppid, cpu seconds, rss MB)} of every process under the roots."""
        if self.psutil:
            import psutil

            found = {}
            for label, root in self.roots.items():
                try:
                    top = psutil.Process(root)
                    for p in [top, *top.children(recursive=True)]:
                        t, rss = p.cpu_times(), p.memory_info().rss
                        found[p.pid] = (self._label(label, p.pid, root, p.cmdline()), p.ppid(), t.user + t.system,
                                        rss / 1e6)  # fmt: skip
                except (psutil.Error, OSError):
                    continue
            return found
        stats = {}
        for entry in self.proc.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                text = (entry / "stat").read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            rest = text[text.rfind(")") + 2 :].split()
            stats[int(entry.name)] = (int(rest[1]), (int(rest[11]) + int(rest[12])) / self.tick,
                                      int(rest[21]) * self.page / 1e6)  # fmt: skip
        found = {}
        for label, root in self.roots.items():
            todo = [root]
            while todo:
                pid = todo.pop()
                if pid not in stats or pid in found:
                    continue
                try:
                    cmd = (self.proc / str(pid) / "cmdline").read_bytes().split(b"\0")
                except OSError:
                    cmd = []
                words = [c.decode("utf-8", "replace") for c in cmd]
                ppid, cpu, rss = stats[pid]
                found[pid] = (self._label(label, pid, root, words), ppid, cpu, rss)
                todo += [p for p, s in stats.items() if s[0] == pid]
        return found

    @staticmethod
    def _label(label: str, pid: int, root: int, words: list[str]) -> str:
        text = " ".join(words)
        if pid == root:
            return label
        if "--background-work" in text:
            return "background"
        if "resource_tracker" in text:
            return "resource tracker"
        if "multiprocessing" in text:
            return "worker"
        return f"{label} child"

    def sample(self) -> None:
        now = time.monotonic() - self.t0
        for pid, (label, _ppid, cpu, rss) in self._tree().items():
            s = self.series.setdefault(pid, {"label": label, "points": []})
            s["points"].append((round(now, 1), round(cpu, 2), round(rss, 1)))

    def summary(self) -> list[dict[str, Any]]:
        out, workers = [], 0
        for pid, s in sorted(self.series.items()):
            pts = s["points"]
            if not pts:
                continue
            label = s["label"]
            if label == "worker":
                workers += 1
                label = f"worker {workers}"
            span = max(pts[-1][0] - pts[0][0], 1e-9)
            cpu = pts[-1][1] - pts[0][1]
            timeline = [(t, round((c - pc) / max(t - pt, 1e-9), 2), r)
                        for (pt, pc, _), (t, c, r) in zip(pts, pts[1:], strict=False)]  # fmt: skip
            half = (pts[0][0] + pts[-1][0]) / 2
            out.append({"process": label, "pid": pid, "cpu_seconds": round(cpu, 1),
                        "mean_cores": round(cpu / span, 2) if len(pts) > 1 else None,
                        "peak_cores": max((c for _, c, _ in timeline), default=None),
                        "rss_mb_first": pts[0][2], "rss_mb_last": pts[-1][2],
                        "rss_mb_peak": max(r for _, _, r in pts), "rss_slope": rss_slope(pts),
                        "rss_slope_late": rss_slope([p for p in pts if p[0] >= half]),
                        "timeline": timeline})  # fmt: skip
        return out


def rss_slope(points: list[tuple[float, float, float]]) -> float | None:
    """How fast a process's resident memory grew over ``points`` (seconds into the run, CPU seconds, RSS MB): the
    least-squares line's slope, in MB per ``SLOPE_SECONDS`` (10 minutes); None for fewer than three points or less
    than a minute of them. A memory flat for an hour has a slope near 0 over the run's second half: its caches fill
    in the first minutes, up to their bounds (D-195)."""
    if len(points) < 3 or points[-1][0] - points[0][0] < 60:
        return None
    n = len(points)
    mean_t, mean_rss = sum(p[0] for p in points) / n, sum(p[2] for p in points) / n
    spread = sum((p[0] - mean_t) ** 2 for p in points)
    return round(sum((p[0] - mean_t) * (p[2] - mean_rss) for p in points) / spread * SLOPE_SECONDS, 1)


# --- What a page holds: its forms (as FormData sends them), its static files, its links -------------------------------

VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}
STATIC_REF = re.compile(r"""(?:src|href)=["'](/(?:static|fonts|assets)/[^"'#]+)["']""")
JS_IMPORT = re.compile(r"""^\s*import\s+(?:[^"';]*?\s+from\s+)?["'](\.{1,2}/[^"']+)["']""", re.M)
CSS_URL = re.compile(r"""url\(\s*["']?(/(?:fonts|static|assets)/[^"')\s]+)["']?\s*\)""")
INVOICE_LINK = re.compile(r'href="/review/(\d+)"')
VENDOR_LABEL = re.compile(r'aria-label="Review invoice from ([^"]+)"')
READER_POLL = re.compile(r'<div data-reader[^>]*\bdata-poll="(\d+)"')
READER_TAG = re.compile(r'<div data-reader[^>]*\bdata-tag="([^"]*)"')  # what its panel shows, which the poll sends back
INVOICE_URL = re.compile(r"^/review/(\d+)(?:[?#]|$)")
ACCESS_LOG = re.compile(r"access(?:-\d+)?\.log(?:\.\d+)?")  # a web worker's access log, or one rotated from it


def json_script(page: str, element_id: str) -> Any:
    """The JSON in the page's ``<script type="application/json" id=...>`` (None when there is none)."""
    found = re.search(rf'<script type="application/json" id="{re.escape(element_id)}">(.*?)</script>', page, re.S)
    if not found:
        return None
    try:
        return json.loads(found.group(1))
    except ValueError:
        return None


@dataclass
class Field:
    owner: str  # the form it belongs to (its id, or "form<N>")
    name: str
    kind: str  # input type, "select", "textarea"
    values: list[str]
    checkable: bool = False
    checked: bool = False
    disabled: bool = False
    section: str | None = None  # the [data-live] section it is drawn in (the checks draw those again)


class Forms(HTMLParser):
    """A page's forms as a browser has them: each form's action, method and attributes; every field in document
    order with its form (a field's ``form=`` attribute, else the form around it) and its [data-live] section; each
    form's submit buttons (disabled or not). What a ``<template>`` holds (a new row drawn by the page) is no part of
    the page (B-1622)."""

    def __init__(self, form: str | None = None, section: str | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: dict[str, dict[str, Any]] = {}
        self.fields: list[Field] = []
        self.buttons: dict[str, list[bool]] = collections.defaultdict(list)
        self.section_owner: dict[str, str | None] = {}
        self._form = form
        self._stack: list[tuple[str, str | None]] = [("", section)] if section else []
        self._select: Field | None = None
        self._options: list[tuple[str | None, str, bool]] = []
        self._text: list[str] | None = None
        self._textarea: Field | None = None
        self._template = 0  # inside a <template>, this deep

    def _section(self) -> str | None:
        return next((s for _, s in reversed(self._stack) if s), None)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._template += tag == "template"
        if self._template:
            return
        a = {k: v if v is not None else "" for k, v in attrs}
        if tag not in VOID:
            self._stack.append((tag, a.get("data-live")))
            if a.get("data-live"):
                self.section_owner[a["data-live"]] = self._form
        owner = a.get("form") or self._form or ""
        disabled = "disabled" in a
        if tag == "form":
            key = a.get("id") or f"form{len(self.forms)}"
            self.forms[key] = {"action": a.get("action", ""), "method": a.get("method", "get").lower(), "attrs": a}
            self._form = key
        elif tag == "input":
            kind = a.get("type", "text").lower()
            if kind in ("submit", "image"):
                self.buttons[owner].append(disabled)
            elif kind not in ("button", "reset", "file") and a.get("name"):
                checkable = kind in ("checkbox", "radio")
                value = a.get("value", "on" if checkable else "")
                self.fields.append(Field(owner, a["name"], kind, [value], checkable, "checked" in a, disabled,
                                         self._section()))  # fmt: skip
        elif tag == "button":
            if a.get("type", "submit").lower() == "submit":
                self.buttons[owner].append(disabled)
        elif tag == "select" and a.get("name"):
            self._select = Field(owner, a["name"], "select-multiple" if "multiple" in a else "select", [],
                                 disabled=disabled, section=self._section())  # fmt: skip
            self._options = []
        elif tag == "option" and self._select is not None:
            self._end_option()
            self._options.append((a.get("value"), "", "selected" in a))
            self._text = []
        elif tag == "textarea" and a.get("name"):
            self._textarea = Field(owner, a["name"], "textarea", [], disabled=disabled, section=self._section())
            self._text = []

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self._template:
            return
        self.handle_starttag(tag, attrs)
        if tag not in VOID and self._stack:
            self._stack.pop()

    def handle_data(self, data: str) -> None:
        if self._text is not None:
            self._text.append(data)

    def _end_option(self) -> None:
        if self._select is not None and self._options and self._text is not None:
            value, _, selected = self._options[-1]
            self._options[-1] = (value, "".join(self._text).strip(), selected)
        self._text = None

    def handle_endtag(self, tag: str) -> None:
        if self._template:
            self._template -= tag == "template"
            return
        if tag == "option":
            self._end_option()
        elif tag == "select" and self._select is not None:
            self._end_option()
            chosen = [v if v is not None else t for v, t, s in self._options if s]
            if not chosen and self._select.kind == "select" and self._options:
                v, t, _ = self._options[0]
                chosen = [v if v is not None else t]
            self._select.values = chosen
            self.fields.append(self._select)
            self._select = None
        elif tag == "textarea" and self._textarea is not None:
            text = "".join(self._text or [])
            self._textarea.values = [text[1:] if text.startswith("\n") else text]
            self.fields.append(self._textarea)
            self._textarea, self._text = None, None
        elif tag == "form":
            self._form = None
        for i in range(len(self._stack) - 1, -1, -1):
            if self._stack[i][0] == tag:
                del self._stack[i:]
                break


def form_data(fields: list[Field], owner: str, **values: str) -> list[tuple[str, str]]:
    """What ``FormData`` of form ``owner`` holds, with the text fields named in ``values`` typed in."""
    out = []
    for f in fields:
        if f.owner != owner or f.disabled:
            continue
        if f.checkable:
            if f.checked:
                out.append((f.name, f.values[0] if f.values else "on"))
        elif f.name in values:
            out.append((f.name, values[f.name]))
        else:
            out += [(f.name, v) for v in f.values]
    return out


class Screen:
    """An invoice's review screen as the browser holds it: its forms and fields (changed by edits and by the
    sections each check draws again), its page images, its reader poll, its accounts."""

    def __init__(self, invoice_id: int, page: str) -> None:
        parsed = Forms()
        parsed.feed(page)
        parsed.close()
        self.id, self.forms, self.fields, self.buttons = invoice_id, parsed.forms, parsed.fields, parsed.buttons
        self.section_owner = parsed.section_owner
        inv = self.forms.get("inv") or {}
        self.editable = bool(inv) and "data-readonly" not in inv["attrs"] and inv["method"] == "post"
        poll = READER_POLL.search(page)
        self.poll = int(poll.group(1)) if poll else 0
        tag = READER_TAG.search(page)
        self.tag = tag.group(1) if tag else ""
        viewer = json_script(page, "viewer-data") or {}
        self.pages = [p["src"] for p in viewer.get("pages", []) if isinstance(p, dict) and p.get("src")]
        seed = json_script(page, "vendor-seed") or {}  # the vendor list's address (none to fetch: an empty list)
        self.vendors = str(seed.get("url") or "") if isinstance(seed, dict) and seed.get("count") else ""
        options = json_script(page, "gl-options") or []
        self.accounts = [str(o[0] if isinstance(o, list) else o.get("value", o.get("code", ""))) for o in options
                         if isinstance(o, list | dict)]  # fmt: skip
        self.accounts = [a for a in self.accounts if a and a != "UNASSIGNED"]  # (that one is "Needs an account")
        self.allow: bool | None = None

    def data(self, owner: str, **values: str) -> list[tuple[str, str]]:
        return form_data(self.fields, owner, **values)

    def form_ending(self, action_end: str) -> str | None:
        return next((k for k, f in self.forms.items() if f["action"].endswith(action_end)), None)

    def field(self, name: str, owner: str = "inv") -> Field | None:
        return next((f for f in self.fields if f.owner == owner and f.name == name), None)

    def apply(self, answer: dict[str, Any]) -> None:
        """A check's answer: whether Approve is open, and the sections drawn again (their fields replaced)."""
        if "allow" in answer:
            self.allow = bool(answer["allow"])
        for name, fragment in (answer.get("sections") or {}).items():
            if name not in self.section_owner or not isinstance(fragment, str):
                continue
            at = next((i for i, f in enumerate(self.fields) if f.section == name), len(self.fields))
            self.fields = [f for f in self.fields if f.section != name]
            fresh = Forms(self.section_owner[name], name)
            fresh.feed(fragment)
            fresh.close()
            at = min(at, len(self.fields))
            self.fields[at:at] = fresh.fields

    def edit(self, rng: random.Random) -> str:
        """One edit as a clerk makes it: a line's account changed, a word added to (or taken from) its description,
        or its reason typed in. What was changed."""

        def lines(name: str) -> list[Field]:
            return [f for f in self.fields if f.owner == "inv" and f.name == name and not f.disabled]

        accounts, descriptions, reasons = lines("line_gl"), lines("line_description"), lines("line_reason")
        pick = rng.random()
        if pick < 0.25 and accounts and self.accounts:  # half the time an account another line of it has
            used = [v for f in accounts for v in f.values if v in self.accounts]
            rng.choice(accounts).values = [rng.choice(used if used and rng.random() < 0.5 else self.accounts)]
            return "account"
        if pick < 0.8 and descriptions:
            f = rng.choice(descriptions)
            text = f.values[0] if f.values else ""
            word = next((w for w in EDIT_WORDS if text.endswith(" " + w)), None)
            f.values = [text[: -len(word) - 1] if word else f"{text} {rng.choice(EDIT_WORDS)}"]
            return "description"
        if reasons:
            rng.choice(reasons).values = [f"Checked by AP {rng.randint(1, 99)}"]
            return "reason"
        return ""


def route_of(method: str, url: str) -> str:
    """The route of a request, its ids taken out: GET /review/{id}/page/{n}.png, GET /static/{file}."""
    path = urlsplit(url).path or "/"
    for prefix in STATIC_PREFIXES:
        if path.startswith(prefix):
            return f"{method} {prefix}{{file}}"
    path = re.sub(r"/page/\d+\.png$", "/page/{n}.png", path)
    path = re.sub(r"/\d+(?=/|$)", "/{id}", path)
    return f"{method} {path}"


def multipart(fields: list[tuple[str, str]], files: list[tuple[str, str, bytes, str]] = ()) -> tuple[bytes, str]:
    """A body as a browser's FormData sends it (multipart/form-data): (body, content type)."""
    boundary = "----APCoderLoad" + secrets.token_hex(12)

    def quoted(text: str) -> str:
        return text.replace('"', "%22").replace("\r", "%0D").replace("\n", "%0A")

    out = bytearray()
    for name, value in fields:
        out += f'--{boundary}\r\nContent-Disposition: form-data; name="{quoted(name)}"\r\n\r\n'.encode()
        out += str(value).encode("utf-8") + b"\r\n"
    for name, filename, content, kind in files:
        out += (f'--{boundary}\r\nContent-Disposition: form-data; name="{quoted(name)}"; filename="{quoted(filename)}"'
                f"\r\nContent-Type: {kind}\r\n\r\n").encode()  # fmt: skip
        out += content + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


# --- A browser's cache for static files ----------------------------------------------------------------------------


STORED_HEADERS = ("cache-control", "date", "etag", "last-modified")  # what its freshness and its next check read


@dataclass
class Cached:
    headers: dict[str, str]  # its STORED_HEADERS, each 304's laid over them (RFC 9111 4.3.4: the others kept)
    fresh_until: float  # time.monotonic()
    no_cache: bool
    children: list[str]  # what it brings in (a stylesheet's fonts, a module's imports)


def stored(headers: Any) -> dict[str, str]:
    return {name: headers[name] for name in STORED_HEADERS if name in headers}


def freshness(headers: Any) -> tuple[bool, bool, float]:
    """(kept, revalidated every use, seconds fresh) as a browser reads a response's headers: ``no-store`` never
    kept; ``no-cache`` kept but asked again (conditionally) each time; ``max-age`` fresh that long (``immutable``
    the same, as there is no reload here); with neither, a tenth of its age since Last-Modified, at most 7 days."""
    directives = {}
    for part in (headers.get("cache-control") or "").split(","):
        key, _, value = part.strip().partition("=")
        if key:
            directives[key.lower()] = value.strip('"')
    if "no-store" in directives:
        return False, False, 0.0
    no_cache = "no-cache" in directives
    if "max-age" in directives:
        try:
            return True, no_cache, max(0.0, float(directives["max-age"]))
        except ValueError:
            return True, no_cache, 0.0
    try:
        modified = parsedate_to_datetime(headers.get("last-modified") or "")
        date = parsedate_to_datetime(headers.get("date") or "") if headers.get("date") else dt.datetime.now(dt.UTC)
        return True, no_cache, min(max(0.0, (date - modified).total_seconds() / 10), 7 * 86400.0)
    except (TypeError, ValueError):
        return True, no_cache, 0.0


def children_of(url: str, body: str) -> list[str]:
    """The files a stylesheet (its fonts) or an ES module (its imports) brings in."""
    path = urlsplit(url).path
    if path.endswith(".css"):
        return list(dict.fromkeys(CSS_URL.findall(body)))
    if path.endswith(".js"):
        folder = posixpath.dirname(path)
        return list(dict.fromkeys(posixpath.normpath(posixpath.join(folder, i)) for i in JS_IMPORT.findall(body)))
    return []


# --- One person: a browser, logged in, working --------------------------------------------------------------------


class Person:
    def __init__(self, run: Run, account: Account, index: int, start: float) -> None:
        import httpx

        self.run, self.account, self.index = run, account, index
        self.rng = random.Random(f"{run.seed}/{index}")
        self.client = httpx.AsyncClient(
            base_url=run.base, trust_env=False, follow_redirects=False,
            timeout=httpx.Timeout(run.timeout, connect=10.0),
            limits=httpx.Limits(max_connections=6, max_keepalive_connections=6, keepalive_expiry=run.keep_alive),
            headers={"User-Agent": "Mozilla/5.0 (AP Coder load test)", "Accept-Language": "en-CA,en;q=0.9",
                     "Accept-Encoding": "gzip, deflate"},
        )  # fmt: skip
        self.httpx = httpx
        self.cache: dict[str, Cached] = {}
        self.next = start  # when this person's next step is due (time.monotonic)
        self.current: Screen | None = None  # the invoice an action led to (Next), still open
        self.poller: asyncio.Task | None = None

    # The schedule ---------------------------------------------------------------------------------------------------

    def think(self, share: float = 1.0) -> float:
        median = max(0.05, self.run.think * share)
        return min(10 * median, self.rng.lognormvariate(math.log(median), THINK_SIGMA))

    async def step(self, think: float) -> float:
        """Wait for this person's next step, due ``think`` seconds after their last one was due; when it was due."""
        run = self.run
        self.next += think
        now = time.monotonic()
        if now - self.next > MAX_LAG:
            run.counts["schedule restarted (over a minute behind)"] += 1
            self.next = now
        if self.next >= run.deadline:
            raise Done
        if self.next > now:
            await asyncio.sleep(self.next - now)
        run.lateness.append(max(0.0, time.monotonic() - self.next))
        return self.next

    # Requests -------------------------------------------------------------------------------------------------------

    async def send(self, method: str, url: str, kind: str, *, route: str = "", due: float | None = None,
                   fields: list[tuple[str, str]] | None = None, files: list | None = None, accept: str = "",
                   headers: dict[str, str] | None = None, urlencoded: bool = False) -> Any:  # fmt: skip
        """One request, timed from when it was due (``due``: a person's step) or sent; recorded under its route.
        A POST's fields go as multipart (FormData), or ``urlencoded`` (URLSearchParams: the login's forms). StepFailed
        for no answer or a 5xx; LoggedOut when it sent the person to the login page."""
        run, httpx = self.run, self.httpx
        sent = {"Accept": accept or ("text/html,application/xhtml+xml,*/*;q=0.8" if kind == "page" else "*/*")}
        sent.update(headers or {})
        body = None
        if method == "POST":
            sent.update(run.post_headers)
            if urlencoded:
                body = urlencode(fields or []).encode("ascii")
                sent["Content-Type"] = "application/x-www-form-urlencoded;charset=UTF-8"
            else:
                body, sent["Content-Type"] = multipart(fields or [], files or [])
        start = time.monotonic()
        answer, status, size, error = None, 0, 0, ""
        stopped: asyncio.CancelledError | None = None
        try:
            answer = await self.client.request(method, url, content=body, headers=sent)
            status, size = answer.status_code, answer.num_bytes_downloaded  # on the wire (compressed, if it is)
        except httpx.TimeoutException:
            error = "timeout"
        except httpx.HTTPError as exc:
            error = type(exc).__name__
        except asyncio.CancelledError as exc:
            if not run.stopping:  # the poll of a page the person left: the browser drops it too
                raise
            stopped, error = exc, CUT_OFF  # still under way as the run stopped its people: the slowest, recorded
        end = time.monotonic()
        run.record(route or route_of(method, url), kind, status, end - (start if due is None else due), end - start,
                   end, size, error)  # fmt: skip
        if stopped is not None:
            raise stopped
        if answer is None:
            raise StepFailed(error)
        if status >= 500:
            raise StepFailed(str(status))
        if status in (302, 303, 307) and answer.headers.get("location", "").startswith("/login"):
            raise LoggedOut
        return answer

    async def page(self, url: str, due: float | None = None, route: str = "") -> str | None:
        """A page as the browser opens it: the HTML (its redirects followed), then its styles, scripts, fonts and
        pictures, from the cache when they are fresh. None when it was refused."""
        self.leave()
        answer = await self.send("GET", url, "page", route=route, due=due)
        for _ in range(3):
            if answer.status_code not in (301, 302, 303, 307, 308):
                break
            answer = await self.send("GET", answer.headers["location"], "page")
        if answer.status_code != 200:
            return None
        text = answer.text
        await self.assets(text)
        for name in VENDOR_LABEL.findall(text)[:20]:
            word = htmllib.unescape(name).split()[0] if name.split() else ""
            if len(word) > 2 and len(self.run.vendor_words) < 200:
                self.run.vendor_words.add(word)
        return text

    async def assets(self, text: str) -> None:
        """The static files a page brings in, level by level (a stylesheet's fonts and a module's imports after it),
        at most six at once, as a browser fetches them."""
        seen: set[str] = set()
        level = list(dict.fromkeys(htmllib.unescape(u) for u in STATIC_REF.findall(text)))
        while level:
            seen.update(level)
            found = await asyncio.gather(*(self.static(u) for u in level), return_exceptions=True)
            failed = next((f for f in found if isinstance(f, BaseException)), None)
            if failed is not None:
                raise failed
            level = [c for kids in found for c in kids if c not in seen]

    async def static(self, url: str, kind: str = "static") -> list[str]:
        """A file through the cache: no request while it is fresh; a conditional one when it was kept."""
        entry = self.cache.get(url)
        if entry is not None and not entry.no_cache and time.monotonic() < entry.fresh_until:
            self.run.counts[f"{kind} from the cache"] += 1
            return entry.children
        asked = {}
        if entry is not None:
            if entry.headers.get("etag"):
                asked["If-None-Match"] = entry.headers["etag"]
            if entry.headers.get("last-modified"):
                asked["If-Modified-Since"] = entry.headers["last-modified"]
        accept = "image/avif,image/webp,image/png,*/*;q=0.8" if kind == "image" else ""
        answer = await self.send("GET", url, kind, headers=asked, accept=accept)
        if answer.status_code == 304 and entry is not None:  # Starlette's says no Last-Modified: the stored one kept
            entry.headers.update(stored(answer.headers))
            _, entry.no_cache, life = freshness(entry.headers)
            entry.fresh_until = time.monotonic() + life
            return entry.children
        if answer.status_code != 200:
            return []
        kept, no_cache, life = freshness(answer.headers)
        kids = children_of(url, answer.text) if urlsplit(url).path.endswith((".css", ".js")) else []
        if kept:
            self.cache[url] = Cached(stored(answer.headers), time.monotonic() + life, no_cache, kids)
        else:
            self.cache.pop(url, None)
        return kids

    async def post(self, url: str, fields: list[tuple[str, str]], due: float | None = None, kind: str = "action",
                   files: list | None = None, urlencoded: bool = False) -> tuple[int, dict[str, Any]]:  # fmt: skip
        answer = await self.send("POST", url, kind, due=due, fields=fields, files=files, urlencoded=urlencoded)
        try:
            found = answer.json()
        except ValueError:
            found = {}
        return answer.status_code, found if isinstance(found, dict) else {}

    async def form_post(self, page: str | None, action: str, due: float, kind: str = "action",
                        **typed: str) -> tuple[int, dict[str, Any]]:  # fmt: skip
        """The form of ``page`` whose action is ``action``, sent as the page sends it: its fields with ``typed`` typed
        in, urlencoded when the form says ``data-urlencoded``."""
        parsed = Forms()
        parsed.feed(page or "")
        key = next((k for k, f in parsed.forms.items() if f["action"] == action), None)
        if key is None:
            raise StepFailed(f"no form for {action} on the page")
        encoded = "data-urlencoded" in parsed.forms[key]["attrs"]
        return await self.post(action, form_data(parsed.fields, key, **typed), due, kind, urlencoded=encoded)

    async def follow(self, url: str) -> None:
        """Where an action's answer leads (``redirect``), as the page goes there: an invoice opens in full."""
        found = INVOICE_URL.match(url)
        if found:
            self.current = await self.open_invoice(int(found.group(1)), url=url)
        else:
            self.current = None
            await self.page(url)

    def leave(self) -> None:
        if self.poller is not None:
            self.poller.cancel()
            self.poller = None

    async def close(self) -> None:
        self.leave()
        await self.client.aclose()

    # Logging in -----------------------------------------------------------------------------------------------------

    async def login(self) -> None:
        from ap_coder.core import authenticator

        a = self.account
        page = await self.page("/login", due=await self.step(0))
        due = await self.step(self.think(0.3))
        status, answer = await self.form_post(page, "/login", due, "login", username=a.username, password=a.password)
        if status != 200 or not answer.get("redirect"):
            raise LoginFailed(f"password: {status} {answer.get('error', '')}")
        step = urlsplit(answer["redirect"]).path
        if step not in ("/login/code", "/login/authenticator"):
            raise LoginFailed(f"the login goes on at {step}: seed the accounts with their passwords chosen")
        page = await self.page(answer["redirect"], route=f"GET {step}")
        for attempt in range(2):
            due = await self.step(self.think(0.3))  # the code read off the phone
            code = authenticator.code(self.account.secret, time.time())
            status, answer = await self.form_post(page, step, due, "login", code=code)
            if status == 200 and answer.get("redirect"):
                break
            if attempt == 0 and status == 401:  # that step's code used already (a login just before): the next
                self.run.counts["login codes refused (step used), tried at the next step"] += 1
                self.next = time.monotonic() + authenticator.STEP - time.time() % authenticator.STEP + 1
                continue
            raise LoginFailed(f"code: {status} {answer.get('error', '')}")
        self.run.counts["logins"] += 1
        await self.follow(answer["redirect"])

    # Scenarios ------------------------------------------------------------------------------------------------------

    def pick(self, ids: list[int]) -> int:
        """An invoice of a list as people choose: mostly near the top (the lower of two picks in the first 50)."""
        top = ids[:50]
        return top[min(self.rng.randrange(len(top)), self.rng.randrange(len(top)))]

    async def open_invoice(self, invoice_id: int, due: float | None = None, url: str = "") -> Screen | None:
        """The review screen as the browser opens it: the page and its files, the first page image (sometimes more
        pages, sometimes zoomed in), the vendor list (an editable screen's) and the reader's poll (no check on
        opening: the page is drawn with its checks)."""
        page = await self.page(url or f"/review/{invoice_id}", due, route="GET /review/{id}")
        if page is None:
            return None
        screen = Screen(invoice_id, page)
        fetched = []
        if screen.pages:
            want = [screen.pages[0]] + [p for p in screen.pages[1:3] if self.rng.random() < 0.5]
            if self.rng.random() < 0.2:
                first = screen.pages[0]
                want.append(f"{first}{'&' if '?' in first else '?'}scale=2")
            fetched += [self.static(p, "image") for p in want]
        if screen.editable and screen.vendors:  # the vendor list, as invoice.js fetches it (D-160): through the cache
            fetched.append(self.static(screen.vendors, "vendors"))
        if fetched:
            found = await asyncio.gather(*fetched, return_exceptions=True)
            failed = next((f for f in found if isinstance(f, BaseException)), None)
            if failed is not None:
                raise failed
        if screen.poll:
            self.poller = asyncio.create_task(self.watch_reader(screen))
        return screen

    async def watch_reader(self, screen: Screen) -> None:
        """The reader's poll while the screen is open (invoice.js: every data-poll seconds, at least 5), with what its
        panel shows (``?seen=``: the answer is only "same" while it still is)."""
        every = max(5, screen.poll)
        while time.monotonic() + every < self.run.deadline:
            await asyncio.sleep(every)
            try:
                answer = await self.send("GET", f"/review/{screen.id}/reader?seen={quote(screen.tag)}", "poll",
                                         accept="application/json")  # fmt: skip
                found = answer.json()
                if not found.get("poll"):
                    return
                if not found.get("same"):
                    screen.tag = str(found.get("tag") or "")
            except (StepFailed, LoggedOut, ValueError):
                continue

    async def check(self, screen: Screen, due: float | None = None) -> bool:
        """The screen checked as it is now; False when the invoice left the review queue meanwhile (409: someone
        else approved or parked it), which the page says, and the person moves on."""
        status, answer = await self.post(f"/review/{screen.id}/check", screen.data("inv"), due)
        if status == 409:
            self.run.counts["invoices acted on by someone else meanwhile (409)"] += 1
            return False
        if status == 200 and answer.get("ok", True):
            screen.apply(answer)
        return True

    async def queue(self) -> None:
        page = await self.page(
            "/review" + self.rng.choice(QUEUE_VIEWS + self.searches()), await self.step(self.think())
        )
        if page and self.rng.random() < 0.25:  # "Show 50 more"
            await self.page("/review?limit=100", await self.step(self.think(STEP_THINK)))

    def searches(self) -> tuple[str, ...]:
        words = sorted(self.run.vendor_words)
        return (f"?q={self.rng.choice(words)}",) if words else ()

    async def queue_ids(self, view: str = "") -> list[int]:
        page = await self.page("/review" + view, await self.step(self.think()))
        return [int(i) for i in dict.fromkeys(INVOICE_LINK.findall(page or ""))]

    async def work(self) -> None:
        """An invoice worked through: opened (or the one Next opened), 3 to 6 edits each checked, then approved
        (approve anyway when only errors keep it closed), parked, rejected, or left with its draft."""
        screen = self.current if self.current is not None and self.rng.random() < 0.8 else None
        if screen is None:
            ids = await self.queue_ids(self.rng.choice(("", "", "?sort=due", "?show=ready", "?show=attention")))
            if not ids:
                return
            screen = await self.open_invoice(self.pick(ids), await self.step(self.think(STEP_THINK)))
        else:
            await self.step(self.think(STEP_THINK))
        self.current = None
        if screen is None or not screen.editable:
            return
        for _ in range(self.rng.randint(3, 6)):
            screen.edit(self.rng)
            if not await self.check(screen, await self.step(self.think(STEP_THINK))):
                return
        choice = self.rng.random()
        if choice < 0.72:
            await self.approve(screen)
        elif choice < 0.86:
            await self.act(screen, "/park", reason="Waiting for the purchase order")
        elif choice < 0.91:
            await self.act(screen, "/reject", reason="Billed twice")
        else:
            self.run.counts["invoices left with a draft"] += 1

    async def approve(self, screen: Screen) -> None:
        if screen.allow is False:
            box, why = screen.field("override"), screen.field("override_reason")
            if box is not None and why is not None:  # errors only: "Approve anyway", with why
                box.checked, why.values = True, ["Checked with the vendor"]
                await self.check(screen, await self.step(self.think(STEP_THINK)))
            if not screen.allow:
                self.run.counts["approvals not open: parked instead"] += 1
                await self.act(screen, "/park", reason="Not ready to approve")
                return
        due = await self.step(self.think(STEP_THINK))
        fields = screen.data("inv")
        url = screen.forms["inv"]["action"]
        status, answer = await self.post(url, fields, due)
        if answer.get("ask_why"):  # the same correction again: the question skipped
            status, answer = await self.post(url, [*fields, ("why_done", "1")], await self.step(self.think(STEP_THINK)))
        if status == 200 and answer.get("redirect"):
            self.run.counts["approved"] += 1
            await self.follow(answer["redirect"])
        else:
            self.run.counts["approvals refused"] += 1

    async def act(self, screen: Screen, ending: str, **typed: str) -> None:
        """Park or reject (a form of the screen), with the reason typed in."""
        key = screen.form_ending(ending)
        if key is None:
            return
        status, answer = await self.post(screen.forms[key]["action"], screen.data(key, **typed),
                                         await self.step(self.think(STEP_THINK)))  # fmt: skip
        name = ending.strip("/")
        if status == 200 and answer.get("redirect"):
            self.run.counts[ACTED.get(name, name)] += 1
            await self.follow(answer["redirect"])
        else:
            self.run.counts[f"{name} refused"] += 1

    async def look(self) -> None:
        """An invoice opened to look at it, then left."""
        ids = await self.queue_ids(self.rng.choice(("", "?tab=approved", "?tab=parked", "?show=attention")))
        if ids:
            await self.open_invoice(self.pick(ids), await self.step(self.think(STEP_THINK)))
            await self.step(self.think())  # read
            self.current = None

    async def second(self) -> None:
        """A second approval: the tab, an invoice, its Final approval (when this person may give it)."""
        ids = await self.queue_ids("?tab=second")
        if not ids:
            return
        screen = await self.open_invoice(self.pick(ids), await self.step(self.think(STEP_THINK)))
        key = screen.form_ending("/final-approve") if screen else None
        if screen is None or key is None or all(screen.buttons.get(key, [True])):
            self.run.counts["second approvals not theirs to give"] += 1
            return
        status, answer = await self.post(screen.forms[key]["action"], screen.data(key),
                                         await self.step(self.think(STEP_THINK)))  # fmt: skip
        if status == 200 and answer.get("redirect"):
            self.run.counts["final approvals"] += 1
            await self.follow(answer["redirect"])
        else:
            self.run.counts["final approvals refused"] += 1

    def search_query(self) -> str:
        words = sorted(self.run.vendor_words) or ["Supplier"]
        word = self.rng.choice(words)
        return self.rng.choice((f"q={word}", f"f_vendor={word}", "f_status=approved", "sort=total&dir=desc",
                                "sort=invoice_date&dir=asc", "page=2", "", f"q={word}&f_status=review"))  # fmt: skip

    async def search(self) -> None:
        query = self.search_query()
        page = await self.page("/search" + (f"?{query}" if query else ""), await self.step(self.think()))
        ids = [int(i) for i in dict.fromkeys(INVOICE_LINK.findall(page or ""))]
        if ids and self.rng.random() < 0.3:
            await self.open_invoice(self.pick(ids), await self.step(self.think(STEP_THINK)))
            self.current = None

    async def export_search(self) -> None:
        """A search, then its Excel or CSV as the page's link gets it on the server (D-169, ``app.js``): the download
        asked for with the page's header, its job followed, then the file downloaded once made. A job that failed or
        is gone, or a file not there once made, fails the run (``NOT_MADE``), as the 500 of its plain address did."""
        query = self.search_query()
        await self.page("/search" + (f"?{query}" if query else ""), await self.step(self.think()))
        kind = "xlsx" if self.rng.random() < 1 / 3 else "csv"
        asked = await self.send("GET", f"/search/export.{kind}" + (f"?{query}" if query else ""), "download",
                                due=await self.step(self.think(STEP_THINK)), accept="application/json",
                                headers={"X-AP-Coder": "1"})  # fmt: skip
        try:
            answer = asked.json()
        except ValueError:  # the file itself: made as it is asked for (a laptop)
            return
        if not isinstance(answer, dict) or not answer.get("job"):
            self.run.counts["search exports refused" + (" (too many at once)" if asked.status_code == 429 else "")] += 1
            return
        state = (await self.follow_job(str(answer["status"]))).get("state")
        if state == "running":  # the person stopped waiting (JOB_MOST, the run's end)
            self.run.counts["search exports still being made when left"] += 1
            return
        if state == "done":
            got = await self.send("GET", str(answer["file"]), "download", route=f"GET /search/export.{kind}?job={{id}}")
            if got.status_code == 200:
                self.run.counts["search exports downloaded"] += 1
                return
        self.run.counts[NOT_MADE] += 1

    async def follow_job(self, url: str) -> dict[str, Any]:
        """A download's job looked at as ``app.js`` follows it (``pollJob``): at once, then DOWNLOAD_POLL seconds
        after an answer that changed, half as long again after one the same as the last, DOWNLOAD_POLL_MOST at most;
        its last snapshot once it is no longer running (or after JOB_MOST, or at the run's end)."""
        wait, last, until = DOWNLOAD_POLL, None, min(time.monotonic() + JOB_MOST, self.run.deadline)
        while True:
            polled = await self.send("GET", url, "poll", accept="application/json")
            try:
                snap = polled.json()
            except ValueError:
                snap = {}
            if not isinstance(snap, dict) or snap.get("state") != "running" or time.monotonic() >= until:
                return snap if isinstance(snap, dict) else {}
            wait = DOWNLOAD_POLL if polled.text != last else min(DOWNLOAD_POLL_MOST, wait * 1.5)
            last = polled.text
            await asyncio.sleep(wait)

    async def reports(self) -> None:
        pages = REPORTS + (CLERK_REPORTS if self.account.role != "readonly" else ())
        await self.page(self.rng.choice(pages), await self.step(self.think()))

    async def admin(self) -> None:
        await self.page(self.rng.choice(ADMIN_PAGES), await self.step(self.think()))

    async def upload(self) -> None:
        """A small PDF uploaded on the Import page, its job followed (every 0.8 s) until it is done."""
        from ap_coder.ops.demo import SAMPLES_DIR

        await self.page("/process", await self.step(self.think()))
        pdfs = sorted(SAMPLES_DIR.glob("*.pdf"))
        source = self.rng.choice(pdfs)
        tag = secrets.token_hex(6)
        content = source.read_bytes() + f"\n% load upload {tag}\n".encode()  # a file of its own (its hash is new)
        upload = ("files", f"{source.stem}-{tag}.pdf", content, "application/pdf")
        status, answer = await self.post("/process/upload", [], await self.step(self.think(STEP_THINK)), files=[upload])
        if status == 429:
            self.run.counts["uploads refused by the hourly allowance"] += 1
            return
        if status != 200 or not answer.get("ok"):
            self.run.counts["uploads refused"] += 1
            return
        self.run.counts["uploads"] += 1
        status_url, until = str(answer.get("status") or "/process/status"), time.monotonic() + JOB_MOST
        while time.monotonic() < min(until, self.run.deadline):
            await asyncio.sleep(JOB_POLL)
            polled = await self.send("GET", status_url, "poll", accept="application/json")
            try:
                snap = polled.json()
            except ValueError:
                snap = {}
            if snap.get("state") != "running" and not snap.get("waiting"):
                break
        await self.page("/process")  # process.js reloads the page when the job is done

    async def bulk(self) -> None:
        """Bulk approval from the queue: the clean candidates the page lists (at most ``BULK_MOST``)."""
        page = await self.page("/review", await self.step(self.think()))
        parsed = Forms()
        parsed.feed(page or "")
        key = next((k for k, f in parsed.forms.items() if f["action"] == "/review/bulk-approve"), None)
        if key is None:
            return
        fields = [(f.name, v) for f in parsed.fields if f.owner == key for v in f.values]
        ids = [p for p in fields if p[0] == "ids"][:BULK_MOST]
        if not ids:
            return
        status, answer = await self.post("/review/bulk-approve", [p for p in fields if p[0] != "ids"] + ids,
                                         await self.step(self.think(STEP_THINK)))  # fmt: skip
        if status == 200 and answer.get("redirect"):
            self.run.counts["bulk approvals"] += 1
            self.run.counts["invoices sent to bulk approval"] += len(ids)
            await self.follow(answer["redirect"])

    async def batch(self) -> None:
        """An export batch of the invoices ready (at most ``EXPORT_MOST``): JD Edwards when it is set up (an invoice
        may be ticked on its page), else CSV from then on."""
        run = self.run
        fmt = "jde" if run.jde is not False else "csv"
        fields = export_form(await self.page(f"/exports?fmt={fmt}", await self.step(self.think())))
        if fmt == "jde" and run.jde is None:
            run.jde = any(name == "ids" for name, _ in fields)
            if not run.jde:  # its settings missing, or every invoice held back (no supplier number)
                fmt = "csv"
                fields = export_form(await self.page(f"/exports?fmt={fmt}", await self.step(self.think(STEP_THINK))))
        ids = [p for p in fields if p[0] == "ids"][:EXPORT_MOST]
        if not ids:
            return
        status, answer = await self.post("/exports/create", [p for p in fields if p[0] != "ids"] + ids,
                                         await self.step(self.think(STEP_THINK)))  # fmt: skip
        if status != 200 or not answer.get("redirect"):
            run.counts["export batches refused"] += 1
            if fmt == "jde":
                run.jde = False  # not set up after all: CSV from now on
            return
        run.counts[f"export batches ({fmt})"] += 1
        await self.follow(answer["redirect"])
        made = re.search(r"[?&]made=(\d+)", answer["redirect"])
        if made and fmt != "jde" and self.rng.random() < 0.5:
            await self.send("GET", f"/exports/batches/{made.group(1)}/download", "download",
                            due=await self.step(self.think(STEP_THINK)))  # fmt: skip


def export_form(page: str | None) -> list[tuple[str, str]]:
    """What the Exports page's form sends: its format and the invoices ticked (those not held back)."""
    parsed = Forms()
    parsed.feed(page or "")
    if not (parsed.forms.get("export-form") or {}).get("action"):
        return []
    return form_data(parsed.fields, "export-form")


async def person(run: Run, account: Account, index: int, start: float) -> None:
    """One person from their login to the end of the run, scenario after scenario."""
    me = Person(run, account, index, start)
    weights = SCENARIOS.get(account.role, SCENARIOS["readonly"])
    names, odds = list(weights), list(weights.values())
    try:
        for attempt in range(3):
            try:
                await me.login()
                break
            except (LoginFailed, StepFailed) as exc:
                run.counts["logins refused"] += 1
                run.problems.append(f"{account.username} could not log in: {exc}")
                if attempt == 2:
                    return
                await me.step(30.0)
        while True:
            name = me.rng.choices(names, odds)[0]
            if name != "work":
                me.current = None  # the invoice Next opened is left for another page
            try:
                await getattr(me, name)()
                run.scenarios[name]["done"] += 1
            except StepFailed:
                run.scenarios[name]["failed"] += 1
            except LoggedOut:
                run.scenarios[name]["failed"] += 1
                run.counts["sent to the login page (logged in again)"] += 1
                try:
                    await me.login()
                except (LoginFailed, StepFailed) as exc:
                    run.counts["logins refused"] += 1
                    run.problems.append(f"{account.username} could not log in again: {exc}")
                    return
    except Done:
        pass
    finally:
        await me.close()


# --- The run ------------------------------------------------------------------------------------------------------


@dataclass
class Run:
    base: str
    host: str
    users: int
    duration: float
    ramp: float
    think: float
    timeout: float
    seed: int
    t0: float = 0.0
    deadline: float = 0.0
    samples: list[tuple] = field(default_factory=list)
    counts: collections.Counter = field(default_factory=collections.Counter)
    scenarios: dict[str, collections.Counter] = field(
        default_factory=lambda: collections.defaultdict(collections.Counter)
    )
    lateness: list[float] = field(default_factory=list)
    vendor_words: set[str] = field(default_factory=set)
    problems: list[str] = field(default_factory=list)
    jde: bool | None = None
    stopping: bool = False  # the people still under way are being stopped: their requests are recorded as cut off
    keep_alive: float = KEEP_ALIVE  # seconds a person's connection is kept unused (--keep-alive)

    @property
    def post_headers(self) -> dict[str, str]:
        return {"X-AP-Coder": "1", "Origin": self.base, "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors"}

    def record(self, route: str, kind: str, status: int, seconds: float, service: float, end: float, size: int,
               error: str) -> None:  # fmt: skip
        self.samples.append((route, kind, status, seconds, service, end - self.t0, size, error))


async def drive(run: Run, people: list[Account]) -> None:
    now = time.monotonic()
    run.t0, run.deadline = now, now + run.ramp + run.duration
    spacing = run.ramp / max(1, len(people) - 1) if len(people) > 1 else 0.0
    tasks = [asyncio.create_task(person(run, a, i, now + i * spacing)) for i, a in enumerate(people)]
    _, late = await asyncio.wait(tasks, timeout=run.ramp + run.duration + run.timeout + STOP_GRACE)
    run.stopping = True
    for task in late:  # stuck past every timeout: stopped, said in the report
        task.cancel()
        run.counts["people stopped at the end, a request still under way"] += 1
    await asyncio.gather(*tasks, return_exceptions=True)


# --- Figures --------------------------------------------------------------------------------------------------------


def quantile(ordered: list[float], q: float) -> float | None:
    if not ordered:
        return None
    return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


def stats(values: list[float]) -> dict[str, Any]:
    ordered = sorted(values)
    out = {"n": len(ordered)}
    for name, q in (("p50", 0.5), ("p95", 0.95), ("p99", 0.99)):
        found = quantile(ordered, q)
        out[name] = None if found is None else round(found, 4)
    out["max"] = round(ordered[-1], 4) if ordered else None
    out["mean"] = round(sum(ordered) / len(ordered), 4) if ordered else None
    return out


def waited(sample: tuple, timeout: float, at: int = 3) -> float:
    """A request's seconds in the percentiles (``at`` 3: from when it was due; 4: from when it was sent). One without
    an answer (a timeout, a dropped connection) counts at least ``timeout``: a server that answers fewer requests never
    looks quicker for it."""
    return sample[at] if not sample[7] else max(sample[at], timeout)


def route_table(samples: list[tuple], timeout: float) -> dict[str, dict[str, Any]]:
    by: dict[str, list[tuple]] = collections.defaultdict(list)
    for s in samples:
        by[s[0]].append(s)
    table = {}
    for route, rows in sorted(by.items()):
        answered = [r for r in rows if not r[7]]
        entry = stats([waited(r, timeout) for r in rows])
        entry["service"] = stats([waited(r, timeout, 4) for r in rows])
        entry["kind"] = collections.Counter(r[1] for r in rows).most_common(1)[0][0]
        entry["statuses"] = dict(sorted(collections.Counter(str(r[2]) for r in answered).items()))
        entry["refused"] = sum(1 for r in answered if 400 <= r[2] < 500)
        entry["errors_5xx"] = sum(1 for r in answered if r[2] >= 500)
        entry["no_answer"] = dict(collections.Counter(r[7] for r in rows if r[7]))
        entry["over_30s"] = sum(1 for r in answered if r[3] > SLOW_SECONDS)
        entry["bytes"] = sum(r[6] for r in rows)
        entry["count"] = len(rows)
        table[route] = entry
    return table


def timeline(samples: list[tuple], timeout: float, every: float = 10.0) -> list[dict[str, Any]]:
    buckets: dict[int, list[tuple]] = collections.defaultdict(list)
    for s in samples:
        buckets[int(s[5] // every)].append(s)
    return [{"t": k * every, "requests": len(rows), "per_second": round(len(rows) / every, 2),
             "errors": sum(1 for r in rows if r[7] or r[2] >= 500),
             "p95": quantile(sorted(waited(r, timeout) for r in rows), 0.95)}
            for k, rows in sorted(buckets.items())]  # fmt: skip


def access_logs(folder: Path) -> list[Path]:
    """The web workers' access logs in ``folder`` (``access.log``, ``access-1.log``...) and those rotated from them
    (``access-1.log.1``...)."""
    return sorted(p for p in folder.glob("access*.log*") if ACCESS_LOG.fullmatch(p.name))


def log_sizes(folder: Path) -> dict[int, int]:
    """Each access log's size, by file (its inode): a log rotated in the run is the same file under a new name."""
    return {(info := p.stat()).st_ino: info.st_size for p in access_logs(folder)}


def read_logs(data: Path, out: Path, before: dict[int, int]) -> dict[str, Any]:
    """What the server wrote during the run: its access logs (this run's part: status codes, its own milliseconds
    per route), its output and its error log ("database is locked", tracebacks). ``before``: ``log_sizes`` at the
    start; each file is read from there (rotated meanwhile, under its new name), one made in the run from its start."""
    server_ms: dict[str, list[float]] = collections.defaultdict(list)
    lines = fives = 0
    for path in access_logs(data / "logs"):
        info = path.stat()
        start = before.get(info.st_ino, 0)
        with path.open("rb") as f:
            f.seek(start if start <= info.st_size else 0)  # smaller than it was: not that file (its inode reused)
            for raw in f:
                parts = raw.decode("utf-8", "replace").split()
                if len(parts) < 5 or not parts[-1].isdigit() or not parts[-3].isdigit():
                    continue
                lines += 1
                if int(parts[-3]) >= 500:
                    fives += 1
                server_ms[route_of(parts[-5], parts[-4])].append(int(parts[-1]) / 1000)
    texts = []
    for path in (out / "server.log", out / "user" / "logs" / "dashboard-errors.log"):
        if path.is_file():
            texts.append(path.read_text(encoding="utf-8", errors="replace"))
    text = "\n".join(texts)
    locked = [line.strip()[:300] for line in text.splitlines() if "database is locked" in line]
    return {"access_lines": lines, "access_5xx": fives, "database_locked": len(locked), "locked_lines": locked[:10],
            "tracebacks": text.count("Traceback (most recent call last)"),
            "server_seconds": {r: stats(v) for r, v in sorted(server_ms.items())}}  # fmt: skip


def audit(db: Path, since: str, seed: int = 1) -> dict[str, Any]:
    """The database after the run, the server stopped: no invoice approved, finally approved or exported twice in
    the run, an approval event for every approved invoice, the invoices' summary as a recomputation makes it, and no
    login paused (honest people never trip the lockout). Nothing is changed: the recomputation (of every invoice the
    run touched, and ``SUMMARY_SAMPLE`` others) is rolled back, and rows are compared by their hashes."""
    from ap_coder.core.store import APPROVED, PENDING, Store
    from ap_coder.core.store.summary import SIDE_TABLES

    store = Store(db)
    conn = store._open()
    try:
        per: dict[int, collections.Counter] = collections.defaultdict(collections.Counter)
        for invoice_id, action, n in conn.execute(
            "SELECT invoice_id, action, COUNT(*) FROM events WHERE created_at >= ? AND invoice_id IS NOT NULL AND "
            "action IN (SELECT value FROM json_each(?)) GROUP BY 1, 2", (since, json.dumps(AUDITED))):  # fmt: skip
            per[invoice_id][action] = n
        events = dict(conn.execute("SELECT action, COUNT(*) FROM events WHERE created_at >= ? GROUP BY 1", (since,)))
        twice = sorted(i for i, c in per.items() if c["approved"] - sum(c[u] for u in UNDOING) > 1)
        final_twice = sorted(i for i, c in per.items() if c["final_approved"] > 1)
        exported_twice = sorted(i for i, c in per.items() if c["exported"] > 1)
        unrecorded = [r[0] for r in conn.execute(
            "SELECT i.id FROM invoices i WHERE i.status IN (?, ?) AND NOT EXISTS (SELECT 1 FROM events e WHERE "
            "e.invoice_id = i.id AND e.action = 'approved')", (APPROVED, PENDING))]  # fmt: skip
        touched = {r[0] for r in conn.execute("SELECT DISTINCT invoice_id FROM events WHERE created_at >= ? AND "
                                              "invoice_id IS NOT NULL", (since,))}  # fmt: skip
        others = [r[0] for r in conn.execute("SELECT id FROM invoices") if r[0] not in touched]
        ids = sorted(touched | set(random.Random(seed).sample(others, min(SUMMARY_SAMPLE, len(others)))))
        listed = json.dumps(ids)
        tables = ("invoice_summary", *SIDE_TABLES, "summary_codes", "summary_spend")

        def rows(table: str) -> collections.Counter:
            key = "invoice_id" if table == "summary_spend" else "id"
            sql = f"SELECT * FROM {table} WHERE {key} IN (SELECT value FROM json_each(?))"
            return collections.Counter(hash(tuple(r)) for r in conn.execute(sql, (listed,)))

        conn.execute("BEGIN")
        kept = {t: rows(t) for t in tables}
        conn.execute("INSERT OR IGNORE INTO summary_dirty (id) SELECT value FROM json_each(?)", (listed,))
        store._refresh_summary(conn)  # the summary's own code, as a change makes it
        fresh = {t: rows(t) for t in tables}
        conn.rollback()
        summary = {t: sum(((kept[t] - fresh[t]) + (fresh[t] - kept[t])).values()) for t in tables}
        statuses = dict(conn.execute("SELECT status, COUNT(*) FROM invoices GROUP BY 1"))
    finally:
        conn.close()
    paused = [*store.paused_addresses(), *store.paused_usernames()]
    return {"approved_twice": twice[:20], "final_approved_twice": final_twice[:20],
            "exported_twice": exported_twice[:20], "approved_without_event": unrecorded[:20],
            "summary_checked": len(ids), "summary_rows_differing": {t: n for t, n in summary.items() if n},
            "logins_paused": paused[:20], "events": events, "statuses": statuses,
            "page_reads_waiting": store.page_reads_waiting()}  # fmt: skip


# --- The report, and the gate ---------------------------------------------------------------------------------------


def _pooled(samples: list[tuple], test: Any, timeout: float) -> dict[str, Any]:
    return stats([waited(s, timeout) for s in samples if test(s)])


def build_report(run: Run, cfg: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    samples = run.samples
    table = route_table(samples, run.timeout)
    measured = max(run.duration + run.ramp, 1e-9)
    pages = _pooled(samples, lambda s: s[1] == "page", run.timeout)
    actions = _pooled(samples, lambda s: s[1] == "action" and s[0].startswith("POST")
                      and s[0].endswith(ACTIONS), run.timeout)  # fmt: skip
    fives = sum(r["errors_5xx"] for r in table.values())
    cut_off = sum(r["no_answer"].get(CUT_OFF, 0) for r in table.values())
    no_answer = sum(sum(r["no_answer"].values()) for r in table.values()) - cut_off
    slow = sum(r["over_30s"] for r in table.values())
    targets = {f"pages {k} <= {v} s": pages[k] is not None and pages[k] <= v for k, v in PAGE_TARGETS.items()}
    targets[f"actions p95 <= {ACTION_TARGET_P95} s"] = (
        actions["p95"] is not None and actions["p95"] <= ACTION_TARGET_P95
    )
    return {
        "config": cfg,
        "computer": {"cores": os.cpu_count(), "python": platform.python_version(), "system": platform.platform()},
        "requests": {
            "total": len(samples),
            "seconds": round(max([measured, *(s[5] for s in samples)]), 2),  # from the start to the last answer
            "per_second": round(len(samples) / measured, 2),
            "errors_5xx": fives,
            "no_answer": no_answer,
            "cut_off": cut_off,
            "over_30s": slow,
            "refused_4xx": sum(r["refused"] for r in table.values()),
        },  # fmt: skip
        "pages": pages,
        "actions": actions,
        "targets": targets,
        "lateness": stats(run.lateness),
        "counts": dict(sorted(run.counts.items())),
        "scenarios": {k: dict(v) for k, v in sorted(run.scenarios.items())},
        "problems": run.problems[:50],
        "routes": table,
        "timeline": timeline(samples, run.timeout),
        **extra,
    }


def gate(report: dict[str, Any], baseline: dict[str, Any] | None, factor: float) -> list[str]:
    """Why this run fails: errors, correctness, and (with a baseline) each route whose p95 is over ``factor`` times
    its baseline's (and more than ``REGRESSION_FLOOR`` above it)."""
    problems = []
    r = report["requests"]
    if r["errors_5xx"] or r["no_answer"] or r["cut_off"] or r["over_30s"]:
        problems.append(f"errors: {r['errors_5xx']} 5xx, {r['no_answer']} without an answer, {r['cut_off']} cut off at "
                        f"the end, {r['over_30s']} over {SLOW_SECONDS:.0f} s")  # fmt: skip
    logs = report.get("logs") or {}
    if logs.get("database_locked") or logs.get("access_5xx"):
        problems.append(f"server logs: {logs.get('database_locked', 0)} 'database is locked', "
                        f"{logs.get('access_5xx', 0)} 5xx")  # fmt: skip
    a = report.get("audit") or {}
    for key, text in (("approved_twice", "approved twice"), ("final_approved_twice", "finally approved twice"),
                      ("exported_twice", "exported twice"), ("approved_without_event", "approved without an event"),
                      ("logins_paused", "logins paused")):  # fmt: skip
        if a.get(key):
            problems.append(f"{text}: {a[key]}")
    if a.get("summary_rows_differing"):
        problems.append(f"the summary differs from a recomputation: {a['summary_rows_differing']}")
    if report["counts"].get("uploads refused by the hourly allowance"):
        problems.append("uploads refused by the hourly allowance")
    if report["counts"].get("logins refused"):
        problems.append(f"{report['counts']['logins refused']} logins refused")
    if report["counts"].get(NOT_MADE):
        problems.append(f"{NOT_MADE}: {report['counts'][NOT_MADE]}")
    most = (report.get("config") or {}).get("max_slope")
    # (a process with too few samples, a worker started afresh late, is left out; none left: the run was too short,
    # which passed unjudged, B-2038)
    found = report.get("processes") or []
    judged = [p for p in found if p["process"] != "model" and p.get("rss_slope_late") is not None]
    if most is not None and not judged:
        problems.append("the memory slope can't be judged: the run's second half has under a minute of samples "
                        "(--max-slope wants a run of 2 minutes or more)")  # fmt: skip
    for p in judged if most is not None else []:
        if p["rss_slope_late"] > most:
            problems.append(f"{p['process']}'s memory grew {p['rss_slope_late']:.1f} MB a 10 minutes over the run's "
                            f"second half (more than {most:g})")  # fmt: skip
    for route, base in ((baseline or {}).get("routes") or {}).items():
        now = report["routes"].get(route)
        if not now or now["n"] < MIN_COUNT or base.get("n", 0) < MIN_COUNT or not base.get("p95"):
            continue
        if now["p95"] > factor * base["p95"] and now["p95"] - base["p95"] > REGRESSION_FLOOR:
            problems.append(f"{route}: p95 {now['p95']:.3f} s, baseline {base['p95']:.3f} s "
                            f"({now['p95'] / base['p95']:.1f}x)")  # fmt: skip
    return problems


def _mb(value: float | None) -> str:
    return "-" if value is None else f"{value:+.1f}"


def _s(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value * 1000:.0f} ms" if value < 1 else f"{value:.2f} s"


def markdown(report: dict[str, Any]) -> str:
    c, pc, r = report["config"], report["computer"], report["requests"]
    h, logs, a = report.get("harness") or {}, report.get("logs") or {}, report.get("audit") or {}
    pages, actions = report["pages"], report["actions"]
    out = [
        f"# Load test: {c['users']} people for {c['duration']:.0f} s on {c['workers']} worker(s)",
        "",
        f"{report['started']}; ramp {c['ramp']:.0f} s, think {c['think']:.1f} s (median), connections kept "
        f"{c['keep_alive']:.0f} s unused; {c['invoices']} invoices in {c['data']}; models: {c['model']}; people reach "
        f"the server at {c['host']}. {pc['cores']} cores, Python {pc['python']}, {pc['system']}.",
        "",
        "## Summary",
        f"- Requests: {r['total']} ({r['per_second']}/s); 5xx {r['errors_5xx']}, without an answer {r['no_answer']}, "
        f"cut off at the end {r['cut_off']}, over 30 s {r['over_30s']}; refused (4xx) {r['refused_4xx']}.",
        f"- Pages: p50 {_s(pages['p50'])}, p95 {_s(pages['p95'])}, p99 {_s(pages['p99'])} ({pages['n']}); actions "
        f"(check, approve, park, reject, final approval): p50 {_s(actions['p50'])}, p95 {_s(actions['p95'])} "
        f"({actions['n']}). Timed from when each step was due (scheduled start).",
        "- Targets: " + ", ".join(f"{k} {'met' if v else 'NOT met'}" for k, v in report["targets"].items()) + ".",
        f"- Behind schedule: p50 {_s(report['lateness']['p50'])}, p95 {_s(report['lateness']['p95'])}.",
        f"- Harness CPU: {h.get('process_cores')} cores (its event loop {h.get('loop_cores')}): "
        + ("well under one core, so the figures are the server's." if (h.get("process_cores") or 0) < 0.5
           else "**high: the harness may hold the figures back**.")
        + f" Server started in {c.get('started_in')} s; its stop took {c.get('stopped_in')} s ({c.get('stop_how')}).",
        f"- Server logs: {logs.get('database_locked', 0)} 'database is locked', {logs.get('access_5xx', 0)} 5xx of "
        f"{logs.get('access_lines', 0)} lines, {logs.get('tracebacks', 0)} tracebacks.",
        "",
        "## Gate",
    ]  # fmt: skip
    gate_ = report.get("gate") or {}
    out += [f"- {p}" for p in gate_.get("problems", [])] or ["- Passed."]
    out += ["", "## Routes", "",
            "| route | n | p50 | p95 | p99 | max | server p95 | statuses | refused | errors |",
            "|---|---:|---:|---:|---:|---:|---:|---|---:|---:|"]  # fmt: skip
    server = logs.get("server_seconds") or {}
    for route, e in sorted(report["routes"].items(), key=lambda kv: -kv[1]["count"]):
        statuses = ", ".join(f"{k}: {v}" for k, v in e["statuses"].items())
        errors = e["errors_5xx"] + sum(e["no_answer"].values()) + e["over_30s"]
        own = (server.get(route) or {}).get("p95")
        out.append(f"| {route} | {e['count']} | {_s(e['p50'])} | {_s(e['p95'])} | {_s(e['p99'])} | {_s(e['max'])} | "
                   f"{_s(own)} | {statuses} | {e['refused']} | {errors} |")  # fmt: skip
    out += ["", "## Server processes", "", "| process | CPU s | mean cores | peak cores | RSS MB first → last (peak) | "
            "RSS MB a 10 min: whole run, second half |", "|---|---:|---:|---:|---|---|"]  # fmt: skip
    for p in report.get("processes") or []:
        out.append(f"| {p['process']} | {p['cpu_seconds']} | {p['mean_cores']} | {p['peak_cores']} | "
                   f"{p['rss_mb_first']} → {p['rss_mb_last']} ({p['rss_mb_peak']}) | "
                   f"{_mb(p.get('rss_slope'))}, {_mb(p.get('rss_slope_late'))} |")  # fmt: skip
    out += [
        "",
        "## Over time (a row a minute)",
        "",
        "| from | requests/s | errors | p95 (worst 10 s) |",
        "|---:|---:|---:|---:|",
    ]
    minutes: dict[int, list[dict[str, Any]]] = collections.defaultdict(list)
    for bucket in report["timeline"]:
        minutes[int(bucket["t"] // 60)].append(bucket)
    for minute, buckets in sorted(minutes.items()):
        covered = min(60.0, r["seconds"] - 60 * minute)  # the seconds the minute covers: 60, or the last one's part
        rate = sum(b["requests"] for b in buckets) / max(1.0, covered)
        worst = max((b["p95"] for b in buckets if b["p95"] is not None), default=None)
        out.append(f"| {minute} min | {rate:.1f} | {sum(b['errors'] for b in buckets)} | {_s(worst)} |")
    out += ["", "## Correctness after the run", ""]
    if a:
        out += [f"- Approved twice: {a['approved_twice'] or 'none'}; finally approved twice: "
                f"{a['final_approved_twice'] or 'none'}; exported twice: {a['exported_twice'] or 'none'}.",
                f"- Approved without an approval event: {a['approved_without_event'] or 'none'}.",
                f"- Summary against a recomputation ({a['summary_checked']} invoices: every one the run touched, and a "
                f"sample): {a['summary_rows_differing'] or 'the same'}.",
                f"- Logins paused: {a['logins_paused'] or 'none'}.",
                f"- Events in the run: {a['events']}; invoices now: {a['statuses']}; waiting for OvisOCR2 at the end: "
                f"{a['page_reads_waiting']}."]  # fmt: skip
    out += ["", "## What people did", ""]
    out += [f"- {k}: {v}" for k, v in report["counts"].items()]
    out += [
        f"- scenario {k}: {v.get('done', 0)} done, {v.get('failed', 0)} stopped by an error"
        for k, v in report["scenarios"].items()
    ]
    if report["problems"]:
        out += ["", "## Problems", ""] + [f"- {p}" for p in report["problems"][:20]]
    return "\n".join(out) + "\n"


# --- The command ----------------------------------------------------------------------------------------------------


def add_parser(sub: Any) -> argparse.ArgumentParser:
    p = sub.add_parser("load", help="many people at once on the real server (server mode, several workers)")
    p.add_argument("--users", type=int, default=10, help="people at once (default 10)")
    p.add_argument("--duration", type=float, default=60.0, help="seconds of work after the ramp (default 60)")
    p.add_argument("--ramp", type=float, default=10.0, help="seconds over which the people start (default 10)")
    p.add_argument("--think", type=float, default=10.0, help="think time between scenarios, median s (default 10)")
    p.add_argument("--workers", type=int, default=2, help="the server's web worker processes (default 2)")
    p.add_argument("--data", default=None, help="the data folder (used as it is; seeded first if it has no database)")
    p.add_argument("--users-file", default=None, help="its accounts (default: DATA/users.json)")
    p.add_argument("--seed-invoices", type=int, default=2000, help="invoices in a folder seeded here (default 2000)")
    p.add_argument("--keep-data", action="store_true", help="keep the folder seeded in OUT/data")
    p.add_argument("--model-url", default=None, help="a model server already running (default: a stand-in started)")
    p.add_argument("--model-latency", default="1,4", help="the stand-in's seconds an answer, low,high (default 1,4)")
    p.add_argument("--model-slots", type=int, default=2, help="answers the stand-in works on at once (default 2)")
    p.add_argument("--host", default=None, help="the address people reach the server at (default: its network one)")
    p.add_argument("--timeout", type=float, default=120.0, help="seconds a request may take at most (default 120)")
    p.add_argument("--keep-alive", type=float, default=KEEP_ALIVE,
                   help="seconds a person keeps a connection unused (default 5; 300 as a browser does)")  # fmt: skip
    p.add_argument("--seed", type=int, default=1, help="random seed for the people's choices")
    p.add_argument("--out", default="bench_load", help="output folder (default bench_load)")
    p.add_argument("--baseline", default=None, help="an earlier report.json: fail on a route's p95 over it")
    p.add_argument("--max-regression", type=float, default=1.5, help="with --baseline: the p95 ratio that fails")
    p.add_argument("--sample-every", type=float, default=SAMPLE_EVERY,
                   help="seconds between two looks at each server process's CPU and memory (default 2; 10 for an "
                        "hour's run)")  # fmt: skip
    p.add_argument("--max-slope", type=float, default=None,
                   help="fail when a server process's memory grew more than this many MB a 10 minutes over the "
                        "run's second half (its slope is always reported)")  # fmt: skip
    p.add_argument("--quick", action="store_true", help="a small run to check a change (3 people, 20 s, ~1 min)")
    p.set_defaults(load_defaults={key: p.get_default(key) for key in QUICK})
    return p


def main_load(a: argparse.Namespace) -> int:
    if a.quick:  # the small sizes, unless given
        for key, value in QUICK.items():
            if getattr(a, key) == a.load_defaults[key]:
                setattr(a, key, value)
    if a.users < 1 or a.duration <= 0 or a.workers < 1 or a.sample_every <= 0:
        print("--users, --duration, --workers and --sample-every must be positive", file=sys.stderr)
        return 2
    try:
        report = run_load(a)
    except (RuntimeError, OSError) as exc:
        print(f"the load test could not run: {exc}", file=sys.stderr)
        return 2
    r, p, ac, cores = report["requests"], report["pages"], report["actions"], report["harness"]["process_cores"]
    print(f"{a.users} people, {a.duration:.0f} s, {a.workers} worker(s): {r['total']} requests ({r['per_second']}/s), "
          f"pages p50 {_s(p['p50'])} p95 {_s(p['p95'])}, actions p95 {_s(ac['p95'])}; 5xx {r['errors_5xx']}, no answer "
          f"{r['no_answer']}, cut off {r['cut_off']}, over 30 s {r['over_30s']}; harness {cores} cores")  # fmt: skip
    for problem in report["gate"]["problems"]:
        print(f"FAIL: {problem}")
    print(f"report: {Path(a.out) / 'report.md'}")
    return 0 if report["gate"]["passed"] else 1


def run_load(a: argparse.Namespace) -> dict[str, Any]:
    out = Path(a.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    seeded_here = a.data is None
    data = (out / "data") if seeded_here else Path(a.data).resolve()
    if seeded_here and data.exists():
        shutil.rmtree(data)
    users_file = Path(a.users_file).resolve() if a.users_file else data / "users.json"
    saved = {k: os.environ.get(k) for k in ("AP_PRIVATE_DIR", "AP_USER_SETTINGS")}
    os.environ.update({"AP_PRIVATE_DIR": str(data), "AP_USER_SETTINGS": str(out / "user" / "settings.json")})
    model = server = sampler = None
    try:
        if not (data / "ap_coder.db").exists():
            t0 = time.monotonic()
            users_file = seed_folder(data, a.seed_invoices, max(a.users, 20), a.seed)
            print(f"seeded {a.seed_invoices} invoices in {data} in {time.monotonic() - t0:.0f} s", file=sys.stderr)
        people = choose_people(read_accounts(users_file), a.users)
        from ap_coder.core.store import Store

        invoices = sum(Store(data / "ap_coder.db").status_counts().values())
        host = a.host or network_address()
        if a.model_url:
            model_url, which = a.model_url, f"{a.model_url} (given)"
        else:
            lo, _, hi = a.model_latency.partition(",")
            latency = (float(lo), float(hi or lo))
            model_url, model, which = start_model(host, latency, a.model_slots, out / "model.log")
            which = f"{which}, at {model_url}"
        port = free_port()
        server = Server(data, out, a.workers, model_url, host, port)
        server.start()
        roots = {"server": server.proc.pid, **({"model": model.pid} if model is not None else {})}
        run = Run(f"http://{host}:{port}", host, a.users, a.duration, min(a.ramp, a.duration), a.think, a.timeout,
                  a.seed, keep_alive=a.keep_alive)  # fmt: skip
        started = dt.datetime.now().isoformat(timespec="seconds")
        sampler = Sampler(roots, time.monotonic(), a.sample_every)
        sampler.start()
        cpu0, wall0 = time.process_time(), time.monotonic()
        loop_cpu = asyncio.run(_timed(run, people))
        harness = {"process_cores": round((time.process_time() - cpu0) / (time.monotonic() - wall0), 3),
                   "loop_cores": round(loop_cpu / (time.monotonic() - wall0), 3)}  # fmt: skip
        server.stop()
        sampler.stop()
        logs = read_logs(data, out, server.logs_before)
        checked = audit(data / "ap_coder.db", started, a.seed)
        cfg = {"users": a.users, "duration": a.duration, "ramp": run.ramp, "think": a.think, "workers": a.workers,
               "data": str(data) if not seeded_here else "a folder seeded for the run", "invoices": invoices,
               "seed_invoices": a.seed_invoices if seeded_here else None, "model": which, "host": host,
               "timeout": a.timeout, "seed": a.seed, "started_in": server.started_in, "stopped_in": server.stopped_in,
               "stop_how": server.stop_how, "jde": run.jde, "keep_alive": a.keep_alive,
               "sample_every": a.sample_every, "max_slope": a.max_slope}  # fmt: skip
        report = build_report(run, cfg, {"started": started, "harness": harness, "logs": logs, "audit": checked,
                                         "processes": sampler.summary()})  # fmt: skip
        baseline = json.loads(Path(a.baseline).read_text(encoding="utf-8")) if a.baseline else None
        problems = gate(report, baseline, a.max_regression)
        report["gate"] = {"passed": not problems, "problems": problems, "baseline": a.baseline,
                          "max_regression": a.max_regression if a.baseline else None}  # fmt: skip
        (out / "report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
        (out / "report.md").write_text(markdown(report), encoding="utf-8")
        return report
    finally:
        if server is not None:
            server.stop()
        if sampler is not None and sampler.is_alive():
            sampler.stop()
        if model is not None:
            stop_process(model)
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if seeded_here and not a.keep_data and data.exists():
            shutil.rmtree(data, ignore_errors=True)


async def _timed(run: Run, people: list[Account]) -> float:
    """The run, and the CPU seconds its event loop's thread used."""
    start = time.thread_time()
    await drive(run, people)
    return time.thread_time() - start
