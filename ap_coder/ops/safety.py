"""The company server's data folder, checked as the server starts and by the doctor (D-188):

* **Never on a network share.** SQLite's write-ahead log, which AP Coder's database uses, needs the locks and the
  shared memory of one computer's own disk: on a share (SMB, NFS, AFP, WebDAV) they don't hold, and the database can
  be damaged. ``network_share`` tells: a UNC path (``\\\\server\\share``, ``//server/share``), a mapped network drive
  (Windows: ``GetDriveTypeW`` says DRIVE_REMOTE), or a folder of a network file system mounted on a Mac or Linux
  (``os.statvfs`` can't tell: the mount's type, from ``mount`` on a Mac and ``/proc/mounts`` on Linux). The server
  refuses to start there; a laptop is warned, as for a folder OneDrive syncs (the doctor's *data folder* line). A
  folder this machine's host shares with it over 9p (WSL2's /mnt/c, a virtual machine's folder) is one computer, all
  of AP Coder's processes in the one machine: both are warned, never refused (``host_share``, D-188).
* **No silent new database.** On the server, a database file that is missing while the data folder shows it had one
  (its backups, server-update's record in server.json, its write-ahead log, the sign-in codes' key, the access log,
  invoice files) is never replaced by a new, empty one: the server refuses to start and says to restore a backup,
  or ``serve --new-database`` to begin again on purpose. A laptop's first start makes its database, as always.
* **Never opened without sign-in by accident** (D-211). A data folder a server-mode AP Coder serves, or was set up to
  serve (``team_server``: one answers on it now; its database has sign-in accounts; the sign-in codes' key or its
  server.json is there), is not started in laptop mode, by the launcher's double-click or ``serve`` by hand: stopped
  for the night or not. Laptop mode again on the same data is ``serve --without-sign-in``, on purpose, and only while
  the server is stopped. A laptop's own sign-in (D-243: its database's setting says ``laptop``, or ``off``) is not a
  team's: its Admin's account and codes' key tell nothing then (``laptop_sign_in``, ``mark_team``).
* **One laptop-mode AP Coder a data folder** (B-2351). ``serve`` typed by hand while the shortcut's AP Coder runs on
  the same data folder (``open_here``: its server file names a live process, which answers on its port) is refused,
  with where it is open: it would overwrite that one's server file, and ``backup restore`` then go ahead under it.

Standard library only (the store is asked for the accounts where its packages are installed); nothing is asked of the
network (the dashboard of this computer is asked whether it runs).
"""

from __future__ import annotations

import dataclasses
import importlib.util
import json
import os
import re
import socket
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path, PurePath
from typing import Any

WINDOWS = os.name == "nt"
DRIVE_REMOTE = 4  # GetDriveTypeW
# The network file systems a share is mounted as (macOS: smbfs, afpfs, nfs, webdav; Linux: cifs, smb3, nfs, nfs4,
# davfs (fuse.davfs2), sshfs).
NETWORK_TYPES = {"smbfs", "afpfs", "nfs", "nfs4", "cifs", "smb3", "smb2", "webdav", "davfs", "fuse.davfs2",
                 "fuse.sshfs", "sshfs", "ncpfs"}  # fmt: skip
# A folder this machine's host shares with it: WSL2's /mnt/c (the Windows disk of the same computer), a virtual
# machine's folder from its host. One computer, all of AP Coder's processes in the one machine: warned, never refused.
HOST_SHARED = {"9p"}
WHY = ("AP Coder's database (SQLite, with its write-ahead log) can be damaged there: the locks it relies on don't "
       "hold across computers")  # fmt: skip


def _unc(text: str) -> bool:
    """``\\\\server\\share`` or ``//server/share`` (also ``\\\\?\\UNC\\...``), not ``\\\\?\\C:\\`` nor a device."""
    flat = text.replace("/", "\\")
    if not flat.startswith("\\\\"):
        return False
    if flat.startswith(("\\\\?\\", "\\\\.\\")):
        return flat[4:].upper().startswith("UNC\\")
    return len(flat) > 2


