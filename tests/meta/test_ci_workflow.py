"""The CI workflow is one GitHub accepts: a file it rejects runs no job at all, so nothing is tested."""

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOWS = sorted((Path(__file__).resolve().parents[2] / ".github" / "workflows").glob("*.yml"))


def _env_blocks(node, where="workflow"):
    """Every ``env:`` mapping in the workflow (top level, jobs, steps), with where it is."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "env" and isinstance(value, dict):
                yield where, value
            else:
                yield from _env_blocks(value, f"{where}.{key}")
    elif isinstance(node, list):
        for number, item in enumerate(node):
            yield from _env_blocks(item, f"{where}[{number}]")


@pytest.mark.parametrize("workflow", WORKFLOWS, ids=lambda p: p.name)
def test_env_names_are_unique_ignoring_case(workflow):
    """GitHub reads env names without case: HTTP_PROXY and http_proxy in one env block reject the whole file."""
    for where, env in _env_blocks(yaml.safe_load(workflow.read_text(encoding="utf-8"))):
        lowered = [name.lower() for name in env]
        duplicates = sorted({name for name in lowered if lowered.count(name) > 1})
        assert not duplicates, f"{workflow.name} {where}: {duplicates}"


CI = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"
# A run of an install / start script the app no longer ships: by path ("$folder/install.sh"), or as the command.
# A check that one is gone ([ ! -e "$F/install.sh" ]) is not a run.
RETIRED_RUN = re.compile(r"^(?!.*\[ ! -e ).*(/(install|start|terminal)\.(sh|bat)\b|(cmd\s+/c\s+\"|run:\s*|^\s*)"
                         r"(install|start|terminal)\.(sh|bat)\b)", re.MULTILINE)  # fmt: skip
# The offline bundle leaves .github out (scripts/build_offline_bundle.py): there these tests have nothing to read.
CELLS = CI.parent.parent / "ci-tests.json"  # the test matrix's cells (the plan job picks them, D-260)


def test_cells() -> list[dict]:
    """Every cell of the test matrix, as main runs them."""
    return json.loads(CELLS.read_text(encoding="utf-8"))


test_cells.__test__ = False  # (a helper, not a test)


needs_ci = pytest.mark.skipif(not CI.is_file(), reason="no .github/workflows/ci.yml here (an offline bundle)")


def test_the_workflow_tests_skip_where_there_is_no_workflow(tmp_path):
    """B-171: the offline bundle leaves .github out (scripts/build_offline_bundle.py), so ``--installer --full-tests``
    from a bundle failed these tests: each one that reads ci.yml skips when it isn't there."""
    copy = tmp_path / "tests" / "meta"  # as deep as the real one
    copy.mkdir(parents=True)
    shutil.copy(__file__, copy / "test_ci_workflow.py")
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-k",
                          "not looked_up_once and not where_there_is_no_workflow and not one_shard"
                          " and not older_versions and not weighed_by and not times_are_refreshed", str(copy)],
                         cwd=tmp_path, capture_output=True, text=True, encoding="utf-8")  # fmt: skip
    assert out.returncode == 0, out.stdout[-2000:]


@needs_ci
def test_ci_runs_the_one_launcher_per_computer():
    """The app folder has one file to open per computer: CI runs the installer as APProcessor.bat --installer /
    APProcessor.command --installer, never the install / start scripts older versions shipped (only
    scripts/ci/old_versions.py runs those, inside an old version's own folder)."""
    text = CI.read_text(encoding="utf-8")
    assert not RETIRED_RUN.findall(text)
    assert "APProcessor.bat --installer --yes" in text and 'APProcessor.command" --installer' in text
    jobs = yaml.safe_load(text)["jobs"]
    assert "mac-pilot-update" in jobs and jobs["mac-pilot-update"]["name"].startswith("The pilot tester's Mac")


def test_the_retired_run_check_catches_what_it_should():
    for line in ('offline "$folder/install.sh" --yes', "        run: install.bat --yes", 'cmd /c "start.bat"'):
        assert RETIRED_RUN.search(line), line
    for line in (
        "for old in install.sh start.sh; do",
        'offline "$folder/APProcessor.command" --installer',
        '[ ! -e "$F/install.sh" ] || fail "still there"',
    ):
        assert not RETIRED_RUN.search(line), line


