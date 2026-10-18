"""Older AP Coder versions, installed and used the way their README said, to test the update over them.

Every version a user may have is main after a pull request was merged (``VERSIONS``; docs/UPGRADING.md says how
each was installed and what the update does with it). This tool sets one up for real and checks the update:

    python scripts/ci/old_versions.py list
    python scripts/ci/old_versions.py extract SHA FOLDER            # its code (git archive), as its ZIP held it
    python scripts/ci/old_versions.py old-wheelhouse FOLDER         # an older Windows bundle's wheelhouse, copied in
    python scripts/ci/old_versions.py install FOLDER --how HOW      # as its README said (downloads from PyPI)
    python scripts/ci/old_versions.py start FOLDER --how HOW --log FILE [--port N]   # its dashboard, left running
    python scripts/ci/old_versions.py seed FOLDER --how HOW --out BEFORE.json        # use it: see below
    python scripts/ci/old_versions.py unzip BUNDLE.zip FOLDER       # the new version (a new folder, or over it)
    python scripts/ci/old_versions.py check BEFORE.json --new FOLDER --port N [...]  # after its double-click
    python scripts/ci/old_versions.py scenario SHA --how HOW --where new-folder|beside|over --bundle ZIP --work DIR
                                                                    # all of it in a scratch home (Linux, Mac)

``HOW`` is how its README had it installed and started:

* ``cli``: ``pip install -e ".[dev]"`` in a ``.venv``, ``cp .env.example .env`` (the first version: a command line,
  no dashboard, no database);
* ``by-hand``: the same, then ``python -m ap_coder dashboard``: the data in the code folder's ``private/``;
* ``installer``: ``install.sh`` / ``install.bat`` (data folder ``~/APCoder``, recorded in ``~/.ap_coder``), then
  ``start.sh`` / ``start.bat`` (or the desktop shortcut);
* ``launcher``: the double-click, ``APProcessor.command`` / ``APProcessor.bat``: sets itself up and starts.

``seed`` uses the old version with its own code (run by its own ``.venv``): the demo (or sample) invoices, one invoice
"uploaded" into the data folder, one approved with a line's GL account corrected (the learning memory), the vendor
taught (a note, a coding rule), an export batch, a setting, and ``.env`` values. What it made is written to
``BEFORE.json``; ``check`` compares the new version's data, web pages and files with it.

``check`` needs only the standard library (any Python 3.10 or newer); ``seed`` runs inside the old version.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
WINDOWS = os.name == "nt"
REVIEWER = "Ann Reviewer"
VENDOR_NOTE = "Taught before the update"
RULE_WORDS = "upgrade-check"
SETTING = ("approval_limit", "25000")
# .env values set in the old version: each must still be in the data folder's .env after the update.
ENV_VALUES = {"AP_REVIEWER": REVIEWER, "AP_REVIEW_THRESHOLD": "0.9"}
RETIRED = "AP_PAGE_READER=on"  # a setting the new version no longer uses: commented out, with a note

# main after each merged pull request (and the first commit): what a user may have installed.
VERSIONS = [
    {"sha": "81f691e", "label": "first commit", "how": ["cli"], "schema": None},
    {"sha": "db95511", "label": "PR #1", "how": ["installer", "by-hand"], "schema": 2},
    {"sha": "b968b74", "label": "PR #2", "how": ["installer", "by-hand"], "schema": 13},
    {"sha": "4366f35", "label": "PR #3", "how": ["launcher", "installer"], "schema": 14},
    {"sha": "e2a20cf", "label": "PR #4", "how": ["launcher"], "schema": 14},
    {"sha": "a798f79", "label": "PR #5", "how": ["launcher"], "schema": 15},
    {"sha": "abbc331", "label": "PR #6", "how": ["launcher"], "schema": 15},
    {"sha": "0de8afe", "label": "PR #7", "how": ["launcher"], "schema": 15},
    {"sha": "e1cf61a", "label": "PR #8", "how": ["launcher"], "schema": 15},
]
# The pages of the new web app every update must still show with the old data.
PAGES = ["/", "/review", "/learning", "/exports", "/vendors", "/accounts", "/activity", "/search", "/settings"]


def say(message: str) -> None:
    print(message, flush=True)


def venv_python(folder: Path) -> Path:
    return folder / ".venv" / ("Scripts/python.exe" if WINDOWS else "bin/python")


def run(cmd: list[str], cwd: Path | None = None, **kwargs) -> None:
    say(f"$ {' '.join(cmd)}")
    subprocess.run(cmd, cwd=cwd, check=True, **kwargs)


# --- Its code -----------------------------------------------------------------------------------------------------


def extract(sha: str, folder: Path, bundle: bool = False) -> None:
    """``sha``'s files (what its ZIP or checkout held), in ``folder``; ``bundle``: as its offline bundle unzipped them,
    with the bundle_manifest.json its builder wrote (its version and build time, no file list)."""
    import io
    import tarfile

    folder.mkdir(parents=True, exist_ok=True)
    archive = subprocess.run(["git", "archive", "--format=tar", sha], cwd=ROOT, capture_output=True, check=True)
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(folder, filter="data") if sys.version_info >= (3, 12) else tar.extractall(folder)  # noqa: S202
    for script in [*folder.glob("*.command"), *folder.glob("*.sh")]:
        script.chmod(0o755)  # (git archive keeps the mode; a ZIP unpacked on a Mac does too)
    if bundle:
        built = subprocess.run(["git", "log", "-1", "--format=%cI", sha], cwd=ROOT, capture_output=True, text=True)
        manifest = {"app": "AP Coder", "version": "0.1.0", "built": built.stdout.strip()[:19]}
        (folder / "bundle_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


# An older Windows bundle's wheelhouse (Python 3.11 and 3.12, win_amd64), as someone copies it next to GitHub's ZIP
# (the pilot tester: as ".wheelhouse", on a Mac with Python 3.14). Its compiled wheels stand in by name only: none fits
# a Mac or Linux, and AP Coder must say so in one sentence instead of installing anything or listing packages.
OLD_WINDOWS_WHEELS = [
    "numpy-2.1.3-{t}-{t}-win_amd64.whl", "pandas-2.2.3-{t}-{t}-win_amd64.whl", "pyarrow-18.1.0-{t}-{t}-win_amd64.whl",
    "pillow-11.0.0-{t}-{t}-win_amd64.whl", "pydantic_core-2.27.1-{t}-{t}-win_amd64.whl",
    "onnxruntime-1.20.1-{t}-{t}-win_amd64.whl", "pymupdf-1.24.14-cp39-abi3-win_amd64.whl",
    "streamlit-1.40.2-py2.py3-none-any.whl", "python_dotenv-1.0.1-py3-none-any.whl",
    "colorama-0.4.6-py2.py3-none-any.whl",
]  # fmt: skip


def old_wheelhouse(folder: Path) -> None:
    """``folder`` as an older Windows bundle's wheelhouse (each wheel a valid, empty one)."""
    import zipfile

    folder.mkdir(parents=True, exist_ok=True)
    for pattern in OLD_WINDOWS_WHEELS:
        for tag in ("cp311", "cp312"):
            name = pattern.format(t=tag)
            project, version = name.split("-")[:2]
            with zipfile.ZipFile(folder / name, "w") as wheel:
                info = f"{project}-{version}.dist-info"
                wheel.writestr(f"{info}/METADATA", f"Metadata-Version: 2.1\nName: {project}\nVersion: {version}\n")
                wheel.writestr(f"{info}/WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: false\n")
                wheel.writestr(f"{info}/RECORD", "")
    say(f"An older Windows bundle's wheelhouse: {len(list(folder.glob('*.whl')))} wheels in {folder}")