def _drive_type(root: str) -> int:
    import ctypes

    return int(ctypes.windll.kernel32.GetDriveTypeW(ctypes.c_wchar_p(root)))  # type: ignore[attr-defined]


def _unescape(field: str) -> str:
    """/proc/mounts writes a space as \\040, a tab \\011, a backslash \\134."""
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), field)


def linux_mounts(text: str) -> list[tuple[str, str]]:
    """(the mount point, its type) of each line of /proc/mounts."""
    found = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) >= 3:
            found.append((_unescape(fields[1]), fields[2]))
    return found


_MAC_LINE = re.compile(r"^.* on (?P<point>/.*?) \((?P<type>[^,)]+)")


def mac_mounts(text: str) -> list[tuple[str, str]]:
    """(the mount point, its type) of each line ``mount`` prints on a Mac (``//ann@nas/ap on /Volumes/ap (smbfs,
    ...)``)."""
    return [(m.group("point"), m.group("type").strip()) for line in text.splitlines() if (m := _MAC_LINE.match(line))]


def _mounts() -> list[tuple[str, str]]:
    try:
        if os.path.exists("/proc/mounts"):
            return linux_mounts(Path("/proc/mounts").read_text(encoding="utf-8", errors="replace"))
        done = subprocess.run(["/sbin/mount"], capture_output=True, text=True, timeout=10, check=False)  # noqa: S603
        return mac_mounts(done.stdout)
    except (OSError, subprocess.SubprocessError):
        return []


def mount_of(folder: str, mounts: list[tuple[str, str]]) -> tuple[str, str]:
    """The mount ``folder`` is on: the longest mount point it is inside (("", "") when none is listed)."""
    best = ("", "")
    for point, kind in mounts:
        base = point.rstrip("/") or "/"
        if (folder == base or folder.startswith(base.rstrip("/") + "/")) and len(base) > len(best[0]):
            best = (base, kind)
    return best


def _written(folder: str | Path, windows: bool) -> str:
    """``folder`` as text in the form of the system it is looked at for (a POSIX path's forward slashes kept when a
    Path is looked at as a POSIX one, whatever this machine is: the tests on Windows)."""
    if not windows and isinstance(folder, PurePath):
        return folder.as_posix()
    return str(folder)


def network_share(folder: str | Path, *, windows: bool = WINDOWS, drive_type: Callable[[str], int] | None = None,
                  mounts: list[tuple[str, str]] | None = None) -> str:  # fmt: skip
    """How ``folder`` is on another computer, in words ("" when it is on this one, or that can't be told)."""
    text = _written(folder, windows).strip().strip('"')
    if windows:
        if _unc(text):
            return f"a network path ({text})"
        drive = text[:2] if len(text) >= 2 and text[1] == ":" else os.path.splitdrive(os.path.abspath(text))[0]
        if len(drive) == 2 and drive[1] == ":":
            try:
                remote = (drive_type or _drive_type)(drive.upper() + "\\") == DRIVE_REMOTE
            except (OSError, AttributeError, ValueError):
                remote = False
            if remote:
                return f"the network drive {drive.upper()} (a share mapped to a letter)"
        return ""
    try:
        real = os.path.realpath(text)
    except OSError:
        return ""
    point, kind = mount_of(real, _mounts() if mounts is None else mounts)
    if kind.lower() in NETWORK_TYPES:
        return f"a network share ({kind}, mounted at {point})"
    return ""


def host_share(folder: str | Path, *, windows: bool = WINDOWS, mounts: list[tuple[str, str]] | None = None) -> str:
    """How ``folder`` is a folder this machine's host shares with it (:data:`HOST_SHARED`), in words ("" when it
    isn't)."""
    if windows:
        return ""
    try:
        real = os.path.realpath(_written(folder, windows).strip().strip('"'))
    except OSError:
        return ""
    point, kind = mount_of(real, _mounts() if mounts is None else mounts)
    return f"a folder this machine's host shares with it ({kind}, mounted at {point})" if kind in HOST_SHARED else ""


