"""A newer tested AP Coder from GitHub's releases (scripts/releases.py, D-247): asked about by the launcher before it
sets anything up, taken only when the person says yes. GitHub is a stand-in here, on 127.0.0.1: no test reaches the
internet."""

from __future__ import annotations

import hashlib
import http.server
import io
import json
import os
import threading
import time
import zipfile
from types import SimpleNamespace

import pytest

from ap_coder.core import paths
from tests.scripts import load_script

TAG = "v0.3.1-build.b2b2b2b"
TOP = "AP_Processer-0.3.1-build.b2b2b2b"
ZIP = f"{TOP}.zip"
NOTES = "- #140 Faster reading of scans\n- #141 Plainer words on the review screen\n\nBuild b2b2b2b, committed " \
        "2026-11-20T11:00:00Z"  # fmt: skip


def release_zip(names: dict[str, str] | None = None, version: str = "0.3.1") -> bytes:
    """A release's ZIP as the workflow makes it: one folder, AP Coder in it."""
    files = names or {
        f"{TOP}/pyproject.toml": f'[project]\nname = "ap-coder"\nversion = "{version}"\n',
        f"{TOP}/ap_coder/__init__.py": "",
        f"{TOP}/ap_coder/BUILD": "b2b2b2b 2026-11-20T11:00:00+00:00\n",
        f"{TOP}/scripts/launch.py": "",
        f"{TOP}/APProcessor.bat": "",
        f"{TOP}/APProcessor.command": "",
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return buffer.getvalue()


class GitHub:
    """A stand-in for api.github.com and its release downloads: what it answers, and every request it got."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, dict]] = []
        self.files: dict[str, bytes] = {}
        self.redirects: dict[str, str] = {}
        self.delay = 0.0
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):  # noqa: N802
                owner.requests.append((self.path, dict(self.headers)))
                time.sleep(owner.delay)
                name = self.path.rsplit("/", 1)[-1]
                if self.path.startswith("/redirect/"):
                    self.send_response(302)
                    self.send_header("Location", owner.redirects[name])
                    self.end_headers()
                    return
                if self.path.startswith("/repos/"):
                    body = json.dumps(owner.releases).encode()
                elif self.path.startswith("/dl/") and name in owner.files:
                    body = owner.files[name]
                else:
                    self.send_response(404)
                    self.end_headers()
                    return
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.publish(release_zip())

    def publish(self, data: bytes, listed: bytes | None = None, zip_url: str = "") -> None:
        """The release with ``data`` as its ZIP; SHA256SUMS lists ``listed``'s SHA-256 (the ZIP's own by default)."""
        self.files = {ZIP: data, "SHA256SUMS": f"{hashlib.sha256(listed or data).hexdigest()}  {ZIP}\n".encode()}
        self.releases = [
            {"tag_name": "v0.3.0-pilot2", "draft": False, "prerelease": False, "assets": [
                {"name": "APProcessor-offline-0.3.0.zip", "browser_download_url": f"{self.base}/dl/x.zip"}]},
            {"tag_name": TAG, "draft": False, "prerelease": False, "published_at": "2026-11-20T12:00:00Z",
             "body": NOTES, "assets": [
                 {"name": ZIP, "browser_download_url": zip_url or f"{self.base}/dl/{ZIP}"},
                 {"name": "SHA256SUMS", "browser_download_url": f"{self.base}/dl/SHA256SUMS"}]},
        ]  # fmt: skip

    @property
    def asked(self) -> list[str]:
        return [path for path, _headers in self.requests]


@pytest.fixture
def github():
    stand_in = GitHub()
    yield stand_in
    stand_in.httpd.shutdown()
    stand_in.httpd.server_close()


@pytest.fixture
def launch(tmp_path, monkeypatch, github):
    """The launcher in AP Coder 0.3.0 from GitHub's ZIP (``installs/AP_Processer-main``: no offline bundle), asking
    the stand-in GitHub; someone at the window."""
    module = load_script("launch", "ap_script_launch_releases")
    here = tmp_path / "installs" / "AP_Processer-main"
    (here / "ap_coder").mkdir(parents=True)
    (here / "scripts").mkdir()
    (here / "scripts" / "launch.py").write_text("", encoding="utf-8")
    (here / "pyproject.toml").write_text('[project]\nname = "ap-coder"\nversion = "0.3.0"\n', encoding="utf-8")
    (here / "ap_coder" / "BUILD").write_text(
        "a1a1a1a 2026-11-10T10:00:00+00:00\n", encoding="utf-8"
    )  # GitHub's ZIP's stamp
    monkeypatch.setattr(module, "ROOT", here)
    monkeypatch.setattr(module.fetch_models, "BUNDLE_MODELS", here / "models")
    monkeypatch.setattr(module, "WINDOWS", False)
    releases = module.releases
    monkeypatch.setattr(releases, "API", f"{github.base}/repos/{releases.REPO}/releases?per_page=20")
    monkeypatch.setattr(releases, "HOSTS", ("127.0.0.1",))
    monkeypatch.setattr(releases, "SCHEME", "http")
    monkeypatch.delenv("AP_UPDATE_CHECK", raising=False)  # (tests/conftest.py switches it off for every other test)
    monkeypatch.delenv("AP_NO_PAUSE", raising=False)
    monkeypatch.delenv(releases.UPDATE_FROM_ENV, raising=False)
    monkeypatch.setattr(module.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    module.handed = []
    monkeypatch.setattr(module, "handover", lambda folder, argv: module.handed.append((folder, argv)) or 0)
    return module


def _answer(launch, monkeypatch, answer: str) -> list[str]:
    asked: list[str] = []
    monkeypatch.setattr(launch, "input", lambda question: asked.append(question) or answer, raising=False)
    return asked


def _offer(launch, argv: list[str] | None = None):
    argv = argv or []
    return launch.offer_update(launch.parse(argv)[0], argv)


def _left_beside(launch) -> list[str]:
    return sorted(p.name for p in launch.ROOT.parent.iterdir() if p != launch.ROOT)


@pytest.mark.parametrize("answer", ["", "y"])
def test_a_newer_release_is_said_asked_and_taken(launch, github, monkeypatch, capsys, answer):
    asked = _answer(launch, monkeypatch, answer)
    assert _offer(launch, ["--port", "8502"]) == 0
    folder = launch.ROOT.parent / TOP
    assert launch.handed == [(folder, ["--port", "8502"])]
    assert paths.project_version(folder) == "0.3.1" and (folder / "scripts" / "launch.py").is_file()
    assert _left_beside(launch) == [TOP]  # (the download and the unzipping folder are gone)
    out = capsys.readouterr().out
    assert "A newer AP Coder is available: 0.3.1 (build b2b2b2b, 2026-11-20)" in out
    assert "#140 Faster reading of scans" in out and "#141 Plainer words" in out and "committed" not in out
    assert asked == ["Update now? Your invoices and settings are kept (a backup is made first). [Y/n] "]
    # One request to the releases, then the two files: nothing else sent, and no name of this computer or person.
    assert github.asked == [f"/repos/{launch.releases.REPO}/releases?per_page=20", "/dl/SHA256SUMS", f"/dl/{ZIP}"]
    headers = github.requests[0][1]
    assert headers["User-Agent"] == "AP-Coder-launcher" and "Cookie" not in headers and "Authorization" not in headers


def test_the_new_folders_launcher_is_started_as_a_double_click_runs_it(launch, monkeypatch, tmp_path):
    """From the new folder, its own APProcessor; this start's lock let go (the new one cleans this folder up), and
    the new launcher told this folder said yes already (it does not ask about taking over again)."""
    module = load_script("launch", "ap_script_launch_handover")
    monkeypatch.setattr(module, "ROOT", launch.ROOT)
    ran = []
    monkeypatch.setattr(module.subprocess, "call", lambda cmd, cwd, env: ran.append((cmd, cwd, env)) or 3)
    monkeypatch.setenv(module.LOCK_HELD_ENV, "123")
    released = []
    monkeypatch.setattr(module, "release_lock", lambda: released.append(True))
    folder = tmp_path / TOP
    assert module.handover(folder, ["--port", "8502"]) == 0  # (its own window said why it failed, and paused)
    ((cmd, cwd, env),) = ran
    assert cwd == folder and released == [True]
    assert cmd == (["cmd.exe", "/c", "APProcessor.bat", "--port", "8502"] if module.os.name == "nt" else
                   ["/bin/bash", "APProcessor.command", "--port", "8502"])  # fmt: skip
    assert module.LOCK_HELD_ENV not in env and env[module.releases.UPDATE_FROM_ENV] == str(launch.ROOT)
    assert module.releases.handover_command(folder, [], windows=True) == ["cmd.exe", "/c", "APProcessor.bat"]


def test_no_starts_this_version_and_that_release_is_not_asked_about_again(launch, github, monkeypatch, capsys):
    asked = _answer(launch, monkeypatch, "n")
    assert _offer(launch) is None and len(asked) == 1 and launch.handed == []
    assert github.asked == [f"/repos/{launch.releases.REPO}/releases?per_page=20"] and _left_beside(launch) == []
    assert "Starting this version" in capsys.readouterr().out
    paths.write_user_settings(update_asked="2020-01-01")  # (a start of another day: GitHub is asked once a day)
    assert _offer(launch) is None and len(asked) == 1  # the next start: said in one line, not asked
    out = capsys.readouterr().out
    assert "AP Coder 0.3.1 (build b2b2b2b, 2026-11-20) is available" in out and "--update" in out
    assert _offer(launch, ["--update"]) is None and len(asked) == 2  # asked for: asked again


@pytest.mark.parametrize("ours", ["same build", "newer build", "newer version"])
def test_the_same_or_an_older_release_is_not_said(launch, github, monkeypatch, capsys, ours):
    asked = _answer(launch, monkeypatch, "")
    if ours == "same build":
        (launch.ROOT / "ap_coder" / "BUILD").write_text("b2b2b2b9 2026-11-20T11:00:00+00:00\n", encoding="utf-8")
        (launch.ROOT / "pyproject.toml").write_text(
            '[project]\nname = "ap-coder"\nversion = "0.3.1"\n', encoding="utf-8"
        )
    elif ours == "newer build":
        (launch.ROOT / "ap_coder" / "BUILD").write_text("c3c3c3c 2026-11-21T09:00:00+00:00\n", encoding="utf-8")
        (launch.ROOT / "pyproject.toml").write_text(
            '[project]\nname = "ap-coder"\nversion = "0.3.1"\n', encoding="utf-8"
        )
    else:
        (launch.ROOT / "pyproject.toml").write_text(
            '[project]\nname = "ap-coder"\nversion = "0.4.0"\n', encoding="utf-8"
        )
    assert _offer(launch) is None and asked == [] and launch.handed == []
    assert "AP Coder" not in capsys.readouterr().out and len(github.requests) == 1


def test_the_same_version_built_later_is_newer():
    releases = load_script("releases", "ap_script_releases")
    release = SimpleNamespace(version="0.3.0", commit="b2b2b2b", committed="2026-11-20T11:00:00")
    assert releases.is_newer(release, {"version": "0.3.0", "commit": "a1a1a1a", "commit_time": "2026-11-10T10:00:00"})
    assert not releases.is_newer(release, {"version": "0.3.0", "commit": "", "commit_time": ""})  # (can't be told)


@pytest.mark.parametrize("how", ["no answer", "too slow"])
def test_github_not_answering_is_one_line_and_this_version_starts(launch, github, monkeypatch, capsys, how):
    # Both within the check's own time (made short here): Windows takes about 2 s to give up on a refused
    # connection to this computer, retrying it, so the time is the check's, not the network's.
    monkeypatch.setattr(launch.releases, "CHECK_SECONDS", 0.5)
    if how == "no answer":
        github.httpd.shutdown()
        github.httpd.server_close()
    else:
        github.delay = 2.0
    started = time.monotonic()
    assert _offer(launch) is None and launch.handed == []
    assert time.monotonic() - started < 1.9
    out = capsys.readouterr().out
    assert out.count("Couldn't check for a newer version of AP Coder; starting this one.") == 1
    assert "Traceback" not in out


@pytest.mark.parametrize("bad", ["checksum", "host", "redirect", "escaping zip", "not ap coder"])
def test_a_release_that_is_not_right_is_refused_and_this_version_starts(launch, github, monkeypatch, capsys, bad):
    _answer(launch, monkeypatch, "")
    if bad == "checksum":
        github.publish(release_zip(), listed=b"another file")
    elif bad == "host":  # (an address GitHub's are not: here, this computer by another name)
        github.publish(release_zip(), zip_url=f"{github.base.replace('127.0.0.1', 'localhost')}/dl/{ZIP}")
    elif bad == "redirect":
        github.publish(release_zip(), zip_url=f"{github.base}/redirect/{ZIP}")
        github.redirects[ZIP] = f"{github.base.replace('127.0.0.1', 'localhost')}/dl/{ZIP}"
    elif bad == "escaping zip":
        github.publish(release_zip({f"{TOP}/pyproject.toml": "", f"{TOP}/../escaped.txt": "x"}))
    else:
        github.publish(release_zip({f"{TOP}/README.md": "not AP Coder"}))
    assert _offer(launch) is None and launch.handed == []
    assert _left_beside(launch) == []  # nothing left: no download, no half-unzipped folder, nothing outside it
    out = capsys.readouterr().out
    assert "The update was not installed:" in out and "Starting this version" in out
    said = {"checksum": "its SHA-256 differs", "host": "is not GitHub's", "redirect": "which is not GitHub's",
            "escaping zip": "outside its folder", "not ap coder": "holds no AP Coder"}[bad]  # fmt: skip
    assert said in out
    if bad in ("host", "redirect"):
        assert f"/dl/{ZIP}" not in github.asked


@pytest.mark.parametrize("how", ["offline bundle", "AP_ALLOW_INTERNET=0", "AP_UPDATE_CHECK=0", "--no-update",
                                 "server", "just updated", "--no-start", "demo"])  # fmt: skip
def test_no_request_at_all_where_the_check_does_not_belong(launch, github, monkeypatch, capsys, how):
    asked = _answer(launch, monkeypatch, "")
    argv = []
    if how == "offline bundle":
        (launch.ROOT / "bundle_manifest.json").write_text("{}", encoding="utf-8")
    elif how == "AP_ALLOW_INTERNET=0":
        monkeypatch.setenv("AP_ALLOW_INTERNET", "0")
    elif how == "AP_UPDATE_CHECK=0":
        monkeypatch.setenv("AP_UPDATE_CHECK", "0")
    elif how == "server":
        monkeypatch.setenv("AP_SERVER_MODE", "1")
    elif how == "just updated":
        monkeypatch.setenv(launch.releases.UPDATE_FROM_ENV, "/somewhere/AP_Processer-main")
    elif how == "demo":
        monkeypatch.setenv("AP_DEMO", "1")
    else:
        argv = [how]
    assert _offer(launch, argv) is None
    assert github.requests == [] and asked == [] and capsys.readouterr().out == ""


@pytest.mark.parametrize("how", ["AP_NO_PAUSE", "no terminal"])
def test_with_nobody_at_the_window_the_release_is_said_and_not_taken(launch, github, monkeypatch, capsys, how):
    asked = _answer(launch, monkeypatch, "")
    if how == "AP_NO_PAUSE":
        monkeypatch.setenv("AP_NO_PAUSE", "1")
    else:
        monkeypatch.setattr(launch.sys, "stdin", SimpleNamespace(isatty=lambda: False))
    assert _offer(launch) is None and asked == [] and launch.handed == [] and len(github.requests) == 1
    out = capsys.readouterr().out
    assert "A newer AP Coder is available: 0.3.1" in out and "It was not installed" in out


def test_yes_takes_it_without_asking(launch, github, monkeypatch):
    asked = _answer(launch, monkeypatch, "n")
    assert _offer(launch, ["--yes"]) == 0 and asked == [] and launch.handed[0][0].name == TOP


def test_a_laptop_shared_with_share_is_asked_too(launch):
    """Its double-click sets up with nothing of its own to start (it runs share afterwards, D-246): asked all the
    same; the new folder's launcher then starts share. A set-up asked for by name (--no-start) is never asked."""
    args = launch.parse([])[0]
    args.no_start = args.shared = True  # (as _main sets them on a shared laptop)
    assert launch.update_check_wanted(args)
    assert not launch.update_check_wanted(launch.parse(["--no-start"])[0])


