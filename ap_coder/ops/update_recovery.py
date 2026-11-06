"""An update of the company server cut short (a power cut, the window closed, the server restarted): what
``server-update`` was doing is in ``<data folder>/service/update.json`` (D-188), and the next ``server-update`` or the
service's next start acts on it.

The marker says where the update was: ``from`` and ``to`` (the versions), their folders, the database, the safety copy
once made, the step under way and when it started. It is written whole at each step (a new file renamed over the old
one, flushed to the disk first), and removed once the update is recorded. While ``server-update`` runs it holds
``update.lock`` (the operating system's lock: let go when the process ends, killed or not), so a marker whose lock is
free is one left behind.

What is done with it, decided per step (:data:`RULES`):

* ``prepare`` (unzip, set up, a copy of the database updated, the self-check), ``stop`` and ``safety-copy``: the old
  version still has its own database, untouched: it just starts. The new folder is left (the next ``server-update``
  unzips beside it; delete it when done) and a safety copy cut short is removed.
* ``migrate``: the new version may have updated part of the database: the safety copy (made with the service stopped,
  so nothing was done since) is put back and the old version starts.
* ``switch``: the database is updated and the new folder checked; the switch is finished (the new version starts).
  The service host finishes it only when it runs the new folder already (run.cmd was rewritten), else it goes back.
* ``start``: the switch was made: the new version starts (``server-update --back`` goes back if it turns out wrong;
  ``server-update`` itself goes back when it doesn't answer).
* ``going-back``: an update going back on its own (the new version did not answer): it goes back.

The service host runs the folder run.cmd names. When that is not the folder the rule starts, it rewrites run.cmd
and ends: the scheduled task's repeating trigger starts the right one within 5 minutes.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import os
import shutil
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from ..core.paths import try_lock

Say = Callable[[str], None]
MARKER = "update.json"  # in the data folder's service folder, beside run.cmd
LOCK = "update.lock"
CHECK_FOLDER = "update-check"  # the copy of the database the new version updates first (removed after)
STEPS = ("prepare", "stop", "safety-copy", "migrate", "switch", "start", "going-back")
OLD, BACK, FINISH = "old", "back", "finish"
RULES = {"prepare": OLD, "stop": OLD, "safety-copy": OLD, "migrate": BACK, "switch": FINISH, "start": FINISH,
         "going-back": BACK}  # fmt: skip
WHAT = {
    "prepare": "while it prepared the new version (the old one kept serving)",
    "stop": "while it stopped the service",
    "safety-copy": "while it made the safety copy of the database",
    "migrate": "while the new version updated the database",
    "switch": "while it switched the service to the new folder",
    "start": "while the new version started",
    "going-back": "while it went back to the old version",
}


def service_folder(data: Path) -> Path:
    return data / "service"


def marker_path(data: Path) -> Path:
    return service_folder(data) / MARKER


def check_folder(data: Path) -> Path:
    return service_folder(data) / CHECK_FOLDER


def write(data: Path, state: dict) -> None:
    """The marker, whole: written beside it, flushed to the disk, then renamed over it (never half a file)."""
    path = marker_path(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(f"{path.name}.{os.getpid()}.part")
    with open(part, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    os.replace(part, path)
    if os.name != "nt":  # the rename itself on the disk (Windows journals it)
        folder = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(folder)
        finally:
            os.close(folder)


def read(data: Path) -> dict:
    """The marker ({} when there is none, or it is not one: a file cut short is never guessed around)."""
    try:
        found = json.loads(marker_path(data).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return found if isinstance(found, dict) and found.get("step") in STEPS else {}


def clear(data: Path) -> None:
    marker_path(data).unlink(missing_ok=True)


@contextlib.contextmanager
def update_lock(data: Path) -> Iterator[object | None]:
    """``update.lock``, held by this process while it lasts (None when another process holds it: an update runs)."""
    handle = try_lock(service_folder(data) / LOCK)
    try:
        yield handle
    finally:
        if handle is not None:
            handle.close()


def rule(step: str, host_runs_new: bool | None = None) -> str:
    """What is done with an update cut short at ``step``: OLD, BACK or FINISH. ``host_runs_new``: the service host
    deciding (True when it runs the new folder): a switch it can't finish itself is undone."""
    found = RULES.get(step, OLD)
    if step == "switch" and host_runs_new is False:
        return BACK
    return found


