"""A server day (``python -m ap_coder.bench day``): a morning's drop of invoices through a real company server, read
against the stand-in model server (``fake_model``), measured.

``--n`` invoices (the bench's own, ``--scanned`` of them scans) land at once on ``serve --server --workers W
--insecure-http`` started on a fresh data folder (``OUT/data``, deleted afterwards unless ``--keep``). They are
delivered as production does: copied into the invoices folder and picked up from the Process page (``--deliver
folder``, how a big batch comes in: the upload's hourly allowance sends one there), or uploaded by an Admin in
batches of ``--batch`` (``--deliver upload``, the import queue). The server's background process reads them with the
page reader and the AI reviewer against the fake model: ``--latency`` seconds a page, ``--chat-latency`` a chat
answer, ``--slots`` answered at once, every wait multiplied by ``--time-scale`` so a day runs in minutes (the app's
model timeouts are scaled with it, at least 30 s, unless ``--no-scale-timeouts``). Meanwhile a light probe asks for
the review queue (``GET /review``) every ``--probe-every`` seconds.

Measured: arrival (invoices an hour into the review queue), the page reader's backlog over time and how long it took
to drain, the AI reviewer's throughput, every failure, each process's CPU (Linux) and the probe's latency, idle and
during the drop. ``--drill`` makes the model machine misbehave once ``--drill-at`` pages were read: ``kill`` (down
for ``--drill-for`` seconds), ``hang`` (nothing answered, then LM Studio restarted), ``slow`` (``--slow-factor``
times slower), ``swap`` (another chat model) or ``swap-reader`` (another OvisOCR2 build): the reads lost or done
twice, and how long recovery took. Failover to a backup model server is reported as not supported (there is none).

The report (``OUT/report.json``, ``OUT/report.md``) gives each figure as measured (real seconds, the model's answers
``--time-scale`` long) and at full latency, an estimate: the model's share of each stage stretched back by
1 / time-scale, the work on this computer as measured. The app's own timers (a minute's pause after a failed read, 30
s when nothing waits) are not scaled. The database is read through a read-only connection; nothing is written."""

from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import platform
import shutil
import sqlite3
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from .fake_model import READER, FakeModel, page_key, seconds_range
from .harness import default_workers, prepare_cases

CHAT_MODEL = "gemma-4-12b-it"  # stage 2's chat model (Gemma 4); the swap drill brings Qwen 3.5
SWAP_CHAT = "qwen/qwen3.5-9b"
SWAP_READER = "ath-maas_ovisocr2@q4_k_m"  # another build of OvisOCR2: its self-test runs before it reads
DRILLS = ("none", "kill", "hang", "slow", "swap", "swap-reader")
DELIVERIES = ("folder", "upload")
REVIEWER_SCOPES = ("all", "touchless", "off")
ADMIN = "bench.admin"
PAGE = {"X-AP-Coder": "1"}  # what AP Coder's own page sends with every action
WORKING_DAY_HOURS = 10.0  # the model machine's day, for its capacity a day
FAILOVER = ("not supported: AP Coder has one address for each model server (AP_LLM_BASE_URL, AP_PAGE_READER_BASE_URL)"
            " and nothing switches to a backup on its own; a person changes the address")  # fmt: skip
QUALIFIED = ("not supported: a new chat model is used as soon as LM Studio lists it, with no test of its own before "
             "its reviews count")  # fmt: skip


def add_arguments(p: argparse.ArgumentParser) -> None:
    p.add_argument("--n", type=int, default=1250, help="invoices in the drop (default 1250, a day at 25,000 a month)")
    p.add_argument("--seed", type=int, default=1, help="random seed; the same seed gives the same invoices")
    p.add_argument("--scanned", type=float, default=0.5, help="share of invoices made as scans (default 0.5)")
    p.add_argument("--out", default="bench_day", help="output folder (default bench_day)")
    p.add_argument("--workers", type=int, default=4, help="the server's web workers (default 4)")
    p.add_argument("--gen-workers", type=int, default=default_workers(), help="processes making the invoices")
    p.add_argument(
        "--deliver",
        choices=DELIVERIES,
        default="folder",
        help="folder: copied into the invoices folder and picked up; upload: an Admin's uploads",
    )
    p.add_argument("--batch", type=int, default=25, help="files an upload (--deliver upload; at most 200)")
    p.add_argument("--latency", default="8-20", help="seconds OvisOCR2 takes a page, lo-hi (default 8-20)")
    p.add_argument("--chat-latency", default=None, help="seconds a chat answer takes (default --latency)")
    p.add_argument("--slots", type=int, default=2, help="requests the model machine answers at once (default 2)")
    p.add_argument("--llm-parallel", type=int, default=None, help="AP_LLM_PARALLEL (default --slots)")
    p.add_argument("--time-scale", type=float, default=0.05, help="the fake model's waits times this (default 0.05)")
    p.add_argument("--no-scale-timeouts", action="store_true", help="keep the app's model timeouts (600 / 1,200 s)")
    p.add_argument("--fail-rate", type=float, default=0.0, help="share of model answers that are errors")
    p.add_argument(
        "--reviewer",
        choices=REVIEWER_SCOPES,
        default="all",
        help="what the AI reviewer reviews: all (to measure it), touchless (the server's own), off",
    )
    p.add_argument("--drill", choices=DRILLS, default="none", help="the model machine misbehaves part way")
    p.add_argument("--drill-at", type=int, default=3, help="pages read before the drill starts (default 3)")
    p.add_argument("--drill-for", type=float, default=30.0, help="seconds the kill, hang or slow lasts (default 30)")
    p.add_argument("--slow-factor", type=float, default=5.0, help="how much slower --drill slow is (default 5)")
    p.add_argument("--probe-every", type=float, default=3.0, help="seconds between GET /review probes (default 3)")
    p.add_argument("--sample-every", type=float, default=2.0, help="seconds between samples (default 2)")
    p.add_argument("--timeout", type=float, default=120.0, help="minutes before the run stops anyway (default 120)")
    p.add_argument("--self-test-minutes", type=float, default=10.0,
                   help="minutes to wait for the page reader's self-test before the drop (default 10)")  # fmt: skip
    p.add_argument("--keep", action="store_true", help="keep the data folder (OUT/data) afterwards")


