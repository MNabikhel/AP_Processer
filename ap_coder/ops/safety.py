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

Standard library only; nothing is asked of the network.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path, PurePath

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


def serve_refusal(db: Path, server: bool, new_database: bool = False) -> str:
    """Why the dashboard must not start on ``db`` ("" = it may), checked as ``serve`` starts: its folder on a network
    share (the server refuses, a laptop is warned), a database missing where there was one (the server; made new
    with ``new_database``), and a restore's marker left by one that never ended (removed, and said: the service's
    log keeps what the dashboard prints)."""
    from ..core.store.backups import clear_left_restore

    status, words = share_problem(db.parent, server)
    if status == "FAIL":
        return words
    if words:
        print(f"WARNING: {words}", file=sys.stderr)
    missing = missing_database(db.parent, db) if server else ""
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