@needs_ci
def test_every_ci_job_has_a_time_limit():
    """A job without ``timeout-minutes`` that hangs (a test waiting for good, a server that never answers) holds its
    runner for GitHub's 6 hours (D-191): each has a limit of about twice its time in a passing run."""
    jobs = yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]
    assert {name for name, job in jobs.items() if not 0 < int(job.get("timeout-minutes") or 0) <= 60} == set()


@needs_ci
def test_ci_runs_the_browser_tests_and_never_lets_them_skip():
    """The tests that open the dashboard in Chromium skip on a computer without it, and CI had none: they passed by
    never running. A CI job installs Chromium and runs them with AP_REQUIRE_BROWSER=1, where a missing browser fails."""
    jobs = yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]
    job = jobs["browser-tests"]
    text = yaml.safe_dump(job)
    assert "playwright install --with-deps chromium" in text and "-k in_a_browser" in text.replace('"', "")
    assert any((step.get("env") or {}).get("AP_REQUIRE_BROWSER") == "1" for step in job["steps"])


@needs_ci
def test_ci_runs_the_pilot_laptops_python_on_windows_with_ocr_and_the_launcher():
    """B-2105: the pilot laptop is Windows with Python 3.14 alone. CI ran 3.14 on Windows without the OCR packages (the
    tests that read scans skipped) and every Windows launcher job on 3.12: the laptop's own combination ran nowhere.
    The Windows 3.14 test shards install the OCR extra as the 3.12 ones do, and the pilot launcher job runs
    APProcessor.bat on 3.12 and on 3.14, where 3.14 is the only Python the launcher finds."""
    jobs = yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]
    cells = [c for c in test_cells() if c["os"] == "windows-latest"]
    whole = [c for c in cells if c.get("tier") != "fast"]  # (the fast tier is split into parts too, D-233)
    assert {(c["python"], c.get("extras")) for c in whole} == {("3.12", "ocr,dev"), ("3.14", "ocr,dev")}
    job = jobs["windows-pilot-launcher"]
    assert job["strategy"]["matrix"]["python"] == ["3.12", "3.14"] and job["strategy"]["fail-fast"] is False
    assert "Python ${{ matrix.python }}" in job["name"]  # each cell says which Python it is
    setup = next(step for step in job["steps"] if "setup-python" in step.get("uses", ""))
    assert setup["with"]["python-version"] == "${{ matrix.python }}"
    text = yaml.safe_dump(job)
    assert "Python 3\\.12" not in text  # what the window must print follows the cell's Python
    alone = next(step for step in job["steps"] if "the only Python" in step.get("name", ""))
    assert alone["if"] == "matrix.python != '3.12'" and "PythonCore" in alone["run"]
    assert "fetch_models.py --check" in text and "pilot_check.py" in text  # OCR models there, the samples read


def test_the_browser_is_looked_up_once_not_inside_a_running_playwright(monkeypatch, tmp_path):
    """B-127: CI has no browser under /opt/pw-browsers, so chromium_path() asks Playwright for its own Chromium. The
    browser tests call it again inside ``with sync_playwright()``, where a second Playwright can't start ("Sync API
    inside the asyncio loop"): every browser test failed in CI and none locally, where the first lookup finds one."""
    import importlib.util

    import pytest

    if importlib.util.find_spec("playwright") is None:
        pytest.skip("Playwright isn't installed")
    from playwright.sync_api import sync_playwright

    from tests import conftest

    monkeypatch.setattr(conftest, "BROWSER_ROOTS", [str(tmp_path)])
    monkeypatch.delenv("PLAYWRIGHT_BROWSERS_PATH", raising=False)
    monkeypatch.delenv("AP_REQUIRE_BROWSER", raising=False)
    conftest.chromium_path.cache_clear()
    try:
        first = conftest.chromium_path()  # as the fixtures do, before starting Playwright
        with sync_playwright():
            assert conftest.chromium_path() == first
    finally:
        conftest.chromium_path.cache_clear()