def run_command(a: argparse.Namespace) -> int:
    report = run_day(a)
    r, full = report["results"], report["at_full_latency"]
    print(f"{r['arrived']}/{report['config']['n']} invoices in the review queue in {_hms(r['arrival_seconds'])} "
          f"({_num(r['arrival_per_hour'])} an hour); page reader: {r['reads_done']} read, drained in "
          f"{_hms(r['drain_seconds'])} ({_hms(full['drain_seconds'])} at full latency); reviewer "
          f"{r['reviews_done']} done; GET /review p95 {_ms(r['probe_p95_drop'])} during the drop vs "
          f"{_ms(r['probe_p95_idle'])} idle; failures {r['failures']}")  # fmt: skip
    if report.get("drill"):
        d = report["drill"]
        print(f"drill {d['kind']}: reads lost {d['reads_lost']}, done twice {d['pages_read_twice']}, recovered in "
              f"{_hms(d['recovery_seconds'])}; failover: {d['failover']}")  # fmt: skip
    print(f"report: {Path(a.out) / 'report.md'}")
    return 0 if not report["timed_out"] and not report.get("problem") else 1


# --- The run ----------------------------------------------------------------------------------------------------


def run_day(a: argparse.Namespace) -> dict[str, Any]:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    cases = prepare_cases(out / "cases", a.n, a.seed, a.scanned, a.gen_workers, False)
    data = out / "data"
    shutil.rmtree(data, ignore_errors=True)
    (data / "invoices").mkdir(parents=True)
    parallel = a.llm_parallel or a.slots
    password, secret = _seed(data, a.reviewer)
    latency = seconds_range(a.latency)
    chat_latency = seconds_range(a.chat_latency or a.latency)
    fake = FakeModel(latency=latency, chat_latency=chat_latency, slots=a.slots, fail_rate=a.fail_rate,
                     models=[CHAT_MODEL, READER], seed=a.seed, time_scale=a.time_scale).start()  # fmt: skip
    test_pages = _teach_self_test(fake)
    server = Server(data, out / "server.log", a.workers, _server_env(fake, parallel, a))
    report: dict[str, Any] = {}
    run: Run | None = None
    try:
        server.start()
        client = Client(server.base)
        client.login(ADMIN, password, secret)
        run = Run(a, cases, data, fake, server, client, test_pages)
        run.wait_for_self_test(a.self_test_minutes * 60)  # on a server it ran when the model was first seen
        for _ in range(4 * a.workers):  # each worker's first page is slow: not the idle figure
            client.probe()
        idle = []
        for _ in range(20):
            time.sleep(0.2)
            idle.append(client.probe())
        run.drop()
        run.watch()
        report = run.report(idle)
    finally:
        server.stop()
        fake.stop()
        if run is not None:
            run.db.close()  # Windows deletes no file still open: the data folder stayed behind (B-1681)
        if (data / "logs").is_dir():  # the server's own logs outlive the data folder: they say what went wrong
            shutil.copytree(data / "logs", out / "server-logs", dirs_exist_ok=True)
        if not a.keep:
            shutil.rmtree(data, ignore_errors=True)
    report["config"].update({"latency": latency, "chat_latency": chat_latency, "llm_parallel": parallel})
    (out / "report.json").write_text(json.dumps(report, indent=1, default=str) + "\n", encoding="utf-8")
    (out / "report.md").write_text(markdown(report), encoding="utf-8")
    return report


def _seed(data: Path, reviewer: str) -> tuple[str, str]:
    """The fresh data folder: the sample GL accounts and tax set-up, the AI reviewer's scope, and an Admin whose
    authenticator is set up at the first login (its password, its secret)."""
    from ap_coder.ai import verifier
    from ap_coder.core.store import Store, load_sample_setup
    from ap_coder.core.store.users import ADMIN as ADMIN_ROLE
    from ap_coder.core.store.users import temporary_password

    old = os.environ.get("AP_PRIVATE_DIR")
    os.environ["AP_PRIVATE_DIR"] = str(data)  # the authenticator's key beside this database, not AP's
    try:
        store = Store(data / "ap_coder.db")
        load_sample_setup(store, cost_centers=False)
        scope = reviewer if reviewer != "off" else "touchless"
        verifier.save_settings(store, on=reviewer != "off", rounds=verifier.DEFAULT_ROUNDS,
                               minutes=verifier.DEFAULT_MINUTES, scope=scope)  # fmt: skip
        password = temporary_password() + "-bench"
        user = store.add_user(ADMIN, password, ADMIN_ROLE, "Bench Admin", must_change=False)
        return password, store.authenticator_secret(user)
    finally:
        if old is None:
            os.environ.pop("AP_PRIVATE_DIR", None)
        else:
            os.environ["AP_PRIVATE_DIR"] = old


def _teach_self_test(fake: FakeModel) -> set[str]:
    """The page reader's self-test answered as printed (the sample's own text): it passes, and reading starts. The
    test pages' keys, so they are not counted as invoices' pages."""
    import pymupdf

    from ap_coder.ai import page_reader

    pngs = page_reader.test_pages(page_reader.OVIS)
    with pymupdf.open(page_reader.TEST_SAMPLE) as doc:
        texts = [page.get_text() for page in doc]
    fake.learn_pages(pngs, texts[: len(pngs)])
    return {page_key(png) for png in pngs}


def _server_env(fake: FakeModel, parallel: int, a: argparse.Namespace) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("AP_")}  # nothing of this computer's AP Coder
    env.update({"BROWSER": "none", "AP_ALLOWED_HOSTS": "127.0.0.1", "AP_LLM_BASE_URL": fake.base_url,
                "AP_LLM_PARALLEL": str(parallel), "PYTHONUNBUFFERED": "1",
                "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
                "AP_STACK_DUMP_SECONDS": "60"})  # fmt: skip  # where the background process waits, if it seems stuck
    if not a.no_scale_timeouts and a.time_scale < 1:
        env["AP_LLM_TIMEOUT_SECONDS"] = str(round(max(30.0, 600 * a.time_scale)))
        env["AP_PAGE_READER_TIMEOUT_SECONDS"] = str(round(max(30.0, 1200 * a.time_scale)))
    return env


