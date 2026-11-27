"""The update test of a release, the one done by hand for each release so far, in one command (D-337): an older AP
Coder unzipped as a person unzips it and used, then started again to answer "Update now?" as the person at its window
does.

    python scripts/release_proof.py --from OLD --to NEW [--answer y|n] [--work DIR] [--keep]

OLD and NEW are each a release tag (``v0.3.0-build.<commit>``: its ZIP and SHA256SUMS downloaded from its GitHub
release, the ZIP checked against SHA256SUMS; only with ``AP_ALLOW_INTERNET=1``) or a ZIP on this computer laid out as a
release's (one folder ``AP_Processer-<version>-build.<commit>/``, its ap_coder/BUILD stamped by git archive). Linux
and Macs only: the question is answered in a pseudo-terminal, which Windows has not (there it refuses, exit 2). Only
with ``AP_ALLOW_INTERNET=1`` whichever they are: both first setups download their packages from PyPI, as a person's
first start does (D-94), and the launchers started are not told the setting (it refuses, exit 2).

In ``--work`` (a new folder; a temporary one by default), with a home of its own (``home/``: HOME points there, and
nothing of this computer's AP Coder nor any AP_ setting is used):

1. OLD is unzipped into ``home/Downloads`` and its APProcessor.command started in a pseudo-terminal (``--no-update``),
   as a double-click opens it: the first setup (which downloads its packages and OCR models itself, D-94), the first
   sign-in (admin / admin, a password, the authenticator app), the demo (``demo``); then the made-up company in a
   second data folder (``demo --company``, ``serve``: docs/PROTOTYPE.md section 2) with two of its invoices approved
   on the review screen and its ReportsNow workbook loaded (Setup -> JD Edwards files). What Settings -> About says,
   and the approved invoices opened and their PDFs downloaded; then both data folders' databases copied (``db/``).
2. OLD is started again and "Update now?" answered ``--answer`` (y: NEW takes over in the same window; n: OLD goes
   on). The launcher asks GitHub itself when NEW is GitHub's newest release; otherwise (a ZIP on this computer, an
   older tag) a stand-in for GitHub on 127.0.0.1 answers with NEW's release: a ``sitecustomize`` on PYTHONPATH points
   the launcher's own ``releases`` module there (its address, hosts and scheme; nothing else of it is changed).
3. Proved: the question and the update line said in the window, the time from the answer to the dashboard running,
   Settings -> About's Version line before and after, the data kept (each table compared row by row with its copy
   from before: what an update always changes is named, as the 0a749b3 proof found it: ``files_moved``, the review
   queue's re-check, the sign-in after it; anything else is a failure), every invoice's file still there, the
   approved invoices opened and their PDFs downloaded, the doctor in the folder in use (no failure; its warnings
   listed), the workbook checked again (each sheet loaded before still loads), the notes folder's README.txt, and
   every log line that looks like an error. With n: the old build still the one in use, on the same data.

It prints a short report, writes ``report.json`` (``logs/``: each window and command, ``pages/``: the PDFs), and ends
with "For the pilot tester's script": the question, the update line and the Version line as said. Exit 0 only when
every check passed, 1 when one failed, 2 when it could not run. The program folders (each with its .venv, about 900
MB) and the home's pip cache (what the two setups downloaded) are deleted at the end unless ``--keep``.
"""

from __future__ import annotations

import argparse
import codecs
import collections
import contextlib
import datetime as dt
import hashlib
import html
import http.server
import json
import os
import re
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.parse
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT), str(ROOT / "scripts"), str(ROOT / "scripts" / "ci")]

import first_day  # noqa: E402 - the pilot tester's first day (scripts/ci): its browser steps
import releases  # noqa: E402 - the launcher's own GitHub releases (standard library only)

from ap_coder.bench.day import Client, _json  # noqa: E402
from ap_coder.bench.load import Screen, free_port  # noqa: E402
from ap_coder.core import paths  # noqa: E402
from ap_coder.ops import offline  # noqa: E402

WINDOWS = os.name == "nt"
Failed = first_day.Failed  # a step that could not be done: the proof stops there, said
RUNNING = re.compile(r"AP Coder is running at (http://\S+)")
PAUSE = re.compile(r"[Pp]ress (Enter|any key|a key)")  # the window's pause before it closes (stop() in the launcher)
QUESTION = "Update now?"
FIRST_WAIT = 1800.0  # seconds a first start has (it downloads its packages and OCR models, then runs the self-check)
UPDATE_WAIT = 1800.0  # and the start that answers: the new build sets itself up the same way
COMMAND_WAIT = 900.0  # a command (demo, doctor)
TRY = "APCoder-try"  # the made-up company's data folder, beside the person's own (docs/PROTOTYPE.md section 2)
APPROVED = 2  # the made-up company's invoices approved on the review screen
STAND_IN_ENV = "RELEASE_PROOF_GITHUB"  # the stand-in's address, read by SITECUSTOMIZE


# --- The two builds ---------------------------------------------------------------------------------------------------


@dataclass
class Build:
    """A release's ZIP and what it says it is (its pyproject version, its ap_coder/BUILD stamp)."""

    source: str  # what was given: the tag, or the ZIP's path
    zip: Path
    top: str  # its one folder, AP_Processer-<version>-build.<commit>
    version: str
    commit: str  # ap_coder/BUILD's
    committed: str  # its commit's time, UTC
    sha256: str
    release: dict | None = None  # its GitHub release, for a tag
    sums: str = ""  # the release's SHA256SUMS, for a tag

    @property
    def tag(self) -> str:
        return f"v{self.version}-build.{self.commit[:7]}"  # as .github/workflows/builds.yml tags it

    def said(self) -> str:
        return paths.describe({"version": self.version, "commit": self.commit[:7], "build": self.committed})


def build_of(zip_path: Path, source: str, ids: Path, release: dict | None = None, sums: str = "") -> Build:
    """What ``zip_path`` holds, read as the launcher reads an update's ZIP (one folder, no name outside it, no link)
    and an install (its pyproject.toml and ap_coder/BUILD, put in ``ids`` to be read); Failed when it is not a
    release's ZIP."""
    try:
        with zipfile.ZipFile(zip_path) as archive:
            top = releases._top_folder(archive.namelist())
            links = [i.filename for i in archive.infolist() if (i.external_attr >> 16) & 0o170000 == 0o120000]
            if links:
                raise Failed(f"{source}: the ZIP holds a link ({links[0]})")
            if not top.startswith("AP_Processer-"):
                raise Failed(f"{source}: its folder is {top}, not AP_Processer-<version>-build.<commit> as a release's")
            names = set(archive.namelist())
            for part in ("pyproject.toml", "ap_coder/BUILD", "ap_coder/__init__.py", "scripts/launch.py",
                         "APProcessor.command"):  # fmt: skip
                if f"{top}/{part}" not in names:
                    raise Failed(f"{source}: the ZIP holds no AP Coder (no {part})")
            for part in ("pyproject.toml", "ap_coder/BUILD"):
                (ids / top / part).parent.mkdir(parents=True, exist_ok=True)
                (ids / top / part).write_bytes(archive.read(f"{top}/{part}"))
    except (OSError, zipfile.BadZipFile, releases.UpdateError) as exc:
        raise Failed(f"{source}: {exc}") from exc
    stamp, identity = paths.build_stamp(ids / top), paths.install_identity(ids / top)
    if stamp["source"] != "stamp":
        raise Failed(f"{source}: its ap_coder/BUILD is not stamped (git archive writes the commit into it, as in a "
                     "release's ZIP)")  # fmt: skip
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    return Build(source, zip_path, top, identity["version"], stamp["commit"], stamp["time"], digest, release, sums)


