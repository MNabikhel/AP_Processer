"""The CI workflow is one GitHub accepts: a file it rejects runs no job at all, so nothing is tested."""

import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

WORKFLOWS = sorted((Path(__file__).resolve().parent.parent / ".github" / "workflows").glob("*.yml"))


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


CI = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"
# A run of an install / start script the app no longer ships: by path ("$folder/install.sh"), or as the command.
# A check that one is gone ([ ! -e "$F/install.sh" ]) is not a run.
RETIRED_RUN = re.compile(r"^(?!.*\[ ! -e ).*(/(install|start|terminal)\.(sh|bat)\b|(cmd\s+/c\s+\"|run:\s*|^\s*)"
                         r"(install|start|terminal)\.(sh|bat)\b)", re.MULTILINE)  # fmt: skip


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

    from . import conftest

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
