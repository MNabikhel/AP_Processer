"""server-update's downtime on a big database (D-188): from the service stopped to the new version answering, which
must stay under 5 minutes. server-update prepares the new version while the old one serves (unzip, set up, a copy
of the database updated, the self-check), so the downtime is the stop, the safety copy, the database's update and
the start; it records each in server.json (``history[-1]["times"]``).

On Windows (CI's job "server-update under 5 minutes"), from a checkout, with the offline bundle built once:

    python scripts/ci/server_update_downtime.py windows --bundle dist\\APProcessor-offline-<version>.zip
        [--work FOLDER] [--invoices 20000] [--limit 300]

The bundle is unzipped to ``<work>/apps/APProcessor`` and set up as the server would be (its launcher, offline), a
data folder seeded with ``--invoices`` invoices (``python -m ap_coder.bench seed``), the service installed (the
scheduled task as SYSTEM, plain HTTP on 127.0.0.1) and started. Then ``server-update`` installs the same bundle
again (``--force``: it is the version running) and the times it recorded are judged. ``cleanup`` removes the
service afterwards.

Anywhere (a stopwatch for a developer: no service, the dashboard run as a process of its own, this .venv's
packages for both folders):

    python scripts/ci/server_update_downtime.py local [--work FOLDER] [--invoices 20000] [--limit 300]

and ``check --data FOLDER [--limit 300]`` judges the last update a data folder's server.json recorded: exit 0 when it
went through within the limit. Standard library only, but ``local`` (it runs server-update itself).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIMIT = 300.0  # seconds: the downtime allowed (D-188)
PORT = 8541
PARTS = [("prepare_s", "prepared, the old version serving"), ("check_migration_s", "  of it: a copy of the database "
         "updated"), ("self_check_s", "  of it: the self-check"), ("stop_s", "stopping the service"),
         ("safety_copy_s", "the safety copy"), ("migrate_s", "the database updated"),
         ("start_s", "starting, until it answers"), ("downtime_s", "DOWNTIME (stopped to answering)")]  # fmt: skip


# --- The verdict (also tested on Linux: tests/ops/test_server_update_downtime.py) ------------------------------------


def last_update(data: Path) -> dict:
    """The last entry server-update wrote in ``data``'s server.json ({} when none)."""
    try:
        history = json.loads((data / "server.json").read_text(encoding="utf-8")).get("history") or []
    except (OSError, ValueError, AttributeError):
        return {}
    return history[-1] if history and isinstance(history[-1], dict) else {}


def judge(entry: dict, limit: float = LIMIT) -> tuple[bool, list[str]]:
    """Whether the update ``entry`` went through with a downtime within ``limit`` seconds, and the lines that say so."""
    times = entry.get("times") if isinstance(entry.get("times"), dict) else {}
    lines = [f"server-update {entry.get('from', '?')} -> {entry.get('to', '?')}: {entry.get('result', 'no update')}"]
    lines += [f"  {words:<40} {float(times[key]):8.1f} s" for key, words in PARTS if key in times]
    if not str(entry.get("result") or "").startswith("updated"):
        return False, [*lines, "FAILED: the update did not go through (the lines above it say why)."]
    down = times.get("downtime_s")
    if not isinstance(down, (int, float)):
        return False, [*lines, "FAILED: server-update recorded no downtime."]
    ok = down <= limit
    said = "within" if ok else "more than"
    return ok, [*lines, f"{'OK' if ok else 'FAILED'}: the service was down {down:g} s, {said} the {limit:g} s allowed."]


def check(data: Path, limit: float) -> int:
    ok, lines = judge(last_update(data), limit)
    print("\n".join(lines))
    return 0 if ok else 1


# --- The steps around it ----------------------------------------------------------------------------------------------


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def unzip(bundle: Path, apps: Path) -> Path:
    """The bundle's one folder, as ``apps/APProcessor``."""
    with zipfile.ZipFile(bundle) as zf:
        top = zf.namelist()[0].split("/")[0]
        zf.extractall(apps / ".unzipping")
    folder = apps / "APProcessor"
    (apps / ".unzipping" / top).rename(folder)
    shutil.rmtree(apps / ".unzipping", ignore_errors=True)
    return folder