def words(state: dict) -> str:
    """``The update from 0.3.0 to 0.4.0, started 2026-11-06 21:05, was cut short while ...``."""
    started = str(state.get("started") or "?")[:16].replace("T", " ")
    return (f"The update from {state.get('from', '?')} to {state.get('to', '?')}, started {started}, was cut short "
            f"{WHAT.get(state.get('step', ''), 'part way')}")  # fmt: skip


def plan_words(state: dict, chosen: str) -> str:
    old, new = state.get("from", "the old version"), state.get("to", "the new version")
    if chosen == FINISH:
        return (f"The update is finished: the database was updated and the new folder checked, so AP Coder {new} "
                "starts (server-update --back goes back to the old version if it turns out wrong).")  # fmt: skip
    if chosen == BACK:
        return (f"The database goes back to the safety copy made before the update (with the service stopped: "
                f"nothing was done since), and AP Coder {old} starts.")  # fmt: skip
    return f"AP Coder {old} starts as it was: its database was not changed."


def remove_check_copy(data: Path) -> None:
    shutil.rmtree(check_folder(data), ignore_errors=True)


def _safety(state: dict, db: Path) -> Path | None:
    """The marker's safety copy, when it is a backup of this database's own folder that is there."""
    named = str(state.get("safety_copy") or "")
    if not named:
        return None
    path = Path(named)
    backups = (db.parent / "backups").resolve()
    try:
        inside = path.resolve().parent == backups
    except OSError:
        return None
    return path if inside and path.is_file() and path.suffix == ".db" else None


def _drop_cut_copy(state: dict, db: Path) -> None:
    """A safety copy cut short (``.part``): never left looking like a backup."""
    label = str(state.get("label") or "")
    if label:
        for part in (db.parent / "backups").glob(f"ap_coder-*-{label}.db.part"):
            part.unlink(missing_ok=True)


def apply(state: dict, chosen: str, data: Path, db: Path, say: Say) -> tuple[str, Path | None]:
    """Do what ``chosen`` says with the database and the record; the folder that must run now (None when it can't
    be told: the folders are gone) and what was done, in words. run.cmd and server.json are switched to it."""
    from . import server_update

    old, new = Path(str(state.get("from_folder") or ".")), Path(str(state.get("to_folder") or "."))
    upgrade = server_update._upgrade()
    safety = _safety(state, db)
    if chosen == FINISH and not upgrade.is_install(new):
        say(f"The new folder {new} is gone: the update can't be finished, so it goes back instead.")
        chosen = BACK
    if chosen == BACK and safety is None:
        say("The safety copy made before the update is not in the backups folder: the database is left as it is.")
    if chosen == BACK and safety is not None:
        files = server_update.put_back(safety, db)
        say(f"The database is back as it was before the update ({safety.name}); the user accounts are kept as they "
            f"are, and everyone logs in again. The files: {files.words()}.")  # fmt: skip
    _drop_cut_copy(state, db)
    remove_check_copy(data)
    target = new if chosen == FINISH else old
    if not upgrade.is_install(target):
        return chosen, None
    server_update.switch_to(data, target)
    entry = {"from": state.get("from", "?"), "to": state.get("to", "?"), "backup": str(safety or "")}
    if chosen == FINISH:
        server_update._record(data, {**entry, "result": f"updated: finished after it was cut short at {state['step']}"},
                              previous_folder=str(old), previous_version=state.get("from", ""),
                              pre_update_backup=str(safety or ""))  # fmt: skip
    else:
        server_update._record(data, {**entry, "result": f"not updated: cut short at {state['step']}"})
    return chosen, target


# --- The service's next start (``service run``) ---------------------------------------------------------------------