def test_the_double_click_asks_before_anything_is_set_up(launch, monkeypatch):
    """In the first phase, before .venv is made or packages installed (bootstrap)."""
    order: list[str] = []
    monkeypatch.setattr(launch, "take_lock", lambda: True)
    monkeypatch.setattr(launch, "in_venv", lambda: False)
    monkeypatch.setattr(launch, "installed_already", lambda args, rest: order.append("installed") and None)
    monkeypatch.setattr(launch, "offer_update", lambda args, argv: order.append("update") and None)
    monkeypatch.setattr(launch, "bootstrap", lambda argv: order.append("bootstrap") or 0)
    assert launch.main([]) == 0 and order == ["installed", "update", "bootstrap"]
    monkeypatch.setattr(launch, "offer_update", lambda args, argv: order.append("updated") or 0)
    assert launch.main([]) == 0 and order[-1] == "updated"  # handed over: nothing set up here


def test_the_zip_check_refuses_every_way_out_of_its_folder():
    releases = load_script("releases", "ap_script_releases_zip")
    assert releases._top_folder([f"{TOP}/a.py", f"{TOP}/scripts/launch.py"]) == TOP
    for names in (["/etc/passwd"], [f"{TOP}/../x"], ["C:/x"], [f"{TOP}\\..\\x"], [f"{TOP}/a", "other/b"], [".git/x"]):
        with pytest.raises(releases.UpdateError):
            releases._top_folder(names)


