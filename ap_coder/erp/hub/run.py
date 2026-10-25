"""One run of the finance hub feed, its settings, what it remembers between runs, and its schedule (D-103).

A run reads the six tables from the Store, compares them with what was sent before, and writes only what changed
(new or changed rows as upserts, rows gone as deletes) as one file per table, through the destination chosen:

* **incremental** (the default): ``activity`` by its watermark, the last event id sent (events are only ever added);
  the other tables row by row, each row's fingerprint against the one sent last (an invoice has no "updated at":
  approvals, exports, rechecks and park all change it). A database restored from a backup since (its events end
  below the watermark, or the event there is another) is sent in full again.
* **full** (``--full``, "Full rebuild"): every row again, as upserts, and a delete for each row sent before that is
  gone. A new destination, format or ``HUB_SCHEMA_VERSION`` is a full run by itself.

What was sent is remembered in the data folder (``finance_hub/state.db``), never in the database: a backup put back
does not make the feed forget what Fabric already has. Nothing is remembered from a run that failed, so the next
run sends it again. A run that would send only the record of the previous run sends nothing.

Settings: in the database (``hub_*``, Settings → Finance hub, an Admin's), the client secret in the data folder's
``.env``. Off until an Admin turns it on.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...core.envfile import read_env
from ...core.store.hub import HUB_RUN
from . import landing
from .landing import CSV, DELETE, PARQUET, UPSERT, Destination, FolderDestination, HubError
from .tables import HUB_SCHEMA_VERSION, TABLES, Context, build

log = logging.getLogger(__name__)

FOLDER, ONELAKE = "folder", "onelake"
MODES = (FOLDER, ONELAKE)
FORMATS = ("auto", PARQUET, CSV)
EVERY_HOURS = (0, 1, 4, 12, 24)  # 0: only by hand ("Run now", the command line)
DEFAULT_EVERY, DEFAULT_LINK = 24, "http://127.0.0.1:8501"
SCHEDULE_ACTOR = "AP Coder (schedule)"
SECRET_ENV = "AP_HUB_CLIENT_SECRET"
STATE_DIR, STATE_FILE, LOCK_FILE = "finance_hub", "state.db", "run.lock"
LOCK_STALE_SECONDS = 2 * 3600

# The database's settings (Settings → Finance hub).
KEYS = {
    "enabled": "hub_enabled", "mode": "hub_mode", "folder": "hub_folder", "every_hours": "hub_every_hours",
    "format": "hub_format", "sqlite": "hub_sqlite", "link_base": "hub_link_base", "onelake_url": "hub_onelake_url",
    "tenant_id": "hub_tenant_id", "client_id": "hub_client_id", "authority": "hub_authority",
    "push_opt_in": "hub_push_opt_in",
}  # fmt: skip
LAST_RUN_KEY = "hub_last_run"


def _on(value: str) -> bool:
    return str(value or "").strip().lower() in ("1", "on", "true", "yes")


@dataclass
class HubConfig:
    enabled: bool = False
    mode: str = FOLDER
    folder: str = ""
    every_hours: int = DEFAULT_EVERY
    format: str = "auto"
    sqlite: bool = True
    link_base: str = DEFAULT_LINK
    onelake_url: str = ""
    tenant_id: str = ""
    client_id: str = ""
    authority: str = ""
    push_opt_in: bool = False
    secret: str = field(default="", repr=False)

    @classmethod
    def load(cls, store: Any) -> HubConfig:
        get = store.get_setting
        try:
            every = int(get(KEYS["every_hours"], str(DEFAULT_EVERY)) or DEFAULT_EVERY)
        except ValueError:
            every = DEFAULT_EVERY
        mode, fmt = get(KEYS["mode"], FOLDER), get(KEYS["format"], "auto")
        return cls(
            enabled=_on(get(KEYS["enabled"])), mode=mode if mode in MODES else FOLDER, folder=get(KEYS["folder"]),
            every_hours=every if every in EVERY_HOURS else DEFAULT_EVERY, format=fmt if fmt in FORMATS else "auto",
            sqlite=get(KEYS["sqlite"], "on") != "off", link_base=get(KEYS["link_base"]) or DEFAULT_LINK,
            onelake_url=get(KEYS["onelake_url"]), tenant_id=get(KEYS["tenant_id"]),
            client_id=get(KEYS["client_id"]), authority=get(KEYS["authority"]),
            push_opt_in=_on(get(KEYS["push_opt_in"])), secret=client_secret(store),
        )  # fmt: skip

    def stored(self) -> dict[str, str]:
        """The database's settings for this configuration (not the secret)."""
        return {
            KEYS["enabled"]: "on" if self.enabled else "off", KEYS["mode"]: self.mode, KEYS["folder"]: self.folder,
            KEYS["every_hours"]: str(self.every_hours), KEYS["format"]: self.format,
            KEYS["sqlite"]: "on" if self.sqlite else "off", KEYS["link_base"]: self.link_base,
            KEYS["onelake_url"]: self.onelake_url, KEYS["tenant_id"]: self.tenant_id,
            KEYS["client_id"]: self.client_id, KEYS["authority"]: self.authority,
            KEYS["push_opt_in"]: "on" if self.push_opt_in else "off",
        }  # fmt: skip

    def destination(self) -> Destination:
        if self.mode == ONELAKE:
            from .onelake import DEFAULT_AUTHORITY, OneLakeDestination

            return OneLakeDestination(self.onelake_url, self.tenant_id, self.client_id, self.secret,
                                      authority=self.authority or DEFAULT_AUTHORITY,
                                      opted_in=self.push_opt_in)  # fmt: skip
        if not self.folder.strip():
            raise HubError("Choose the folder the finance hub reads from (Settings → Finance hub).")
        dest = FolderDestination(self.folder.strip())
        dest.check()
        return dest


