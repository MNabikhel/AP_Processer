"""Opening the dashboard in the browser (scripts/launch.py's open_browser, and ``python -m ap_coder serve`` on its own).

On a Linux computer with no desktop (started over SSH, a server), Python's webbrowser picks a text browser (lynx,
w3m, www-browser) and runs it in this very window, waiting for it: the launcher would hang in it, the dashboard's
output mixed into it. There, no browser is opened and the address is said instead. Windows and Macs always have one.
"""

from __future__ import annotations

import sys
import webbrowser
from pathlib import Path

import pytest

from ap_coder.ops import offline
from ap_coder.web import server as web_server

from .scripts import load_script

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def launch(monkeypatch):
    module = load_script("launch", "ap_script_launch_browser")
    yield module
    module.close_log()


@pytest.fixture
def opened(monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url, *a, **k: seen.append(url) or True)
    for name in ("BROWSER", "DISPLAY", "WAYLAND_DISPLAY"):
        monkeypatch.delenv(name, raising=False)
    return seen


def test_a_linux_computer_without_a_desktop_opens_no_browser_and_says_the_address(launch, opened, monkeypatch, capsys):
    monkeypatch.setattr(sys, "platform", "linux")
    assert not offline.browser_wanted()
    launch.open_browser("http://localhost:8501")
    assert opened == [] and "http://localhost:8501" in capsys.readouterr().out


@pytest.mark.parametrize("variable", ["DISPLAY", "WAYLAND_DISPLAY"])
def test_a_linux_desktop_opens_the_browser(launch, opened, monkeypatch, variable):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setenv(variable, ":0")
    launch.open_browser("http://localhost:8501")
    assert opened == ["http://localhost:8501"]


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_windows_and_macs_always_open_the_browser(launch, opened, monkeypatch, platform):
    monkeypatch.setattr(sys, "platform", platform)
    launch.open_browser("http://localhost:8501")
    assert opened == ["http://localhost:8501"]


@pytest.mark.parametrize("platform", ["this computer's", "darwin"])
def test_browser_none_still_opens_nothing_anywhere(launch, opened, monkeypatch, platform):
    if platform == "darwin":
        monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setenv("BROWSER", "none")
    launch.open_browser("http://localhost:8501")
    assert opened == []


def test_the_dashboard_started_on_its_own_follows_the_same_rule(opened, monkeypatch, capsys):
    import urllib.request

    class Up:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class Direct:
        def open(self, url, timeout):
            return Up()

    monkeypatch.setattr(urllib.request, "build_opener", lambda *handlers: Direct())
    monkeypatch.setattr(sys, "platform", "linux")
    web_server._announce_when_up("http://127.0.0.1:8613", open_browser=True)
    assert opened == [] and "AP Coder is running at http://127.0.0.1:8613" in capsys.readouterr().out
