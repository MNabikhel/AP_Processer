"""The settled answers the company server's background process works out again (D-198).

A page that only shows a big answer (Spend, Month-end, Vendors, Insights, Sales tax, Learning, the queue's late
pill) settles it (``Store.shared_answer``'s ``settle``, D-164, D-197). Once it no longer holds, the web worker that is
asked it does not work it out (6 to 11 s at 300,000 invoices, its other requests waiting behind it): it shows the one
kept and leaves a want (``core/store/reports.py``); the ``Refresher`` here, a thread of the background process, works
the wants out one at a time, oldest first, and keeps each for every process. At its start it works out again those
asked in the last day (``ASKED_WITHIN``), so after a restart or an update nobody waits for the first one.

``ANSWERS``: each settled answer by its name, and how it is worked out again from what was asked (the ``again`` its
caller gives, JSON): by its caller itself, so with the same version, format, stamp and settle. A laptop has no
background process: each is worked out where it is asked, as before.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import logging
import threading
from collections.abc import Callable
from typing import Any

from ap_coder.core.store import reports
from ap_coder.erp import accruals, insights, recurring, spend
from ap_coder.web.pages import learning, sales_tax

ASKED_WITHIN = 24 * 3600.0  # at its start the refresher works out again the answers asked this long ago at most
POLL = 0.5  # seconds between two looks for a want while none waits
LEASE, RENEW = 30.0, 5.0  # its hold (``reports.REFRESHER``): askers count on it while it is renewed


def _day(text: str) -> dt.date:
    return dt.date.fromisoformat(text)


ANSWERS: dict[str, Callable[[Any, list[Any]], Any]] = {
    "spend_currencies": lambda store, a: spend.spend_currencies(store, _day(a[0]), _day(a[1])),
    "spend_figures": lambda store, a: spend.figures(store, _day(a[0]), _day(a[1]), a[2], a[3], a[4]),
    "insights": lambda store, a: insights.shown(store),
    "insights_operations": lambda store, a: insights.shown_operations(store, _day(a[0])),
    "vendor_workload": lambda store, a: insights.vendor_workload(store, int(a[0])),
    "sales_tax": lambda store, a: sales_tax.shown(store, _day(a[0]), _day(a[1]), str(a[2]), int(a[3]), int(a[4])),
    **{name: (lambda store, a, work=work: work(store)) for name, work in learning.SETTLED.items()},
    "recurring": lambda store, a: recurring.detected(store, _day(a[0]), float(a[1])),
    "received_not_invoiced": lambda store, a: accruals.received_not_invoiced(store, _day(a[0]), float(a[1])),
    "not_in_erp": lambda store, a: accruals.not_in_erp(store, _day(a[0]), float(a[1])),
    "vendor_totals": lambda store, a: store.vendor_summaries(settled=True),
}


def refresh(store: Any, want: dict[str, Any]) -> bool:
    """Work ``want`` (``Store.next_want``) out again through ``ANSWERS``, kept for every process, and drop it; one whose
    work fails (its arguments unreadable too) is logged and tried again after the others, then dropped
    (``reports.WANT_TRIES``). Whether it was worked out."""
    name, key = want["name"], want["asked"]
    try:
        work = ANSWERS[name]
        with reports.refreshing():
            work(store, json.loads(want["args"]))
    except Exception:  # noqa: BLE001 - any answer's failure: logged, the others go on
        logging.getLogger(__name__).warning("the %s answer was not worked out again (try %d of %d)", name,
                                            want["tries"] + 1, reports.WANT_TRIES, exc_info=True)  # fmt: skip
        store.want_done(name, key, failed=True)
        return False
    store.want_done(name, key)
    return True


class Refresher:
    """The wants worked out by the company server's background process (``app.start_background_work``), one at a time,
    while it holds ``reports.REFRESHER`` (one refresher a database; renewed on a thread of its own, an answer's work
    may last longer than the hold); at its start, those asked in the last ``ASKED_WITHIN`` first."""

    def __init__(self, store_factory: Callable[[], Any]) -> None:
        self.store_factory = store_factory
        self._halt = threading.Event()
        self.thread = threading.Thread(target=self._run, name="ap-coder-answers", daemon=True)

    def start(self) -> Refresher:
        self.thread.start()
        return self

    def stop(self) -> None:
        """Stop after the answer under way (``thread`` ends then), letting go of the hold."""
        self._halt.set()

    def _run(self) -> None:
        log = logging.getLogger(__name__)
        held = threading.Event()
        beat = threading.Thread(target=self._hold, args=(held,), name="ap-coder-answers-hold", daemon=True)
        beat.start()
        started = False
        while not self._halt.is_set():
            try:
                store = self.store_factory()
                want = None
                if held.is_set() and not store.restoring():
                    if not started:  # (those asked lately: after a restart or an update, worked out first)
                        waiting = store.want_again(reports.wall() - ASKED_WITHIN)
                        log.info("the reports' answers asked lately, waiting to be worked out again: %d", waiting)
                        started = True
                    want = store.next_want()
            except Exception:  # noqa: BLE001 - a busy database: looked at again
                log.debug("no answer to work out again looked for", exc_info=True)
                want = None
            if want is None:
                self._halt.wait(POLL)
                continue
            try:
                refresh(store, want)
            except Exception:  # noqa: BLE001 - its want not dropped (a busy database): taken again at the next look
                log.info("the %s answer's want was not dropped", want["name"], exc_info=True)
        beat.join(RENEW + 1)

    def _hold(self, held: threading.Event) -> None:
        """Take and renew the hold every RENEW while running (``held`` while it is this process's), then let go."""
        while not self._halt.is_set():
            try:
                if self.store_factory().take_lease(reports.REFRESHER, reports.OWNER, LEASE):
                    held.set()
                else:
                    held.clear()
            except Exception:  # noqa: BLE001 - a busy database: renewed at the next
                logging.getLogger(__name__).debug("the answers' hold not renewed", exc_info=True)
            self._halt.wait(RENEW)
        with contextlib.suppress(Exception):  # (else it runs out within LEASE)
            self.store_factory().drop_lease(reports.REFRESHER, reports.OWNER)