class Run:
    """One drop, watched until every queue is empty (or ``--timeout``)."""

    def __init__(self, a: argparse.Namespace, cases: list[Any], data: Path, fake: FakeModel, server: Server,
                 client: Client, test_pages: set[str]) -> None:  # fmt: skip
        self.a, self.cases, self.data, self.fake, self.server, self.client = a, cases, data, fake, server, client
        self.test_pages = test_pages
        self.db = Reader(data / "ap_coder.db")
        self.samples: list[dict[str, Any]] = []
        self.tries: dict[int, int] = {}  # the most times each invoice's read was cut off (a read done starts at 0)
        self.probes: list[tuple[float, float, int]] = []
        self.deliveries: list[dict[str, Any]] = []
        self.cpu = Cpu(server)
        self.cpu_before: dict[int, float] = {}
        self.drill = Drill(a, fake)
        self.t0 = self.wall0 = 0.0
        self.fake_t0 = 0.0
        self.timed_out = False
        self.problem = ""
        self._stop = threading.Event()

    def since(self) -> float:
        return time.monotonic() - self.t0

    def wait_for_self_test(self, timeout: float = 600.0) -> None:
        """Until the page reader passed its self-test (``problem`` says so when it failed, or took too long)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            test = self.db.self_test()
            if test is not None and test.get("fields_total"):
                if not test.get("ok"):
                    self.problem = f"the page reader failed its self-test before the drop: {test.get('problem')}"
                return
            time.sleep(1.0)
        self.problem = f"the page reader's self-test did not end within {timeout / 60:g} minutes of the start"

    def drop(self) -> None:
        """Every invoice at once, as the morning's batch."""
        self.cpu.sample()
        self.cpu_before = self.cpu.totals()
        self.t0, self.wall0 = time.monotonic(), time.time()
        self.fake_t0 = self.fake.stats()["uptime"]
        threading.Thread(target=self._probe, name="bench-probe", daemon=True).start()
        if self.a.deliver == "folder":
            for case in self.cases:  # an Admin copies the batch in, then presses Process on the Process page
                shutil.copy(case.path, self.data / "invoices" / case.path.name)
            status, body, seconds = self.client.post_form("/process/folder", {})
            self.deliveries.append({"t": round(self.since(), 3), "files": len(self.cases), "status": status,
                                    "seconds": round(seconds, 3), "answer": _json(body)})  # fmt: skip
            return
        batch = max(1, min(200, self.a.batch))
        for first in range(0, len(self.cases), batch):
            files = [c.path for c in self.cases[first : first + batch]]
            status, body, seconds = self.client.upload(files)
            self.deliveries.append({"t": round(self.since(), 3), "files": len(files), "status": status,
                                    "seconds": round(seconds, 3), "answer": _json(body)})  # fmt: skip

    def _probe(self) -> None:
        while not self._stop.wait(self.a.probe_every):
            t = self.since()
            seconds, status = self.client.probe()
            self.probes.append((round(t, 3), round(seconds, 4), status))

    def watch(self) -> None:
        deadline = self.t0 + self.a.timeout * 60
        settled = 0
        while True:
            time.sleep(self.a.sample_every)
            sample = self.sample()
            self.drill.check(self.since(), self._pages_read())
            if self.server.proc.poll() is not None:
                self.problem = f"the server stopped (exit code {self.server.proc.returncode}): see server.log"
                break
            test = self.db.self_test()
            if test is not None and not test.get("ok") and test.get("model") == self.drill.reader:
                self.problem = f"the page reader failed its self-test: {test.get('problem') or test.get('rows')}"
                break
            open_work = sample["reads_open"] + (sample["reviews_open"] if self.a.reviewer != "off" else 0)
            arrived = sample["invoices"] >= len(self.cases) or (
                sample["import_idle"] and sample["t"] > 30 and not sample["import_waiting"])  # fmt: skip
            # A drill not started by the end never starts (fewer pages than --drill-at): its report says so.
            drill_over = self.drill.over() or self.drill.started is None
            settled = settled + 1 if arrived and not open_work and drill_over else 0
            if settled >= 3:
                break
            if time.monotonic() > deadline:
                self.timed_out = True
                break
        self._stop.set()

    def sample(self) -> dict[str, Any]:
        counts = self.db.counts()
        for invoice, tries in counts["tries"].items():
            self.tries[invoice] = max(tries, self.tries.get(invoice, 0))
        status, body, _ = self.client.request("GET", "/process/status")
        job = _json(body) if status == 200 else {}
        s = self.fake.stats()
        self.cpu.sample()
        reads, reviews = counts["page_reads"], counts["reviews"]
        sample = {
            "t": round(self.since(), 2), "invoices": counts["invoices"], "page_reads": reads, "reviews": reviews,
            "reads_open": reads.get("waiting", 0) + reads.get("reading", 0),
            "reviews_open": reviews.get("waiting", 0) + reviews.get("verifying", 0),
            "import_idle": job.get("state") != "running", "import_waiting": int(job.get("waiting") or 0),
            "import_done": job.get("done"), "model": {k: s[k] for k in ("active", "waiting", "served", "failed",
                                                                       "hung", "dropped")},
        }  # fmt: skip
        self.samples.append(sample)
        return sample

    def _pages_read(self) -> int:
        return sum(n for key, n in self.fake.page_answers().items() if key not in self.test_pages)

    # --- The report

    def report(self, idle: list[tuple[float, int]]) -> dict[str, Any]:
        from ap_coder.ai.pipeline import ARRIVAL_ENV, ARRIVAL_SECONDS
        from ap_coder.web.server import capture_processes

        wall = self.since()
        self.cpu.sample()
        invoices, reads, reviews = self.db.invoices(), self.db.page_reads(), self.db.reviews()
        test = self.db.self_test()
        history = self.fake.history()
        stats = self.fake.stats()
        scale = self.a.time_scale

        def at(stamp: str | None) -> float | None:
            if not stamp:
                return None
            with contextlib.suppress(ValueError):
                return round(dt.datetime.fromisoformat(stamp).timestamp() - self.wall0, 1)
            return None

        kinds = {c.path.name: ("scan" if c.scanned else "digital") for c in self.cases}
        arrivals = sorted(t for t in (at(i["created_at"]) for i in invoices) if t is not None)
        arrival_end = arrivals[-1] if arrivals else None
        failed_imports = [f"{i['file_name']}: {i['error']}" for i in invoices if i.get("error")]
        done = [r for r in reads if r["status"] == "done"]
        ends = sorted(t for t in (at(r["updated_at"]) for r in done) if t is not None)
        read_failed = [r for r in reads if r["status"] == "failed"]
        read_open = [r for r in reads if r["status"] in ("waiting", "reading")]
        drain = max([*ends, arrival_end or 0.0]) if reads else arrival_end
        reviews_done = [v for v in reviews if v["status"] == "done"]
        review_ends = sorted(t for t in (at(v["updated_at"]) for v in reviews_done) if t is not None)
        # The AI reviewer starved: how many of its reviews ended while the page reader still had invoices in line, and
        # how long each invoice waited for its review once the page reader had read it.
        read_spans = [(at(r["created_at"]), at(r["updated_at"])) for r in done]
        while_reading = sum(1 for t in review_ends if any(a is not None and b is not None and a <= t < b
                                                          for a, b in read_spans))  # fmt: skip
        read_end = {r["invoice_id"]: at(r["updated_at"]) for r in done}
        review_waits = [at(v["updated_at"]) - read_end[v["invoice_id"]] for v in reviews_done
                        if read_end.get(v["invoice_id"]) is not None and at(v["updated_at"]) is not None]  # fmt: skip
        probe_drop = [s for t, s, code in self.probes if code == 200 and t <= (drain or wall)]
        probe_errors = [(t, code) for t, _, code in self.probes if code != 200]
        log = self.server.log_counts()
        fake_t = self.fake_t0

        def model_seconds(kinds_: tuple[str, ...], before: float | None = None, service: bool = False) -> float:
            """The model's seconds answering these kinds since the drop (``before`` it, when given), waits for a slot
            included or (``service``) not: answered requests only (a hang or a drop is no model time to stretch)."""
            return sum(
                h["seconds"] - (h["wait"] if service else 0.0)
                for h in history
                if h["kind"] in kinds_
                and h["outcome"] in ("served", "failed")
                and h["t"] >= fake_t
                and (before is None or h["t"] - fake_t <= before)
            )

        stretch = (1 / scale - 1) if 0 < scale < 1 else 0.0
        parallel = self.a.llm_parallel or self.a.slots
        cpu = self.cpu.report(self.cpu_before, wall)
        # The arrival's chat answers: each capture process waits for its own, side by side (D-168), and at most the
        # arrival's limit, its wait for a chat slot included (``pipeline.ARRIVAL_SECONDS``): at full latency each one
        # takes its time stretched back, cut at that limit, the capture processes waiting at once.
        limit = max(0.0, float(self.server.settings.get(ARRIVAL_ENV) or ARRIVAL_SECONDS)) or float("inf")  # 0: none
        capture = (cpu.get("processes") or {}).get("capture", {}).get("processes") or capture_processes()
        arrival_model = sum(
            min(h["seconds"] / scale, limit) - h["seconds"]
            for h in history
            if h["kind"] == "suggest" and h["outcome"] in ("served", "failed") and h["t"] >= fake_t
            and (arrival_end is None or h["t"] - fake_t <= arrival_end)
        ) / max(1, capture) if stretch else 0.0  # fmt: skip
        # The background readers' answering, as many at once as both the model's slots and the readers allow (a wait
        # in line is another request's answering, counted already).
        at_once = max(1, min(self.a.slots, parallel))
        background_model = model_seconds(("page", "header", "review"), service=True) / at_once
        page_model = model_seconds(("page", "header", "suggest", "review"), service=True)
        per_invoice = page_model / max(1, len(invoices)) / scale if scale else None
        capacity = (WORKING_DAY_HOURS * 3600 * self.a.slots / per_invoice) if per_invoice else None
        failures = (len(failed_imports) + len(read_failed) + sum(v["status"] == "failed" for v in reviews)
                    + len(probe_errors) + log["database is locked"] + log["Traceback"])  # fmt: skip
        results = {
            "arrived": len(invoices), "arrival_seconds": arrival_end, "first_arrival_seconds": arrivals[0] if arrivals
            else None, "arrival_per_hour": _rate(len(arrivals), arrival_end),
            "arrived_by_kind": dict(sorted(_count(kinds.get(i["file_name"], "other") for i in invoices).items())),
            "reads_queued": len(reads), "reads_skipped": sum(r["status"] == "skipped" for r in reads),
            "reads_done": len(done), "reads_failed": len(read_failed), "reads_open_at_end": len(read_open),
            "pages_read": sum(int(r["pages"] or 0) for r in done), "drain_seconds": drain,
            "drain_after_last_arrival": round(drain - arrival_end, 1) if drain is not None and arrival_end is not None
            else None, "reads_per_hour": _rate(len(done), ends[-1] - ends[0] if len(ends) > 1 else None),
            "read_seconds": _summary(float(r["seconds"] or 0) for r in done),
            "reviews_queued": len(reviews), "reviews_done": len(reviews_done),
            "review_outcomes": dict(_count(v["outcome"] or "?" for v in reviews_done)),
            "reviews_failed": sum(v["status"] == "failed" for v in reviews),
            "reviews_open_at_end": sum(v["status"] in ("waiting", "verifying") for v in reviews),
            "first_review_seconds": review_ends[0] if review_ends else None,
            "reviews_while_reads_waited": while_reading, "review_wait_after_read": _summary(review_waits),
            "reviews_per_hour": _rate(len(review_ends), review_ends[-1] - review_ends[0] if len(review_ends) > 1
                                      else None),
            "probe_p50_idle": _pct([s for s, _ in idle], 0.5), "probe_p95_idle": _pct([s for s, _ in idle], 0.95),
            "probe_p50_drop": _pct(probe_drop, 0.5), "probe_p95_drop": _pct(probe_drop, 0.95),
            "probe_max_drop": max(probe_drop) if probe_drop else None, "probes": len(self.probes),
            "probe_errors": len(probe_errors), "failures": failures,
        }  # fmt: skip
        full = {
            "arrival_seconds": _round(arrival_end + arrival_model) if arrival_end is not None else None,
            "drain_seconds": _round(drain + background_model * stretch) if drain is not None else None,
            "model_seconds_per_invoice": _round(per_invoice), "invoices_a_working_day": _round(capacity, 0),
        }  # fmt: skip
        full["arrival_per_hour"] = _rate(len(arrivals), full["arrival_seconds"])
        report = {
            "config": {k: getattr(self.a, k) for k in ("n", "seed", "scanned", "workers", "deliver", "batch", "slots",
                                                         "time_scale", "fail_rate", "reviewer", "drill", "drill_at",
                                                         "drill_for", "slow_factor", "probe_every")},
            "computer": {"cores": os.cpu_count(), "python": platform.python_version(), "system": platform.system(),
                         "machine": platform.machine()},
            "timed_out": self.timed_out, "problem": self.problem, "wall_seconds": round(wall, 1),
            "results": results, "at_full_latency": full, "cpu": cpu,
            "failures": {"imports": failed_imports[:20], "reads": [f"{r['invoice_id']}: {r['error']}"
                                                                   for r in read_failed][:20],
                         "reviews": [f"{v['invoice_id']}: {v['error']}" for v in reviews if v["status"] == "failed"
                                     ][:20], "probe_errors": probe_errors[:20], "server_log": log},
            "self_test": {k: (test or {}).get(k) for k in ("model", "ok", "fields_right", "fields_total", "seconds",
                                                           "problem")},
            "deliveries": self.deliveries, "model_server": stats,
            "samples": [{k: v for k, v in s.items()} for s in self.samples],
            "probes": self.probes, "server_settings": self.server.settings,
        }  # fmt: skip
        if self.a.drill != "none":
            report["drill"] = self.drill.report(
                history, fake_t, reads, self.fake.page_answers(), self.test_pages, self.tries
            )
        return report


