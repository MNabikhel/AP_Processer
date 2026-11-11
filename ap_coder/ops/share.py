"""``python -m ap_coder share``: let a few colleagues use this laptop's AP Coder, with sign-in (D-218).

One command for docs/SERVER.md section 16, the prototype's way. It checks what sharing needs and says each missing
thing in plain words with its next step; what needs no administrator rights it does itself (the self-signed
certificate, when this computer has ``openssl``; the first Admin's account; the server settings); then it runs AP
Coder as the server (``serve --server``: sign-in, HTTPS) on this laptop's own data folder, until its window is closed
or ``share --stop``. It never starts without sign-in, and never changes Windows' firewall: that rule needs
administrator rights, so it is said, with its command, not made. Standard library only.
"""

from __future__ import annotations

import argparse
import dataclasses
import os
import shutil
import socket
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..core.paths import PROJECT_DIR, env_path, private_dir
from . import service

TLS_DIR = "tls"  # in the data folder, as docs/SERVER.md section 3 has it
CERT_NAME, KEY_NAME = "apcoder.pem", "apcoder.key.pem"
CERT_DAYS = 825
WORKERS = 2  # web workers when server.json names none: a few people, on a laptop that also reads the invoices
CAPTURE_PROCESSES = 2  # processes reading the imports when nothing names a number: the laptop is its owner's too
RULE = "AP Coder"  # the firewall rule's name in docs/SERVER.md
GUIDE = "docs/SERVER.md section 16"
# Where Git for Windows keeps the openssl it brings, when it is not on the PATH.
GIT_OPENSSL = ("Git/usr/bin/openssl.exe", "Git/mingw64/bin/openssl.exe")

Say = Callable[[str], None]


def names() -> list[str]:
    """The names colleagues may type for this computer: its own, and its full name where the office DNS gives one."""
    own = socket.gethostname().strip()
    full = socket.getfqdn().strip()
    found = [own] if own else []
    if full and full.lower() != own.lower() and full.lower().startswith(own.lower() + "."):
        found.append(full)
    return found or ["localhost"]


def find_openssl() -> str:
    """This computer's ``openssl`` ("" when it has none): on the PATH, or the one Git for Windows brings."""
    found = shutil.which("openssl")
    if found:
        return found
    roots = [os.environ.get(name) for name in ("ProgramFiles", "ProgramFiles(x86)", "LocalAppData")]
    for root in filter(None, roots):
        for place in (*GIT_OPENSSL, *(f"Programs/{p}" for p in GIT_OPENSSL)):
            if (Path(root) / place).is_file():
                return str(Path(root) / place)
    return ""


def certificate_command(openssl: str, folder: Path) -> list[str]:
    """The self-signed certificate of docs/SERVER.md section 3, for this computer's names."""
    listed = names()
    return [openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", str(CERT_DAYS),
            "-subj", f"/CN={listed[0]}", "-addext", "subjectAltName=" + ",".join(f"DNS:{n}" for n in listed),
            "-keyout", str(folder / KEY_NAME), "-out", str(folder / CERT_NAME)]  # fmt: skip


def certificate(data: Path, s: service.ServerSettings, say: Say, run: Callable[..., Any] = service.run_command,
                openssl: str | None = None) -> tuple[str, str] | None:  # fmt: skip
    """The certificate and its private key to serve with: server.json's, else the data folder's ``tls`` files, else
    one made now with this computer's openssl (also when server.json names those two files and they were deleted:
    the way to a certificate for a laptop's new name, B-2313). None when there is none and none could be made: said,
    with how."""
    folder = data / TLS_DIR
    cert, key = folder / CERT_NAME, folder / KEY_NAME
    if (s.certfile or s.keyfile) and (Path(s.certfile), Path(s.keyfile)) != (cert, key):
        missing = [f for f in (s.certfile, s.keyfile) if not f or not Path(f).is_file()]
        if not missing:
            return s.certfile, s.keyfile
        say(
            "The certificate: server.json names files that are not there ("
            + ", ".join(f or "(none)" for f in missing)
            + f"). Put them back, or take certfile and keyfile out of {service.config_path(data)} and run this again."
        )
        return None
    if cert.is_file() and key.is_file():
        return str(cert), str(key)
    openssl = find_openssl() if openssl is None else openssl
    if not openssl:
        say(
            "The certificate: there is none yet, and this computer has no openssl to make one (Windows has no tool "
            "of its own that writes the two files). Either install Git for Windows, which brings openssl (on most "
            "company laptops installing it needs administrator rights: ask IT), and run this again; or make the two "
            f"files on any Mac or Linux computer and copy them into {folder}:"
        )
        say("  " + service.shown(certificate_command("openssl", Path("."))))
        return None
    folder.mkdir(parents=True, exist_ok=True)
    done = run(certificate_command(openssl, folder), capture_output=True)
    if done.returncode != 0 or not (cert.is_file() and key.is_file()):
        say(
            f"The certificate could not be made ({(done.stderr or '').strip()[-300:] or 'openssl gave no reason'}). "
            "docs/SERVER.md section 3 (Without a company CA) says how to make it by hand."
        )
        return None
    if not service.WINDOWS:
        os.chmod(key, 0o600)
    say(
        f"Made a certificate for this computer's name ({', '.join(names())}), good for {CERT_DAYS} days: {cert}. "
        "Nobody signed it, so each colleague's browser warns (“Your connection isn't private”) in each new browser "
        f"session: Advanced, then Continue. {GUIDE} says how to trust it on their PCs, so that it no longer warns "
        "(give them this file, never the key file beside it), and what to do if this laptop's name changes."
    )
    return str(cert), str(key)