@needs_ci
def test_ci_runs_github_zip_as_the_pilot_tester_does():
    """M5: no job ran GitHub's ZIP the way the pilot tester does: every Windows job set AP_ALLOW_INTERNET=1 or used the
    offline bundle, from a plain folder, with nothing marked as downloaded, and no job updated one ZIP with a newer
    one. This one does: a git archive marked with the Mark of the Web, Expand-Archive into a path with a space and an
    accent, no opt-in to the internet, then a newer ZIP of the same version unzipped over it."""
    jobs = yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]
    job = jobs["windows-github-zip"]
    assert job["runs-on"].startswith("windows")
    assert not any("AP_ALLOW_INTERNET" in env for _where, env in _env_blocks(job))
    text = "\n".join(str(step.get("run") or "") for step in job["steps"])
    for needed in ("git archive", "Expand-Archive", "Zone.Identifier", "Pilot tester Ré", "APProcessor.bat",
                   "Updated AP Coder from", "A newer AP Coder took over", "kept.pdf"):  # fmt: skip
        assert needed in text, needed


@needs_ci
def test_ci_runs_the_prototype_guides_first_day_on_windows_through_the_launcher():
    """D-225: docs/PROTOTYPE.md is the pilot tester's guide on a Windows laptop. A job follows it there, set up by the
    installer and started as the AP Coder shortcut starts it (APProcessor.bat), its steps in scripts/ci/first_day.py."""
    job = yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]["windows-first-day"]
    assert job["runs-on"].startswith("windows") and 0 < int(job["timeout-minutes"]) <= 20
    text = "\n".join(str(step.get("run") or "") for step in job["steps"])
    assert "APProcessor.bat --installer --yes" in text
    assert "scripts\\ci\\first_day.py" in text and "--start launcher" in text


def test_the_update_check_finds_the_schema_the_new_version_writes():
    """old_versions.py reads the new version's SCHEMA_VERSION from its source: the store became a package (D-69) and
    the check read a file that is no longer there (B-831)."""
    scripts = Path(__file__).resolve().parents[2] / "scripts" / "ci"
    if not (scripts / "old_versions.py").is_file():
        pytest.skip("scripts/ci is not in this copy (an offline bundle)")
    sys.path.insert(0, str(scripts))
    try:
        import old_versions
    finally:
        sys.path.pop(0)
    from ap_coder.core.store import SCHEMA_VERSION

    assert old_versions.schema_of(Path(__file__).resolve().parents[2]) == str(SCHEMA_VERSION)


def test_the_update_check_works_out_the_authenticators_code_as_ap_coder_does():
    """old_versions.py signs in after the update (D-243) with the standard library alone: its code is AP Coder's."""
    scripts = Path(__file__).resolve().parents[2] / "scripts" / "ci"
    if not (scripts / "old_versions.py").is_file():
        pytest.skip("scripts/ci is not in this copy (an offline bundle)")
    sys.path.insert(0, str(scripts))
    try:
        import old_versions
    finally:
        sys.path.pop(0)
    from ap_coder.core import authenticator

    secret = authenticator.new_secret()
    for now in (0.0, 59.0, 1_800_000_000.0, 1_800_000_029.9):
        assert old_versions._totp(secret, now) == authenticator.code(secret, now)


@needs_ci
def test_every_cells_parts_are_all_there_and_the_test_windows_leaves_out_runs_on_main():
    """D-233: each cell of the test matrix (an OS, a Python, the whole suite or the fast tier) is split into parts
    K/N: every K from 1 to N is there once, or tests would run nowhere. The one test the Windows cells leave out
    (the JD Edwards sync at the company's size, 20 minutes there) is run by windows-sync-volume, on both Windows
    Pythons of the whole suite, whenever the run is not a pull request's; Ubuntu's cells leave nothing out."""
    workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    parts: dict[tuple, list[str]] = {}
    for cell in test_cells():
        parts.setdefault((cell["os"], cell["python"], cell.get("tier")), []).append(cell["shard"])
    assert len(parts) == 8
    for cell, shards in parts.items():
        n = int(shards[0].split("/")[1])
        assert sorted(shards) == [f"{k}/{n}" for k in range(1, n + 1)], cell
    volume = workflow["env"]["SYNC_VOLUME"]
    path, name = volume.split("::")
    assert f"def {name}(" in (CI.parents[2] / path).read_text(encoding="utf-8")
    run = next(step["run"] for step in jobs["tests"]["steps"] if step.get("name") == "Tests")
    assert "matrix.os == 'windows-latest' && format('--deselect {0}', env.SYNC_VOLUME) || ''" in run
    assert run.count("deselect") == 1 and " -k " not in run
    job = jobs["windows-sync-volume"]
    assert job["if"] == "github.event_name != 'pull_request'" and job["runs-on"] == "windows-latest"
    assert job["strategy"]["matrix"]["python"] == ["3.12", "3.14"]
    assert 'pytest -q -m "" -o faulthandler_timeout=2400 ${{ env.SYNC_VOLUME }}' in job["steps"][-1]["run"]  # B-2727


