"""The offline bundle is the same bytes for the same commit (D-188): built again, its SHA-256 is the one IT was given,
so a ZIP can be checked by anyone who builds it from that commit."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import subprocess
import sys
import time
import types
import zipfile
from pathlib import Path

from tests.scripts import load_script

NAMES = ["README.md", "ap_coder/__init__.py", "APProcessor.command", "wheelhouse/x-1.0-py3-none-any.whl"]
TEXTS = {"README.md": "hi", "ap_coder/__init__.py": "", "APProcessor.command": "#!/bin/bash\n",
         "wheelhouse/x-1.0-py3-none-any.whl": "PK"}  # fmt: skip


def _stage(folder: Path, order: list[str], when: float, mode: int) -> Path:
    """A bundle folder of a few files, made in ``order``, each dated ``when`` with permissions ``mode``."""
    for rel in order:
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_text(TEXTS[rel])
        os.chmod(folder / rel, mode)
        os.utime(folder / rel, (when, when))
    return folder


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_zip_is_the_same_bytes_whenever_and_however_its_files_were_made(tmp_path, monkeypatch):
    """Every entry is dated the commit's time (or SOURCE_DATE_EPOCH), in one order, with fixed permissions: not the
    files' own times (a fresh checkout's, a download's), the order they were made in, or this computer's umask."""
    bundle = load_script("build_offline_bundle", "ap_script_build_offline_bundle")
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1793000000")
    one = _stage(tmp_path / "one", NAMES, 1_700_000_000, 0o600)
    two = _stage(tmp_path / "two", list(reversed(NAMES)), 1_790_000_000, 0o775)
    bundle._zip(one, tmp_path / "one.zip")
    bundle._zip(two, tmp_path / "two.zip")
    assert _sha(tmp_path / "one.zip") == _sha(tmp_path / "two.zip")
    with zipfile.ZipFile(tmp_path / "one.zip") as zf:
        infos = zf.infolist()
    assert [i.filename for i in infos] == sorted(i.filename for i in infos)
    utc = time.gmtime(1793000000)
    assert {i.date_time for i in infos} == {(*utc[:5], utc[5] // 2 * 2)}  # (a ZIP keeps even seconds)
    assert (infos[0].external_attr >> 16) & 0o777 in (0o644, 0o755)
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1793000100")  # another commit's time: another ZIP
    bundle._zip(one, tmp_path / "three.zip")
    assert _sha(tmp_path / "three.zip") != _sha(tmp_path / "one.zip")


def test_the_bundle_is_dated_by_its_commit_not_by_when_it_was_built(monkeypatch):
    bundle = load_script("build_offline_bundle", "ap_script_build_offline_bundle")
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    monkeypatch.setattr(bundle, "commit_time", lambda: "2026-11-06T20:14:00")
    assert bundle.build_epoch() == 1793996040 and bundle.built_text(1793996040) == "2026-11-06T20:14:00"
    monkeypatch.setattr(bundle, "commit_time", lambda: "")  # no git, no stamp: when it is built
    assert abs(bundle.build_epoch() - time.time()) < 5
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1793000000")
    assert bundle.build_epoch() == 1793000000


def test_two_builds_of_one_commit_are_the_same_zip(tmp_path, monkeypatch):
    """The whole build twice (the downloads stood in for, the same wheels), a while apart: the same SHA-256."""
    bundle = load_script("build_offline_bundle", "ap_script_build_offline_bundle")
    real_run = subprocess.run

    def run(cmd, **kwargs):
        if cmd[0] != sys.executable:
            return real_run(cmd, **kwargs)  # git
        dest = Path(cmd[cmd.index("--dest") + 1]) if "--dest" in cmd else None
        if dest is not None:
            (dest / "x-1.0-py3-none-any.whl").write_bytes(b"PK the same wheel")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(bundle.subprocess, "run", run)
    monkeypatch.delenv("SOURCE_DATE_EPOCH", raising=False)
    made = []
    for n, later in enumerate((0, 3600)):  # an hour apart: what "now" says differs, the bytes don't

        class Later(dt.datetime):
            @classmethod
            def now(cls, tz=None):
                return dt.datetime.now(tz) + dt.timedelta(seconds=later)  # noqa: B023 - (read in this round)

        monkeypatch.setattr(bundle, "dt", types.SimpleNamespace(datetime=Later, date=dt.date, timezone=dt.timezone))
        monkeypatch.setattr(bundle, "time", types.SimpleNamespace(time=lambda shift=later: time.time() + shift,
                                                                  gmtime=time.gmtime))  # fmt: skip
        out = tmp_path / f"dist{n}"
        assert bundle.main(["--out", str(out), "--no-models", "--python", "3.12", "--no-current"]) == 0
        (zipped,) = out.glob("APProcessor-offline-*.zip")
        made.append(zipped)
        manifest = json.loads((out / "offline-bundle" / "APProcessor" / "bundle_manifest.json").read_text())
        assert manifest["built"] == bundle.built_text(bundle.build_epoch())
    assert _sha(made[0]) == _sha(made[1])
    assert made[0].with_name(made[0].name + ".sha256").read_text().split()[0] == _sha(made[0])
