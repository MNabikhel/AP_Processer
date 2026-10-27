"""The update over every older version a user may have, for real (scripts/ci/old_versions.py).

Each old version (main after each merged pull request) is installed the way its README said, in a scratch home
(it downloads its packages from PyPI), used (demo invoices, an approval with a correction, a vendor taught, an export
batch, settings) and left running; then the new offline bundle is unzipped into a new folder, or over the old one,
and double-clicked with no internet. Slow (minutes per version): run only with ``AP_UPGRADE_TESTS=1``:

    AP_UPGRADE_TESTS=1 AP_UPGRADE_BUNDLE=dist/APProcessor-offline-....zip python -m pytest -m upgrade -v

``AP_UPGRADE_BUNDLE`` is the new version's offline bundle (built with ``scripts/build_offline_bundle.py --python
3.12``, which also holds this computer's packages); ``AP_UPGRADE_ONLY=e1cf61a,db95511`` limits the versions.
The fast tests below check the tool itself.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("ap_ci_old_versions", ROOT / "scripts" / "ci" / "old_versions.py")
old_versions = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = old_versions
_spec.loader.exec_module(old_versions)

WANTED = os.environ.get("AP_UPGRADE_TESTS") == "1"
ONLY = {s.strip() for s in os.environ.get("AP_UPGRADE_ONLY", "").split(",") if s.strip()}
# Each version as each README had it installed, its dashboard left running: unzipped into a new folder, and over it.
# Then the less kind cases: closed before the update (found next to the new folder), two old windows open (8501 and
# 8502), and a .venv made with Python 3.10 (PR #1 and #2 allowed it; AP_UPGRADE_PYTHON310 names one).
RUNS = [(v["sha"], v["label"], how, where, ()) for v in old_versions.VERSIONS for how in v["how"]
        for where in ("new-folder", "over")]  # fmt: skip
RUNS += [("db95511", "PR #1 closed", "by-hand", "beside", ("--windows", "0")),
         ("e1cf61a", "PR #8 closed", "launcher", "beside", ("--windows", "0")),
         ("b968b74", "PR #2 two windows", "installer", "new-folder", ("--windows", "2"))]  # fmt: skip
if os.environ.get("AP_UPGRADE_PYTHON310"):
    RUNS.append(
        ("b968b74", "PR #2 Python 3.10", "by-hand", "over", ("--old-python", os.environ["AP_UPGRADE_PYTHON310"]))
    )
CASES = [
    pytest.param(sha, how, where, extra, id=f"{sha}-{label.replace(' ', '-').replace('#', '')}-{how}-{where}")
    for sha, label, how, where, extra in RUNS
    if not ONLY or sha in ONLY
]


@pytest.mark.slow
@pytest.mark.upgrade
@pytest.mark.skipif(not WANTED, reason="installs each old version from PyPI: set AP_UPGRADE_TESTS=1")
@pytest.mark.skipif(os.name == "nt", reason="Windows runs it step by step in CI (.github/workflows/ci.yml)")
@pytest.mark.parametrize(("sha", "how", "where", "extra"), CASES)
def test_the_update_over_an_old_version(sha, how, where, extra, tmp_path):
    bundle = Path(os.environ.get("AP_UPGRADE_BUNDLE", ""))
    if not bundle.is_file():
        pytest.fail("AP_UPGRADE_BUNDLE must name the new version's offline bundle (ZIP)")
    args = ["scenario", sha, "--how", how, "--where", where, "--bundle", str(bundle), "--work", str(tmp_path), *extra]
    if sys.platform.startswith("linux") and shutil.which("strace"):
        args.append("--strace")
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "ci" / "old_versions.py"), *args])
    assert result.returncode == 0, f"the update over {sha} ({how}, {where}) failed: see the output above"


# --- The tool itself (fast) -----------------------------------------------------------------------------------


def test_every_version_on_main_is_listed():
    """Each merge into main (and the first commit) is a version someone may have: none is left out."""
    try:
        merges = subprocess.run(["git", "log", "--merges", "--first-parent", "--format=%h", "-n", "200", "HEAD"],
                                cwd=ROOT, capture_output=True, text=True, check=True).stdout.split()  # fmt: skip
        first = subprocess.run(["git", "rev-list", "--max-parents=0", "--abbrev-commit", "HEAD"], cwd=ROOT,
                               capture_output=True, text=True, check=True).stdout.split()  # fmt: skip
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("no git history here (an offline bundle)")
    listed = [v["sha"] for v in old_versions.VERSIONS]
    if not merges or not any(m.startswith(s) or s.startswith(m) for m in merges for s in listed):
        pytest.skip("a shallow clone: the old versions are not in it")
    new = {m for m in merges if subprocess.run(["git", "show", f"{m}:pyproject.toml"], cwd=ROOT, capture_output=True,
                                               text=True).stdout.count('version = "0.1.0"')}  # fmt: skip
    for sha in [*new, *first]:
        assert any(sha.startswith(s) or s.startswith(sha) for s in listed), f"{sha} is missing from VERSIONS"


def test_connections_leaving_the_computer_are_found_in_an_strace_log(tmp_path):
    trace = tmp_path / "connect.trace"
    trace.write_text(
        '12 connect(3, {sa_family=AF_UNIX, sun_path="/run/x"}, 110) = 0\n'
        '12 connect(4, {sa_family=AF_INET, sin_port=htons(8601), sin_addr=inet_addr("127.0.0.1")}, 16) = 0\n'
        "13 connect(5, {sa_family=AF_INET6, sin6_port=htons(8601), sin6_flowinfo=htonl(0), "
        'inet_pton(AF_INET6, "::1", &sin6_addr), sin6_scope_id=0}, 28) = -1 ECONNREFUSED\n'
        '14 connect(6, {sa_family=AF_INET, sin_port=htons(53), sin_addr=inet_addr("10.0.0.2")}, 16) = 0\n'
    )
    leaving = old_versions.connects(trace)
    assert len(leaving) == 1 and "10.0.0.2" in leaving[0]


def test_the_old_versions_person_has_none_of_this_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://proxy:3128")
    monkeypatch.setenv("AP_PRIVATE_DIR", "/somewhere")
    offline = old_versions.user_env(tmp_path, online=False)
    online = old_versions.user_env(tmp_path, online=True)
    assert offline["HOME"] == str(tmp_path) and "HTTPS_PROXY" not in offline and "AP_ALLOW_INTERNET" not in offline
    assert not any(k.startswith("AP_") and k != "AP_NO_PAUSE" for k in offline)
    assert online["HTTPS_PROXY"] == "http://proxy:3128" and online["AP_ALLOW_INTERNET"] == "1"
    assert "AP_PRIVATE_DIR" not in online
