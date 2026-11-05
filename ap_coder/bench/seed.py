"""``python -m ap_coder.bench seed --out DIR --invoices N``: a big, realistic server-mode data folder, made fast, for
the volume and load benches (docs/SCALE_GOAL.md, phase 0).

What it holds, by default for N invoices: 3,000 vendors with invoices among a vendor master of 20,000, 1,500 GL
accounts, 300 cost centers, our bank accounts in CAD and USD (each invoice paid from its currency's: the Pay from of
every approval), 3,000 POs, invoices spread over 12 months (the newest 3,000 waiting in the review queue,
parked or for a second approval; the rest approved, nearly all exported in daily batches, a few rejected or failed),
their events (arrival, approval, second approval, export, park, reject) and the people's logins, the learning memory
(a line of feedback per approved line, lessons, the readers' and suppliers' scores, the lines as approved), the JD
Edwards register (2 rows per invoice; ``--register 600000``) and 400 people across the roles (70% Clerk, 20%
Approver, 5% Admin, 5% Read-only), each with a password and an authenticator app set up: ``users.json`` lists them
for the load harness (username, password, TOTP secret, role).

How it stays faithful to the app:

* The database is made by the real ``Store()``; the reference data by the store's own imports (``import_accounts``,
  ``import_vendor_master``, ``import_purchase_orders``, ``import_erp_register``); each password is hashed by
  ``users.hash_password`` and each secret sealed with this folder's ``authenticator.key``.
* The ten bundled sample invoices are the templates: each is read, coded and checked by the pipeline
  (``InvoicePipeline.process``) and approved as the review screen does (``approve_invoice``, ``learn_from_approval``),
  so every table an arrival and an approval write has its real rows.
* Every other invoice is a template's rows cloned with ``executemany``: its own id, vendor (with the vendor master's
  GST/HST number), invoice number, dates, amounts of its own (its template's scaled and added up again, so every check
  that adds up still does: ``_scaled``) and status. A clone in the queue keeps its template's checks, stamped with
  today's ``checks_stamp``, as if checked after the last change to the reference data.
* The triggers mark every clone in ``summary_dirty`` as any change does; the store then works the summary, its search
  index and its generations out itself (``_refresh_summary``).
* Each clone's file is a hard link to its template's PDF in ``invoices/``, so 100,000 invoices take a few MB of files;
  its row records a SHA-256 of its own, as a real file has (hashing the file itself finds its template: "already in
  AP Coder"). ``--files copy`` gives each its own bytes instead (about 12 KB on disk each), and their true SHA-256.

What it leaves out: the AI reviewer's results (nothing waits for it), template snapshots (5 per supplier at most in
the app), sessions and login tries, drafts, manager requests. The clones' page text and words are their template's.

The bulk writes run with the rollback journal off and the database back in WAL mode at the end: a build cut short is
made again, not repaired. It refuses a folder that already has a database (``--replace`` deletes it first) and a build
the disk can't hold. It ends with a stand-in for the day's backup (``mark_backed_up``), so the app serving the folder
doesn't copy it whole at its start; ``--mark-backed-up`` writes it again (it lasts 20 hours)."""

from __future__ import annotations

import argparse
import base64
import bisect
import datetime as dt
import hashlib
import json
import math
import os
import random
import shutil
import sqlite3
import sys
import time
from multiprocessing import get_context
from pathlib import Path
from typing import Any, NamedTuple

DB_NAME = "ap_coder.db"
USERS_FILE = "users.json"
REPORT_FILE = "seed.json"
# Bytes on disk, by what is written (measured on a 12,000-invoice build: 370 MB): the disk check's estimate.
BYTES_FIXED = 40_000_000  # the vendor master, GL accounts, POs, lessons and supplier profiles
BYTES_PER_INVOICE = 19_500  # its row (11.5 KB), its capture, summary, events and feedback
BYTES_PER_HISTORY = 9_500  # the learning rows of an approved invoice (``--history``)
BYTES_PER_REGISTER = 200
DISK_MARGIN = 300 * 1024 * 1024  # left free after the build
ROLE_SHARES = (("clerk", 0.70), ("approver", 0.20), ("admin", 0.05), ("readonly", 0.05))
APPROVAL_LIMIT = 25_000  # CAD: over it, a second approval (Settings → Review); about 4% of the invoices
# A clone's amounts: its template's ($1,800 to $21,600) times a factor drawn evenly on a log scale, so each invoice has
# amounts of its own (a vendor's invoices of one amount are as rare as on real ones: the duplicate checks pair them)
FACTORS = (0.05, 2.0)
FX_RATES = "USD=1.37"
# Our bank accounts (D-66), as a real installation has them: an invoice is paid from its currency's default, so every
# approval records one, and the queue asks each waiting vendor's last (``Store.last_pay_froms``, B-1739).
BANK_ACCOUNTS = ({"code": "CAD-OP", "name": "Operating account", "currency": "CAD", "is_default": "yes"},
                 {"code": "USD-OP", "name": "US dollar account", "currency": "USD", "is_default": "yes"})  # fmt: skip
CREDIT_NOTE = "northwind_ON_HST_CN-2026-0047"  # the template a share of the clones of every vendor use
SHARES = {"parked": 0.05, "rejected": 0.01, "failed": 0.003, "credit": 0.02, "po": 0.15}
CHUNK = 1000  # clones made by a worker at a time

_FIRST = ("Ann", "Bob", "Chantal", "Dev", "Elena", "Farid", "Grace", "Hugo", "Isabelle", "Jamal", "Kim", "Luc",
          "Maya", "Noah", "Olivia", "Priya", "Quinn", "Raj", "Sofia", "Tom", "Uma", "Victor", "Wei", "Xavier",
          "Yasmin", "Zoe", "Amir", "Brigitte", "Carlos", "Dana")  # fmt: skip
_LAST = ("Lee", "Martin", "Tremblay", "Singh", "Roy", "Wong", "Gagnon", "Smith", "Brown", "Côté", "Patel", "Nguyen",
         "Bouchard", "Wilson", "Taylor", "Chen", "Pelletier", "Kaur", "Leblanc", "Campbell", "Morin", "Ali", "Fraser",
         "Gauthier", "MacDonald", "Kim", "Lavoie", "Young", "Stewart", "Fortin")  # fmt: skip
_WORDS = ("Northern", "Prairie", "Harbour", "Maple", "Summit", "Granite", "Cedar", "Aurora", "Riverbend", "Lakeshore",
          "Bluewater", "Ironwood", "Silverline", "Westridge", "Eastgate", "Pinecrest", "Redcliff", "Stonebridge",
          "Clearview", "Highland", "Birchwood", "Coastal", "Frontier", "Glacier", "Kestrel", "Meadowlark", "Oakridge",
          "Pioneer", "Thunder Bay", "Whitecap")  # fmt: skip
_TRADES = ("Office", "Industrial", "Electric", "Mechanical", "Courier", "Logistics", "Printing", "Janitorial",
           "Consulting", "Engineering", "Fuel", "Tire", "Safety", "Software", "Telecom", "Catering", "Hardware",
           "Lumber", "Plumbing", "Medical", "Fleet", "Security", "Signage", "Uniform", "Waste")  # fmt: skip
_KINDS = ("Supply", "Services", "Solutions", "Group", "Partners", "Systems", "Works", "Distributors", "Contractors",
          "Rentals")  # fmt: skip
_SUFFIXES = ("Ltd.", "Inc.", "Corp.", "LP", "Ltée")


class Vendor(NamedTuple):
    name: str
    key: str  # ``memory.vendor_key`` of the name: the vendor master's key, the invoices'
    supplier: str  # ``capture.supplier.supplier_key``: its supplier profile's, its outcomes'
    gst: str
    erp_id: str
    terms: str
    template: int  # the template its invoices are clones of (its layout)
    in_master: bool
    prefix: str  # its invoice numbers' style