def _count(values: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return out


def _rate(n: int, seconds: float | None) -> float | None:
    return round(n / seconds * 3600, 1) if n and seconds and seconds > 0 else None


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def _pct(values: list[float], q: float) -> float | None:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(q * len(ordered)))] if ordered else None


def _summary(values: Any) -> dict[str, float | int | None]:
    ordered = sorted(values)
    if not ordered:
        return {"n": 0, "median": None, "mean": None, "max": None}
    return {"n": len(ordered), "median": round(statistics.median(ordered), 2),
            "mean": round(statistics.fmean(ordered), 2), "max": round(ordered[-1], 2)}  # fmt: skip


def _json(body: bytes) -> dict[str, Any]:
    with contextlib.suppress(ValueError):
        found = json.loads(body or b"{}")
        return found if isinstance(found, dict) else {}
    return {}


# --- The drills -------------------------------------------------------------------------------------------------


class Drill:
    """The model machine misbehaving once ``drill_at`` pages were read: killed, hung, slowed for ``drill_for``
    seconds, or its chat model or page reader swapped for good."""

    def __init__(self, a: argparse.Namespace, fake: FakeModel) -> None:
        self.kind, self.at, self.length, self.factor, self.fake = a.drill, a.drill_at, a.drill_for, a.slow_factor, fake
        self.reader = READER  # the page reader in use (the swapped one once the swap is done)
        self.started: float | None = None
        self.ended: float | None = None
        self._saved: dict[str, Any] = {}

    def over(self) -> bool:
        return self.kind == "none" or self.ended is not None

    def check(self, t: float, pages: int) -> None:
        if self.kind == "none":
            return
        if self.started is None and pages >= self.at:
            self.start(t)
        elif self.started is not None and self.ended is None and t - self.started >= self.length:
            self.end(t)

    def start(self, t: float) -> None:
        self.started = t
        settings = self.fake.settings()
        if self.kind == "kill":
            self.fake.set(down=True)
        elif self.kind == "hang":
            self.fake.set(hang_rate=1.0)
        elif self.kind == "slow":
            self._saved = {k: settings[k] for k in ("page_latency", "chat_latency")}
            self.fake.set(**{k: (lo * self.factor, hi * self.factor) for k, (lo, hi) in self._saved.items()})
        elif self.kind == "swap":
            self.fake.set(models=[SWAP_CHAT, READER])
            self.ended = t
        elif self.kind == "swap-reader":
            self.fake.set(models=[CHAT_MODEL, SWAP_READER])
            self.reader, self.ended = SWAP_READER, t

    def end(self, t: float) -> None:
        self.ended = t
        if self.kind == "kill":
            self.fake.set(down=False)
        elif self.kind == "hang":
            self.fake.set(hang_rate=0.0)
            self.fake.release_hung()  # LM Studio restarted: the requests it held are dropped
        elif self.kind == "slow":
            self.fake.set(**self._saved)

    def report(self, history: list[dict[str, Any]], fake_t0: float, reads: list[dict[str, Any]],
               answers: dict[str, int], test_pages: set[str], tries: dict[int, int]) -> dict[str, Any]:  # fmt: skip
        """What the drill did: requests failed or dropped in it, reads lost (queued, never read) or pages read twice,
        how long the app took to see it and to recover."""
        if self.started is None:
            return {"kind": self.kind, "started": None, "note": f"never started: fewer than {self.at} pages read",
                    "reads_lost": 0, "pages_read_twice": 0, "recovery_seconds": None, "failover": FAILOVER}  # fmt: skip
        start, end = self.started, self.ended if self.ended is not None else self.started

        def t_of(h: dict[str, Any]) -> float:
            return h["t"] - fake_t0

        bad = [h for h in history if t_of(h) + h["seconds"] >= start and h["outcome"] != "served"]
        first_bad = min((t_of(h) + h["seconds"] for h in bad), default=None)
        served_after = sorted(t_of(h) + h["seconds"] for h in history if h["outcome"] == "served"
                              and h["kind"] == "page" and h["image"] not in test_pages and t_of(h) >= end)  # fmt: skip
        lost = [r for r in reads if r["status"] in ("failed", "waiting", "reading")]
        hung = [h for h in bad if h["outcome"] == "hung"]
        twice = {k: n for k, n in answers.items() if n > 1 and k not in test_pages}
        out = {
            "kind": self.kind, "started": round(start, 1), "ended": round(end, 1),
            "requests_not_answered": dict(_count(f"{h['kind']} {h['outcome']}" for h in bad)),
            "app_saw_it_after": _round(first_bad - start) if first_bad is not None else None,
            "recovery_seconds": _round(served_after[0] - end) if served_after else None,
            "reads_postponed": len(tries), "postponed_most": max(tries.values(), default=0),
            "reads_lost": len(lost), "lost": [f"{r['invoice_id']}: {r['status']} {r['error']}" for r in lost][:10],
            "pages_read_twice": len(twice), "failover": FAILOVER,
            "hung": len(hung), "hung_given_up_by_the_app": sum(bool(h.get("gave_up")) for h in hung),
            "hung_longest_seconds": max((h["seconds"] for h in hung), default=None),
            "answer_seconds_before": _summary(h["seconds"] for h in history if h["outcome"] == "served"
                                              and 0 <= t_of(h) < start and h["kind"] != "models")["median"],
            "answer_seconds_during": _summary(h["seconds"] for h in history if h["outcome"] == "served"
                                              and start <= t_of(h) < end and h["kind"] != "models")["median"],
        }  # fmt: skip
        if self.kind == "swap":
            old = [h for h in history if t_of(h) >= start and h["model"] == CHAT_MODEL]
            new = sorted(
                t_of(h) + h["seconds"] for h in history if h["model"] == SWAP_CHAT and h["outcome"] == "served"
            )
            out.update({"asked_the_old_model_after": len(old), "qualified": QUALIFIED,
                        "new_model_answered_after": _round(new[0] - start) if new else None})  # fmt: skip
        if self.kind == "swap-reader":
            out["new_reader_read_after"] = out.pop("recovery_seconds")
            out["recovery_seconds"] = out["new_reader_read_after"]
        return out