def username_from(name: str) -> str:
    """A username made from a person's name: "Pat Lee" gives pat.lee."""
    from ..core.store.users import USERNAME, fold_username

    made = ".".join("".join(c for c in part if c.isalnum() or c in "-_") for part in fold_username(name).split())
    made = made.strip(".")
    return made if USERNAME.fullmatch(made) else ""


def first_admin(db: Path, say: Say, ask: Callable[[str], str] | None = None,
                password: Callable[[str, str], str | None] | None = None) -> bool:  # fmt: skip
    """True once the database has a sign-in account. With none, the first Admin is made here, the name, username and
    password asked for (``ask``, ``password``: the console's); with no console to ask on, said with its command."""
    from ..core.paths import read_user_settings
    from ..core.store.users import sign_in_accounts

    if sign_in_accounts(db):
        return True
    command = 'python -m ap_coder users add --admin <username> --name "<Your Name>"'
    if ask is None and not sys.stdin.isatty():
        say(f"The first Admin: nobody can sign in yet. Make your own account, then run this again: {command}")
        return False
    from ..core.store import Store
    from ..core.store.users import ADMIN, USERNAME, fold_username
    from .cli import _new_password

    ask, password = ask or input, password or _new_password
    say(
        "The first Admin: nobody can sign in yet, so your own account is made now. What you typed on a review screen "
        "without saving is not shown once people sign in: if AP Coder was open, approve or park that first (Ctrl+C "
        "here stops, nothing changed)."
    )
    known = str(read_user_settings().get("reviewer") or "").strip()
    name = ask(f"Your name, as it is written on your approvals{f' [{known}]' if known else ''}: ").strip() or known
    if not name:
        say(f"No name given: nothing was changed. Run this again, or: {command}")
        return False
    offered = username_from(name)
    username = fold_username(ask(f"A username to sign in with{f' [{offered}]' if offered else ''}: ")) or offered
    if not USERNAME.fullmatch(username):
        say(
            "A username is 1 to 64 letters, digits, dots, dashes, underscores or @ (e.g. ann.lee): nothing was "
            "changed. Run this again."
        )
        return False
    secret = password(username, name)
    if secret is None:
        return False
    try:
        Store(db).add_user(username, secret, role=ADMIN, display_name=name, actor="the server console")
    except ValueError as exc:
        say(f"{exc} Nothing was changed: run this again.")
        return False
    say(
        f"Added {username} (Admin). At your first sign-in you choose a new password and set up an authenticator app "
        "on your phone, for a code at every sign-in; then add your colleagues on the Users page."
    )
    return True


def firewall_note(port: int, run: Callable[..., Any] = service.run_command, windows: bool | None = None) -> str:
    """What to say about the firewall ("" when Windows has the rule): the one step that needs administrator rights,
    so never done here."""
    if not (service.WINDOWS if windows is None else windows):
        return (
            f"The firewall: colleagues reach this computer on TCP port {port}; if it has a firewall, let that "
            "port in from the office network."
        )
    try:
        found = run(["netsh", "advfirewall", "firewall", "show", "rule", f"name={RULE}"], capture_output=True)
    except OSError:
        found = None
    if found is not None and found.returncode == 0:
        return ""
    return (
        f"The firewall: Windows has no rule named “{RULE}” yet, so colleagues' computers may not reach this "
        "laptop (you can sign in on it all the same). Adding the rule needs administrator rights, once: in a "
        "Command Prompt opened with Run as administrator (ask IT when you have none), for the office network "
        f"only ({GUIDE}, step 5, has the address range to add):\n"
        f'  netsh advfirewall firewall add rule name="{RULE}" dir=in action=allow protocol=TCP '
        f"localport={port} profile=domain,private\n"
        "If Windows asks whether to let Python through its firewall when AP Coder starts, that question needs "
        "the same rights; with the rule above it does not matter what is answered."
    )


