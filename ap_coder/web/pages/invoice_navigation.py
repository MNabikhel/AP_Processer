"""Where the review screen stands in the queue: Previous / Next, and where to go after an action.

No routes here. ``navigation``: the invoices Previous / Next step through, the queue as shown (its filter, search
and sort, carried in the address), past those someone else has open (D-184); ``next_url``: after approving, parking
or rejecting, the next one in the queue as shown.
"""

from __future__ import annotations

from typing import Any

from fastapi import Request

from ap_coder.core.store import REVIEW, Store
from ap_coder.web.core import reference_or_none
from ap_coder.web.pages.invoice_drafts import names_text, open_elsewhere
from ap_coder.web.pages.invoice_router import _with_query
from ap_coder.web.pages.review import attention, draft_reasons, filter_queue, sort_queue


def navigation(store: Store, invoice_id: int, query: dict[str, str], request: Request | None = None) -> dict[str, Any]:
    """The invoices Previous / Next step through: the queue as shown (filter, search, sort in ``query``), or the
    whole queue if this invoice isn't part of that view. ``request``: the person asking, whose invoices with edits
    kept the queue shows under Needs attention (B-1620). In the queue's own order (no search, sort or view of its
    own) only its ids are read (B-1833)."""
    show = query.get("show", "all")
    if show not in ("attention", "ready") and not query.get("q") and query.get("sort", "priority") == "priority":
        ids = store.listed_ids(REVIEW)
    else:
        pending = store.list_invoices(REVIEW)
        reasons = attention(store, reference_or_none(store), pending) if show in ("attention", "ready") else {}
        if show in ("attention", "ready") and request is not None:
            reasons.update(draft_reasons(request, pending))
        shown = {"attention": [i for i in pending if i["id"] in reasons],
                 "ready": [i for i in pending if i["id"] not in reasons]}.get(show, pending)  # fmt: skip
        shown = sort_queue(filter_queue(shown, query.get("q", "")), query.get("sort", "priority"))
        ids = [i["id"] for i in shown]
        if invoice_id not in ids:
            ids = [i["id"] for i in pending]
    if invoice_id not in ids:
        return {"ids": ids, "position": None, "count": len(ids), "prev": "", "next": "", "next_id": None, "busy": {},
                "skipped": ""}  # fmt: skip
    position = ids.index(invoice_id)
    url = lambda i: _with_query(f"/review/{i}", query)  # noqa: E731
    # The ones someone else has open are passed over (D-184), unless every other one is
    busy = open_elsewhere(request, store) if request is not None else {}
    if all(i in busy for i in ids if i != invoice_id):
        busy = {}
    prev, before = _step(ids, position, busy, -1)
    after, passed = _step(ids, position, busy, 1)
    later = [i for i in ids[position + 1 :] if i not in busy]
    return {
        "ids": ids,
        "position": position,
        "count": len(ids),
        "done_pct": round((position + 1) / len(ids) * 100),
        # Always enabled (wrapping around), so Alt+Left/Right never fall through to the browser's Back/Forward.
        "prev": url(prev),
        "next": url(after),
        # The one after it as the screen showed it (none for the last): where an action on it goes next, even if
        # editing moved it in the queue's order meanwhile (B-426).
        "next_id": later[0] if later else None,
        "busy": busy,
        "skipped": _skipped(busy, before, passed),
    }


def _step(ids: list[int], position: int, busy: dict[int, list[str]], by: int) -> tuple[int, list[int]]:
    """The invoice ``by`` (1 the next, -1 the previous) from ``position`` (wrapping around), past those someone else
    has open (``busy``), and the ones passed over."""
    passed = []
    for k in range(1, len(ids)):
        found = ids[(position + by * k) % len(ids)]
        if found not in busy:
            return found, passed
        passed.append(found)
    return ids[(position + by) % len(ids)], []


def _skipped(busy: dict[int, list[str]], before: list[int], after: list[int]) -> str:
    """What the screen says of the invoices Previous / Next pass over ("" when none is)."""
    passed = list(dict.fromkeys(after + before))
    if not passed:
        return ""
    names = list(dict.fromkeys(name for i in passed for name in busy[i]))
    which = "Previous and Next skip" if before and after else "Next skips" if after else "Previous skips"
    return f"{which} {len(passed)} that {names_text(names)} {'has' if len(names) == 1 else 'have'} open."


def next_url(store: Store, invoice_id: int, query: dict[str, str], seen_next: Any = None,
             request: Request | None = None) -> str:  # fmt: skip
    """After an action on this invoice: the next one in the queue as shown (or the queue when none is left).
    ``seen_next``: the invoice that followed it when its screen was drawn (the form's ``next_id``), opened when it is
    still waiting: the edits refresh the invoice's confidence, which moves it in the queue's order while it is open,
    and the one after its new place would skip those in between (B-426). ``request``: as ``navigation``'s."""
    nav = navigation(store, invoice_id, query, request)
    ids, position = nav["ids"], nav["position"]
    remaining = [i for i in ids if i != invoice_id]
    if not remaining:
        return _with_query("/review", query)
    try:
        seen = int(seen_next or 0)
    except (TypeError, ValueError):
        seen = 0
    free = [i for i in remaining if i not in nav["busy"]] or remaining  # none someone else has open (D-184)
    if seen in free:
        return _with_query(f"/review/{seen}", query)
    if position is None or position >= len(remaining):  # it was the last one: back to the first, as said (B-998)
        return _with_query(f"/review/{free[0]}", query)
    return _with_query(f"/review/{next(i for i in remaining[position:] + remaining[:position] if i in free)}", query)
