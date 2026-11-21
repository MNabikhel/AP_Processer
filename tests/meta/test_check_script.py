"""scripts/check.py picks the tests of what changed; the default run leaves the slow tests to CI."""

import importlib.util
import os
import shutil
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


def test_a_widely_imported_module_brings_its_own_tests_and_the_quickest_of_those_that_import_it():
    picked = check.picks(["ap_coder/core/store/invoices.py"])["ap_coder/core/store/invoices.py"]  # a store part
    assert {"tests/core/test_store_package.py", "tests/core/test_store_session.py"} <= picked
    assert len(picked) < 60  # (of the hundreds that import the store)
    mirror = ROOT / "tests" / "coding"
    users = [mirror / "test_slow.py", mirror / "test_quick.py", mirror / "test_new.py", ROOT / "tests/web/test_x.py"]
    times = {"tests/coding/test_slow.py": [30.0, 19.0], "tests/coding/test_quick.py": [1.0, 0.5],
             "tests/web/test_x.py": [0.0, 0.0]}  # fmt: skip
    # Its own folder's first, the quickest first (a file not timed yet: UNTIMED), up to BUDGET seconds.
    assert check.quickest(users, mirror, times) == {mirror / "test_quick.py", mirror / "test_new.py"}
    assert check.quickest(users[:1], mirror, {"tests/coding/test_slow.py": [99.0, 99.0]}) == {users[0]}  # never none


def test_a_changed_test_runs_itself_and_a_page_script_its_page_tests():
    assert check.tests_for(["tests/coding/test_tax.py"]) == sorted(["tests/coding/test_tax.py", *check.RULES])
    assert "tests/web/test_web_invoice.py" in check.tests_for(["ap_coder/web/static/js/invoice.js"])


def test_a_script_the_package_and_the_shared_test_files_bring_their_tests():
    assert "tests/launcher/test_update.py" in check.tests_for(["scripts/upgrade.py"])
    assert check.picks(["ap_coder/__init__.py"]) == {"ap_coder/__init__.py": {"tests/meta/test_moved_modules.py"}}
    assert check.tests_for(["tests/conftest.py"]) is None  # the whole default run
    assert check.tests_for(["pyproject.toml", "ap_coder/coding/tax.py"]) is None


def test_any_change_to_the_app_the_scripts_or_the_tests_runs_the_rules_that_check_the_whole_tree():
    """D-329: the KEPT list of tests/meta/test_core_imports.py (a store part moved, 6420278) and the offline guard's
    TOUCHPOINTS (a script added, b4c06bf) failed only in CI: no test named after the files changed checks them."""
    assert set(check.RULES) <= set(check.tests_for(["ap_coder/core/store/learning.py"]))
    assert "tests/offline/test_offline_guard.py" in check.tests_for(["scripts/new.py"])
    for changed in ("ap_coder/coding/gone.py", "ap_coder/web/templates/review.html", "tests/web/test_new.py"):
        assert set(check.RULES) <= set(check.tests_for([changed])), changed  # (deleted, not Python, new)
    assert not any(t.startswith("tests/meta/") for t in check.tests_for(["tests/meta/test_caches.py"]))  # (in one)
    for path in check.RULES:
        file, _, name = path.partition("::")  # (a folder, a file or one test of it)
        assert (ROOT / file).exists(), path
        assert not name or f"def {name}(" in (ROOT / file).read_text(encoding="utf-8"), path


def test_a_schema_raised_runs_the_rule_that_each_schema_since_27_has_an_old_database():
    """B-2999: SCHEMA_VERSION is in core/store/_base.py, and a schema raised by new tables alone (29's, 2a7ee29:
    _base.py, the store's __init__.py, roles.py, users.py) ran no tests/core/test_migrations.py: the check said OK and
    CI failed on "No old database at schema 33"."""
    rule = "tests/core/test_migrations.py::test_every_schema_since_pr_99s_has_an_old_database"
    raised = ["ap_coder/core/store/_base.py", "ap_coder/core/store/__init__.py", "ap_coder/core/store/roles.py",
              "ap_coder/core/store/users.py"]  # fmt: skip
    assert rule in check.tests_for(raised[:1]) and rule in check.tests_for(raised)
    assert "tests/core/test_migrations.py" in check.tests_for(["ap_coder/core/store/migrations.py"])  # (all of it)