def share_problem(data: Path, server: bool, **looks: object) -> tuple[str, str]:
    """(status, words) for the data folder on a network share: ("FAIL", ...) on the server, ("WARN", ...) on a
    laptop, ("", "") when it is on this computer's own disk."""
    how = network_share(data, **looks)  # type: ignore[arg-type]
    windows, mounts = looks.get("windows", WINDOWS), looks.get("mounts")
    shared = "" if how else host_share(data, windows=bool(windows), mounts=mounts)  # type: ignore[arg-type]
    if shared:  # (B-1963)
        return "WARN", (f"The data folder {data} is on {shared}: WSL's /mnt/c, or a virtual machine's folder from its "
                        "host. AP Coder's database is safe there only while nothing outside this machine opens it (no "
                        "other computer, nor a program on the host itself), and it is slower than this machine's own "
                        "disk: keep the data folder there if you can.")  # fmt: skip
    if not how:
        return "", ""
    if server:
        return "FAIL", (f"The data folder {data} is on {how}. {WHY}. The server does not start there: keep the data "
                        "folder on one of the server's own disks (for example D:\\APCoderData, docs/SERVER.md step 2; "
                        "AP_PRIVATE_DIR), and copy the backups to the share instead (Settings → Data & backups → Also "
                        "copy each backup to).")  # fmt: skip
    return "WARN", (f"The data folder {data} is on {how}. {WHY}: keep it on this computer's own disk (the installer "
                    "asks for one), and the backups' second copy on the share.")  # fmt: skip


def signs_of_a_database(data: Path, db: Path) -> list[str]:
    """What in ``data`` shows ``db`` was there once: what was made from it, or for it."""
    signs = []
    if any(db.with_name(db.name + end).exists() for end in ("-wal", "-shm")):
        signs.append(f"its write-ahead log ({db.name}-wal)")
    backups = list((data / "backups").glob("ap_coder-*.db")) if (data / "backups").is_dir() else []
    if backups:
        signs.append(f"{len(backups)} backup{'s' if len(backups) > 1 else ''} in {data / 'backups'}")
    from .service import SettingsError, read_config

    try:
        config = read_config(data)
    except SettingsError:
        config = {}
    if config.get("history") or config.get("pre_update_backup"):
        signs.append("server-update's record in server.json")
    if (data / "authenticator.key").is_file():
        signs.append("the sign-in codes' key (authenticator.key)")
    if (data / "logs" / "access.log").is_file():
        signs.append("the access log (logs\\access.log)" if WINDOWS else "the access log (logs/access.log)")
    invoices = data / "invoices"
    if invoices.is_dir() and any(p.is_file() for p in invoices.rglob("*")):
        signs.append("invoice files in invoices")
    return signs


def missing_database(data: Path, db: Path) -> str:
    """Why the server must not start on ``db`` ("" = it may): the file is missing, and the data folder shows it had
    one. A new, empty database in its place would look like every invoice lost, and the next backups would hold
    nothing (the older ones pruned in two weeks)."""
    if db.exists():
        return ""
    signs = signs_of_a_database(data, db)
    if not signs:
        return ""
    return (f"The database {db} is missing, but this data folder had one ({'; '.join(signs)}). AP Coder does not "
            "start with a new, empty database in its place. Put it back: restore the newest backup (python -m ap_coder "
            "backup list, then backup restore <name>; docs/SERVER.md section 9), or copy the file back if it was "
            "moved. To begin again with a new, empty database on purpose: python -m ap_coder serve --server "
            "--new-database, once.")  # fmt: skip


