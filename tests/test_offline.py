"""The offline build: nothing leaves this computer, the OCR models are fetched once (and the app copes
without them), the launchers and the offline bundle are put together as the pilot guide says."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import ipaddress
import json
import os
import pkgutil
import re
import socket
import sys
import zipfile
from pathlib import Path

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from ap_coder import cli, offline
from ap_coder.capture import analyze, layout
from ap_coder.store import APPROVED, Store

from .conftest import ROOT, SAMPLE_STEM, SAMPLES

TIMEOUT = 90


def _script(name: str):
    spec = importlib.util.spec_from_file_location(f"ap_script_{name}", ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # its dataclasses look themselves up there
    spec.loader.exec_module(module)
    return module


# --- No connection beyond this computer -------------------------------------------------------------------------


def _local(host) -> bool:
    if host in (None, "", "localhost"):
        return True
    try:
        return ipaddress.ip_address(str(host).split("%")[0]).is_loopback
    except ValueError:
        return False


@pytest.fixture
def no_network(monkeypatch):
    """Every attempt to reach another computer (a DNS lookup or a connection) is recorded and refused."""
    attempts: list[str] = []
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex
    real_getaddrinfo = socket.getaddrinfo

    def refuse(what: str):
        attempts.append(what)
        raise OSError(f"test: no network ({what})")

    def connect(self, address):
        if isinstance(address, tuple) and not _local(address[0]):
            refuse(f"connect {address[0]}:{address[1]}")
        return real_connect(self, address)

    def connect_ex(self, address):
        if isinstance(address, tuple) and not _local(address[0]):
            refuse(f"connect {address[0]}:{address[1]}")
        return real_connect_ex(self, address)

    def getaddrinfo(host, *args, **kwargs):
        if not _local(host.decode() if isinstance(host, bytes) else host):
            attempts.append(f"dns {host}")
            raise socket.gaierror(f"test: no network (dns {host})")
        return real_getaddrinfo(host, *args, **kwargs)

    for name in ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)  # a proxy on this computer would hide where a request goes
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    return attempts


def test_the_guard_catches_a_connection(no_network):
    import urllib.request

    with pytest.raises(OSError):
        urllib.request.urlopen("https://example.com/", timeout=2)
    assert no_network  # recorded, so the tests below would see one


ocr = pytest.mark.skipif(not layout.ocr_available(), reason="local OCR not installed (pip install -e .[ocr])")


@ocr
def test_capture_reads_an_invoice_offline(no_network):
    """OCR of every page (forced on a text PDF) with the second read: all from local models."""
    result = analyze(SAMPLES / f"{SAMPLE_STEM}.pdf", ocr=True)
    assert result.layout_source == "ocr"
    assert result.fields["invoice_number"].value
    assert result.fields["grand_total"].value
    assert no_network == []


@ocr
def test_missing_ppocrv5_models_mean_one_engine_not_a_download(no_network, monkeypatch, tmp_path):
    monkeypatch.setattr(offline, "model_dir", lambda: tmp_path)  # an empty models folder
    monkeypatch.setattr(offline, "_warned", False)
    assert not offline.ppocrv5_ready()
    if layout._installed("rapidocr_onnxruntime"):
        assert layout.second_engine_name() == layout._engine_name() == "rapidocr"
        result = analyze(SAMPLES / f"{SAMPLE_STEM}.pdf", ocr=True)
        assert result.layout_source == "ocr" and result.fields["grand_total"].value
    # With only RapidOCR 3 installed, OCR is unavailable rather than downloading.
    monkeypatch.setattr(layout, "_installed", lambda module: module == "rapidocr")
    assert not layout.ocr_available()
    assert no_network == []


@pytest.fixture
def demo_db(tmp_path, monkeypatch):
    path = tmp_path / "private" / "ap_coder.db"
    monkeypatch.setenv("AP_DB_PATH", str(path))
    for name in [m for m in sys.modules if m.startswith(("ap_coder.webapp", "ap_coder.dashboard"))]:
        monkeypatch.delitem(sys.modules, name)
    st.cache_resource.clear()
    st.cache_data.clear()
    return path


def test_demo_review_and_jde_export_offline(no_network, demo_db):
    """The demo invoices through validation, tax checks, capture and a JD Edwards batch, offline."""
    from ap_coder import jde
    from ap_coder.demo import load_demo

    store = Store(demo_db)
    counts = load_demo(store)
    assert counts["added"] >= 5
    approved = [store.get_invoice(i["id"]) for i in store.list_invoices(APPROVED)]
    assert approved
    data, name, _ = jde.build_zip(approved, jde.JdeSettings(), lambda final: "10023", batch=1)
    assert name.endswith(".zip") and zipfile.ZipFile(__import__("io").BytesIO(data)).namelist()
    assert no_network == []


def test_every_dashboard_page_runs_offline(no_network, demo_db):
    from ap_coder.demo import load_demo

    from .test_dashboard_pages import PAGES

    load_demo(Store(demo_db))
    import ap_coder.webapp as webapp

    for info in pkgutil.walk_packages(webapp.__path__, "ap_coder.webapp."):
        importlib.import_module(info.name)
    at = AppTest.from_file(str(ROOT / "ap_coder" / "dashboard.py"), default_timeout=TIMEOUT).run()
    assert not at.exception, [e.value for e in at.exception]
    for module, function in PAGES:
        at = AppTest.from_string(f"from ap_coder.webapp.{module} import {function}\n{function}()",
                                 default_timeout=TIMEOUT).run()  # fmt: skip
        assert not at.exception, (module, [e.value for e in at.exception])
    assert no_network == []


def test_doctor_offline_checks_need_no_network(no_network):
    checks = {area: (status, detail) for area, status, detail in offline.offline_checks()}
    assert checks["data folder"][0] == offline.PASS
    assert checks["dashboard network"][0] == offline.PASS
    if offline.ppocrv5_installed() and not offline.missing_models():
        assert checks["OCR PP-OCRv5"][0] == offline.PASS
    assert no_network == []


# --- The dashboard listens locally and loads nothing from the internet ------------------------------------------


def _dashboard_cmd(monkeypatch, *argv) -> list[str]:
    import subprocess

    seen = {}
    monkeypatch.setattr(subprocess, "call", lambda cmd, env=None: seen.setdefault("cmd", cmd) and 0)
    assert cli.main(["dashboard", "--port", "8599", *argv]) == 0
    return seen["cmd"]


def _opt(cmd: list[str], name: str) -> str:
    return cmd[cmd.index(name) + 1]


def test_dashboard_is_local_only_by_default(monkeypatch):
    monkeypatch.delenv(offline.ADDRESS_ENV, raising=False)
    cmd = _dashboard_cmd(monkeypatch)
    assert _opt(cmd, "--server.address") == "127.0.0.1"
    assert _opt(cmd, "--browser.gatherUsageStats") == "false"
    assert _opt(cmd, "--server.showEmailPrompt") == "false"
    assert _opt(cmd, "--server.enableStaticServing") == "true"


def test_lan_access_is_opt_in(monkeypatch, capsys):
    monkeypatch.setenv(offline.ADDRESS_ENV, "0.0.0.0")
    cmd = _dashboard_cmd(monkeypatch)
    assert _opt(cmd, "--server.address") == "0.0.0.0" and _opt(cmd, "--browser.serverAddress") == "localhost"
    assert "other computers on the network" in capsys.readouterr().err
    checks = {area: status for area, status, _ in offline.offline_checks()}
    assert checks["dashboard network"] == offline.WARN
    monkeypatch.delenv(offline.ADDRESS_ENV)
    assert _opt(_dashboard_cmd(monkeypatch, "--address", "127.0.0.1"), "--server.address") == "127.0.0.1"


def test_streamlit_config_sends_no_usage_statistics():
    """``.streamlit/config.toml`` (read by ``streamlit run`` from this folder, e.g. the public demo)."""
    tomllib = _script("check_deps").tomllib
    config = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))
    assert config["browser"]["gatherUsageStats"] is False
    assert config["server"]["showEmailPrompt"] is False


ALLOWED_HOSTS = {
    "www.w3.org",  # the SVG namespace, not a download
    "cognitiveservices.azure.com",  # the Azure sign-in scope, used only when Azure is set up
    "github.com", "scripts.sil.org",  # the font licence text
    "www.modelscope.cn",  # where scripts/fetch_models.py gets the OCR models at setup; never by the app
}  # fmt: skip


def test_no_assets_are_loaded_from_the_internet():
    """Fonts, icons, scripts and styles all come from the package: no URL to another host in the app."""
    found = set()
    for path in (ROOT / "ap_coder").rglob("*"):
        if path.suffix not in (".py", ".js", ".html", ".css", ".toml", ".svg", ".json") or "bench" in path.parts:
            continue
        for host in re.findall(r"https?://([A-Za-z0-9.-]+)", path.read_text(encoding="utf-8", errors="ignore")):
            if not _local(host) and host not in ALLOWED_HOSTS and not host.endswith(("example.com", "example.org")):
                found.add(f"{path.relative_to(ROOT)}: {host}")
    assert not found, sorted(found)


# --- OCR models: fetched once, idempotent -----------------------------------------------------------------------


def test_ppocrv5_settings_match_the_capture_engine(monkeypatch):
    rapidocr = pytest.importorskip("rapidocr")
    seen = {}

    class FakeRapidOCR:
        def __init__(self, params=None):
            seen["params"] = params

    monkeypatch.setattr(rapidocr, "RapidOCR", FakeRapidOCR)
    monkeypatch.setattr(layout, "_ENGINES", {})
    layout._engine("ppocrv5")
    assert seen["params"] == offline.ppocrv5_engine_params()
    assert {k: v for k, v in seen["params"].items() if not k.endswith(".model_path")} == offline.ppocrv5_params()
    names = [m.name for m in offline.ppocrv5_files()]
    assert names == [n for n, _, _ in offline._PPOCRV5_FALLBACK]  # RapidOCR's own list agrees with ours
    # Each model by its path on disk: RapidOCR's downloader runs only for a model given no path.
    folder = offline.model_dir()
    assert [seen["params"][f"{task}.model_path"] for task in ("Det", "Cls", "Rec")] == [
        str(folder / name) for name in names
    ]


def test_a_damaged_model_file_counts_as_missing_and_is_checked_once(tmp_path, monkeypatch):
    """RapidOCR downloads again a model whose checksum is wrong: a damaged file of the right size is missing."""
    contents = {"det.onnx": b"detector", "rec.onnx": b"recogniser"}
    models = tuple(offline.ModelFile(n, f"https://models.invalid/{n}", hashlib.sha256(d).hexdigest())
                   for n, d in contents.items())  # fmt: skip
    monkeypatch.setattr(offline, "ppocrv5_files", lambda: models)
    monkeypatch.setattr(offline, "ppocrv5_installed", lambda: True)
    monkeypatch.setattr(offline, "model_dir", lambda: tmp_path)
    monkeypatch.setattr(offline, "_warned", False)
    for name, data in contents.items():
        (tmp_path / name).write_bytes(data)
    reads: list[Path] = []
    real = offline.sha256
    monkeypatch.setattr(offline, "sha256", lambda path: reads.append(path) or real(path))
    assert offline.ppocrv5_ready() and offline.ppocrv5_ready()
    assert len(reads) == 2  # each file read once, not on every OCR'd page
    damaged = tmp_path / "det.onnx"
    damaged.write_bytes(b"detectoX")  # the same size, the wrong content
    # Written later than the first check, as a real damaged copy is: a quick test write can keep the same timestamp
    # (Windows updates it lazily), which would look like the file already checked.
    later = damaged.stat().st_mtime_ns + 2_000_000_000
    os.utime(damaged, ns=(later, later))
    assert not offline.ppocrv5_ready()


def test_only_the_bundle_builder_opts_in_to_the_internet(monkeypatch):
    assert not offline.internet_allowed()  # the default (tests/conftest.py clears it): offline
    for value in ("1", "true", "YES", "on"):
        monkeypatch.setenv("AP_ALLOW_INTERNET", value)
        assert offline.internet_allowed()
    monkeypatch.setenv("AP_ALLOW_INTERNET", "0")
    assert not offline.internet_allowed()
    bundle = _script("build_offline_bundle")
    assert bundle.main(["--dry-run", "--no-models"]) == 0 and offline.internet_allowed()


@pytest.fixture
def fake_models(monkeypatch):
    contents = {"det.onnx": b"detector", "rec.onnx": b"recogniser"}
    models = tuple(
        offline.ModelFile(name, f"https://models.invalid/{name}", hashlib.sha256(data).hexdigest())
        for name, data in contents.items()
    )
    monkeypatch.setattr(offline, "ppocrv5_files", lambda: models)
    return contents


def test_fetch_models_downloads_once(tmp_path, fake_models, monkeypatch):
    fetch_models = _script("fetch_models")
    calls = []

    def get(url, dest):
        calls.append(url)
        dest.write_bytes(fake_models[url.rsplit("/", 1)[-1]])

    offline_run = fetch_models.fetch(tmp_path / "models", get=get, say=lambda _: None)
    assert offline_run.failed and calls == []  # the offline default: never downloaded
    monkeypatch.setenv("AP_ALLOW_INTERNET", "1")  # the bundle builder's / a developer's opt-in
    first = fetch_models.fetch(tmp_path / "models", get=get, say=lambda _: None)
    assert first.ok and sorted(first.downloaded) == sorted(fake_models) and len(calls) == 2
    second = fetch_models.fetch(tmp_path / "models", get=get, say=lambda _: None)
    assert second.ok and sorted(second.kept) == sorted(fake_models) and len(calls) == 2  # nothing fetched again
    (tmp_path / "models" / "det.onnx").write_bytes(b"half a file")
    third = fetch_models.fetch(tmp_path / "models", get=get, say=lambda _: None)
    assert third.downloaded == ["det.onnx"] and len(calls) == 3
    assert not list((tmp_path / "models").glob("*.part"))


def test_fetch_models_never_downloads_offline(tmp_path, fake_models, monkeypatch, capsys, no_network):
    fetch_models = _script("fetch_models")
    monkeypatch.setattr(fetch_models, "BUNDLE_MODELS", tmp_path / "no-bundle")
    with pytest.raises(PermissionError):
        fetch_models.download("https://www.modelscope.cn/x.onnx", tmp_path / "x.onnx")
    assert fetch_models.main(["--to", str(tmp_path / "models")]) == 1
    out = capsys.readouterr().out
    assert "Downloading" not in out and "offline build" in out and no_network == []


def test_fetch_models_from_the_bundle_folder_offline(tmp_path, fake_models):
    fetch_models = _script("fetch_models")
    source = tmp_path / "bundle_models"
    source.mkdir()
    for name, data in fake_models.items():
        (source / name).write_bytes(data)

    def get(url, dest):
        raise AssertionError("no download when the bundle has the file")

    result = fetch_models.fetch(tmp_path / "models", source, online=False, get=get, say=lambda _: None)
    assert result.ok and sorted(result.copied) == sorted(fake_models)
    (source / "rec.onnx").unlink()
    (tmp_path / "models" / "rec.onnx").unlink()
    result = fetch_models.fetch(tmp_path / "models", source, online=False, get=get, say=lambda _: None)
    assert result.failed == ["rec.onnx"] and result.kept == ["det.onnx"]


def test_fetch_models_check_reports_without_network(tmp_path, fake_models, capsys, no_network):
    fetch_models = _script("fetch_models")
    assert fetch_models.main(["--check", "--to", str(tmp_path)]) == 1
    for name, data in fake_models.items():
        (tmp_path / name).write_bytes(data)
    assert fetch_models.main(["--check", "--to", str(tmp_path)]) == 0
    assert "all present" in capsys.readouterr().out
    assert offline.missing_models(tmp_path, verify=True) == []
    assert no_network == []


# --- Offline bundle ----------------------------------------------------------------------------------------------


def test_bundle_dry_run_plans_without_writing(tmp_path, capsys, no_network):
    bundle = _script("build_offline_bundle")
    out = tmp_path / "dist"
    assert bundle.main(["--dry-run", "--out", str(out)]) == 0
    plan = json.loads(capsys.readouterr().out)
    assert not out.exists()  # nothing downloaded or written
    assert {"rapidocr>=3.4,<3.5", "streamlit>=1.62", "setuptools>=68", "pip"} <= set(plan["requirements"])
    cross = [s for s in plan["pip_commands"] if "--platform" in s["cmd"]]
    assert {s["target"] for s in cross} == {"win_amd64, Python 3.11", "win_amd64, Python 3.12"}
    assert all("--only-binary=:all:" in s["cmd"] for s in cross)
    assert len(plan["models"]) == 3
    assert no_network == []


def test_bundle_ships_the_code_but_never_data_or_keys():
    bundle = _script("build_offline_bundle")
    files = {p.as_posix() for p in bundle.source_files()}
    for needed in ("APProcessor.bat", "APProcessor.command", "pyproject.toml", "scripts/fetch_models.py",
                   "ap_coder/static/InterVariable.woff2", "docs/PILOT.md", ".env.example"):  # fmt: skip
        assert needed in files, needed
    assert not [f for f in files if f.startswith(("private/", ".venv/", "wheelhouse/", ".git/")) and
                f != "private/README.md"]  # fmt: skip
    for path, out in (("private/ap_coder.db", True), ("private/README.md", False), (".env", True),
                      (".env.local", True), (".env.example", False), (".venv/lib/x.py", True),
                      ("ap_coder/__pycache__/x.pyc", True), ("ap_coder/cli.py", False)):  # fmt: skip
        assert bundle.excluded(Path(path)) is out, path


def test_bundle_never_ships_databases_spreadsheets_or_invoices_left_in_the_code_folder(tmp_path, monkeypatch):
    """A copy of the code without git (e.g. an unzipped download) lists every file: a database, a labels workbook,
    an invoice PDF or the Streamlit secrets left in it are data, never code. A database is not git-ignored, so
    even a git checkout would list it as a new file."""
    bundle = _script("build_offline_bundle")
    code = ("ap_coder/cli.py", "samples/a.pdf", "data/chart_of_accounts.csv", ".streamlit/config.toml")
    data = ("ap_coder.db", "old/ap_coder.db-wal", "labels.xlsx", "Invoice 1.pdf", "scan.TIF", ".streamlit/secrets.toml")
    for rel in code + data:
        (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / rel).write_text("x")
    monkeypatch.setattr(bundle, "ROOT", tmp_path)  # no .git here
    files = {p.as_posix() for p in bundle.source_files()}
    assert files == set(code)


def test_bundle_zip_keeps_the_mac_launcher_executable(tmp_path):
    bundle = _script("build_offline_bundle")
    stage = tmp_path / "stage"
    (stage / "wheelhouse").mkdir(parents=True)
    (stage / "APProcessor.command").write_text("#!/bin/bash\n")
    (stage / "README.md").write_text("hi")
    (stage / "wheelhouse" / "x-1.0-py3-none-any.whl").write_bytes(b"PK")
    dest = tmp_path / "b.zip"
    bundle._zip(stage, dest)
    with zipfile.ZipFile(dest) as zf:
        infos = {i.filename: i for i in zf.infolist()}
    assert set(infos) == {"APProcessor/APProcessor.command", "APProcessor/README.md",
                          "APProcessor/wheelhouse/x-1.0-py3-none-any.whl"}  # fmt: skip
    assert (infos["APProcessor/APProcessor.command"].external_attr >> 16) & 0o111
    assert not (infos["APProcessor/README.md"].external_attr >> 16) & 0o111


# --- Launchers -------------------------------------------------------------------------------------------------


def test_windows_launcher():
    """APProcessor.bat only finds (or, with consent, installs) a Python and hands over to scripts/launch.py;
    install.bat and start.bat run APProcessor.bat, so there is one setup, not three."""
    for name in ("APProcessor.bat", "install.bat", "start.bat"):
        raw = (ROOT / name).read_bytes()
        assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b""), name  # CRLF throughout (cmd.exe needs it)
    text = (ROOT / "APProcessor.bat").read_text(encoding="ascii")
    assert "%PY% scripts\\launch.py %*" in text
    assert '".venv\\Scripts\\python.exe" -c "import encodings, pip"' in text  # set up before: straight in
    # 3.12 and 3.11 first: the offline bundle has packages for them (3.13 only when neither is there).
    assert "for %%V in (3.12 3.11 3.13)" in text and "(3, 11) <= sys.version_info[:2] <= (3, 13)" in text
    assert "for %%V in (312 311 313)" in text
    assert "winget install -e --id Python.Python.3.12 --scope user" in text  # only when no Python is found
    assert text.index("call :findpython") < text.index("call :installpython")
    # Offline build: winget (the internet) only with the developers' opt-in, never by default or --yes alone.
    assert "if not defined PY if defined AP_ONLINE call :installpython" in text
    assert 'if /i "%AP_ALLOW_INTERNET%"=="%%V" set "AP_ONLINE=1"' in text
    # From a network share (\\server\share): pushd maps a drive letter, cd /d cannot.
    assert 'pushd "%~dp0"' in text and 'cd /d "%~dp0"' not in text and "popd" in text
    assert "if not defined AP_YES set /p" in text  # asked once; automated runs (AP_NO_PAUSE, --yes) are not
    assert "https://www.python.org/downloads/" in text and "if not defined AP_NO_PAUSE pause" in text
    assert 'call "%~dp0APProcessor.bat" --installer %*' in (ROOT / "install.bat").read_text()
    assert 'call "%~dp0APProcessor.bat" %*' in (ROOT / "start.bat").read_text()
    assert "APProcessor.bat first" in (ROOT / "terminal.bat").read_text()


def test_mac_launcher():
    raw = (ROOT / "APProcessor.command").read_bytes()
    assert b"\r" not in raw and raw.startswith(b"#!/bin/bash")
    text = raw.decode()
    assert '"$PY" scripts/launch.py "$@"' in text and "PY=.venv/bin/python" in text
    assert "(3, 11) <= sys.version_info[:2] <= (3, 13)" in text
    assert "/usr/bin/python3" in text  # skipped on a Mac: it only offers Apple's developer tools
    assert "--installer" in (ROOT / "install.sh").read_text()
    if os.name == "posix":
        assert os.access(ROOT / "APProcessor.command", os.X_OK)
        assert 'APProcessor.command" "$@"' in (ROOT / "start.sh").read_text()


def test_check_deps():
    check_deps = _script("check_deps")
    assert check_deps.problem("surely-not-installed-package>=1").startswith("Missing")
    assert check_deps.problem("pip>=9999").startswith("Outdated")
    assert check_deps.problem("pip>=1") == ""
    assert check_deps.problem("pip>=9999; sys_platform == 'no-such-os'") == ""
    assert "streamlit>=1.62" in check_deps.requirements()
    assert "rapidocr>=3.4,<3.5" not in check_deps.requirements()
    assert {"rapidocr>=3.4,<3.5", "pytest>=8"} <= set(check_deps.requirements(extras=("ocr", "dev")))
    assert check_deps.requirement_name("Rapidocr_ONNXRuntime>=1.3; sys_platform == 'win32'") == "rapidocr-onnxruntime"


def test_python_3_13_does_not_need_the_ocr_package_it_cannot_install(monkeypatch):
    """rapidocr-onnxruntime is published for Python up to 3.12: on 3.13 it is not asked for (RapidOCR 3 reads)."""
    check_deps = _script("check_deps")
    (pinned,) = [r for r in check_deps.requirements(extras=("ocr",)) if r.startswith("rapidocr-onnxruntime")]
    assert "python_version < '3.13'" in pinned
    monkeypatch.setattr(check_deps, "Requirement", None)  # the fallback without packaging understands it too
    monkeypatch.setattr(check_deps.sys, "version_info", (3, 13, 0))
    assert not check_deps.applies(pinned) and check_deps.problem(pinned) == ""
    monkeypatch.setattr(check_deps.sys, "version_info", (3, 12, 4))
    assert check_deps.applies(pinned)


def test_ap_coder_installed_from_a_network_share_is_this_folder():
    """pip records \\\\server\\share\\AP Coder as file://server/share/AP%20Coder: the server is the URL's host."""
    check_deps = _script("check_deps")
    unc = check_deps.file_url_path("file://server/share/AP%20Coder")
    assert unc.as_posix().lstrip("/") == "server/share/AP Coder" and str(unc).startswith(("//", "\\\\"))
    assert check_deps.file_url_path(Path(ROOT).as_uri()) == Path(ROOT)
    assert check_deps.file_url_path("file://localhost" + Path(ROOT).as_uri()[7:]) == Path(ROOT)


