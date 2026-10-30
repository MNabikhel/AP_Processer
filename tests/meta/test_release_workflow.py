"""The Release workflow (.github/workflows/release.yml, D-129 and D-130): the laptop check runs once for a pilot
release; a later run with the same check of the same release reuses the zip that passed (scripts/ci/release_key.py).
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[2]
RELEASE = ROOT / ".github" / "workflows" / "release.yml"
_spec = importlib.util.spec_from_file_location("ap_ci_release_key", ROOT / "scripts" / "ci" / "release_key.py")
release_key = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = release_key
_spec.loader.exec_module(release_key)

TAGS = """# The release tags: tag, commit, title.
v0.3.0-pilot1 a1724b5 AP Coder 0.3.0, pilot release 1
v0.3.0-handoff1 230f629 The handoff
v0.3.0-pilot2 16a0d4c AP Coder 0.3.0, pilot release 2
"""
TEST = "def test_the_run_sheet():\n    pass\n"


def _key(tags=TAGS, workflow=None, test=TEST):
    return release_key.key(tags, RELEASE.read_text(encoding="utf-8") if workflow is None else workflow, test)


def test_the_key_is_the_newest_pilot_release_checked_the_same_way():
    workflow = RELEASE.read_text(encoding="utf-8")
    assert release_key.pilot(TAGS) == "v0.3.0-pilot2 16a0d4c"
    same = _key()
    assert len(same) == 64 and _key() == same
    # what does not change the check: another kind of tag, a title, the job's name or comments, when it runs
    assert _key(tags=TAGS + "v0.3.0-handoff2 feaaefc Another handoff\n") == same
    assert _key(tags=TAGS.replace("pilot release 2", "the second pilot")) == same
    renamed = workflow.replace("name: Pilot laptop with the real LM Studio", "name: The pilot laptop")
    assert renamed != workflow and _key(workflow=renamed) == same
    commented = workflow.replace("  pilot-laptop:\n", "  pilot-laptop:\n    # one more comment\n", 1)
    assert commented != workflow and _key(workflow=commented) == same
    # what does: another pilot release or commit, a step of the check, the run sheet it walks
    assert _key(tags=TAGS + "v0.3.0-pilot3 feaaefc Pilot release 3\n") != same
    assert _key(tags=TAGS.replace("16a0d4c", "61b0aed")) != same
    longer = workflow.replace('AP_DEMO_DAY_WAIT: "7200"', 'AP_DEMO_DAY_WAIT: "9000"')
    assert longer != workflow and _key(workflow=longer) != same
    assert _key(test=TEST + "\n\ndef test_one_more():\n    pass\n") != same


def test_no_key_without_a_pilot_release_or_a_check():
    assert release_key.key("v0.3.0-handoff1 230f629 The handoff\n", RELEASE.read_text(encoding="utf-8"), TEST) == ""
    assert release_key.key(TAGS, "name: Release\njobs:\n  tags:\n    runs-on: ubuntu-latest\n", TEST) == ""
    assert release_key.key(TAGS, "", TEST) == ""


def test_the_key_of_a_commit_reads_its_own_files():
    """At a commit of this repository's history: what that commit held, not this checkout."""
    try:
        head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True)
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("no git history here (an offline bundle)")
    files = [release_key._show(head.stdout.strip(), path) for path in (release_key.TAGS, release_key.WORKFLOW,
                                                                       release_key.TEST)]  # fmt: skip
    assert release_key.key_at("HEAD") == release_key.key(*files)
    assert release_key.key_at("not-a-commit") == ""


def test_main_reuses_a_passed_check_and_a_pull_request_never_tags():
    """The check job runs only when no earlier run passed the same check (the "checked" job finds it); the tags job
    runs off pull requests only, after a check that passed here or before, with that check's zip."""
    jobs = yaml.safe_load(RELEASE.read_text(encoding="utf-8"))["jobs"]
    checked, laptop, tags = jobs["checked"], jobs[release_key.CHECK_JOB], jobs["tags"]
    assert "scripts/ci/release_key.py" in yaml.safe_dump(checked)
    assert laptop["needs"] == "checked" and laptop["if"] == "needs.checked.outputs.run == ''"
    assert set(tags["needs"]) == {"checked", release_key.CHECK_JOB}
    when = " ".join(tags["if"].split())
    assert "github.event_name != 'pull_request'" in when and "needs.checked.result == 'success'" in when
    download = next(s for s in tags["steps"] if str(s.get("uses", "")).startswith("actions/download-artifact"))
    assert download["with"]["run-id"] == "${{ needs.checked.outputs.run || github.run_id }}"
    assert tags["permissions"]["actions"] == "read" and checked["permissions"]["actions"] == "read"