def data_folder(store: Any) -> Path:
    return Path(store.path).parent


def env_file(store: Any) -> Path:
    """The data folder's ``.env``: where the client secret is kept (git-ignored, never in the code folder)."""
    return data_folder(store) / ".env"


def client_secret(store: Any) -> str:
    return os.environ.get(SECRET_ENV) or read_env(env_file(store)).get(SECRET_ENV, "")


def save_config(store: Any, config: HubConfig, actor: str | None = None, secret: str | None = None) -> list[str]:
    """Save what changed (``secret``: a new client secret, None to keep it); logged once as ``settings_changed`` with
    the names of what changed, never a value of the secret. Returns the keys changed."""
    from ...core.envfile import write_env

    changed = [k for k, v in config.stored().items() if store.get_setting(k, "") != v]
    for key in changed:
        store.set_setting(key, config.stored()[key])
    if secret is not None and secret != client_secret(store):
        write_env(env_file(store), {SECRET_ENV: secret})
        os.environ.pop(SECRET_ENV, None)  # the file is now what counts
        changed.append(SECRET_ENV)
    if changed:
        store.log_event("settings_changed", actor=actor, detail={"keys": sorted(changed), "finance_hub": True})
    return changed


def last_run(store: Any) -> dict[str, Any] | None:
    try:
        found = json.loads(store.get_setting(LAST_RUN_KEY) or "null")
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


# --- What was sent --------------------------------------------------------------------------------------------------


