"""The offline proof: AP Coder never reaches the internet.

The only network it may use is this computer (its own dashboard on 127.0.0.1, LM Studio on localhost), and the Azure
check's endpoint once AP switched that check on in Settings. Developers and the bundle builder opt in to the internet
with AP_ALLOW_INTERNET=1 (ap_coder/ops/offline.py).

* A static audit: every module that can open a connection is listed here with what keeps it on this computer; a new
  one fails the test until it is added (with its gate). Pages, styles and scripts name no other host.
* Under the network guard (tests/offline_guard.py, the ``offline_guard`` fixture: anything but loopback is refused and
  recorded), with every proxy and package index pointed at an address that must never be used:
  the launcher's install from a wheelhouse; ``python -m ap_coder serve`` walked through as AP uses it (every page and
  static file, every sample invoice: text PDFs, a scan, photos, an emailed invoice; approval, export, Settings and
  Help), with LM Studio off and with a fake LM Studio on 127.0.0.1; the same in this process; a model server or an
  Azure endpoint on the internet.
* On Linux, the whole walk again inside a network namespace that has no network but ``lo`` (``unshare -rn``).
* In Chromium (when Playwright is installed): every page, every request it makes goes to 127.0.0.1.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from ap_coder.ai import local_llm
from ap_coder.core.config import LocalLLMSettings, Settings
from ap_coder.ops import offline

from . import offline_flow
from . import offline_guard as guard_module
from .conftest import chromium_path

ROOT = Path(__file__).resolve().parent.parent
REAL_FETCH = local_llm._fetch_json  # conftest replaces it for every test; the real one is needed here


def _bait(monkeypatch: pytest.MonkeyPatch) -> None:
    """Proxies and package indexes that point at an address no traffic may go to."""
    for name, value in guard_module.BAIT_ENV.items():
        monkeypatch.setenv(name, value)


# --- The guard itself ----------------------------------------------------------------------------------------------


def test_the_guard_refuses_the_internet_and_allows_this_computer(offline_guard):
    import socket

    with pytest.raises(ConnectionRefusedError), socket.socket() as s:
        s.connect(("203.0.113.7", 80))
    with pytest.raises(ConnectionRefusedError):
        socket.getaddrinfo("example.com", 443)
    with pytest.raises(ConnectionRefusedError):
        socket.create_connection(("example.com", 443), timeout=1)
    assert len(offline_guard.attempts) == 3
    offline_guard.attempts.clear()
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.create_connection(server.getsockname(), timeout=2):
            pass
    socket.getaddrinfo("localhost", 80)
    assert offline_guard.attempts == []


def test_the_guard_reaches_a_python_subprocess(tmp_path):
    log = tmp_path / "guard.log"
    code = (
        "import urllib.request\n"
        "try:\n    urllib.request.urlopen('http://example.com/', timeout=2)\n"
        "except OSError:\n    pass"
    )
    subprocess.run([sys.executable, "-c", code], env=guard_module.subprocess_env(log), timeout=60, check=True)
    attempts = guard_module.read_log(log)
    assert attempts and all(guard_module.BAIT_HOST in a or "example.com" in a for a in attempts)


# --- Static audit ----------------------------------------------------------------------------------------------------

# Every module that can talk to another computer, and what keeps it on this one. A module that starts to import a
# network library (or to run git / winget / curl) fails the audit until it is listed here, with its gate.
TOUCHPOINTS = {
    "ap_coder/web/server.py": "urllib to its own /healthz on 127.0.0.1 (no proxy); webbrowser opens that address",
    "ap_coder/web/security.py": "socket.gethostname/getfqdn: only when AP_DASHBOARD_ADDRESS=0.0.0.0 (office network)",
    "ap_coder/ai/local_llm.py": "LM Studio on this computer or network (no proxy); a server on the internet only with "
    "AP_ALLOW_INTERNET=1",
    "ap_coder/ai/page_reader.py": "OvisOCR2 in LM Studio: same gate as local_llm",
    "ap_coder/ai/inference.py": "Azure OpenAI only with AP_ALLOW_INTERNET=1 (local_llm.resolve_provider); else LM "
    "Studio",
    "ap_coder/ai/extraction.py": "Azure Document Intelligence client: AP_ALLOW_INTERNET=1, or the Azure check's client",
    "ap_coder/ai/azure_check.py": "Azure Document Intelligence: only switched on in Settings, only its endpoint host "
    "(only_host)",
    "ap_coder/ops/doctor.py": "doctor --online: Azure only with AP_ALLOW_INTERNET=1; LM Studio as local_llm",
    "ap_coder/ops/offline.py": "import_azure: imports only; free_port binds and connects on this computer's dashboard "
    "address (no proxy)",
    "ap_coder/core/paths.py": "git log -1 on this checkout, for its build (no network)",
    "ap_coder/ops/service.py": "the company server's service: urllib to its own dashboard on 127.0.0.1 (/healthz, "
    "/shutdown; no proxy)",
    "ap_coder/core/config.py": "socket.getaddrinfo of a model server's name, to judge the address it has (no "
    "connection)",
    "scripts/launch.py": "pip --no-index from the wheelhouse (PyPI only with AP_ALLOW_INTERNET=1); socket binds "
    "127.0.0.1; webbrowser opens http://localhost",
    "scripts/running.py": "the dashboard on 127.0.0.1 (no proxy)",
    "scripts/fetch_models.py": "model download only with AP_ALLOW_INTERNET=1 (else copied from models/)",
    "scripts/check.py": "git status on this checkout, for the changed files (no network)",
    "scripts/check_deps.py": "urllib.request.url2pathname only (a file: URL), no connection",
    "scripts/install.py": "git fetch/pull only with AP_ALLOW_INTERNET=1",
    "scripts/build_offline_bundle.py": "the bundle builder (online by design: it sets AP_ALLOW_INTERNET=1 itself)",
    "scripts/ci/old_versions.py": "the CI and developer test of updating from older versions: installs each OLD "
    "version online (pip, git archive); never run by the launcher, and the new version under test updates offline",
    "scripts/ci/fake_lm_studio.py": "CI's stand-in for LM Studio: listens on 127.0.0.1 and asks AP Coder's own page "
    "there (no proxy); never run by the launcher",
}
_NETWORK = re.compile(
    r"^\s*(?:import|from)\s+(?:urllib\.request|urllib\.error|http\.client|socket|requests|httpx|webbrowser|aiohttp|"
    r"smtplib|imaplib|poplib|ftplib|telnetlib|xmlrpc|openai|azure|huggingface_hub|modelscope)\b"
    r"|import_azure\(|import_module\(\s*[\"']azure|[\"'](?:git|winget|curl|wget|brew|pip)[\"']|urlopen\(",
    re.MULTILINE,
)


def test_every_network_touchpoint_is_known_and_gated():
    found = set()
    for folder in ("ap_coder", "scripts"):
        for path in (ROOT / folder).rglob("*.py"):
            if _NETWORK.search(path.read_text(encoding="utf-8")):
                found.add(path.relative_to(ROOT).as_posix())
    unknown = sorted(found - set(TOUCHPOINTS))
    assert not unknown, f"new network code, not in the audit (add it to TOUCHPOINTS with its gate): {unknown}"
    gone = sorted(set(TOUCHPOINTS) - found)
    assert not gone, f"listed in the audit but no longer touching the network (remove them): {gone}"


def test_launcher_scripts_download_nothing_without_the_opt_in():
    bat = (ROOT / "APProcessor.bat").read_text(encoding="utf-8")
    assert "call :installpython" in bat and re.search(r"if defined AP_ONLINE call :installpython", bat)
    assert bat.count("winget install") == 1  # inside :installpython only
    for name in ("APProcessor.bat", "APProcessor.command", "scripts/terminal.bat"):
        text = (ROOT / name).read_text(encoding="utf-8")
        for line in text.splitlines():
            plain = line.strip().lower()
            if plain.startswith(("rem", "#", "echo", "::")):
                continue
            assert not re.search(
                r"\b(curl|wget|invoke-webrequest|bitsadmin|pip install|git (fetch|pull|clone))\b", plain
            ), f"{name}: {line}"


_URL = re.compile(r"""(?:https?:|wss?:)?//[A-Za-z0-9][A-Za-z0-9.-]*[A-Za-z0-9](?::\d+)?[^\s"'`)<>]*""")
_NAMESPACES = ("http://www.w3.org/2000/svg", "http://www.w3.org/1999/xlink")