def server_env(data: Path) -> dict[str, str]:
    return {**os.environ, "AP_PRIVATE_DIR": str(data), "AP_SERVER_MODE": "1", "AP_NO_PAUSE": "1",
            "PYTHONIOENCODING": "utf-8"}  # fmt: skip


def timed(what: str, cmd: list, **kwargs) -> float:
    """``cmd`` run (it must work), and how long it took, said."""
    started = time.monotonic()
    print(f"\n=== {what}: {' '.join(str(c) for c in cmd)}", flush=True)
    subprocess.run([str(c) for c in cmd], check=True, **kwargs)  # noqa: S603
    took = time.monotonic() - started
    print(f"=== {what}: {took:.1f} s", flush=True)
    return took


def answers(port: int) -> bool:
    """AP Coder answers its /healthz on this computer (never through a proxy: the CI step points them at a closed
    port)."""
    direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with direct.open(f"http://127.0.0.1:{port}/healthz", timeout=3) as r:
            return json.loads(r.read().decode()).get("app") == "ap-coder"
    except (OSError, ValueError):
        return False


def wait_until_up(port: int, seconds: float) -> float:
    started = time.monotonic()
    while time.monotonic() - started < seconds:
        if answers(port):
            return time.monotonic() - started
        time.sleep(1)
    raise SystemExit(f"FAILED: the dashboard did not answer on port {port} within {seconds:g} s")


def windows(args: argparse.Namespace) -> int:
    work = Path(args.work).resolve()
    apps, data, bundle = work / "apps", work / "APCoderData", Path(args.bundle).resolve()
    env = server_env(data)
    folder = unzip(bundle, apps)
    timed("Set up as the server (its launcher, offline)", [sys.executable, "-E", "-s", folder / "scripts" / "launch.py",
          "--no-start", "--yes"], cwd=folder, env=env)  # fmt: skip
    python = folder / ".venv" / "Scripts" / "python.exe"
    # (--replace: the launcher's first run has just made an empty database in the data folder)
    timed(f"A data folder of {args.invoices:,} invoices", [python, "-m", "ap_coder.bench", "seed", "--out", data,
          "--invoices", args.invoices, "--replace"], cwd=folder, env=env)  # fmt: skip
    timed("The service installed and started", [python, "-m", "ap_coder", "service", "install", "--account", "SYSTEM",
          "--insecure-http", "--host", "127.0.0.1", "--port", PORT, "--workers", "2"], cwd=folder, env=env)  # fmt: skip
    print(f"=== It answers after {wait_until_up(PORT, 300):.1f} s", flush=True)
    size = (data / "ap_coder.db").stat().st_size / 1e6
    print(f"=== The database: {size:,.0f} MB", flush=True)
    done = subprocess.run([str(python), "-m", "ap_coder", "server-update", str(bundle), "--sha256", sha256(bundle),  # noqa: S603
                           "--force"], cwd=folder, env=env, check=False)  # fmt: skip
    print(f"\n=== server-update exit code {done.returncode}")
    failed = check(data, args.limit) or (1 if done.returncode else 0)
    if failed:  # what the service said, for whoever reads the job
        log = data / "logs" / "service.log"
        print(f"\n=== The end of {log}:")
        print("\n".join(log.read_text(encoding="utf-8", errors="replace").splitlines()[-60:]) if log.exists() else "-")
    return failed


def cleanup(args: argparse.Namespace) -> int:
    """The service removed (from the folder it runs now), whatever happened."""
    data = Path(args.work).resolve() / "APCoderData"
    try:
        folder = Path(json.loads((data / "server.json").read_text(encoding="utf-8"))["folder"])
    except (OSError, ValueError, KeyError):
        return 0
    python = folder / ".venv" / "Scripts" / "python.exe"
    if python.exists():
        subprocess.run([str(python), "-m", "ap_coder", "service", "remove"], cwd=folder, env=server_env(data),  # noqa: S603
                       check=False)  # fmt: skip
    return 0


# --- Locally: server-update itself, the dashboard as a process of its own ---------------------------------------------


