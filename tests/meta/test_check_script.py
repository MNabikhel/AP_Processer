"""scripts/check.py picks the tests of what changed; the default run leaves the slow tests to CI."""

import importlib.util
import subprocess
import sys
from pathlib import Path

from tests.conftest import _SLOW, ROOT

spec = importlib.util.spec_from_file_location("check_script", ROOT / "scripts" / "check.py")
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def test_a_module_brings_its_own_tests_and_those_that_import_it():
    picked = check.tests_for(["ap_coder/coding/terms.py"])
    assert "tests/coding/test_terms.py" in picked
    assert "tests/coding/test_guesses_and_learned_codes.py" in picked  # imports ap_coder.coding.terms by name


def test_a_widely_imported_module_brings_only_its_own_tests():
    picked = check.tests_for(["ap_coder/core/store/invoices.py"])  # a part of the store package
    assert picked and all(p.startswith("tests/core/test_store") for p in picked)


def test_a_changed_test_runs_itself_and_a_page_script_its_page_tests():
    assert check.tests_for(["tests/coding/test_tax.py"]) == ["tests/coding/test_tax.py"]
    assert "tests/web/test_web_invoice.py" in check.tests_for(["ap_coder/web/static/js/invoice.js"])


def test_a_script_the_package_and_the_shared_test_files_bring_their_tests():
    assert "tests/launcher/test_update.py" in check.tests_for(["scripts/upgrade.py"])
    assert check.tests_for(["ap_coder/__init__.py"]) == ["tests/meta/test_moved_modules.py"]
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
                          "tests/web/test_web_invoice.py"], cwd=ROOT, capture_output=True, text=True)  # fmt: skip
    collected = [line for line in out.stdout.splitlines() if "::" in line]
    assert collected and not any("in_a_browser" in line for line in collected)
    assert "deselected" in out.stdout


def test_help_prints_the_usage_and_checks_nothing(monkeypatch, capsys):
    """B-1576: ``check.py --help`` took the flag for a file name and ran the check."""
    monkeypatch.setattr(check, "pytest", lambda args: (_ for _ in ()).throw(AssertionError("ran the tests")))
    for flag in ("--help", "-h"):
        assert check.main([flag]) == 0
        out = capsys.readouterr().out
        assert "python scripts/check.py FILE..." in out and "check:" not in out


def test_a_page_its_words_the_guide_or_the_launcher_changed_bring_the_first_day_walk():
    for changed in ("ap_coder/web/templates/review.html", "ap_coder/web/static/js/invoice.js",
                    "ap_coder/web/static/imports.css", "ap_coder/web/pages/jde_files.py", "ap_coder/ops/help.py",
                    "docs/PROTOTYPE.md", "scripts/ci/first_day.py", "APProcessor.bat", "APProcessor.command",
                    "scripts/launch.py", "scripts\\launch.py"):  # fmt: skip
        assert check.walk_needed(["ap_coder/coding/tax.py", changed]), changed
    for changed in ("ap_coder/coding/tax.py", "ap_coder/web/identity.py", "docs/BUGS.md", "tests/web/test_tax.py",
                    "ap_coder/ops/help_text.py", "scripts/check.py"):  # fmt: skip
        assert not check.walk_needed([changed]), changed


def _checked(monkeypatch, capsys, argv: list[str], walk_ok: bool = True) -> tuple[int, str, list[str]]:
    """``check.main(argv)`` with the tests and the walk stood in for: its exit code, what it printed, the walks."""
    walks: list[str] = []
    monkeypatch.setattr(check, "pytest", lambda args: subprocess.CompletedProcess(args, 0, "1 passed\n", ""))
    monkeypatch.setattr(check, "walk", lambda: walks.append("walked") or (walk_ok, "FAILED: the menu does not say x"))
    code = check.main(argv)
    return code, capsys.readouterr().out, walks


def test_the_walk_runs_when_it_matters_and_the_flags_force_or_skip_it(monkeypatch, capsys):
    code, out, walks = _checked(monkeypatch, capsys, ["docs/PROTOTYPE.md"])
    assert code == 0 and walks and "first-day walk: OK" in out
    code, out, walks = _checked(monkeypatch, capsys, ["docs/BUGS.md"])
    assert code == 0 and not walks and "first-day walk: not needed" in out
    code, out, walks = _checked(monkeypatch, capsys, ["docs/PROTOTYPE.md", "--no-walk"])
    assert code == 0 and not walks and "first-day walk: skipped" in out
    code, out, walks = _checked(monkeypatch, capsys, ["--walk", "docs/BUGS.md"])
    assert code == 0 and walks and "--walk" not in out  # (a flag, not a file to check)
    monkeypatch.setattr(check, "changed_files", lambda: [])
    code, out, walks = _checked(monkeypatch, capsys, ["--walk"])  # nothing changed: the walk alone
    assert code == 0 and walks and "Tests:" not in out


def test_a_walk_that_fails_fails_the_check_with_what_the_walk_said(monkeypatch, capsys):
    code, out, walks = _checked(monkeypatch, capsys, ["docs/PROTOTYPE.md"], walk_ok=False)
    assert code == 1 and "FAILED: the menu does not say x" in out and out.rstrip().endswith("check: FAILED")


def test_the_walk_runs_in_a_folder_of_its_own_removed_after_never_the_data_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("AP_PRIVATE_DIR", str(tmp_path / "the developers data"))
    asked = {}

    def run(cmd, **kwargs):
        asked.update(cmd=cmd, env=kwargs["env"], work=Path(cmd[cmd.index("--work") + 1]).parent)
        assert asked["work"].is_dir()
        return subprocess.CompletedProcess(cmd, 1, "1. the start\n", "FAILED: the page does not say ['To review']\n")

    monkeypatch.setattr(check.subprocess, "run", run)
    ok, said = check.walk()
    assert not ok and said.endswith("FAILED: the page does not say ['To review']")
    env, cmd = asked["env"], asked["cmd"]
    assert Path(env["AP_PRIVATE_DIR"]).parent == asked["work"] and "the developers data" not in str(env)
    assert cmd[cmd.index("--start") + 1] == "serve" and int(cmd[cmd.index("--port") + 1]) > 0
    assert not asked["work"].exists()  # (removed after)
