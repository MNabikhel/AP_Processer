"""CI's "server-update under 5 minutes" job (scripts/ci/server_update_downtime.py, D-188), its verdict tested here on
Linux: on the times server-update itself records in server.json, so the job judges what the update measured."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from ap_coder.ops import service
from tests.ops import test_server_update as base
from tests.ops.test_server_update import FakeServer, _bundle, _install
from tests.scripts import load_script

server = base.server  # the fixture: a data folder served by APProcessor (0.3.0)


@pytest.fixture
def downtime():
    return load_script("ci/server_update_downtime", "ap_script_server_update_downtime")


class SlowStart(FakeServer):
    """A service that takes ``seconds`` to answer once started."""

    def __init__(self, seconds: float) -> None:
        super().__init__()
        self.seconds = seconds

    def up(self, folder: Path) -> bool:
        time.sleep(self.seconds)
        return super().up(folder)


def test_the_job_passes_an_update_whose_downtime_is_within_the_limit(server, downtime, capsys):
    assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=SlowStart(0.3)) == 0
    times = service.read_config(server["data"])["history"][-1]["times"]
    assert 0.3 <= times["downtime_s"] < 5 and times["start_s"] >= 0.3
    assert times["downtime_s"] >= times["stop_s"] + times["safety_copy_s"] + times["migrate_s"] + times["start_s"] - 0.5
    capsys.readouterr()
    assert downtime.main(["check", "--data", str(server["data"])]) == 0
    out = capsys.readouterr().out
    assert "OK: the service was down" in out and "DOWNTIME" in out and "the safety copy" in out


def test_the_job_fails_a_downtime_over_the_limit(server, downtime, capsys):
    assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=SlowStart(0.3)) == 0
    assert downtime.main(["check", "--data", str(server["data"]), "--limit", "0.2"]) == 1
    assert "FAILED: the service was down" in capsys.readouterr().out


def test_the_job_fails_an_update_that_did_not_go_through(server, downtime, capsys):
    assert _install(_bundle(server["tmp"]), server["data"], server["db"], server=FakeServer(up_ok=False)) == 1
    assert downtime.main(["check", "--data", str(server["data"])]) == 1
    assert "did not go through" in capsys.readouterr().out
    assert downtime.main(["check", "--data", str(server["tmp"] / "nothing")]) == 1


@pytest.mark.parametrize(("entry", "ok"), [({"result": "updated", "times": {"downtime_s": 299.9}}, True),
                                           ({"result": "updated", "times": {"downtime_s": 300.1}}, False),
                                           ({"result": "updated", "times": {}}, False),
                                           ({"result": "rolled back: x", "times": {"downtime_s": 3}}, False),
                                           ({}, False)])  # fmt: skip
def test_the_verdict(downtime, entry, ok):
    assert downtime.judge(entry)[0] is ok