# --- The server, a client of it, and its database -----------------------------------------------------------------


class Server:
    """``python -m ap_coder serve --server --workers W --insecure-http`` on ``data`` (127.0.0.1, a free port)."""

    def __init__(self, data: Path, log: Path, workers: int, env: dict[str, str]) -> None:
        from ap_coder.ops.offline import free_port

        self.data, self.log, self.workers, self.env = data, log, workers, env
        self.port = free_port(18500 + os.getpid() % 1000, "127.0.0.1")
        self.base = f"http://127.0.0.1:{self.port}"
        self.proc: subprocess.Popen[bytes] | None = None
        self.settings = {k: v for k, v in env.items() if k.startswith("AP_")}

    def start(self, timeout: float = 120.0) -> None:
        from ap_coder.core import paths

        empty = self.data.parent / "empty.env"
        empty.write_text("", encoding="utf-8")
        command = [sys.executable, "-m", "ap_coder", "--env-file", str(empty), "--db", str(self.data / "ap_coder.db"),
                   "serve", "--server", "--insecure-http", "--host", "127.0.0.1", "--port", str(self.port),
                   "--workers", str(self.workers)]  # fmt: skip
        env = {**self.env, "AP_PRIVATE_DIR": str(self.data)}
        with self.log.open("wb") as f:
            self.proc = subprocess.Popen(command, cwd=paths.PROJECT_DIR, env=env, stdout=f, stderr=subprocess.STDOUT)
        deadline = time.monotonic() + timeout
        while True:
            with contextlib.suppress(OSError), _direct().open(self.base + "/healthz", timeout=2) as answer:
                if answer.status == 200:
                    return
            if self.proc.poll() is not None or time.monotonic() > deadline:
                raise RuntimeError(f"the server did not start (see {self.log})")
            time.sleep(0.3)

    def pids(self) -> dict[int, str]:
        """The server's processes (Linux): pid → its role."""
        if self.proc is None or not Path("/proc").is_dir():
            return {}
        parents: dict[int, int] = {}
        for entry in Path("/proc").iterdir():
            if entry.name.isdigit():
                with contextlib.suppress(OSError, ValueError, IndexError):
                    parents[int(entry.name)] = int(
                        (entry / "stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()[1]
                    )
        tree, todo = {self.proc.pid}, [self.proc.pid]
        while todo:
            pid = todo.pop()
            for child, parent in parents.items():
                if parent == pid and child not in tree:
                    tree.add(child)
                    todo.append(child)
        roles = {}
        for pid in sorted(tree):
            try:
                command = (Path("/proc") / str(pid) / "cmdline").read_bytes().replace(b"\0", b" ").decode()
            except OSError:
                continue
            roles[pid] = ("server" if pid == self.proc.pid else "background" if "--background-work" in command
                          else "capture" if "--capture-work" in command
                          else "other" if "resource_tracker" in command else "web worker")  # fmt: skip
        return roles

    def stop(self) -> None:
        if self.proc is None or self.proc.poll() is not None:
            return
        pids = self.pids()
        with contextlib.suppress(OSError, ValueError):
            token = json.loads((self.data / ".ap_coder_server.json").read_text(encoding="utf-8"))["token"]
            request = urllib.request.Request(self.base + "/shutdown", data=b"", method="POST",
                                             headers={"X-AP-Coder-Token": token})  # fmt: skip
            _direct().open(request, timeout=10).close()
        try:
            self.proc.wait(60)
        except subprocess.TimeoutExpired:
            for pid in pids:
                with contextlib.suppress(OSError):
                    os.kill(pid, 9)
            self.proc.wait(10)

    def log_counts(self) -> dict[str, int]:
        """Lines of the server's output and logs that say something went wrong."""
        texts = [self.log] + sorted((self.data / "logs").glob("*.log")) if (self.data / "logs").is_dir() else [self.log]
        words = ("database is locked", "Traceback", "ERROR")
        counts = dict.fromkeys(words, 0)
        for path in texts:
            with contextlib.suppress(OSError), path.open(encoding="utf-8", errors="replace") as f:
                for line in f:
                    for word in words:
                        counts[word] += word in line
        return counts


def _direct() -> urllib.request.OpenerDirector:
    """No proxy, no redirect followed: the server on this computer only."""

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args: Any, **kwargs: Any) -> None:
            return None

    return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect)