def test_every_test_named_by_hand_is_there():
    """A test renamed would leave its module (or doc) in OWN with none of its tests."""
    missing = [f"{f}: {t}" for f, tests in check.OWN.items() for t in tests if not list(ROOT.glob(t))]
    assert not missing and (ROOT / check.DOC_RULES[0]).is_file(), missing
    assert all((ROOT / f).is_file() for f in check.OWN), [f for f in check.OWN if not (ROOT / f).is_file()]


def test_a_changed_doc_runs_the_rules_every_doc_is_held_to_and_the_tests_that_open_it():
    rules = [t for t in check.DOC_RULES if (ROOT / t).is_file()]  # (test_log_entries.py: once it is there)
    for log in ("docs/BUGS.md", "docs/decisions.md", "docs/bugs/README.md", "docs/decisions/D-329.md", ".ignore"):
        assert check.tests_for([log]) == rules, log
    for doc in ("docs/WHATS_NEW.md", "docs/QUICK_START.md"):  # what the Help page shows
        assert {*rules, "tests/web/test_web_help.py"} <= set(check.tests_for([doc])), doc
    assert "tests/meta/test_how_it_runs_doc.py" in check.tests_for(["docs/HOW_IT_RUNS.md"])
    assert "tests/meta/test_agent_profile.py" in check.tests_for([".claude/agents/developer.md"])
    assert set(rules) <= set(check.tests_for(["CLAUDE.md"])) and check.tests_for(["notes.txt"]) == []


def test_no_module_of_the_app_goes_without_tests():
    """These 28 modules got no test at all and the check said OK (offline_coder.py, the review screen's routes...):
    each now brings tests of its own, the quickest of its many importers or those named in OWN."""
    names = """__main__ ai/offline_coder bench/__main__ bench/jde_company capture/bridge capture/layout
        capture/normalize capture/types capture/workflow coding/taxmath core/store/summary erp/__init__
        erp/jde_retainage ops/__init__ ops/doctor ops/jde_cmd ops/service_windows web/identity web/invoice_form
        web/pages/__init__ web/pages/invoice_approve web/pages/invoice_document web/pages/invoice_lines
        web/pages/invoice_router web/pages/month_end web/pages/settings_hub web/pages/settings_jde web/pages/who"""
    modules = [f"ap_coder/{name}.py" for name in names.split()]
    found = check.picks(modules)
    assert check.untested(found) == []
    assert "tests/ai/test_offline_coding.py" in found["ap_coder/ai/offline_coder.py"]


def test_a_changed_module_no_test_is_picked_for_fails_the_check(monkeypatch, capsys):
    monkeypatch.setattr(check, "picks", lambda files: {f: set() for f in files})
    code, out, _ = _checked(monkeypatch, capsys, ["ap_coder/coding/tax.py", "docs/BUGS.md", "--no-walk"])
    assert code == 1 and "NO TESTS RAN FOR ap_coder/coding/tax.py" in out and "check: OK" not in out
    assert "docs/BUGS.md" not in out  # (a doc is no module)


def test_a_run_from_another_folder_picks_the_same_tests(monkeypatch, tmp_path):
    """The part of a package (core/store/invoices.py) was looked for from the folder check.py ran in: 0 tests."""
    changed = ["ap_coder/core/store/invoices.py", "ap_coder/coding/terms.py", "docs/WHATS_NEW.md"]
    monkeypatch.chdir(ROOT)
    here = check.tests_for(changed)
    monkeypatch.chdir(tmp_path)
    assert check.tests_for(changed) == here and "tests/core/test_store_session.py" in here