def test_the_new_folder_is_never_put_inside_the_data_folder(launch, github, monkeypatch, capsys, tmp_path):
    _answer(launch, monkeypatch, "")
    monkeypatch.setenv("AP_PRIVATE_DIR", str(launch.ROOT.parent))  # (an install inside its own data folder)
    assert _offer(launch) is None and launch.handed == []
    assert "inside the data folder" in capsys.readouterr().out and _left_beside(launch) == []


def test_a_new_folder_whose_takeover_is_answered_already_is_not_asked_again(tmp_path, monkeypatch):
    """The new folder's launcher, started by the old one once the person said yes (``AP_UPDATE_FROM``): it takes
    over from that folder without asking a second time."""
    module = load_script("launch", "ap_script_launch_update_from")
    monkeypatch.setattr(module.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.delenv("AP_NO_PAUSE", raising=False)
    monkeypatch.setattr(module, "input", lambda q: pytest.fail("asked again"), raising=False)
    older = {"folder": str(tmp_path / "AP_Processer-main"), "version": "0.3.0"}
    monkeypatch.setenv(module.releases.UPDATE_FROM_ENV, older["folder"])
    assert module.update_wanted(module.parse([])[0], older, {"version": "0.3.1"})


def test_a_folder_an_earlier_yes_unzipped_is_started_again_not_downloaded_again(launch, github, monkeypatch):
    """Its new start did not finish (the window closed): the next yes starts it again. A folder of that name holding
    something else is never written over."""
    _answer(launch, monkeypatch, "")
    with zipfile.ZipFile(io.BytesIO(release_zip())) as archive:
        archive.extractall(launch.ROOT.parent)
    assert _offer(launch) == 0 and launch.handed == [(launch.ROOT.parent / TOP, [])]
    assert github.asked == [f"/repos/{launch.releases.REPO}/releases?per_page=20"]
    (launch.ROOT.parent / TOP / "ap_coder" / "BUILD").write_text(
        "d4d4d4d 2026-11-01T10:00:00+00:00\n", encoding="utf-8"
    )
    assert _offer(launch) is None and len(launch.handed) == 1


@pytest.mark.skipif(os.name == "nt", reason="Windows files have no executable bit")
def test_the_new_folders_launchers_stay_executable(launch, github, monkeypatch):
    """A Mac's desktop "AP Coder.command" runs the new folder's APProcessor.command itself (``exec``): unzipped
    without the modes the ZIP keeps, it would be refused ("Permission denied") at the next double-click."""
    _answer(launch, monkeypatch, "")
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(release_zip())) as source, zipfile.ZipFile(buffer, "w") as archive:
        for info in source.infolist():
            if info.filename.endswith(".command"):
                info.external_attr = 0o100755 << 16  # (as git archive writes an executable file)
            archive.writestr(info, source.read(info))
    github.publish(buffer.getvalue())
    assert _offer(launch) == 0
    folder = launch.ROOT.parent / TOP
    assert os.access(folder / "APProcessor.command", os.X_OK)
    assert not os.access(folder / "scripts" / "launch.py", os.X_OK)


