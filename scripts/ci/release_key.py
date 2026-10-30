"""What the Release workflow's laptop check checks at a commit, as one key (D-130): two commits with the same key
check the same pilot release the same way, so a check that passed on one counts for the other.

    python scripts/ci/release_key.py [COMMIT]   # prints the key (HEAD by default)

The key covers the newest pilot release in .github/release-tags.txt (its tag and commit: the zip is built from that
commit), the check job of .github/workflows/release.yml as it runs (its runner, time limit, environment and steps;
not its name, comments or when it runs) and tests/ops/test_demo_day.py (the run sheet it walks). Exits 1 when the
commit has no pilot release or no check.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TAGS, WORKFLOW, TEST = ".github/release-tags.txt", ".github/workflows/release.yml", "tests/ops/test_demo_day.py"
CHECK_JOB = "pilot-laptop"
_RUNS = ("runs-on", "timeout-minutes", "env", "steps")  # what the check does; its name, needs and if are not
_PILOT = re.compile(r"^(v\S+-pilot\d+)\s+(\S+)", re.MULTILINE)


def pilot(tags: str) -> str:
    """The newest pilot release (the last pilot line), as ``tag commit``; '' when there is none."""
    found = _PILOT.findall(tags)
    return " ".join(found[-1]) if found else ""


def check_job(workflow: str) -> dict | None:
    """The check job as it runs, or None when the workflow has none."""
    import yaml

    job = ((yaml.safe_load(workflow) or {}).get("jobs") or {}).get(CHECK_JOB)
    return {name: job.get(name) for name in _RUNS} if isinstance(job, dict) else None


def key(tags: str, workflow: str, test: str) -> str:
    """The key of these three files' contents; '' when there is no pilot release or no check to key."""
    release, job = pilot(tags), check_job(workflow)
    if not release or job is None:
        return ""
    parts = {"pilot": release, "check": job, "test": test}
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def _show(commit: str, path: str) -> str:
    done = subprocess.run(["git", "show", f"{commit}:{path}"], cwd=ROOT, capture_output=True, text=True)
    return done.stdout if done.returncode == 0 else ""


def key_at(commit: str = "HEAD") -> str:
    return key(_show(commit, TAGS), _show(commit, WORKFLOW), _show(commit, TEST))


if __name__ == "__main__":
    found = key_at(sys.argv[1] if len(sys.argv) > 1 else "HEAD")
    if not found:
        sys.exit(1)
    print(found)