def recover_at_start(data: Path, db: Path, here: Path) -> bool:
    """The service host of ``here``, as it starts: a marker left by an update cut short is acted on (``rule``), said
    in the service's log. False when this host must not start the dashboard: run.cmd now names another folder, which
    the task starts next (within 5 minutes), or the database could not be put back (tried again then). Nothing while
    an update runs (its lock is held) or when none was cut short."""
    if not marker_path(data).exists():
        return True
    with update_lock(data) as lock:
        if lock is None:
            return True  # server-update runs: it decides
        state = read(data)
        if not state:
            clear(data)
            return True
        from ..core.paths import same_folder
        from . import service

        log = service.host_logger(data)
        runs_new = same_folder(here, state.get("to_folder") or "")
        chosen = rule(state["step"], host_runs_new=runs_new)
        log.warning("%s. %s", words(state), plan_words(state, chosen))
        try:
            chosen, target = apply(state, chosen, data, db, lambda m: log.warning("%s", m))
        except Exception as exc:  # noqa: BLE001 - said in the log; the marker stays, and is tried again
            log.error("The update cut short could not be dealt with (%s): the service tries again at its next start "
                      "(within 5 minutes); else run server-update again, or restore a backup (docs/SERVER.md section "
                      "9). The marker: %s", exc, marker_path(data))  # fmt: skip
            return chosen != BACK  # never the old version on a database half put back
        clear(data)
        if state["step"] != "prepare":
            service.stop_flag(data).unlink(missing_ok=True)  # the update's own stop, never one made on purpose
        if target is None:
            log.error("Neither %s nor %s is an AP Coder folder any more: the service runs %s as it is.",
                      state.get("from_folder"), state.get("to_folder"), here)  # fmt: skip
            return True
        if same_folder(here, target):
            log.warning("Done: AP Coder %s starts.", state.get("to" if chosen == FINISH else "from"))
            return True
        log.warning("Done: the service runs %s from its next start (the scheduled task starts it within 5 minutes).",
                    target)  # fmt: skip
        return False


# --- The next ``server-update`` (the administrator) -----------------------------------------------------------------


def refusal(state: dict) -> str:
    """Why the administrator's server-update must not act on ``state`` ("" = it may): both folders it names must
    be beside the folder this command runs from (the code folder only administrators can change). The marker is in
    the data folder, which the service's account can write (D-109)."""
    from . import server_update

    for key in ("from_folder", "to_folder"):
        folder = str(state.get(key) or "")
        why = server_update.outside_install(Path(folder)) if folder else f"The update's marker names no {key}."
        if why:
            return f"{why} (The marker: {marker_path_of(state)}.)"
    return ""


def marker_path_of(state: dict) -> str:
    return str(state.get("_marker") or MARKER)


def recover(data: Path, db: Path, server: Any, state: dict, *, dry_run: bool, say: Say) -> int:
    """``server-update`` (or ``--back``) finding an update cut short: it is acted on (``rule``), and nothing else is
    done this time. ``db`` is the database the command was given, never the marker's. 0 when AP Coder runs as it
    should afterwards."""
    chosen = rule(state["step"])
    say(f"{words(state)}.")
    say(plan_words(state, chosen))
    why = refusal({**state, "_marker": str(marker_path(data))})
    if why:
        print(why, file=sys.stderr)
        return 1
    if dry_run:
        say("Nothing was changed (dry run): server-update without --dry-run does it; then run the update again.")
        return 0
    installed = server.installed()
    serving = state["step"] == "prepare"  # the old version was never stopped: it keeps serving
    if installed and not serving:
        server.stop()  # whatever runs now (a new version started before the cut, or a host started since)
    chosen, target = apply(state, chosen, data, db, say)
    clear(data)
    if target is None:
        say(f"Neither {state.get('from_folder')} nor {state.get('to_folder')} is an AP Coder folder any more: "
            "install the bundle again (server-update), or ask IT.")  # fmt: skip
        return 1
    if installed and not serving:
        server.start()
        up = server.up(target)
        if not up and chosen == FINISH:
            say(f"AP Coder {state.get('to')} does not answer: going back to {state.get('from')}.")
            server.stop()
            chosen, target = apply({**state, "step": "going-back"}, BACK, data, db, say)
            if target is not None:
                server.start()
                up = server.up(target)
        if not up:
            say("AP Coder does not answer: python -m ap_coder service status, and the log.")
            return 1
    version = state.get("to") if chosen == FINISH else state.get("from")
    again = "" if chosen == FINISH else " Run server-update again to update."
    say(f"Done: AP Coder {version} runs from {target}.{again}")
    return 0


def started_now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")
