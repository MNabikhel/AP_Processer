"""``python -m ap_coder backup now|list|check|restore``: the backups from the console (D-143), for IT.

* ``now``: a backup of the database and the files (as *Back up now* on Settings → Data & backups), copied to the
  second backup folder when one is set.
* ``list``: each backup, what it holds and whether every file is there; ``--from <folder>``: the backups in that
  folder (the second backup folder, e.g. a network share) instead of the data folder's ``backups``.
* ``check [<name>]``: every file of one backup, or of each, read and compared with its SHA-256.
* ``restore <name> [--from <folder>]``: AP Coder back as it was at that backup, as the page does it (in server mode
  the accounts stay as they are and everyone is logged out). Onto an empty data folder too: a new computer restored
  from the network share. AP Coder must not be running on that data folder meanwhile (``service stop`` first).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

from ..core.store import Store, backup_files
from . import service

SERVER_FILE = service.SERVER_FILE  # written by the running dashboard


def add_parser(sub: Any) -> None:
    p = sub.add_parser("backup", help="Backups of the database and the invoice files: now, list, check, restore")
    p.add_argument("action", choices=["now", "list", "check", "restore"])
    p.add_argument("name", nargs="?", default=None, help="check, restore: the backup (ap_coder-<date>-<time>.db)")
    p.add_argument("--from", dest="source", default=None, metavar="FOLDER",
                   help="list, check, restore: the backups in this folder (e.g. the second backup folder on a network "
                   "share), not the data folder's")  # fmt: skip
    p.add_argument("--yes", action="store_true", help="restore: don't ask first")


def _running(data: Path) -> bool:
    """An AP Coder dashboard (the service, or the laptop's) runs on this data folder."""
    try:
        found = json.loads((data / SERVER_FILE).read_text(encoding="utf-8"))
        pid = int(found.get("pid") or 0) if isinstance(found, dict) else 0
    except (OSError, ValueError, TypeError):
        return False
    return service.process_alive(pid)


def main(args: argparse.Namespace) -> int:
    db = Path(args.db)
    data = db.parent
    folder = Path(args.source).expanduser() if args.source else data / "backups"
    if args.action == "now":
        store = Store(db)
        try:
            made = store.backup_now("manual", actor="the console (backup)")
        except OSError as exc:
            print(f"The backup could not be made: {exc}", file=sys.stderr)
            return 1
        print(f"Backed up to {made}: {backup_files.holds_words(made, backup_files.check(made))}.")
        copied = backup_files.check(made).copy
        if copied:
            print(f"Also copied to {copied['what']}." if copied["ok"] else f"Not copied to the second backup folder: "
                  f"{copied['what']}.")  # fmt: skip
        return 0
    found = sorted(folder.glob("ap_coder-*.db"), reverse=True) if folder.is_dir() else []
    if args.action == "list":
        if not found:
            print(f"No backups in {folder}.")
        seen: dict[str, int | None] = {}
        for path in found:
            held = backup_files.check(path, seen=seen)
            made = dt.datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            copy = ""
            if held.copy:
                copy = " · copied to the second folder" if held.copy.get("ok") else " · not copied to the second folder"
            print(
                f"{path.name}  {made}  {backup_files.holds_words(path, held)}  {backup_files.state_words(held)}{copy}"
            )
        return 0
    if args.name is not None:
        chosen = [p for p in found if p.name == args.name]
        if not chosen:
            print(f"No backup {args.name} in {folder} (python -m ap_coder backup list"
                  f"{' --from ' + str(folder) if args.source else ''} lists them).", file=sys.stderr)  # fmt: skip
            return 1
        found = chosen
    if args.action == "check":
        bad = 0
        for path in found:
            held = backup_files.check(path, deep=True)
            print(f"{path.name}: {backup_files.state_words(held)}")
            for rel in held.missing:
                print(f"  missing: {rel}")
            for rel in held.damaged:
                print(f"  damaged: {rel}")
            bad += not held.complete and held.listed
        return 1 if bad else 0
    if args.name is None:
        print("Name the backup to restore (python -m ap_coder backup list).", file=sys.stderr)
        return 2
    return _restore(found[0], db, args.yes)


def _restore(backup: Path, db: Path, yes: bool) -> int:
    data = db.parent
    if _running(data):
        print(f"AP Coder is running on {data}: stop it first (on the server: python -m ap_coder service stop; on a "
              "laptop, close its window), or restore from Settings → Data & backups. Nothing was changed.",
              file=sys.stderr)  # fmt: skip
        return 1
    server = service.is_server_here(data)
    print(f"Restoring {backup.name} puts AP Coder in {data} back as it was then: the database and the invoice files, "
          "saved emails and attachments of that moment. What was done since is replaced; files added since are moved "
          "to backups/set-aside-<date>. The current state is backed up first, so this can be undone.")  # fmt: skip
    if server:
        print("The user accounts, their passwords and roles, and paused logins stay as they are now; everyone is "
              "logged out.")  # fmt: skip
    if not yes:
        try:
            answer = input("Type yes to restore: ").strip().lower()
        except EOFError:
            answer = ""
        if answer != "yes":
            print("Nothing was changed.")
            return 1
    try:
        done = Store(db).restore_state(backup, keep_accounts=server)
    except (ValueError, OSError) as exc:
        print(f"Not restored: {exc}.", file=sys.stderr)
        return 1
    print(f"Restored {backup.name}. The files: {done.files.words()}.")
    print(f"The state before was backed up as {done.safety.name}. A restore clears the second backup folder: set it "
          "again in Settings → Data & backups.")  # fmt: skip
    return 1 if done.files.missing or done.files.damaged else 0
