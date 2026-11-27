"""The update test of a release in one command (scripts/release_proof.py, D-337): its pieces with made-up inputs (the
data compared table by table and the rows an update always changes, the window's update line and the Version line
read, the log scan, the stand-in for GitHub the launcher's own releases module asks, the launcher's window in a
pseudo-terminal, the refusals), and the whole of it on two GitHub-like ZIPs of this checkout (slow: both first setups
download their packages from PyPI, so only with AP_ALLOW_INTERNET=1)."""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from tests.scripts import load_script

ROOT = Path(__file__).resolve().parents[2]
ONLINE = os.environ.get("AP_ALLOW_INTERNET") == "1"  # (read before tests/conftest.py clears it for each test)
POSIX = os.name != "nt"

proof = load_script("release_proof", "ap_script_release_proof")


# --- The data, table by table -----------------------------------------------------------------------------------------

OLD_FOLDER = "/home/me/Downloads/AP_Processer-0.3.0-build.aaaaaaa"
NEW_FOLDER = "/home/me/Downloads/AP_Processer-0.3.0-build.bbbbbbb"


def _database(path: Path, rows: dict[str, list[tuple]]) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        "CREATE TABLE invoices (id INTEGER PRIMARY KEY, status TEXT, vendor_name TEXT, source_path TEXT, meta TEXT,"
        " validation TEXT);"
        "CREATE TABLE events (id INTEGER PRIMARY KEY AUTOINCREMENT, invoice_id INTEGER, action TEXT, actor TEXT,"
        " detail TEXT, created_at TEXT);"
        "CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT);"
        "CREATE TABLE invoice_summary (id INTEGER PRIMARY KEY, r_stamp TEXT, r_found TEXT);"
        "CREATE TABLE vendors (vendor_key TEXT PRIMARY KEY, notes TEXT);"
        "CREATE TABLE feedback (id INTEGER PRIMARY KEY, field TEXT, value TEXT);"
        "CREATE TABLE seen (word TEXT);"  # (no key: its rows are compared whole)
    )
    for table, values in rows.items():
        for row in values:
            conn.execute(f"INSERT INTO {table} VALUES ({', '.join('?' * len(row))})", row)
    conn.commit()
    conn.close()


def _invoice(number: int, folder: str, vendor: str, stamp: str, spaced: bool = True) -> tuple:
    validation = {"issues": [{"code": "PAYMENT_OVERDUE"}], "recheck": {"stamp": stamp}}
    return (number, "approved", vendor, f"{folder}/samples/{number}.pdf", json.dumps({"source": f"{folder}/x"}),
            json.dumps(validation, separators=(", ", ": ") if spaced else (",", ":")))  # fmt: skip


BEFORE = {
    "invoices": [_invoice(1, OLD_FOLDER, "Northwind", "a1"), _invoice(2, OLD_FOLDER, "Chinook", "a1")],
    "events": [(1, 1, "approved", "Ann", "{}", "2026-11-25T18:00:00")],
    "settings": [("summary_logged", "12"), ("approval_limit", "25000"), ("schema_version", "130")],
    "invoice_summary": [(1, "", "None")],
    "vendors": [("northwind", "Taught before the update")],
    "feedback": [(1, "gl_code", "6100"), (2, "gl_code", "6200")],
    "seen": [("one",), ("two",)],
}


def _after(tmp_path: Path) -> tuple[Path, Path]:
    before, after = tmp_path / "before.db", tmp_path / "after.db"
    _database(before, BEFORE)
    changed = {**BEFORE}
    changed["invoices"] = [
        _invoice(1, NEW_FOLDER, "Northwind", "b2", spaced=False),  # moved, checked again
        _invoice(2, NEW_FOLDER, "Chinook Ltd", "a1"),  # renamed: not the update's doing
    ]
    changed["events"] = [*BEFORE["events"], (2, None, "files_moved", "AP Coder update", "{}", "2026-11-25T18:41:49"),
                         (3, None, "login", "Alex Owner", "{}", "2026-11-25T18:41:59")]  # fmt: skip
    changed["settings"] = [("summary_logged", "14"), ("approval_limit", "30000"), ("schema_version", "131")]
    changed["invoice_summary"] = [(1, "2e705459", '[["info","PAYMENT_OVERDUE"]]')]
    changed["feedback"] = BEFORE["feedback"][:1]  # a correction learned before the update: gone
    changed["seen"] = [("one",), ("three",)]
    _database(after, changed)
    conn = sqlite3.connect(after)
    conn.execute("CREATE TABLE answer_wants (id INTEGER PRIMARY KEY)")  # the new build's schema
    conn.commit()
    conn.close()
    return before, after