@pytest.mark.parametrize("held", [2, 1000])
def test_a_new_folder_antivirus_still_holds_is_renamed_once_it_lets_go(launch, github, monkeypatch, capsys, held):
    """Windows' antivirus reads the files just unzipped, and renaming their folder fails meanwhile: tried again for a
    few seconds; still held after that, said as before (and nothing left beside this folder)."""
    _answer(launch, monkeypatch, "")
    monkeypatch.setattr(launch.releases, "RENAME_SECONDS", 0.5)
    real_rename, tries = launch.releases.Path.rename, []

    def rename(self, target):
        if self.name == TOP and len(tries) < held:
            tries.append(self)
            raise PermissionError(13, "Access is denied")
        return real_rename(self, target)

    monkeypatch.setattr(launch.releases.Path, "rename", rename)
    if held == 2:
        assert _offer(launch) == 0 and len(tries) == 2 and (launch.ROOT.parent / TOP / "scripts").is_dir()
    else:
        assert _offer(launch) is None and 2 < len(tries) < 50 and _left_beside(launch) == []
        assert "Access is denied" in capsys.readouterr().out


@pytest.mark.parametrize("windows", [True, False])
def test_said_no_says_how_to_be_asked_again(launch, github, monkeypatch, capsys, windows):
    _answer(launch, monkeypatch, "n")
    monkeypatch.setattr(launch, "WINDOWS", windows)
    assert _offer(launch) is None
    paths.write_user_settings(update_asked="")  # (the next day)
    assert _offer(launch) is None
    out = capsys.readouterr().out
    if windows:
        assert r"open scripts\terminal.bat in AP Coder's folder and type APProcessor.bat --update" in out
    else:
        assert "in Terminal, in AP Coder's folder, type ./APProcessor.command --update" in out
    assert "a newer one is asked about by itself" in out