# --- Installed as its README said ---------------------------------------------------------------------------------


def install(folder: Path, how: str) -> None:
    """Install the old version in ``folder`` the way its README said (from PyPI: the old versions had no offline
    bundle, or one that is not at hand). ``launcher``: its double-click installs and starts in one go (``start``)."""
    if how in ("cli", "by-hand"):
        import shutil

        names = ("python",) if WINDOWS else ("python3.12", "python3", "python")  # (python3.exe: the Store's stub)
        python = os.environ.get("AP_OLD_PYTHON") or next(filter(None, map(shutil.which, names)), sys.executable)
        run([python, "-m", "venv", str(folder / ".venv")])  # python -m venv .venv
        run([str(venv_python(folder)), "-m", "pip", "install", "--disable-pip-version-check", "-q", "-e", ".[dev]"],
            cwd=folder)  # fmt: skip
        example = folder / ".env.example"
        if example.is_file():  # cp .env.example .env
            (folder / ".env").write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    elif how == "installer":
        args = ["--yes", "--skip-tests", "--no-start"]
        if WINDOWS:
            run(["cmd", "/c", "install.bat", *args], cwd=folder, env={**os.environ, "AP_NO_PAUSE": "1"})
        else:
            run(["bash", "install.sh", *args], cwd=folder)
    elif how != "launcher":
        raise SystemExit(f"unknown --how {how}")


def start_command(folder: Path, how: str, port: int | None) -> list[str]:
    """How its README started the dashboard."""
    extra = ["--port", str(port)] if port else []
    if how == "launcher":
        return ["cmd", "/c", "APProcessor.bat", *extra] if WINDOWS else ["bash", "APProcessor.command", *extra]
    if how == "installer" and not port:
        return ["cmd", "/c", "start.bat"] if WINDOWS else ["bash", "start.sh"]
    return [str(venv_python(folder)), "-m", "ap_coder", "dashboard", *extra]


def streamlit_answers(port: int) -> bool:
    """AP Coder 0.1's dashboard answers on ``port`` (the font only it serves)."""
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(f"http://127.0.0.1:{port}/app/static/InterVariable.woff2", timeout=3) as response:
            return response.status == 200
    except OSError:
        return False


