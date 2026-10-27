"""The CI workflow is one GitHub accepts: a file it rejects runs no job at all, so nothing is tested."""

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
needs_ci = pytest.mark.skipif(not CI.is_file(), reason="no .github/workflows/ci.yml here (an offline bundle)")


def test_the_workflow_tests_skip_where_there_is_no_workflow(tmp_path):
    """B-171: the offline bundle leaves .github out (scripts/build_offline_bundle.py), so ``--installer --full-tests``
    from a bundle failed these tests: each one that reads ci.yml skips when it isn't there."""
    copy = tmp_path / "tests" / "meta"  # as deep as the real one
    copy.mkdir(parents=True)
    shutil.copy(__file__, copy / "test_ci_workflow.py")
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-k",
                          "not looked_up_once and not where_there_is_no_workflow and not one_shard", str(copy)],
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
def test_ci_runs_the_browser_tests_and_never_lets_them_skip():
    """The tests that open the dashboard in Chromium skip on a computer without it, and CI had none: they passed by
    never running. A CI job installs Chromium and runs them with AP_REQUIRE_BROWSER=1, where a missing browser fails."""
    jobs = yaml.safe_load(CI.read_text(encoding="utf-8"))["jobs"]
    job = jobs["browser-tests"]
    text = yaml.safe_dump(job)
    assert "playwright install --with-deps chromium" in text and "-k in_a_browser" in text.replace('"', "")
    assert any((step.get("env") or {}).get("AP_REQUIRE_BROWSER") == "1" for step in job["steps"])


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