FLAKY = """
from pathlib import Path


def test_always_fails():
    assert False


def test_fails_the_first_time_only():
    ran = Path(__file__).with_name("ran-once")
    if not ran.exists():
        ran.write_text("")
        assert False


def test_passes():
    pass
"""


@needs_ci
def test_after_failed_tests_each_runs_again_and_the_summary_says_if_it_passed_then(tmp_path):
    """A red run did not say whether a failure was a flake, and each needed a whole job run again (20 minutes or
    more). In the jobs of tests, a step after a failed Tests step runs the failed tests once more and says of each, in
    the run's summary and as an annotation, "passed when run again (likely flaky)" or "failed again"; the job stays red
    either way (no step may hide a failure). Here its script runs on a failing test, a flaky one and one that passes."""
    jobs = yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]
    scripts = set()
    for name in ("tests", "browser-tests"):
        steps = jobs[name]["steps"]
        tests = next(n for n, step in enumerate(steps) if step.get("id") == "tests")
        again = steps[tests + 1]
        assert again["if"] == "failure() && steps.tests.outcome == 'failure'" and "continue-on-error" not in again
        assert steps[tests].get("env") == again.get("env") and "continue-on-error" not in steps[tests], name
        scripts.add(again["run"])
    assert len(scripts) == 1  # (the same in both jobs)
    script = scripts.pop().split("<<'EOF'\n", 1)[1].rsplit("EOF", 1)[0]
    (tmp_path / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (tmp_path / "test_flaky.py").write_text(FLAKY, encoding="utf-8")
    summary = tmp_path / "summary.md"
    env = {**os.environ, "GITHUB_STEP_SUMMARY": str(summary)}
    first = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=tmp_path, env=env,
                           capture_output=True, text=True, timeout=120)  # fmt: skip
    assert first.returncode == 1 and "2 failed, 1 passed" in first.stdout, first.stdout
    done = subprocess.run([sys.executable, "-c", script], cwd=tmp_path, env=env, capture_output=True, text=True,
                          timeout=120)  # fmt: skip
    assert done.returncode == 0, done.stderr
    assert summary.read_text(encoding="utf-8").splitlines() == [
        "### The failed tests, run again",
        "",
        "- failed again: `test_flaky.py::test_always_fails`",
        "- passed when run again (likely flaky): `test_flaky.py::test_fails_the_first_time_only`",
    ]
    assert "::error title=failed again::test_flaky.py::test_always_fails" in done.stdout
    assert "::warning title=passed when run again (likely flaky)::test_flaky.py::test_fails_the_" in done.stdout
    assert "1 failed, 1 passed" in done.stdout  # (the two failed ones alone ran again)


def test_every_test_runs_in_exactly_one_shard():
    """CI splits the Windows run with --shard K/N (tests/conftest.py): whole files, each in one part, none left out."""
    from tests.conftest import _shard

    class item:  # the two things _shard reads off a pytest item
        def __init__(self, nodeid, slow=False):
            self.nodeid, self.slow = nodeid, slow

        def get_closest_marker(self, name):
            return self.slow or None

    items = [item(f"tests/test_{f}.py::test_{t}", slow=t % 3 == 0) for f in range(9) for t in range(f + 2)]
    parts = [_shard(items, f"{k}/3") for k in (1, 2, 3)]
    assert sum(len(p) for p in parts) == len(items) and set().union(*parts) == set(items)
    for part in parts:
        files = {i.nodeid.split("::")[0] for i in part}
        assert all(i in part for i in items if i.nodeid.split("::")[0] in files)  # a file is never split
    with pytest.raises(pytest.UsageError):
        _shard(items, "4/3")