def test_after_a_no_nothing_is_asked_again_that_day(launch, github, monkeypatch, capsys):
    """A build is released at each merge: after a no, the rest of the day's starts ask GitHub nothing (not even about a
    newer build); the next day it is asked again. ``--update`` asks at once."""
    asked = _answer(launch, monkeypatch, "n")
    assert _offer(launch) is None and len(asked) == 1 and len(github.requests) == 1
    capsys.readouterr()
    github.publish(release_zip())
    github.releases[1]["tag_name"] = "v0.3.1-build.c3c3c3c"  # a newer build, the same day
    assert _offer(launch) is None and len(asked) == 1 and len(github.requests) == 1
    assert capsys.readouterr().out == ""
    assert _offer(launch, ["--update"]) is None and len(asked) == 2 and len(github.requests) == 2
    capsys.readouterr()
    paths.write_user_settings(update_asked="2020-01-01")  # the next day: GitHub asked, the build said no to said
    assert _offer(launch) is None and len(asked) == 2 and len(github.requests) == 3
    assert "(you said no to it)" in capsys.readouterr().out


def test_a_yes_that_could_not_be_installed_is_asked_again_at_the_next_start(launch, github, monkeypatch):
    asked = _answer(launch, monkeypatch, "")
    github.publish(release_zip(), listed=b"another file")
    assert _offer(launch) is None and _offer(launch) is None and len(asked) == 2


