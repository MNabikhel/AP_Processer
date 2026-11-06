"""What the dashboard keeps for a short while (core/store/ephemeral.py): who has an invoice open on its review screen
(D-184)."""

from __future__ import annotations

import time

from ap_coder.core.store import Store


def test_an_invoice_open_is_written_once_a_renewal_and_forgotten_after_its_time(tmp_path):
    store = Store(tmp_path / "ap.db")
    assert store.keep_open(7, "ann", "Ann Lee", ttl=120, renew=45)
    assert store.keep_open(7, "bo", "Bo Chen", ttl=120, renew=45)
    assert store.open_by() == {7: {"ann": "Ann Lee", "bo": "Bo Chen"}}  # the first to open it first
    assert store.open_by(7) == store.open_by() and store.open_by(8) == {}
    with store._conn() as conn:  # nothing written by a renewal sooner than 45 s after the last
        before = conn.execute("SELECT total_changes()").fetchone()[0]
        assert not store.keep_open(7, "ann", "Ann Lee", ttl=120, renew=45)
        assert conn.execute("SELECT total_changes()").fetchone()[0] == before
    with store._conn() as conn:  # renewed 50 s ago: written again
        conn.execute("UPDATE ephemeral SET expires_at = expires_at - 50 WHERE token = 'ann'")
    assert store.keep_open(7, "ann", "Ann Lee", ttl=120, renew=45)
    with store._conn() as conn:  # Bo's tab closed two minutes ago: gone
        conn.execute("UPDATE ephemeral SET expires_at = expires_at - 121 WHERE token = 'bo'")
    assert store.open_by() == {7: {"ann": "Ann Lee"}}


def test_an_invoice_open_lasts_by_the_databases_clock_not_the_computers(tmp_path, monkeypatch):
    """As B-1770: the computer's clock moved an hour (or a process paused) leaves who has it open as it is."""
    store = Store(tmp_path / "ap.db")
    store.keep_open(7, "ann", "Ann Lee", ttl=120, renew=45)
    real = time.time
    monkeypatch.setattr(time, "time", lambda: real() + 3600)
    assert store.open_by() == {7: {"ann": "Ann Lee"}}
    assert not store.keep_open(7, "ann", "Ann Lee", ttl=120, renew=45)  # and not renewed: written a moment ago
