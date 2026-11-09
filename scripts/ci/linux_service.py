"""CI's check of the company server's service on Linux (D-208), for real: the systemd unit installed with sudo as the
account ``service install`` makes (``apcoder``), systemd's own reading of it (``systemd-analyze verify``) and its start
at boot (enabled), the dashboard answering as that account, its host killed (systemd starts it again), restarted while
it runs (``service start``), stopped by systemd itself (``systemctl stop``: SIGTERM, on which the host ends its
dashboard, then itself, and stays stopped), stopped with ``service stop`` (still stopped a minute later, and when
systemd starts the unit as at boot), started again, an update cut short (root's marker and ``stopped`` file) dealt with
by the host at the unit's next start, removed (its unit gone). Run by .github/workflows/ci.yml (job linux-service) on
GitHub's Ubuntu runner, whose sudo asks no password; on an Ubuntu of your own, from an AP Coder folder set up with its
.venv (root's, as docs/SERVER.md section 15 has it, so the service's account can read it):

    /opt/apcoder/APProcessor/.venv/bin/python /opt/apcoder/APProcessor/scripts/ci/linux_service.py \\
        --data /var/lib/apcoder-ci --port 8597

It installs this folder's AP Coder on 127.0.0.1 over plain HTTP under a test name (``apcoder-ci``, the unit
``apcoder-ci.service``) so a real AP Coder service on the computer is not touched; the account is kept (as service
remove keeps it). The dashboard is asked through ``service status --json`` (its /healthz on 127.0.0.1): nothing here
opens a connection itself. On a failure it prints the service's log, the journal and what systemd says. Standard
library only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

TASK = "apcoder-ci"  # the unit's name (a name starting with apcoder is used as it is)
UNIT = f"{TASK}.service"
ROOT = Path(__file__).resolve().parents[2]
RUNNING = ("active", "activating", "deactivating", "reloading")  # systemd's ActiveState of a unit that runs
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
        self.unit = ""  # the unit file, once install has said it (removed at the end, failed or not)

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
        done = self.service("install", "--account", self.account, "--insecure-http", "--host", "127.0.0.1",
                            "--port", str(self.port))  # fmt: skip
        if done.returncode:
            raise Failed(f"service install ended with exit code {done.returncode}")
        found = self.status()
        unit = self.unit = str(found.get("unit_file") or "")
        if not found.get("installed") or not Path(unit).is_file():
            raise Failed(f"installed, but no unit at {unit or '(none said)'}")
        if run(["sudo", "systemd-analyze", "verify", unit]).returncode:
            raise Failed(f"{unit} is not a unit systemd reads (systemd-analyze verify)")
        run(["systemctl", "cat", UNIT])  # (what systemd was given, in the log)
        if run(["systemctl", "is-enabled", UNIT]).stdout.strip() != "enabled":
            raise Failed(f"{UNIT} is not enabled: systemd would not start it at boot")
        return found

    def runs_as_the_account(self) -> None:
        """The host and its dashboard are the service account's processes, never root's."""
        found = self.until("the dashboard answers", self.answering, within=240)
        for what, pid in (("the host", found.get("pid")), ("the dashboard", found.get("dashboard_pid"))):
            user = run(["ps", "-o", "user=", "-p", str(pid)]).stdout.strip()
            if user != self.account:
                raise Failed(f"{what} (process {pid}) runs as {user or '(nobody: gone)'}, not {self.account}")

    def again(self, what: str, host: int, dashboard: int, within: float) -> None:
        """A new host within ``within`` seconds, then a dashboard of its own answering; the old dashboard gone."""
        started = clock()
        self.until(f"{what}: a new host", lambda f: bool(f.get("running")) and f.get("pid") != host, within)
        self.until(f"{what}: its dashboard answers",
                   lambda f: self.answering(f) and f.get("pid") != host and f.get("dashboard_pid") != dashboard,
                   within=240)  # fmt: skip
        if dashboard:
            self.gone(f"{what}: the old dashboard (process {dashboard})", dashboard)
        print(f"{what}: the dashboard answers again {clock() - started:.0f} s later", flush=True)

    def gone(self, what: str, pid: int, within: float = 30) -> None:
        """``pid`` has ended within ``within`` seconds (``kill -0`` fails)."""
        deadline = clock() + within
        while run(["sudo", "kill", "-0", str(pid)]).returncode == 0:
            if clock() >= deadline:
                raise Failed(f"{what} still runs {within:.0f} s later")
            pause(1.0)

    def kill_the_host(self, within: float) -> None:
        """``kill -9`` of the host: systemd kills what is left of it (its dashboard: KillMode=mixed) and,
        Restart=on-failure, starts the host again 10 s later (a new process), whose own dashboard answers."""
        before = self.until("the dashboard answers", self.answering, within=240)
        host, dashboard = int(before["pid"]), int(before.get("dashboard_pid") or 0)
        if run(["sudo", "kill", "-9", str(host)]).returncode:
            raise Failed(f"the host (process {host}) could not be killed")
        self.again(f"the host (process {host}) killed", host, dashboard, within)

    def restart(self) -> None:
        """``service start`` while it runs: systemctl restart ends the running host and starts it again."""
        before = self.until("the dashboard answers", self.answering, within=240)
        host, dashboard = int(before["pid"]), int(before.get("dashboard_pid") or 0)
        if self.service("start").returncode:
            raise Failed("service start (while it runs) ended with an error")
        self.again("service start while it runs", host, dashboard, within=120)

    def systemd_stop(self, stays: float) -> None:
        """systemd's own stop of the running unit (``systemctl stop``, as at the computer's shutdown): SIGTERM to the
        host, which ends its dashboard, then itself with exit code 0; systemd does not start it again."""
        before = self.until("the dashboard answers", self.answering, within=240)
        dashboard = int(before.get("dashboard_pid") or 0)
        if run(["sudo", "systemctl", "stop", UNIT]).returncode:
            raise Failed(f"systemctl stop {UNIT} ended with an error")
        self.until("stopped by systemd", lambda f: not f.get("running") and not f.get("answering"), within=90)
        log = str(self.data / "logs" / "service.log")
        if run(["sudo", "grep", "-q", "the host was asked to stop (SIGTERM)", log]).returncode:
            raise Failed("the host's log does not say it was asked to stop (SIGTERM)")
        if dashboard:
            self.gone(f"the dashboard (process {dashboard}), after systemd stopped its host,", dashboard)
        self.stays_stopped(stays, "systemctl stop")

    def stays_stopped(self, stays: float, after: str) -> None:
        """Not running, not answering, and systemd not running it, now and for ``stays`` seconds."""
        deadline = clock() + stays
        while True:
            found = self.status()
            if found.get("running") or found.get("answering") or found.get("state") in RUNNING:
                raise Failed(f"the service runs again after {after}")
            if clock() >= deadline:
                break
            pause(min(10.0, max(0.0, deadline - clock())))
        print(f"still stopped {stays:.0f} s after {after}", flush=True)

    def stop(self, stays: float) -> None:
        """``service stop``: stopped at once, still stopped ``stays`` seconds later (Restart=on-failure leaves it),
        and still when systemd starts the unit as at the computer's next start (it stays enabled: the host ends at
        once, with exit code 0, seeing the ``stopped`` file)."""
        if self.service("stop").returncode:
            raise Failed("service stop ended with an error")
        self.stays_stopped(stays, "service stop")
        if run(["sudo", "systemctl", "start", UNIT]).returncode:  # what the next boot does
            raise Failed(f"systemctl start {UNIT} (as at boot) ended with an error")
        ended = lambda f: f.get("state") not in RUNNING and str(f.get("last_exit_code")) == "0"  # noqa: E731
        self.until("the host started as at boot ends at once", ended, within=60)
        self.stays_stopped(15, "service stop, and systemd starting the unit as at boot")

    def start(self) -> None:
        if self.service("start").returncode:
            raise Failed("service start ended with an error")
        self.until("the dashboard answers after service start", self.answering, within=240)

    def cut_short(self) -> None:
        """An update cut short, as a power cut leaves it: root's sudo server-update had stopped the service (its
        ``stopped`` file) and was making the safety copy, its marker (update.json) written by root. At the computer's
        next start (systemd starting the unit) the host, as the service's account, deals with it: the version it runs
        starts, the marker and the ``stopped`` file gone, said in its log (D-188). Nothing else is touched."""
        if self.service("stop").returncode:
            raise Failed("service stop (the update's own) ended with an error")
        marker = self.data / "service" / "update.json"
        state = {"from": "0.0.1", "to": "0.0.2", "from_folder": str(ROOT), "label": "before-update-0.0.2",
                 "to_folder": str(ROOT.parent / "APProcessor-ci-cut-short"), "database": str(self.data / "ap_coder.db"),
                 "step": "safety-copy", "started": "2026-11-09T02:00:00", "pid": 1, "safety_copy": ""}  # fmt: skip
        write = ("import json, sys; from pathlib import Path; from ap_coder.ops import update_recovery; "
                 "update_recovery.write(Path(sys.argv[1]), json.loads(sys.argv[2]))")  # fmt: skip
        done = run(["sudo", "env", "PYTHONDONTWRITEBYTECODE=1", self.python, "-c", write, str(self.data),
                    json.dumps(state)])  # fmt: skip
        if done.returncode:
            raise Failed("the marker of an update cut short could not be written (as root)")
        run(["sudo", "systemctl", "start", UNIT])  # the computer's next start: systemd starts the enabled unit
        self.until("the dashboard answers after an update cut short", self.answering, within=240)
        if run(["sudo", "test", "-e", str(marker)]).returncode == 0:
            raise Failed(f"the update cut short was not dealt with: {marker} is still there")
        log = str(self.data / "logs" / "service.log")
        if run(["sudo", "grep", "-q", "was cut short while it made the safety copy", log]).returncode:
            raise Failed("the service's log does not say it dealt with the update cut short")

    def remove(self, unit: str) -> None:
        if self.service("remove").returncode:
            raise Failed("service remove ended with an error")
        found = self.status()
        if found.get("installed") or Path(unit).exists():
            raise Failed(f"service remove left the unit ({unit})")
        if "LoadState=not-found" not in run(["systemctl", "show", UNIT, "--property=LoadState"]).stdout:
            raise Failed(f"systemd still has {UNIT} after service remove")
        if found.get("running") or found.get("answering"):
            raise Failed("the dashboard still runs after service remove")

    def show_logs(self, unit: str) -> None:
        """What the service and systemd say, for a failure."""
        run(["sudo", "tail", "-n", "80", str(self.data / "logs" / "service.log")])
        run(["sudo", "journalctl", "-u", UNIT, "--no-pager", "-n", "200"])
        run(["sudo", "systemctl", "status", UNIT, "--no-pager"])
        if unit:
            run(["sudo", "cat", unit])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--data", required=True, help="A data folder for the test service (made by service install)")
    parser.add_argument("--port", type=int, default=8597)
    parser.add_argument("--account", default="apcoder",
                        help="The account it runs as (default apcoder, made when missing; never root)")  # fmt: skip
    parser.add_argument("--back-within", type=float, default=30, help="Seconds systemd has to start a killed host")
    parser.add_argument("--stays-stopped", type=float, default=60, help="Seconds it must stay stopped after stop")
    args = parser.parse_args(argv)
    check = Check(Path(args.data).resolve(), args.port, args.account)
    link = ROOT.parent / "current"
    made_link = not link.is_symlink()
    try:
        check.install()
        check.runs_as_the_account()
        check.kill_the_host(args.back_within)
        check.restart()
        check.systemd_stop(stays=15)
        check.start()
        check.stop(args.stays_stopped)
        check.start()
        check.cut_short()
        check.remove(check.unit)
    except (Failed, subprocess.TimeoutExpired, OSError, ValueError, KeyError) as exc:
        print(f"\nFAILED: {exc}", flush=True)
        check.show_logs(check.unit)
        if check.unit and Path(check.unit).exists():
            check.service("remove")  # never a test unit left running on the computer
        return 1
    finally:
        if made_link and link.is_symlink():
            run(["sudo", "rm", str(link)])  # (the link install made beside this folder)
    print(f"\nThe systemd unit: installed, answering as {args.account}, started again by systemd after a kill, "
          "restarted, stopped by systemd (SIGTERM), stopped for good (a start as at boot included), started, an update "
          "cut short dealt with at its next start, removed.")  # fmt: skip
    return 0


if __name__ == "__main__":
    sys.exit(main())
