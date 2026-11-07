"""The settled answers the company server's background process works out again (D-198).

A page that only shows a big answer (Spend, Month-end, Vendors, Insights, Sales tax, Learning, the queue's late
pill) settles it (``Store.shared_answer``'s ``settle``, D-164, D-197). Once it no longer holds, the web worker that is
asked it does not work it out (6 to 11 s at 300,000 invoices, its other requests waiting behind it): it shows the one
kept and leaves a want (``core/store/reports.py``); the ``Refresher`` here, a thread of the background process, works
the wants out one at a time, oldest first, and keeps each for every process. At its start it works out again those
asked in the last day (``ASKED_WITHIN``), so after a restart or an update nobody waits for the first one; with none
(a first start: a database never served, or not asked anything for a day), each page's default view (``DEFAULTS``,
D-200), so the first person to open a report takes a kept answer too.

``ANSWERS``: each settled answer by its name, and how it is worked out again from what was asked (the ``again`` its
caller gives, JSON): by its caller itself, so with the same version, format, stamp and settle. ``DEFAULTS``: what each
page asks opened with nothing in its address, today, picked by the page's own code. A laptop has no background
process: each is worked out where it is asked, as before.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import logging
import threading
import time
from collections.abc import Callable
from typing import Any

from ap_coder.core.store import reports
from ap_coder.erp import accruals, insights, recurring, spend
from ap_coder.web.core import reference_or_none
from ap_coder.web.pages import learning, month_end, sales_tax
from ap_coder.web.pages import spend as spend_page

ASKED_WITHIN = 24 * 3600.0  # at its start the refresher works out again the answers asked this long ago at most
POLL = 0.5  # seconds between two looks for a want while none waits
LEASE, RENEW = 30.0, 5.0  # its hold (``reports.REFRESHER``): askers count on it while it is renewed
# A first start's default view nobody asked yet is asked again once its answer is this old, a cheap take while it
# holds (looked at every WARM_LOOK seconds while no want waits), so one is always there to show (STALE_MOST), for
# ASKED_WITHIN after the start at most (B-2051)
WARM_AGAIN, WARM_LOOK = reports.STALE_MOST / 2, 60.0


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


def _text(args: Any) -> list[Any]:
    """Arguments as the answer's caller gives them to be worked out again (its ``again``): each date as ISO text."""
    return [a.isoformat() if isinstance(a, dt.date) else a for a in args]


def _spend_figures(store: Any) -> list[Any]:
    start, end = spend.default_period()
    return _text(spend_page.asked(store, reference_or_none(store), start, end)[3])


# Each page's default view (opened with nothing in its address, today) by the answers it asks, as their arguments are
# worked out again (``ANSWERS``), picked by the page's own code: at a first start the refresher works each out, in this
# order, before anyone asks (D-200). Month-end's first, the biggest (its lines by invoice, D-194: 12 to 50 s at a first
# start at 300,000 invoices); the recurring vendors twice: month-end's for its period end, the queue's (every
# person's first page) and the Vendors page's for today.
DEFAULTS: tuple[tuple[str, Callable[[Any], list[Any]]], ...] = (
    ("received_not_invoiced", lambda store: _text(month_end.asked())),
    ("not_in_erp", lambda store: _text(month_end.asked())),
    ("recurring", lambda store: _text(month_end.asked())),
    ("recurring", lambda store: _text((dt.date.today(), recurring.SETTLE_SECONDS))),
    ("spend_currencies", lambda store: _text(spend.default_period())),
    ("spend_figures", _spend_figures),
    ("sales_tax", lambda store: _text(sales_tax.asked())),
    *((name, lambda store: []) for name in learning.SETTLED),
    ("vendor_totals", lambda store: []),
    ("vendor_workload", lambda store: [insights.WORKLOAD_MIN_INVOICES]),
    ("insights", lambda store: []),
    ("insights_operations", lambda store: _text((dt.date.today(),))),
)
# Each answer of ``ANSWERS`` with no default view (one only a person's question asks), and why: (name, why).
NO_DEFAULT: tuple[tuple[str, str], ...] = ()


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