def test_the_data_is_compared_table_by_table_and_what_an_update_changes_is_named(tmp_path):
    before, after = _after(tmp_path)
    found = proof.compare_tables(before, after, (OLD_FOLDER, NEW_FOLDER))
    assert found["same"] == ["vendors"]
    assert found["tables added"] == ["answer_wants"] and found["tables gone"] == []
    expected = found["expected"]
    assert any("files_moved" in why for why in expected["events"])
    assert any("sign-in" in why for why in expected["events"])
    assert any("files' new place" in why for why in expected["invoices"])
    assert any("re-check" in why for why in expected["invoices"])
    assert any("re-check" in why for why in expected["invoice_summary"])
    assert any("schema" in why for why in expected["settings"])
    unexpected = "\n".join(found["unexpected"])
    assert "invoices id=2: vendor_name" in unexpected and "Chinook Ltd" in unexpected
    assert "settings key='approval_limit'" in unexpected
    assert "feedback id=2: gone" in unexpected
    assert "seen: 1 row gone, 1 added" in unexpected
    assert "invoices id=1" not in unexpected and "events" not in unexpected
    assert not proof.compare_tables(before, before, (OLD_FOLDER, NEW_FOLDER))["unexpected"]


@pytest.mark.parametrize(
    ("table", "kind", "was", "now", "named"),
    [
        ("events", "added", None, {"action": "files_moved", "actor": "AP Coder update"}, "files_moved"),
        ("events", "added", None, {"action": "login", "actor": "Alex Owner"}, "sign-in"),
        ("events", "added", None, {"action": "deleted", "actor": "Alex Owner"}, ""),
        ("login_attempts", "added", None, {"ok": 1}, "sign-in"),
        ("sessions", "changed", {"last_seen": 1.0, "user_id": 1}, {"last_seen": 2.0, "user_id": 1}, "browser"),
        ("users", "changed", {"last_login": "a", "totp_step": 1}, {"last_login": "b", "totp_step": 2}, "sign-in"),
        ("users", "changed", {"password_hash": "a"}, {"password_hash": "b"}, ""),
        ("queue_changes", "added", None, {"seq": 13, "id": 3}, "re-check"),
        ("verifications", "changed", {"status": "waiting", "error": "", "updated_at": "1"},
         {"status": "skipped", "error": "already dealt with", "updated_at": "2"}, "AI reviewer"),
        ("verifications", "changed", {"invoice_id": 1, "verdict": "ok"}, {"invoice_id": 1, "verdict": "bad"}, ""),
        ("ephemeral", "added", None, {"kind": "jde-workbook"}, "short-lived"),
        ("invoice_search_data", "changed", {"block": b"a"}, {"block": b"b"}, "search index"),
        ("invoice_search_idx", "gone", {"segid": 1}, None, "search index"),
        ("invoice_search_content", "changed", {"c0": "a"}, {"c0": "b"}, ""),
        ("report_answers", "added", None, {"name": "recurring", "asked": "2df3"}, "reports' answers"),
        ("settings", "changed", {"key": "summary_generation/all", "value": "1"},
         {"key": "summary_generation/all", "value": "2"}, "re-check"),
        ("sqlite_sequence", "changed", {"name": "events", "seq": 26}, {"name": "events", "seq": 28}, "row number"),
        ("vendors", "changed", {"notes": "a"}, {"notes": "b"}, ""),
    ],
)  # fmt: skip
def test_the_rows_an_update_always_changes_are_named_and_nothing_else(table, kind, was, now, named):
    why = proof.why_changed(table, kind, was, now, (OLD_FOLDER, NEW_FOLDER))
    assert (named in why) if named else why == ""


# --- What the window and the pages said -------------------------------------------------------------------------------