def test_the_parts_are_weighed_by_the_tests_each_run_really_runs(tmp_path, monkeypatch):
    """D-233: the split weighed every file by all its tests, also for the fast tier (which runs no slow test) and for
    a run that leaves a test out (--deselect): a file of one 20-minute slow test filled a part that then ran nothing.
    A file's weight is its measured time for the tests this run runs (tests/test_times.json: all, and not slow), and
    tests/times.py works both out of a ``--junitxml`` report."""
    from tests import conftest, times

    class item:
        def __init__(self, nodeid, slow=False):
            self.nodeid, self.slow = nodeid, slow

        def get_closest_marker(self, name):
            return self.slow or None

    report = tmp_path / "times.xml"
    report.write_text(
        '<testsuites><testsuite>'
        '<testcase classname="tests.meta.test_ci_workflow" name="test_big" time="100.0"/>'
        '<testcase classname="tests.meta.test_ci_workflow" name="test_small[a]" time="3.0"/>'
        '<testcase classname="tests.meta.test_ci_workflow.TestGroup" name="test_inside" time="2.0"/>'
        '<testcase classname="tests.conftest" name="test_in_a_browser_page" time="7.0"/>'
        '<testcase classname="tests.webclient" name="test_quick" time="40.0"/>'
        '<testcase classname="tests.pdfs" name="test_other" time="60.0"/>'
        '<testcase classname="tests.times" name="test_alone" time="500.0"/>'
        "</testsuite></testsuites>", encoding="utf-8")  # fmt: skip
    slow = {"tests/meta/test_ci_workflow.py::test_big", "tests/times.py::test_alone"}
    measured = times.times(report, slow)
    assert measured == {"tests/conftest.py": [7.0, 0.0], "tests/meta/test_ci_workflow.py": [105.0, 5.0],
                        "tests/pdfs.py": [60.0, 60.0], "tests/times.py": [500.0, 0.0],
                        "tests/webclient.py": [40.0, 40.0]}  # fmt: skip
    monkeypatch.setattr(conftest, "_TIMES", measured)
    big, small = item("tests/meta/test_ci_workflow.py::test_big", slow=True), item("tests/meta/test_ci_workflow.py::t")
    quick, other = item("tests/webclient.py::test_quick"), item("tests/pdfs.py::test_other")
    alone = item("tests/times.py::test_alone", slow=True)
    items = [big, small, quick, other, alone]
    assert conftest._shard(items, "1/2") == {alone}  # all of it: 500 s against 105 + 60 + 40
    # the fast tier runs no slow test: 60 s against 40 + 5 (a file's slow tests go where it goes: -m leaves them out)
    assert conftest._shard(items, "1/2", fast=True) == {other}
    assert conftest._shard(items, "2/2", fast=True) == {quick, small, big, alone}
    # a test left out weighs nothing: 105 s against 60 + 40
    assert conftest._shard(items, "1/2", left_out=("tests/times.py::test_alone",)) == {big, small}
    whole = json.loads((Path(conftest.__file__).parent / "test_times.json").read_text(encoding="utf-8"))
    assert all(len(pair) == 2 and pair[0] >= pair[1] >= 0 for pair in whole.values())
    assert all((Path(conftest.__file__).parents[1] / path).is_file() for path in whole)  # no file that is gone