def start(folder: Path, how: str, log: Path, port: int | None, wait: float) -> int:
    """Start its dashboard in the background (it keeps running after this returns) and wait until it answers.
    Writes ``<log>.pid`` (its process group). Returns the port."""
    cmd = start_command(folder, how, port)
    say(f"$ {' '.join(cmd)}  (in the background, output in {log})")
    busy = {p for p in range(8501, 8511) if streamlit_answers(p)}  # (a second window takes the next free port)
    flags = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if WINDOWS else {"start_new_session": True}
    with open(log, "wb") as out:
        proc = subprocess.Popen(cmd, cwd=folder, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                **flags)  # fmt: skip
    Path(f"{log}.pid").write_text(str(proc.pid), encoding="utf-8")
    deadline = time.monotonic() + wait
    candidates = [port] if port else [p for p in range(8501, 8511) if p not in busy]
    while time.monotonic() < deadline:
        for candidate in candidates:
            if streamlit_answers(candidate):
                say(f"The old dashboard answers on port {candidate}.")
                return candidate
        if proc.poll() is not None:
            break
        time.sleep(2)
    say(log.read_text(encoding="utf-8", errors="replace")[-4000:])
    raise SystemExit("the old dashboard did not start")


# --- Used, with its own code ----------------------------------------------------------------------------------------


def seed(folder: Path, how: str, out: Path) -> None:
    """Run :func:`seed_inside` with the old version's own Python and code."""
    run([str(venv_python(folder)), str(Path(__file__).resolve()), "seed-inside", str(folder), "--how", how,
         "--out", str(out)], cwd=folder)  # fmt: skip


def _try(call, *args, **kwargs):
    """``call(*args, **kwargs)``, without the keywords an older signature does not take."""
    try:
        return call(*args, **kwargs)
    except TypeError:
        return call(*args)


def _sample_invoices(store, folder: Path, data: Path) -> None:
    """The first dashboard version had no demo: its samples were uploaded (*Process invoices*: saved into the data
    folder's invoices/) and read through Azure. The same result, without Azure: each sample saved there, its answer
    (samples/ground_truth) finalised by its own pipeline code and put in the queue."""
    from ap_coder.config import Settings
    from ap_coder.pipeline import finalise_coding
    from ap_coder.schema import InvoiceCoding
    from ap_coder.store import load_sample_setup

    if not store.list_accounts("gl_accounts"):
        load_sample_setup(store)
    for pdf in sorted((folder / "samples").glob("*.pdf")):
        truth_file = folder / "samples" / "ground_truth" / f"{pdf.stem}.json"
        if not truth_file.is_file():
            continue
        coding = InvoiceCoding.model_validate(json.loads(truth_file.read_text(encoding="utf-8")))
        output, report = finalise_coding(coding, store.reference_data(), Settings.from_env(), store=store)
        md = pdf.with_suffix(".md")
        upload = data / "invoices" / pdf.name
        upload.parent.mkdir(parents=True, exist_ok=True)
        upload.write_bytes(pdf.read_bytes())
        store.add_invoice(upload, output, report.to_dict(), extraction_md=md.read_text(encoding="utf-8") if md.is_file()
                          else "", meta={"source": str(upload), "status": "ok"})  # fmt: skip


def _upload(store, folder: Path, data: Path) -> None:
    """An invoice the user added: a sample copied into the data folder's invoices/ (a copy with other bytes, so it
    is not the demo's file) and read by the old version's own pipeline, offline when it could; the first versions
    read invoices with Azure only: then its answer is put in the queue as that pipeline would have."""
    sample = next(p for p in sorted((folder / "samples").glob("*.pdf")) if "pacific" in p.name)
    target = data / "invoices" / "uploaded_pacific.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(sample.read_bytes() + b"\n%uploaded before the update\n")
    before = len(store.list_invoices())
    result = subprocess.run([sys.executable, "-m", "ap_coder", "process", str(target)], cwd=folder,
                            capture_output=True, text=True, timeout=600)  # fmt: skip
    if len(store.list_invoices()) > before:
        return
    say(f"(the old pipeline did not read it offline: {(result.stderr or result.stdout).strip()[-300:]})")
    from ap_coder.config import Settings
    from ap_coder.pipeline import finalise_coding
    from ap_coder.schema import InvoiceCoding

    truth = json.loads((folder / "samples" / "ground_truth" / f"{sample.stem}.json").read_text(encoding="utf-8"))
    coding = InvoiceCoding.model_validate(truth)
    output, report = _try(finalise_coding, coding, store.reference_data(), Settings.from_env(), store=store)
    md = sample.with_suffix(".md")
    store.add_invoice(target, output, report.to_dict(), extraction_md=md.read_text(encoding="utf-8"),
                      meta={"source": str(target), "status": "ok"})  # fmt: skip