def lost_database(data: Path, db: Path) -> str:
    """What a laptop is told as it starts on ``db`` ("" = nothing): the file is missing while the data folder still
    has backups. It starts all the same, with a new, empty database (the server alone refuses, D-188)."""
    if db.exists() or not any((data / "backups").glob("ap_coder-*.db")):
        return ""
    signs = "; ".join(signs_of_a_database(data, db))
    return (f"The database {db} was not there, though this data folder had one ({signs}). AP Coder starts with a new, "
            "empty one and shows no invoices; what the backups hold is still there. To get your invoices back, open "
            "Settings → Data & backups and press Restore this state beside the newest backup whose Holds column "
            "shows them.")  # fmt: skip


def serve_refusal(db: Path, server: bool, new_database: bool = False) -> str:
    """Why the dashboard must not start on ``db`` ("" = it may), checked as ``serve`` starts: its folder on a network
    share (the server refuses, a laptop is warned), a database missing where there was one (the server; made new
    with ``new_database``; a laptop whose backups remain starts, and is warned: ``lost_database``), and a restore's
    marker left by one that never ended (removed, and said: the service's log keeps what the dashboard prints)."""
    from ..core.store.backups import clear_left_restore

    status, words = share_problem(db.parent, server)
    if status == "FAIL":
        return words
    if words:
        print(f"WARNING: {words}", file=sys.stderr)
    missing = missing_database(db.parent, db) if server else ""
    if not server and (lost := lost_database(db.parent, db)):
        print(f"WARNING: {lost}", file=sys.stderr)
    if missing and not new_database:
        return missing
    if missing:  # made now: the service starts on it, also when this start stops later (no HTTPS on the console)
        from ..core.store import Store

        Store(db)
        print(f"Made a new, empty database, as asked (--new-database): {db}", file=sys.stderr)
    left = clear_left_restore(db)
    if left:
        print(left, file=sys.stderr)
    return ""


def doctor_checks(data: Path, db: Path, server: bool) -> list[tuple[str, str, str]]:
    """(area, status, detail) for the doctor: the data folder's place, and on the server its database."""
    out = []
    status, words = share_problem(data, server)
    if status or server:  # (a laptop's doctor says it only when it is on a share)
        out.append(("data folder place", status or "PASS", words or "on this computer's own disk"))
    if server:
        missing = missing_database(data, db)
        if missing:
            out.append(("server database", "FAIL", missing))
        else:
            out.append(("server database", "PASS", f"{db}" if db.exists() else "none yet: the first start makes it"))
    return out


# --- A data folder that serves a team: never opened without sign-in by accident (D-211) -------------------------------

WITHOUT_SIGN_IN = "--without-sign-in"  # serve's option (the launcher passes it on): laptop mode again, on purpose


def sign_in_accounts(db: Path) -> int:
    """How many sign-in accounts (server mode's, D-97) the database ``db`` holds (``core/store/users.py``: only read,
    no Store opened on it). 0 also where the store can't be loaded: the launcher's first phase in a new AP Coder
    folder runs before the packages are installed, and is told by the sign-in codes' key instead."""
    try:
        from ..core.store.users import sign_in_accounts as count
    except ImportError:
        return 0
    return count(db)


# A laptop's own sign-in (D-243): the database's setting, by core/store/sign_in.py's words (SIGN_IN, LAPTOP, OFF),
# read without a Store (``sign_in_setting``). With it, the laptop's own account and codes' key are not
# a team's; a start in server mode sets it to "team" (``mark_team``), and D-211 holds again.
SIGN_IN_SETTING = "sign_in"
LAPTOP_SIGN_IN = ("laptop", "off")


def sign_in_setting(db: Path) -> str:
    """The database ``db``'s sign-in setting ("" when it has none, is not there, or can't be read), read without a
    Store (``core/store/peek.py``, loaded by its path: the launcher's first phase can't import the store package)."""
    return _peek(db, SIGN_IN_SETTING)


def _peek(db: Path, key: str) -> str:
    """``core/store/peek.py``'s ``setting``, the module loaded by its path."""
    path = Path(__file__).resolve().parents[1] / "core" / "store" / "peek.py"
    spec = importlib.util.spec_from_file_location("ap_coder_store_peek", path)
    if spec is None or spec.loader is None:
        return ""
    peek = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(peek)
    return peek.setting(db, key)