def test_the_new_folder_backs_the_database_up_before_anything_opens_it(launch, tmp_path, monkeypatch, capsys):
    """The question says "Your invoices and settings are kept (a backup is made first)": on the takeover an update
    hands over to, the database and the data folder's files are backed up (D-143), whether or not this version changes
    the database, before it is opened; never on another start."""
    from ap_coder.core.store import Store

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("AP_PRIVATE_DIR", str(data))
    monkeypatch.delenv("AP_DB_PATH", raising=False)
    Store(data / "ap_coder.db")
    monkeypatch.setattr(launch, "ensure_data_folder", lambda: data)
    monkeypatch.setattr(launch, "settle_settings", lambda data, version: [])
    monkeypatch.setattr(launch, "database_too_new", lambda data, version: None)
    made: list[list[str]] = []

    def next_step(first, state):  # (what follows the backup: stops the test there)
        made.append(sorted(p.name for p in (data / "backups").glob("ap_coder-*.db")) if (data / "backups").is_dir()
                    else [])  # fmt: skip
        raise KeyboardInterrupt

    monkeypatch.setattr(launch, "ensure_models", next_step)

    def settle():
        args = launch.parse([])[0]
        with pytest.raises(KeyboardInterrupt):
            launch.settle_and_check(args, {}, launch.identity(), False, None, [], ("ok", "", False), launch.Step())

    settle()  # a start that is not an update's takeover: no backup
    monkeypatch.setenv(launch.releases.UPDATE_FROM_ENV, str(tmp_path / "AP_Processer-main"))
    settle()
    assert made[0] == [] and len(made[1]) == 1 and made[1][0].endswith("-before-update-0.3.0.db")
    assert "Backed up the database and its files before the update" in capsys.readouterr().out


def test_a_backup_that_cant_be_made_stops_the_takeover_before_the_database_is_opened(launch, tmp_path, monkeypatch,
                                                                                      capsys):  # fmt: skip
    from ap_coder.core.store import Store
    from ap_coder.ops import server_update

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("AP_PRIVATE_DIR", str(data))
    monkeypatch.delenv("AP_DB_PATH", raising=False)
    Store(data / "ap_coder.db")
    monkeypatch.setenv(launch.releases.UPDATE_FROM_ENV, str(tmp_path / "AP_Processer-main"))

    def full(db, label):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(server_update, "safety_copy", full)
    blocked = launch.backup_before_update()
    assert blocked is not None and "No space left on device" in str(blocked) and "nothing was changed" in str(blocked)