def download(tag: str, folder: Path) -> Build:
    """The release ``tag``'s ZIP and SHA256SUMS from GitHub (through the launcher's own requests, scripts/releases.py:
    GitHub's hosts only, every redirect checked), the ZIP checked against SHA256SUMS as the launcher checks it."""
    folder.mkdir(parents=True, exist_ok=True)
    url = f"https://api.github.com/repos/{releases.REPO}/releases/tags/{tag}"
    try:
        with releases._open(url, releases.DOWNLOAD_TIMEOUT, accept="application/vnd.github+json") as answer:
            item = json.loads(answer.read(2_000_000).decode("utf-8"))
        assets = {str(a.get("name")): str(a.get("browser_download_url") or "") for a in item.get("assets") or []
                  if isinstance(a, dict)}  # fmt: skip
        zips = [name for name in assets if name.startswith("AP_Processer-") and name.endswith(".zip")]
        if len(zips) != 1 or releases.SUMS not in assets:
            raise Failed(f"the release {tag} has no ZIP and {releases.SUMS}: {sorted(assets)}")
        sums = releases._read_text(assets[releases.SUMS])
        got = releases._download(assets[zips[0]], folder / zips[0])
        if got != releases._sum_for(sums, zips[0]):
            raise Failed(f"{zips[0]} is not the file {releases.SUMS} lists (its SHA-256 differs)")
    except (OSError, ValueError, releases.UpdateError) as exc:
        raise Failed(f"the release {tag} could not be downloaded from GitHub ({exc})") from exc
    (folder / releases.SUMS).write_text(sums, encoding="utf-8")
    (folder / "release.json").write_text(json.dumps(item, indent=1), encoding="utf-8")
    build = build_of(folder / zips[0], tag, folder / "ids", item, sums)
    if build.tag != tag:
        raise Failed(f"the ZIP of the release {tag} holds {build.tag}")
    return build


def obtain(given: str, folder: Path) -> Build:
    if releases.TAG.match(given):
        return download(given, folder / given)
    return build_of(Path(given).resolve(), given, folder / "ids")


# --- The stand-in for GitHub ------------------------------------------------------------------------------------------

SITECUSTOMIZE = '''"""Written by scripts/release_proof.py for the start that answers "Update now?": the launcher's
own releases module asks the stand-in for GitHub at RELEASE_PROOF_GITHUB (http://127.0.0.1:<port>) in place of
api.github.com. Nothing else of it is changed, and any other Python started with this folder on PYTHONPATH is left
as it is."""

import importlib.abc
import importlib.machinery
import os
import sys

BASE = os.environ.get("RELEASE_PROOF_GITHUB", "")


class _StandIn(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name != "releases" or not BASE:
            return None
        spec = importlib.machinery.PathFinder.find_spec(name, path)
        if spec is None or spec.loader is None:
            return spec
        run = spec.loader.exec_module

        def exec_module(module):
            run(module)
            module.API = f"{BASE}/repos/{module.REPO}/releases?per_page=20"
            module.HOSTS = ("127.0.0.1",)
            module.SCHEME = "http"

        spec.loader.exec_module = exec_module
        return spec


sys.meta_path.insert(0, _StandIn())
'''


class StandIn:
    """A stand-in for GitHub on 127.0.0.1, for the launcher's update check: its releases list holds ``build``'s release
    alone (a tag's as GitHub gave it, a local ZIP's made as .github/workflows/builds.yml makes one), with the ZIP and
    SHA256SUMS it names. Every path asked is kept (``asked``)."""

    def __init__(self, build: Build) -> None:
        self.asked: list[str] = []
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def do_GET(self) -> None:  # noqa: N802 - http.server's name
                owner.asked.append(self.path)
                body = owner.answer(self.path)
                self.send_response(200 if body is not None else 404)
                self.send_header("Content-Length", str(len(body or b"")))
                self.end_headers()
                self.wfile.write(body or b"")

        self.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        name = f"{build.top}.zip"  # (the launcher unzips it into a folder of that name: its one folder's)
        self.files = {name: build.zip.read_bytes(), releases.SUMS: (build.sums or f"{build.sha256}  {name}\n").encode()}
        made = build.release or {
            "tag_name": build.tag, "draft": False, "prerelease": False,
            "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "body": f"- {Path(build.source).name}, a ZIP on this computer\n\nBuild {build.commit[:7]}, committed "
                    f"{build.committed}Z",
        }  # fmt: skip
        self.release = {**made, "assets": [{"name": n, "browser_download_url": f"{self.base}/dl/{n}"}
                                           for n in self.files]}  # fmt: skip
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()

    def answer(self, path: str) -> bytes | None:
        if path.startswith(f"/repos/{releases.REPO}/releases"):
            return json.dumps([self.release]).encode()
        return self.files.get(path.rsplit("/", 1)[-1]) if path.startswith("/dl/") else None

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def with_stand_in(env: dict[str, str], stand_in: StandIn, folder: Path) -> dict[str, str]:
    """``env`` with SITECUSTOMIZE (written in ``folder``) on PYTHONPATH and the stand-in's address, never through a
    proxy."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "sitecustomize.py").write_text(SITECUSTOMIZE, encoding="utf-8")
    near = ",".join(x for x in (env.get("NO_PROXY") or env.get("no_proxy") or "", "127.0.0.1") if x)
    found = os.pathsep.join(p for p in (str(folder), env.get("PYTHONPATH", "")) if p)
    return {**env, "PYTHONPATH": found, STAND_IN_ENV: stand_in.base, "NO_PROXY": near, "no_proxy": near}


def newest_tag() -> str:
    """GitHub's newest tested release as the launcher finds it ("" when GitHub can't be asked)."""
    try:
        found = releases.newest()
    except Exception:  # noqa: BLE001 - any failure: the stand-in answers instead
        return ""
    return found.tag if found else ""


# --- The person, the launcher's window --------------------------------------------------------------------------------


def person_env(home: Path) -> dict[str, str]:
    """A person at this computer whose home is ``home``: this computer's PATH (without this script's own virtual
    environment), language and proxy; nothing of AP Coder's (no AP_ setting), of Python's or of the folders of this
    computer's own home (XDG_); no browser opened."""
    mine = Path(sys.prefix) / "bin" if sys.prefix != sys.base_prefix else None
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AP_", "PYTHON", "VIRTUAL_ENV", "XDG_"))}
    path = [p for p in os.environ.get("PATH", "").split(os.pathsep) if p and (mine is None or Path(p) != mine)]
    (home / "tmp").mkdir(parents=True, exist_ok=True)
    env.update(HOME=str(home), PATH=os.pathsep.join(path), BROWSER="none", TMPDIR=str(home / "tmp"))
    return env


def unzip(build: Build, parent: Path) -> Path:
    """``build``'s ZIP unzipped into ``parent`` as a Mac's Archive Utility unzips it: its one folder, the modes kept
    (the launcher is executable); the folder."""
    with zipfile.ZipFile(build.zip) as archive:
        archive.extractall(parent)
        for info in archive.infolist():
            if (info.external_attr >> 16) & 0o111 and not info.is_dir():
                path = parent / info.filename
                path.chmod(path.stat().st_mode | 0o755)
    return parent / build.top