class User(NamedTuple):
    id: int
    username: str
    name: str
    role: str
    password: str
    secret: str


class Clone(NamedTuple):
    id: int
    template: int
    vendor: int
    number: str
    created: str
    invoice_date: str
    factor: float  # its amounts: about its template's times this (``_scaled``)
    status: str
    reviewed: str | None
    reviewer: int | None  # index in the users
    second: int | None
    second_at: str | None
    batch: int | None
    batch_at: str | None
    exporter: int | None
    parked: str
    history: bool  # its approval's learning rows (lessons, readers' and suppliers' scores, lines) are written
    po: str
    uploader: int


def add_parser(sub: Any) -> argparse.ArgumentParser:
    """``seed``'s command line, under ``python -m ap_coder.bench``."""
    p = sub.add_parser("seed", help="a big, realistic server-mode data folder for the volume and load benches")
    p.add_argument("--out", required=True, help="the data folder to make (AP_PRIVATE_DIR for the server)")
    p.add_argument("--invoices", type=int, default=12_000, help="invoices in all (default 12,000)")
    p.add_argument("--vendors", type=int, default=3_000, help="vendors with invoices (default 3,000)")
    p.add_argument("--master", type=int, default=20_000, help="vendors in the vendor master (default 20,000)")
    p.add_argument("--gl", type=int, default=1_500, help="GL accounts (default 1,500)")
    p.add_argument("--cost-centers", type=int, default=300, help="cost centers (default 300)")
    p.add_argument("--pos", type=int, default=3_000, help="purchase orders (default 3,000)")
    p.add_argument("--users", type=int, default=400, help="people with an account (default 400)")
    p.add_argument("--queue", type=int, default=3_000, help="invoices waiting in the review queue (default 3,000)")
    p.add_argument("--months", type=int, default=12, help="months the invoices arrived over (default 12)")
    p.add_argument("--register", type=int, default=None, help="JD Edwards register rows (default 2 per invoice)")
    p.add_argument(
        "--history",
        type=float,
        default=1.0,
        help="share of approved invoices with their learning rows (default 1; less saves disk)",
    )
    p.add_argument(
        "--files",
        choices=("link", "copy"),
        default="link",
        help="each invoice's file: a hard link to its template's (default) or a copy of its own",
    )
    p.add_argument("--seed", type=int, default=1, help="random seed: the same seed gives the same data")
    p.add_argument("--workers", type=int, default=min(4, os.cpu_count() or 1), help="processes making the clones")
    p.add_argument("--replace", action="store_true", help="delete the folder's database and files first")
    p.add_argument("--mark-backed-up", action="store_true",
                   help="only write a stand-in for today's backup in a folder made already (the app then makes none "
                        "for 20 hours): run it before serving a big folder")  # fmt: skip
    p.set_defaults(run=main)
    return p


def main(a: argparse.Namespace) -> int:
    if a.mark_backed_up:
        if not (Path(a.out) / DB_NAME).is_file():
            print(f"seed: {a.out} has no {DB_NAME}", file=sys.stderr)
            return 1
        if not (Path(a.out) / REPORT_FILE).is_file():  # a real data folder's daily backups would stop for 20 hours
            print(f"seed: {a.out} has no {REPORT_FILE}: only a folder the seed made is marked", file=sys.stderr)
            return 1
        print(f"the day's backup stood in for (for 20 hours): {mark_backed_up(Path(a.out))}")
        return 0
    try:
        report = build(Path(a.out), invoices=a.invoices, vendors=a.vendors, master=a.master, gl=a.gl,
                       cost_centers=a.cost_centers, pos=a.pos, users=a.users, queue=a.queue, months=a.months,
                       register=a.register, history=a.history, files=a.files, seed=a.seed, workers=a.workers,
                       replace=a.replace)  # fmt: skip
    except (FileExistsError, OSError, ValueError) as exc:
        print(f"seed: {exc}", file=sys.stderr)
        return 1
    s = report["sizes"]
    print(f"{report['counts']['rows/invoices']:,} invoices in {report['seconds']['total']:.0f} s: database "
          f"{s['database_mb']:,.1f} MB ({s['per_1000_invoices_mb']:.1f} MB per 1,000 invoices), files "
          f"{s['files_mb']:,.1f} MB")  # fmt: skip
    print("  " + ", ".join(f"{k} {v:.1f} s" for k, v in report["seconds"].items() if k != "total"))
    print(f"  people: {Path(a.out) / USERS_FILE}; report: {Path(a.out) / REPORT_FILE}")
    return 0


def estimate_bytes(invoices: int, history: float, register: int) -> int:
    """What a build takes on disk, roughly (``BYTES_PER_*``)."""
    return int(BYTES_FIXED + invoices * (BYTES_PER_INVOICE + BYTES_PER_HISTORY * history)
               + register * BYTES_PER_REGISTER)  # fmt: skip


