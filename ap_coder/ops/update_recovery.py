"""An update of the company server cut short (a power cut, the window closed, the server restarted): what
``server-update`` was doing is in ``<data folder>/service/update.json`` (D-188), and the next ``server-update`` or the
service's next start acts on it.

The marker says where the update was: ``from`` and ``to`` (the versions), their folders, the database, the safety copy
once made, the step under way and when it started. It is written whole at each step (a new file renamed over the old
one, flushed to the disk first), and removed once the update is recorded. While ``server-update`` runs it holds
``update.lock`` (the operating system's lock: let go when the process ends, killed or not), so a marker whose lock is
free is one left behind.

What is done with it, decided per step (:data:`RULES`):

* ``prepare`` (unzip, set up, a copy of the database updated, the self-check, the safety copy made ahead: D-196),
  ``stop`` and ``safety-copy`` (that copy brought up to date): the old version still has its own database, untouched
  (the copy only reads it): it just starts. The new folder is left (the next ``server-update`` unzips beside it;
  delete it when done) and a safety copy cut short is removed, with all SQLite left beside it.
* ``migrate``: the new version may have updated part of the database: the safety copy (brought up to date with the
  service stopped, so nothing was done since) is put back and the old version starts.
* ``switch``: the database is updated and the new folder checked; the switch is finished (the new version starts).
  The service host finishes it only when it runs the new folder already (run.cmd was rewritten), else it goes back.
* ``start``: the switch was made: the new version starts (``server-update --back`` goes back if it turns out wrong;
  ``server-update`` itself goes back when it doesn't answer).
* ``going-back``: an update going back on its own (the new version did not answer): it goes back.

Once put back, the marker says so (``put_back``): it is never put back twice, and only going back is left.

The service host runs the folder run.cmd names (the Mac's ``current`` link). When that is the folder the rule starts,
it switches nothing. When it is another, it rewrites run.cmd and ends: the scheduled task's repeating trigger starts
the right one within 5 minutes. The Mac's host can't change the link (root's, beside the code folders): then it
changes nothing at all, keeps the marker and ends with 0 (launchd leaves it stopped), its log saying to run
server-update (which finishes or undoes it). Neither does it act when it can't open ``update.lock`` (which can't
tell an update cut short from one running): it goes on as without a marker, so the old version serves at ``prepare``
and the update's own ``stopped`` file keeps it stopped after. ``update.lock`` and the marker made by root's sudo are
the service folder owner's (the service's account), so its host can open them.
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


def _owned(fd: int, folder: Path) -> None:
    """A file root has just made in the service folder (``fd``, open) given to the folder's owner, when that is
    another account: on a Mac, ``sudo server-update`` makes update.lock and update.json, and the service host, which
    runs as the service's account, must open them after a power cut (a Mac's file is its maker's, B-1956)."""
    from . import service_macos

    if not service_macos.is_root():
        return
    found = os.stat(folder)
    if found.st_uid != service_macos.ROOT_UID:
        os.fchown(fd, found.st_uid, found.st_gid)