def allow_folder(data: Path, folder: str, say: Say, now: bool = False) -> None:
    """``folder`` (``--allow-folder``; nothing for an empty one) added to the folders server.json allows for the
    backups' second copy (D-109), and said, also when it was allowed already: the command never ends having said
    nothing of it (B-2297). ``now``: AP Coder is being shared; it reads the list each time a folder is saved, so the
    folder can be chosen with no restart."""
    folder = folder.strip().strip('"')
    if not folder:
        return
    then = " AP Coder follows it now, with no restart: save the folder there." if now else ""
    if not service.folder_refusal(folder, data, server=True):
        say(f"Backups: {folder} is allowed already for the backups' second copy (Settings → Data & backups).{then}")
        return
    allowed = list(service._folders(service.read_config(data).get("allowed_folders")))
    service.write_config(data, allowed_folders=[*allowed, folder])
    say(f"Backups: {folder} may be used for the backups' second copy (Settings → Data & backups).{then}")


def backup_folder(data: Path, db: Path, first: bool, say: Say, allow: str = "") -> None:
    """The backups' second folder in server mode, where Admins may name only the folders server.json allows (D-109):
    ``allow`` (``--allow-folder``) is added to them, and so is, the first time (``first``: no account yet, the data
    is still one person's), the folder that person had chosen in laptop mode. Later one that is not allowed is said."""
    from ..core.store.backups import setting_in

    chosen = (setting_in(db, "backup_copy_dir") or "").strip() if db.is_file() else ""
    allow_folder(data, allow, say)
    if first and chosen and service.folder_refusal(chosen, data, server=True):
        allow_folder(data, chosen, say)
    if not chosen:
        say(
            "Backups: there is no second backup folder yet, so a lost or broken laptop loses everything. Choose one "
            "that is not on this laptop's disk (a OneDrive or network folder) with: python -m ap_coder share "
            '--allow-folder "<the folder>", then save it in Settings → Data & backups.'
        )
    elif service.folder_refusal(chosen, data, server=True):
        say(
            f"Backups: the second backup folder {chosen} is not one the server may write to, so the backups are not "
            f'copied there. Allow it with: python -m ap_coder share --allow-folder "{chosen}"'
        )


def running(data: Path, s: service.ServerSettings, say: Say) -> int | None:
    """An exit code when AP Coder already runs on this data folder (said: shared already, or open in laptop mode);
    None when nothing does."""
    found = service.dashboard_answer(data, s)
    if found is None:
        return None
    scheme, answer = found
    if answer.get("server_mode"):
        say(
            f"AP Coder is shared from this computer already: colleagues sign in at {address(s, scheme)}. To stop "
            "it: python -m ap_coder share --stop"
        )
        return 0
    say(
        "AP Coder is open on this computer now, without sign-in (your own window). Close it first (its window, or "
        "Stop AP Coder), then run this again: two AP Coders never use the same data at once."
    )
    return 1


def address(s: service.ServerSettings, scheme: str = "https") -> str:
    """What colleagues type: this computer's name (the first of server.json's ``allowed_hosts`` when it has any)."""
    name = s.allowed_hosts[0] if s.allowed_hosts else names()[0]
    return f"{scheme}://{name}:{s.port}"


def stop(data: Path, s: service.ServerSettings, say: Say) -> int:
    found = service.dashboard_answer(data, s)
    if found is None or not found[1].get("server_mode"):
        say("AP Coder is not shared from this computer now: there is nothing to stop.")
        return 0
    if service.host_running(data):  # the installed service would start it again
        say("AP Coder runs as this computer's service here: stop it with python -m ap_coder service stop")
        return 1
    if not service.dashboard_stopped(data, s):
        say("AP Coder did not stop: close its window, or end python.exe in Task Manager (Details).")
        return 1
    say(
        "Stopped: nobody can sign in until it is started again (python -m ap_coder share). A double-click on AP "
        "Coder's own shortcut does not open this data without sign-in."
    )
    return 0