def _approve_corrected(store, reviewer: str) -> tuple[int, str, str]:
    """Approve the first invoice waiting for review, with its first line's GL account corrected (what teaches the
    learning memory). Returns (invoice id, vendor, corrected GL)."""
    accounts = [a["code"] if "code" in a else next(iter(a.values())) for a in store.list_accounts("gl_accounts")]
    rows = sorted(store.list_invoices(), key=lambda r: "uploaded" not in str(r.get("file_name")))  # (ours first)
    for row in rows:
        invoice = store.get_invoice(row["id"])
        ai = invoice.get("ai_output") or {}
        if invoice["status"] not in ("review", "needs_review", "to_review", "waiting") or not ai.get("line_items"):
            continue
        final = json.loads(json.dumps(ai))
        line = final["line_items"][0]
        new_gl = next(code for code in accounts if code and code != line.get("predicted_gl_code"))
        line["predicted_gl_code"] = new_gl
        try:  # the dashboard checked and distributed the edited coding before approving it
            from ap_coder.config import Settings
            from ap_coder.pipeline import finalise_coding
            from ap_coder.schema import InvoiceCoding

            coding = InvoiceCoding.model_validate({k: v for k, v in final.items() if k != "gl_distribution"})
            final, _ = _try(finalise_coding, coding, store.reference_data(), Settings.from_env(), store=store,
                            exclude_invoice_id=row["id"])  # fmt: skip
        except Exception as exc:  # noqa: BLE001 - approved as edited
            say(f"(approved without finalising: {type(exc).__name__}: {exc})")
        store.approve_invoice(row["id"], final, reviewer)
        return row["id"], str(final.get("vendor_name") or ""), new_gl
    raise SystemExit(f"no invoice to approve: {[(r['id'], r['status']) for r in store.list_invoices()]}")


def _set_env(env: Path) -> Path:
    """The .env values a person set (``ENV_VALUES``, and a setting the new version no longer uses)."""
    env.parent.mkdir(parents=True, exist_ok=True)
    text = env.read_text(encoding="utf-8") if env.is_file() else ""
    names = {*ENV_VALUES, RETIRED.split("=")[0]}
    lines = [ln for ln in text.splitlines() if ln.split("=", 1)[0].strip() not in names]
    values = [*(f"{k}={v}" for k, v in ENV_VALUES.items()), RETIRED]
    env.write_text("\n".join([*lines, *values]) + "\n", encoding="utf-8")
    return env


def seed_inside(folder: Path, how: str, out: Path) -> None:
    """Use the old version as a person would have (see the module docstring). Runs with its own Python and code."""
    sys.path.insert(0, str(folder))
    if how == "cli":  # the first version: a command line writing to output/, its .env in the code folder
        out_dir = folder / "output"
        out_dir.mkdir(exist_ok=True)
        (out_dir / "batch_summary.json").write_text('[{"file": "contoso_invoice_INV-2026-04471.pdf"}]\n')
        env = _set_env(folder / ".env")
        summary = {"invoices": [], "feedback": 0, "export_batches": 0, "settings": {}, "vendors": [], "rules": [],
                   "schema_version": None, "env_file": str(env), "folder": str(folder), "how": how,
                   "retired": RETIRED}  # fmt: skip
        out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        say(f"Used the command-line version: {out_dir} and {env}")
        return
    from ap_coder import paths
    from ap_coder.store import Store

    data, db = paths.private_dir(), paths.default_db_path()
    store = Store(db)
    try:
        from ap_coder import demo
    except ImportError:
        demo = None
    if demo is not None:
        _try(demo.load_demo, store)
    else:
        _sample_invoices(store, folder, data)
    _upload(store, folder, data)
    approved, vendor, gl = _approve_corrected(store, REVIEWER)
    if hasattr(store, "save_vendor"):
        from ap_coder.memory import vendor_key

        _try(store.save_vendor, vendor_key(vendor), vendor, notes=VENDOR_NOTE, actor=REVIEWER)
    if hasattr(store, "save_coding_rules"):
        from ap_coder.rules import Rule

        _try(store.save_coding_rules, [*store.coding_rules(), Rule(vendor, RULE_WORDS, gl)], actor=REVIEWER)
    if hasattr(store, "create_export_batch"):
        ready = [r["id"] for r in store.list_invoices() if r["status"] == "approved"]
        _try(store.create_export_batch, ready, "csv", actor=REVIEWER)
    store.set_setting(*SETTING)
    env = _set_env(paths.env_file() or (folder / ".env" if how in ("cli", "by-hand") else data / ".env"))
    summary = snapshot(db)
    summary.update(data_dir=str(data), env_file=str(env), approved=approved, vendor=vendor, corrected_gl=gl,
                   folder=str(folder), how=how, retired=RETIRED)  # fmt: skip
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    say(f"Seeded {len(summary['invoices'])} invoices (invoice {approved} approved with GL {gl}): {out}")


def snapshot(db: Path) -> dict:
    """What the database holds, read with plain SQL (any schema version)."""
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        cols = {r[1] for r in conn.execute("PRAGMA table_info(invoices)")}
        pick = [c for c in ("id", "status", "vendor_name", "invoice_number", "source_path", "file_sha256",
                            "export_batch", "reviewer") if c in cols]  # fmt: skip
        invoices = [dict(r) for r in conn.execute(f"SELECT {', '.join(pick)} FROM invoices ORDER BY id")]

        def count(table: str) -> int:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] if table in tables else 0

        settings = {r[0]: r[1] for r in conn.execute("SELECT key, value FROM settings")}
        vendors = ([dict(r) for r in conn.execute("SELECT vendor_key, notes FROM vendors")]
                   if "vendors" in tables else [])  # fmt: skip
        rules = [dict(r) for r in conn.execute("SELECT * FROM coding_rules")] if "coding_rules" in tables else []
        return {"invoices": invoices, "feedback": count("feedback"), "export_batches": count("export_batches"),
                "settings": settings, "vendors": vendors, "rules": [r.get("contains") for r in rules],
                "schema_version": settings.get("schema_version")}  # fmt: skip
    finally:
        conn.close()


