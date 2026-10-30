"""The offline build: nothing leaves this computer, the OCR models are fetched once (and the app copes
without them), the launchers and the offline bundle are put together as the pilot guide says."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import re
import socket
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from ap_coder.capture import analyze, layout
from ap_coder.core.store import APPROVED, Store
from ap_coder.ops import cli, offline
from tests.conftest import ROOT, SAMPLE_STEM, SAMPLES
from tests.scripts import load_script


def _script(name: str):
    module = load_script(name, f"ap_script_{name}")  # its dataclasses look themselves up there
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
def demo_db(tmp_path):
    return tmp_path / "private" / "ap_coder.db"


def test_demo_review_and_jde_export_offline(no_network, demo_db):
    """The demo invoices through validation, tax checks, capture and a JD Edwards batch, offline."""
    from ap_coder.erp import jde
    from ap_coder.ops.demo import load_demo

    store = Store(demo_db)
    counts = load_demo(store)
    assert counts["added"] >= 5
    approved = [store.get_invoice(i["id"]) for i in store.list_invoices(APPROVED)]
    assert approved
    data, name, _ = jde.build_zip(approved, jde.JdeSettings(), lambda final: "10023", batch=1)
    assert name.endswith(".zip") and zipfile.ZipFile(__import__("io").BytesIO(data)).namelist()
    assert no_network == []


def test_every_dashboard_page_runs_offline(no_network, demo_db):
    """Every page of the dashboard and the review screen of each demo invoice, with nothing reachable."""
    from ap_coder.ops.demo import load_demo
    from ap_coder.web.pages import PAGES
    from tests.webclient import client_for

    store = Store(demo_db)
    load_demo(store)
    client = client_for(store)
    for page in PAGES:
        assert client.get(page.path).status_code == 200, page.path
    for tab in ("reading", "automation", "review", "jde", "data", "about"):
        assert client.get(f"/settings/{tab}").status_code == 200, tab
    for inv in store.list_invoices():
        assert client.get(f"/review/{inv['id']}").status_code == 200, inv["id"]
    assert no_network == []


def test_doctor_names_the_data_folder_it_uses_with_the_home_folder_shortened(tmp_path, monkeypatch):
    """B-1501: a checkout on a computer where AP Coder was installed before uses that install's recorded data folder
    (~/.ap_coder/settings.json), which may hold real company data; doctor said only "writable", never which folder.
    It names it, the home folder shortened to ~ (the setup report is pasted elsewhere: no Windows user name)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setenv("AP_PRIVATE_DIR", str(tmp_path / "APCoder"))
    status, detail = {a: (s, d) for a, s, d in offline.offline_checks()}["data folder"]
    assert status == offline.PASS and detail == f"writable: ~{os.sep}APCoder"
    elsewhere = tmp_path.parent / "shared-data"
    monkeypatch.setenv("AP_PRIVATE_DIR", str(elsewhere))
    detail = {a: d for a, _s, d in offline.offline_checks()}["data folder"]
    assert detail == f"writable: {elsewhere}" and Path(elsewhere).is_dir()


def test_doctor_offline_checks_need_no_network(no_network):
    checks = {area: (status, detail) for area, status, detail in offline.offline_checks()}
    assert checks["data folder"][0] == offline.PASS
    assert checks["dashboard network"][0] == offline.PASS
    if offline.ppocrv5_installed() and not offline.missing_models():
        assert checks["OCR PP-OCRv5"][0] == offline.PASS
    assert no_network == []


# --- The dashboard listens locally and loads nothing from the internet ------------------------------------------


def _serve_call(monkeypatch, *argv, command="dashboard") -> dict:
    """What ``python -m ap_coder dashboard`` (an alias of serve) would run the web server with."""
    from ap_coder.web import server

    seen = {}

    def fake_serve(host, port, open_browser=True, store=None):
        seen.update(host=host, port=port, open_browser=open_browser, store=store)
        return 0

    monkeypatch.setattr(server, "serve", fake_serve)
    assert cli.main([command, "--port", "8599", *argv]) == 0
    return seen