UPDATE = [
    (0.0, "RUN /bin/bash APProcessor.command --port 8681"),
    (1.0, ""),
    (1.0, "A newer AP Coder is available: 0.3.0 (build 0a749b3, 2026-11-25). What's new:"),
    (1.0, "  - #166 The audit's cleanup (13 batches, nothing the app decides changes) and the notes folder hidden..."),
    (1.0, "Update now? Your invoices and settings are kept (a backup is made first). [Y/n] "),
    (1.1, "y"),
    (2.2, "AP Coder 0.3.0 (build 0a749b3, 2026-11-25) is in /home/me/Downloads/AP_Processer-0.3.0-build.0a749b3: "
          "starting it. It takes over from this one."),
    (4.4, "Updated AP Coder from 0.3.0 (build efbd375f, 2026-11-24) to 0.3.0 (build 0a749b3c, 2026-11-25)."),
    (31.7, "  ✓ Cleaned up the older AP Coder: 862 MB freed"),
    (33.4, "AP Coder is running at http://127.0.0.1:8681"),
]  # fmt: skip


def test_the_update_line_and_the_time_it_took_are_read_from_the_window():
    said = proof.window_said(UPDATE)
    assert said["offer"] == "A newer AP Coder is available: 0.3.0 (build 0a749b3, 2026-11-25)."
    assert said["update"] == "Updated AP Coder from 0.3.0 (build efbd375f, 2026-11-24) to 0.3.0 (build 0a749b3c, " \
                             "2026-11-25)."  # fmt: skip
    assert said["running"] == "http://127.0.0.1:8681" and said["seconds"] == 32.4 and said["kept"] == ""
    no = [*UPDATE[:5], (1.1, "n"), (1.1, "Starting this version. You are not asked about this one again, nor about a "
                                         "newer one before tomorrow."), (3.0, UPDATE[-1][1])]  # fmt: skip
    said = proof.window_said(no)
    assert said["update"] == "" and said["kept"].startswith("Starting this version.") and said["seconds"] == 2.0
    assert proof.window_said(UPDATE[:1])["offer"] == ""


def test_the_question_for_the_pilot_testers_script_is_the_one_the_window_said(tmp_path, capsys):
    """What "For the pilot tester's script" says the window asks is the line the old launcher said, not a copy of it
    kept in the script: a launcher whose question is worded otherwise is shown as it is (B-3060)."""
    assert proof.window_said(UPDATE)["question"] == ("Update now? Your invoices and settings are kept (a backup is "
                                                     "made first). [Y/n]")  # fmt: skip
    worded = [(s, t.replace("Your invoices and settings are kept", "Your data stays")) for s, t in UPDATE]
    r = proof.Run(argparse.Namespace(old="a.zip", new="b.zip", answer="y", keep=False), tmp_path, proof.Proof())
    r.seen["window"] = proof.window_said(worded)
    proof.report(r)
    said = capsys.readouterr().out
    assert "Update now? Your data stays (a backup is made first). [Y/n]" in said
    assert "Your invoices and settings are kept" not in said


def test_the_version_line_is_read_from_settings_about():
    page = ('<dl class="about"><dt>Version</dt><dd>0.3.0 &middot; build 0a749b3c, 2026-11-25 17:46</dd>'
            "<dt>Python</dt><dd>3.12.13</dd></dl>")  # fmt: skip
    assert proof.version_line(page) == "0.3.0 · build 0a749b3c, 2026-11-25 17:46"
    assert proof.version_line("<dt>Python</dt><dd>3.12</dd>") == ""


def test_the_doctor_is_read_as_failures_and_warnings():
    said = proof.doctor_said(
        "## AP Coder doctor report\n\n[PASS] python        3.12.13\n[WARN] local model   not running at "
        "http://127.0.0.1:1234/v1\n[WARN] page reader   Nothing answered\n[SKIP] JD Edwards API  off\n\n"
        "0 failure(s), 2 warning(s)"
    )
    assert said == {"summary": "0 failure(s), 2 warning(s)", "failures": [],
                    "warnings": ["local model not running at http://127.0.0.1:1234/v1",
                                 "page reader Nothing answered"]}  # fmt: skip
    assert proof.doctor_said("[FAIL] data folder   not writable\n\n1 failure(s), 0 warning(s)")["failures"] == [
        "data folder not writable"
    ]