class State:
    """``finance_hub/state.db`` in the data folder: each row's fingerprint as sent, the watermark, the file numbers."""

    def __init__(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(folder / STATE_FILE, timeout=30)
        self.conn.executescript(
            "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
            "CREATE TABLE IF NOT EXISTS sent (tbl TEXT NOT NULL, key TEXT NOT NULL, hash TEXT NOT NULL, "
            "PRIMARY KEY (tbl, key));"
        )

    def get(self, key: str, default: str = "") -> str:
        row = self.conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row[0] if row else default

    def set(self, key: str, value: Any) -> None:
        self.conn.execute("INSERT INTO kv VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                          (key, str(value)))  # fmt: skip

    def sent(self, table: str) -> dict[str, str]:
        return dict(self.conn.execute("SELECT key, hash FROM sent WHERE tbl = ?", (table,)))

    def forget(self, everything: bool = True) -> None:
        """Forget what was sent (a new destination, format or schema version); the file numbers too for a new
        destination (the same folder keeps counting up)."""
        self.conn.execute("DELETE FROM sent")
        self.conn.execute("DELETE FROM kv" if everything else "DELETE FROM kv WHERE key NOT LIKE 'seq:%'")

    def record(self, table: str, upserts: dict[str, str], deletes: list[str]) -> None:
        self.conn.executemany(
            "INSERT INTO sent VALUES (?, ?, ?) ON CONFLICT(tbl, key) DO UPDATE SET hash = excluded.hash",
            [(table, k, h) for k, h in upserts.items()],
        )
        self.conn.executemany("DELETE FROM sent WHERE tbl = ? AND key = ?", [(table, k) for k in deletes])

    def close(self) -> None:
        self.conn.close()


def _key(row: dict[str, Any], key: tuple[str, ...]) -> str:
    return "|".join(str(row.get(k)) for k in key)


def _fingerprint(row: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(row, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:20]


@contextlib.contextmanager
def _run_lock(folder: Path):
    """One run at a time for this data folder, also between the dashboard and the command line."""
    folder.mkdir(parents=True, exist_ok=True)
    lock = folder / LOCK_FILE
    with contextlib.suppress(OSError):
        if time.time() - lock.stat().st_mtime > LOCK_STALE_SECONDS:
            lock.unlink()  # left by a run that crashed
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise HubError("A finance hub run is already under way: wait for it to finish.") from exc
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        with contextlib.suppress(OSError):
            lock.unlink()


# --- A run ------------------------------------------------------------------------------------------------------------


@dataclass
class Result:
    ok: bool
    mode: str = ""
    format: str = ""
    full: bool = False
    dry_run: bool = False
    tables: dict[str, dict[str, int]] = field(default_factory=dict)  # {table: {"upserts": n, "deletes": n}}
    files: list[str] = field(default_factory=list)
    seconds: float = 0.0
    error: str = ""
    target: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def rows(self) -> int:
        return sum(t["upserts"] + t["deletes"] for t in self.tables.values())

    def summary(self) -> str:
        if not self.ok:
            return f"The finance hub run failed: {self.error}"
        if not self.rows:
            return "Nothing new for the finance hub: nothing was sent."
        parts = ", ".join(f"{name} {t['upserts'] + t['deletes']:,}" for name, t in self.tables.items()
                          if t["upserts"] + t["deletes"])  # fmt: skip
        verb = "would send" if self.dry_run else "sent"
        kind = "Full rebuild" if self.full else "Changes"
        said = f"{kind}: {verb} {self.rows:,} rows ({parts}) as {self.format.upper()} to {self.target}."
        return " ".join([said, *self.notes])


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def _own_record(name: str, row: dict[str, Any]) -> bool:
    """A row that is only the record of a hub run itself (sent with the next real change, never alone)."""
    return (name == "activity" and row.get("action") == HUB_RUN) or (
        name == "workflow_runs" and row.get("kind") == "hub_export"
    )


def export(
    store: Any, config: HubConfig | None = None, *, full: bool = False, dry_run: bool = False,
    actor: str | None = None, destination: Destination | None = None,
) -> Result:  # fmt: skip
    """One run of the feed (see the module docstring). ``destination``: instead of the configured one (tests).
    A dry run reads and compares, and writes, sends and records nothing. Never raises for a run that failed: the
    result says why (and the run is in the audit trail)."""
    config = config or HubConfig.load(store)
    started, clock = _now(), time.monotonic()
    result = Result(ok=False, mode=config.mode, full=full, dry_run=dry_run)
    folder = data_folder(store) / STATE_DIR
    try:
        with _run_lock(folder):
            dest = destination or (_dry_destination(config) if dry_run else config.destination())
            result.target = dest.describe()
            result.mode = getattr(dest, "kind", config.mode)
            result.format = landing.choose_format(config.format)
            _run(store, config, dest, result, folder, started)
        result.ok = True
    except (HubError, OSError, sqlite3.Error) as exc:
        result.error = str(exc) if isinstance(exc, HubError) else f"{type(exc).__name__}: {exc}"
        log.warning("finance hub run failed: %s", result.error)
    result.seconds = round(time.monotonic() - clock, 1)
    if not dry_run:
        _record(store, result, started, actor)
    return result


def _dry_destination(config: HubConfig) -> Destination:
    """Where a dry run would write, made without touching it (no folder made, nothing asked of OneLake)."""
    if config.mode == ONELAKE:
        return config.destination()
    return FolderDestination(config.folder.strip()) if config.folder.strip() else _Nowhere()


class _Nowhere(landing.MemoryDestination):
    """A dry run's destination before a folder is chosen."""

    def describe(self) -> str:
        return "the finance hub (no folder chosen yet)"


def _run(store: Any, config: HubConfig, dest: Destination, result: Result, folder: Path, started: str) -> None:
    state = State(folder)
    try:
        full = result.full
        fresh = (state.get("destination") != dest.ident() or state.get("format") != result.format
                 or state.get("schema_version") != str(HUB_SCHEMA_VERSION))  # fmt: skip
        watermark = int(state.get("watermark", "0") or 0)
        last_id, _ = store.hub_last_event()
        restored = watermark > last_id or (watermark and store.hub_event_stamp(watermark) != state.get("stamp"))
        if fresh or restored:
            full = True
        result.full = full

        rows = store.hub_invoice_rows()
        ctx = Context(link_base=config.link_base, vendor_ids=store.vendor_ids(),
                      batches={int(b["id"]): b for b in store.export_batches()},
                      currencies={int(r["id"]): (r.get("currency") or "").upper() for r in rows})  # fmt: skip
        built: dict[str, list[dict[str, Any]]] = {
            "invoices": build(store, "invoices", ctx, rows=rows),
            "invoice_lines": build(store, "invoice_lines", ctx, rows=rows),
            "approvals": build(store, "approvals", ctx),
            "accuracy": build(store, "accuracy", ctx, rows=rows),
            "workflow_runs": build(store, "workflow_runs", ctx),
        }
        built["activity"] = [r for r in build(store, "activity", ctx, after_id=0) if r["event_id"] <= last_id]

        changes: dict[str, tuple[list[dict[str, Any]], list[str], dict[str, str]]] = {}
        for name, table in TABLES.items():
            if table.changes == "events" and not full:
                new = [r for r in built[name] if r["event_id"] > watermark]
                changes[name] = (new, [], {})
                continue
            now = {_key(r, table.key): (r, _fingerprint(r)) for r in built[name]}
            # What this destination holds: a new format or schema version at the same destination still sends a delete
            # for each row sent there before and gone since (a new destination holds nothing).
            before = {} if state.get("destination") != dest.ident() else state.sent(name)
            upserts = [r for k, (r, h) in now.items() if full or before.get(k) != h]
            deletes = [k for k in before if k not in now]
            changes[name] = (upserts, deletes, {k: h for k, (_, h) in now.items()})
        if full and restored and watermark > last_id:
            # Events sent before the backup was put back, gone now: deleted (their ids come again).
            gone = [str(i) for i in range(last_id + 1, watermark + 1)]
            changes["activity"] = (changes["activity"][0], gone, {})

        real = any(any(not _own_record(name, r) for r in ups) or dels for name, (ups, dels, _) in changes.items())
        for name, (ups, dels, _) in changes.items():
            result.tables[name] = {"upserts": len(ups), "deletes": len(dels)} if (real or full) else \
                {"upserts": 0, "deletes": 0}  # fmt: skip
        if result.dry_run or not (real or full):
            return

        if fresh:
            state.forget(everything=state.get("destination") != dest.ident())
        for name, table in TABLES.items():
            ups, dels, prints = changes[name]
            existing = dest.list(name)
            if full or landing.METADATA_FILE not in existing:  # every table's folder, rows or none yet
                dest.write(f"{name}/{landing.METADATA_FILE}", landing.metadata(table, result.format))
                dest.write(f"{name}/{landing.SCHEMA_FILE}", landing.schema_json(table, result.format))
            if not ups and not dels:
                continue
            seq = max(int(state.get(f"seq:{name}", "0") or 0), landing.last_sequence(existing))
            marked = [(r, UPSERT) for r in ups] + [(_delete_row(table, k), DELETE) for k in dels]
            for start in range(0, len(marked), landing.ROWS_PER_FILE):
                chunk = marked[start : start + landing.ROWS_PER_FILE]
                seq += 1
                path = f"{name}/{landing.sequenced_name(seq, result.format)}"
                dest.write(path, landing.encode(table, [r for r, _ in chunk], [m for _, m in chunk], result.format))
                result.files.append(path)
            state.set(f"seq:{name}", seq)
            if table.changes == "events":
                state.record(name, {}, [])
            else:
                kept = {_key(r, table.key): prints[_key(r, table.key)] for r in ups}
                state.record(name, kept, dels)
        info = {"at": started, "rows": result.rows, "full": full, "files": len(result.files)}
        dest.write(landing.HUB_FILE, landing.hub_json(result.format, info))
        if isinstance(dest, FolderDestination) and config.sqlite and config.mode == FOLDER:
            _gateway(dest, built, started, result)
        state.set("destination", dest.ident())
        state.set("format", result.format)
        state.set("schema_version", HUB_SCHEMA_VERSION)
        state.set("watermark", last_id)
        state.set("stamp", store.hub_event_stamp(last_id) if last_id else "")
        state.conn.commit()
    finally:
        state.close()


def _gateway(dest: FolderDestination, built: dict[str, list[dict[str, Any]]], started: str, result: Result) -> None:
    """The gateway's SQLite copy, after the tables went out: whatever goes wrong making or placing it is a warning of
    the run (the next run tries again), never a failed run (the data files are written and recorded)."""
    made: Path | None = None
    try:
        made = landing.gateway_database(built, started)
        dest.put_file(f"{landing.GATEWAY_DIR}/{landing.GATEWAY_FILE}", made)
        result.files.append(f"{landing.GATEWAY_DIR}/{landing.GATEWAY_FILE}")
    except Exception as exc:  # noqa: BLE001 - e.g. held open by the gateway (Windows), the disk full, a value SQLite refuses
        why = getattr(exc, "strerror", None) or str(exc) or type(exc).__name__
        result.notes.append(f"Warning: the gateway's SQLite copy was not replaced ({why}); the tables were sent, and "
                            "the next run tries again.")  # fmt: skip
        log.warning("finance hub: the gateway's SQLite copy was not made: %s", why)
    finally:
        if made is not None:
            shutil.rmtree(made.parent, ignore_errors=True)


def _delete_row(table: Any, key: str) -> dict[str, Any]:
    values = key.split("|")
    row: dict[str, Any] = {c: None for c in table.names}
    for column, value in zip(table.key, values, strict=False):
        kind = next(c.type for c in table.columns if c.name == column)
        row[column] = int(value) if kind == "int64" and value.lstrip("-").isdigit() else value
    return row


def _record(store: Any, result: Result, started: str, actor: str | None) -> None:
    """The run in the audit trail (``hub_export``: a run that sent something, or failed) and as the last run."""
    actor = actor or "AP Coder"
    summary = {"at": started, "ok": result.ok, "mode": result.mode, "format": result.format, "full": result.full,
               "rows": result.rows, "files": len(result.files), "seconds": result.seconds, "error": result.error,
               "by": actor, "target": result.target, "summary": result.summary()}  # fmt: skip
    store.set_setting(LAST_RUN_KEY, json.dumps(summary))
    if result.rows or not result.ok:
        store.log_event(HUB_RUN, actor=actor, detail={
            "mode": result.mode, "format": result.format, "full": result.full, "rows": result.rows,
            "files": len(result.files), "tables": result.tables, "started_at": started, "seconds": result.seconds,
            "ok": result.ok, **({"error": result.error} if result.error else {}),
        })  # fmt: skip


# --- The schedule -------------------------------------------------------------------------------------------------


def due(config: HubConfig, last: dict[str, Any] | None, now: dt.datetime | None = None) -> bool:
    """Whether the schedule calls for a run: on, every N hours (0: only by hand), N hours after the last run."""
    if not config.enabled or not config.every_hours:
        return False
    if not last or not last.get("at"):
        return True
    try:
        when = dt.datetime.fromisoformat(str(last["at"]))
    except ValueError:
        return True
    now = now or dt.datetime.now()
    return now - when >= dt.timedelta(hours=config.every_hours) or when > now + dt.timedelta(minutes=5)


class HubSchedule:
    """The feed on its schedule inside the dashboard, as the daily backup is (``web/app.py`` ``DailyBackup``): each
    page served lets it look, at most every ``every`` seconds, whether a run is due, and run it on a thread of its
    own. A dashboard nobody opens runs nothing: the command line (``hub export``) in a scheduled task covers that."""

    def __init__(self, store: Any, every: float = 60) -> None:
        self.store, self.every = store, every
        self.last = time.monotonic()  # the first look a minute after the start
        self.thread: threading.Thread | None = None
        self._lock = threading.Lock()

    def check(self) -> None:
        now = time.monotonic()
        with self._lock:
            if now - self.last < self.every or (self.thread is not None and self.thread.is_alive()):
                return
            self.last = now
            self.thread = threading.Thread(target=self._run, name="ap-coder-finance-hub", daemon=True)
            self.thread.start()

    def _run(self) -> None:
        try:
            config = HubConfig.load(self.store)
            if due(config, last_run(self.store)):
                export(self.store, config, actor=SCHEDULE_ACTOR)
        except Exception:  # noqa: BLE001 - the feed must never reach a page
            log.warning("the finance hub run was not made", exc_info=True)