def write(data: Path, state: dict) -> None:
    """The marker, whole: written beside it, flushed to the disk, then renamed over it (never half a file). Made by
    root, it is the service folder owner's (``_owned``)."""
    path = marker_path(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    part = path.with_name(f"{path.name}.{os.getpid()}.part")
    part.unlink(missing_ok=True)
    with open(part, "x", encoding="utf-8") as f:  # (made now: never a file or a link left under that name)
        _owned(f.fileno(), path.parent)
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
    """``update.lock``, held by this process while it lasts (None when another process holds it: an update runs).
    Made by root, it is the service folder owner's (``_owned``); one there already is left as it is."""
    path = service_folder(data) / LOCK
    path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.suppress(FileExistsError), open(path, "xb") as made:
        _owned(made.fileno(), path.parent)
    handle = try_lock(path)
    try:
        yield handle
    finally:
        if handle is not None:
            handle.close()


def rule(step: str, host_runs_new: bool | None = None, put_back: bool = False) -> str:
    """What is done with an update cut short at ``step``: OLD, BACK or FINISH. ``host_runs_new``: the service host
    deciding (True when it runs the new folder): a switch it can't finish itself is undone. ``put_back``: the marker
    says the safety copy was put back already (by a start whose next step failed): only back, then."""
    found = RULES.get(step, OLD)
    if put_back or (step == "switch" and host_runs_new is False):
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
    """A safety copy cut short (``.part``, and SQLite's ``-journal``, ``-wal``, ``-shm`` beside it, B-2035; one made
    ahead, D-196, its list of files too): never left looking like a backup, nor left at all."""
    label = str(state.get("label") or "")
    if label:
        backups = db.parent / "backups"
        for part in [*backups.glob(f"ap_coder-*-{label}.db.part*"), *backups.glob(f"ap_coder-*-{label}.db.files.json")]:
            part.unlink(missing_ok=True)


def plan(state: dict, chosen: str) -> tuple[str, Path | None]:
    """``chosen`` once the folders are looked at (an update to finish whose new folder is gone goes back instead),
    and the folder that must run then (None when it is gone too)."""
    from . import server_update

    old, new = Path(str(state.get("from_folder") or ".")), Path(str(state.get("to_folder") or "."))
    upgrade = server_update._upgrade()
    if chosen == FINISH and not upgrade.is_install(new):
        chosen = BACK
    target = new if chosen == FINISH else old
    return chosen, target if upgrade.is_install(target) else None


def apply(state: dict, chosen: str, data: Path, db: Path, say: Say, switch: bool = True) -> tuple[str, Path | None]:
    """Do what ``chosen`` says with the database and the record; the folder that must run now (None when it can't
    be told: the folders are gone) and what was done, in words. run.cmd (the Mac's link) and server.json are
    switched to it; only server.json when ``switch`` is False (the service host runs that folder already). Once the
    safety copy is put back the marker says so, and it is never put back again (``rule``)."""
    from . import server_update, service

    old, new = Path(str(state.get("from_folder") or ".")), Path(str(state.get("to_folder") or "."))
    safety = _safety(state, db)
    wanted, (chosen, target) = chosen, plan(state, chosen)
    if wanted == FINISH and chosen == BACK:
        say(f"The new folder {new} is gone: the update can't be finished, so it goes back instead.")
    if chosen == BACK and state.get("put_back"):
        say(f"The database was put back already ({state['put_back']}), at an earlier start.")
    elif chosen == BACK and safety is None:
        say("The safety copy made before the update is not in the backups folder: the database is left as it is.")
    elif chosen == BACK and safety is not None:
        files = server_update.put_back(safety, db)
        write(data, {**state, "put_back": safety.name})  # (a step below that fails is tried again: this is not)
        say(f"The database is back as it was before the update ({safety.name}); the user accounts are kept as they "
            f"are, and everyone logs in again. The files: {files.words()}.")  # fmt: skip
    _drop_cut_copy(state, db)
    remove_check_copy(data)
    if target is None:
        return chosen, None
    if switch:
        server_update.switch_to(data, target)
    else:
        service.write_config(data, folder=str(target))
    entry = {"from": state.get("from", "?"), "to": state.get("to", "?"), "backup": str(safety or "")}
    if chosen == FINISH:
        server_update._record(data, {**entry, "result": f"updated: finished after it was cut short at {state['step']}"},
                              previous_folder=str(old), previous_version=state.get("from", ""),
                              pre_update_backup=str(safety or ""))  # fmt: skip
    else:
        server_update._record(data, {**entry, "result": f"not updated: cut short at {state['step']}"})
    return chosen, target


# --- The service's next start (``service run``) ---------------------------------------------------------------------


ASK = ("Run server-update again as an administrator (with sudo on a Mac): it finishes the update cut short or undoes "
       "it, then starts the service.")  # fmt: skip


def recover_at_start(data: Path, db: Path, here: Path) -> int | None:
    """The service host of ``here``, as it starts: a marker left by an update cut short is acted on (``rule``), said
    in the service's log. None: the host goes on (it starts the dashboard, unless the service is stopped). Else the
    exit code it ends with: ``service.again()`` when run.cmd now names another folder, which the task starts next
    (within 5 minutes), or the database could not be put back (tried again then); 0 when the folder to run is another
    that this host can't switch to (the Mac's ``current`` link is root's): nothing is changed, and the service stays
    stopped until the administrator's server-update. Nothing either while an update runs (its lock is held), when
    none was cut short, or when that can't be told (the lock can't be opened)."""
    if not marker_path(data).exists():
        return None
    from . import service

    log = service.host_logger(data)
    try:
        lock = try_lock(service_folder(data) / LOCK)
    except OSError as exc:  # (a Mac: made by root's sudo before B-1956, and no service start since to hand it back)
        log.error("An update was cut short, or runs now (%s), and which can't be told: %s can't be opened (%s). "
                  "Nothing was changed, and AP Coder starts only if the update had not stopped it. %s",
                  marker_path(data), LOCK, exc, ASK)  # fmt: skip
        return None
    if lock is None:
        return None  # server-update runs: it decides
    try:
        return _recover_at_start(data, db, here, log)
    finally:
        lock.close()


def _recover_at_start(data: Path, db: Path, here: Path, log: Any) -> int | None:
    from ..core.paths import same_folder
    from . import service

    state = read(data)
    if not state:
        clear(data)
        return None
    runs_new = same_folder(here, state.get("to_folder") or "")
    wanted = rule(state["step"], host_runs_new=runs_new, put_back=bool(state.get("put_back")))
    chosen, target = plan(state, wanted)
    switch = target is not None and not same_folder(here, target)
    if switch and not service.can_switch(data, target):
        log.error("%s. AP Coder %s must run now (%s), and this service can't switch to it itself (it runs %s): nothing "
                  "was changed, and the service stays stopped. %s", words(state),
                  state.get("to" if chosen == FINISH else "from"), target, here, ASK)  # fmt: skip
        return 0
    log.warning("%s. %s", words(state), plan_words(state, chosen))
    try:
        chosen, target = apply(state, wanted, data, db, lambda m: log.warning("%s", m), switch=switch)
    except Exception as exc:  # noqa: BLE001 - said in the log; the marker stays, and is acted on at the next start
        ready = not switch and (chosen != BACK or bool(read(data).get("put_back")))  # never a database half put back
        if not ready and isinstance(exc, PermissionError):  # (a file root's sudo made: no use trying again)
            log.error("The update cut short could not be dealt with (%s), and this service's account can't do it: "
                      "the service stays stopped. %s The marker: %s", exc, ASK, marker_path(data))  # fmt: skip
            return 0
        if not ready:
            log.error("The update cut short could not be dealt with (%s): the service tries again at its next start; "
                      "else run server-update again, or restore a backup (docs/SERVER.md section 9). The marker: %s",
                      exc, marker_path(data))  # fmt: skip
            return service.again()
        log.error("The update cut short could not all be dealt with (%s), but the database is as the folder this "
                  "service runs needs it: AP Coder starts, and the rest is done at its next start. The marker: %s",
                  exc, marker_path(data))  # fmt: skip
        if state["step"] != "prepare":
            service.stop_flag(data).unlink(missing_ok=True)  # the update's own stop
        return None
    clear(data)
    if state["step"] != "prepare":
        service.stop_flag(data).unlink(missing_ok=True)  # the update's own stop, never one made on purpose
    if target is None:
        log.error("Neither %s nor %s is an AP Coder folder any more: the service runs %s as it is.",
                  state.get("from_folder"), state.get("to_folder"), here)  # fmt: skip
        return None
    if not switch:
        log.warning("Done: AP Coder %s starts.", state.get("to" if chosen == FINISH else "from"))
        return None
    log.warning("Done: the service runs %s from its next start (the scheduled task starts it within 5 minutes).",
                target)  # fmt: skip
    return service.again()


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
    chosen = rule(state["step"], put_back=bool(state.get("put_back")))
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
            back = {**state, "step": "going-back"}
            write(data, back)  # (a power cut while it goes back: the next start goes back, B-1958)
            server.stop()
            chosen, target = apply(back, BACK, data, db, say)
            clear(data)
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
