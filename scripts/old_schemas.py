"""The new database each older AP Coder release made, kept as SQL in ``tests/fixtures/old_schemas/``, for the test that
an older database brought up to date is the same as a new one (``tests/core/test_migrations.py``, D-190).

    python scripts/old_schemas.py                        every release in RELEASES (and UNRELEASED)
    python scripts/old_schemas.py --code e1cf61a=FOLDER  a release's code unpacked already (GitHub's ZIP of it)

In a shallow clone (a cloud session's) it writes nothing and says so (``git fetch --unshallow`` gets every release):
a file made again without the older releases' code would be wrong. In a clone without a release's history (the code
handed over as a zip, docs/HANDOFF.md's route B) a release it cannot read whose file is here keeps that file as it is,
never written again, and a release that makes the same database adds none; a release with no file yet that it cannot
read (``--code`` gives its code) stops it, writing nothing.

Each release's own code (its commit's ``ap_coder/``, as ``git archive`` gives it) makes a new database, run by this
Python; what it made (its tables, indexes, triggers and the rows it wrote, such as ``schema_version``) is written as
SQL. Releases that made the same database share one file, named after the first of them. Run it again after a release
that changes the schema; nothing connects to the internet.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tests" / "fixtures" / "old_schemas"

# What a user may have: main after each merged pull request (scripts/ci/old_versions.py's for PR #1 to #8, then the
# ones since, the pilot's and the hand-over's release tags among them), then each tested build GitHub released (D-247:
# a v0.3.0-build.<sha> tag, from PR #133), oldest first.
RELEASES = [
    ("db95511", "PR #1"), ("b968b74", "PR #2"), ("4366f35", "PR #3"), ("e2a20cf", "PR #4"), ("a798f79", "PR #5"),
    ("abbc331", "PR #6"), ("0de8afe", "PR #7"), ("e1cf61a", "PR #8"), ("968cf9d", "PR #44"), ("5dd9d0d", "PR #45"),
    ("da7cfc2", "PR #46"), ("c4c7e7c", "PR #47"), ("b3002bb", "PR #48"), ("b30eb2d", "PR #49"), ("ff9c3ff", "PR #50"),
    ("d90daae", "PR #51"), ("4cd5c1c", "PR #52"), ("a0b556e", "PR #53"), ("08984c2", "PR #54"), ("e833562", "PR #55"),
    ("0bd918a", "PR #56"), ("14546cf", "PR #57"), ("e11d9bb", "PR #58"), ("345744d", "PR #59"), ("16b869d", "PR #60"),
    ("dfdb962", "PR #61"), ("2ed0fa1", "PR #62"), ("116274c", "PR #63"), ("15a0a48", "PR #64"), ("51d5c42", "PR #65"),
    ("a1724b5", "PR #66, v0.3.0-pilot1"), ("230f629", "PR #67, v0.3.0-handoff1"), ("9183460", "PR #68"),
    ("61b0aed", "PR #69"), ("16a0d4c", "PR #70, v0.3.0-pilot2"), ("feaaefc", "PR #71"), ("a610722", "PR #72"),
    ("0446e2e", "PR #73, v0.3.0-handoff2"), ("6b683b1", "PR #74"), ("f97383b", "PR #75"), ("378e642", "PR #76"),
    ("7b1c924", "PR #77, v0.3.0-pilot3, v0.3.0-handoff3"), ("cff0ad5", "PR #78"), ("1d7d604", "PR #79"),
    ("d87a975", "PR #80"), ("d4563c5", "v0.3.0-pilot4, v0.3.0-handoff4"),
    ("f57d472", "PR #81, v0.3.0-pilot5, v0.3.0-handoff5"), ("34d1eb5", "PR #82"),
    ("a1bbbf1", "v0.3.0-pilot6, v0.3.0-handoff6"), ("c362eb4", "v0.3.0-pilot7, v0.3.0-handoff7"),
    ("37cbef2", "v0.3.0-pilot8, v0.3.0-handoff8"), ("265419a", "v0.3.0-pilot9, v0.3.0-handoff9"),
    ("1696f20", "PR #99"), ("12e5dfe", "PR #133"), ("3b834d2", "PR #134"), ("51c29f3", "PR #135"),
    ("a7fd031", "PR #136"), ("cf4fde6", "PR #138"), ("9520c2f", "PR #139"), ("d34e47b", "PR #140"),
    ("19fb88b", "PR #141"), ("f5a2dab", "PR #142"), ("8437593", "PR #143"), ("8269793", "PR #144"),
    ("9e2cb35", "PR #145"), ("eb1f4f9", "PR #146"), ("a6af108", "PR #148"), ("ec1b52a", "PR #149"),
    ("77edd38", "PR #150"), ("1a9e943", "PR #151"), ("610577c", "PR #152"), ("8ebd4f9", "PR #154"),
    ("95b4a22", "PR #155"), ("d025cd4", "PR #156"), ("34d7df2", "PR #158"),
]  # fmt: skip
# The schema versions no release had (made on the way to PR #99), each as the commit that first made it.
UNRELEASED = [
    ("44a6ba0", "schema 20"), ("4101eed", "schema 22"), ("2e8512b", "schema 24"), ("025c979", "schema 25"),
    ("1933ce4", "schema 26"),
]  # fmt: skip

# Made by the old code in a scratch folder, its own ``Store`` (``ap_coder/store.py`` before the store moved into
# ``core``, D-67; ``core/store.py`` or the package after).
MAKE = """import sys
from pathlib import Path
folder, db = sys.argv[1], sys.argv[2]
sys.path.insert(0, folder)
if (Path(folder) / "ap_coder" / "store.py").is_file():
    from ap_coder.store import Store
