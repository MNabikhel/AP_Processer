"""scripts/screens.py: the dashboard's pages pictured at each width, for a person or an agent checking a layout."""

from __future__ import annotations

import subprocess
import sys

import pytest

from tests.conftest import ROOT, chromium_path
from tests.scripts import load_script


def test_a_picture_is_named_after_its_page_and_width():
    screens = load_script("screens", "ap_screens")
    assert screens.picture_name("/", 390) == "home-390.png"
    assert screens.picture_name("/review?q=Acme", 911) == "review-q-Acme-911.png"


def test_screens_pictures_a_page_in_a_browser(tmp_path):
    """One page at one width: its picture saved, its width and what is past its right edge printed."""
    if chromium_path() is None:
        pytest.skip("Playwright with Chromium isn't installed")
    run = subprocess.run([sys.executable, str(ROOT / "scripts" / "screens.py"), "/jde-files", "--widths", "683",
                          "--out", str(tmp_path)], capture_output=True, text=True, timeout=300)  # fmt: skip
    assert run.returncode == 0, run.stdout + run.stderr
    assert (tmp_path / "jde-files-683.png").stat().st_size > 10_000
    assert "jde-files-683.png: /jde-files answered 200; the page is 683 px wide in a 683 px window" in run.stdout