def serve(data: Path, s: service.ServerSettings, popen: Callable[..., Any] | None = None, say: Say = print) -> int:
    """AP Coder as the server (``serve --server``, as the service's host starts it), in this window, until it ends:
    the window closed, Ctrl+C, or ``share --stop``. Meanwhile Windows is asked not to put the laptop to sleep by
    itself (B-2311): colleagues may be signed in while its owner is away from it."""
    from .awake import stay_awake

    env = {**os.environ, **service.serve_env(s, data)}
    child = (popen or subprocess.Popen)(service.serve_command(s, sys.executable), env=env, cwd=str(PROJECT_DIR))
    if service.WINDOWS:
        service.backend().tie_to_host(child)  # it ends with this window, however that is closed
    with stay_awake(serving=True) as awake:
        if awake:
            say(
                "While it serves, Windows does not put this laptop to sleep by itself (the screen may still turn "
                "off); closing the lid or the power button still does. Once it stops, the laptop sleeps as before."
            )
        while True:
            try:
                return int(child.wait() or 0)
            except KeyboardInterrupt:  # Ctrl+C reached it too: it is finishing what is under way
                continue


def prepare(data: Path, db: Path, say: Say, *, allow: str = "", ask: Callable[[str], str] | None = None,
            password: Callable[[str, str], str | None] | None = None, run: Callable[..., Any] = service.run_command,
            openssl: str | None = None) -> service.ServerSettings | None:  # fmt: skip
    """Everything sharing needs, checked in order and made where it can be; the settings to serve with, or None when
    something is still missing (said, with its next step). From the first Admin on, the data folder is a team's (its
    account, then its server.json): a plain start of AP Coder no longer opens it without sign-in (D-211)."""
    from ..core.store.users import sign_in_accounts

    s = service.settings(data)
    files = certificate(data, s, say, run, openssl)
    if files is None:
        return None
    first = not sign_in_accounts(db)
    if not first_admin(db, say, ask, password):
        return None
    kept = service.read_config(data)
    if (kept.get("certfile"), kept.get("keyfile")) != files or "port" not in kept:
        service.write_config(data, certfile=files[0], keyfile=files[1], port=s.port, host=s.host)
    backup_folder(data, db, first, say, allow)
    # Always with sign-in over HTTPS: a first test's --insecure-http is not this command's to start.
    s = dataclasses.replace(service.settings(data), insecure_http=False)
    return dataclasses.replace(
        s, workers=s.workers or WORKERS, capture_processes=s.capture_processes or CAPTURE_PROCESSES
    )


def add_parser(sub: Any) -> None:
    p = sub.add_parser("share", help="Let a few colleagues use this laptop's AP Coder, with sign-in: checks what is "
                       "missing, says the next step, then serves until its window is closed (docs/SERVER.md, "
                       "section 16)")  # fmt: skip
    one = p.add_mutually_exclusive_group()  # (a folder named with --stop would be allowed by nothing)
    one.add_argument("--stop", action="store_true", help="Stop sharing (as closing its window does)")
    one.add_argument("--allow-folder", default="", metavar="FOLDER",
                     help="Let Admins choose this folder for the backups' second copy, on Settings (also while AP "
                     "Coder is being shared: no restart)")  # fmt: skip
    p.add_argument("--admin-from-stdin", action="store_true",
                   help="A scripted set-up: read the first Admin's name, username and password from standard input, "
                   "one a line, instead of asking (never the password as an argument)")  # fmt: skip


def main(args: argparse.Namespace, say: Say = print) -> int:
    data = private_dir()
    db = env_path("AP_DB_PATH") or data / "ap_coder.db"
    try:
        s = service.settings(data)
        if args.stop:
            return stop(data, s, say)
        code = running(data, s, say)
        if code == 0:  # shared now: the folder is allowed all the same, and the server follows it (B-2297)
            allow_folder(data, args.allow_folder, say, now=True)
        elif code is not None and args.allow_folder.strip():
            say(f"Backups: {args.allow_folder.strip()} is not allowed yet: run this again once that window is closed.")
        if code is not None:
            return code
        ask = password = None
        if args.admin_from_stdin:  # (Windows' password prompt reads the keyboard, never a pipe)
            ask = lambda prompt: sys.stdin.readline().strip()  # noqa: E731
            password = lambda username, name: sys.stdin.readline().rstrip("\r\n")  # noqa: E731
        s = prepare(data, db, say, allow=args.allow_folder, ask=ask, password=password)
    except service.SettingsError as exc:
        say(str(exc))
        return 1
    except KeyboardInterrupt:
        say("Stopped: AP Coder was not started.")
        return 1
    if s is None:
        say(f"AP Coder was not started. Do the step above, then run this again ({GUIDE} has every step).")
        return 1
    note = firewall_note(s.port)
    if note:
        say(note)
    say(
        f"Colleagues open {address(s)} in Edge or Chrome and sign in (add them on the Users page first). It runs "
        "while this window is open and the laptop is awake: closing the window, or python -m ap_coder share --stop, "
        "stops it."
    )
    return serve(data, s, say=say)