def test_the_times_are_refreshed_from_several_reports_and_a_test_over_two_seconds_goes_on_the_slow_list(tmp_path):
    """tests/times.py: the files the reports measured get their times, the others keep theirs and a file that is gone
    leaves (a folder at a time, or CI's parts, add up); ``--slow-over 2`` puts each default-run test over 2 s on
    tests/slow_tests.txt, in order under its comment, as its rule says (one at 16.5 s had stayed in the default run)."""
    from tests import times

    for name in ("test_new", "test_kept"):
        (tmp_path / "tests" / "a").mkdir(parents=True, exist_ok=True)
        (tmp_path / "tests" / "a" / f"{name}.py").write_text("", encoding="utf-8")
    report = tmp_path / "part.xml"
    report.write_text(
        "<testsuites><testsuite>"
        '<testcase classname="tests.a.test_new" name="test_waits[one]" time="3.5"/>'
        '<testcase classname="tests.a.test_new" name="test_waits[two]" time="0.5"/>'
        '<testcase classname="tests.a.test_new" name="test_quick" time="1.0"/>'
        '<testcase classname="tests.a.test_new" name="test_in_a_browser_page" time="9.0"/>'
        '<testcase classname="tests.a.test_new" name="test_listed" time="6.0"/>'
        "</testsuite></testsuites>", encoding="utf-8")  # fmt: skip
    listed = tmp_path / "slow_tests.txt"
    listed.write_text("# the rule\ntests/z.py::test_last\ntests/a/test_new.py::test_listed\n", encoding="utf-8")
    kept = tmp_path / "test_times.json"
    kept.write_text(json.dumps({"tests/a/test_kept.py": [4.0, 1.0], "tests/a/test_gone.py": [9.0, 9.0]}))
    slow = {"tests/z.py::test_last", "tests/a/test_new.py::test_listed"}
    new, measured = times.refresh([report], slow, 2.0, tmp_path, kept, listed)
    assert new == {"tests/a/test_new.py::test_waits"}  # (all its parameters: the list names a test without them)
    assert listed.read_text(encoding="utf-8").splitlines() == [
        "# the rule",
        "tests/a/test_new.py::test_listed",
        "tests/a/test_new.py::test_waits",
        "tests/z.py::test_last",
    ]
    assert measured == {"tests/a/test_kept.py": [4.0, 1.0], "tests/a/test_new.py": [20.0, 1.0]}
    assert json.loads(kept.read_text(encoding="utf-8")) == measured
    assert times.refresh([report], slow | new, None, tmp_path, kept, listed) == (set(), measured)  # (nothing more)
    # a test marked slow in its own file is slow too, though the report doesn't say so
    marked = "import pytest\n\n\n@pytest.mark.slow\ndef test_long():\n    pass\n\n\ndef test_short():\n    pass\n\n\n"
    marked += "class TestGroup:\n    @pytest.mark.slow\n    def test_inside(self):\n        pass\n"
    (tmp_path / "tests" / "a" / "test_kept.py").write_text(marked, encoding="utf-8")
    (tmp_path / "tests" / "a" / "test_new.py").write_text("import pytest\n\npytestmark = pytest.mark.slow\n\n\n"
                                                          "def test_any():\n    pass\n", encoding="utf-8")  # fmt: skip
    assert times.marked_slow(tmp_path) == {"tests/a/test_kept.py::test_long", "tests/a/test_kept.py::TestGroup::"
                                           "test_inside", "tests/a/test_new.py::test_any"}  # fmt: skip


def test_an_older_versions_test_file_left_in_tests_is_not_run(tmp_path):
    """B-1411: the tests live in folders since D-116. Unzipping a newer GitHub ZIP over an older copy (no manifest, so
    nothing is pruned) left the older version's tests/test_*.py, and pytest ran them against the new code."""
    from tests import conftest

    tests = tmp_path / "tests"
    (tests / "core").mkdir(parents=True)
    (tests / "conftest.py").write_text(f"collect_ignore_glob = {conftest.collect_ignore_glob!r}\n", encoding="utf-8")
    (tests / "test_left_by_an_older_version.py").write_text("def test_old():\n    assert False\n", encoding="utf-8")
    (tests / "core" / "test_this_version.py").write_text("def test_new():\n    assert True\n", encoding="utf-8")
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "tests"],
                         cwd=tmp_path, capture_output=True, text=True, encoding="utf-8")  # fmt: skip
    assert out.returncode == 0 and "1 passed" in out.stdout, out.stdout[-2000:]