class Window:
    """A launcher's window: ``command`` in a pseudo-terminal of its own, as a double-click opens one, each line it says
    kept with the seconds since it started (``lines``) and written to ``log`` with the time; "Update now?" answered
    ``answer`` (none: left unanswered), and a "Press Enter" pause answered with Enter."""

    def __init__(self, command: list[str], cwd: Path, env: dict[str, str], log: Path, answer: str = "") -> None:
        import pty

        self.lines: list[tuple[float, str]] = []
        self.answer, self.answered, self.started = answer, False, time.monotonic()
        self._out = log.open("w", encoding="utf-8", buffering=1)
        self._out.write(f"{time.strftime('%H:%M:%S', time.gmtime())} +   0.0s RUN {' '.join(command)}\n")
        self._master, slave = pty.openpty()
        self.process = subprocess.Popen(command, cwd=cwd, env=env, stdin=slave, stdout=slave, stderr=slave,
                                        start_new_session=True)  # fmt: skip
        os.close(slave)
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _say(self, text: str, note: str = "") -> None:
        seconds = round(time.monotonic() - self.started, 1)
        self.lines.append((seconds, text))
        self._out.write(f"{time.strftime('%H:%M:%S', time.gmtime())} +{seconds:6.1f}s {text}{note}\n")

    def _read(self) -> None:
        decoder, pending = codecs.getincrementaldecoder("utf-8")(errors="replace"), ""
        while True:
            try:
                chunk = os.read(self._master, 4096)
            except OSError:  # (Linux: once the last process holding the terminal closed it)
                break
            if not chunk:
                break
            pending += decoder.decode(chunk).replace("\r\n", "\n").replace("\r", "\n")
            *said, pending = pending.split("\n")
            for text in said:
                self._say(text)
            if self.answer and not self.answered and QUESTION in pending:
                self._say(pending, f"<< answered {self.answer!r}")
                pending, self.answered = "", True
                os.write(self._master, f"{self.answer}\n".encode())
            elif PAUSE.search(pending):
                self._say(pending, "<< pressed Enter")
                pending = ""
                os.write(self._master, b"\n")
        if pending:
            self._say(pending)
        self.process.wait()
        self._out.write(f"{time.strftime('%H:%M:%S', time.gmtime())} EXIT {self.process.returncode}\n")
        self._out.close()
        os.close(self._master)

    def wait_for(self, pattern: re.Pattern, seconds: float) -> str | None:
        """The first group of ``pattern`` in the first line that matches it, waited for at most ``seconds``; None when
        the window closed (or the time passed) first."""
        deadline = time.monotonic() + seconds
        while True:
            alive = self._reader.is_alive()
            for _seconds, text in list(self.lines):
                found = pattern.search(text)
                if found:
                    return found.group(1) if found.groups() else found.group(0)
            if not alive or time.monotonic() > deadline:
                return None
            time.sleep(0.2)

    def close(self, seconds: float = 60.0) -> int | None:
        """Waited for at most ``seconds`` to close by itself, then closed with what it started; its exit code (None:
        it had to be closed)."""
        try:
            self.process.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            pass
        code = self.process.poll()
        for sig in (signal.SIGTERM, signal.SIGKILL):
            with contextlib.suppress(OSError):
                os.killpg(self.process.pid, sig)  # (what it started and left running too)
            self._reader.join(10)
            if not self._reader.is_alive():
                break
        return code


def window_said(lines: list[tuple[float, str]]) -> dict[str, Any]:
    """What a launcher's window said about the update: the question's first line (``offer``) and its last
    (``question``), the line saying it was updated (``update``) or that this version starts (``kept``), where the
    dashboard runs (``running``) and the seconds from the answer (or from the start, when nothing was asked) until
    then."""
    said: dict[str, Any] = {"offer": "", "question": "", "update": "", "kept": "", "running": "", "seconds": None}
    asked = 0.0
    for seconds, text in lines:
        line = text.strip()
        if line.startswith("A newer AP Coder is available:") and not said["offer"]:
            said["offer"] = line.split(" What's new:", 1)[0]
        elif QUESTION in line and not said["question"]:
            asked, said["question"] = seconds, line
        elif re.fullmatch(r"Updated AP Coder from .+ to .+\.", line) and not said["update"]:
            said["update"] = line
        elif line.startswith("Starting this version.") and not said["kept"]:
            said["kept"] = line
        found = RUNNING.search(line)
        if found and not said["running"]:
            said["running"], said["seconds"] = found.group(1), round(seconds - asked, 1)
    return said


# --- What the pages and the commands said -----------------------------------------------------------------------------


def version_line(page: str) -> str:
    """Settings -> About's Version row, as it reads (``0.3.0 · build 0a749b3c, 2026-11-25 17:46``); "" without one."""
    found = re.search(r"<dt>\s*Version\s*</dt>\s*<dd>(.*?)</dd>", page, re.S)
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", found.group(1))).split()) if found else ""


def doctor_said(text: str) -> dict[str, Any]:
    """The doctor's last line (``0 failure(s), 2 warning(s)``) and its failures and warnings, a line each."""
    summary = re.findall(r"\d+ failure\(s\), \d+ warning\(s\)", text)
    picked = {mark: [" ".join(line[len(mark) :].split()) for line in text.splitlines() if line.startswith(mark)]
              for mark in ("[FAIL]", "[WARN]")}  # fmt: skip
    return {"summary": summary[-1] if summary else "", "failures": picked["[FAIL]"], "warnings": picked["[WARN]"]}


def workbook_said(page: str) -> dict[str, Any]:
    """Setup -> JD Edwards files' summary of a workbook checked ("Check the file"): its verdict, and each sheet's name,
    rows and whether it loads."""
    verdict = re.search(r'class="[^"]*imp-verdict[^"]*"[^>]*>.*?<div>(.*?)</div>', page, re.S)
    table = page.split('id="workbook-sheets"', 1)[-1].split("</table>", 1)[0] if 'id="workbook-sheets"' in page else ""
    sheets = []
    for row in re.findall(r"<tr>(.*?)</tr>", table.split("<tbody>", 1)[-1], re.S):
        name, rows = re.search(r"<b>(.*?)</b>", row, re.S), re.search(r'<td class="r">\s*([\d,]+)', row)
        if name:
            words = first_day.plain(row)
            loads = "Loads" in words and "Not loaded" not in words
            sheets.append([html.unescape(name.group(1)).strip(), int(rows.group(1).replace(",", "")) if rows else 0,
                           loads])  # fmt: skip
    return {"verdict": first_day.plain(verdict.group(1)) if verdict else "", "sheets": sheets}


# A line of a window or a log that says something went wrong: a Python error, the launcher's ✗ or [FAIL], a step that
# failed, could not, was not installed, or the window's last words when it stopped on a problem.
ERROR_LIKE = re.compile(
    r"Traceback|\b\w*(?:Error|Exception)\b|\bERROR\b|\bCRITICAL\b|✗|\[FAIL\]|"
    r"(?i:\bfailed\b|\bcould ?n[o'’]t\b|\bcan ?n[o'’]t\b|\bnot installed\b|\bAP Coder stopped\b)"
)


