"""The tested builds (.github/workflows/builds.yml, D-247): a GitHub release only for a commit CI passed on main,
made the way the launcher's check (scripts/releases.py) reads it. Its script is run here on a small repository, with
a stand-in for ``gh``: what it would release is what the launcher would take."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import pytest

from tests.scripts import load_script

yaml = pytest.importorskip("yaml")

ROOT = Path(__file__).resolve().parents[2]
BUILDS = ROOT / ".github" / "workflows" / "builds.yml"


def _workflow() -> dict:
    return yaml.safe_load(BUILDS.read_text(encoding="utf-8"))


def test_a_build_is_released_only_once_ci_passed_on_main():
    flow = _workflow()
    on = flow.get("on") or flow.get(True)  # (YAML 1.1 reads a bare "on" as true)
    assert on == {"workflow_run": {"workflows": ["CI"], "types": ["completed"], "branches": ["main"]}}
    ci = yaml.safe_load((ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"))
    assert ci["name"] == "CI"  # (the name the trigger waits for)
    assert flow["permissions"] == {"contents": "read"}
    (job,) = flow["jobs"].values()
    assert "conclusion == 'success'" in job["if"] and "event == 'push'" in job["if"]
    assert job["permissions"] == {"contents": "write"} and 0 < job["timeout-minutes"] <= 30
    text = "\n".join(str(step.get("run") or "") for step in job["steps"])
    assert "GH_TOKEN: ${{ github.token }}" in BUILDS.read_text(encoding="utf-8") and "secrets." not in text
    assert job["steps"][0]["with"]["ref"] == "${{ github.event.workflow_run.head_sha }}"


def _run(cmd: list[str], cwd: Path, env: dict | None = None) -> str:
    done = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout


@pytest.mark.skipif(not (shutil.which("bash") and shutil.which("git") and shutil.which("sha256sum")),
                    reason="needs bash, git and sha256sum (Linux)")  # fmt: skip
def test_what_it_releases_is_what_the_launcher_takes(tmp_path):
    repo = tmp_path / "repo"
    (repo / "ap_coder").mkdir(parents=True)
    (repo / "scripts").mkdir()
    (repo / "pyproject.toml").write_text('[project]\nname = "ap-coder"\nversion = "0.3.1"\n')
    (repo / ".gitattributes").write_text("ap_coder/BUILD export-subst\n")
    (repo / "ap_coder" / "BUILD").write_text("$Format:%h %cI$\n")
    (repo / "scripts" / "launch.py").write_text("")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-c", "commit.gpgsign=false"]
    _run(["git", "init", "-q", "-b", "main"], repo)
    _run([*git, "add", "-A"], repo)
    _run([*git, "commit", "-q", "-m", "First"], repo)
    _run(["git", "tag", "v0.3.1-build.0000000"], repo)  # the last build released
    (repo / "scripts" / "launch.py").write_text("# faster\n")
    _run([*git, "commit", "-q", "-am", "Merge pull request #140: Faster reading of scans"], repo)
    sha = _run(["git", "rev-parse", "HEAD"], repo).strip()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    gh = bin_dir / "gh"  # a stand-in: no release yet; what "release create" was given, recorded
    gh.write_text('#!/bin/bash\n[ "$2" = view ] && exit 1\nprintf "%s\\n" "$@" > "$GH_ARGS"\n')
    gh.chmod(0o755)
    (step,) = [s for s in _workflow()["jobs"]["build"]["steps"] if "run" in s]
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "SHA": sha,
           "GH_ARGS": str(tmp_path / "gh_args"), "GH_TOKEN": "unused"}  # fmt: skip
    _run(["bash", "-c", step["run"]], repo, env)
    created = (tmp_path / "gh_args").read_text().splitlines()
    short = sha[:7]
    tag, name = f"v0.3.1-build.{short}", f"AP_Processer-0.3.1-build.{short}"
    assert created[:3] == ["release", "create", tag] and created[-2:] == [f"dist/{name}.zip", "dist/SHA256SUMS"]
    releases = load_script("releases", "ap_script_releases_workflow")
    assert releases.TAG.match(tag).groups() == ("0.3.1", short)
    notes = (repo / "notes.md").read_text()
    assert notes.startswith("- #140 Faster reading of scans\n") and "First" not in notes
    built = releases.BUILT.search(notes)
    assert built and built.group(1) == short and releases.paths._utc(built.group(2))
    archive = repo / "dist" / f"{name}.zip"
    sums = (repo / "dist" / "SHA256SUMS").read_text()
    assert releases._sum_for(sums, archive.name) == hashlib.sha256(archive.read_bytes()).hexdigest()
    with zipfile.ZipFile(archive) as z:
        assert releases._top_folder(z.namelist()) == name
        stamp = z.read(f"{name}/ap_coder/BUILD").decode()
    assert stamp.startswith(short) and "$Format" not in stamp  # (the launcher's build: paths.build_stamp)