def test_the_workbook_summary_is_read_sheet_by_sheet():
    page = """<div id="workbook-summary"><div class="flash ok imp-verdict"><span class="ms">check_circle</span>
      <div>10 sheets of 8 reports can be loaded; the others say why not.</div></div>
      <table class="apc-table" id="workbook-sheets"><thead><tr><th>Sheet</th></tr></thead><tbody>
      <tr><td><b>Accounts</b><br><span>column names on row 2</span></td><td>Chart of accounts</td>
          <td class="r">1,250<br><span>by company: 00210 250</span></td><td>7 found <span class='apc-pill ok'>
          <span aria-hidden='true'>check</span>Loads</span></td></tr>
      <tr><td><b>Notes</b></td><td>Not recognised</td><td class="r">0</td><td><span class='apc-pill warn'>
          <span aria-hidden='true'>warning</span>Not loaded</span></td></tr></tbody></table></div>"""
    assert proof.workbook_said(page) == {
        "verdict": "10 sheets of 8 reports can be loaded; the others say why not.",
        "sheets": [["Accounts", 1250, True], ["Notes", 0, False]],
    }


def test_the_log_lines_that_look_like_an_error_are_found():
    text = "\n".join([
        "04:55:16 +   3.1s First-time setup: ... or their mirrors huggingface.co and github.com if that fails, once",
        "  ✓ Self-check OK: 10 of 10 sample invoices read right",
        "  ! LM Studio isn't running yet: until it is, invoices are read by OCR only and wait for a person.",
        "0 failure(s), 2 warning(s)",
        "Traceback (most recent call last):",
        "sqlite3.OperationalError: database is locked",
        "ERROR: pip's dependency resolver does not currently take into account all the packages",
        "  ✗ The update was not installed: the download didn't finish.",
        "Couldn't check for a newer version of AP Coder; starting this one.",
        "It was not installed: nobody is at this window to say yes. Starting this version.",
        "AP Coder stopped. The message above says what to do.",
    ])  # fmt: skip
    assert proof.error_lines(text) == text.splitlines()[4:]


# --- The stand-in for GitHub, the launcher's window -------------------------------------------------------------------

TOP = "AP_Processer-0.3.0-build.b2b2b2b"


def _release_zip(path: Path, top: str = TOP, stamp: str = "b2b2b2b2 2026-11-20T05:00:00-06:00") -> Path:
    """A ZIP laid out as a release's: one folder, AP Coder in it, ap_coder/BUILD stamped as git archive stamps it."""
    files = {f"{top}/pyproject.toml": '[project]\nname = "ap-coder"\nversion = "0.3.0"\n',
             f"{top}/ap_coder/__init__.py": "", f"{top}/ap_coder/BUILD": f"{stamp}\n", f"{top}/scripts/launch.py": "",
             f"{top}/APProcessor.bat": "", f"{top}/APProcessor.command": "#!/bin/bash\n"}  # fmt: skip
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in files.items():
            info = zipfile.ZipInfo(name)
            info.external_attr = (0o100755 if name.endswith(".command") else 0o100644) << 16
            archive.writestr(info, text)
    path.write_bytes(buffer.getvalue())
    return path


def test_a_local_zip_is_offered_and_taken_by_the_launchers_own_releases_module_through_the_stand_in(tmp_path):
    """The launcher's scripts/releases.py, unchanged, in a Python started with the stand-in's sitecustomize on
    PYTHONPATH (as the update start's is): it asks 127.0.0.1, finds the local ZIP as a release, and takes it."""
    build = proof.build_of(_release_zip(tmp_path / "new.zip"), "new.zip", tmp_path / "ids")
    assert (build.tag, build.top, build.version, build.commit) == ("v0.3.0-build.b2b2b2b", TOP, "0.3.0", "b2b2b2b2")
    assert build.committed == "2026-11-20T11:00:00"
    stand_in = proof.StandIn(build)
    try:
        env = proof.with_stand_in(dict(os.environ), stand_in, tmp_path / "site")
        beside, data = tmp_path / "installs" / "AP_Processer-0.3.0-build.a1a1a1a", tmp_path / "data"
        beside.mkdir(parents=True)
        data.mkdir()
        asked = subprocess.run(
            [sys.executable, "-c", "import sys; from pathlib import Path; sys.path[:0] = sys.argv[1:3]; "
             "import releases; r = releases.newest(); print(r.tag, r.zip_name, r.committed, r.shown()); "
             "print(releases.take(r, Path(sys.argv[3]), Path(sys.argv[4]), lambda _said: None))",
             str(ROOT), str(ROOT / "scripts"), str(beside), str(data)],
            env=env, capture_output=True, text=True, timeout=60)  # fmt: skip
    finally:
        stand_in.stop()
    assert asked.returncode == 0, asked.stderr
    offered, taken = asked.stdout.splitlines()
    assert offered == f"v0.3.0-build.b2b2b2b {TOP}.zip 2026-11-20T11:00:00 0.3.0 (build b2b2b2b, 2026-11-20)"
    assert Path(taken) == beside.parent / TOP and (Path(taken) / "ap_coder" / "BUILD").is_file()
    assert any(path.startswith("/repos/MNabikhel/AP_Processer/releases") for path in stand_in.asked)
    assert f"/dl/{TOP}.zip" in stand_in.asked and "/dl/SHA256SUMS" in stand_in.asked


