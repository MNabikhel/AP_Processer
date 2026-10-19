"""The map-reviewer before every commit, without anyone having to remember it (Claude Code hooks).

The hooks are in .claude/settings.json:

    python scripts/map_review_gate.py --reviewed   # SubagentStop of the map-reviewer agent: record what it reviewed
    python scripts/map_review_gate.py --commit     # PreToolUse on Bash: stop a `git commit` it hasn't reviewed

When the map-reviewer agent (.claude/agents/map-reviewer.md) finishes, ``--reviewed`` records every changed file of the
repository (against HEAD, and new files not yet added) as it then is, both staged and in the work tree (git blob
ids), in the repository's git folder, never in the work tree. Before an agent's ``git commit``, ``--commit`` asks the
same of every changed file now: one that is not Markdown (code, tests, scripts, CI, settings) and whose staged or
work-tree content was not reviewed stops the command (exit 2), with a message saying to run the reviewer. Comparing
every changed file, not only the staged ones, holds whatever the command adds first (``git add -A && git commit``,
``commit -a``, ``commit <paths>``). Markdown edits after the review (the map updates it asks for) need no second
review, and a merge, cherry-pick or revert being finished passes: its changes were reviewed on their own side.

It is a habit made automatic, not a lock: a person committing in a terminal is never stopped, and anything that
cannot be read (no git, bad input) lets the command through. Only the standard library, offline.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

RECORD = "ap-coder-map-review.json"
IN_PROGRESS = ("MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD")
ABSENT = "absent"
SEPARATORS = re.compile(r"&&|\|\||[;&|\n()]")
GIT_OPTIONS_WITH_VALUE = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
WRAPPERS = {"env", "command", "time", "nohup", "sudo", "exec", "xargs", "nice"}  # run the word after them
SHELLS = {"bash", "sh", "zsh"}


def _git(cwd: Path, *args: str) -> str | None:
    try:
        done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8")
    except OSError:
        return None
    return done.stdout if done.returncode == 0 else None


def _paths(cwd: Path, *args: str) -> set[str]:
    return {p for p in (_git(cwd, *args, "-z") or "").split("\0") if p}


def _reviewable(path: str) -> bool:
    return not path.lower().endswith(".md")


def _top(cwd: Path) -> Path | None:
    if not cwd.is_dir():
        return None
    top = (_git(cwd, "rev-parse", "--show-toplevel") or "").strip()
    return Path(top) if top else None


def changed(top: Path) -> dict[str, list[str]]:
    """Every changed non-Markdown file (top-level paths): its staged and its work-tree content, as git blob ids."""
    paths = _paths(top, "diff", "--name-only", "HEAD") | _paths(top, "diff", "--cached", "--name-only")
    paths |= _paths(top, "ls-files", "--others", "--exclude-standard")
    paths = {p for p in paths if _reviewable(p)}
    staged = {}
    for line in (_git(top, "ls-files", "-s", "-z", "--", *sorted(paths)) or "").split("\0") if paths else []:
        meta, _, path = line.partition("\t")
        if path:
            staged[path] = meta.split()[1]
    out = {}
    for path in sorted(paths):
        work = (_git(top, "hash-object", "--", path) or "").strip() if (top / path).is_file() else ABSENT
        out[path] = sorted({staged.get(path, ABSENT), work})
    return out


def _record_path(top: Path) -> Path | None:
    where = (_git(top, "rev-parse", "--git-path", RECORD) or "").strip()
    return (top / where) if where else None


def _folder(word: str) -> str:
    return os.path.expandvars(os.path.expanduser(word))


def commit_dirs(command: str, cwd: Path) -> list[Path]:
    """The folder each ``git commit`` in a shell command runs in (``cd X &&``, ``git -C X``, ``$VAR``, ``env``,
    ``NAME=value``, ``bash -c '...'`` followed). The caller also checks its own folder: a guess here never lets a
    commit through, it only adds folders to check."""
    found, here = [], cwd
    for part in SEPARATORS.split(command):
        try:
            words = shlex.split(part)
        except ValueError:
            words = part.split()
        while words and (re.fullmatch(r"\w+=.*", words[0]) or words[0] in WRAPPERS or words[0].startswith("-")):
            words = words[1:]  # NAME=value, env, time, xargs -0 ...: the command is the next word
        if not words:
            continue
        if words[0] == "cd" and len(words) > 1:
            here = here / _folder(words[1])
            continue
        if Path(words[0]).name in SHELLS and "-c" in words[1:-1]:
            found += commit_dirs(words[words.index("-c") + 1], here)
            continue
        if Path(words[0]).name.lower() not in ("git", "git.exe"):
            continue
        at, i = here, 1
        while i < len(words) and words[i].startswith("-"):
            if words[i] == "-C" and i + 1 < len(words):
                at = at / _folder(words[i + 1])
            i += 2 if words[i] in GIT_OPTIONS_WITH_VALUE else 1
        if i < len(words) and words[i] == "commit":
            found.append(at)
    return found


def _event() -> dict:
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return {}
    return event if isinstance(event, dict) else {}


def _cwd(event: dict) -> Path:
    return Path(str(event.get("cwd") or os.getcwd()))


def reviewed() -> int:
    top = _top(_cwd(_event()))
    record = _record_path(top) if top else None
    if record is not None:
        record.write_text(json.dumps(changed(top), indent=1, sort_keys=True), encoding="utf-8")
    return 0


def _unreviewed(top: Path) -> list[str]:
    record = _record_path(top)
    if record is None or any((record.parent / name).exists() for name in IN_PROGRESS):
        return []
    try:
        seen = json.loads(record.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        seen = {}
    return [p for p, blobs in changed(top).items() if not set(blobs) <= set(seen.get(p, ()))]


def commit() -> int:
    event = _event()
    tool_input = event.get("tool_input")
    command = str((tool_input if isinstance(tool_input, dict) else {}).get("command") or "")
    folders = commit_dirs(command, _cwd(event))
    tops = {_top(folder) for folder in ([_cwd(event), *folders] if folders else [])}  # fail closed: its own folder too
    for top in sorted((t for t in tops if t), key=str):
        unreviewed = _unreviewed(top)
        if unreviewed:
            shown = ", ".join(unreviewed[:8]) + (f" and {len(unreviewed) - 8} more" if len(unreviewed) > 8 else "")
            print(f"Not committed: the map-reviewer agent (.claude/agents/map-reviewer.md) has not reviewed {shown} "
                  f"in {top} as they are now (staged and on disk). Run it on this change, fix its BLOCKING findings "
                  "and the map lines it asks for, then commit again (Markdown edits after the review need no second "
                  "one; a code change you don't mean to commit: stash it).", file=sys.stderr)  # fmt: skip
            return 2
    return 0


def main(argv: list[str]) -> int:
    if "--reviewed" in argv:
        return reviewed()
    if "--commit" in argv:
        return commit()
    print(__doc__)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