def build(out: Path, *, invoices: int = 12_000, vendors: int = 3_000, master: int = 20_000, gl: int = 1_500,
          cost_centers: int = 300, pos: int = 3_000, users: int = 400, queue: int = 3_000, months: int = 12,
          register: int | None = None, history: float = 1.0, files: str = "link", seed: int = 1, workers: int = 1,
          replace: bool = False) -> dict[str, Any]:  # fmt: skip
    """Make the data folder ``out`` (see the module's docstring); returns what ``seed.json`` records."""
    out = out.resolve()
    if invoices < 20 or vendors < 10 or users < 4:
        raise ValueError("a seed has at least 20 invoices, 10 vendors and 4 people")
    vendors, master = min(vendors, invoices), max(master, min(vendors, invoices))
    register = 2 * invoices if register is None else register
    db = out / DB_NAME
    if db.exists() or (out / "invoices").exists():
        if not replace:
            raise FileExistsError(f"{out} has a database or invoices already (--replace deletes them first)")
        for name in (DB_NAME, f"{DB_NAME}-wal", f"{DB_NAME}-shm", USERS_FILE, REPORT_FILE, "authenticator.key"):
            (out / name).unlink(missing_ok=True)
        shutil.rmtree(out / "invoices", ignore_errors=True)
    out.mkdir(parents=True, exist_ok=True)
    need = estimate_bytes(invoices, history, register) + (invoices * 12_288 if files == "copy" else 0)
    free = shutil.disk_usage(out).free
    if need + DISK_MARGIN > free:
        raise OSError(f"about {need / 1e6:,.0f} MB needed and {free / 1e6:,.0f} MB free: build fewer invoices, or "
                      f"lower --history / --register")  # fmt: skip
    rng = random.Random(seed)
    old_env = os.environ.get("AP_PRIVATE_DIR")
    os.environ["AP_PRIVATE_DIR"] = str(out)  # the pipeline's caches, the authenticator's key: this folder's
    seconds: dict[str, float] = {}
    started = clock = time.perf_counter()

    def lap(name: str) -> None:
        nonlocal clock
        now = time.perf_counter()
        seconds[name] = round(now - clock, 2)
        clock = now

    try:
        from ap_coder.core.store import Store
        from ap_coder.core.store.folder import fill_seen_files

        store = Store(db)
        the_vendors = _reference(store, rng, vendors, master, gl, cost_centers)
        lap("reference")
        people = _people(store, out, rng, users, workers)
        lap("users")
        templates = _templates(store, out, people)
        lap("templates")
        the_vendors = _place(the_vendors, templates, rng)
        _master(store, the_vendors, templates, rng, master)
        po_numbers = _purchase_orders(store, the_vendors[:vendors], templates, rng, pos)
        lap("vendors_pos")
        plan = _plan(store, rng, invoices - len(templates), the_vendors[:vendors], templates, people, po_numbers,
                     queue, max(1, months), history)  # fmt: skip
        lap("plan")
        counts, totals = _write(store, out, plan, templates, the_vendors, people, files, workers)
        lap("clones")
        _summary(store)
        with store._conn() as conn:  # what a pick-up read, as an upgrade notes it: else Import invoices lists them all
            fill_seen_files(conn, again=True)
        lap("summary")
        counts["register"] = _register(store, plan, totals, the_vendors, rng, register)
        lap("register")
    finally:
        if old_env is None:
            os.environ.pop("AP_PRIVATE_DIR", None)
        else:
            os.environ["AP_PRIVATE_DIR"] = old_env
    sizes = _sizes(db, out, invoices)
    mark_backed_up(out)
    seconds["total"] = round(time.perf_counter() - started, 2)
    report = {
        "config": {"invoices": invoices, "vendors": vendors, "master": master, "gl": gl, "cost_centers": cost_centers,
                   "pos": pos, "users": users, "queue": queue, "months": months, "register": register,
                   "history": history, "files": files, "seed": seed, "workers": workers},
        "counts": {**counts, **_counts(db)},
        "seconds": seconds,
        "sizes": sizes,
        "computer": {"cores": os.cpu_count(), "python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version},
        "made_at": dt.datetime.now().isoformat(timespec="seconds"),
    }  # fmt: skip
    (out / REPORT_FILE).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


# --- The reference data ----------------------------------------------------------------------------------------------


def _gst(rng: random.Random) -> str:
    """A GST/HST number whose Business Number passes the CRA's check digit."""
    from ap_coder.coding.tax import gst_check_digit_ok

    while True:
        number = f"{rng.randrange(100_000_000, 999_999_999)} RT0001"
        if gst_check_digit_ok(number):
            return number


def _names(rng: random.Random, n: int) -> list[str]:
    combos = [(a, b, c) for a in _WORDS for b in _TRADES for c in _KINDS]
    rng.shuffle(combos)
    names, seen = [], set()
    for i in range(n):
        a, b, c = combos[i % len(combos)]
        name = f"{a} {b} {c} {_SUFFIXES[i % len(_SUFFIXES)]}" + (f" {i // len(combos) + 1}" if i >= len(combos) else "")
        if name not in seen:
            seen.add(name)
            names.append(name)
    return names


def _reference(store: Any, rng: random.Random, vendors: int, master: int, gl: int,
               cost_centers: int) -> list[dict[str, Any]]:  # fmt: skip
    """The sample setup grown to ``gl`` accounts and ``cost_centers`` centers, the review settings; the vendors (not
    yet in the vendor master: ``_master``) as dicts."""
    from ap_coder.coding import banks
    from ap_coder.core.store import load_sample_purchase_orders, load_sample_setup, load_sample_vendor_master

    load_sample_setup(store)
    store.save_bank_accounts(banks.clean_rows(BANK_ACCOUNTS)[0])
    have = {r["code"] for r in store.list_accounts("gl_accounts")}
    categories = ("Operating Expenses", "Cost of Sales", "Fixed Assets", "Prepaids", "Facilities", "Travel")
    rows, code = [], 20_000
    while len(have) + len(rows) < gl:
        code += 7
        rows.append({"gl_code": str(code), "description": f"Expense account {code} - seeded for the volume bench",
                     "category": categories[code % len(categories)], "meals": ""})  # fmt: skip
    store.import_accounts("gl_accounts", rows, "gl_code", "description", "category", meals_column="meals")
    have = {r["code"] for r in store.list_accounts("cost_centers")}
    rows = [{"cost_center": f"CC{n}", "description": f"Site {n}", "category": "Operations"}
            for n in range(1000, 1000 + max(0, cost_centers - len(have)))]  # fmt: skip
    store.import_accounts("cost_centers", rows, "cost_center", "description", "category")
    load_sample_vendor_master(store)  # the templates' own vendors
    load_sample_purchase_orders(store)
    for key, value in (("approval_limit", str(APPROVAL_LIMIT)), ("fx_rates", FX_RATES), ("our_province", "ON")):
        store.set_setting(key, value)
    names = _names(rng, master)
    terms = ("Net 30", "Net 30", "Net 45", "Net 15", "Due on receipt", "2% 10 Net 30")
    return [{"name": name, "gst": _gst(rng), "erp_id": str(200_000 + i), "terms": rng.choice(terms),
             "in_master": i < master} for i, name in enumerate(names)]  # fmt: skip


def _place(found: list[dict[str, Any]], templates: list[dict[str, Any]], rng: random.Random) -> list[Vendor]:
    """Each vendor with a template (its invoices' layout; never the credit note's) and a style of invoice number. The
    first ones have invoices (``_plan`` takes as many as it needs); 5% of those are not in the vendor master."""
    from ap_coder.capture.supplier import supplier_key
    from ap_coder.core.memory import vendor_key

    usable = [n for n, t in enumerate(templates) if t["stem"] != CREDIT_NOTE]
    styles = ("{p}-{n:05d}", "INV-{n:06d}", "{n:07d}", "{p}{n:05d}", "{p}-{y}-{n:04d}")
    out = []
    for i, v in enumerate(found):
        in_master = v["in_master"] and not (i % 20 == 7)  # 1 vendor in 20 with invoices is new to the master
        prefix = "".join(w[0] for w in v["name"].split()[:3]).upper() + str(i % 97)
        out.append(Vendor(v["name"], vendor_key(v["name"]), supplier_key(v["name"], v["gst"], v["erp_id"]
                                                                          if in_master else None),
                          v["gst"], v["erp_id"], v["terms"], usable[i % len(usable)], in_master,
                          styles[i % len(styles)].replace("{p}", prefix)))  # fmt: skip
    rng.shuffle(out)
    return out


def _master(store: Any, vendors: list[Vendor], templates: list[dict[str, Any]], rng: random.Random,
            master: int) -> None:  # fmt: skip
    """The vendor master, by the store's import (``import_vendor_master``): each vendor's ERP ID, GST/HST number (and
    its template's QST and PST numbers and province), terms and default GL; 1 in 100 on hold."""
    rows = []
    for v in vendors:
        if not v.in_master or len(rows) >= master:
            continue
        doc = templates[v.template]["review"]["coding"]
        rows.append({"vendor_name": v.name, "erp_id": v.erp_id, "gst": v.gst, "terms": v.terms,
                     "default_gl": templates[v.template]["default_gl"],
                     "status": "on_hold" if rng.random() < 0.01 else "active",
                     "qst": doc.get("qst_registration_number") or "", "pst": doc.get("pst_registration_number") or "",
                     "province": doc.get("supplier_province") or ""})  # fmt: skip
    store.import_vendor_master(rows)


def _purchase_orders(store: Any, vendors: list[Vendor], templates: list[dict[str, Any]], rng: random.Random,
                     n: int) -> dict[int, list[str]]:  # fmt: skip
    """``n`` POs by the store's import, each for one of the vendors with invoices, its lines its template's (times a
    whole number); {vendor index: its PO numbers}."""
    rows, numbers = [], {}
    for i in range(n):
        v = rng.randrange(len(vendors))
        number = f"PO-{500_000 + i}"
        numbers.setdefault(v, []).append(number)
        k = rng.randint(1, 4)
        for li in templates[vendors[v].template]["review"]["coding"].get("line_items") or []:
            qty = float(li.get("quantity") or 1) * k
            rows.append({"po_number": number, "vendor_name": vendors[v].name, "line_number": li.get("line_number"),
                         "description": li.get("description") or "", "quantity": qty,
                         "unit_price": float(li.get("unit_price") or 0), "amount": round(float(li.get("amount") or 0)
                                                                                         * k, 2),
                         "received_quantity": qty if rng.random() < 0.8 else 0.0,
                         "gl_code": templates[vendors[v].template]["default_gl"], "cost_center": "",
                         "status": "closed" if rng.random() < 0.3 else "open", "amount_only": False,
                         "open_amount": None})  # fmt: skip
    if rows:
        store.import_purchase_orders(rows)
    return numbers


# --- The people ------------------------------------------------------------------------------------------------------


def _hash(password: str) -> str:
    from ap_coder.core.store.users import hash_password

    return hash_password(password)


def _people(store: Any, out: Path, rng: random.Random, n: int, workers: int) -> list[User]:
    """``n`` accounts across the roles (``ROLE_SHARES``), each with its password and its authenticator app set up
    (``totp_on``), none to change its password; the first Admin by ``add_user``, the others written alike in one go
    (scrypt in ``workers`` processes). Written to ``users.json``."""
    from ap_coder.core import authenticator
    from ap_coder.core.store.users import _now

    counts = {role: max(1, round(n * share)) for role, share in ROLE_SHARES}
    counts["clerk"] = n - sum(c for r, c in counts.items() if r != "clerk")
    letters = "abcdefghjkmnpqrstuvwxyz23456789"
    planned = []
    for role in ("admin", "approver", "clerk", "readonly"):
        for i in range(1, counts[role] + 1):
            password = "-".join("".join(rng.choice(letters) for _ in range(4)) for _ in range(4))
            secret = base64.b32encode(bytes(rng.randrange(256) for _ in range(authenticator.SECRET_BYTES))).decode()
            name = f"{rng.choice(_FIRST)} {rng.choice(_LAST)}"
            planned.append((f"{role}{i:03d}", name, role, password, secret))
    first = planned[0]
    store.add_user(first[0], first[3], role=first[2], display_name=first[1], must_change=False)
    if workers > 1:
        with get_context("spawn").Pool(workers) as pool:
            hashes = pool.map(_hash, [p[3] for p in planned[1:]], chunksize=16)
    else:
        hashes = [_hash(p[3]) for p in planned[1:]]
    key = authenticator.load_key(out, create=True)
    with store._conn() as conn:
        conn.execute("UPDATE users SET totp_secret = ?, totp_on = 1 WHERE username = ?",
                     (authenticator.seal_secret(key, first[4]), first[0]))  # fmt: skip
        conn.executemany(
            "INSERT INTO users (username, display_name, role, pw_hash, active, must_change, created_at, totp_secret, "
            "totp_on) VALUES (?, ?, ?, ?, 1, 0, ?, ?, 1)",
            [(p[0], p[1], p[2], h, _now(), authenticator.seal_secret(key, p[4]))
             for p, h in zip(planned[1:], hashes, strict=True)],
        )  # fmt: skip
        ids = dict(conn.execute("SELECT username, id FROM users"))
    people = [User(ids[p[0]], p[0], p[1], p[2], p[3], p[4]) for p in planned]
    listed = [{"username": u.username, "password": u.password, "totp_secret": u.secret, "role": u.role,
               "name": u.name, "id": u.id} for u in people]  # fmt: skip
    (out / USERS_FILE).write_text(json.dumps(listed, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return people


# --- The templates ---------------------------------------------------------------------------------------------------


def _rows(conn: sqlite3.Connection, sql: str, *params: Any) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, params)]


def _templates(store: Any, out: Path, people: list[User]) -> list[dict[str, Any]]:
    """The ten sample invoices through the pipeline, then approved as on the review screen; each one's rows as it
    arrived (``review``) and once approved (``approved``), its vendor's lessons and supplier profile."""
    from dataclasses import replace

    from ap_coder.ai.pipeline import InvoicePipeline, finalise_coding
    from ap_coder.capture.workflow import learn_from_approval
    from ap_coder.coding.line_tax import confirm_codes
    from ap_coder.core.config import Settings
    from ap_coder.core.schema import InvoiceCoding
    from ap_coder.ops.demo import DEMO_INVOICES, SAMPLES_DIR, _ground_truth

    s = Settings()
    settings = replace(s, llm=replace(s.llm, provider="off"))
    reference = store.reference_data()
    pipeline = InvoicePipeline(settings, reference, store=store)
    (out / "invoices").mkdir(exist_ok=True)
    clerk = next(u for u in people if u.role == "clerk")
    found = []
    for demo in DEMO_INVOICES:
        pdf = SAMPLES_DIR / f"{demo.stem}.pdf"
        path = out / "invoices" / pdf.name
        shutil.copy(pdf, path)
        result = pipeline.process(path)
        if result.error or result.invoice_id is None:
            raise ValueError(f"the template {demo.stem} was not read: {result.error}")
        iid = result.invoice_id
        with store._conn() as conn:
            review = {
                "invoice": _rows(conn, "SELECT * FROM invoices WHERE id = ?", iid)[0],
                "capture": (_rows(conn, "SELECT * FROM invoice_capture WHERE invoice_id = ?", iid) or [None])[0],
                "page_read": (_rows(conn, "SELECT * FROM page_reads WHERE invoice_id = ?", iid) or [None])[0],
                "processed": _rows(conn, "SELECT * FROM events WHERE invoice_id = ? AND action = 'processed'", iid)[0],
            }
        review["coding"] = json.loads(review["invoice"]["ai_output"])
        found.append({"stem": demo.stem, "id": iid, "file": str(path), "review": review})
    for t, demo in zip(found, DEMO_INVOICES, strict=True):
        truth = _ground_truth(demo.stem)
        coding = InvoiceCoding.model_validate({k: v for k, v in truth.items() if k != "gl_distribution"})
        final, _ = finalise_coding(coding, reference, settings, store=store, exclude_invoice_id=t["id"])
        confirm_codes(final)
        store.approve_invoice(t["id"], final, clerk.name, open_issues=[], login=f"user:{clerk.id}")
        learn_from_approval(store, t["id"], final, actor=clerk.name)
    for t in found:
        iid = t["id"]
        with store._conn() as conn:
            inv = _rows(conn, "SELECT * FROM invoices WHERE id = ?", iid)[0]
            t["approved"] = {
                "invoice": inv,
                "approved": _rows(conn, "SELECT * FROM events WHERE invoice_id = ? AND action = 'approved'", iid)[0],
                **{table: _rows(conn, f"SELECT * FROM {table} WHERE invoice_id = ?", iid)
                   for table in ("feedback", "lesson_approvals", "reader_outcomes", "supplier_outcomes",
                                 "line_texts")},
            }  # fmt: skip
            t["vendor_key"], t["vendor_name"] = inv["vendor_key"], inv["vendor_name"]
            t["supplier"] = (t["approved"]["supplier_outcomes"] or [{}])[0].get("supplier_key") or ""
            t["lessons"] = _rows(conn, "SELECT * FROM lessons WHERE vendor_key = ?", t["vendor_key"])
            t["profile"] = (_rows(conn, "SELECT * FROM supplier_profiles WHERE key = ?", t["supplier"]) or [None])[0]
        lines = json.loads(inv["final_output"]).get("line_items") or [{}]
        t["default_gl"] = str(lines[0].get("predicted_gl_code") or "")
        t["sha"] = inv["file_sha256"]
    return found


# --- The plan: every clone's id, vendor, number, dates and status -----------------------------------------------------


def _business_days(start: dt.date, end: dt.date) -> list[dt.date]:
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += dt.timedelta(days=1)
    return days


def _at(day: dt.date, seconds: float) -> str:
    return (dt.datetime.combine(day, dt.time(8)) + dt.timedelta(seconds=seconds)).isoformat(timespec="seconds")


def _plan(store: Any, rng: random.Random, n: int, vendors: list[Vendor], templates: list[dict[str, Any]],
          people: list[User], po_numbers: dict[int, list[str]], queue: int, months: int,
          history: float) -> list[Clone]:  # fmt: skip
    """The ``n`` clones, in the order they arrived (their ids): each business day of the last ``months`` months its
    share, between 8:00 and 17:00 (today only once the day is over); the newest ``queue`` in the review queue (parked,
    or waiting for a second approval when over the approval limit), the others approved within three days (a few
    rejected or failed), exported in the next business day's batch but for the newest day's worth, still to export.
    Vendors by a Zipf-like weight: a few big suppliers, many small ones."""
    now = dt.datetime.now().replace(microsecond=0)
    today = now.date()
    last = today if now.hour >= 17 else today - dt.timedelta(days=1)
    days = _business_days(today - dt.timedelta(days=round(30.4 * months)), last)
    with store._conn() as conn:
        first_id = int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM invoices").fetchone()[0]) + 1
        first_batch = int(conn.execute("SELECT COALESCE(MAX(id), 0) FROM export_batches").fetchone()[0]) + 1
    weights = [1 / (r + 1) ** 0.9 for r in range(len(vendors))]
    picks = rng.choices(range(len(vendors)), weights=weights, k=n)
    clerks = [i for i, u in enumerate(people) if u.role in ("clerk", "approver")]
    approvers = [i for i, u in enumerate(people) if u.role in ("approver", "admin")]
    credit = next((i for i, t in enumerate(templates) if t["stem"] == CREDIT_NOTE), None)
    totals = [abs(float(t["review"]["coding"].get("grand_total") or 0))
              * (1.37 if t["review"]["coding"].get("currency") == "USD" else 1.0) for t in templates]  # fmt: skip
    sequence: dict[int, int] = {}
    po_used: dict[int, int] = {}
    batches: dict[dt.date, int] = {}
    per_day = [(d + 1) * n // len(days) - d * n // len(days) for d in range(len(days))]
    ready_count = max(50, n // len(days))  # a business day's approvals
    times = []
    for d, count in enumerate(per_day):
        times += [(d, s) for s in sorted(rng.uniform(0, 9 * 3600) for _ in range(count))]
    plan = []
    for i, ((d, seconds), v) in enumerate(zip(times, picks, strict=True)):
        vendor = vendors[v]
        created = dt.datetime.combine(days[d], dt.time(8)) + dt.timedelta(seconds=seconds)
        t = credit if credit is not None and rng.random() < SHARES["credit"] else vendor.template
        factor = round(math.exp(rng.uniform(*(math.log(f) for f in FACTORS))), 4)
        number_n = sequence[v] = sequence.get(v, rng.randrange(1000, 90_000)) + rng.randint(1, 3)
        number = vendor.prefix.format(n=number_n, y=created.year)
        inv_date = (created.date() - dt.timedelta(days=rng.randint(0, 10))).isoformat()
        po = ""
        if v in po_numbers and rng.random() < SHARES["po"]:
            listed = po_numbers[v]
            po = listed[po_used.get(v, 0) % len(listed)]
            po_used[v] = po_used.get(v, 0) + 1
        over = totals[t] * factor > APPROVAL_LIMIT
        in_queue = i >= n - queue
        ready = i >= n - queue - ready_count  # approved, waiting for the next export
        reviewer = rng.choice(clerks)
        reviewed = min(created + dt.timedelta(hours=rng.uniform(0.3, 72)), now - dt.timedelta(minutes=5))
        status, second, second_at, batch, batch_at, exporter, parked = "approved", None, None, None, None, None, ""
        roll = rng.random()
        if in_queue:
            status, reviewed_at = "review", None
            if over:
                status, reviewed_at = "pending_approval", min(created + dt.timedelta(hours=1), now)
            elif roll < SHARES["parked"]:
                status, parked = (
                    "parked",
                    rng.choice(
                        (
                            "waiting for the buyer",
                            "credit note to come",
                            "PO not received yet",
                            "asked the vendor for a copy",
                        )
                    ),
                )
        elif roll < SHARES["failed"]:
            status, reviewed_at = "failed", None
        elif roll < SHARES["failed"] + SHARES["rejected"]:
            status, reviewed_at = "rejected", reviewed
        else:
            reviewed_at = reviewed
            if over:
                second = rng.choice([a for a in approvers if a != reviewer] or approvers)
                second_at = reviewed + dt.timedelta(hours=rng.uniform(0.5, 24))
                second_at = min(second_at, now - dt.timedelta(minutes=1))
            after = bisect.bisect_right(days, (second_at or reviewed).date())
            export_day = days[after] if after < len(days) else None
            if export_day is not None and export_day < today and not ready:
                batch = batches.setdefault(export_day, first_batch + len(batches))
                batch_at = _at(export_day, 8 * 3600)
                exporter = approvers[export_day.toordinal() % len(approvers)]
        plan.append(Clone(
            first_id + i, t, v, number, created.isoformat(timespec="seconds"), inv_date, factor, status,
            reviewed_at.isoformat(timespec="seconds") if reviewed_at else None,
            reviewer if status != "failed" else None, second, second_at.isoformat(timespec="seconds")
            if second_at else None, batch, batch_at, exporter, parked,
            status in ("approved", "pending_approval") and rng.random() < history, po, rng.choice(clerks),
        ))  # fmt: skip
    return plan


# --- The clones' rows (made in worker processes) ---------------------------------------------------------------------

_CONTEXT: dict[str, Any] = {}
TABLES = ("invoices", "invoice_capture", "page_reads", "feedback", "lesson_approvals", "reader_outcomes",
          "supplier_outcomes", "line_texts")  # fmt: skip


def _init(context: dict[str, Any]) -> None:
    _CONTEXT.update(context)
    logging_off()


def logging_off() -> None:
    import logging

    logging.disable(logging.INFO)


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def _scaled(doc: dict[str, Any], factor: float) -> dict[str, float]:
    """The coding's amounts about ``factor`` times its template's, worked out again as an invoice adds up, so every
    check that adds up still does: each unit price times ``factor`` to the cent and each line its quantity times its
    price, the subtotal their sum (with what the template has beside its lines), each tax its rate times its base,
    the total their sum, the posting lines in proportion with the odd cents on the largest. Returns the stored checks'
    sums (``validation.checks``)."""
    old_sub = _number(doc.get("subtotal")) or 0.0
    lines = doc.get("line_items") or []
    old_lines = sum(_number(li.get("amount")) or 0.0 for li in lines)
    for li in lines:
        quantity, price, amount = (_number(li.get(f)) for f in ("quantity", "unit_price", "amount"))
        if quantity and price is not None and amount is not None and round(quantity * price, 2) == round(amount, 2):
            li["unit_price"] = round(price * factor, 2)
            li["amount"] = round(quantity * li["unit_price"], 2)
        elif amount is not None:
            li["amount"] = round(amount * factor, 2)
    new_lines = round(sum(_number(li.get("amount")) or 0.0 for li in lines), 2)
    new_sub = round(new_lines + (old_sub - old_lines) * factor, 2)
    share = new_sub / old_sub if old_sub else factor
    taxes: dict[float, float] = {}  # a tax's amount before -> after (a posting line of recoverable tax is one)
    for tl in doc.get("tax_lines") or []:
        base, rate, tax = (_number(tl.get(f)) for f in ("taxable_amount", "rate", "tax_amount"))
        new_base = round(base * share, 2) if base is not None else None
        if rate and new_base is not None and tax is not None and abs(rate * base - tax) <= 0.011:
            new_tax = round(rate * new_base, 2)
        else:
            new_tax = round((tax or 0.0) * factor, 2)
        if new_base is not None:
            tl["taxable_amount"] = new_base
        if tax is not None:
            taxes.setdefault(tax, new_tax)
            tl["tax_amount"] = new_tax
    old_total = _number(doc.get("grand_total")) or 0.0
    old_tax = _number(doc.get("tax_total")) or 0.0
    tax_total = round(sum(_number(tl.get("tax_amount")) or 0.0 for tl in doc.get("tax_lines") or []), 2)
    if not doc.get("tax_lines"):
        tax_total = round(old_tax * factor, 2)
    total = round(new_sub + tax_total + (old_total - old_sub - old_tax) * factor, 2)
    doc.update(subtotal=new_sub, tax_total=tax_total, grand_total=total)
    by_line = {li.get("line_number"): _number(li.get("amount")) for li in lines}
    rows = [r for r in doc.get("gl_distribution") or [] if isinstance(r, dict)]
    for r in rows:
        net, extra, amount = (_number(r.get(f)) or 0.0 for f in ("net_amount", "non_recoverable_tax", "amount"))
        if r.get("kind") == "tax":
            r["amount"] = taxes.get(amount, round(amount * factor, 2))
            continue
        new_net = by_line.get(r.get("line_number"))
        new_net = round(net * factor, 2) if new_net is None else new_net
        r["net_amount"] = new_net
        r["non_recoverable_tax"] = round(extra * (new_net / net if net else factor), 2)
        r["amount"] = round(new_net + r["non_recoverable_tax"], 2)
    posted = round(sum(_number(r.get("amount")) or 0.0 for r in rows), 2)
    expenses = [r for r in rows if r.get("kind") != "tax"]
    if expenses and posted != total:  # the odd cents, as the app's split puts them: on one line
        largest = max(expenses, key=lambda r: abs(r["amount"]))
        largest["non_recoverable_tax"] = round(largest["non_recoverable_tax"] + total - posted, 2)
        largest["amount"] = round(largest["net_amount"] + largest["non_recoverable_tax"], 2)
        posted = total
    return {"line_sum": new_lines, "subtotal": new_sub, "subtotal_plus_tax": round(new_sub + tax_total, 2),
            "grand_total": total, "posting_total": posted if rows else total}  # fmt: skip


def _coding(text: str | None, c: Clone, v: Vendor,
            terms_days: int) -> tuple[dict[str, Any] | None, str | None, dict[str, float]]:  # fmt: skip
    """A template's coding (JSON text) as the clone ``c`` of vendor ``v``: (the coding, its due date, its checks'
    sums)."""
    from ap_coder.core.store._base import _due

    if not text:
        return None, None, {}
    doc = json.loads(text)
    doc.update(vendor_name=v.name, invoice_number=c.number, invoice_date=c.invoice_date, po_number=c.po)
    if doc.get("gst_hst_registration_number"):
        doc["gst_hst_registration_number"] = v.gst
    if doc.get("due_date"):
        doc["due_date"] = ""
    sums = _scaled(doc, c.factor)
    return doc, _due(doc, terms_days, v.terms), sums


def _file(c: Clone, template: dict[str, Any]) -> tuple[Path, str]:
    """The clone's file in ``invoices/`` and the SHA-256 its row records: a copy of its template's PDF with a line of
    its own (its own SHA-256), or a hard link to it (``--files link``) with a SHA-256 of its own all the same, as a
    file of its own would have: the bytes are its template's, so hashing the file finds the template (an import of it
    again is "already in AP Coder"), and no SHA-256 is shared by thousands of rows, which no real folder has."""
    folder = Path(_CONTEXT["folder"]) / "invoices"
    name = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in c.number)
    path = folder / f"{c.id:07d}_{name}.pdf"
    if _CONTEXT["files"] == "copy":
        data = Path(template["file"]).read_bytes() + f"\n% {c.id}\n".encode()
        path.write_bytes(data)
        return path, hashlib.sha256(data).hexdigest()
    os.link(template["file"], path)
    return path, hashlib.sha256(f"{template['sha']}/{c.id}".encode()).hexdigest()


def _changed(value: Any, field: str, c: Clone, v: Vendor, t: dict[str, Any]) -> Any:
    """A learning row's value of ``field`` as the clone's (its vendor's name, its number); others as the template's."""
    if field == "vendor_name":
        return v.name if value is not None else value
    if field == "invoice_number":
        return c.number if value is not None else value
    if field == "invoice_date":
        return c.invoice_date if value is not None else value
    return value


def _clone_rows(clones: list[Clone]) -> dict[str, list[tuple[Any, ...]]]:
    """The rows of ``clones``, by table (``TABLES``, and ``events`` and ``files``), in each table's column order."""
    ctx = _CONTEXT
    templates, vendors, people, columns = ctx["templates"], ctx["vendors"], ctx["people"], ctx["columns"]
    out: dict[str, list[tuple[Any, ...]]] = {t: [] for t in (*TABLES, "events")}
    for c in clones:
        t, v = templates[c.template], vendors[c.vendor]
        signed = c.status in ("approved", "pending_approval")
        base = t["approved"] if signed else t["review"]
        path, sha = _file(c, t)
        row = dict(base["invoice"])
        ai, due, sums = _coding(t["review"]["invoice"]["ai_output"], c, v, 30)
        final, final_due, final_sums = (
            _coding(base["invoice"]["final_output"], c, v, 30) if signed else (None, None, {})
        )
        doc = final or ai or {}
        validation = json.loads(base["invoice"]["validation"] or "{}")
        validation.update(checks_stamp=ctx["stamp"], source=str(path))
        checks = validation.get("checks") or {}
        for f, value in (final_sums or sums).items():
            if isinstance(checks.get(f), int | float):
                checks[f] = value
        meta = json.loads(base["invoice"]["meta"] or "{}")
        meta["source"] = str(path)
        reviewer = people[c.reviewer] if c.reviewer is not None else None
        row.update(
            id=c.id, source_path=str(path), file_name=path.name, file_sha256=sha, status=c.status,
            vendor_name=v.name, vendor_key=v.key, invoice_number=c.number, invoice_date=c.invoice_date,
            grand_total=doc.get("grand_total"), ai_output=json.dumps(ai), validation=json.dumps(validation),
            final_output=json.dumps(final) if final else None, meta=json.dumps(meta), created_at=c.created,
            reviewed_at=c.reviewed, reviewer=reviewer.name if reviewer and c.reviewed else None,
            export_batch=c.batch, po_key=_po_key(c.po), due_date=final_due or due,
            second_reviewer=people[c.second].name if c.second is not None else None, second_reviewed_at=c.second_at,
            parked_reason=c.parked or None, follow_up="" if c.parked else None, edits=row["edits"] if signed else None,
        )  # fmt: skip
        if c.status == "failed":
            row.update(ai_output=None, validation=None, final_output=None, vendor_name=None, vendor_key=None,
                       invoice_number=None, invoice_date=None, grand_total=None, currency=None, po_key="",
                       due_date=None, model_confidence=None, adjusted_confidence=None, requires_review=1,
                       error="The file could not be read: it may be damaged or protected by a password.")  # fmt: skip
        out["invoices"].append(tuple(row.get(col) for col in columns))
        uploader = people[c.uploader].name
        events = out["events"]
        if c.status == "failed":
            events.append((c.id, "failed", uploader, json.dumps({"file": path.name, "error": row["error"]}), c.created))
            continue
        processed = json.loads(t["review"]["processed"]["detail"])
        processed.update(file=path.name, vendor=v.name, invoice_number=c.number, total=(ai or {}).get("grand_total"))
        events.append((c.id, "processed", uploader, json.dumps(processed), c.created))
        cap = t["review"]["capture"]
        if cap is not None:
            out["invoice_capture"].append((c.id, cap["capture_json"], cap["layout_source"], c.created,
                                           cap.get("name_status"), cap.get("credit_note")))  # fmt: skip
        read = t["review"]["page_read"]
        if read is not None:
            out["page_reads"].append((c.id, "done", "seed", read["pages"] or 1, 12.5, read["reason"], "", "",
                                      c.created, c.created, "", 0))  # fmt: skip
        if c.status == "parked":
            events.append((c.id, "parked", uploader, json.dumps({"reason": c.parked, "follow_up": ""}), c.created))
        if c.status == "rejected" and reviewer is not None:
            events.append((c.id, "rejected", reviewer.name, json.dumps({"reason": "not our invoice"}), c.reviewed))
        if not signed or reviewer is None:
            continue
        approved = json.loads(t["approved"]["approved"]["detail"])
        approved.update(total=doc.get("grand_total"), login=f"user:{reviewer.id}", user_id=reviewer.id)
        if c.status == "pending_approval" or c.second is not None:
            approved.update(needs_second=True, second_reason=f"over the approval limit of {APPROVAL_LIMIT:,.2f} CAD")
        events.append((c.id, "approved", reviewer.name, json.dumps(approved), c.reviewed))
        if c.second is not None:
            second = people[c.second]
            events.append((c.id, "final_approved", second.name, json.dumps({
                "first_approver": reviewer.name, "total": doc.get("grand_total"),
                "second_reason": approved["second_reason"], "login": f"user:{second.id}", "user_id": second.id,
            }), c.second_at))  # fmt: skip
        if c.batch is not None:
            events.append((c.id, "exported", people[c.exporter].name, json.dumps({
                "batch": c.batch, "format": "csv", "total": doc.get("grand_total"),
                "currency": (doc.get("currency") or "CAD").upper()}), c.batch_at))  # fmt: skip
        amounts = {li.get("line_number"): li.get("amount") for li in doc.get("line_items") or []}
        for f in t["approved"]["feedback"]:
            amount = amounts.get(f["line_number"], f["amount"])  # the line's, as approved
            out["feedback"].append((c.id, f["line_number"], v.key, v.name, f["description"], amount, f["suggested_gl"],
                                    f["final_gl"], f["suggested_cc"], f["final_cc"], f["outcome"], reviewer.name,
                                    c.reviewed, f["final_tax_code"], f["tax_source"], f["tax_province"],
                                    f["tax_uncollected"]))  # fmt: skip
        if not c.history:
            continue
        old = t["vendor_key"] + "|"
        for a in t["approved"]["lesson_approvals"]:
            situation = v.key + "|" + a["situation"][len(old) :] if a["situation"].startswith(old) else a["situation"]
            field = a["situation"].split("|")[2] if a["situation"].count("|") >= 2 else ""
            out["lesson_approvals"].append((c.id, situation, a["corrected"], a["confirmed"],
                                            _changed(a["value"], field, c, v, t),
                                            _changed(a["proposed"], field, c, v, t), c.reviewed))  # fmt: skip
        for r in t["approved"]["reader_outcomes"]:
            read, final = (_changed(r[f], r["field"], c, v, t) for f in ("read_value", "final_value"))
            out["reader_outcomes"].append((c.id, r["reader"], r["field"], read, final, r["correct"], r["evidence"],
                                           r["layout_source"], r["status"], c.reviewed))  # fmt: skip
        for r in t["approved"]["supplier_outcomes"]:
            values = [r["ai_value"], r["final_value"]]
            if r["field"] in ("vendor_name", "invoice_number", "invoice_date"):
                values = [json.dumps(_changed(json.loads(x), r["field"], c, v, t)) if x else x for x in values]
            out["supplier_outcomes"].append((v.supplier, c.id, r["field"], *values, r["correct"], r["source"],
                                             c.reviewed))  # fmt: skip
        for r in t["approved"]["line_texts"]:
            out["line_texts"].append((v.supplier, r["read_key"], r["printed"], r["description"], r["corrected"], c.id,
                                      c.reviewed))  # fmt: skip
    return out


def _po_key(number: str) -> str:
    from ap_coder.coding.po import po_key

    return po_key(number)


_INSERTS = {
    "invoice_capture": "INSERT INTO invoice_capture (invoice_id, capture_json, layout_source, created_at, name_status, "
                       "credit_note) VALUES (?, ?, ?, ?, ?, ?)",
    "page_reads": "INSERT INTO page_reads (invoice_id, status, model, pages, seconds, reason, error, requested_by, "
                  "created_at, updated_at, reader, tries) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    "feedback": "INSERT INTO feedback (invoice_id, line_number, vendor_key, vendor_name, description, amount, "
                "suggested_gl, final_gl, suggested_cc, final_cc, outcome, reviewer, created_at, final_tax_code, "
                "tax_source, tax_province, tax_uncollected) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    "lesson_approvals": "INSERT OR IGNORE INTO lesson_approvals (invoice_id, situation, corrected, confirmed, value, "
                        "proposed, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
    "reader_outcomes": "INSERT INTO reader_outcomes (invoice_id, reader, field, read_value, final_value, correct, "
                       "evidence, layout_source, status, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
    "supplier_outcomes": "INSERT INTO supplier_outcomes (supplier_key, invoice_id, field, ai_value, final_value, "
                         "correct, source, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
    "line_texts": "INSERT INTO line_texts (supplier_key, read_key, printed, description, corrected, invoice_id, at) "
                  "VALUES (?, ?, ?, ?, ?, ?, ?)",
    "events": "INSERT INTO events (invoice_id, action, actor, detail, created_at) VALUES (?, ?, ?, ?, ?)",
}  # fmt: skip


def _bulk(db: Path) -> sqlite3.Connection:
    """A connection for the bulk writes: no rollback journal, no syncing (a build cut short is made again)."""
    conn = sqlite3.connect(db, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA cache_size=-262144")  # 256 MB
    return conn


def _write(store: Any, out: Path, plan: list[Clone], templates: list[dict[str, Any]], vendors: list[Vendor],
           people: list[User], files: str, workers: int) -> tuple[dict[str, int], dict[int, float]]:  # fmt: skip
    """Every clone's rows (made by ``workers`` processes, ``_clone_rows``), its export batches, its vendors' lessons
    and supplier profiles, and the people's logins; the events and lessons written in the order of their time, as the
    app writes them. (what was written by table, each clone's grand total)"""
    stamp = store.checks_stamp()
    with store._conn() as conn:
        columns = [r[1] for r in conn.execute("PRAGMA table_info(invoices)")]
    context = {"templates": templates, "vendors": vendors, "people": people, "columns": columns, "stamp": stamp,
               "folder": str(out), "files": files}  # fmt: skip
    chunks = [plan[i : i + CHUNK] for i in range(0, len(plan), CHUNK)]
    marks = ", ".join("?" * len(columns))  # the invoices' columns: at most 40
    insert_invoices = f"INSERT INTO invoices ({', '.join(columns)}) VALUES ({marks})"
    counts = {"clones": len(plan), **dict.fromkeys((*TABLES[1:], "events"), 0)}
    events: list[tuple[Any, ...]] = []
    totals: dict[int, float] = {}
    at_id, at_total = columns.index("id"), columns.index("grand_total")
    conn = _bulk(store.path)
    try:
        if workers > 1:
            pool = get_context("spawn").Pool(workers, initializer=_init, initargs=(context,))
            results = pool.imap(_clone_rows, chunks)
        else:
            pool = None
            _init(context)
            results = map(_clone_rows, chunks)
        try:
            for rows in results:
                conn.execute("BEGIN")
                invoices = rows.pop("invoices")
                conn.executemany(insert_invoices, invoices)
                totals.update((r[at_id], r[at_total]) for r in invoices if r[at_total] is not None)
                for table, found in rows.items():
                    counts[table] += len(found)
                    if table == "events":
                        events += found
                    else:
                        conn.executemany(_INSERTS[table], found)
                conn.execute("COMMIT")
        finally:
            if pool is not None:
                pool.close()
                pool.join()
        events += _logins(plan, people)
        counts["events"] = len(events)
        events.sort(key=lambda e: e[4])
        conn.execute("BEGIN")
        conn.executemany(_INSERTS["events"], events)
        _batches(conn, plan, totals, templates, people)
        counts["lessons"], counts["supplier_profiles"] = _lessons(conn, plan, templates, vendors, people)
        conn.execute("COMMIT")
    finally:
        conn.close()
    return counts, totals


def _logins(plan: list[Clone], people: list[User]) -> list[tuple[Any, ...]]:
    """The people's logins and logouts: about 1.2 events per invoice, on the days they arrived."""
    rng = random.Random(len(plan))
    out = []
    for c in rng.sample(plan, int(len(plan) * 0.6)):
        u = people[rng.randrange(len(people))]
        at = dt.datetime.fromisoformat(c.created)
        out.append((None, "login", u.name, json.dumps({"username": u.username}), (at - dt.timedelta(minutes=20))
                    .isoformat(timespec="seconds")))  # fmt: skip
        out.append((None, "logout", u.name, json.dumps({"username": u.username}), (at + dt.timedelta(hours=3))
                    .isoformat(timespec="seconds")))  # fmt: skip
    return out


def _batches(conn: sqlite3.Connection, plan: list[Clone], totals: dict[int, float], templates: list[dict[str, Any]],
             people: list[User]) -> None:  # fmt: skip
    """One export batch per business day (CSV), with its invoices' count and total."""
    found: dict[int, list[Clone]] = {}
    for c in plan:
        if c.batch is not None:
            found.setdefault(c.batch, []).append(c)
    rows = []
    for batch, clones in sorted(found.items()):
        total = sum(totals.get(c.id, 0.0) for c in clones)
        mixed = len({templates[c.template]["review"]["coding"].get("currency") or "CAD" for c in clones}) > 1
        rows.append((batch, clones[0].batch_at, people[clones[0].exporter].name, "csv", len(clones),
                     None if mixed else round(total, 2)))  # fmt: skip
    conn.executemany("INSERT INTO export_batches (id, created_at, actor, format, invoices, total) "
                     "VALUES (?, ?, ?, ?, ?, ?)", rows)  # fmt: skip


def _lessons(conn: sqlite3.Connection, plan: list[Clone], templates: list[dict[str, Any]], vendors: list[Vendor],
             people: list[User]) -> tuple[int, int]:  # fmt: skip
    """Each vendor's lessons (its template's, seen once per approval) and supplier profile, for the vendors with an
    approval that taught (``history``)."""
    taught: dict[int, list[Clone]] = {}
    for c in plan:
        if c.history:
            taught.setdefault(c.vendor, []).append(c)
    lessons, profiles = [], []
    for v, clones in taught.items():
        vendor, t = vendors[v], templates[vendors[v].template]
        old, last = t["vendor_key"] + "|", clones[-1]
        for lesson in t["lessons"]:
            situation = lesson["situation"]
            situation = vendor.key + "|" + situation[len(old) :] if situation.startswith(old) else situation
            field = lesson["field"]
            lessons.append(
                (
                    situation,
                    vendor.key,
                    vendor.name,
                    lesson["kind"],
                    field,
                    lesson["pattern"],
                    _changed(lesson["value"], field, last, vendor, t),
                    _changed(lesson["proposed"], field, last, vendor, t),
                    len(clones),
                    lesson["corrected"],
                    lesson["since_why"],
                    lesson["confirmed"],
                    0,
                    last.id,
                    clones[0].reviewed,
                    last.reviewed,
                )
            )
        if t["profile"] is not None:
            p = t["profile"]
            profiles.append((vendor.supplier, vendor.name, vendor.erp_id if vendor.in_master else "", p["state"],
                             p["template_json"], last.reviewed, people[last.reviewer].name))  # fmt: skip
    conn.executemany(
        "INSERT OR IGNORE INTO lessons (situation, vendor_key, vendor_name, kind, field, pattern, value, proposed, "
        "seen, corrected, since_why, confirmed, retired, last_invoice, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        lessons,
    )
    conn.executemany("INSERT OR IGNORE INTO supplier_profiles (key, display_name, vendor_id, state, template_json, "
                     "updated_at, updated_by) VALUES (?, ?, ?, ?, ?, ?, ?)", profiles)  # fmt: skip
    return len(lessons), len(profiles)


# --- The register, the summary, the sizes ----------------------------------------------------------------------------


def _register(store: Any, plan: list[Clone], totals: dict[int, float], vendors: list[Vendor], rng: random.Random,
              n: int) -> int:  # fmt: skip
    """The JD Edwards register (``import_erp_register``, as the nightly sync writes it): every exported invoice, then
    older vouchers of the vendor master's vendors (the two years before AP Coder, the biggest suppliers first) up to
    ``n`` rows."""
    if n <= 0:
        return 0
    rows = []
    for c in plan:
        if c.batch is not None and len(rows) < n:
            rows.append({"vendor_name": vendors[c.vendor].name, "invoice_number": c.number,
                         "total": totals[c.id], "invoice_date": c.invoice_date})  # fmt: skip
    weights = [1 / (r + 1) ** 0.9 for r in range(len(vendors))]
    start = dt.date.today() - dt.timedelta(days=3 * 365)
    for i, v in enumerate(rng.choices(range(len(vendors)), weights=weights, k=max(0, n - len(rows)))):
        rows.append({"vendor_name": vendors[v].name, "invoice_number": f"H{i:07d}",
                     "total": round(rng.uniform(20, 15_000), 2),
                     "invoice_date": (start + dt.timedelta(days=rng.randrange(730))).isoformat()})  # fmt: skip
    return store.import_erp_register(rows)


def _summary(store: Any) -> None:
    """The invoices' summary of every invoice the triggers marked, worked out by the store itself (``_refresh_summary``,
    on the bulk connection), then the database back in WAL mode as the app keeps it."""
    conn = _bulk(store.path)
    try:
        conn.execute("BEGIN")
        store._refresh_summary(conn)
        conn.execute("COMMIT")
        conn.execute("PRAGMA journal_mode=WAL")
    finally:
        conn.close()
    store.status_counts()  # a store connection: nothing left to work out


MARKER_NOTE = "a stand-in for the day's backup, made by ap_coder.bench seed: not a backup, never restore it"


def mark_backed_up(out: Path) -> Path:
    """A stand-in for the day's backup in ``out/backups``: ``ap_coder-<now>.db`` (a database of one setting saying
    what it is) and its list of no files. The app's daily backup (``Store.auto_backup``: when the dashboard or the
    server's background process starts, and every hour) makes none while a daily backup under 20 hours old is there,
    so serving a big seeded folder never copies its database whole (2.2 GB at 100,000 invoices) and fills the disk.
    It lasts 20 hours: write it again before each run (``seed --out DIR --mark-backed-up``). Its name and list are
    the app's own (``backups.DAILY``, ``backup_files.list_path``)."""
    from ap_coder.core.store import backup_files

    folder = out / "backups"
    folder.mkdir(exist_ok=True)
    path = folder / f"ap_coder-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    path.unlink(missing_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute("CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO settings (key, value) VALUES ('bench_marker', ?)", (MARKER_NOTE,))
        conn.commit()
    finally:
        conn.close()
    listed = {"files": [], "note": MARKER_NOTE}
    backup_files.list_path(path).write_text(json.dumps(listed) + "\n", encoding="utf-8")
    return path


def _counts(db: Path) -> dict[str, int]:
    conn = sqlite3.connect(db)
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE "
                "'invoice_search_%' AND name != 'sqlite_sequence' ORDER BY name"
            )
        ]
        return {f"rows/{t}": int(conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]) for t in tables}
    finally:
        conn.close()


def _sizes(db: Path, out: Path, invoices: int) -> dict[str, Any]:
    """The database's size (after a checkpoint), per 1,000 invoices, by table (``dbstat``, where this SQLite has it)
    and the invoice files' blocks on disk (a hard link counted once)."""
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        try:
            tables = {r[0]: r[1] for r in conn.execute("SELECT name, SUM(pgsize) FROM dbstat GROUP BY name "
                                                       "ORDER BY 2 DESC")}  # fmt: skip
        except sqlite3.Error:
            tables = {}
    finally:
        conn.close()
    seen, blocks = set(), 0
    for entry in os.scandir(out / "invoices"):
        st = entry.stat(follow_symlinks=False)
        if st.st_ino not in seen:
            seen.add(st.st_ino)
            blocks += getattr(st, "st_blocks", math.ceil(st.st_size / 512)) * 512
    size = db.stat().st_size
    return {
        "database_mb": round(size / 1e6, 1),
        "per_1000_invoices_mb": round(size / 1e6 / invoices * 1000, 2),
        "files_mb": round(blocks / 1e6, 1),
        "tables_mb": {name: round(n / 1e6, 2) for name, n in tables.items() if n >= 1e5},
    }
