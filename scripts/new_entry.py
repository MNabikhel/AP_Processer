"""Write a new bug or decision entry under its number, taken now (D-328): a file of its own, ``docs/bugs/B-NNNN.md``
or ``docs/decisions/D-NNN.md``, numbered after every entry any branch of this clone has.

    python scripts/new_entry.py B "what broke · severity · `tests/x/test_y.py::test_z`"
    python scripts/new_entry.py D --file entry.md      a decision, its lines in a file (or on stdin: D < entry.md)

The text is the entry without its start, ``- **B-NNNN** · ``, which the script writes with the number. A bug is one
line. A decision is its first line (``**The choice.** (B-, D- it builds on)``) and then its ``  - **Why:**``,
``  - **How:**`` and ``  - *Not chosen:*`` lines, indented two spaces, each line at most 120 characters with the start.

The number is one more than the highest an entry has in this checkout (docs/BUGS.md and docs/decisions.md, frozen at
B-2997 and D-327; the archive; docs/bugs/ and docs/decisions/), on every local and remote branch (as last fetched:
nothing is fetched, no network) and among the numbers this clone's worktrees reserved. It is reserved by a file in
``<git common dir>/ap-numbers/``, made only if no other run made it first, so two worktrees of one clone never take
the same number. Then the file is written and added to git, and the number printed. Separate clones can still take
one number: ``scripts/combine.py`` gives the later branch's entry the next one.
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import subprocess
import sys
from pathlib import Path

LOGS = {"B": "docs/BUGS.md", "D": "docs/decisions.md"}  # the frozen logs: every entry to B-2997 and D-327
ARCHIVES = {"B": "docs/archive/bugs-*.md", "D": "docs/archive/decisions-*.md"}
FOLDERS = {"B": "docs/bugs", "D": "docs/decisions"}  # an entry a file, after the frozen logs
FROZEN = {"B": 2997, "D": 327}  # the last entry of each frozen log
FILE_NAME = re.compile(r"([BD])-(\d+)\.md")
LONGEST = {"B": 2000, "D": 120}  # a bug's line is cut off past 2,000 when read; a decision's lines are wrapped
RESERVED = "ap-numbers"


class Refused(Exception):
    """What the script won't write, and why."""


def git(*args: str, data: bytes | None = None) -> bytes:
    done = subprocess.run(["git", "-c", "core.quotePath=false", *args], input=data, capture_output=True)
    if done.returncode:
        raise Refused(f"git {' '.join(args)} failed: {done.stderr.decode('utf-8', 'replace').strip()}")
    return done.stdout


def headers(text: str, kind: str) -> list[int]:
    """The numbers of the entries in a log: its lines ``- **B-12** · ...`` (a number named in an entry's words is not
    one)."""
    return [int(n) for n in re.findall(rf"^- \*\*{kind}-(\d+)\*\*", text, re.M)]


def in_folder(names: list[str], kind: str) -> list[int]:
    return [int(m[2]) for name in names if (m := FILE_NAME.fullmatch(name)) and m[1] == kind]


def _objects(data: bytes) -> list[tuple[str, bytes]]:
    """``git cat-file --batch``'s answer: each object's type and content."""
    found, at = [], 0
    while at < len(data):
        end = data.index(b"\n", at)
        _sha, kind, size = data[at:end].split()
        found.append((kind.decode(), data[end + 1 : end + 1 + int(size)]))
        at = end + 1 + int(size) + 1
    return found


def _tree_names(tree: bytes) -> list[str]:
    """The names in a git tree object (``<mode> <name>\\0`` and 20 bytes of id each)."""
    names, at = [], 0
    while at < len(tree):
        end = tree.index(b"\0", at)
        names.append(tree[at:end].split(b" ", 1)[1].decode("utf-8", "replace"))
        at = end + 21
    return names


def on_branches(kind: str) -> set[int]:
    """The numbers of the entries on every local and remote branch: their frozen log and entry folder, read in two
    passes of ``git cat-file`` (the ids, then each different log and folder once)."""
    tips = sorted(set(git("for-each-ref", "--format=%(objectname)", "refs/heads", "refs/remotes").decode().split()))
    if not tips:
        return set()
    asked = "".join(f"{tip}:{path}\n" for tip in tips for path in (LOGS[kind], FOLDERS[kind]))
    ids = {line.split()[0] for line in git("cat-file", "--batch-check", data=asked.encode()).decode().splitlines()
           if not line.endswith(" missing")}  # fmt: skip
    found: set[int] = set()
    if ids:
        for kind_of, content in _objects(git("cat-file", "--batch", data="".join(f"{i}\n" for i in ids).encode())):
            if kind_of == "tree":
                found.update(in_folder(_tree_names(content), kind))
            elif kind_of == "blob":
                found.update(headers(content.decode("utf-8", "replace"), kind))
    return found