class Client:
    """A browser of the server: its cookies, the page's header on every action."""

    def __init__(self, base: str) -> None:
        self.base, self.cookies, self.opener = base, {}, _direct()
        self._lock = threading.Lock()

    def request(self, method: str, path: str, data: bytes | None = None, headers: dict[str, str] | None = None,
                timeout: float = 120.0) -> tuple[int, bytes, float]:  # fmt: skip
        with self._lock:
            cookie = "; ".join(f"{k}={v}" for k, v in self.cookies.items())
        request = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={**({"Cookie": cookie} if cookie else {}), **(headers or {})},
        )
        started = time.perf_counter()
        try:
            with self.opener.open(request, timeout=timeout) as answer:
                status, body, sent = answer.status, answer.read(), answer.headers
        except urllib.error.HTTPError as exc:
            status, body, sent = exc.code, exc.read(), exc.headers
        except OSError:
            return 0, b"", time.perf_counter() - started
        seconds = time.perf_counter() - started
        with self._lock:
            for line in sent.get_all("Set-Cookie") or []:
                name, _, rest = line.partition("=")
                value = rest.split(";", 1)[0]
                if not value or "max-age=0" in rest.lower():
                    self.cookies.pop(name.strip(), None)
                else:
                    self.cookies[name.strip()] = value
        return status, body, seconds

    def post_form(self, path: str, fields: dict[str, str]) -> tuple[int, bytes, float]:
        data = urllib.parse.urlencode(fields).encode()
        return self.request("POST", path, data, {**PAGE, "Content-Type": "application/x-www-form-urlencoded"})

    def login(self, username: str, password: str, secret: str) -> None:
        """The password, then the authenticator's code (it sets the authenticator up at this first login)."""
        from ap_coder.core import authenticator

        status, body, _ = self.post_form("/login", {"username": username, "password": password, "next": "/review"})
        step = str(_json(body).get("redirect") or "").split("?", 1)[0]
        if status != 200 or not step.startswith("/login/"):
            raise RuntimeError(f"the login was refused ({status}): {body[:300]!r}")
        code = authenticator.code(secret, time.time())
        status, body, _ = self.post_form(step, {"code": code, "next": "/review"})
        if status != 200 or not any("session" in name for name in self.cookies):
            raise RuntimeError(f"the authenticator's code was refused ({status}): {body[:300]!r}")

    def upload(self, files: list[Path]) -> tuple[int, bytes, float]:
        boundary = f"apbench{os.getpid()}{time.monotonic_ns()}"
        parts = [f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="{p.name}"\r\n'
                 f"Content-Type: application/octet-stream\r\n\r\n".encode() + p.read_bytes() + b"\r\n"
                 for p in files]  # fmt: skip
        body = b"".join(parts) + f"--{boundary}--\r\n".encode()
        return self.request(
            "POST",
            "/process/upload",
            body,
            {**PAGE, "Content-Type": f"multipart/form-data; boundary={boundary}"},
            timeout=600,
        )

    def probe(self) -> tuple[float, int]:
        """GET /review: (seconds, status); 0 when nothing answered."""
        status, _, seconds = self.request("GET", "/review", timeout=60)
        return round(seconds, 4), status


