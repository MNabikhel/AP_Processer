"""The server-mode switch (D-97): off unless asked for, so the laptop pilot is unchanged."""

import pytest

from ap_coder.ops import offline


@pytest.mark.parametrize(("value", "on"), [(None, False), ("", False), ("0", False), ("1", True), ("yes", True)])
def test_server_mode_is_off_unless_asked_for(monkeypatch, value, on):
    if value is None:
        monkeypatch.delenv(offline.SERVER_ENV, raising=False)
    else:
        monkeypatch.setenv(offline.SERVER_ENV, value)
    assert offline.server_mode() is on