def test_a_zip_that_is_not_laid_out_as_a_release_is_refused(tmp_path):
    with pytest.raises(proof.Failed, match="not stamped"):
        proof.build_of(_release_zip(tmp_path / "a.zip", stamp="$Format:%h %cI$"), "a.zip", tmp_path / "ids")
    with pytest.raises(proof.Failed, match="AP_Processer-"):
        proof.build_of(_release_zip(tmp_path / "b.zip", top="AP Coder"), "b.zip", tmp_path / "ids")


@pytest.mark.skipif(not POSIX, reason="a pseudo-terminal: Linux and Macs only")
def test_the_launchers_window_is_a_terminal_whose_question_is_answered(tmp_path):
    launcher = tmp_path / "APProcessor.command"
    launcher.write_text(
        'echo "A newer AP Coder is available: 0.3.1 (build b2b2b2b, 2026-11-20). What\'s new:"\n'
        'read -r -p "Update now? Your invoices and settings are kept (a backup is made first). [Y/n] " answer\n'
        'echo "answered: $answer"\n[ -t 0 ] && echo "at a terminal"\n'
        'echo "AP Coder is running at http://127.0.0.1:9"\nread -r -p "Press Enter to close. " _\necho done\n',
        encoding="utf-8",
    )
    window = proof.Window(["/bin/bash", str(launcher)], tmp_path, dict(os.environ), tmp_path / "window.log", "n")
    assert window.wait_for(proof.RUNNING, 30) == "http://127.0.0.1:9"
    assert window.close(30) == 0
    said = [text for _seconds, text in window.lines]
    assert "answered: n" in said and "at a terminal" in said and "done" in said
    assert proof.window_said(window.lines)["offer"].startswith("A newer AP Coder is available: 0.3.1")
    log = (tmp_path / "window.log").read_text(encoding="utf-8")
    assert "<< answered 'n'" in log and "<< pressed Enter" in log and "EXIT 0" in log


# --- When it can't run ------------------------------------------------------------------------------------------------