def reserved_folder() -> Path:
    """Where this clone's worktrees reserve their numbers: one folder in the git directory they share."""
    return (Path.cwd() / git("rev-parse", "--git-common-dir").decode().strip()).resolve() / RESERVED


def taken(kind: str) -> set[int]:
    """Every number of this kind an entry has, here or on a branch, or that a worktree of this clone reserved."""
    found = set(on_branches(kind))
    for path in [Path(LOGS[kind]), *Path().glob(ARCHIVES[kind])]:
        if path.is_file():
            found.update(headers(path.read_text(encoding="utf-8"), kind))
    found.update(in_folder([p.name for p in Path(FOLDERS[kind]).glob("*.md")], kind))
    folder = reserved_folder()
    if folder.is_dir():
        found.update(int(p.name[2:]) for p in folder.iterdir() if re.fullmatch(rf"{kind}-\d+", p.name))
    return found


def reserve(kind: str, after: int) -> int:
    """The first number after ``after`` no worktree of this clone reserved, reserved now: its file is made only if
    none is there (O_EXCL), so two runs at once never both have it."""
    folder = reserved_folder()
    folder.mkdir(exist_ok=True)
    branch = git("branch", "--show-current").decode().strip() or "no branch"
    number = after + 1
    while True:
        try:
            handle = os.open(folder / f"{kind}-{number}", os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            number += 1
            continue
        with os.fdopen(handle, "w", encoding="utf-8") as note:
            note.write(f"{branch} {Path.cwd()} {datetime.datetime.now():%Y-%m-%d %H:%M}\n")
        return number


def entry(kind: str, number: int, text: str) -> str:
    """The entry's file: its start and number, then the text given (checked)."""
    text = "\n".join(line.rstrip() for line in text.strip().replace("\r\n", "\n").split("\n"))
    if not text:
        raise Refused("No text given: the entry's words, as an argument, in a file (--file) or on stdin.")
    if text.startswith("- **"):
        raise Refused(f"Give the entry without its start (- **{kind}-NNNN** · ): the script writes it, numbered.")
    lines = f"- **{kind}-{number}** · {text}".split("\n")
    if kind == "B" and len(lines) > 1:
        raise Refused("A bug is one line: what broke · severity · `the test that keeps it fixed`.")
    for n, line in enumerate(lines[1:], 2):
        if not line.startswith("  "):
            raise Refused(f"Line {n} is not indented: each line after the first starts with two spaces (  - **Why:**).")
    for n, line in enumerate(lines, 1):
        if len(line) > LONGEST[kind]:
            raise Refused(f"Line {n} is {len(line)} characters, with the start: at most {LONGEST[kind]}.")
    return "\n".join(lines) + "\n"


def write_entry(kind: str, text: str) -> tuple[int, str]:
    """Takes the next number, writes the entry's file and adds it to git: the number and the file."""
    if subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True).returncode:
        raise Refused("Not in a git checkout: run it from the AP Coder folder of a clone.")
    os.chdir(git("rev-parse", "--show-toplevel").decode().strip())
    top = max(taken(kind), default=0)
    entry(kind, top + 1, text)  # checked before a number is reserved
    number = reserve(kind, top)
    path = f"{FOLDERS[kind]}/{kind}-{number}.md"
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(entry(kind, number, text).encode("utf-8"))
    git("add", "--", path)
    return number, path


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("kind", type=str.upper, choices=sorted(LOGS), help="B, a bug; D, a decision")
    parser.add_argument("text", nargs="?", help="the entry's words, without its start (else --file, or stdin)")
    parser.add_argument("--file", type=Path, help="a file holding the entry's words (a decision's several lines)")
    args = parser.parse_args(argv)
    if args.text is not None:
        text = args.text
    elif args.file is not None:
        text = args.file.read_text(encoding="utf-8")
    elif not sys.stdin.isatty():
        text = sys.stdin.read()
    else:
        text = ""
    try:
        number, path = write_entry(args.kind, text)
    except Refused as refused:
        print(refused, file=sys.stderr)
        return 1
    print(f"{args.kind}-{number}")
    print(f"Written: {path} (added to git). Name {args.kind}-{number} in the test and the commit.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