# --- After the update ---------------------------------------------------------------------------------------------


class Checks:
    def __init__(self) -> None:
        self.failed: list[str] = []

    def that(self, ok: bool, what: str) -> bool:
        say(f"  {'ok  ' if ok else 'FAIL'} {what}")
        if not ok:
            self.failed.append(what)
        return ok


def _get(port: int, path: str, timeout: float = 60) -> tuple[int, str, bytes]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}{path}", timeout=timeout) as response:
            return response.status, response.headers.get("Content-Type", ""), response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers.get("Content-Type", ""), exc.read()
    except OSError as exc:
        return 0, "", str(exc).encode()


def listening(port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) == 0


def pid_alive(pid: int) -> bool:
    if WINDOWS:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:  # a zombie (ended, not reaped yet) is not running
        return " Z " not in Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except OSError:
        return True


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def check(before: dict, new: Path, port: int, old_ports: list[int], old_pids: list[int], old_gone: bool,
          launch_log: Path | None, home: Path) -> list[str]:  # fmt: skip
    c = Checks()
    say("The old version stopped:")
    for old_port in old_ports:
        c.that(not streamlit_answers(old_port), f"port {old_port} no longer serves the old dashboard")
    for pid in old_pids:
        c.that(not pid_alive(pid), f"the old dashboard's process {pid} ended")

    say("The record of installs:")
    settings = json.loads((home / ".ap_coder" / "settings.json").read_text(encoding="utf-8"))
    data = Path(settings.get("data_dir") or "")
    c.that(Path(settings.get("current") or "").resolve() == new.resolve(), f"current install is {new}: {settings}")
    folders = [Path(e["folder"]).resolve() for e in settings.get("installs", [])]
    c.that(folders == [new.resolve()], f"the only install on record is {new}: {folders}")
    c.that(data.resolve() == (home / "APCoder").resolve(), f"the data folder is ~/APCoder: {data}")
    old = Path(before["folder"])
    over = old.resolve() == new.resolve()
    if old_gone:
        c.that(not old.exists(), f"the old folder {old} was removed")
    elif not over and before["how"] != "cli":
        c.that(not (old / ".venv").exists(), f"the old folder's .venv was removed ({old} kept)")
    if before["how"] == "cli":  # no dashboard, no database: its .env (and output/) are all it had
        if over:
            env_text = (data / ".env").read_text(encoding="utf-8") if (data / ".env").is_file() else ""
            for key, value in ENV_VALUES.items():
                c.that(f"{key}={value}" in env_text.splitlines(), f".env: {key}={value} moved to the data folder")
            c.that((old / "output" / "batch_summary.json").is_file(), "its output/ folder is kept")
        else:  # nothing on this computer says where it is: left as it was (docs/UPGRADING.md)
            c.that((old / ".env").is_file() and (old / "output").is_dir(), "the old folder is left as it was")
        status, _type, body = _get(port, "/healthz")
        c.that(status == 200, "the new version runs")
        for page in PAGES:
            c.that(_get(port, page)[0] == 200, f"GET {page}")
        return c.failed

    say("The data:")
    db = data / "ap_coder.db"
    after = snapshot(db) if db.is_file() else {"invoices": [], "settings": {}, "vendors": [], "rules": []}
    if before.get("schema_version") is not None:
        c.that(after["settings"].get("schema_version") == "15", f"database brought to schema 15 "
               f"(was {before['schema_version']})")  # fmt: skip
    rows = {r["id"]: r for r in after["invoices"]}
    c.that(len(rows) == len(before["invoices"]), f"{len(before['invoices'])} invoices kept ({len(rows)})")
    for inv in before["invoices"]:
        now = rows.get(inv["id"])
        if not c.that(now is not None, f"invoice {inv['id']} is there"):
            continue
        same = all(now.get(k) == inv.get(k) for k in ("status", "vendor_name", "invoice_number", "export_batch"))
        c.that(same, f"invoice {inv['id']}: status {inv['status']}, vendor, number and batch kept")
        path = Path(now["source_path"])
        c.that(path.is_file(), f"invoice {inv['id']}: its file is there ({path})")
        if path.is_file() and inv.get("file_sha256"):
            same_bytes = sha256(path) == inv["file_sha256"]
            if not same_bytes and over and str(path).startswith(str(new / "samples") + os.sep):
                # A demo invoice's file is the old version's sample: unzipping over it replaced it (docs/UPGRADING.md)
                say(f"  note invoice {inv['id']}: its sample was replaced by the new version's when unzipped over")
            else:
                c.that(same_bytes, f"invoice {inv['id']}: its file has the same bytes")
        gone_with = old / "private" if over else old  # what the update moved or removed
        outside = not str(path).startswith(str(gone_with) + os.sep)
        c.that(outside, f"invoice {inv['id']}: its file is not in {gone_with}")
    c.that(after.get("feedback", 0) >= before["feedback"], f"learning memory kept ({before['feedback']} lines)")
    c.that(after.get("export_batches", 0) == before["export_batches"],
           f"export batches kept ({before['export_batches']})")  # fmt: skip
    for vendor in before["vendors"]:
        if vendor.get("notes") == VENDOR_NOTE:
            c.that(vendor in after["vendors"], f"vendor {vendor['vendor_key']} keeps its note")
    c.that(sorted(before["rules"]) == sorted(after["rules"]), f"coding rules kept ({before['rules']})")
    for key, value in before["settings"].items():
        if key not in ("schema_version", "backup_copy_dir"):
            c.that(after["settings"].get(key) == value, f"setting {key} kept")
    env_text = (data / ".env").read_text(encoding="utf-8") if (data / ".env").is_file() else ""
    for key, value in ENV_VALUES.items():
        c.that(f"{key}={value}" in env_text.splitlines(), f".env: {key}={value} in the data folder's .env")
    if before.get("retired"):
        note = f"# no longer used (AP Coder 0.2): {before['retired']}"
        c.that(note in env_text.splitlines(), f".env: {before['retired']} commented out, with a note")

    say("The new web app:")
    status, _type, body = _get(port, "/healthz")
    health = json.loads(body) if status == 200 else {}
    c.that(health.get("app") == "ap-coder" and Path(health.get("folder", "")).resolve() == new.resolve(),
           f"/healthz is the new version from {new}: {health}")  # fmt: skip
    for page in PAGES:
        status, _type, body = _get(port, page)
        c.that(status == 200, f"GET {page}: {status}")
    status, _type, body = _get(port, f"/review/{before['approved']}")
    text = body.decode("utf-8", errors="replace")
    c.that(status == 200 and before["vendor"].split()[0] in text, "the approved invoice's page shows its vendor")
    status, _type, body = _get(port, "/learning")
    c.that(before["corrected_gl"] in body.decode("utf-8", errors="replace"), "the learning page shows the correction")
    for inv in before["invoices"]:
        status, kind, body = _get(port, f"/review/{inv['id']}/page/1.png")
        c.that(status == 200 and kind == "image/png" and body[:8] == b"\x89PNG\r\n\x1a\n",
               f"/review/{inv['id']}/page/1.png: {status} {kind}")  # fmt: skip

    if launch_log is not None:
        say("What the launcher said:")
        out = launch_log.read_text(encoding="utf-8", errors="replace")
        c.that("Self-check OK" in out, "the self-check passed")
        c.that("Traceback" not in out, "no Python error")
        c.that(not re.search(r"Downloading https?:|Looking in indexes", out), "pip stayed offline")
    return c.failed