def laptop_sign_in(store: Any, data: Path) -> bool:
    """Whether a laptop-mode start's Admin signs in (D-243). A data folder that never decided is given sign-in now,
    but only one that shows nothing of a team (no account, no codes' key, no server.json): a team's data opened on
    purpose without sign-in (``--without-sign-in``) is never made a laptop's, which would undo D-211."""
    from ..core.store.sign_in import LAPTOP, OFF, SIGN_IN
    from . import service

    said = store.get_setting(SIGN_IN)
    team = store.user_count() or (data / "authenticator.key").is_file() or service.config_path(data).is_file()
    if not said and not team:
        # scripts/demo.bat's folder (AP_DEMO=1): a presenter's demo, opened without signing in, as the made-up company
        said = OFF if os.environ.get("AP_DEMO", "").strip() == "1" else LAPTOP
        store.set_setting(SIGN_IN, said)
    return said == LAPTOP


def mark_team(store: Any) -> None:
    """A start in server mode: the data folder serves a team from now on (its accounts are D-211's again)."""
    from ..core.store.sign_in import SIGN_IN, TEAM

    if store.get_setting(SIGN_IN) != TEAM:
        store.set_setting(SIGN_IN, TEAM)


@dataclasses.dataclass(frozen=True)
class TeamServer:
    """A data folder a server-mode AP Coder serves, or was set up to serve (:func:`team_server`)."""

    running: bool  # a server-mode AP Coder answers on it now, on this computer
    address: str  # where the team signs in ("": it is stopped, and no server.json says)
    accounts: int  # the sign-in accounts in its database
    settings: str  # its server.json ("": none)
    key: bool = False  # the sign-in codes' key is in the data folder: someone set up their authenticator app
    service: bool = True  # this computer's service is installed (False: a laptop shared with ``share``, D-218)
    unreadable: str = ""  # the key alone tells, and the database can't be read (missing, damaged): its path

    @property
    def signs(self) -> str:
        """What shows it, in words."""
        found = []
        if self.accounts:
            found.append(f"{self.accounts} sign-in account{'s' if self.accounts != 1 else ''} in its database")
        elif self.key:
            found.append("the sign-in codes' key, authenticator.key")
        if self.settings:
            found.append(f"its server settings, {self.settings}")
        return " and ".join(found)

    def refusal(self, on_purpose: bool = False) -> tuple[str, str]:
        """(why AP Coder must not start in laptop mode on this data, what to do: no full stop, it may end with a
        command); ("", "") when it may: only ``on_purpose`` (``--without-sign-in``), and only while the server is
        stopped."""
        if self.running:
            return (f"AP Coder is serving your team from this computer (sign in at {self.address}): it was not "
                    "started a second time.",
                    f"Use it in your browser, at {self.address}. Two AP Coders never use the same data at once"
                    + (f": {WITHOUT_SIGN_IN} works only once the server is stopped" if on_purpose else ""))  # fmt: skip
        if on_purpose:
            return "", ""
        if self.unreadable:  # a laptop signed into (D-243), or a team's: which, its database would have said
            return (f"AP Coder can't read this data folder's database ({self.unreadable}): it is missing or damaged, "
                    "though the folder holds the sign-in codes' key (authenticator.key), so people signed in here. "
                    "It was not started.",
                    "Copy the database back if it was moved, or restore the newest backup: python -m ap_coder backup "
                    "list, then python -m ap_coder backup restore <name>. To open the folder anyway, with no sign-in: "
                    f"python -m ap_coder serve {WITHOUT_SIGN_IN}")  # fmt: skip
        at = f", then sign in at {self.address}" if self.address else ""
        start = "Start the server again (python -m ap_coder service start, or the way it is started here)"
        if not self.service:  # a laptop shared with ``share`` (D-218)
            start = "Start it again with python -m ap_coder share, typed in scripts\\terminal.bat"
        return (f"AP Coder on this computer is set up to serve your team, with sign-in ({self.signs}), and its server "
                "is stopped now: it was not started without sign-in.",
                f"{start}{at}. To open this data in laptop mode again on purpose, with no sign-in for whoever uses "
                f"this computer: python -m ap_coder serve {WITHOUT_SIGN_IN}")  # fmt: skip

    def on_purpose(self) -> str:
        """What a laptop-mode start says once ``--without-sign-in`` let it open this data."""
        return (f"WARNING: opened in laptop mode on purpose ({WITHOUT_SIGN_IN}): this data folder is set up to serve "
                f"a team ({self.signs}), and there is no sign-in on this computer now. The accounts stay in the "
                "database, unused, until AP Coder runs as the server again.")  # fmt: skip