def default_view(store: Any, name: str, default: Callable[[Any], list[Any]]) -> tuple[bool, str | None]:
    """At a first start (D-200): answer ``name`` as a page asks it opened with nothing in its address (``default``,
    one of ``DEFAULTS``), its arguments as a want keeps them (JSON), worked out through ``ANSWERS`` and kept for every
    process; one whose work fails (or its arguments') is logged, the others go on. (Whether it was worked out, the
    question it is kept under: its ``asked_key``, None when not known.)"""
    try:
        with reports.refreshing() as asked:
            ANSWERS[name](store, json.loads(json.dumps(default(store))))
    except Exception:  # noqa: BLE001 - any answer's failure: logged, the others go on
        logging.getLogger(__name__).warning("the %s answer's default view was not worked out at a first start", name,
                                            exc_info=True)  # fmt: skip
        return False, None
    return True, next((key for seen, key in reversed(asked) if seen == name), None)


class _Views:
    """A first start's default views on one database (D-200): each worked out once, in ``DEFAULTS``' order; then those
    nobody asked since the start kept warm, for ``ASKED_WITHIN`` at most (B-2051)."""

    def __init__(self, began: float) -> None:
        self.began = began  # (``reports.wall``)
        self.left = list(range(len(DEFAULTS)))  # those to ask now, by their place in DEFAULTS
        self.warm: set[int] = set(self.left)  # those nobody asked yet
        self.keys: dict[int, str] = {}  # the question each was kept under last
        self.first, self.made, self.look = True, 0, 0.0

    def step(self, store: Any) -> bool:
        """One default view asked (or those kept warm looked at): whether one was."""
        log = logging.getLogger(__name__)
        if not self.left and self.warm and time.monotonic() >= self.look:
            self.look = time.monotonic() + WARM_LOOK
            self._due(store)
            log.debug("the reports' default views nobody asked yet: %d of %d asked again", len(self.left),
                      len(self.warm))  # fmt: skip
        if not self.left:
            return False
        n = self.left.pop(0)
        worked, key = default_view(store, *DEFAULTS[n])
        if key is None:
            self.warm.discard(n)  # (failed, logged, or its question not known: not kept warm)
        else:
            self.keys[n] = key
        if self.first:
            self.made += worked
            if not self.left:
                log.info("the reports' default views worked out at a first start: %d", self.made)
                self.first = False
        return True

    def _due(self, store: Any) -> None:
        """Those kept warm that are due again (their answer ``WARM_AGAIN`` old) waiting to be asked; one asked by a
        page since the start is left to D-198 (worked out again as it is asked)."""
        now = reports.wall()
        if now - self.began > ASKED_WITHIN:
            self.warm.clear()
            return
        for n in sorted(self.warm):
            made, asked = store.answer_seen(DEFAULTS[n][0], self.keys[n])
            if asked is not None and asked >= self.began:
                self.warm.discard(n)
            elif made is None or now - made >= WARM_AGAIN:
                self.left.append(n)


class Refresher:
    """The wants worked out by the company server's background process (``app.start_background_work``), one at a time,
    while it holds ``reports.REFRESHER`` (one refresher a database; renewed on a thread of its own, an answer's work
    may last longer than the hold); at its start, those asked in the last ``ASKED_WITHIN`` first, and with none (a
    first start), each of ``DEFAULTS`` while no want waits (D-200), kept warm until asked (B-2051). Its start is done
    again for another database (a restore while it runs, B-2052)."""

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
        started: str | None = None  # (the database its start was done for)
        views: _Views | None = None  # (a first start's default views, D-200)
        while not self._halt.is_set():
            try:
                store = self.store_factory()
                want = None
                if held.is_set() and not store.restoring():
                    if (db := store.database_id()) != started:  # (those asked lately: worked out first)
                        waiting = store.want_again(reports.wall() - ASKED_WITHIN)
                        log.info("the reports' answers asked lately, waiting to be worked out again: %d", waiting)
                        started, views = db, None if waiting else _Views(reports.wall())  # (none: a first start)
                    want = store.next_want()
                    if want is None and views is not None and views.step(store):  # (a person's want first)
                        continue
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