def connects(trace: Path) -> list[str]:
    """The network connections in an ``strace -f -e trace=connect`` log that leave this computer (anything but
    127.0.0.1 / ::1 and local sockets)."""
    found = []
    for row in trace.read_text(encoding="utf-8", errors="replace").splitlines():
        if "connect(" not in row or "AF_UNIX" in row or "AF_NETLINK" in row:
            continue
        address = re.search(r'inet_addr\("([^"]+)"\)|inet_pton\(AF_INET6, "([^"]+)"', row)
        host = (address.group(1) or address.group(2)) if address else "?"
        if host not in ("127.0.0.1", "::1", "::ffff:127.0.0.1"):
            found.append(row.strip())
    return found


# --- The whole update, on this computer (Linux or a Mac) -----------------------------------------------------------

# What the old version's install may use (it downloads from PyPI); the new one gets none of it: offline.
ONLINE_ENV = ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "NO_PROXY", "no_proxy", "SSL_CERT_FILE",
              "PIP_CERT", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE", "PIP_INDEX_URL", "PIP_CACHE_DIR")  # fmt: skip


def user_env(home: Path, online: bool) -> dict[str, str]:
    """A person's environment in the scratch home ``home``: nothing of this one's (no AP_* setting, no proxy)
    unless ``online`` (the old version's install)."""
    env = {"HOME": str(home), "PATH": os.environ.get("AP_TEST_PATH", "/usr/local/bin:/usr/bin:/bin"),
           "LANG": "C.UTF-8", "AP_NO_PAUSE": "1", "BROWSER": "none", "TMPDIR": str(home / "tmp")}  # fmt: skip
    (home / "tmp").mkdir(parents=True, exist_ok=True)
    if online:
        env.update({k: os.environ[k] for k in ONLINE_ENV if os.environ.get(k)})
        env["AP_ALLOW_INTERNET"] = "1"  # (the old launchers' opt-in; the oldest ones downloaded anyway)
    return env


def me(*args: str) -> list[str]:
    return [sys.executable, str(Path(__file__).resolve()), *args]


def wait_for_health(port: int, proc: subprocess.Popen, wait: float) -> dict:
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        status, _type, body = _get(port, "/healthz", timeout=5)
        if status == 200:
            return json.loads(body)
        if proc.poll() is not None:
            return {}
        time.sleep(2)
    return {}


