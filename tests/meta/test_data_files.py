"""The repository is public: company files never go in. Every data-like file git tracks (a CSV, a workbook, a PDF, a
picture, a ZIP, a database, a saved email, a JSON file) is named in ``data_files.txt`` beside this test: the made-up
reference files, sample invoices, screenshots and fixtures. One more fails here until it is added to that list on
purpose. ``.gitignore`` keeps an export or an invoice dropped next to the code out of git; this list is the rule for
the folders it can't close (``data/``, ``samples/``, ``tests/fixtures/``, ``docs/``), where the samples live."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import ROOT

LIST = Path(__file__).with_name("data_files.txt")
# What a company's file would be: an export, an invoice, a scan or a photo, a backup, a database, a saved email.
DATA = (".csv", ".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".zip", ".db", ".eml", ".msg", ".json")


def is_data(name: str) -> bool:
    suffix = Path(name).suffix.lower()
    return suffix in DATA or suffix.startswith(".xls")


def _tracked() -> list[str]:
    """The data-like files git tracks. GitHub's ZIP and the offline bundle have no git to ask: skipped there."""
    git = shutil.which("git")
    if not (ROOT / ".git").exists() or not git:
        pytest.skip("not a git checkout")
    out = subprocess.run([git, "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        pytest.skip("git can't list the files here")
    return sorted(name for name in out.stdout.split("\0") if name and is_data(name))


def _listed() -> list[str]:
    lines = LIST.read_text(encoding="utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.startswith("#")]


def test_an_export_an_invoice_or_a_database_is_a_data_file_and_code_is_not():
    data = ["Supplier Master.csv", "data/Accounts.XLS", "docs/chart.xlsm", "book.xlsx", "samples/invoice.PDF",
            "scan.jpeg", "tests/fixtures/mail.msg", "backup.zip", "ap_coder.db", "samples/truth/a.json"]  # fmt: skip
    assert [name for name in data if not is_data(name)] == []
    assert not any(map(is_data, ["ap_coder/erp/exports.py", "docs/REFERENCE.md", "requirements.txt", ".gitignore"]))


def test_every_data_file_git_tracks_is_on_the_list():
    new = sorted(set(_tracked()) - set(_listed()))
    assert not new, (
        f"{new}: the repository is public: company files never go in. If this is company data, take it out of git "
        "(it belongs in the data folder). A new made-up sample is added to tests/meta/data_files.txt on purpose."
    )


def test_the_list_names_only_files_git_still_tracks():
    gone = sorted(set(_listed()) - set(_tracked()))
    assert not gone, f"no longer tracked: take {gone} out of tests/meta/data_files.txt"
