"""scripts/check.py picks the tests of what changed; the default run leaves the slow tests to CI."""

import importlib.util
import subprocess
import sys

from tests.conftest import _SLOW, ROOT

spec = importlib.util.spec_from_file_location("check_script", ROOT / "scripts" / "check.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def test_a_module_brings_its_own_tests_and_those_that_import_it():
    picked = check.tests_for(["ap_coder/coding/terms.py"])
    assert "tests/test_terms.py" in picked
    assert "tests/test_trust_review.py" in picked  # imports ap_coder.coding.terms by name


def test_a_widely_imported_module_brings_only_its_own_tests():
    picked = check.tests_for(["ap_coder/core/store/invoices.py"])  # a part of the store package
    assert picked and all(p.startswith("tests/test_store") for p in picked)


def test_a_changed_test_runs_itself_and_a_page_script_its_page_tests():
    assert check.tests_for(["tests/test_tax.py"]) == ["tests/test_tax.py"]
    assert "tests/test_web_invoice.py" in check.tests_for(["ap_coder/web/static/js/invoice.js"])


def test_a_script_the_package_and_the_shared_test_files_bring_their_tests():
    assert "tests/test_update.py" in check.tests_for(["scripts/upgrade.py"])
    assert check.tests_for(["ap_coder/__init__.py"]) == ["tests/test_moved_modules.py"]
    assert check.tests_for(["tests/conftest.py"]) is None  # the whole default run
    assert check.tests_for(["pyproject.toml", "ap_coder/coding/tax.py"]) is None


def test_outside_a_git_checkout_it_asks_for_the_files(monkeypatch, capsys):
    monkeypatch.setattr(check, "changed_files", lambda: None)
    assert check.main([]) == 2
    assert "name the files" in capsys.readouterr().out


def test_every_slow_test_listed_still_exists():
    """A test renamed or removed would leave its slow twin in the default run unnoticed."""
    missing = []
    for entry in sorted(_SLOW):
        path, *_, name = entry.split("::")
        text = (ROOT / path).read_text(encoding="utf-8") if (ROOT / path).is_file() else ""
        if f"def {name}(" not in text:
            missing.append(entry)
    assert not missing, missing


def test_the_default_run_leaves_out_browser_and_listed_tests():
    out = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:cacheprovider",
                          "tests/test_web_invoice.py"], cwd=ROOT, capture_output=True, text=True)  # fmt: skip
    collected = [line for line in out.stdout.splitlines() if "::" in line]
    assert collected and not any("in_a_browser" in line for line in collected)
    assert "deselected" in out.stdout