@needs_ci
def test_the_release_zip_covers_every_python_the_guides_promise():
    """B-1524: the pilot release's zip was built for Python 3.12 to 3.14 while PILOT.md, GETTING_STARTED.md and
    KNOWN_LIMITS.md say any Python 3.11 or newer works (D-55: 3.11 is the floor everywhere): on a laptop with 3.11 the
    first start stopped, offline, until IT installed another Python."""
    import re
    from pathlib import Path

    from scripts.build_offline_bundle import DEFAULT_PYTHONS

    root = Path(__file__).resolve().parents[2]
    text = (root / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    built = re.search(r"build_offline_bundle\.py[^\n]*--python ((?:3\.\d+ ?)+)", text)
    assert built and built.group(1).split() == DEFAULT_PYTHONS
    assert DEFAULT_PYTHONS[0] == "3.11"


@needs_ci
def test_the_nightly_a_run_by_hand_and_a_merge_never_cancel_each_other():
    """D-330: a merge, the nightly and a run by hand all run on main's ref, and the runs of one group cancel each other:
    grouped by the ref alone, a merge's run (run again) cancelled the nightly. The group names the kind of run too, so
    a newer run of one kind still cancels the older on its ref (a pull request's next push, the next merge)."""
    workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
    on = workflow.get("on") or workflow.get(True)
    concurrency = workflow["concurrency"]
    assert concurrency["cancel-in-progress"] is True
    group = concurrency["group"]
    assert re.findall(r"\$\{\{ *([^}]*?) *\}\}", group) == ["github.event_name", "github.ref"]
    main = "refs/heads/main"
    runs = {"push": main, "schedule": main, "workflow_dispatch": main, "pull_request": "refs/pull/1/merge"}
    assert set(runs) == set(on)  # (each kind of run the workflow has, on the ref it runs on)
    named = {
        group.replace("${{ github.event_name }}", kind).replace("${{ github.ref }}", ref) for kind, ref in runs.items()
    }
    assert len(named) == len(runs)
    assert all("concurrency" not in job for job in workflow["jobs"].values())


@needs_ci
def test_a_pull_request_runs_the_suite_on_ubuntu_and_the_pilot_laptops_windows_and_main_runs_everything():
    """D-260: a pull request runs the whole suite three times (Ubuntu 3.12, and Windows 3.14 with OCR: the pilot
    laptop's; D-330: Windows 3.12 with OCR too, as Windows before Python 3.13 behaves otherwise), every part of each,
    and the Windows launcher jobs on 3.14 alone; Mac jobs and the other Pythons run on main (each merge, nightly),
    where a build is released only once all of them passed (.github/workflows/builds.yml)."""
    workflow = yaml.safe_load(CI.read_text(encoding="utf-8"))
    jobs, on = workflow["jobs"], workflow.get("on") or workflow.get(True)
    assert on["push"] == {"branches": ["main"]} and on["schedule"] and "pull_request" in on
    assert jobs["tests"]["needs"] == "plan"
    assert jobs["tests"]["strategy"]["matrix"]["include"] == "${{ fromJSON(needs.plan.outputs.tests) }}"
    pick = jobs["plan"]["steps"][-1]["run"]
    assert "select(.pull_request)" in pick and 'github.event_name }}" = pull_request' in pick
    picked = [c for c in test_cells() if c.get("pull_request")]
    shards: dict[tuple, list[str]] = {}
    for cell in picked:
        shards.setdefault((cell["os"], cell["python"], cell.get("extras")), []).append(cell["shard"])
    assert set(shards) == {
        ("ubuntu-latest", "3.12", None),
        ("windows-latest", "3.12", "ocr,dev"),
        ("windows-latest", "3.14", "ocr,dev"),
    }
    for key, parts in shards.items():  # every part of the cell, or tests would run nowhere on a pull request
        n = int(parts[0].split("/")[1])
        assert sorted(parts) == [f"{k}/{n}" for k in range(1, n + 1)], key
    assert all(c.get("tier") != "fast" for c in picked)
    for name, job in jobs.items():
        if "macos" in str(job.get("runs-on")):
            assert job["if"] == "github.event_name != 'pull_request'", name
    trimmed = "${{ fromJSON(github.event_name == 'pull_request' && '[{\"python\":\"3.12\"}]' || '[]') }}"
    for name in ("windows-pilot-launcher", "windows-github-zip", "windows-share", "windows-first-day"):
        assert jobs[name]["strategy"]["matrix"]["exclude"] == trimmed, name
    no_mac = "${{ fromJSON(github.event_name == 'pull_request' && '[{\"os\":\"macos-latest\"}]' || '[]') }}"
    assert jobs["update-over-older-install"]["strategy"]["matrix"]["exclude"] == no_mac
