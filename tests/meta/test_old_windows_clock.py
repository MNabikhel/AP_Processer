"""``AP_TEST_OLD_WINDOWS_CLOCK=1`` (tests/conftest.py) runs the tests with Windows' clock before Python 3.13, so an edge
of the clock like B-2997's (found only on CI's Windows 3.12, after a merge) is met on any computer: the switch must
still make such a case fail, and leave it passing when it is off."""

import os
import subprocess
import sys

from tests.conftest import OLD_CLOCK_SAID, ROOT

# B-2997's edge: a pick-up "a minute later" by the clock found an email written just before it under a minute old,
# because Windows' clock (Python 3.12) gave the file a time a few milliseconds after time.time().
CASE = """
import time


def test_a_file_written_just_now_is_never_newer_than_the_clock(tmp_path):
    for n in range(50):
        path = tmp_path / f"{n}.eml"
        path.write_text("an email")
        assert path.stat().st_mtime <= time.time()
        time.sleep(0.005)  # (each try at another moment of the clock's step)
"""


def _run(folder, switch: str) -> subprocess.CompletedProcess:
    (folder / "test_edge.py").write_text(CASE, encoding="utf-8")
    env = {k: v for k, v in os.environ.items() if k != "AP_TEST_OLD_WINDOWS_CLOCK"}
    env.update({"AP_TEST_OLD_WINDOWS_CLOCK": switch} if switch else {})
    command = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "tests.conftest",
               "--rootdir", str(folder), "-c", str(folder / "none.ini"), str(folder / "test_edge.py")]  # fmt: skip
    (folder / "none.ini").write_text("[pytest]\n", encoding="utf-8")
    return subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)


def test_the_old_windows_clock_makes_a_file_written_just_now_newer_than_the_clock(tmp_path):
    on = _run(tmp_path, "1")
    assert on.returncode == 1 and "1 failed" in on.stdout, on.stdout + on.stderr
    assert OLD_CLOCK_SAID in on.stdout  # (a quiet run says it at the end: it has no header)
    if sys.platform == "win32" and sys.version_info < (3, 13):
        return  # (there the clock is that one already: off, the case fails too)
    off = _run(tmp_path, "")
    assert off.returncode == 0 and "1 passed" in off.stdout, off.stdout + off.stderr
    assert "clock:" not in off.stdout