def test_pages_styles_and_scripts_name_no_other_host():
    """Templates, styles, scripts and pictures load everything from this server (fonts and icons are in static/):
    an address of another host is only a placeholder the person types over (``https://<name>...``)."""
    problems = []
    folders = [
        ROOT / "ap_coder" / "web" / "templates",
        ROOT / "ap_coder" / "web" / "static",
        ROOT / "ap_coder" / "static",
        ROOT / "ap_coder" / "assets",
    ]
    for folder in folders:
        for path in folder.rglob("*"):
            if path.suffix not in (".html", ".css", ".js", ".svg"):
                continue
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for url in _URL.findall(line):
                    if url.startswith(_NAMESPACES) or "localhost" in url or "127.0.0.1" in url:
                        continue
                    if url.startswith("//") and not re.match(r"//[A-Za-z0-9-]+\.[A-Za-z]{2,}", url):
                        continue  # a comment (// note) or a path, not a host
                    problems.append(f"{path.relative_to(ROOT)}:{number}: {url}")
    assert not problems, "\n".join(problems)
    css = (ROOT / "ap_coder" / "web" / "static" / "app.css").read_text(encoding="utf-8")
    assert 'url("/fonts/InterVariable.woff2")' in css and 'url("/fonts/MaterialSymbolsRounded.woff2")' in css
    assert (ROOT / "ap_coder" / "static" / "InterVariable.woff2").is_file()