def error_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if ERROR_LIKE.search(line)]


# --- The data, table by table -----------------------------------------------------------------------------------------

DERIVED = ("invoice_summary", "summary_facts", "summary_codes")  # the reports' summary, worked out from the invoices
SEARCH_PAGES = ("invoice_search_data", "invoice_search_idx")  # the search index's own pages (its words: _content)
RECHECK_SETTINGS = re.compile(r"summary_generation/.+|summary_logged")


def why_changed(table: str, kind: str, was: dict | None, now: dict | None, moved: tuple[str, str]) -> str:
    """Why a row of ``table`` was ``added``, ``gone`` or ``changed`` (``was`` -> ``now``) as an update always does
    it, and this proof's own browsing after it (the rows the 0a749b3 proof found); "" when that is not why.
    ``moved``: the program folder before and after (the update moves the files the demo invoices point into)."""
    was, now = was or {}, now or {}
    if table == "ephemeral":
        return "short-lived rows (an upload being checked, a page's token)"
    if table in SEARCH_PAGES:
        return "the search index's own pages, merged (its words are invoice_search_content's)"
    if table == "report_answers":
        return "the reports' answers kept for their next ask, worked out again from the data"
    if kind == "added" and table == "events":
        if now.get("action") == "files_moved":
            return "the update's note that it pointed the invoices at their files' new place (files_moved)"
        return "the proof's sign-in after the update" if now.get("action") == "login" else ""
    if kind == "added" and table in ("login_attempts", "sessions"):
        return "the proof's sign-in after the update"
    if kind in ("added", "gone") and table == "queue_changes":
        return "the review queue's notices of the invoices its re-check looked at again"
    if kind == "added" and table == "settings":
        return _why_setting(now.get("key"))
    if kind != "changed":
        return ""
    columns = [c for c in dict.fromkeys([*was, *now]) if was.get(c) != now.get(c)]
    reasons = [_why_column(table, column, was, now, moved) for column in columns]
    return "; ".join(dict.fromkeys(reasons)) if reasons and all(reasons) else ""


def _why_setting(key: object) -> str:
    if key == "schema_version":
        return "the new build's schema version"
    return "the review queue's re-check (its counters)" if RECHECK_SETTINGS.fullmatch(str(key)) else ""


def _why_column(table: str, column: str, was: dict, now: dict, moved: tuple[str, str]) -> str:
    before, after = was.get(column), now.get(column)
    if table == "invoices" and column in ("source_path", "meta") and isinstance(before, str):
        return "their files' new place (they were in the program folder the update moved)" if \
            before.replace(*moved) == after else ""  # fmt: skip
    if table == "invoices" and column == "validation":
        return "the review queue's re-check (its stamp)" if _without_recheck(before, moved) == \
            _without_recheck(after, moved) else ""  # fmt: skip
    if table in DERIVED:
        return ("the review queue's re-check" if column[:2] in ("q_", "r_") else
                f"the reports' summary of the invoice worked out again by the new build ({column})")  # fmt: skip
    if table == "settings" and column == "value":
        return _why_setting(now.get("key"))
    reasons = {("users", "last_login"): "the proof's sign-in after the update",
               ("users", "totp_step"): "the proof's sign-in after the update",
               ("sessions", "last_seen"): "the proof's browser, still signed in",
               ("sqlite_sequence", "seq"): "the next row number of a table with rows added"}  # fmt: skip
    if table == "verifications" and column in ("status", "error", "updated_at", "renewed", "tries"):
        return "the AI reviewer's background check tried again (no model running)"
    return reasons.get((table, column), "")


def _without_recheck(text: object, moved: tuple[str, str]) -> object:
    try:
        found = json.loads(str(text).replace(*moved))
    except ValueError:
        return text
    if isinstance(found, dict):
        found.pop("recheck", None)
    return found


def _tables(db: Path) -> dict[str, tuple[list[str], list[str], list[tuple]]]:
    """Every table of ``db`` (read only): its columns, its key's columns and its rows."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        found = {}
        for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name").fetchall():
            info = conn.execute(f'PRAGMA table_info("{name}")').fetchall()
            key = [r[1] for r in sorted(info, key=lambda r: r[5]) if r[5]] or (["name"] if name == "sqlite_sequence"
                                                                             else [])  # fmt: skip
            found[name] = ([r[1] for r in info], key, conn.execute(f'SELECT * FROM "{name}"').fetchall())
        return found
    finally:
        conn.close()


def compare_tables(before: Path, after: Path, moved: tuple[str, str], shown: int = 12) -> dict[str, Any]:
    """Two copies of a database compared table by table, row by row (on each table's key; a table with none, its rows
    whole): the tables the same, those added or gone, and in the others each row added, gone or changed, ``expected``
    when an update always changes it so (by table: what, how many) and ``unexpected`` otherwise (a line each)."""
    old, new = _tables(before), _tables(after)
    found: dict[str, Any] = {"tables": len(new), "same": [], "tables added": sorted(set(new) - set(old)),
                             "tables gone": sorted(set(old) - set(new)), "expected": {}, "unexpected": []}  # fmt: skip
    found["unexpected"] += [f"{name}: the table is gone ({len(old[name][2])} rows)" for name in found["tables gone"]]
    for name in sorted(set(old) & set(new)):
        (cols, key, rows), (new_cols, _new_key, new_rows) = old[name], new[name]
        common = [c for c in cols if c in new_cols]
        if [c for c in cols if c not in new_cols]:
            found["unexpected"].append(f"{name}: columns gone {[c for c in cols if c not in new_cols]}")
        why = collections.Counter()
        if [c for c in new_cols if c not in cols]:
            why[f"new columns of the new build's schema: {', '.join(c for c in new_cols if c not in cols)}"] += 1
        wrong = _compare_rows(name, [dict(zip(cols, r, strict=True)) for r in rows],
                              [dict(zip(new_cols, r, strict=True)) for r in new_rows], [c for c in key if c in common],
                              common, moved, why)  # fmt: skip
        if not why and not wrong:
            found["same"].append(name)
        if why:
            found["expected"][name] = [f"{n} row{'s' * (n != 1)}: {reason}" for reason, n in why.items()]
        more = [f"{name}: ... {len(wrong) - shown} more"] if len(wrong) > shown else []
        found["unexpected"] += wrong[:shown] + more
    return found


def _compare_rows(name: str, rows: list[dict], new_rows: list[dict], key: list[str], common: list[str],
                  moved: tuple[str, str], why: collections.Counter) -> list[str]:  # fmt: skip
    """One table's rows compared (``why`` counts the expected changes by reason): the unexpected ones, a line each."""
    if not key:  # (no key: rows compared whole)
        whole = [collections.Counter(tuple(r.get(c) for c in common) for r in found) for found in (rows, new_rows)]
        left = {"gone": 0, "added": 0}  # the rows no reason explains
        for kind, counted in (("gone", whole[0] - whole[1]), ("added", whole[1] - whole[0])):
            for values, n in counted.items():
                row = dict(zip(common, values, strict=True))
                reason = why_changed(name, kind, row if kind == "gone" else None, row if kind == "added" else None,
                                     moved)  # fmt: skip
                if reason:
                    why[reason] += n
                else:
                    left[kind] += n
        said = [f"{left['gone']} row{'s' * (left['gone'] != 1)} gone" if left["gone"] else "",
                f"{left['added']} added" if left["added"] else ""]  # fmt: skip
        return [f"{name}: {', '.join(s for s in said if s)}"] if any(said) else []
    by_key = [{tuple(r.get(c) for c in key): {c: r.get(c) for c in common} for r in found}
              for found in (rows, new_rows)]  # fmt: skip
    wrong = []
    for k in sorted(set(by_key[0]) | set(by_key[1]), key=repr):
        was, now = by_key[0].get(k), by_key[1].get(k)
        if was == now:
            continue
        kind = "added" if was is None else "gone" if now is None else "changed"
        reason = why_changed(name, kind, was, now, moved)
        if reason:
            why[reason] += 1
            continue
        where = f"{name} {', '.join(f'{c}={v!r}' for c, v in zip(key, k, strict=True))}"
        if kind != "changed":
            wrong.append(f"{where}: {kind}")
            continue
        columns = [c for c in common if was.get(c) != now.get(c) and not _why_column(name, c, was, now, moved)]
        wrong.append(f"{where}: " + "; ".join(f"{c} {_short(was.get(c))} -> {_short(now.get(c))}" for c in columns))
    return wrong


