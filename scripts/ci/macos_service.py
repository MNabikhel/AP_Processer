"""CI's check of the company server's service on a Mac (D-186), for real: the LaunchDaemon installed with sudo, the
dashboard answering, its host killed (launchd starts it again), restarted while it runs (launchd's SIGTERM: the host
ends its dashboard, then itself), stopped (still stopped a minute later), started again, removed (its plist gone).
Run by .github/workflows/ci.yml (job macos-service) on GitHub's macOS runner, whose sudo asks no password; on a Mac
of your own, from an AP Coder folder set up with its .venv:

    .venv/bin/python scripts/ci/macos_service.py --data /tmp/apcoder-ci-data --port 8597

It installs this folder's AP Coder as the account running it (never root), on 127.0.0.1 over plain HTTP, under a
test label (``com.apcoder.ci``) so a real AP Coder service on the Mac is not touched. The dashboard is asked through
``service status --json`` (its /healthz on 127.0.0.1): nothing here opens a connection itself. On a failure it prints
the service's logs and what launchd says. Standard library only.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

TASK = "com.apcoder.ci"  # the label (a name like a label is used as it is)
ROOT = Path(__file__).resolve().parents[2]
clock, pause = time.monotonic, time.sleep  # (the tests stand in for them)


class Failed(Exception):
    """A step did not go as it should: said in plain words."""


def run(cmd: list[str], timeout: float = 300) -> subprocess.CompletedProcess:
    """Run a command, printed with what it said (the one place commands run: the tests stand in for it)."""
    print("$ " + " ".join(cmd), flush=True)
    done = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=timeout, check=False)  # noqa: S603
    for said in (done.stdout, done.stderr):
        if said.strip():
            print(said.rstrip(), flush=True)
    return done


class Check:
    def __init__(self, data: Path, port: int, account: str, python: str = sys.executable) -> None:
        self.data, self.port, self.account, self.python = data, port, account, python

    def service(self, *args: str) -> subprocess.CompletedProcess:
        """``python -m ap_coder service ...`` as root (sudo), given the data folder (sudo leaves the environment)."""
        env = [f"AP_PRIVATE_DIR={self.data}", "PYTHONDONTWRITEBYTECODE=1"]
        return run(["sudo", "env", *env, self.python, "-m", "ap_coder", "service", *args, "--task-name", TASK])

    def status(self) -> dict:
        done = self.service("status", "--json")
        try:
            found = json.loads(done.stdout)
        except ValueError:
            raise Failed(f"service status --json did not say JSON (exit code {done.returncode})") from None
        if not isinstance(found, dict):
            raise Failed("service status --json did not say a JSON object")
        return found

    def until(self, what: str, ok: Callable[[dict], bool], within: float, every: float = 3.0) -> dict:
        """The status once ``ok`` says it is so, within ``within`` seconds (Failed otherwise)."""
        deadline = clock() + within
        while True:
            found = self.status()
            if ok(found):
                return found
            if clock() >= deadline:
                raise Failed(f"{what}: not so within {within:.0f} s (the last status above)")
            pause(every)

    def answering(self, found: dict) -> bool:
        return bool(found.get("running") and found.get("answering") and (found.get("health") or {}).get("app"))

    def install(self) -> dict:
        self.data.mkdir(parents=True, exist_ok=True)
        done = self.service("install", "--account", self.account, "--insecure-http", "--host", "127.0.0.1",
                            "--port", str(self.port))  # fmt: skip
        if done.returncode:
            raise Failed(f"service install ended with exit code {done.returncode}")
        found = self.status()
        plist = str(found.get("plist") or "")
        if not found.get("installed") or not Path(plist).is_file():
            raise Failed(f"installed, but no plist at {plist or '(none said)'}")
        if run(["plutil", "-lint", plist]).returncode:
            raise Failed(f"{plist} is not a plist launchd reads (plutil -lint)")
        run(["plutil", "-p", plist])  # (what launchd was given, in the log)
        return found

    def again(self, what: str, host: int, dashboard: int, within: float) -> None:
        """A new host within ``within`` seconds, then a dashboard of its own answering; the old dashboard gone."""
        started = clock()
        self.until(f"{what}: a new host", lambda f: bool(f.get("running")) and f.get("pid") != host, within)
        self.until(f"{what}: its dashboard answers",
                   lambda f: self.answering(f) and f.get("pid") != host and f.get("dashboard_pid") != dashboard,
                   within=240)  # fmt: skip
        if dashboard and run(["sudo", "kill", "-0", str(dashboard)]).returncode == 0:
            raise Failed(f"{what}: the old dashboard (process {dashboard}) still runs")
        print(f"{what}: the dashboard answers again {clock() - started:.0f} s later", flush=True)

    def kill_the_host(self, within: float) -> None:
        """``kill -9`` of the host: launchd ends its dashboard with it (the process group) and, KeepAlive, starts the
        host again (a new process), whose own dashboard answers."""
        before = self.until("the dashboard answers", self.answering, within=240)
        host, dashboard = int(before["pid"]), int(before.get("dashboard_pid") or 0)
        if run(["sudo", "kill", "-9", str(host)]).returncode:
            raise Failed(f"the host (process {host}) could not be killed")
        self.again(f"the host (process {host}) killed", host, dashboard, within)

    def restart(self) -> None:
        """``service start`` while it runs: launchctl kickstart -k sends the host SIGTERM; it ends its dashboard, then
        itself, and launchd starts it again."""
        before = self.until("the dashboard answers", self.answering, within=240)
        host, dashboard = int(before["pid"]), int(before.get("dashboard_pid") or 0)
        if self.service("start").returncode:
            raise Failed("service start (while it runs) ended with an error")
        self.again("service start while it runs", host, dashboard, within=120)
        log = str(self.data / "logs" / "service.log")
        if run(["grep", "-q", "the host was asked to stop (SIGTERM)", log]).returncode:
            raise Failed("the host's log does not say it was asked to stop by SIGTERM")

    def stop(self, stays: float) -> None:
        """``service stop``: stopped at once, and still stopped ``stays`` seconds later (KeepAlive leaves it)."""
        if self.service("stop").returncode:
            raise Failed("service stop ended with an error")
        deadline = clock() + stays
        while True:
            found = self.status()
            if found.get("running") or found.get("answering") or found.get("state") == "running":
                raise Failed("the service runs again after service stop")
            if clock() >= deadline:
                break
            pause(min(10.0, max(0.0, deadline - clock())))
        print(f"still stopped {stays:.0f} s after service stop", flush=True)

    def start(self) -> None:
        if self.service("start").returncode:
            raise Failed("service start ended with an error")
        self.until("the dashboard answers after service start", self.answering, within=240)

    def remove(self, plist: str) -> None:
        if self.service("remove").returncode:
            raise Failed("service remove ended with an error")
        found = self.status()
        if found.get("installed") or Path(plist).exists():
            raise Failed(f"service remove left the LaunchDaemon ({plist})")
        if found.get("running") or found.get("answering"):
            raise Failed("the dashboard still runs after service remove")

    def show_logs(self, plist: str) -> None:
        """What the service and launchd say, for a failure."""
        logs = self.data / "logs"
        for name in ("service.log", "launchd.out.log", "launchd.err.log"):
            run(["sudo", "tail", "-n", "80", str(logs / name)])
        run(["sudo", "launchctl", "print", f"system/{TASK}"])
        if plist:
            run(["sudo", "cat", plist])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", required=True, help="A data folder for the test service (made when missing)")
    parser.add_argument("--port", type=int, default=8597)
    parser.add_argument("--account", default=os.environ.get("SUDO_USER") or getpass.getuser(),
                        help="The account the service runs as (default: this one; never root)")  # fmt: skip
    parser.add_argument("--back-within", type=float, default=30, help="Seconds launchd has to start a killed host")
    parser.add_argument("--stays-stopped", type=float, default=60, help="Seconds it must stay stopped after stop")
    args = parser.parse_args(argv)
    check = Check(Path(args.data).resolve(), args.port, args.account)
    plist, link = "", ROOT.parent / "current"
    made_link = not link.is_symlink()
    try:
        plist = str(check.install()["plist"])
        check.kill_the_host(args.back_within)
        check.restart()
        check.stop(args.stays_stopped)
        check.start()
        check.remove(plist)
    except (Failed, subprocess.TimeoutExpired, OSError, ValueError, KeyError) as exc:
        print(f"\nFAILED: {exc}", flush=True)
        check.show_logs(plist)
        if plist and Path(plist).exists():
            check.service("remove")  # never a test daemon left running on the Mac
        return 1
    finally:
        if made_link and link.is_symlink():
            run(["sudo", "rm", str(link)])  # (the link install made beside this folder)
    print("\nThe LaunchDaemon: installed, answering, started again by launchd after a kill, restarted (SIGTERM), "
          "stopped for good, started, removed.")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