class Reader:
    """The server's database, read only (the bench writes nothing in it)."""

    def __init__(self, db: Path) -> None:
        self.db = db
        self._conn: sqlite3.Connection | None = None

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def _rows(self, sql: str, args: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        if self._conn is None:
            self._conn = sqlite3.connect(f"{self.db.resolve().as_uri()}?mode=ro", uri=True, timeout=10,
                                         check_same_thread=False)  # fmt: skip
            self._conn.row_factory = sqlite3.Row
        try:
            return self._conn.execute(sql, args).fetchall()
        except sqlite3.OperationalError:
            return []

    def counts(self) -> dict[str, Any]:
        found = self._rows("SELECT COUNT(*) FROM invoices")
        reads = self._rows("SELECT status, COUNT(*) FROM page_reads GROUP BY status")
        reviews = self._rows("SELECT status, COUNT(*) FROM verifications GROUP BY status")
        tries = self._rows("SELECT invoice_id, tries FROM page_reads WHERE tries > 0")  # cut off, read again later
        return {
            "invoices": int(found[0][0]) if found else 0,
            "page_reads": {r[0]: int(r[1]) for r in reads},
            "reviews": {r[0]: int(r[1]) for r in reviews},
            "tries": {int(r[0]): int(r[1]) for r in tries},
        }

    def invoices(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._rows("SELECT id, file_name, status, created_at, error FROM invoices")]

    def page_reads(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self._rows("SELECT invoice_id, status, model, pages, seconds, tries, error, "
                                             "created_at, updated_at FROM page_reads")]  # fmt: skip

    def reviews(self) -> list[dict[str, Any]]:
        return [
            dict(r)
            for r in self._rows(
                "SELECT invoice_id, status, rounds, seconds, tries, error, created_at, "
                "updated_at, CASE WHEN json_valid(result_json) THEN "
                "json_extract(result_json, '$.status') END outcome FROM verifications"
            )
        ]

    def self_test(self) -> dict[str, Any] | None:
        """The page reader's latest self-test (any model)."""
        rows = self._rows("SELECT value FROM settings WHERE key = 'page_reader_test'")
        if not rows:
            return None
        with contextlib.suppress(ValueError):
            saved = json.loads(rows[0][0])
            tests = list((saved.get("models") or {}).values()) if isinstance(saved.get("models"), dict) else [saved]
            return max(tests, key=lambda t: str(t.get("when") or ""), default=None)
        return None


class Cpu:
    """Each server process's CPU seconds (Linux /proc), kept at their highest as processes come and go."""

    def __init__(self, server: Server) -> None:
        self.server = server
        self.seen: dict[int, tuple[str, float]] = {}
        self.tick = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100

    def sample(self) -> None:
        for pid, role in self.server.pids().items():
            with contextlib.suppress(OSError, ValueError, IndexError):
                fields = (Path("/proc") / str(pid) / "stat").read_text(encoding="utf-8").rsplit(")", 1)[1].split()
                seconds = (int(fields[11]) + int(fields[12])) / self.tick
                self.seen[pid] = (role, max(seconds, self.seen.get(pid, (role, 0.0))[1]))

    def totals(self) -> dict[int, float]:
        return {pid: seconds for pid, (_role, seconds) in self.seen.items()}

    def report(self, before: dict[int, float], wall: float) -> dict[str, Any]:
        if not self.seen:
            return {"measured": False, "note": "CPU is measured on Linux only (/proc)"}
        roles: dict[str, list[float]] = {}
        for pid, (role, seconds) in sorted(self.seen.items()):
            roles.setdefault(role, []).append(seconds - before.get(pid, 0.0))
        own = time.process_time()
        out: dict[str, Any] = {"measured": True, "wall_seconds": round(wall, 1), "processes": {}}
        for role, values in roles.items():
            out["processes"][role] = {"processes": len(values), "cpu_seconds": round(sum(values), 1),
                                      "busiest": round(max(values), 1),
                                      "cores": round(sum(values) / wall, 2) if wall else None}  # fmt: skip
        out["bench_and_fake_model_cpu_seconds"] = round(own, 1)
        out["total_cpu_seconds"] = round(sum(sum(v) for v in roles.values()), 1)
        return out


# --- The report ---------------------------------------------------------------------------------------------------


def _hms(seconds: float | None) -> str:
    if seconds is None:
        return "-"
    seconds = float(seconds)
    if seconds < 120:
        return f"{seconds:.0f} s"
    if seconds < 7200:
        return f"{seconds / 60:.1f} min"
    return f"{seconds / 3600:.1f} h"


def _ms(seconds: float | None) -> str:
    return "-" if seconds is None else f"{1000 * seconds:.0f} ms"


def _num(value: float | None) -> str:
    return "-" if value is None else f"{value:,.0f}"


def markdown(report: dict[str, Any]) -> str:
    c, r, full, cpu = report["config"], report["results"], report["at_full_latency"], report["cpu"]
    lo, hi = c["latency"]
    clo, chi = c["chat_latency"]
    lines = [
        "# Server day: a morning's drop through the company server",
        "",
        f"{c['n']} invoices ({round(100 * c['scanned'])}% scans, seed {c['seed']}) delivered by "
        f"{'the invoices folder and the Process page' if c['deliver'] == 'folder' else 'an Admin uploading'} "
        f"to `serve --server --workers {c['workers']}`; the fake model: {lo:g}-{hi:g} s a page, {clo:g}-{chi:g} s a "
        f"chat answer, {c['slots']} slot(s), AP_LLM_PARALLEL {c['llm_parallel']}, time x{c['time_scale']:g}; AI "
        f"reviewer: {c['reviewer']}. {report['computer']['cores']} cores, Python {report['computer']['python']}, "
        f"{report['computer']['system']}. Wall time {_hms(report['wall_seconds'])}"
        f"{' (TIMED OUT)' if report['timed_out'] else ''}.",
        "",
    ]
    if report.get("problem"):
        lines += [f"**Stopped: {report['problem']}**", ""]
    lines += [
        "| | measured | at full latency (estimate) |",
        "|---|---:|---:|",
        f"| invoices in the review queue | {r['arrived']} of {c['n']} | |",
        f"| all of them in after | {_hms(r['arrival_seconds'])} | {_hms(full['arrival_seconds'])} |",
        f"| arrival, invoices an hour | {_num(r['arrival_per_hour'])} | {_num(full['arrival_per_hour'])} |",
        f"| page reader: queued / skipped (clean digital) / read / failed / left | {r['reads_queued']} / "
        f"{r['reads_skipped']} / {r['reads_done']} / {r['reads_failed']} / {r['reads_open_at_end']} | |",
        f"| page reader drained after (from the drop) | {_hms(r['drain_seconds'])} | {_hms(full['drain_seconds'])} |",
        f"| page reader, invoices an hour | {_num(r['reads_per_hour'])} | |",
        f"| AI reviewer: queued / done / failed / left | {r['reviews_queued']} / {r['reviews_done']} / "
        f"{r['reviews_failed']} / {r['reviews_open_at_end']} | |",
        f"| AI reviewer, reviews an hour (first one after) | {_num(r['reviews_per_hour'])} "
        f"({_hms(r['first_review_seconds'])}) | |",
        f"| AI reviewer: reviews done while the page reader had invoices in line | "
        f"{r.get('reviews_while_reads_waited', '-')} | |",
        f"| AI reviewer: an invoice's wait for it once read, median / max | "
        f"{_hms((r.get('review_wait_after_read') or {}).get('median'))} / "
        f"{_hms((r.get('review_wait_after_read') or {}).get('max'))} | |",
        f"| model time an invoice | | {full['model_seconds_per_invoice'] or '-'} s |",
        f"| invoices the model machine serves in a {WORKING_DAY_HOURS:g}-hour day | | "
        f"{_num(full['invoices_a_working_day'])} |",
        f"| GET /review p50 / p95, idle | {_ms(r['probe_p50_idle'])} / {_ms(r['probe_p95_idle'])} | |",
        f"| GET /review p50 / p95 / max, during the drop | {_ms(r['probe_p50_drop'])} / {_ms(r['probe_p95_drop'])} / "
        f"{_ms(r['probe_max_drop'])} ({r['probes']} probes, {r['probe_errors']} errors) | |",
        f"| failures (imports, reads, reviews, probes, locked, tracebacks) | {r['failures']} | |",
        "",
    ]
    if cpu.get("measured"):
        lines += ["CPU (seconds over the run; cores = seconds / wall time):", "",
                  "| process | n | CPU s | busiest | cores |", "|---|---:|---:|---:|---:|"]  # fmt: skip
        for role, p in cpu["processes"].items():
            lines.append(f"| {role} | {p['processes']} | {p['cpu_seconds']} | {p['busiest']} | {p['cores']} |")
        lines += [f"| the bench and its fake model | 1 | {cpu['bench_and_fake_model_cpu_seconds']} | | |", ""]
    else:
        lines += [cpu.get("note", ""), ""]
    samples = report["samples"]
    if samples:
        step = max(1, len(samples) // 12)
        lines += ["Over time (seconds from the drop):", "",
                  "| s | in the queue | page reads waiting / reading / done | reviews waiting / done | model busy / "
                  "waiting |", "|---:|---:|---:|---:|---:|"]  # fmt: skip
        for s in samples[::step] + ([samples[-1]] if (len(samples) - 1) % step else []):
            pr, rv, m = s["page_reads"], s["reviews"], s["model"]
            lines.append(f"| {s['t']:.0f} | {s['invoices']} | {pr.get('waiting', 0)} / {pr.get('reading', 0)} / "
                         f"{pr.get('done', 0)} | {rv.get('waiting', 0)} / {rv.get('done', 0)} | {m['active']} / "
                         f"{m['waiting']} |")  # fmt: skip
        lines.append("")
    test = report["self_test"]
    lines += [f"Page reader self-test: {test.get('model')} {'passed' if test.get('ok') else 'NOT passed'} "
              f"({test.get('fields_right')} of {test.get('fields_total')} fields).", ""]  # fmt: skip
    d = report.get("drill")
    if d:
        lines += [f"## Drill: {d['kind']}", ""]
        if d.get("started") is None:
            lines += [d.get("note", ""), ""]
        else:
            lines += [
                f"- from {d['started']} s to {d['ended']} s after the drop; requests not answered in it: "
                f"{d['requests_not_answered'] or 'none'}; the app saw it after {_hms(d['app_saw_it_after'])}",
                f"- recovery (the first page read after it ended): {_hms(d['recovery_seconds'])}; a model answer took "
                f"{d['answer_seconds_before'] or '-'} s before it, {d['answer_seconds_during'] or '-'} s in it "
                "(median, as measured)",
                f"- reads postponed {d['reads_postponed']} (cut off up to {d['postponed_most']} time(s) each), lost "
                f"{d['reads_lost']} (failed or never read: {'; '.join(d['lost']) or 'none'}), pages read twice "
                f"{d['pages_read_twice']}",
            ]  # fmt: skip
            if d["hung"]:
                lines.append(
                    f"- hung requests {d['hung']}, given up by AP Coder {d['hung_given_up_by_the_app']} (the "
                    f"rest dropped when LM Studio restarted); the longest held {_hms(d['hung_longest_seconds'])}"
                )
            if d["kind"] == "swap":
                lines += [
                    f"- requests to the old chat model after the swap: {d['asked_the_old_model_after']}; the "
                    f"new one answered after {_hms(d['new_model_answered_after'])}; qualified: {d['qualified']}"
                ]
            lines += [f"- failover to a backup model server: {d['failover']}", ""]
    f = report["failures"]
    if any(f[k] for k in ("imports", "reads", "reviews", "probe_errors")) or any(f["server_log"].values()):
        lines += ["## Failures", "", f"- server log: {f['server_log']}"]
        lines += [f"- {kind}: {item}" for kind in ("imports", "reads", "reviews", "probe_errors") for item in f[kind]]
        lines.append("")
    lines += [
        "Notes: at full latency is an estimate. Arrival adds back the chat model's answers the import waited for "
        "(GL suggestions), each at most the arrival's limit and the capture processes waiting side by side; the "
        "page reader its pages, header and review answers divided by AP_LLM_PARALLEL; the work "
        "on this computer (OCR, the pipeline) is as measured. The app's own timers are real time: a minute's pause "
        "after a failed read, 30 s when nothing waits, the model status kept 10-30 s. Failover: " + FAILOVER + ".",
    ]
    return "\n".join(lines) + "\n"