def test_check_deps_knows_where_ap_coder_is_installed_from(tmp_path, monkeypatch):
    """Only pip's record counts: the project folder's own ap_coder.egg-info (on sys.path while the launcher
    runs) does not, and an install from another copy of the folder is not this one."""
    check_deps = _script("check_deps")
    code = tmp_path / "AP Coder"  # a space, as in a real folder name
    code.mkdir()
    site = tmp_path / "site"
    dist = site / "ap_coder-0.1.0.dist-info"
    dist.mkdir(parents=True)
    (dist / "METADATA").write_text("Metadata-Version: 2.1\nName: ap-coder\nVersion: 0.1.0\n")
    (dist / "direct_url.json").write_text(json.dumps({"url": code.as_uri(), "dir_info": {"editable": True}}))
    egg = code / "ap_coder.egg-info"
    egg.mkdir()
    (egg / "PKG-INFO").write_text("Metadata-Version: 2.1\nName: ap-coder\nVersion: 0.1.0\n")
    monkeypatch.syspath_prepend(str(site))
    monkeypatch.syspath_prepend(str(code))
    assert check_deps.self_problem(code) == ""
    assert check_deps.self_problem(tmp_path / "elsewhere") != ""


def test_first_run_records_a_data_folder_once(tmp_path, monkeypatch):
    first_run = _script("first_run")
    monkeypatch.delenv("AP_PRIVATE_DIR", raising=False)
    monkeypatch.setattr(first_run, "ROOT", tmp_path / "code")
    monkeypatch.setattr(first_run, "DEFAULT_DATA_DIR", tmp_path / "APCoder")
    assert first_run.choose_data_dir() == tmp_path / "APCoder"
    monkeypatch.setattr(first_run, "DEFAULT_DATA_DIR", tmp_path / "elsewhere")
    assert first_run.choose_data_dir() == tmp_path / "APCoder"  # kept, not changed on the next run