else:
    from ap_coder.core.store import Store
Store(db)
"""
# The settings a new database writes anew each time, as one value: the summary's id (``summary_memo``) and the day it
# began to keep the rules' use (``rule_use_since``, the clock's time; the releases from PR #133 have it).
NEW_EACH_TIME = {"summary_id": "0" * 32, "rule_use_since": "2000-01-01T00:00:00"}


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True)


def files_of(out: Path) -> dict[str, Path]:
    """Each release a file in ``out`` was made from (named after the first, its first line naming the others)."""
    found = {}
    for path in sorted(out.glob("schema-*.sql")):
        with path.open(encoding="utf-8") as sql:
            found |= {sha: path for sha in re.findall(r"\b([0-9a-f]{7,40}) \(", sql.readline())}
    return found


def not_here(given: dict[str, str]) -> list[tuple[str, str]]:
    """The releases whose code this clone does not have (one given with ``--code`` is read from its folder)."""
    return [(sha, label) for sha, label in [*RELEASES, *UNRELEASED]
            if sha not in given and git("cat-file", "-e", f"{sha}^{{commit}}").returncode]  # fmt: skip


def unreadable(gone: list[tuple[str, str]], files: dict[str, Path]) -> str:
    """What stops this clone from writing the files: being shallow, or a release it cannot read (of ``gone``) that no
    file was made from (``files``); and what to do: '' when nothing does."""
    if git("rev-parse", "--is-shallow-repository").stdout.strip() == b"true":
        return ("this clone is shallow: it has only the newest commits, not the older releases' code. Run git fetch "
                "--unshallow, then run this again.")  # fmt: skip
    missing = [f"{sha} ({label})" for sha, label in gone if sha not in files]
    if not missing:
        return ""
    return (f"this clone does not have {', '.join(missing)}, and no file here was made from it yet. Fetch it (git "
            "fetch origin SHA), or give its code with --code SHA=FOLDER (GitHub's ZIP of it, unpacked), then run this "
            "again.")  # fmt: skip


def code_of(sha: str, folder: Path) -> None:
    """``sha``'s ``ap_coder/`` in ``folder``."""
    archive = git("archive", "--format=tar", sha, "ap_coder")
    archive.check_returncode()
    with tarfile.open(fileobj=io.BytesIO(archive.stdout)) as tar:
        tar.extractall(folder, filter="data") if sys.version_info >= (3, 12) else tar.extractall(folder)  # noqa: S202