def test_it_refuses_on_windows(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(proof, "WINDOWS", True)
    zip_path = _release_zip(tmp_path / "old.zip")
    assert proof.main(["--from", str(zip_path), "--to", str(zip_path), "--work", str(tmp_path / "w")]) == 2
    said = capsys.readouterr().err
    assert "Linux or a Mac" in said and "pseudo-terminal" in said
    assert not (tmp_path / "w").exists()


def test_a_release_tag_is_not_downloaded_without_the_opt_in(tmp_path, offline_guard, monkeypatch, capsys):
    monkeypatch.setattr(proof, "WINDOWS", False)  # (the refusals after Windows': on Windows CI too, B-3061)
    zip_path = _release_zip(tmp_path / "new.zip")
    work = tmp_path / "w"
    assert proof.main(["--from", "v0.3.0-build.0a749b3", "--to", str(zip_path), "--work", str(work)]) == 2
    said = capsys.readouterr().err
    assert "AP_ALLOW_INTERNET=1" in said and "v0.3.0-build.0a749b3" in said
    assert offline_guard.attempts == [] and not work.exists()


@pytest.mark.parametrize("opt_in", [None, "0"])
def test_two_zips_are_not_set_up_without_the_opt_in(tmp_path, offline_guard, monkeypatch, capsys, opt_in):
    """Both first setups download their packages from PyPI: not without AP_ALLOW_INTERNET=1, and never with
    AP_ALLOW_INTERNET=0, which the launchers started (a person's environment, no AP_ setting) would not be told
    (B-3059)."""
    monkeypatch.setattr(proof, "WINDOWS", False)
    if opt_in is not None:
        monkeypatch.setenv("AP_ALLOW_INTERNET", opt_in)
    monkeypatch.setattr(proof, "prove", lambda _r: pytest.fail("the proof started its first setups"))
    old, new, work = _release_zip(tmp_path / "old.zip"), _release_zip(tmp_path / "new.zip"), tmp_path / "w"
    assert proof.main(["--from", str(old), "--to", str(new), "--work", str(work)]) == 2
    assert "AP_ALLOW_INTERNET=1" in capsys.readouterr().err
    assert offline_guard.attempts == [] and not work.exists()


def test_a_proof_stopped_by_an_error_it_did_not_foresee_is_not_proved(tmp_path, monkeypatch, capsys):
    """A command that took too long, a database that is not there: the proof stops there, said as a failure, never
    "PROVED" with the checks it made until then (B-3058)."""
    monkeypatch.setattr(proof, "WINDOWS", False)
    monkeypatch.setenv("AP_ALLOW_INTERNET", "1")

    def stopped(r):
        r.proof.check(True, "the two builds")
        raise subprocess.TimeoutExpired(["python", "-m", "ap_coder", "doctor"], 900)

    monkeypatch.setattr(proof, "prove", stopped)
    zip_path, work = _release_zip(tmp_path / "new.zip"), tmp_path / "w"
    with pytest.raises(subprocess.TimeoutExpired):
        proof.main(["--from", str(zip_path), "--to", str(zip_path), "--work", str(work)])
    report = json.loads((work / "report.json").read_text(encoding="utf-8"))
    assert report["ok"] is False and report["checks"][-1]["check"] == "stopped"
    assert "TimeoutExpired" in report["checks"][-1]["detail"]
    assert "NOT PROVED" in capsys.readouterr().out


def test_it_refuses_a_work_folder_that_holds_an_earlier_run_and_a_name_that_is_neither(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(proof, "WINDOWS", False)
    zip_path = _release_zip(tmp_path / "new.zip")
    (tmp_path / "w" / "home").mkdir(parents=True)
    assert proof.main(["--from", str(zip_path), "--to", str(zip_path), "--work", str(tmp_path / "w")]) == 2
    assert "earlier run" in capsys.readouterr().err
    assert proof.main(["--from", "0a749b3", "--to", str(zip_path), "--work", str(tmp_path / "v")]) == 2
    assert "neither a release tag" in capsys.readouterr().err


# --- The whole of it --------------------------------------------------------------------------------------------------


def _git(*args: str, cwd: Path) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.mark.slow
@pytest.mark.skipif(not POSIX, reason="a pseudo-terminal: Linux and Macs only")
@pytest.mark.skipif(not ONLINE, reason="both first setups download their packages from PyPI: AP_ALLOW_INTERNET=1")
@pytest.mark.skipif(shutil.which("git") is None or not (ROOT / ".git").exists(), reason="needs this checkout's git")
def test_the_whole_proof_on_two_zips_of_this_checkout(tmp_path):
    """As CI's windows-github-zip job makes them: this commit's ZIP, and one of an empty commit made after it (the same
    version, a newer build), each laid out as a release's (git archive, ap_coder/BUILD stamped); the update answered
    yes."""
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", "--shared", "--no-checkout", str(ROOT), str(clone)], check=True)
    version = proof.paths.project_version(ROOT)
    zips = []
    for commit in (_git("rev-parse", "HEAD", cwd=clone), None):
        if commit is None:  # a newer build of the same code, as a merge with nothing in it would be
            commit = _git("-c", "user.name=ci", "-c", "user.email=ci@example.invalid", "commit-tree",
                          "HEAD^{tree}", "-p", "HEAD", "-m", "A newer build", cwd=clone)  # fmt: skip
        name = f"AP_Processer-{version}-build.{commit[:7]}"
        _git("archive", "--format=zip", f"--prefix={name}/", "-o", str(tmp_path / f"{name}.zip"), commit, cwd=clone)
        zips.append(tmp_path / f"{name}.zip")
    work = tmp_path / "work"
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "release_proof.py"), "--from", str(zips[0]), "--to",
                           str(zips[1]), "--answer", "y", "--work", str(work)], timeout=3600,
                          env={**os.environ, "AP_ALLOW_INTERNET": "1"})  # fmt: skip
    report = json.loads((work / "report.json").read_text(encoding="utf-8"))
    failed = [c for c in report["checks"] if not c["ok"]]
    assert done.returncode == 0 and report["ok"], f"failed: {failed}"
    assert report["pilot tester"]["update line"].startswith("Updated AP Coder from")
    assert report["pilot tester"]["version after"].startswith(f"{version} · build {zips[1].stem[-7:]}")
    assert not list((work / "home" / "Downloads").glob("AP_Processer-*"))  # the big folders deleted
