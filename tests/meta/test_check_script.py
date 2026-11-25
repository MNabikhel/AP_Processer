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


def test_a_module_directly_in_tests_is_no_test_and_a_word_in_quotes_no_use_of_a_script():
    """B-3009: tests/test_map.py (the test map's module, D-333) was taken for a test file: run by name, pytest collected
    ``tests_for`` as a test ("fixture 'keys' not found") and the check failed, for a change to it, to a function named
    in it, or to scripts/combine.py (its ``coverage combine`` read as a use of the script). Files directly in tests/
    are no tests (tests/conftest.py's ``collect_ignore_glob``)."""
    texts, _ = check.read_tests()
    assert texts and not [p for p in texts if p.parent == ROOT / "tests"]
    found = check.picks(["tests/test_map.py", "scripts/combine.py"])
    assert "tests/test_map.py" not in found["tests/test_map.py"] | found["scripts/combine.py"]
    assert "tests/meta/test_test_map.py" in found["tests/test_map.py"]  # (its own tests, which import it)
    a, b, c = (ROOT / f"tests/x/test_{n}.py" for n in "abc")
    texts = {a: 'subprocess.run(["coverage", "combine"])', b: 'load_script("combine", "ap_script_combine")',
             c: 'names = ("combine", "upgrade")  # each of ROOT / "scripts"'}  # fmt: skip
    assert check.script_users("combine", texts) == [b, c]


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