def code_bundle(dest: Path) -> Path:
    """A bundle of this checkout's code (no wheelhouse: ``local`` sets each folder up with this .venv)."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_offline_bundle as builder

    stage = dest.parent / "stage" / builder.TOP
    for rel in builder.source_files():
        (stage / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, stage / rel)
    builder.stamp_build(stage)
    builder.write_manifest(stage)
    builder._zip(stage, dest)
    return dest


def local(args: argparse.Namespace) -> int:
    sys.path.insert(0, str(ROOT))
    from ap_coder.ops import server_update, service

    work = Path(args.work or tempfile.mkdtemp(prefix="ap_coder-downtime-")).resolve()
    apps, data = work / "apps", work / "APCoderData"
    venv = Path(sys.prefix)

    class LocalServer(server_update.Server):
        """The dashboard run as a process of its own, from the folder server.json names: no scheduled task."""

        child: subprocess.Popen | None = None

        def installed(self) -> bool:
            return True

        def set_up(self, folder: Path) -> bool:
            (folder / ".venv").symlink_to(venv, target_is_directory=True)  # (this .venv: its packages)
            return service.folder_python(folder).exists()

        def start(self) -> bool:
            folder = Path(service.read_config(self.data)["folder"])
            cmd = [str(service.folder_python(folder)), "-m", "ap_coder", "serve", "--server", "--insecure-http",
                   "--no-browser", "--host", "127.0.0.1", "--port", str(PORT), "--workers", "2"]  # fmt: skip
            log = open(work / "dashboard.log", "ab")  # noqa: SIM115 (the dashboard's own, while it runs)
            self.child = subprocess.Popen(cmd, cwd=folder, env=server_env(self.data), stdout=log,  # noqa: S603
                                          stderr=subprocess.STDOUT)  # fmt: skip
            # Reaped as soon as it ends: a child of this process left a zombie would look alive to service.stop (the
            # service's dashboard is no child of server-update's).
            threading.Thread(target=self.child.wait, daemon=True).start()
            return True

        def stop(self) -> bool:
            stopped = service.dashboard_stopped(self.data, self.settings)
            if self.child is not None:
                self.child.wait(60)
            return stopped

    bundle = code_bundle(work / "APProcessor-offline-local.zip")
    current = unzip(bundle, apps)
    (current / ".venv").symlink_to(venv, target_is_directory=True)
    seed = [sys.executable, "-m", "ap_coder.bench", "seed", "--out", data, "--invoices", args.invoices]
    timed(f"A data folder of {args.invoices:,} invoices", seed, cwd=ROOT, env=server_env(data))
    # (the record of installs, which a switch writes, in the work folder: never this account's own)
    os.environ.update({"AP_PRIVATE_DIR": str(data), "AP_SERVER_MODE": "1",
                       "AP_USER_SETTINGS": str(work / "user-settings.json")})  # fmt: skip
    service.write_config(data, folder=str(current), insecure_http=True, host="127.0.0.1", port=PORT, workers=2)
    server = LocalServer(data, service.settings(data))
    server.start()
    print(f"=== It answers after {wait_until_up(PORT, 300):.1f} s; the database: "
          f"{(data / 'ap_coder.db').stat().st_size / 1e6:,.0f} MB", flush=True)  # fmt: skip
    server_update.running_folder = lambda: Path(service.read_config(data)["folder"])  # (the "cd" into it)
    try:
        code = server_update.update(bundle, data, data / "ap_coder.db", server=server, sha256=sha256(bundle),
                                    force=True)  # fmt: skip
    finally:
        server.stop()
    print(f"\n=== server-update exit code {code}; the folders and data: {work}")
    return check(data, args.limit) or code


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="mode", required=True)
    for name in ("windows", "local", "check", "cleanup"):
        p = sub.add_parser(name)
        p.add_argument("--limit", type=float, default=LIMIT, help="the downtime allowed, in seconds (300)")
        if name in ("windows", "local", "cleanup"):
            p.add_argument("--work", default=None if name == "local" else str(Path(tempfile.gettempdir()) / "downtime"),
                           help="where the folders are made (empty)")  # fmt: skip
        if name in ("windows", "local"):
            p.add_argument("--invoices", type=int, default=20_000, help="invoices in the database (20,000)")
        if name == "windows":
            p.add_argument("--bundle", required=True, help="the offline bundle (built once)")
        if name == "check":
            p.add_argument("--data", required=True, help="the data folder whose last update is judged")
    args = parser.parse_args(argv)
    if args.mode == "check":
        return check(Path(args.data), args.limit)
    return {"windows": windows, "local": local, "cleanup": cleanup}[args.mode](args)


if __name__ == "__main__":
    sys.exit(main())
