"""The settled answers the company server's background process works out again (D-198).

A page that only shows a big answer (Spend, Month-end, Vendors, Insights, Sales tax, Learning, the queue's late
pill) settles it (``Store.shared_answer``'s ``settle``, D-164, D-197). Once it no longer holds, the web worker that is
asked it does not work it out (6 to 11 s at 300,000 invoices, its other requests waiting behind it): it shows the one
kept and leaves a want (``core/store/reports.py``); the ``Refresher`` here, a thread of the background process, works
the wants out one at a time, oldest first, and keeps each for every process. At its start it works out again those
asked in the last day (``ASKED_WITHIN``), so after a restart or an update nobody waits for the first one, then each
page's default view (``DEFAULTS``, D-200); from then on it keeps those and the latest question asked of each answer
warm (``WARM_AGAIN``), so the first person to open a report, in the morning too, takes a kept answer. On a database
that never had them, they are worked out before the server answers at all (``prepare``, D-204).

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
# A view kept warm (a page's default view, the latest question asked of an answer) is asked again once its answer is
# this old, a cheap take while it holds (looked at every WARM_LOOK seconds while no want waits), so one is always there
# to show (STALE_MOST): B-2052, B-2055
WARM_AGAIN, WARM_LOOK = reports.STALE_MOST / 2, 60.0
# Seconds the company server's first start on a database that never had the default views works them out at most,
# before it answers (``prepare``, D-204), looked at before each view, the one under way then finished: at 300,000
# invoices all 14 took 92 s (Month-end's three alone 121 s in another run). Month-end's, the longest by far, come
# first: a view under way at the bound is one of the quick ones.
PREPARE_MOST = 200.0


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


def ahead(store: Any, name: str, args_of: Callable[[Any], list[Any]], view: str) -> tuple[bool, str | None]:
    """Answer ``name`` asked ahead of its page (D-200): its arguments (``args_of``: a default view's, ``DEFAULTS``, or
    those a page asked it with) as a want keeps them (JSON), worked out through ``ANSWERS`` and kept for every process;
    one whose work fails (or its arguments') is logged, the others go on. (Whether it was worked out, the question it
    is kept under: its ``asked_key``, None when not known.)"""
    try:
        with reports.refreshing() as asked:
            ANSWERS[name](store, json.loads(json.dumps(args_of(store))))
    except Exception:  # noqa: BLE001 - any answer's failure: logged, the others go on
        logging.getLogger(__name__).warning("the %s answer's %s was not worked out ahead of its page", name, view,
                                            exc_info=True)  # fmt: skip
        return False, None
    return True, next((key for seen, key in reversed(asked) if seen == name), None)


def served_before(store: Any) -> bool:
    """Whether this database has had the reports' default views (D-204): an answer of any of ``DEFAULTS``' names kept by
    this AP Coder or any before it, or Month-end's billing facts (``Store.answered_before``). Then the server answers at
    once and they are worked out while it serves, as before D-204: an update keeps none of the version before's (an
    answer's version is AP Coder's own), and working them out before it answered made its downtime minutes (D-188)."""
    return bool(store.answered_before(sorted({name for name, _ in DEFAULTS})))


def prepare(store: Any, say: Callable[[str], None], most: float = PREPARE_MOST) -> tuple[int, int, float]:
    """The company server's first start on a database that never had the reports (``served_before``), before it
    answers (D-204): each default view (``DEFAULTS``, in that order) worked out as the refresher works it out
    (``ahead``), kept for every process (``store``: the company server's, ``background_answers``), so no page waits for
    it. Within ``most`` seconds, looked at before each view: past them the rest is left to the refresher. One that fails
    is logged and the next goes on. ``say``: told before the first and at the end. (worked out, left, seconds.)"""
    began, made, left = time.monotonic(), 0, 0
    say(f"Working out the reports' default views before the dashboard answers (this database never had them), "
        f"{most:g} seconds at most...")  # fmt: skip
    for n, (name, args_of) in enumerate(DEFAULTS):
        if time.monotonic() - began >= most:
            left = len(DEFAULTS) - n
            break
        made += ahead(store, name, args_of, "default view")[0]
    seconds = time.monotonic() - began
    past = f"; past {most:g} s, {left} left to the background process" if left else ""
    say(f"The reports' default views worked out before the dashboard answers: {made} of {len(DEFAULTS)}, in "
        f"{seconds:.1f} s{past}.")  # fmt: skip
    return made, left, seconds


class _Warm:
    """What the refresher keeps warm on one database from its start (D-200): each page's default view (``DEFAULTS``,
    asked anew each time: today's), worked out at the start in that order, and the latest question asked of each
    settled answer within ``ASKED_WITHIN`` (``answer_wants``, its arguments there: B-2055); the default views while
    anything was asked within ``ASKED_WITHIN``, or for that long after the start. Each is asked again, while no want
    waits, once its answer is ``WARM_AGAIN`` old (looked at every ``WARM_LOOK``): a cheap take while it holds, worked
    out again once something changed. One that failed, or whose answer could not be kept for every process (NaN, too
    big), is not asked again before the next start (B-2056)."""

    def __init__(self, began: float) -> None:
        self.began, self.look, self.first, self.made = began, 0.0, True, 0
        self.left: list[tuple[str, Callable[[Any], list[Any]], int | None, str | None]] = [
            (name, default, n, None) for n, (name, default) in enumerate(DEFAULTS)]  # fmt: skip
        self.keys: dict[int, str] = {}  # the question each default view was kept under last
        self.dropped: set[int] = set()  # default views not kept warm
        self.unkept: set[tuple[str, str]] = set()  # questions asked not kept warm

    def step(self, store: Any) -> bool:
        """One view asked (or those kept warm looked at): whether one was."""
        log = logging.getLogger(__name__)
        if not self.left and time.monotonic() >= self.look:
            self.look = time.monotonic() + WARM_LOOK
            self._due(store)
            log.debug("the reports' views kept warm: %d asked again", len(self.left))
        if not self.left:
            return False
        name, args_of, n, known = self.left.pop(0)
        worked, key = ahead(store, name, args_of, "question asked lately" if n is None else "default view")
        key = key or known
        if not worked or key is None or store.answer_made(name, key) is None:  # (failed, or nothing kept)
            self.dropped.update(() if n is None else (n,))
            self.unkept.update(() if key is None else ((name, key),))
        elif n is not None:
            self.keys[n] = key
        if self.first:
            self.made += worked and n is not None
            if not any(m is not None for *_, m, _ in self.left):
                log.info("the reports' default views worked out at the refresher's start: %d", self.made)
                self.first = False
        return True

    def _due(self, store: Any) -> None:
        """Those kept warm whose answer is ``WARM_AGAIN`` old (or none is kept), waiting to be asked again."""
        now = reports.wall()
        lately = [r for r in store.asked_lately(now - ASKED_WITHIN) if r["name"] in ANSWERS]
        warm = {(DEFAULTS[n][0], key) for n, key in self.keys.items() if n not in self.dropped}
        if lately or now - self.began <= ASKED_WITHIN:
            for n, (name, default) in enumerate(DEFAULTS):
                if n in self.dropped:
                    continue
                made = store.answer_made(name, self.keys[n]) if n in self.keys else None
                if made is None or now - made >= WARM_AGAIN:
                    self.left.append((name, default, n, None))
        for r in lately:
            asked = (r["name"], r["asked"])
            if asked not in warm and asked not in self.unkept and (r["made_at"] is None
                                                                    or now - r["made_at"] >= WARM_AGAIN):  # fmt: skip
                self.left.append((r["name"], lambda store, args=r["args"]: json.loads(args), None, r["asked"]))


class Refresher:
    """The wants worked out by the company server's background process (``app.start_background_work``), one at a time,
    while it holds ``reports.REFRESHER`` (one refresher a database; renewed on a thread of its own, an answer's work
    may last longer than the hold); at its start, those asked in the last ``ASKED_WITHIN`` first, then each of
    ``DEFAULTS`` while no want waits (D-200), and from then on the views kept warm (``_Warm``). Its start is done again
    for another database (a restore while it runs, B-2053)."""

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
        warm: _Warm | None = None  # (the views it keeps warm, D-200)
        while not self._halt.is_set():
            try:
                store = self.store_factory()
                want = None
                if held.is_set() and not store.restoring():
                    if (db := store.database_id()) != started:  # (those asked lately: worked out first)
                        waiting = store.want_again(reports.wall() - ASKED_WITHIN)
                        log.info("the reports' answers asked lately, waiting to be worked out again: %d", waiting)
                        started, warm = db, _Warm(reports.wall())
                    want = store.next_want()
                    if want is None and warm is not None and warm.step(store):  # (a person's want first)
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