def test_the_rules_run_their_slow_tests_but_no_browser_nor_the_offline_guards_whole_walks(monkeypatch, capsys):
    ran = []
    monkeypatch.setattr(check, "pytest", lambda args: ran.append(args) or subprocess.CompletedProcess(args, 0, "", ""))
    assert check.main(["ap_coder/coding/terms.py", "--no-walk"]) == 0
    rules, tests = ran
    assert rules[:4] == ["-m", "", "-k", "not in_a_browser"] and set(rules[-len(check.RULES) :]) == set(check.RULES)
    left_out = [rules[i + 1] for i, a in enumerate(rules) if a == "--deselect"]
    assert "tests/offline/test_offline_guard.py::test_the_whole_walk_works_in_a_network_namespace_without_network" in (
        left_out
    )
    assert left_out and not any(t.startswith("tests/meta/") for t in left_out)
    assert "tests/coding/test_terms.py" in tests and "-m" not in tests and not set(check.RULES) & set(tests)
    migrations = "tests/core/test_migrations.py::test_every_schema_since_pr_99s_has_an_old_database"  # (one test)
    assert capsys.readouterr().out.startswith(f"Rules: {migrations} tests/core/test_store_package.py tests/meta ")


def test_a_changed_function_near_the_length_limit_gets_a_warning(monkeypatch, capsys):
    from tests.meta.test_function_length import LIMIT

    assert LIMIT - 15 < check.NEAR < LIMIT and 900 < check.NEAR_PART < 1000
    monkeypatch.setattr(check, "changed_lines", lambda f: None)  # (all of it new)
    warned = check.near_limits(["ap_coder/bench/__main__.py", "ap_coder/core/store/summary.py", "docs/BUGS.md"])
    assert any(w.startswith("ap_coder/bench/__main__.py::main: ") for w in warned)  # (265 lines)
    assert any(w.startswith("ap_coder/core/store/summary.py: ") for w in warned)  # (997 lines)
    monkeypatch.setattr(check, "changed_lines", lambda f: {1})  # the docstring only: no function changed
    assert check.near_limits(["ap_coder/bench/__main__.py"]) == []
    code, out, _ = _checked(monkeypatch, capsys, ["ap_coder/core/store/summary.py", "--no-walk"])
    assert code == 0 and "near the limit: split before adding: ap_coder/core/store/summary.py" in out
    assert out.rstrip().endswith("check: OK")


def test_a_changed_line_the_code_page_cannot_read_still_ends_in_the_verdict(tmp_path):
    """B-2998: git's and ruff's output were read in the code page (Windows before Python 3.15), so a changed line
    holding ” (byte 0x9D: no character in cp1252) stopped the check with a traceback, before the NO TESTS RAN check,
    the walk and the verdict. Here the locale is ASCII and the output a cp1252 pipe, as on Windows."""
    (tmp_path / "scripts").mkdir()
    shutil.copy(ROOT / "scripts" / "check.py", tmp_path / "scripts" / "check.py")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "slow_tests.txt").write_text("", encoding="utf-8")
    module = tmp_path / "ap_coder" / "words.py"
    module.parent.mkdir()
    module.write_bytes('WORDS = "the “Add new vendor” button"\n'.encode())
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    for args in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c",
                 "commit.gpgsign=false", "commit", "-q", "-m", "start"]):  # fmt: skip
        subprocess.run(["git", *args], cwd=tmp_path, env=env, check=True, capture_output=True)
    changed = 'import os  # the “Add new vendor” button → here\nWORDS = "the “Add new vendor” button"\n'
    module.write_bytes(changed.encode())
    env.update(PYTHONUTF8="0", PYTHONCOERCECLOCALE="0", LC_ALL="C", PYTHONIOENCODING="cp1252")
    done = subprocess.run([sys.executable, "scripts/check.py", "--no-walk"], cwd=tmp_path, env=env, capture_output=True)
    out, err = done.stdout.decode("cp1252", "replace"), done.stderr.decode("cp1252", "replace")
    assert "Traceback" not in err and done.returncode == 1, err
    assert "F401" in out and "the “Add new vendor” button ? here" in out  # (ruff's words, → as the pipe can write it)
    assert "NO TESTS RAN FOR ap_coder/words.py" in out and out.rstrip().endswith("check: FAILED")


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