def team_server(data: Path, db: Path) -> TeamServer | None:
    """The server-mode AP Coder the data folder ``data`` (its database ``db``) belongs to; None for a laptop's own.
    Any of these tells: a server-mode AP Coder answers for it now (its server file's port, or server.json's, asked on
    this computer over HTTPS, the certificate not checked, and over HTTP: ``/healthz`` says ``server_mode``); its
    database holds sign-in accounts; the sign-in codes' key (authenticator.key) or its server.json is in the folder.
    All but the first hold while the server is stopped."""
    from . import service
    from .offline import listening

    marker = service.config_path(data)
    try:
        settings = service.settings(data) if marker.is_file() else None
    except service.SettingsError:  # (damaged: still a server's)
        settings = None
    try:
        record = json.loads((data / service.SERVER_FILE).read_text(encoding="utf-8"))
        port = int(record.get("port") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        port = 0
    port = port or (settings.port if settings else 0)
    answer = None
    if port and listening(port):  # (a closed port is told in 0.3 s; nothing names one on a laptop that is not running)
        answer = service.dashboard_answer(data, settings or service.ServerSettings(port=port))
    scheme = answer[0] if answer and answer[1].get("server_mode") else ""
    accounts, key = sign_in_accounts(db), (data / "authenticator.key").is_file()
    if (accounts or key) and sign_in_setting(db) in LAPTOP_SIGN_IN:  # the laptop's own (D-243)
        accounts, key = 0, False
    if not (scheme or accounts or key or marker.is_file()):
        return None
    running = bool(scheme)
    if settings and not running:  # (stopped: its own port, over HTTPS, as the server runs but for a first test)
        scheme, port = "http" if settings.insecure_http else "https", settings.port
    name = settings.allowed_hosts[0] if settings and settings.allowed_hosts else socket.gethostname()
    address = f"{scheme}://{name or '127.0.0.1'}:{port}" if scheme else ""
    installed = running or service.installed(settings or service.ServerSettings())  # (asked only once stopped)
    unreadable = key and not accounts and not running and not marker.is_file() and not _peek(db, "schema_version")
    return TeamServer(running, address, accounts, str(marker) if marker.is_file() else "", key, installed,
                      str(db) if unreadable else "")  # fmt: skip


def open_here(data: Path) -> str:
    """The address of the AP Coder that serves the data folder ``data`` now ("" when none does): its server file
    names a live process other than this one, and the dashboard on the file's port answers as that process (a file
    left by one that was killed, its number since given to another program, is no AP Coder)."""
    from . import service
    from .offline import listening

    try:
        record = json.loads((data / service.SERVER_FILE).read_text(encoding="utf-8"))
        pid, port = int(record.get("pid") or 0), int(record.get("port") or 0)
    except (OSError, ValueError, TypeError, AttributeError):
        return ""
    if pid == os.getpid() or not 0 < port < 65536 or not service.process_alive(pid) or not listening(port):
        return ""
    answer = service.dashboard_answer(data, service.ServerSettings(port=port))
    return f"{answer[0]}://127.0.0.1:{port}" if answer and answer[1].get("pid") == pid else ""