def test_python_written_html_names_no_other_host():
    """Help, the charts and the other pages Python writes: no address of another host (SVG namespaces aside)."""
    problems = []
    for path in sorted((ROOT / "ap_coder").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            for url in re.findall(r"https?://[A-Za-z][^\s\"'<>)]*", line):
                if url.startswith(_NAMESPACES) or re.match(r"https?://(localhost|127\.0\.0\.1)\b", url):
                    continue
                if rel == "ap_coder/ops/offline.py" and "modelscope.cn" in url:
                    continue  # the models' origin, for the bundle builder (fetch_models: AP_ALLOW_INTERNET=1)
                if rel == "ap_coder/ai/inference.py" and "cognitiveservices.azure.com/.default" in url:
                    continue  # Azure OpenAI's sign-in scope (AP_ALLOW_INTERNET=1 only)
                if rel == "ap_coder/ops/service.py" and url == "http://schemas.microsoft.com/windows/2004/02/mit/task":
                    continue  # the scheduled task's XML namespace: a name, never fetched
                problems.append(f"{rel}:{number}: {url}")
    assert not problems, "\n".join(problems)


# --- A model server or an Azure endpoint on the internet --------------------------------------------------------------


@pytest.fixture
def real_fetch(monkeypatch):
    monkeypatch.setattr(local_llm, "_fetch_json", REAL_FETCH)
    local_llm.forget_status()


def _internet_llm(url: str = "https://api.example.com/v1") -> LocalLLMSettings:
    return LocalLLMSettings(provider="local", base_url=url)


def test_a_model_server_on_the_internet_is_never_asked(offline_guard, real_fetch, monkeypatch):
    """AP_LLM_BASE_URL (or AP_PAGE_READER_BASE_URL) set to a host on the internet: the offline build doesn't ask it,
    so no invoice text leaves this computer. It says so, as for a server that isn't running."""
    _bait(monkeypatch)
    status = local_llm.check_server(_internet_llm(), use_cache=False)
    assert not status.reachable and not status.active
    import openai

    client = local_llm.build_local_client(_internet_llm())  # every request fails as a server not running would
    with pytest.raises(openai.APIConnectionError) as refused:
        client.models.list()
    assert "AP_ALLOW_INTERNET" in str(refused.value.__cause__ or refused.value)
    from ap_coder.ai import page_reader

    with pytest.raises(page_reader.PageReaderError, match="AP_ALLOW_INTERNET"):
        page_reader._post_json("https://api.example.com/api/v1/models/load", {}, "key", 1.0)
    assert offline_guard.attempts == []


def test_a_model_server_on_this_computer_or_network_is_asked(offline_guard, real_fetch):
    """LM Studio on this computer is asked (and on another computer of the office network, which isn't the
    internet): the guard sees no attempt for 127.0.0.1."""
    local_llm.check_server(
        LocalLLMSettings(base_url=f"http://127.0.0.1:{offline_flow.free_port()}/v1"), use_cache=False
    )
    assert offline_guard.attempts == []
    assert local_llm.build_local_client(LocalLLMSettings(base_url="http://127.0.0.1:1234/v1")) is not None
    assert local_llm.build_local_client(LocalLLMSettings(base_url="http://192.168.1.20:1234/v1")) is not None


def test_with_the_opt_in_a_model_server_on_the_internet_is_asked(offline_guard, real_fetch, monkeypatch):
    """The gate is AP_ALLOW_INTERNET (developers): with it, the request is made (here the guard stops it)."""
    monkeypatch.setenv(offline.INTERNET_ENV, "1")
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    local_llm.check_server(_internet_llm(), use_cache=False)
    assert any("api.example.com" in a for a in offline_guard.attempts)


def _azure_store(tmp_path):
    from ap_coder.core.store import Store

    return Store(tmp_path / "private" / "ap_coder.db")


def test_the_azure_check_sends_nothing_until_it_is_switched_on(offline_guard, tmp_path, monkeypatch):
    """Endpoint and key saved but the check off: "Test connection" and the background check send nothing. Switched
    on, only the endpoint's host is contacted (here the guard stops it)."""
    from ap_coder.ai import azure_check

    _bait(monkeypatch)
    installed = azure_check.packages_installed()
    monkeypatch.setattr(azure_check, "packages_installed", lambda: True)
    store = _azure_store(tmp_path)
    endpoint, host = "https://ap-proof.cognitiveservices.azure.com/", "ap-proof.cognitiveservices.azure.com"
    azure_check.save_settings(store, enabled=False, endpoint=endpoint, api_key="k" * 32)
    record = azure_check.connection_test(store, Settings())
    assert record["ok"] is False and "off" in record["error"].lower()
    assert azure_check.check_one(Settings(), store) is None
    assert not azure_check.overview(store, Settings())["can_test"]
    assert offline_guard.attempts == []

    if not installed:
        pytest.skip("the Azure packages (optional) aren't installed: switched on, it would say so and send nothing")
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    azure_check.save_settings(store, enabled=True, endpoint=endpoint, api_key="k" * 32)
    assert azure_check.overview(store, Settings())["can_test"]
    record = azure_check.connection_test(store, Settings())
    assert record["ok"] is False  # the guard refused the connection
    assert offline_guard.attempts and all(host in a for a in offline_guard.attempts), offline_guard.attempts


def test_onnx_runtime_usage_events_are_switched_off(monkeypatch):
    """ONNX Runtime (the OCR engines, the table finder) never connects anywhere, but on Windows it writes usage events
    the system may send on: they are switched off before a session is made."""
    import types

    calls = []
    fake = types.SimpleNamespace(disable_telemetry_events=lambda: calls.append("off"))
    monkeypatch.setitem(sys.modules, "onnxruntime", fake)
    offline.quiet_onnxruntime()
    assert calls == ["off"]
    monkeypatch.setitem(sys.modules, "onnxruntime", types.SimpleNamespace())  # an ONNX Runtime without the switch
    offline.quiet_onnxruntime()


# --- The launcher's install from a wheelhouse ----------------------------------------------------------------------


def _wheel(folder: Path, name: str = "ap_offline_probe", version: str = "1.0") -> Path:
    """A minimal pure-Python wheel, built here (no build tool, no index)."""
    files = {
        f"{name}/__init__.py": b"VALUE = 'installed from the wheelhouse'\n",
        f"{name}-{version}.dist-info/METADATA": f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n".encode(),
        f"{name}-{version}.dist-info/WHEEL": b"Wheel-Version: 1.0\nGenerator: ap-offline-proof\nRoot-Is-Purelib: true\n"
        b"Tag: py3-none-any\n",
    }
    record = []
    for path, data in files.items():
        digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
        record.append(f"{path},sha256={digest},{len(data)}")
    record.append(f"{name}-{version}.dist-info/RECORD,,")
    files[f"{name}-{version}.dist-info/RECORD"] = ("\n".join(record) + "\n").encode()
    wheel = folder / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as z:
        for path, data in files.items():
            z.writestr(path, data)
    return wheel


@pytest.fixture
def launch_module(monkeypatch):
    spec = importlib.util.spec_from_file_location("ap_script_launch_offline", ROOT / "scripts" / "launch.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


@pytest.mark.skipif(importlib.util.find_spec("pip") is None, reason="pip isn't installed in this Python")
def test_the_launcher_installs_from_the_wheelhouse_without_the_internet(launch_module, tmp_path, monkeypatch):
    """The launcher's setup path, offline (the default): pip --no-index from the bundle's wheelhouse. Every proxy,
    index and find-links setting (environment, pip.conf) points at the internet, and none of them is used."""
    wheelhouse = tmp_path / "wheelhouse"
    wheelhouse.mkdir()
    _wheel(wheelhouse)
    conf = tmp_path / "pip.conf"
    conf.write_text(
        f"[global]\nindex-url = https://{guard_module.BAIT_HOST}/conf-index\n"
        f"find-links = https://{guard_module.BAIT_HOST}/conf-links\n",
        encoding="utf-8",
    )
    log = tmp_path / "guard.log"
    for name, value in guard_module.subprocess_env(log, base={}, extra=[("PIP_CONFIG_FILE", str(conf))]).items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(launch_module, "wheelhouse_dir", lambda: wheelhouse)
    monkeypatch.setattr(launch_module, "wheelhouse_misfit", lambda: "")
    said: list[str] = []
    monkeypatch.setattr(launch_module, "say", said.append)
    assert launch_module.pip_source() == ["--no-index", "--find-links", str(wheelhouse)]
    target = tmp_path / "site"
    assert launch_module.pip_install(["--target", str(target), "--no-compile", "ap_offline_probe"]), said
    assert (target / "ap_offline_probe" / "__init__.py").is_file()
    assert guard_module.read_log(log) == [], said


def test_without_a_wheelhouse_the_launcher_installs_nothing(launch_module, tmp_path, monkeypatch):
    """An offline bundle (its manifest) that lacks its wheelhouse: nothing installed, never the internet."""
    monkeypatch.setattr(launch_module, "ROOT", tmp_path)
    (tmp_path / "bundle_manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(launch_module, "wheelhouse_dir", lambda: tmp_path / "missing")
    monkeypatch.setattr(launch_module, "say", lambda text: None)
    assert launch_module.pip_source() is None
    monkeypatch.setattr(launch_module.subprocess, "Popen", lambda *a, **k: pytest.fail("pip must not run"))
    assert launch_module.pip_install(["anything"]) is False


def test_the_ocr_models_are_copied_never_downloaded(offline_guard, tmp_path, monkeypatch):
    name = "ap_script_fetch_models_offline"
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / "fetch_models.py")
    fetch_models = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, fetch_models)
    spec.loader.exec_module(fetch_models)
    _bait(monkeypatch)
    with pytest.raises(PermissionError):
        fetch_models.download("https://www.modelscope.cn/x.onnx", tmp_path / "x.onnx")
    source = tmp_path / "models"
    source.mkdir()
    for model in offline.bundle_models():
        (source / model.name).write_bytes(b"damaged")  # a bad copy: not used, and not downloaded instead
    result = fetch_models.fetch(tmp_path / "dest", source=source, say=lambda text: None)  # online: the default (off)
    assert not result.downloaded and result.failed
    assert offline_guard.attempts == []


# --- The dashboard, in this process ---------------------------------------------------------------------------------


@contextlib.contextmanager
def _dashboard(settings: Settings, store):
    """The dashboard on 127.0.0.1 in a thread of this process (so the guard covers the server's own code)."""
    import uvicorn

    from ap_coder.web.app import create_app

    app = create_app(settings, store)
    port = offline_flow.free_port()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning", lifespan="on"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.05)
    try:
        yield app, f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(30)


def _approve_one(app, base: str) -> tuple[int | None, list[str]]:
    """Approve one invoice on its review screen, as the page posts it: its coding, with an account and a cost center
    picked for the lines left for AP (as a reviewer does)."""
    from ap_coder.coding.line_tax import tax_code_of
    from ap_coder.core.store import REVIEW
    from ap_coder.web import invoice_view
    from ap_coder.web.invoice_view import Draft

    store = app.state.store
    gl = store.list_accounts("gl_accounts")[0]["code"]
    ccs = store.list_accounts("cost_centers")  # none in the demo (D-53): a line then needs only its GL account
    cc = ccs[0]["code"] if ccs else ""
    refused = []
    for row in store.list_invoices(REVIEW):
        if offline_flow.request(base, "GET", f"/review/{row['id']}").status != 200:
            continue
        inv = store.get_invoice(row["id"])
        draft = Draft.stored(inv["final_output"] or inv["ai_output"])
        lines = [dict(r) for r in draft.lines]
        for r in lines:
            for column, code in (("predicted_gl_code", gl), ("predicted_cost_center", cc)):
                if r.get(column) in (None, "", "UNASSIGNED"):
                    r[column] = code
        pairs = [(k, str(v)) for k, v in draft.header.items() if v is not None] + [("grids", "1")]
        pairs += [("line_key", f"r{i}") for i in range(len(lines))]
        for column, name in invoice_view.LINE_INPUTS.items():
            pairs += [(name, "" if r.get(column) is None else str(r.get(column))) for r in lines]
        pairs += [(invoice_view.LINE_TAX_CODE, tax_code_of(r)) for r in lines]  # one tax code a line (D-64)
        for column, name in invoice_view.TAX_INPUTS.items():
            pairs += [(name, "" if t.get(column) is None else str(t.get(column))) for t in draft.taxes]
        checked = offline_flow.post_form(base, f"/review/{row['id']}/check", pairs)  # the page checks each edit
        tick = re.search(r'name=\\?"override\\?" value=\\?"(\w+)', checked.text)  # "Approve anyway", ticked
        if tick:
            pairs += [("override", tick.group(1)), ("override_reason", "Checked with the vendor")]  # why (D-75)
        answer = offline_flow.post_form(base, f"/review/{row['id']}/approve", pairs)
        if answer.status == 200 and answer.json().get("ok", True):
            return row["id"], refused
        refused.append(f"{row['id']}: {answer.status} {answer.text[:200]}")
    return None, refused


@pytest.mark.parametrize("lm_studio", [False, True], ids=["no-lm-studio", "fake-lm-studio"])
def test_the_dashboard_in_this_process_never_reaches_the_internet(
    offline_guard, real_fetch, tmp_path, monkeypatch, lm_studio
):
    from ap_coder.core.store import Store

    _bait(monkeypatch)
    fake = offline_flow.FakeLMStudio() if lm_studio else None
    base_url = fake.base_url if fake else f"http://127.0.0.1:{offline_flow.free_port()}/v1"
    s = Settings()
    settings = replace(s, llm=replace(s.llm, provider="local", base_url=base_url))
    store = Store(tmp_path / "private" / "ap_coder.db")
    try:
        with _dashboard(settings, store) as (app, base):
            report = offline_flow.walk(base, tmp_path, invoices_folder=tmp_path / "private" / "invoices")
            approved, refused = _approve_one(app, base)
            offline_flow.crawl(base, report, start=("/exports", "/activity", "/insights", "/spend"))
    finally:
        if fake is not None:
            fake.close()
    assert report.failed() == [], json.dumps(report.as_json(), indent=1)[:4000]
    assert offline_guard.attempts == []
    _all_read(report.processed)
    assert approved is not None, f"no invoice could be approved on its review screen: {refused}"
    pages = [p for p in report.fetched if not p.startswith(("/static", "/fonts", "/assets"))]
    assert any(p.startswith("/settings/") for p in pages) and "/help" in report.fetched, pages
    assert any(p.startswith("/static/") for p in report.fetched) and any(
        p.startswith("/fonts/") for p in report.fetched
    )
    if fake is not None:
        assert any("/v1/models" in seen for seen in fake.seen), fake.seen  # LM Studio was asked, on 127.0.0.1


# --- The real server, in its own process ----------------------------------------------------------------------------


@pytest.mark.parametrize("lm_studio", [False, True], ids=["no-lm-studio", "fake-lm-studio"])
def test_python_m_ap_coder_serve_never_reaches_the_internet(tmp_path, lm_studio):
    """``python -m ap_coder serve`` under the guard, every proxy and index pointing at the internet: the walk works and
    the server never tried to leave this computer (its own start-up check of /healthz included)."""
    log = tmp_path / "guard.log"
    out = offline_flow.run_walk(guard_module.subprocess_env(log), tmp_path, lm_studio=lm_studio)
    attempts = guard_module.read_log(log)
    assert attempts == [], attempts[:20]
    assert out["failed"] == [], json.dumps(out, indent=1)[:4000]
    assert out["announced"], "\n".join(out["server_output"])
    _all_read(out["processed"])
    if lm_studio:
        assert out["lm_studio_seen"], "the fake LM Studio was never asked"


def _all_read(processed: dict[str, int]) -> None:
    """Every sample read: the text PDFs always; the scan and the photos when local OCR is installed (without it they
    can't be read here, as on any computer without it: never sent anywhere instead)."""
    from ap_coder.capture import layout

    assert processed, "nothing was processed"
    unread = 0 if layout.ocr_available() else processed["images"]
    assert processed["failed"] == unread, processed
    assert processed["ok"] == processed["pdfs"] + processed["images"] - unread, processed


def _netns_available() -> bool:
    if not sys.platform.startswith("linux") or shutil.which("unshare") is None:
        return False
    try:
        return subprocess.run(["unshare", "-rn", "true"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


@pytest.mark.skipif(not _netns_available(), reason="needs Linux with unshare -rn (user and network namespaces)")
@pytest.mark.parametrize("lm_studio", [False, True], ids=["no-lm-studio", "fake-lm-studio"])
def test_the_whole_walk_works_in_a_network_namespace_without_network(tmp_path, lm_studio):
    """A real computer with no network at all (only ``lo``): the server starts, every page opens, every sample is read,
    approved and exported."""
    env = {k: v for k, v in os.environ.items() if k != "AP_ALLOW_INTERNET"}
    cmd = ["unshare", "-rn", sys.executable, "-m", "tests.offline_flow", "--netns", "--work", str(tmp_path)]
    done = subprocess.run(
        cmd + (["--lm-studio"] if lm_studio else []), cwd=ROOT, env=env, capture_output=True, text=True, timeout=900
    )
    lines = [line for line in done.stdout.splitlines() if line.startswith("{")]
    assert lines, done.stdout[-3000:] + done.stderr[-3000:]
    out = json.loads(lines[-1])
    assert out["interfaces"] == ["lo"], out["interfaces"]
    assert done.returncode == 0 and out["failed"] == [], json.dumps(out, indent=1)[:4000]
    assert out["announced"]
    _all_read(out["processed"])


# --- In a browser -----------------------------------------------------------------------------------------------------


def _chromium() -> str | None:
    return chromium_path()


@pytest.mark.skipif(_chromium() is None, reason="Playwright with Chromium isn't installed")
def test_every_page_in_a_browser_requests_only_this_computer(tmp_path):
    """Chromium opens every page of a dashboard with invoices in it (Settings and Help included): every request it
    makes (documents, styles, scripts, fonts, pictures, fetches) goes to 127.0.0.1."""
    from playwright.sync_api import sync_playwright

    log = tmp_path / "guard.log"
    env = guard_module.subprocess_env(log)
    env["AP_LLM_BASE_URL"] = f"http://127.0.0.1:{offline_flow.free_port()}/v1"
    server = offline_flow.Server(tmp_path / "data", env)
    requests: list[tuple[str, str]] = []
    try:
        report = offline_flow.walk(server.base, tmp_path, invoices_folder=tmp_path / "data" / "invoices")
        pages = report.pages
        assert len(pages) > 20, pages
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=_chromium(), args=["--no-proxy-server"])
            page = browser.new_page()
            page.on("request", lambda r: requests.append((page.url, r.url)))
            # recorded above; anything not for this computer is stopped here, before it leaves
            page.route(
                "**/*", lambda route: route.continue_() if offline_flow.local_url(route.request.url) else route.abort()
            )
            for path in pages:
                page.goto(server.base + path, wait_until="networkidle")
            browser.close()
    finally:
        server.stop()
    hosts = {re.sub(r"^[a-z]+://([^/:]+).*$", r"\1", url) for _, url in requests if not url.startswith("data:")}
    assert hosts == {"127.0.0.1"}, sorted((p, u) for p, u in requests if "127.0.0.1" not in u)[:20]
    assert guard_module.read_log(log) == []