def test_dashboard_is_local_only_by_default(monkeypatch):
    monkeypatch.delenv(offline.ADDRESS_ENV, raising=False)
    seen = _serve_call(monkeypatch)
    assert seen["host"] == "127.0.0.1" and seen["port"] == 8599 and seen["open_browser"] is True
    assert _serve_call(monkeypatch, "--no-browser", command="serve")["open_browser"] is False


def test_lan_access_is_opt_in(monkeypatch, capsys):
    monkeypatch.setenv(offline.ADDRESS_ENV, "0.0.0.0")
    assert _serve_call(monkeypatch)["host"] == "0.0.0.0"
    assert "other computers on the network" in capsys.readouterr().err
    checks = {area: status for area, status, _ in offline.offline_checks()}
    assert checks["dashboard network"] == offline.WARN
    monkeypatch.delenv(offline.ADDRESS_ENV)
    assert _serve_call(monkeypatch, "--address", "127.0.0.1")["host"] == "127.0.0.1"
    assert _serve_call(monkeypatch, "--host", "127.0.0.1", command="serve")["host"] == "127.0.0.1"
    assert "other computers" not in capsys.readouterr().err


ALLOWED_HOSTS = {
    "www.w3.org",  # the SVG namespace, not a download
    "cognitiveservices.azure.com",  # the Azure sign-in scope, used only when Azure is set up
    "github.com", "scripts.sil.org",  # the font licence text
    "www.modelscope.cn",  # where scripts/fetch_models.py gets the OCR models at setup; never by the app
    "schemas.microsoft.com",  # the scheduled task's XML namespace (ops/service.py), not a download
    # The finance hub's push to OneLake (erp/hub/onelake.py): only with AP_ALLOW_INTERNET=1 and an Admin's opt-in.
    "login.microsoftonline.com", "onelake.dfs.fabric.microsoft.com", "storage.azure.com",
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


@pytest.mark.parametrize("before", [None, "0"])
def test_onnx_runtime_never_reports_to_microsoft(before):
    """ONNX Runtime (the OCR and table-finder engine) uploads usage events to Microsoft from its own native threads,
    out of reach of the Python socket guard above, unless ORT_DISABLE_TELEMETRY=1 is set before it starts: found by
    tracing every connection of the update from an old version. Set by ap_coder itself, so every process of AP Coder
    (the launcher, the self-check, the server, its page workers) inherits it, whatever the computer had set."""
    env = {k: v for k, v in os.environ.items() if k != "ORT_DISABLE_TELEMETRY"}
    if before is not None:
        env["ORT_DISABLE_TELEMETRY"] = before
    child = "import os; print(os.environ.get('ORT_DISABLE_TELEMETRY'))"
    run_child = f"subprocess.run([sys.executable, '-c', {child!r}], capture_output=True, text=True)"
    code = f"import os, subprocess, sys, ap_coder; {child}; print({run_child}.stdout.strip())"
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    assert out.stdout.split() == ["1", "1"]


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
    assert {"rapidocr>=3.4,<3.5", "fastapi>=0.115,<1", "setuptools>=68", "pip"} <= set(plan["requirements"])
    cross = [s for s in plan["pip_commands"] if "--platform" in s["cmd"]]
    pythons = ("3.11", "3.12", "3.13", "3.14")
    assert [s["target"] for s in cross] == [f"win_amd64, Python {v}" for v in pythons]
    assert all("--only-binary=:all:" in s["cmd"] for s in cross)
    # Without the OCR add-on when a package of it has no wheel there: the same command, minus its packages.
    assert all("onnxruntime>=1.17" in s["cmd"] and "onnxruntime>=1.17" not in s["fallback"] for s in cross)
    assert all("fastapi>=0.115,<1" in s["fallback"] for s in cross)
    # The second OCR engine says Python < 3.13 but works there: downloaded for every version, ignoring that.
    assert all(s["also"][-2:] == ["--ignore-requires-python", "rapidocr-onnxruntime>=1.3"] for s in cross)
    assert not any("rapidocr-onnxruntime" in arg for s in cross for arg in s["cmd"] + s["fallback"])
    assert cross[-1]["also"][:-2] == cross[-1]["cmd"][: len(cross[-1]["also"]) - 2]  # same target, same pip
    assert len(plan["models"]) == 4  # the three PP-OCRv5 models and the table finder
    assert plan["models"][-1] == {
        "name": "yolov8n_layout_general6.onnx",
        "url": "https://www.modelscope.cn/models/RapidAI/RapidLayout/resolve/v1.2.0/onnx/360/yolov8n_layout_general6.onnx",
        "sha256": "927b6edcb268e896e6a170f7d78980591b408e04b3908f54d58eb69efd018c95",
    }
    assert plan["download_hosts"] == ["pypi.org", "files.pythonhosted.org", "www.modelscope.cn"]  # for IT's firewall
    # --mac: one download per platform and Python version, Macs included.
    assert bundle.main(["--dry-run", "--no-models", "--mac", "--out", str(out)]) == 0
    plan = json.loads(capsys.readouterr().out)
    platforms = ("win_amd64", "macosx_14_0_arm64", "macosx_13_0_x86_64")
    expected = [f"{p}, Python {v}" for p in platforms for v in pythons]
    assert [s["target"] for s in plan["pip_commands"]] == ["this computer", *expected]
    assert plan["targets"][1:] == expected and plan["targets"][0].startswith("this computer (")
    assert not out.exists()
    assert no_network == []


def test_bundle_judges_markers_as_the_target_computer():
    bundle = _script("build_offline_bundle")
    win = bundle.marker_env("win_amd64", "3.14")
    assert (win["sys_platform"], win["platform_system"], win["os_name"], win["platform_machine"]) == (
        "win32", "Windows", "nt", "AMD64")  # fmt: skip
    mac = bundle.marker_env("macosx_14_0_arm64", "3.13")
    assert (mac["sys_platform"], mac["platform_system"], mac["platform_machine"], mac["python_version"]) == (
        "darwin", "Darwin", "arm64", "3.13")  # fmt: skip
    assert bundle.marker_env("macosx_13_0_x86_64", "3.11")["platform_machine"] == "x86_64"
    assert bundle.marker_env("manylinux_2_28_x86_64", "3.12")["sys_platform"] == "linux"
    assert bundle.marker_env("some_future_tag", "3.15") == {
        "python_version": "3.15", "python_full_version": "3.15.0", "implementation_name": "cpython",
        "implementation_version": "3.15.0", "platform_python_implementation": "CPython"}  # fmt: skip


def _wheel(folder: Path, name: str, requires: tuple[str, ...] = ()) -> None:
    info = f"{name}-1.0.dist-info"
    with zipfile.ZipFile(folder / f"{name}-1.0-py3-none-any.whl", "w") as zf:
        zf.writestr(f"{info}/METADATA", f"Metadata-Version: 2.1\nName: {name}\nVersion: 1.0\n"
                                        + "".join(f"Requires-Dist: {r}\n" for r in requires))  # fmt: skip
        zf.writestr(f"{info}/WHEEL", "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        zf.writestr(f"{info}/RECORD", "")


@pytest.mark.parametrize(
    ("platform", "expected"),
    [("win_amd64", {"app", "colorama", "notmac"}), ("macosx_14_0_arm64", {"app"}),
     ("manylinux_2_28_x86_64", {"app", "notmac"})],
)  # fmt: skip
def test_bundle_downloads_what_the_target_computer_needs(tmp_path, platform, expected):
    """pip judges the markers in requirements by the Python running it, even for another platform: the builder's
    pip judges them as the target computer, so a bundle built on any computer has what Windows needs (click's
    colorama) and no package a Mac must not get (one with no Mac wheel, marked platform_system != "Darwin").
    Offline: the packages come from a local folder."""
    bundle = _script("build_offline_bundle")
    src, dest = tmp_path / "src", tmp_path / "dest"
    src.mkdir()
    _wheel(src, "app", ('colorama; platform_system == "Windows"', 'notmac; platform_system != "Darwin"',
                        'old; python_version < "3.13"'))  # fmt: skip
    for name in ("colorama", "notmac", "old"):
        _wheel(src, name)
    cmd = bundle.pip_commands(["app"], dest, [platform], ["3.14"], current=False)[0]["cmd"]
    at = cmd.index("download") + 1
    cmd[at:at] = ["--no-index", "--find-links", str(src)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert {p.name.split("-")[0] for p in dest.iterdir()} == expected


def test_bundle_leaves_out_ocr_only_where_it_has_no_wheel(tmp_path, monkeypatch, capsys):
    """onnxruntime has no wheel for Intel Macs on Python 3.14: that target gets everything else (AP Coder runs
    there without OCR), the manifest says so, and the other targets keep the OCR add-on."""
    bundle = _script("build_offline_bundle")
    real_run = subprocess.run
    downloads: list[list[str]] = []

    def run(cmd, **kwargs):
        if cmd[0] != sys.executable:
            return real_run(cmd, **kwargs)  # git ls-files
        downloads.append(cmd)
        if "macosx_13_0_x86_64" in cmd and "onnxruntime>=1.17" in cmd:
            return subprocess.CompletedProcess(cmd, 1, "", "ERROR: No matching distribution found for onnxruntime\n")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(bundle.subprocess, "run", run)
    out = tmp_path / "dist"
    assert bundle.main(["--out", str(out), "--no-models", "--mac", "--python", "3.14"]) == 0
    manifest = json.loads((out / "offline-bundle" / "APProcessor" / "bundle_manifest.json").read_text())
    assert manifest["without_ocr"] == ["macosx_13_0_x86_64, Python 3.14"]
    assert manifest["targets"][1:] == ["win_amd64, Python 3.14", "macosx_14_0_arm64, Python 3.14",
                                       "macosx_13_0_x86_64, Python 3.14"]  # fmt: skip
    # This computer and 2 targets: everything, then rapidocr-onnxruntime ignoring its Requires-Python; the Intel
    # Mac: everything (failed), then everything but the OCR add-on; then, for each of the 4, the optional Azure
    # packages on their own.
    assert len(downloads) == 12 and sum("azure-identity>=1.17" in cmd for cmd in downloads) == 4
    assert manifest["without_azure"] == [] and "azure-identity>=1.17" in manifest["optional_requirements"]
    assert sum("--ignore-requires-python" in cmd for cmd in downloads) == 3
    assert not any("--ignore-requires-python" in cmd and "macosx_13_0_x86_64" in cmd for cmd in downloads)
    assert "No matching distribution found for onnxruntime" in capsys.readouterr().out
    assert list(out.glob("APProcessor-offline-*.zip"))


def test_bundle_ships_the_code_but_never_data_or_keys():
    bundle = _script("build_offline_bundle")
    files = {p.as_posix() for p in bundle.source_files()}
    for needed in ("APProcessor.bat", "APProcessor.command", "pyproject.toml", "scripts/fetch_models.py",
                   "scripts/terminal.bat", "ap_coder/static/InterVariable.woff2", "docs/PILOT.md",
                   ".env.example"):  # fmt: skip
        assert needed in files, needed
    # One file to open per computer: the older versions' install / start scripts and terminal.bat are not shipped.
    for gone in ("install.bat", "install.sh", "start.bat", "start.sh", "terminal.bat"):
        assert gone not in files, gone
    assert not [f for f in files if f.startswith(("private/", ".venv/", "wheelhouse/", ".git/")) and
                f != "private/README.md"]  # fmt: skip
    for path, out in (("private/ap_coder.db", True), ("private/README.md", False), (".env", True),
                      (".env.local", True), (".env.example", False), (".venv/lib/x.py", True),
                      ("ap_coder/__pycache__/x.pyc", True), ("ap_coder/ops/cli.py", False)):  # fmt: skip
        assert bundle.excluded(Path(path)) is out, path


def test_bundle_never_ships_databases_spreadsheets_or_invoices_left_in_the_code_folder(tmp_path, monkeypatch):
    """A copy of the code without git (e.g. an unzipped download) lists every file: a database, a labels workbook,
    an invoice PDF or a .env left in it are data, never code. A database is not git-ignored, so
    even a git checkout would list it as a new file."""
    bundle = _script("build_offline_bundle")
    code = ("ap_coder/ops/cli.py", "samples/a.pdf", "data/chart_of_accounts.csv", "ap_coder/web/static/app.css")
    data = ("ap_coder.db", "old/ap_coder.db-wal", "labels.xlsx", "Invoice 1.pdf", "scan.TIF", "private/.env")
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


def test_bundle_zip_built_on_windows_keeps_the_mac_launcher_executable(tmp_path, monkeypatch):
    """IT often builds the bundle on Windows: zipfile then marks each entry as made on MS-DOS, and the Mac's Archive
    Utility (and unzip) ignore the Unix permissions of such an entry, so APProcessor.command lost its executable bit
    ("could not be executed because you do not have appropriate access privileges")."""
    bundle = _script("build_offline_bundle")
    stage = tmp_path / "stage"
    stage.mkdir()
    (stage / "APProcessor.command").write_text("#!/bin/bash\n")
    monkeypatch.setattr(sys, "platform", "win32")  # what zipfile.ZipInfo reads to set create_system
    bundle._zip(stage, tmp_path / "b.zip")
    monkeypatch.undo()
    with zipfile.ZipFile(tmp_path / "b.zip") as zf:
        info = zf.getinfo("APProcessor/APProcessor.command")
    assert info.create_system == 3  # Unix: the permissions in external_attr count
    assert (info.external_attr >> 16) & 0o111


# --- Launchers -------------------------------------------------------------------------------------------------


def test_windows_launcher():
    """APProcessor.bat only finds (or, with consent, installs) a Python and hands over to scripts/launch.py;
    it is the one file to open on Windows (``--installer`` runs the installer's questions). scripts\\terminal.bat
    opens a command prompt in the app folder for AP Coder's own commands."""
    for name in ("APProcessor.bat", "scripts/terminal.bat"):
        raw = (ROOT / name).read_bytes()
        assert b"\r\n" in raw and b"\n" not in raw.replace(b"\r\n", b""), name  # CRLF throughout (cmd.exe needs it)
    text = (ROOT / "APProcessor.bat").read_text(encoding="ascii")
    assert "%PY% scripts\\launch.py %*" in text
    assert '".venv\\Scripts\\python.exe" scripts\\launch.py --venv-ok' in text  # set up before: straight in
    # Any Python 3.11 or newer (no upper bound): 3.12 first, then 3.11, 3.13, 3.14 (the offline bundle's), then
    # any newer through py -3, then python before python3, then the usual folders (newer ones too).
    assert "for %%V in (3.12 3.11 3.13 3.14) do if not defined PY call :try py -%%V" in text
    assert text.index("call :try py -%%V") < text.index("call :try py -3\n") < text.index("call :try python\n")
    assert text.index("call :try python\n") < text.index("call :try python3\n")
    assert "for %%V in (312 311 313 314)" in text and 'Python\\Python3*")' in text
    assert "sys.exit(sys.version_info[0] * 100 + sys.version_info[1])" in text  # its version, not a range
    assert "if %PYV% GEQ 311 set PY=%*" in text and "3, 13" not in text and "LEQ 313" not in text
    # A Python that is there but too old is said so, never "not installed".
    assert "if defined OLDPY echo Python %OLDPY% is installed, but AP Coder needs Python 3.11 or newer" in text
    assert "PYTHON_MANAGER_AUTOMATIC_INSTALL=false" in text  # py never downloads a missing version
    assert "winget install -e --id Python.Python.3.12 --scope user" in text  # only when no Python is found
    assert text.index("call :findpython") < text.index("call :installpython")
    # Offline build: winget (the internet) only with the developers' opt-in, never by default or --yes alone.
    assert "if not defined PY if defined AP_ONLINE call :installpython" in text
    assert 'if /i "%AP_ALLOW_INTERNET%"=="%%V" set "AP_ONLINE=1"' in text
    # GitHub's ZIP (the code alone: no bundle manifest, wheelhouse or models) may download, like CloseDesk;
    # AP_ALLOW_INTERNET=0 forbids it.
    plain = 'if not exist "bundle_manifest.json" if not exist "wheelhouse\\" if not exist "models\\" set "AP_ONLINE=1"'
    assert plain in text
    assert text.index('set "AP_ONLINE=1"\n') < text.index('if /i "%AP_ALLOW_INTERNET%"=="%%V" set "AP_ONLINE="')
    # From a network share (\\server\share): pushd maps a drive letter, cd /d cannot.
    assert 'pushd "%~dp0"' in text and 'cd /d "%~dp0"' not in text and "popd" in text
    assert "if not defined AP_YES set /p" in text  # asked once; automated runs (AP_NO_PAUSE, --yes) are not
    assert "https://www.python.org/downloads/" in text and "if not defined AP_NO_PAUSE pause" in text
    assert "APProcessor.bat --installer" in text
    terminal = (ROOT / "scripts" / "terminal.bat").read_text()
    assert "APProcessor.bat first" in terminal and 'pushd "%~dp0.."' in terminal  # the app folder: above scripts\


def test_one_file_to_open_per_computer():
    """The app folder's top holds exactly one launcher per computer: APProcessor.bat (Windows) and
    APProcessor.command (Mac, Linux). No install / start scripts beside them to choose from."""
    runnable = sorted(p.name for p in ROOT.iterdir() if p.is_file() and p.suffix.lower() in
                      (".bat", ".cmd", ".command", ".sh", ".ps1", ".exe"))  # fmt: skip
    assert runnable == ["APProcessor.bat", "APProcessor.command"]


def test_mac_launcher():
    raw = (ROOT / "APProcessor.command").read_bytes()
    assert b"\r" not in raw and raw.startswith(b"#!/bin/bash")
    text = raw.decode()
    assert '"$PY" scripts/launch.py "$@"' in text and "PY=.venv/bin/python" in text
    assert ".venv/bin/python scripts/launch.py --venv-ok" in text  # .venv used as it is, unless it is to be remade
    assert 'PREFERRED="3.12 3.11 3.13 3.14"' in text and "<= (3, 13)" not in text  # no upper bound
    assert 'command -v "$py"' in text and "echo python3\n  echo python\n" in text  # python3 before python
    # Apple's stand-in is run only when the developer tools are there (else it opens an install dialog).
    assert "APPLE_STUB=/usr/bin/python3" in text and "xcode-select -p" in text
    assert "./APProcessor.command --installer" in text
    if os.name == "posix":
        assert os.access(ROOT / "APProcessor.command", os.X_OK)


def test_check_deps():
    check_deps = _script("check_deps")
    assert check_deps.problem("surely-not-installed-package>=1").startswith("Missing")
    assert check_deps.problem("pip>=9999").startswith("Outdated")
    assert check_deps.problem("pip>=1") == ""
    assert check_deps.problem("pip>=9999; sys_platform == 'no-such-os'") == ""
    assert "fastapi>=0.115,<1" in check_deps.requirements()
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


def test_doctor_checks_the_model_files_as_the_app_does(tmp_path, monkeypatch):
    """A damaged PP-OCRv5 model (the right name, the wrong content) is one the app won't use: doctor says so too."""
    models = (offline.ModelFile("det.onnx", "https://models.invalid/det.onnx", hashlib.sha256(b"good").hexdigest()),)
    monkeypatch.setattr(offline, "ppocrv5_files", lambda: models)
    monkeypatch.setattr(offline, "ppocrv5_installed", lambda: True)
    monkeypatch.setattr(offline, "model_dir", lambda: tmp_path)
    (tmp_path / "det.onnx").write_bytes(b"damaged")
    assert not offline.ppocrv5_ready()
    (check,) = [c for c in offline.offline_checks() if c[0] == "OCR PP-OCRv5"]
    assert check[1] == offline.WARN and "det.onnx" in check[2]