def _short(value: object) -> str:
    text = repr(value)
    return text if len(text) <= 80 else text[:77] + "..."


def copy_database(db: Path, to: Path) -> Path:
    """``db`` copied to ``to`` with SQLite's own backup, read only."""
    to.parent.mkdir(parents=True, exist_ok=True)
    source, target = sqlite3.connect(f"file:{db}?mode=ro", uri=True), sqlite3.connect(to)
    try:
        source.backup(target)
    finally:
        source.close()
        target.close()
    return to


def approved_in(db: Path) -> list[list[Any]]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        return [list(r) for r in conn.execute("SELECT id, invoice_number FROM invoices WHERE status = 'approved' "
                                              "ORDER BY id")]  # fmt: skip
    finally:
        conn.close()


def files_missing(db: Path) -> list[str]:
    """The invoices whose file is not where the database says (deleted ones aside)."""
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT id, source_path FROM invoices WHERE status != 'deleted' ORDER BY id").fetchall()
    finally:
        conn.close()
    return [f"invoice {i}: {where}" for i, where in rows if not where or not Path(where).is_file()]


def files_of(folder: Path) -> dict[str, str]:
    """A data folder's files and their SHA-256, but the database's (compared table by table), its caches and logs, and
    the running dashboard's record."""
    found = {}
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder).as_posix()
        if (not path.is_file() or rel.startswith(("ap_coder.db", ".cache/", "logs/")) or rel.endswith(".log")
                or rel == ".ap_coder_server.json"):  # fmt: skip
            continue
        found[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    return found


def compare_files(before: dict[str, str], after: dict[str, str]) -> dict[str, list[str]]:
    gone = [f"{rel}: gone" for rel in before if rel not in after]
    changed = [f"{rel}: changed" for rel in before if rel in after and after[rel] != before[rel]]
    added = sorted(rel for rel in after if rel not in before)
    return {"unexpected": gone + changed, "backups": [r for r in added if r.startswith("backups/")],
            "added": [r for r in added if not r.startswith("backups/")]}  # fmt: skip


# --- The proof --------------------------------------------------------------------------------------------------------


class Proof:
    """The checks made, each said as it is made (report.json lists them)."""

    def __init__(self) -> None:
        self.checks: list[dict[str, Any]] = []

    def check(self, ok: object, what: str, detail: str = "") -> bool:
        self.checks.append({"ok": bool(ok), "check": what, "detail": detail})
        print(f"  {'ok  ' if ok else 'FAIL'} {what}" + (f": {detail}" if detail else ""), flush=True)
        return bool(ok)

    @property
    def ok(self) -> bool:
        return bool(self.checks) and all(c["ok"] for c in self.checks)


@dataclass
class Run:
    """One proof's folders, its person (environment, browser, ports) and what was seen before the update."""

    args: argparse.Namespace
    work: Path
    proof: Proof
    old: Build | None = None
    new: Build | None = None
    stand_in: StandIn | None = None
    secret: str = ""
    signed_at: float = 0.0
    started: list[subprocess.Popen] = field(default_factory=list)
    seen: dict[str, Any] = field(default_factory=dict)
    old_folder: Path = Path()  # OLD unzipped

    def __post_init__(self) -> None:
        self.home, self.logs, self.pages, self.db = (self.work / x for x in ("home", "logs", "pages", "db"))
        for folder in (self.home / "Downloads", self.logs, self.pages, self.db):
            folder.mkdir(parents=True, exist_ok=True)
        self.env = person_env(self.home)
        self.port, self.try_port = free_port(), free_port()
        self.client = Client(f"http://127.0.0.1:{self.port}")
        self.tried = self.home / TRY

    @property
    def data(self) -> Path:
        """The person's own data folder, as the first start recorded it."""
        try:
            recorded = json.loads((self.home / ".ap_coder" / "settings.json").read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            recorded = {}
        return Path(recorded.get("data_dir") or self.home / paths.DEFAULT_DATA_DIR_NAME)

    def window(self, name: str, folder: Path, *args: str, env: dict[str, str] | None = None,
               answer: str = "") -> Window:  # fmt: skip
        """A double-click on ``folder``'s APProcessor.command (on this proof's port), its window logged as ``name``,
        "Update now?" answered ``answer`` (none: never answered)."""
        command = ["/bin/bash", str(folder / "APProcessor.command"), *args, "--port", str(self.port)]
        made = Window(command, self.home, env or self.env, self.logs / f"{name}.log", answer)
        self.started.append(made.process)
        return made

    def command(self, folder: Path, name: str, *args: str, data: Path | None = None) -> tuple[int, str]:
        """``python -m ap_coder ARGS`` with ``folder``'s own Python, as typed in its folder (``data``: in another data
        folder, AP_PRIVATE_DIR set); its exit code and what it said (written to logs/NAME)."""
        env = {**self.env, **({"AP_PRIVATE_DIR": str(data)} if data else {})}
        done = subprocess.run([str(folder / ".venv" / "bin" / "python"), "-m", "ap_coder", *args], cwd=folder,
                              env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=COMMAND_WAIT)  # fmt: skip
        said = (done.stdout + done.stderr).strip()
        (self.logs / name).write_text(f"$ python -m ap_coder {' '.join(args)}\n{said}\nexit {done.returncode}\n",
                                      encoding="utf-8")  # fmt: skip
        return done.returncode, said

    def serve(self, folder: Path, name: str) -> tuple[Client, subprocess.Popen]:
        """The made-up company's data folder opened with ``folder``'s ``serve``, as the guide has it typed."""
        out = (self.logs / f"{name}.log").open("w", encoding="utf-8")
        env = {**self.env, "AP_PRIVATE_DIR": str(self.tried)}
        process = subprocess.Popen([str(folder / ".venv" / "bin" / "python"), "-m", "ap_coder", "serve", "--port",
                                    str(self.try_port), "--no-browser"], cwd=folder, env=env, stdin=subprocess.DEVNULL,
                                   stdout=out, stderr=subprocess.STDOUT, start_new_session=True)  # fmt: skip
        out.close()
        self.started.append(process)
        client = Client(f"http://127.0.0.1:{self.try_port}")
        first_day.wait_up(client, process)
        return client, process

    def signed_in(self) -> bool:
        """This browser signed in still, or again (the password, then the authenticator's code of a new 30 seconds)."""
        if self.client.request("GET", "/review")[0] == 200:
            return True
        while int(time.time() // 30) <= int(self.signed_at // 30):
            time.sleep(1)
        with contextlib.suppress(RuntimeError):
            self.client.login("admin", first_day.CHOSEN, self.secret)
        self.signed_at = time.time()
        return self.client.request("GET", "/review")[0] == 200


def approved_open(client: Client, approved: list[list[Any]], pages: Path, stage: str) -> list[str]:
    """Each approved invoice's review screen opened and its approved PDF downloaded (kept in ``pages``): the
    problems."""
    problems = []
    for invoice_id, number in approved:
        status, body, _ = client.request("GET", f"/review/{invoice_id}")
        if status != 200 or html.escape(str(number)).encode() not in body:
            problems.append(f"invoice {invoice_id} ({number}): its review screen answered {status}")
        status, pdf, _ = client.request("GET", f"/review/{invoice_id}/approved.pdf")
        if status != 200 or not pdf.startswith(b"%PDF"):
            problems.append(f"invoice {invoice_id} ({number}): its approved PDF answered {status}")
        else:
            (pages / f"{stage}-invoice-{invoice_id}-approved.pdf").write_bytes(pdf)
    return problems


def approve_two(client: Client) -> list[int]:
    """Two of the review queue's invoices approved on their review screen, as a person approves (the first whose
    Approve opens once the screen is checked)."""
    approved = []
    for found in dict.fromkeys(first_day.INVOICE_LINK.findall(first_day.page(client, "/review"))):
        screen = Screen(int(found), first_day.page(client, f"/review/{found}"))
        if not screen.editable:
            continue
        screen.apply(first_day.post(client, f"/review/{found}/check", screen.data("inv")))
        if not screen.allow:
            continue
        url, fields = screen.forms["inv"]["action"], screen.data("inv")
        answer = first_day.post(client, url, fields)
        if answer.get("ask_why"):  # (asked why a field was changed: skipped, as a person may)
            answer = first_day.post(client, url, [*fields, ("why_done", "1")])
        first_day.check(answer.get("redirect") or answer.get("ok"), f"invoice {found} was not approved: {answer}")
        approved.append(int(found))
        if len(approved) == APPROVED:
            return approved
    raise Failed(f"only {len(approved)} of the made-up company's invoices could be approved: {approved}")


def check_workbook(client: Client, book: Path, load: bool) -> dict[str, Any]:
    """Setup -> JD Edwards files, "Check the file" with ``book``: its summary; ``load``: then "Load all"."""
    said = first_day.send(client, "/jde-files/workbook/upload", [book])
    summary = workbook_said(first_day.page(client, said["redirect"].split("#")[0]))
    if load:
        token = urllib.parse.parse_qs(urllib.parse.urlsplit(said["redirect"]).query).get("workbook", [""])[0]
        loaded = first_day.post(client, "/jde-files/workbook/load", {"token": token})
        words = first_day.plain(first_day.page(client, loaded["redirect"].split("#")[0]))
        first_day.check(re.search(rf"Loaded \d+ sheets? of {re.escape(book.name)}", words),
                        f"Load all did not say what it loaded: {words[:600]}")  # fmt: skip
        summary["loaded"] = re.search(rf"Loaded \d+ sheets? of {re.escape(book.name)}[^.]*\.", words).group(0)
    return summary


# The made-up company's ReportsNow workbook (every report of the build sheet), as the old build's own bench writes it.
WORKBOOK = ("import sys; from pathlib import Path; from ap_coder.bench import jde_exports as made; "
            "truth = made.write_all(Path(sys.argv[1]) / 'exports', year=int(sys.argv[2])); "
            "(Path(sys.argv[1]) / made.MASTER_FILE).write_bytes(made.master_workbook_xlsx(truth))")  # fmt: skip


def first_use(r: Run) -> None:
    """OLD unzipped and started the first time, signed in to and used: the demo; the made-up company with two of its
    invoices approved and its workbook loaded; what Settings -> About and the approved invoices show."""
    first_day.step(f"{r.old.tag}: unzipped into {r.home / 'Downloads'} and double-clicked (its first start)")
    r.old_folder = unzip(r.old, r.home / "Downloads")
    window = r.window("1-first-start", r.old_folder, "--no-update")
    running = window.wait_for(RUNNING, FIRST_WAIT)
    r.proof.check(running, "the first start", f"running at {running} after {window_said(window.lines)['seconds']} s"
                  if running else "it did not start: see logs/1-first-start.log")  # fmt: skip
    first_day.check(running, f"{r.old.tag} did not start")
    r.secret, r.signed_at = first_day.first_sign_in(r.client), time.time()
    code, said = r.command(r.old_folder, "demo.log", "demo")
    first_day.check(code == 0 and "Demo loaded" in said, f"demo: {said[-600:]}")
    first_day.seen(said.splitlines()[-1])
    r.seen["version before"] = version_line(first_day.page(r.client, "/settings/about"))
    r.seen["approved"] = {"own": approved_in(r.data / "ap_coder.db")}

    first_day.step(f"the made-up company in {r.tried}: demo --company, serve, two approved, the workbook loaded")
    code, said = r.command(r.old_folder, "demo-company.log", "demo", "--company", data=r.tried)
    first_day.check(code == 0 and "made-up company is loaded" in said, f"demo --company: {said[-600:]}")
    client, serving = r.serve(r.old_folder, "3-serve-made-up-company")
    first_day.seen(f"approved on the review screen: invoices {approve_two(client)}")
    book = r.work / "workbook" / "jde_reports.xlsx"  # as OLD's own bench writes it (the build sheet's reports)
    done = subprocess.run([str(r.old_folder / ".venv" / "bin" / "python"), "-c", WORKBOOK, str(book.parent),
                           str(dt.date.today().year - 1)], cwd=r.old_folder, env=r.env, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=COMMAND_WAIT)  # fmt: skip
    first_day.check(done.returncode == 0 and book.is_file(), f"the workbook was not written: {done.stderr[-600:]}")
    r.seen["workbook"] = check_workbook(client, book, load=True)
    first_day.seen(f"{book.name}: {r.seen['workbook']['verdict']} {r.seen['workbook']['loaded']}")
    r.seen["approved"]["made-up company"] = approved_in(r.tried / "ap_coder.db")
    problems = approved_open(client, r.seen["approved"]["made-up company"], r.pages, "before-made-up")
    first_day.stop(client, r.tried, serving)
    problems += approved_open(r.client, r.seen["approved"]["own"], r.pages, "before-own")
    count = sum(len(v) for v in r.seen["approved"].values())
    r.proof.check(r.seen["version before"] and count >= 3 and not problems, f"{r.old.tag} used",
                  f"Version {r.seen['version before']!r}; {count} approved invoices open and their PDFs download"
                  + (f"; {problems}" if problems else ""))  # fmt: skip
    first_day.stop(r.client, r.data, window.process)
    window.close()


def update_start(r: Run) -> Window:
    """OLD started again and "Update now?" answered: what the window said."""
    asked = "GitHub" if releases.TAG.match(r.args.new) and newest_tag() == r.new.tag else "a stand-in for GitHub"
    env = r.env
    if asked != "GitHub":
        r.stand_in = StandIn(r.new)
        env = with_stand_in(r.env, r.stand_in, r.work / "stand-in")
    r.seen["asked"] = asked
    first_day.step(f"{r.old.tag} double-clicked again; {QUESTION!r} answered {r.args.answer} (the launcher asks "
                   f"{asked})")  # fmt: skip
    window = r.window("2-update-start", r.old_folder, env=env, answer=r.args.answer)
    running = window.wait_for(RUNNING, UPDATE_WAIT)
    said = r.seen["window"] = window_said(window.lines)
    r.proof.check(f"(build {r.new.commit[:7]}," in said["offer"], "the question", said["offer"] or
                  "not asked: see logs/2-update-start.log")  # fmt: skip
    if r.args.answer == "y":
        right = said["update"].startswith(f"Updated AP Coder from {r.old.version} (build {r.old.commit[:7]}") and \
            f" to {r.new.version} (build {r.new.commit[:7]}" in said["update"]  # fmt: skip
        r.proof.check(right, "the update line", said["update"] or "none: see logs/2-update-start.log")
    else:
        r.proof.check(said["kept"] and not said["update"], "the old build kept", said["kept"] or "not said")
    r.proof.check(running, "the time it took", f"{said['seconds']} s from the answer to {running}" if running else
                  "AP Coder did not start: see logs/2-update-start.log")  # fmt: skip
    if r.stand_in:
        taken = f"/dl/{r.new.top}.zip" in r.stand_in.asked
        r.proof.check(r.stand_in.asked and taken == (r.args.answer == "y"), "the stand-in for GitHub was asked",
                      ", ".join(r.stand_in.asked))  # fmt: skip
    first_day.check(running, "AP Coder did not start after the answer")
    return window


def after_update(r: Run, window: Window) -> Path:
    """The build in use after the answer: its dashboard, Settings -> About, the approved invoices, the made-up company
    and its workbook, the doctor. Its folder."""
    in_use = r.home / "Downloads" / r.new.top if r.args.answer == "y" else r.old_folder
    build = r.new if r.args.answer == "y" else r.old
    if r.args.answer == "y":
        r.proof.check(in_use.is_dir(), "the new folder", f"{in_use}; the old one "
                      + ("left" if r.old_folder.exists() else "cleaned up"))  # fmt: skip
    else:
        r.proof.check(not (r.home / "Downloads" / r.new.top).exists() and r.old_folder.is_dir(), "no new folder",
                      f"{r.old_folder} still in use")  # fmt: skip
        skipped = json.loads((r.home / ".ap_coder" / "settings.json").read_text(encoding="utf-8")).get("update_skipped")
        r.proof.check(skipped == r.new.tag, "not asked about it again", f"update_skipped: {skipped}")
    health = _json(r.client.request("GET", "/healthz")[1])
    r.proof.check(str(build.commit).startswith(str(health.get("build") or "-")), "the dashboard is the build in use",
                  f"/healthz says build {health.get('build')} ({build.tag})")  # fmt: skip
    still = r.client.request("GET", "/review")[0] == 200
    r.proof.check(r.signed_in(), "signed in", "still, after the answer" if still else
                  "again, with the password and the authenticator's code")  # fmt: skip
    after = r.seen["version after"] = version_line(first_day.page(r.client, "/settings/about"))
    before = r.seen["version before"]
    same = after == before
    r.proof.check(f"build {build.commit[:7]}" in after and same == (r.args.answer == "n"),
                  "Settings -> About, Version", f"{before} -> {after}")  # fmt: skip
    problems = approved_open(r.client, r.seen["approved"]["own"], r.pages, "after-own")
    first_day.stop(r.client, r.data, window.process)
    code = window.close()
    r.proof.check(code == 0, "the window closed", f"exit {code}")

    first_day.step(f"the made-up company opened with {build.tag}'s serve; the workbook checked again")
    client, serving = r.serve(in_use, "4-serve-made-up-company-after")
    problems += approved_open(client, r.seen["approved"]["made-up company"], r.pages, "after-made-up")
    count = sum(len(v) for v in r.seen["approved"].values())
    r.proof.check(not problems, "the approved invoices open and their PDFs download", f"{count} of {count}"
                  if not problems else "; ".join(problems))  # fmt: skip
    again = r.seen["workbook after"] = check_workbook(client, r.work / "workbook" / "jde_reports.xlsx", load=False)
    now = {name: (rows, loads) for name, rows, loads in again["sheets"]}
    lost = [f"{name} ({rows} rows)" for name, rows, loads in r.seen["workbook"]["sheets"] if loads and now.get(name)
            != (rows, True)]  # fmt: skip
    r.proof.check(again["sheets"] and not lost, "the workbook check", again["verdict"] + (f"; no longer loading: "
                  f"{lost}" if lost else " (each sheet loaded before still loads)"))  # fmt: skip
    first_day.stop(client, r.tried, serving)

    for which, data in (("own", None), ("made-up company", r.tried)):
        code, said = r.command(in_use, f"doctor-{which.replace(' ', '-')}.txt", "doctor", data=data)
        found = r.seen[f"doctor {which}"] = doctor_said(said)
        listed = "".join(f"\n         [WARN] {w}" for w in found["warnings"]) + "".join(
            f"\n         [FAIL] {f}" for f in found["failures"])  # fmt: skip
        r.proof.check(found["summary"] and not found["failures"], f"doctor in {in_use.name} ({which} data folder)",
                      f"{found['summary'] or said[-300:]}{listed}")  # fmt: skip
    return in_use


def data_kept(r: Run, in_use: Path) -> None:
    """Each data folder's database compared with its copy from before, table by table; its files; its invoices'
    files."""
    moved = (str(r.old_folder), str(in_use))
    for which, data in (("own", r.data), ("made-up company", r.tried)):
        name = which.replace(" ", "-")
        after = copy_database(data / "ap_coder.db", r.db / f"after-{name}.db")
        found = r.seen[f"tables {which}"] = compare_tables(r.db / f"before-{name}.db", after, moved)
        changed = ", ".join(f"{t} ({'; '.join(w)})" for t, w in found["expected"].items())
        r.proof.check(not found["unexpected"], f"the data kept, table by table ({which} data folder)",
                      f"{found['tables']} tables, {len(found['same'])} the same"
                      + (f"; changed as expected: {changed}" if changed else "")
                      + (f"; new tables: {found['tables added']}" if found["tables added"] else "")
                      + "".join(f"\n         NOT EXPECTED: {u}" for u in found["unexpected"]))  # fmt: skip
        files = r.seen[f"files {which}"] = compare_files(r.seen[f"files before {which}"], files_of(data))
        missing = files_missing(after)
        r.proof.check(not files["unexpected"] and not missing, f"the files kept ({which} data folder)",
                      f"{len(r.seen[f'files before {which}'])} files the same, every invoice's file there"
                      + (f"; the update's backups: {len(files['backups'])} files" if files["backups"] else "")
                      + (f"; added: {files['added']}" if files["added"] else "")
                      + "".join(f"\n         NOT EXPECTED: {u}" for u in files["unexpected"] + missing))  # fmt: skip


def notes_and_logs(r: Run, in_use: Path) -> None:
    """The notes folder's README.txt (where the build in use writes one), and the lines of every window and log that
    look like an error."""
    readme = r.home / ".ap_coder" / paths.NOTES_README
    writes = "NOTES_README" in (in_use / "ap_coder" / "core" / "paths.py").read_text(encoding="utf-8")
    text = readme.read_text(encoding="utf-8") if readme.is_file() else ""
    r.seen["readme"] = text
    r.proof.check(
        bool(text) or not writes,
        "the notes folder's README.txt",
        text.splitlines()[0] if text else "not written by this build" if not writes else f"no {readme}",
    )
    logs = sorted({*r.logs.glob("*.log"), *(r.home / ".ap_coder" / "logs").glob("*.log"),
                   *(r.data / "logs").glob("*.log"), *(r.tried / "logs").glob("*.log")})  # fmt: skip
    found = [f"{log.relative_to(r.work)}: {line.strip()}" for log in logs
             for line in error_lines(log.read_text(encoding="utf-8", errors="replace"))]  # fmt: skip
    r.seen["log errors"] = found
    r.proof.check(not found, "no log line looks like an error", f"{len(logs)} logs read" if not found else
                  "".join(f"\n         {line[:300]}" for line in found[:40]))  # fmt: skip


def snapshot(r: Run, stage: str) -> None:
    for which, data in (("own", r.data), ("made-up company", r.tried)):
        copy_database(data / "ap_coder.db", r.db / f"{stage}-{which.replace(' ', '-')}.db")
        r.seen[f"files {stage} {which}"] = files_of(data)


def prove(r: Run) -> None:
    r.old, r.new = obtain(r.args.old, r.work / "downloads"), obtain(r.args.new, r.work / "downloads")
    r.proof.check(r.old.tag != r.new.tag, "the two builds", f"{r.old.tag} ({r.old.said()}, {r.old.zip.name}) -> "
                  f"{r.new.tag} ({r.new.said()}, {r.new.zip.name})")  # fmt: skip
    first_day.check(r.old.tag != r.new.tag, "--from and --to are the same build")
    first_use(r)
    snapshot(r, "before")
    window = update_start(r)
    in_use = after_update(r, window)
    data_kept(r, in_use)
    notes_and_logs(r, in_use)


def pilot_tester(r: Run) -> dict[str, Any]:
    said = r.seen.get("window") or {}
    return {"question": said.get("offer", ""), "question line": said.get("question", ""), "answer": r.args.answer,
            "update line": said.get("update", ""), "kept line": said.get("kept", ""), "seconds": said.get("seconds"),
            "version before": r.seen.get("version before", ""),
            "version after": r.seen.get("version after", "")}  # fmt: skip


def report(r: Run) -> None:
    """The short report: the checks' count, report.json, and the lines for the pilot tester's script."""
    told = pilot_tester(r)
    failed = [c["check"] for c in r.proof.checks if not c["ok"]]
    found = {"ok": r.proof.ok, "from": r.args.old, "to": r.args.new, "answer": r.args.answer,
             "builds": {k: {"tag": b.tag, "zip": str(b.zip), "sha256": b.sha256, "said": b.said()}
                        for k, b in (("from", r.old), ("to", r.new)) if b},
             "checks": r.proof.checks, "pilot tester": told,
             **{k: v for k, v in r.seen.items() if not k.startswith("files before")}}  # fmt: skip
    (r.work / "report.json").write_text(json.dumps(found, indent=1, default=str), encoding="utf-8")
    print()
    print(f"{'PROVED' if r.proof.ok else 'NOT PROVED'}: {len(r.proof.checks) - len(failed)} of "
          f"{len(r.proof.checks)} checks passed" + (f"; failed: {', '.join(failed)}" if failed else "")
          + f". The report: {r.work / 'report.json'}")  # fmt: skip
    print()
    print("For the pilot tester's script")
    print(f"  The window asks:  {told['question'] or '(nothing was asked)'}")
    if told["question line"]:
        print(f"                    {told['question line']}")
    if r.args.answer == "y":
        print(f"  After Enter (yes): {told['update line'] or '(no update line)'}")
    else:
        print(f"  After n:          {told['kept line'] or '(nothing said)'}")
    if told["seconds"] is not None:
        print(f"  About {told['seconds']:.0f} seconds later: AP Coder is running (the browser opens)")
    print(f"  Settings -> About, Version: {told['version after'] or '?'}  (before: {told['version before'] or '?'})")


def cleanup(r: Run) -> None:
    """Whatever is still running stopped; the program folders and the home's pip cache deleted unless --keep."""
    if r.stand_in:
        r.stand_in.stop()
    for process in r.started:
        with contextlib.suppress(OSError):
            os.killpg(process.pid, signal.SIGTERM)
    if not r.args.keep:
        for folder in [*(r.home / "Downloads").glob("*AP_Processer-*"), r.home / ".cache"]:
            shutil.rmtree(folder, ignore_errors=True)


def refusal(a: argparse.Namespace) -> str:
    """Why this proof can't run here, "" when it can."""
    if WINDOWS:
        return ("release_proof.py runs on Linux or a Mac only: it answers the launcher's \"Update now?\" in a "
                "pseudo-terminal, which Windows has not. On Windows, update as the pilot tester's script "
                "says.")  # fmt: skip
    for option, given in (("--from", a.old), ("--to", a.new)):
        if releases.TAG.match(given):
            if not offline.internet_allowed():
                return (f"{option} {given} is downloaded from its GitHub release: set AP_ALLOW_INTERNET=1 to allow it "
                        "(or give a ZIP on this computer).")  # fmt: skip
        elif not (Path(given).is_file() and given.endswith(".zip")):
            return f"{option} {given} is neither a release tag (v0.3.0-build.<commit>) nor a ZIP on this computer."
    if a.work and ((a.work / "home").exists() or (a.work / "report.json").exists()):
        return f"--work {a.work} holds an earlier run: give a new folder."
    if not offline.internet_allowed():  # (the launchers it starts get no AP_ setting: AP_ALLOW_INTERNET=0 not either)
        return ("Both first setups download their Python packages from pypi.org, as a person's first start does: set "
                "AP_ALLOW_INTERNET=1 to allow it.")  # fmt: skip
    return ""


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--from", dest="old", required=True, help="the older build: a release tag or a ZIP")
    p.add_argument("--to", dest="new", required=True, help="the newer build: a release tag or a ZIP")
    p.add_argument("--answer", choices=("y", "n"), default="y", help='the answer to "Update now?" (default y)')
    p.add_argument("--work", type=Path, help="a new folder for the run (default: a new temporary folder)")
    p.add_argument("--keep", action="store_true", help="keep the program folders (each about 900 MB) and the pip cache")
    a = p.parse_args(argv)
    why = refusal(a)
    if why:
        print(why, file=sys.stderr)
        return 2
    work = (a.work or Path(tempfile.mkdtemp(prefix="release-proof-"))).resolve()
    work.mkdir(parents=True, exist_ok=True)
    r = Run(a, work, Proof())
    print(f'Release proof: {a.old} -> {a.new}, "Update now?" answered {a.answer}, in {work}', flush=True)
    try:
        prove(r)
    except Failed as exc:
        r.proof.check(False, "stopped", str(exc))
    except BaseException as exc:  # (not foreseen: a failure too, never "PROVED" with the checks made until then)
        r.proof.check(False, "stopped", f"{type(exc).__name__}: {exc}")
        raise
    finally:
        cleanup(r)
        report(r)
    return 0 if r.proof.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