def unzip(bundle: Path, folder: Path) -> None:
    """The bundle's ``APProcessor/`` folder unzipped as ``folder`` (into a new one, or over an older version's),
    replacing what is there and keeping the launchers executable."""
    import zipfile

    with zipfile.ZipFile(bundle) as archive:
        for info in archive.infolist():
            if not info.filename.startswith("APProcessor/") or info.is_dir():
                continue
            target = folder / info.filename.removeprefix("APProcessor/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(info))
            mode = info.external_attr >> 16
            if mode and not WINDOWS:
                target.chmod(mode & 0o777)


STARTED: list[int] = []  # the process groups a scenario started (stopped when it ends, whatever happened)


def stop_group(pid: int) -> None:
    import signal

    try:
        os.killpg(pid, signal.SIGTERM)
    except OSError:
        pass


def scenario(sha: str, how: str, where: str, bundle: Path, work: Path, port: int, use_strace: bool,
             windows: int = 1, old_python: str = "") -> int:  # fmt: skip
    """Install ``sha`` as ``how``, use it, leave its dashboard running; unzip the new ``bundle`` into a new folder
    (``where`` = ``new-folder``), beside it (``beside``: closed before the update, ``windows`` = 0, it is found there)
    or over the old one (``over``) and double-click it, offline; check everything; start it a second time. Returns
    the number of failed checks."""
    import shutil

    home = work / "home"
    shutil.rmtree(work, ignore_errors=True)
    home.mkdir(parents=True)
    bundle_like = how == "launcher"  # unzipped from its offline bundle (APProcessor/), else GitHub's ZIP
    old = home / "Downloads" / ("APProcessor" if bundle_like else "AP_Processer-main")
    online, offline = user_env(home, True), user_env(home, False)
    if old_python:  # the Python a by-hand install makes its .venv with (a 3.10: PR #1 and #2 allowed it)
        online["AP_OLD_PYTHON"] = old_python
    run(me("extract", sha, str(old), *(["--bundle"] if bundle_like else [])))
    run(me("install", str(old), "--how", how), env=online)
    old_ports, pids = [], []
    for window in range(max(windows, 1) if how != "cli" else 0):  # (a second window: start.sh twice, next port)
        log = work / f"old-dashboard{window or ''}.log"
        run(me("start", str(old), "--how", how, "--log", str(log)), env=online)  # (the double-click installs too)
        pids.append(int(Path(f"{log}.pid").read_text(encoding="ascii")))
        STARTED.append(pids[-1])
        old_ports = [p for p in range(8501, 8511) if streamlit_answers(p)]
    run(me("seed", str(old), "--how", how, "--out", str(work / "before.json")), env=online)
    if windows == 0:  # closed before the update: nothing running says where it is
        for pid in pids:
            stop_group(pid)
        deadline = time.monotonic() + 60
        while any(streamlit_answers(p) for p in old_ports) and time.monotonic() < deadline:
            time.sleep(1)
        old_ports = []

    # over the old folder; beside it, as a Mac unzips a second bundle ("APProcessor 2"); or in a folder of its own
    new = {"over": old, "beside": old.parent / "APProcessor 2"}.get(where, home / "AP Coder new" / "APProcessor")
    unzip(bundle, new)
    launch_log = work / "launch.out"
    cmd = ["bash", "APProcessor.command", "--port", str(port)]
    trace = work / "connect.trace"
    if use_strace:
        cmd = ["strace", "-f", "-qq", "-e", "trace=connect", "-o", str(trace), *cmd]
    say(f"$ {' '.join(cmd)}  (offline, in {new})")
    started = time.monotonic()
    with open(launch_log, "wb") as out:
        proc = subprocess.Popen(cmd, cwd=new, env=offline, stdout=out, stderr=subprocess.STDOUT,
                                stdin=subprocess.DEVNULL, start_new_session=True)  # fmt: skip
    STARTED.append(proc.pid)
    health = wait_for_health(port, proc, 1800)
    say(f"The new version answered after {time.monotonic() - started:.0f} s: {health}")
    say(launch_log.read_text(encoding="utf-8", errors="replace"))
    args = ["check", str(work / "before.json"), "--new", str(new), "--port", str(port), "--home", str(home),
            "--launch-log", str(launch_log)]  # fmt: skip
    args += [a for p in old_ports for a in ("--old-port", str(p))]
    args += [a for pid in pids for a in ("--old-pid", str(pid))]
    args += ["--old-gone"] if where != "over" and how != "cli" else []
    args += ["--strace", str(trace)] if use_strace else []
    failed = subprocess.run(me(*args)).returncode != 0

    # The second start: the first one stopped (closed its window), then a double-click again.
    stop_group(proc.pid)
    proc.wait(timeout=60)
    for _ in range(30):
        if not listening(port):
            break
        time.sleep(1)
    second_log = work / "launch2.out"
    started = time.monotonic()
    with open(second_log, "wb") as out:
        proc = subprocess.Popen(["bash", "APProcessor.command", "--port", str(port)], cwd=new, env=offline,
                                stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=True)  # fmt: skip
    STARTED.append(proc.pid)
    health = wait_for_health(port, proc, 300)
    seconds = time.monotonic() - started
    text = second_log.read_text(encoding="utf-8", errors="replace")
    say(text)
    c = Checks()
    c.that(bool(health), f"the second start serves AP Coder ({seconds:.0f} s)")
    c.that(seconds < 90, f"the second start is fast ({seconds:.0f} s)")
    c.that(not re.search(r"Installing|Updated AP Coder|First run|self-check \(reads", text),
           "the second start sets nothing up again")  # fmt: skip
    stop_group(proc.pid)
    for pid in pids:
        stop_group(pid)
    return int(failed) + len(c.failed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list")
    p = sub.add_parser("unzip", help="the new bundle's APProcessor/ folder as FOLDER (new, or over an old one)")
    p.add_argument("bundle", type=Path)
    p.add_argument("folder", type=Path)
    p = sub.add_parser("extract")
    p.add_argument("sha")
    p.add_argument("folder", type=Path)
    p.add_argument("--bundle", action="store_true", help="as its offline bundle held it (with its manifest)")
    p = sub.add_parser("old-wheelhouse", help="an older Windows bundle's wheelhouse (cp311/cp312 win_amd64) as FOLDER")
    p.add_argument("folder", type=Path)
    for name in ("install", "start", "seed", "seed-inside"):
        p = sub.add_parser(name)
        p.add_argument("folder", type=Path)
        p.add_argument("--how", required=True, choices=["cli", "by-hand", "installer", "launcher"])
        p.add_argument("--port", type=int)
        p.add_argument("--log", type=Path)
        p.add_argument("--out", type=Path)
        p.add_argument("--wait", type=float, default=1200)
    p = sub.add_parser("scenario")
    p.add_argument("sha")
    p.add_argument("--how", required=True, choices=["cli", "by-hand", "installer", "launcher"])
    p.add_argument("--where", required=True, choices=["new-folder", "beside", "over"])
    p.add_argument("--bundle", type=Path, required=True, help="the new version's offline bundle (ZIP)")
    p.add_argument("--work", type=Path, required=True, help="a scratch folder (its home/ is the person's home)")
    p.add_argument("--port", type=int, default=8601)
    p.add_argument("--strace", action="store_true", help="record every connection (Linux): none may leave")
    p.add_argument("--keep", action="store_true", help="keep the scratch home (both installs) afterwards")
    p.add_argument(
        "--windows",
        type=int,
        default=1,
        help="old dashboards left running (0: closed before the update; 2: start.sh twice)",
    )
    p.add_argument("--old-python", default="", help="by-hand: the Python its .venv is made with (e.g. a 3.10)")
    p = sub.add_parser("check")
    p.add_argument("before", type=Path)
    p.add_argument("--new", type=Path, required=True)
    p.add_argument("--port", type=int, required=True)
    p.add_argument("--old-port", type=int, action="append", default=[])
    p.add_argument("--old-pid", type=int, action="append", default=[])
    p.add_argument("--old-gone", action="store_true", help="the old folder must be removed")
    p.add_argument("--launch-log", type=Path)
    p.add_argument("--home", type=Path, default=Path.home())
    p.add_argument("--strace", type=Path, help="an strace -e trace=connect log: no connection may leave")
    args = parser.parse_args(argv)
    if args.command == "list":
        for v in VERSIONS:
            say(f"{v['sha']}  {v['label']:<13} installed: {', '.join(v['how'])}; schema {v['schema']}")
    elif args.command == "unzip":
        unzip(args.bundle, args.folder)
    elif args.command == "extract":
        extract(args.sha, args.folder, args.bundle)
    elif args.command == "old-wheelhouse":
        old_wheelhouse(args.folder)
    elif args.command == "install":
        install(args.folder.resolve(), args.how)
    elif args.command == "start":
        start(args.folder.resolve(), args.how, args.log.resolve(), args.port, args.wait)
    elif args.command == "seed":
        seed(args.folder.resolve(), args.how, args.out.resolve())
    elif args.command == "seed-inside":
        seed_inside(args.folder.resolve(), args.how, args.out)
    elif args.command == "scenario":
        try:
            failed = scenario(args.sha, args.how, args.where, args.bundle.resolve(), args.work.resolve(), args.port,
                              args.strace, args.windows, args.old_python)  # fmt: skip
        finally:
            for pid in STARTED:
                stop_group(pid)
            if not args.keep:  # (the old and the new .venv: a GB or more)
                import shutil

                time.sleep(2)
                shutil.rmtree(args.work / "home", ignore_errors=True)
        say("Update checked: all passed." if not failed else f"Update FAILED: {failed} check(s)")
        return 1 if failed else 0
    elif args.command == "check":
        before = json.loads(args.before.read_text(encoding="utf-8"))
        failed = check(before, args.new, args.port, args.old_port, args.old_pid, args.old_gone, args.launch_log,
                       args.home)  # fmt: skip
        if args.strace is not None:
            leaving = connects(args.strace)
            Checks().that(not leaving, f"no network connection left this computer: {leaving[:5]}")
            failed += ["network"] if leaving else []
        if failed:
            say(f"FAILED: {len(failed)} check(s)")
            return 1
        say("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