def made_by(folder: Path, work: Path) -> Path:
    """A new database made by the code in ``folder``."""
    db = work / "data" / "ap_coder.db"
    env = {k: v for k, v in os.environ.items() if not k.startswith(("AP_", "PYTHON"))}
    env["HOME"] = env["USERPROFILE"] = str(work / "home")
    subprocess.run([sys.executable, "-I", "-c", MAKE, str(folder), str(db)], cwd=work, env=env, check=True)
    return db


def dump(db: Path) -> str:
    """The database as SQL: its tables, the rows in them, then its indexes and triggers (made after the rows, so the
    triggers add nothing), each table's rows in their order. SQLite's own tables and a full-text index's are left to
    SQLite, which makes them with the tables. What is new in every database (``NEW_EACH_TIME``) is written as one
    value: two releases that made the same database give the same SQL."""
    conn = sqlite3.connect(db)
    try:
        conn.executemany("UPDATE settings SET value = ? WHERE key = ?", [(v, k) for k, v in NEW_EACH_TIME.items()])
        made = conn.execute("SELECT type, name, sql FROM sqlite_master WHERE sql IS NOT NULL ORDER BY rowid").fetchall()
        virtual = [name for _, name, sql in made if sql.upper().startswith("CREATE VIRTUAL TABLE")]
        made = [m for m in made if not m[1].startswith(("sqlite_", *(f"{v}_" for v in virtual)))]
        lines = [f"{sql};" for kind, _, sql in made if kind == "table"]
        for kind, table, _ in made:
            if kind != "table" or table in virtual:
                continue
            columns = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            quoted = ", ".join(f"quote({c})" for c in columns)
            for row in conn.execute(f"SELECT {quoted} FROM {table} ORDER BY rowid"):
                lines.append(f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(row)});")
        lines += [f"{sql};" for kind, _, sql in made if kind != "table"]
        version = conn.execute("SELECT value FROM settings WHERE key = 'schema_version'").fetchone()
    finally:
        conn.close()
    return f"-- schema {version[0] if version else 1}\n" + "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--code", action="append", default=[], help="SHA=FOLDER: that release's code, unpacked")
    parser.add_argument("--out", type=Path, default=OUT)
    args = parser.parse_args(argv)
    given = dict(item.split("=", 1) for item in args.code)
    files, gone = files_of(args.out), dict(not_here(given))
    why = unreadable(list(gone.items()), files)
    if why:
        print(f"Nothing was written: {why}")
        return 2
    left = {files[sha] for sha in gone}  # each made from a release not here: kept as it is, never written again
    as_left = {path.read_text(encoding="utf-8").partition("\n")[2]: path for path in left}  # (its SQL under its header)
    for path in sorted(left):
        print(f"{path.name}: kept as it is (made from {', '.join(s for s in gone if files[s] == path)}, not here)")
    args.out.mkdir(parents=True, exist_ok=True)
    kept: dict[str, tuple[Path, list[str]]] = {}  # a dump -> (its file, the releases that made it)
    for sha, label in [*RELEASES, *UNRELEASED]:
        if sha in gone:
            continue
        with tempfile.TemporaryDirectory(prefix="ap-old-schema-") as scratch:
            work = Path(scratch)
            folder = Path(given[sha]) if sha in given else work / "code"
            if sha not in given:
                code_of(sha, folder)
            sql = dump(made_by(folder, work))
        if sql in as_left:
            print(f"{sha} ({label}): the same database as {as_left[sql].name}, kept as it is")
            continue
        same = kept.get(sql)
        if same is not None:
            same[1].append(f"{sha} ({label})")
            continue
        version = int(sql.split("\n", 1)[0].split()[-1])
        path = args.out / f"schema-{version:02d}-{sha}.sql"
        kept[sql] = (path, [f"{sha} ({label})"])
        path.write_text(sql, encoding="utf-8")
    for path, releases in kept.values():
        body = path.read_text(encoding="utf-8")
        also = f"; the same at {', '.join(releases[1:])}" if releases[1:] else ""
        header = f"-- A new database as AP Coder made it at {releases[0]}{also} (scripts/old_schemas.py)\n"
        path.write_text(header + body, encoding="utf-8")
        print(f"{path.relative_to(ROOT) if path.is_relative_to(ROOT) else path}: {', '.join(releases)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