def test_a_changed_helper_of_the_tests_or_test_file_brings_the_test_files_that_import_it():
    """B-3047: a change to a helper of the tests (tests/fake_ais.py, review_page.py, chart_files.py...) picked no test
    file, and one to a test file others import (tests/erp/test_jde.py: 17 of them) only itself: the check said OK where
    CI failed. Each brings the test files that import it by name (one hop; past WIDE the quickest), and
    tests/jde_exports.py its users, not tests/bench/test_jde_exports.py (of ap_coder/bench/jde_exports.py). The files
    are given with no times: as they import them today, whatever tests/test_times.json says."""
    named = ("tests/erp/test_jde_ais.py", "tests/erp/test_jde_terms.py", "tests/bench/test_jde_exports.py",
             "tests/web/test_web_masterdata.py")  # fmt: skip
    real = {ROOT / f: (ROOT / f).read_text(encoding="utf-8") for f in named}
    assert check.own_tests("tests/fake_ais.py", real, {}) == {ROOT / "tests/erp/test_jde_ais.py"}
    assert check.own_tests("tests/erp/test_jde.py", real, {}) == {ROOT / "tests/erp/test_jde.py", ROOT / named[1]}
    assert check.own_tests("tests/jde_exports.py", real, {}) == {ROOT / "tests/web/test_web_masterdata.py"}
    # (made-up names: this file's own text is read as the others' are)
    a, b, c, d = (ROOT / f"tests/x/test_{n}.py" for n in "abcd")
    texts = {a: "from tests.x.test_one import AN8", b: "from tests.x import (\n    test_one_more,\n    test_one)",
             c: "import tests.x.test_one_more", d: "from tests.x.test_one_post import post"}  # fmt: skip
    assert check.test_users("tests/x/test_one.py", texts) == [a, b]
    many = {ROOT / f"tests/x/test_{n}.py": "import tests.x.helper" for n in range(check.WIDE + 5)}
    said: list[str] = []
    quickest = int(check.BUDGET // check.UNTIMED)  # (not timed: UNTIMED each, up to BUDGET)
    assert len(check.own_tests("tests/x/helper.py", many, {}, said)) == quickest
    left = check.WIDE + 5 - quickest
    assert said == [f"{check.WIDE + 5} test files use it: the quickest {quickest} run, {left} left to CI (about "
                    f"{left * check.UNTIMED:.0f} s more)"]  # fmt: skip


def test_a_changed_helper_of_the_tests_no_test_is_picked_for_fails_the_check():
    """B-3047, as a module of the app does: not a package's __init__.py (tests/__init__.py: none imports it), nor a
    file every test uses (SHARED: the whole default run), nor one gone. Each helper of the tests today brings its
    tests."""
    found = {f: set() for f in ("tests/fake_ais.py", "tests/__init__.py", "tests/web/__init__.py", "tests/conftest.py",
                                "tests/gone.py")}  # fmt: skip
    assert check.untested(found) == ["tests/fake_ais.py"]
    tests = ROOT / "tests"
    helpers = [p.relative_to(ROOT).as_posix() for p in sorted(tests.rglob("*.py"))
               if p.parent == tests or not p.name.startswith("test_")]  # fmt: skip
    assert len(helpers) > 15 and check.untested(check.picks(helpers)) == []


def test_a_changed_module_no_test_is_picked_for_fails_the_check(monkeypatch, capsys):
    monkeypatch.setattr(check, "picks", lambda files, notes=None: {f: set() for f in files})
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
    assert check.main(["ap_coder/coding/terms.py", "--no-walk", "--serial"]) == 0
    rules, tests = ran
    assert rules[:4] == ["-m", "", "-k", "not in_a_browser"] and set(rules[-len(check.RULES) :]) == set(check.RULES)
    left_out = [rules[i + 1] for i, a in enumerate(rules) if a == "--deselect"]
    assert "tests/offline/test_offline_guard.py::test_the_whole_walk_works_in_a_network_namespace_without_network" in (
        left_out
    )
    meta = {t for t in left_out if t.startswith("tests/meta/")}  # (only the slow tests of tests/meta that take long)
    assert left_out and meta <= set(check.slow_tests("tests/meta/test_same_behaviour.py"))
    # The files picked run with their slow tests, no browser (--fast: the default run's tests only).
    assert tests[:4] == ["-m", "", "-k", "not in_a_browser"] and "tests/coding/test_terms.py" in tests
    assert not set(check.RULES) & set(tests)
    migrations = "tests/core/test_migrations.py::test_every_schema_since_pr_99s_has_an_old_database"  # (one test)
    assert f"Rules: {migrations} tests/core/test_store_package.py tests/meta " in capsys.readouterr().out
    ran.clear()
    assert check.main(["ap_coder/coding/terms.py", "--no-walk", "--serial", "--fast"]) == 0
    assert "-m" not in ran[1] and "tests/coding/test_terms.py" in ran[1]


def test_asked_for_the_rules_run_once_as_for_a_change_to_code_whatever_changed(monkeypatch, capsys):
    """B-3014: combine.py's check of docs-only merges ran RULES itself with a bare -m "" (the offline guard's whole
    walks, every route for every role, the browser tests: about 110 s more), and with pyproject.toml RULES twice.
    ``--rules`` runs them as check.py's own run does, once, whatever changed."""
    ran = []
    monkeypatch.setattr(check, "pytest", lambda args: ran.append(args) or subprocess.CompletedProcess(args, 0, "", ""))
    rules = sorted(check.RULES)
    assert check.main(["--rules", "--serial", "--no-walk", "docs/BUGS.md"]) == 0
    times = check.read_tests()[1]
    assert ran == [[*check.rules_args(rules, times), *rules]]  # (the doc's own rules are tests/meta's)
    ran.clear()
    assert check.main(["--rules", "--serial", "--no-walk", "pyproject.toml"]) == 0  # (the whole default run too)
    assert len(ran) == 2 and set(ran[0][-len(rules) :]) == set(rules) and ran[1] == []
    ran.clear()
    monkeypatch.setattr(check, "changed_files", lambda: [])
    assert check.main(["--rules", "--serial", "--no-walk"]) == 0 and ran == [[*check.rules_args(rules, times), *rules]]
    assert capsys.readouterr().out.count("Rules: ") == 3


def test_slow_tests_that_take_long_together_are_left_to_ci(monkeypatch, capsys):
    """B-3019: D-331's probe test (the jde and pages probes run twice, about 50 s) ran in every check's rules while
    tests/test_times.json gave its file 0.7 s. A test file whose slow tests take over SLOW_SECONDS together leaves
    them to CI, in the rules run and among the files picked; a slow rule that takes little still runs. B-3029: this
    test held the seconds tests/test_times.json gives test_accruals.py, so a refresh of the times failed it: the times
    of the files it leans on are made up here."""
    texts, real = check.read_tests()
    probe_file, rules_file = "tests/meta/test_same_behaviour.py", "tests/meta/test_code_rules.py"
    accruals = "tests/erp/test_accruals.py"
    long, short = check.SLOW_SECONDS + 10, check.SLOW_SECONDS - 5  # (the seconds of their slow tests)
    times = {**real, probe_file: [long + 1, 1.0], rules_file: [short + 1, 1.0], accruals: [long + 2, 2.0],
             "tests/coding/test_tax.py": [1.0, 1.0]}  # fmt: skip
    monkeypatch.setattr(check, "read_tests", lambda: (texts, times))
    probe = f"{probe_file}::test_the_jde_and_pages_probes_give_the_same_output_each_run"
    assert probe in check.slow_tests(probe_file)  # (marked so in the file, with others: B-3024)
    args = check.rules_args(sorted(check.RULES), times)
    assert probe in args and f"{rules_file}::test_no_sql_placeholders_are_made_one_per_item_of_a_list" not in args
    ran = []
    monkeypatch.setattr(check, "pytest", lambda args: ran.append(args) or subprocess.CompletedProcess(args, 0, "", ""))
    assert check.main([accruals, "tests/coding/test_tax.py", "--serial", "--no-walk"]) == 0
    left = [ran[1][i + 1] for i, a in enumerate(ran[1]) if a == "--deselect"]
    assert left and all(t.startswith(f"{accruals}::") for t in left)  # (test_tax.py's: none)
    assert f"{accruals}: its slow tests left to CI (about {long:.0f} s)" in capsys.readouterr().out


def test_the_estimate_counts_what_runs_a_page_scripts_tests_slow_ones_and_all(monkeypatch, capsys):
    """B-3027: for a changed page script nothing is left to CI (its browser tests are asked for), yet the estimate
    dropped a long-slow file's slow seconds: "about 1 s" for a run of 27 and one core. Only the files whose slow tests
    are really left out are counted without them."""
    times = {"tests/x/test_a.py": [30.0, 1.0], "tests/x/test_b.py": [3.0, 2.0]}
    assert check.estimate(["tests/x/test_a.py", "tests/x/test_b.py"], times) == 33.0  # (all of them run)
    assert check.estimate(["tests/x/test_a.py"], times, long=["tests/x/test_a.py"]) == 1.0  # (its slow ones: CI's)
    texts, real = check.read_tests()
    monkeypatch.setattr(check, "read_tests", lambda: (texts, {**real, "tests/web/test_web_invoice.py": [30.0, 1.0]}))
    ran = []
    monkeypatch.setattr(check, "pytest", lambda args: ran.append(args) or subprocess.CompletedProcess(args, 0, "", ""))
    monkeypatch.setattr(check.importlib.util, "find_spec", lambda name: object())  # (as if pytest-xdist were there)
    monkeypatch.setenv("AP_CHECK_WORKERS", "2")
    assert check.main(["ap_coder/web/static/js/invoice.js", "--no-walk"]) == 0
    tests = ran[-1]
    assert "tests/web/test_web_invoice.py" in tests and "--deselect" not in tests and tests[:2] == ["-n", "2"]


def test_a_rule_renamed_or_a_picked_set_that_runs_nothing_fails_the_check(monkeypatch, capsys):
    """B-3032: a RULES entry not in the checkout (a rule test renamed) made pytest run none of the rules: on several
    cores it says "no tests ran" (exit code 5), which the check took for a pass, and said "check: OK". Each entry is
    looked for first and one not found is named; a run that runs no test fails, the rules' or the files picked."""
    gone = "tests/core/test_store_package.py::test_a_rule_since_renamed"
    monkeypatch.setattr(check, "RULES", (*check.RULES, gone))
    ran = []

    def pytest(args):  # as pytest: none run for a test named not found (4 serially, 5 on several cores) or none there
        ran.append(args)
        if gone in args or any(a.startswith("tests/x/") for a in args):
            code = 4 if gone in args and "-n" not in args else 5
            return subprocess.CompletedProcess(args, code, "no tests ran in 0.1s\n", "")
        return subprocess.CompletedProcess(args, 0, "1 passed\n", "")

    monkeypatch.setattr(check, "pytest", pytest)
    monkeypatch.setattr(check.importlib.util, "find_spec", lambda name: object())  # (as if pytest-xdist were there)
    monkeypatch.setenv("AP_CHECK_WORKERS", "4")
    for how in ([], ["--serial"]):
        ran.clear()
        assert check.main(["ap_coder/coding/terms.py", "--no-walk", *how]) == 1, how
        out = capsys.readouterr().out
        assert f"RULE NOT FOUND: {gone}" in out and out.rstrip().endswith("check: FAILED"), (how, out)
        assert any("tests/meta" in args for args in ran) and not any(gone in args for args in ran)  # (the others run)
    monkeypatch.setattr(check, "RULES", tuple(r for r in check.RULES if r != gone))
    assert check.run_tests([], ["tests/x/test_none.py"], 1.0, serial=True)  # (a picked set that runs none: failed)
    assert "NO TESTS RAN" in capsys.readouterr().out


def test_a_long_run_goes_on_several_cores(monkeypatch, capsys):
    """pytest-xdist (the parallel extra): a run of over PARALLEL_SECONDS on AP_CHECK_WORKERS cores, else up to 4."""
    ran = []
    monkeypatch.setattr(check, "pytest", lambda args: ran.append(args) or subprocess.CompletedProcess(args, 0, "", ""))
    monkeypatch.setattr(check.importlib.util, "find_spec", lambda name: object())  # (as if it were there)
    monkeypatch.setenv("AP_CHECK_WORKERS", "3")
    assert check.main(["ap_coder/coding/terms.py", "--no-walk"]) == 0
    assert ran[0][:2] == ["-n", "3"] and "on 3 cores" in capsys.readouterr().out  # the rules: about 20 s
    assert check.workers(serial=True) == 1
    monkeypatch.delenv("AP_CHECK_WORKERS")
    assert 1 <= check.workers(serial=False) <= 4
    monkeypatch.setattr(check.importlib.util, "find_spec", lambda name: None)  # not installed: one core
    assert check.workers(serial=False) == 1
    ran.clear()
    check.run_tests([], ["tests/coding/test_tax.py"], check.PARALLEL_SECONDS, serial=False)
    assert ran == [["tests/coding/test_tax.py"]]  # (short: one core)
    times = {"tests/meta/test_a.py": [3.0, 1.0], "tests/coding/test_b.py": [10.0, 2.0]}
    assert check.estimate(["tests/meta", "tests/coding/test_b.py", "tests/x/test_new.py"], times, slow=False) == (
        3.0 + 2.0 + check.UNTIMED
    )
    assert check.estimate(["tests/coding/test_b.py"], times) == 10.0


def _lines_of(text: str, name: str) -> set[int]:
    """The lines of a function's body in a module's text."""
    (node,) = [n for n in check.functions(text) if n.name == name]
    return set(range(node.lineno + 1, node.end_lineno + 1))


def test_a_change_inside_a_function_brings_the_tests_that_name_it(monkeypatch):
    """B-2950 (22082a4: ``ready_problems`` read the files of every invoice, caught by tests/bench/test_seed_volume.py)
    and B-2970 (701e2fd: ``bulk.queue_facts``, caught by tests/web/test_day_one.py) failed only in CI: those tests
    import neither module by name, and exports.py and bulk.py are used by too many test files for the budget."""
    exports, bulk = "ap_coder/web/pages/exports.py", "ap_coder/coding/bulk.py"
    texts = {f: (ROOT / f).read_text(encoding="utf-8") for f in (exports, bulk)}
    # B-3013: the lines taken out were found in the file as it is, then looked for in HEAD's: an edit not committed
    # above queue_facts failed this. Both from one text now, whatever the working tree holds.
    monkeypatch.setattr(check, "committed", lambda f: texts[f])
    taken_out = _lines_of(texts[bulk], "queue_facts")
    changed = {exports: (_lines_of(texts[exports], "ready_problems"), set()), bulk: (set(), taken_out)}
    monkeypatch.setattr(check, "changed_lines", lambda f: changed.get(f, (set(), set())))
    assert check.changed_functions(bulk) == {"queue_facts"}  # (lines taken out: found in the file as it was)
    notes: dict[str, list[str]] = {}
    found = check.picks([exports, bulk], notes)
    assert "tests/bench/test_seed_volume.py" in found[exports] and "tests/web/test_day_one.py" in found[bulk]
    assert any(n.startswith("ready_problems (changed) is named in ") for n in notes[exports])
    assert "tests/web/test_day_one.py" in check.tests_for([exports, bulk], found)
    monkeypatch.setattr(check, "changed_lines", lambda f: None)  # a new file: every function of it
    assert {"ready_problems", "queue_facts"} <= check.changed_functions(exports) | check.changed_functions(bulk)


def test_with_a_test_map_the_functions_changed_bring_the_tests_that_ran_them(monkeypatch, tmp_path, capsys):
    """B-3021: D-333 said check.py reads the test map (tests/test_map.json.gz), and it read none. With a map, the
    functions the diff touches bring the test files that ran them, the quickest within BUDGET, the rest left to CI and
    said so; with none, the picks are as before."""
    from tests.test_map import write

    taxmath = "ap_coder/coding/taxmath.py"
    text = (ROOT / taxmath).read_text(encoding="utf-8")
    monkeypatch.setattr(check, "committed", lambda f: text)
    lines = {taxmath: (_lines_of(text, "invoice_taxes"), set())}
    monkeypatch.setattr(check, "changed_lines", lambda f: lines.get(f, (set(), set())))
    load = check.load_map
    monkeypatch.setattr(check, "load_map", lambda: None)  # no map: as before
    without = check.picks([taxmath])[taxmath]
    volume, day_one = "tests/erp/test_jde_sync_volume.py", "tests/web/test_day_one.py"  # (118 s and 3.5 s)
    write({"format": 1, "tests": [volume, day_one], "map": {f"{taxmath}::invoice_taxes": [0, 1]}}, tmp_path / "m.gz")
    monkeypatch.setattr(check, "load_map", lambda: load(tmp_path / "m.gz"))
    notes: dict[str, list[str]] = {}
    found = check.picks([taxmath], notes)[taxmath]
    assert found - without == {day_one} and volume not in found
    assert any("the test map" in n and day_one in n and "1 left to CI" in n for n in notes[taxmath]), notes
    assert load(tmp_path / "gone.gz") is None


def test_the_test_map_takes_a_functions_signature_and_decorators_for_part_of_it(monkeypatch):
    """B-3030: the map's keys of a diff came from the functions' bodies only (the lines coverage counts), so a changed
    default value, parameter or page address (``@router.get``) brought none of the tests the map says ran it:
    Store.final_approve's signature, D-333's own example, among them."""
    mapped = check.load_map()
    assert mapped, "tests/test_map.json.gz, the committed map"
    module, test_map = mapped
    store, exports = "ap_coder/core/store/invoices.py", "ap_coder/web/pages/exports.py"
    texts = {f: (ROOT / f).read_text(encoding="utf-8") for f in (store, exports)}

    def head(text: str, name: str) -> set[int]:  # its decorators' and its signature's lines, before its body
        (node,) = [n for n in check.functions(text) if n.name == name]
        return set(range(min([node.lineno, *(d.lineno for d in node.decorator_list)]), node.body[0].lineno))

    monkeypatch.setattr(check, "committed", lambda f: texts[f])
    changed = {
        store: (head(texts[store], "final_approve"), set()),
        exports: (set(), head(texts[exports], "exports_page")),
    }
    monkeypatch.setattr(check, "changed_lines", lambda f: changed[f])
    final_approve = f"{store}::InvoicesMixin.final_approve"
    assert check.map_keys(store) == {final_approve} and test_map.files(final_approve)  # (tests that ran it)
    assert check.map_keys(exports) == {f"{exports}::exports_page"}  # (its @router lines, taken out)
    assert set(check.map_spans(texts[store])) == set(module.functions(texts[store]))  # (the map's names, all of them)


def test_a_name_too_many_tests_have_brings_none(monkeypatch):
    texts = {ROOT / f"tests/x/test_{i}.py": "main(); ready_problems()" if i < 2 else "main()" for i in range(20)}
    assert check.named_tests({"main", "__init__", "ready_problems", "gone"}, texts) == {
        "ready_problems": {ROOT / "tests/x/test_0.py", ROOT / "tests/x/test_1.py"}
    }


def test_the_old_windows_clock_is_one_switch_away(monkeypatch):
    """tests/conftest.py's coarse clock (AP_TEST_OLD_WINDOWS_CLOCK, B-2997): off until it passes as CI does."""
    asked = []
    monkeypatch.setattr(check.subprocess, "run", lambda cmd, **kw: asked.append(kw["env"]) or None)
    check.pytest(["tests/coding/test_tax.py"])
    monkeypatch.setattr(check, "OLD_WINDOWS_CLOCK", True)
    check.pytest(["tests/coding/test_tax.py"])
    assert asked[0] is None and asked[1]["AP_TEST_OLD_WINDOWS_CLOCK"] == "1"


def test_a_changed_function_near_the_length_limit_gets_a_warning(monkeypatch, capsys):
    from tests.meta.test_function_length import LIMIT

    assert LIMIT - 15 < check.NEAR < LIMIT and 900 < check.NEAR_PART < 1000
    monkeypatch.setattr(check, "changed_lines", lambda f: None)  # (all of it new)
    warned = check.near_limits(["ap_coder/bench/__main__.py", "ap_coder/core/store/summary.py", "docs/BUGS.md"])
    assert any(w.startswith("ap_coder/bench/__main__.py::main: ") for w in warned)  # (265 lines)
    assert any(w.startswith("ap_coder/core/store/summary.py: ") for w in warned)  # (997 lines)
    monkeypatch.setattr(check, "changed_lines", lambda f: ({1}, set()))  # the docstring only: no function changed
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
