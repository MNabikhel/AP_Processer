"""Many people on one review queue (D-184): who else has an invoice open (the screen and its queue row), Previous /
Next past those, a screen out of date said once and read-only, and the revisions a screen's checks carry so two tabs
or two people never write over each other's edits. On a laptop nobody else is shown; the revisions hold between its
tabs."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ap_coder.core.store import APPROVED, REVIEW, Store
from ap_coder.web.app import create_app
from tests.browser import browser_on, wait_until
from tests.stores import cost_centers_demo_store  # noqa: F401  (the tests' store)
from tests.web.test_web_invoice import clean_invoice, flash_text, form_of
from tests.webclient import PAGE, client_for, enrol, sign_in, web_settings

GOOD = "correct horse battery"


@pytest.fixture
def laptop(store, monkeypatch):
    monkeypatch.delenv("AP_SERVER_MODE", raising=False)
    monkeypatch.setenv("AP_REVIEWER", "Jordan Lee")
    return client_for(store)


def _account(store: Store, username: str, name: str, role: str = "clerk") -> None:
    uid = store.add_user(username, GOOD, role=role, display_name=name)
    with store._conn() as conn:
        conn.execute("UPDATE users SET must_change = 0 WHERE id = ?", (uid,))
    enrol(store, uid)


def _person(store: Store, username: str, name: str, role: str = "clerk") -> TestClient:
    """A colleague on the company server, logged in on a browser of their own."""
    _account(store, username, name, role)
    client = TestClient(create_app(web_settings(), store, start_reader=False, server=True), base_url="https://localhost",
                        follow_redirects=False)  # fmt: skip
    assert sign_in(client, username, GOOD).status_code == 200
    return client


def _draft_rev(html: str) -> str:
    """The draft revision the approve bar holds (the page's, or a check's bar drawn again)."""
    return re.search(r'name="draft_rev" form="inv" value="([^"]+)"', html)[1]


def _screen(page: str) -> dict[str, str]:
    """The revisions a review screen is at, and its own id, as its form posts them."""
    return {"rev": re.search(r'name="rev" value="(\d+)"', page)[1], "draft_rev": _draft_rev(page),
            "screen": re.search(r'name="screen" value="([0-9a-f]+)"', page)[1]}  # fmt: skip


def _open(client: TestClient, invoice_id: int) -> dict[str, str]:
    page = client.get(f"/review/{invoice_id}")
    assert page.status_code == 200, page.text
    return _screen(page.text)


def _poll(client: TestClient, invoice_id: int, screen: dict[str, str]) -> dict[str, Any]:
    return client.get(f"/review/{invoice_id}/reader", params={"seen": "", "rev": screen["rev"],
                                                              "draft": screen["draft_rev"],
                                                              "screen": screen["screen"]}).json()  # fmt: skip


# --- Two tabs, two people: nobody's edits written over --------------------------------------------------------------


def test_two_tabs_on_one_invoice_the_second_to_check_is_told_and_writes_nothing_over(laptop, store):
    """B-1915: each check posts the whole screen and replaced the person's draft, so the tab that checked last wrote
    over the other's edits without a word. Each check carries the draft's revision; one behind it is refused, says
    when the other tab saved, and the edits kept are the first tab's."""
    inv_id = store.list_invoices(REVIEW)[0]["id"]
    first, second = _open(laptop, inv_id), _open(laptop, inv_id)
    one = laptop.post(f"/review/{inv_id}/check", data={**form_of(store, inv_id, vendor_name="Typed in tab one"),
                                                         **first}, headers=PAGE)  # fmt: skip
    assert one.status_code == 200 and one.json()["draft"]
    first["draft_rev"] = _draft_rev(one.json()["sections"]["bar"])  # (each check draws it again)
    two = laptop.post(f"/review/{inv_id}/check", data={**form_of(store, inv_id, vendor_name="Typed in tab two"),
                                                         **second}, headers=PAGE)  # fmt: skip
    assert two.status_code == 409
    stale = two.json()["stale"]
    assert stale["text"].startswith("Edited in another tab or window today at ") and "read-only" in stale["sub"]
    assert stale["url"] == f"/review/{inv_id}"
    assert store.invoice_draft(inv_id, "")["header"]["vendor_name"] == "Typed in tab one"
    assert _poll(laptop, inv_id, second)["stale"]["text"] == stale["text"]  # its poll says the same
    assert "stale" not in _poll(laptop, inv_id, first)  # the first tab is at it
    again = laptop.post(f"/review/{inv_id}/check", data={**form_of(store, inv_id, vendor_name="Tab one again"),
                                                           **first}, headers=PAGE)  # fmt: skip
    assert again.status_code == 200  # and goes on checking


def test_two_tabs_whose_checks_overlap_the_second_to_write_is_refused(laptop, store, monkeypatch):
    """B-1938: a check compared its screen's revision with the draft before working it out, and wrote the draft after:
    two tabs whose checks overlapped both passed, and the one that ended last wrote over the other's edits without a
    word. The draft is read again in the write's own transaction: the second to write is refused as out of date."""
    import threading

    from ap_coder.web import invoice_view as view

    inv_id = store.list_invoices(REVIEW)[0]["id"]
    screens = {"one": _open(laptop, inv_id), "two": _open(laptop, inv_id)}
    real, both = view.analyse, threading.Barrier(2, timeout=20)

    def overlapped(*args: Any, **kwargs: Any) -> Any:
        both.wait()  # each check has compared its revisions: both are worked out at once
        return real(*args, **kwargs)

    monkeypatch.setattr(view, "analyse", overlapped)
    answers: dict[str, Any] = {}

    def check(tab: str) -> None:
        data = {**form_of(store, inv_id, vendor_name=f"From tab {tab}"), **screens[tab]}
        answers[tab] = laptop.post(f"/review/{inv_id}/check", data=data, headers=PAGE)

    tabs = [threading.Thread(target=check, args=(tab,)) for tab in screens]
    for tab in tabs:
        tab.start()
    for tab in tabs:
        tab.join(60)
    assert sorted(a.status_code for a in answers.values()) == [200, 409]
    first = next(tab for tab, a in answers.items() if a.status_code == 200)
    refused = next(a for a in answers.values() if a.status_code == 409).json()["stale"]
    assert refused["text"].startswith("Edited in another tab or window today at ")
    assert store.invoice_draft(inv_id, "")["header"]["vendor_name"] == f"From tab {first}"


def test_a_screen_whose_own_check_answered_after_its_poll_asked_is_not_out_of_date(laptop, store):
    """The poll (or a check sent while one was under way) carries the revision from before the screen's own check
    saved the draft: written by that screen, it is no other tab's."""
    inv_id = store.list_invoices(REVIEW)[0]["id"]
    screen = _open(laptop, inv_id)
    data = {**form_of(store, inv_id, vendor_name="Typed here"), **screen}
    assert laptop.post(f"/review/{inv_id}/check", data=data, headers=PAGE).status_code == 200
    assert "stale" not in _poll(laptop, inv_id, screen)
    data["vendor_name"] = "Typed here, again"
    assert laptop.post(f"/review/{inv_id}/check", data=data, headers=PAGE).status_code == 200


def test_a_screen_whose_own_check_undid_its_edits_is_not_out_of_date(laptop, store):
    """B-1937: a screen's own check that dropped the draft (its edits put back as stored, Undo after a line action)
    made that screen out of date: its poll sent meanwhile said "Edits undone in another tab or window" and turned it
    read-only, and an approval sent meanwhile was refused. The screen that dropped it is remembered as its own; another
    tab at those edits is still told."""
    inv_id = clean_invoice(laptop, store)
    kept = laptop.post(f"/review/{inv_id}/check", data=form_of(store, inv_id, invoice_number="TYPED-BEFORE"),
                       headers=PAGE)  # fmt: skip
    assert kept.json()["draft"]  # edits from an earlier visit
    screen, other = _open(laptop, inv_id), _open(laptop, inv_id)
    back = laptop.post(f"/review/{inv_id}/check", data={**form_of(store, inv_id), **screen}, headers=PAGE)
    assert back.status_code == 200 and not back.json()["draft"] and store.invoice_draft(inv_id, "") is None
    assert "stale" not in _poll(laptop, inv_id, screen)  # (sent while its check was under way)
    assert _poll(laptop, inv_id, other)["stale"]["text"] == "Edits undone in another tab or window."
    answer = laptop.post(f"/review/{inv_id}/approve", data={**form_of(store, inv_id), **screen}, headers=PAGE)
    assert answer.json()["ok"] and store.get_invoice(inv_id)["status"] == APPROVED


def test_the_first_check_of_a_screen_whose_draft_was_left_to_it_is_not_out_of_date(laptop, store, monkeypatch):
    """The page keeps its draft once drawn, unless another writer holds the database then (left to the next check):
    the screen is at the draft either way."""
    from ap_coder.web.pages import invoice

    inv_id = store.list_invoices(REVIEW)[0]["id"]
    laptop.post(f"/review/{inv_id}/check", data=form_of(store, inv_id, vendor_name="Kept before"), headers=PAGE)
    real = invoice._keep_checked

    def busy(*args: Any, **kwargs: Any) -> bool:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(invoice, "_keep_checked", busy)
    screen = _open(laptop, inv_id)
    monkeypatch.setattr(invoice, "_keep_checked", real)
    data = {**form_of(store, inv_id, vendor_name="Typed after"), **screen}
    assert laptop.post(f"/review/{inv_id}/check", data=data, headers=PAGE).status_code == 200


def test_a_line_action_from_a_tab_behind_is_refused_and_one_at_the_draft_says_its_revision(laptop, store):
    inv_id = store.list_invoices(REVIEW)[0]["id"]
    first, second = _open(laptop, inv_id), _open(laptop, inv_id)
    data = form_of(store, inv_id)
    gl = data["line_gl"][0]
    split = {"split_line": data["line_number"][0], "part_gl": [gl, gl], "part_pct": ["50", "50"]}
    done = laptop.post(f"/review/{inv_id}/split", data={**data, **split, **first}, headers=PAGE).json()
    assert done["ok"] and done["draft_rev"] != first["draft_rev"]
    refused = laptop.post(f"/review/{inv_id}/split", data={**data, **split, **second}, headers=PAGE)
    assert refused.status_code == 409 and refused.json()["stale"]["text"].startswith("Edited in another tab")
    at_it = {**first, "draft_rev": done["draft_rev"]}
    assert laptop.post(f"/review/{inv_id}/check", data={**data, **at_it}, headers=PAGE).status_code == 200


def test_a_screen_drawn_before_an_approval_was_undone_cannot_approve_over_it(laptop, store):
    """B-1917: approved in one tab (or by someone else), the approval undone: back in the queue with its coding, the
    screen drawn before it still checked and approved, writing its old coding over the one approved. Its revision is
    behind the invoice's: its checks are refused once, and its approval as they are (B-1936)."""
    inv_id = clean_invoice(laptop, store)
    old = _open(laptop, inv_id)
    data = form_of(store, inv_id, invoice_number="APPROVED-1")
    assert laptop.post(f"/review/{inv_id}/approve", data=data, headers=PAGE).json()["ok"]
    assert laptop.post(f"/review/{inv_id}/undo-approve", headers=PAGE).json()["ok"]
    assert store.get_invoice(inv_id)["status"] == REVIEW
    stale = laptop.post(f"/review/{inv_id}/check", data={**form_of(store, inv_id), **old}, headers=PAGE)
    assert stale.status_code == 409 and stale.json()["stale"]["text"].startswith("Reopened by Jordan Lee today at ")
    answer = laptop.post(f"/review/{inv_id}/approve", data={**form_of(store, inv_id, invoice_number="OLD"), **old},
                         headers=PAGE)  # fmt: skip
    assert answer.status_code == 409 and answer.json()["stale"] == stale.json()["stale"]
    inv = store.get_invoice(inv_id)
    assert inv["status"] == REVIEW and inv["final_output"]["invoice_number"] == "APPROVED-1"


def test_park_or_reject_from_a_screen_drawn_before_the_invoice_changed_is_refused_as_approve_is(laptop, store):
    """B-2502: Park and Reject posted no revision, so a screen drawn before the invoice was approved and the approval
    undone (or sent to its manager) parked or rejected it without a word of what had changed. Their forms post the
    screen's revision and are refused as Approve is (D-184): in the queue, said as its check says it; once it left
    it, "changed meanwhile"."""
    inv_id = clean_invoice(laptop, store)
    page = laptop.get(f"/review/{inv_id}").text
    old = _screen(page)
    for action in ("park", "reject"):
        posted = re.search(rf'action="/review/{inv_id}/{action}">(.*?)</form>', page, re.S)[1]
        assert f'name="rev" value="{old["rev"]}"' in posted
    assert laptop.post(f"/review/{inv_id}/approve", data=form_of(store, inv_id), headers=PAGE).json()["ok"]
    assert laptop.post(f"/review/{inv_id}/undo-approve", headers=PAGE).json()["ok"]
    for action in ("park", "reject"):
        answer = laptop.post(f"/review/{inv_id}/{action}", data={"reason": "not ours", "rev": old["rev"]},
                             headers=PAGE)  # fmt: skip
        assert answer.status_code == 409 and answer.json()["stale"]["text"].startswith("Reopened by Jordan Lee")
    assert store.get_invoice(inv_id)["status"] == REVIEW
    store.park_invoice(inv_id, "Bo Chen", "waiting for the buyer")  # left the queue
    answer = laptop.post(f"/review/{inv_id}/reject", data={"reason": "not ours", "rev": old["rev"]}, headers=PAGE)
    assert "changed meanwhile" in flash_text(laptop, answer.json())
    assert store.get_invoice(inv_id)["status"] != "rejected"
    store.unpark_invoice(inv_id, "Bo Chen")
    now = _open(laptop, inv_id)  # brought back: a screen drawn now rejects it
    assert laptop.post(f"/review/{inv_id}/reject", data={"reason": "x", "rev": now["rev"]}, headers=PAGE).json()["ok"]


def test_approve_or_send_to_the_manager_from_a_tab_behind_is_refused_as_its_check_is(laptop, store):
    """B-1936: Approve (or Send to the manager) from a tab behind the other's draft answered "changed meanwhile", a
    redirect to the invoice that the page followed: drawn again from the other tab's draft, what was typed in this
    one was gone (typed, then Approve at once: no check is sent first). While the invoice is in the queue it is
    refused as its check is (409, ``stale``: the screen keeps its boxes); once it left it, "changed meanwhile"."""
    inv_id = clean_invoice(laptop, store)
    one, two = _open(laptop, inv_id), _open(laptop, inv_id)
    data = {**form_of(store, inv_id, vendor_name="From tab one"), **one}
    assert laptop.post(f"/review/{inv_id}/check", data=data, headers=PAGE).json()["draft"]
    typed = {**form_of(store, inv_id, invoice_number="TYPED-IN-TWO"), **two, "manager_name": "Max Manager"}
    for action in ("approve", "manager"):
        answer = laptop.post(f"/review/{inv_id}/{action}", data=typed, headers=PAGE)
        assert answer.status_code == 409 and "redirect" not in answer.json()
        assert answer.json()["stale"]["text"].startswith("Edited in another tab or window today at ")
    assert store.get_invoice(inv_id)["status"] == REVIEW
    assert store.invoice_draft(inv_id, "")["header"]["vendor_name"] == "From tab one"
    store.approve_invoice(inv_id, dict(store.get_invoice(inv_id)["ai_output"]), "Bo Chen")  # left the queue
    for action in ("approve", "manager"):
        answer = laptop.post(f"/review/{inv_id}/{action}", data=typed, headers=PAGE).json()
        assert "changed meanwhile" in flash_text(laptop, answer)


def test_a_screen_approved_meanwhile_says_once_who_approved_it_and_when(laptop, store):
    """B-1916: approved in another tab or by someone else, the screen stayed editable and every edit toasted "This
    invoice is no longer in the review queue: reload the page." again. Its poll (and its next check) says once what
    was done, by whom and when, and the screen turns read-only; the poll stops."""
    inv_id = clean_invoice(laptop, store)
    screen = _open(laptop, inv_id)
    assert laptop.post(f"/review/{inv_id}/approve", data=form_of(store, inv_id), headers=PAGE).json()["ok"]
    assert store.get_invoice(inv_id)["status"] == APPROVED
    polled = _poll(laptop, inv_id, screen)
    assert re.fullmatch(r"Approved by Jordan Lee today at \d{1,2}:\d\d [ap]\.m\.", polled["stale"]["text"])
    assert not polled["poll"]
    check = laptop.post(f"/review/{inv_id}/check", data={**form_of(store, inv_id), **screen}, headers=PAGE)
    assert check.status_code == 409 and check.json()["stale"] == polled["stale"]


def test_a_line_action_on_a_screen_whose_invoice_left_the_queue_says_once_what_was_done(laptop, store):
    """B-1940: a line action (a split, Apply to all lines…) from a screen whose invoice was approved meanwhile got the
    old toast "This invoice is no longer in the review queue: reload the page." and the screen stayed editable: the
    queue was looked at before the screen's revision. It is refused as its check is, saying what was done."""
    inv_id = clean_invoice(laptop, store)
    screen = _open(laptop, inv_id)
    assert laptop.post(f"/review/{inv_id}/approve", data=form_of(store, inv_id), headers=PAGE).json()["ok"]
    data = form_of(store, inv_id)
    gl = data["line_gl"][0]
    split = {"split_line": data["line_number"][0], "part_gl": [gl, gl], "part_pct": ["50", "50"]}
    refused = laptop.post(f"/review/{inv_id}/split", data={**data, **split, **screen}, headers=PAGE)
    assert refused.status_code == 409
    assert re.fullmatch(r"Approved by Jordan Lee today at \d{1,2}:\d\d [ap]\.m\.", refused.json()["stale"]["text"])
    plain = laptop.post(f"/review/{inv_id}/split", data={**data, **split}, headers=PAGE)  # not the screen's: as before
    assert plain.status_code == 409 and "no longer in the review queue" in plain.json()["error"]


def test_a_screen_of_an_invoice_deleted_meanwhile_says_so_and_offers_the_queue(laptop, store):
    inv_id = store.list_invoices(REVIEW)[0]["id"]
    screen = _open(laptop, inv_id)
    store.delete_invoice(inv_id, actor="Sam Rivera")
    polled = _poll(laptop, inv_id, screen)
    assert polled["stale"]["text"].startswith("Deleted by Sam Rivera today at ")
    assert polled["stale"]["url"] == "/review" and polled["stale"]["link"] == "Open the queue"


def test_a_page_without_the_screens_revisions_is_checked_as_before(laptop, store):
    """A form posted without them (not the screen's: a script) is not compared, and an invoice gone from the queue
    still answers that plainly."""
    inv_id = clean_invoice(laptop, store)
    assert laptop.post(f"/review/{inv_id}/approve", data=form_of(store, inv_id), headers=PAGE).json()["ok"]
    answer = laptop.post(f"/review/{inv_id}/check", data=form_of(store, inv_id), headers=PAGE)
    assert answer.status_code == 409 and "no longer in the review queue" in answer.json()["error"]


# --- Who else has it open (the company server) ------------------------------------------------------------------------


def test_on_the_server_the_screen_and_its_queue_row_say_who_else_has_it_open_never_their_email(store):
    ann = _person(store, "ann", "Ann Lee")
    bo = _person(store, "bo", "bo.chen@example.com")  # an account whose name is its email
    inv_id = store.list_invoices(REVIEW)[0]["id"]
    _open(ann, inv_id)
    page = bo.get(f"/review/{inv_id}").text
    assert "Ann Lee is working on this invoice too." in page
    assert "Ann Lee has it open" in bo.get("/review").text
    screen = _screen(ann.get(f"/review/{inv_id}").text)
    assert _poll(ann, inv_id, screen)["others"] == "bo.chen is working on this invoice too."
    queue = ann.get("/review").text
    assert "bo.chen has it open" in queue
    assert "bo.chen@example.com" not in queue and "bo.chen@example.com" not in ann.get(f"/review/{inv_id}").text
    assert "Ann Lee is working" not in ann.get(f"/review/{inv_id}").text  # never oneself


def test_on_the_server_who_has_it_open_is_renewed_by_the_poll_and_only_by_who_may_edit(store, monkeypatch):
    """The page writes it once; its poll renews it at most every 45 s, nothing in between; a Read-only person
    looking at it is not working on it."""
    from ap_coder.web.pages import invoice_drafts

    ann, reader = _person(store, "ann", "Ann Lee"), _person(store, "rita", "Rita Read", role="readonly")
    inv_id = store.list_invoices(REVIEW)[0]["id"]
    screen = _open(ann, inv_id)
    reader.get(f"/review/{inv_id}")
    assert {name for people in store.open_by().values() for name in people.values()} == {"Ann Lee"}
    writes: list[bool] = []
    real = store.keep_open
    monkeypatch.setattr(type(store), "keep_open", lambda self, *a, **k: writes.append(real(*a, **k)) or writes[-1])
    _poll(ann, inv_id, screen)
    assert writes == [False]  # opened a moment ago
    with store._conn() as conn:
        conn.execute("UPDATE ephemeral SET expires_at = expires_at - ? WHERE kind = ?",
                     (invoice_drafts.OPEN_RENEW_SECONDS + 1, f"open:{inv_id}"))  # fmt: skip
    _poll(ann, inv_id, screen)
    assert writes == [False, True]
    reader.get(f"/review/{inv_id}/reader")
    assert writes == [False, True]  # (the Read-only poll renews nothing)


def test_on_the_server_next_and_previous_skip_the_invoices_someone_else_has_open(store):
    ann, bo = _person(store, "ann", "Ann Lee"), _person(store, "bo", "Bo Chen")
    ids = store.listed_ids(REVIEW)
    assert len(ids) >= 4
    first, second, third = ids[0], ids[1], ids[2]
    ann.get(f"/review/{second}")
    page = bo.get(f"/review/{first}").text
    assert f'href="/review/{third}" data-nav="next"' in page
    assert "Next skips 1 that Ann Lee has open." in page
    assert f'name="next_id" value="{third}"' in page  # and the one an action on it goes to next
    for other in ids[2:]:  # every other one open by someone: none skipped
        ann.get(f"/review/{other}")
    page = bo.get(f"/review/{first}").text
    assert f'href="/review/{second}" data-nav="next"' in page and "skip" not in page.split('data-nav="next"')[0][-400:]


def test_on_the_server_the_next_after_an_action_is_not_one_someone_opened_since(store):
    """Drawn before Ann opened it, the next invoice the form names is passed over once she has it open."""
    ann, bo = _person(store, "ann", "Ann Lee"), _person(store, "bo", "Bo Chen")
    first = clean_invoice(bo, store)
    ids = store.listed_ids(REVIEW)
    after = ids[(ids.index(first) + 1) % len(ids)]
    page = bo.get(f"/review/{first}").text
    data = {**form_of(store, first), **_screen(page), "next_id": str(after)}
    ann.get(f"/review/{after}")
    answer = bo.post(f"/review/{first}/approve", data=data, headers=PAGE).json()
    assert answer["ok"] and re.match(r"/review/\d+", answer["redirect"])
    assert not re.match(rf"/review/{after}(\?|$)", answer["redirect"])


def test_on_the_server_send_to_the_manager_goes_next_past_one_someone_opened_since(store):
    """B-1939: Send to the manager worked out the next invoice once this one had left the queue, where none is passed
    over: it opened the one the form named, which Ann had opened since the screen was drawn."""
    ann, bo = _person(store, "ann", "Ann Lee"), _person(store, "bo", "Bo Chen")
    _account(store, "max", "Max Manager", role="manager")
    manager = next(u for u in store.list_users() if u["username"] == "max")
    first = clean_invoice(bo, store)
    ids = store.listed_ids(REVIEW)
    after = ids[(ids.index(first) + 1) % len(ids)]
    page = bo.get(f"/review/{first}").text
    data = {**form_of(store, first), **_screen(page), "next_id": str(after), "manager_user": str(manager["id"])}
    ann.get(f"/review/{after}")
    answer = bo.post(f"/review/{first}/manager", data=data, headers=PAGE).json()
    assert answer["ok"] and re.match(r"/review/\d+", answer["redirect"])
    assert not re.match(rf"/review/{after}(\?|$)", answer["redirect"])


def test_on_a_laptop_nobody_else_is_shown_and_nothing_is_kept_of_who_has_it_open(laptop, store):
    inv_id = store.list_invoices(REVIEW)[0]["id"]
    screen = _open(laptop, inv_id)
    assert "others" not in _poll(laptop, inv_id, screen) and store.open_by() == {}
    assert "working on this invoice" not in laptop.get(f"/review/{inv_id}").text


# --- In a browser ---------------------------------------------------------------------------------------------------


@pytest.fixture
def browser(chromium, store, monkeypatch):
    monkeypatch.delenv("AP_SERVER_MODE", raising=False)
    monkeypatch.setenv("AP_REVIEWER", "Jordan Lee")
    with browser_on(chromium, create_app(web_settings(), store, start_reader=False)) as opened:
        yield opened


def _tab(context: Any, base: str, invoice_id: int) -> tuple[Any, list[str]]:
    page = context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base}/review/{invoice_id}")
    page.wait_for_selector("[form=inv][name=invoice_number]")
    return page, errors


def test_in_a_browser_two_tabs_the_second_to_edit_turns_read_only_once_keeping_what_was_typed(browser, store):
    chromium, base = browser
    inv_id = store.list_invoices(REVIEW)[0]["id"]
    context = chromium.new_context(viewport={"width": 1400, "height": 1000})
    one, errors_one = _tab(context, base, inv_id)
    two, errors_two = _tab(context, base, inv_id)
    one.fill("[form=inv][name=invoice_number]", "FROM-TAB-ONE")
    one.keyboard.press("Tab")
    wait_until(one, lambda: "Draft saved" in one.inner_text("[data-checking]"))
    two.fill("[form=inv][name=po_number]", "PO-TYPED-IN-TWO")
    two.keyboard.press("Tab")
    two.wait_for_selector("[data-stale]")
    assert "Edited in another tab or window today at" in two.inner_text("[data-stale]")
    assert two.input_value("[form=inv][name=po_number]") == "PO-TYPED-IN-TWO"  # still there, to copy
    assert two.locator("[form=inv][name=po_number]").evaluate("el => el.readOnly")
    assert two.locator(".inv-actionbar").is_hidden()
    assert two.locator("[data-stale] a").get_attribute("target") == "_blank"
    two.keyboard.press("Tab")  # said once, never a toast per edit
    assert two.locator("[data-stale]").count() == 1 and two.locator("#toasts > *").count() == 0
    assert store.invoice_draft(inv_id, "")["header"]["invoice_number"] == "FROM-TAB-ONE"
    assert errors_one == [] and errors_two == []


def test_in_a_browser_typed_then_approved_at_once_from_a_tab_behind_keeps_what_was_typed(browser, store):
    """B-1936: the approval's "changed meanwhile" was followed: the page drawn again from the other tab's draft, the
    edit just typed (Approve at once sends no check first) gone. It turns read-only as its check would, keeping it."""
    chromium, base = browser
    inv_id = clean_invoice(client_for(store), store)
    context = chromium.new_context(viewport={"width": 1400, "height": 1000})
    one, errors_one = _tab(context, base, inv_id)
    two, errors_two = _tab(context, base, inv_id)
    one.fill("[form=inv][name=po_number]", "PO-FROM-ONE")
    one.keyboard.press("Tab")
    wait_until(one, lambda: "Draft saved" in one.inner_text("[data-checking]"))
    two.fill("[form=inv][name=invoice_number]", "TYPED-IN-TWO")
    two.click("[data-approve]")
    two.wait_for_selector("[data-stale]", timeout=10000)
    assert "Edited in another tab or window today at" in two.inner_text("[data-stale]")
    assert two.input_value("[form=inv][name=invoice_number]") == "TYPED-IN-TWO"  # still there, to copy
    assert two.locator("[form=inv][name=invoice_number]").evaluate("el => el.readOnly")
    assert two.url == f"{base}/review/{inv_id}" and two.locator("#toasts > *").count() == 0
    assert store.get_invoice(inv_id)["status"] == REVIEW
    assert store.invoice_draft(inv_id, "")["header"]["po_number"] == "PO-FROM-ONE"
    assert errors_one == [] and errors_two == []


def test_in_a_browser_reject_from_a_screen_drawn_before_a_reopen_says_it_is_out_of_date(browser, store):
    """B-2502: Reject (or Park) from a screen drawn before the invoice was approved and reopened is refused: the
    screen says so once, as after Approve, and turns read-only."""
    chromium, base = browser
    inv_id = clean_invoice(client_for(store), store)
    page, errors = _tab(chromium.new_context(viewport={"width": 1400, "height": 1000}), base, inv_id)
    store.approve_invoice(inv_id, dict(store.get_invoice(inv_id)["ai_output"]), "Bo Chen")
    store.reopen(inv_id, "Bo Chen", "line 1 is the wrong account", only_approved=True)
    page.click("details[data-reject] > summary")
    page.fill("details[data-reject] [name=reason]", "not ours")
    page.click("details[data-reject] button[type=submit]")
    page.wait_for_selector("[data-stale]", timeout=10000)
    assert "Reopened by Bo Chen today at" in page.inner_text("[data-stale]")
    assert page.locator(".inv-actionbar").is_hidden() and page.url == f"{base}/review/{inv_id}"
    assert store.get_invoice(inv_id)["status"] == REVIEW
    assert errors == []


def test_in_a_browser_an_approval_clicked_while_a_check_runs_waits_for_its_answer(browser, store, monkeypatch):
    """B-1937: Approve clicked while the screen's check was under way was sent at once, with the draft revision from
    before that check: refused when the check dropped the draft. It waits for the check's answer, then goes."""
    import time

    from ap_coder.web.pages import invoice_check

    real = invoice_check._check

    def slow(*args: Any) -> Any:
        answer = real(*args)
        time.sleep(1.5)  # its answer comes late: the approval is clicked meanwhile
        return answer

    monkeypatch.setattr(invoice_check, "_check", slow)
    chromium, base = browser
    inv_id = clean_invoice(client_for(store), store)
    context = chromium.new_context(viewport={"width": 1400, "height": 1000})
    page, errors = _tab(context, base, inv_id)
    sent: list[str] = []
    page.on("request", lambda r: sent.append("asked " + r.url.split("/")[-1]) if r.method == "POST" else None)
    page.on("response", lambda r: sent.append("answered " + r.url.split("/")[-1]) if r.request.method == "POST"
            else None)  # fmt: skip
    page.fill("[form=inv][name=po_number]", "PO-TYPED")
    wait_until(page, lambda: "asked check" in sent)
    page.click("[data-approve]")
    page.wait_for_url(lambda url: f"/review/{inv_id}" not in url or "undo" in url, timeout=10000)
    assert sent.index("asked approve") > sent.index("answered check"), sent
    inv = store.get_invoice(inv_id)
    assert inv["status"] == APPROVED and inv["final_output"]["po_number"] == "PO-TYPED"
    assert errors == []


def test_in_a_browser_a_screen_approved_in_another_tab_says_so_from_its_poll(browser, store, monkeypatch):
    from ap_coder.web.pages import invoice

    monkeypatch.setattr(invoice, "WATCH_SECONDS", 1)  # (the page asks every 5 s at least)
    chromium, base = browser
    inv_id = clean_invoice(client_for(store), store)
    context = chromium.new_context(viewport={"width": 1400, "height": 1000})
    waiting, errors = _tab(context, base, inv_id)
    other, _ = _tab(context, base, inv_id)
    other.click("[data-approve]")
    other.wait_for_url(lambda url: f"/review/{inv_id}" not in url or "undo" in url, timeout=10000)
    waiting.wait_for_selector("[data-stale]", timeout=15000)
    assert "Approved by Jordan Lee today at" in waiting.inner_text("[data-stale]")
    assert waiting.locator("[form=inv][name=invoice_number]").evaluate("el => el.readOnly")
    assert errors == []


@pytest.fixture
def server_browser(chromium, store):
    _account(store, "ann", "Ann Lee")
    _account(store, "bo", "Bo Chen")
    with browser_on(chromium, create_app(web_settings(), store, start_reader=False, server=True)) as opened:
        yield opened


def test_in_a_browser_edits_kept_over_a_login_stay_in_their_boxes_when_it_changed_meanwhile(server_browser, store):
    """B-1941: the edits kept in the tab when the session ended, put back after the login, were refused as out of date
    (approved and the approval undone meanwhile) and the screen said so on the page drawn afresh, without them: taken
    out of the tab already, they were lost. They are put in its boxes first, read-only there to copy."""
    from tests.web.test_server_browser import _again, _log_in, _said_ended, _session_ends, _sign_in_again

    inv_id = store.list_invoices(REVIEW)[0]["id"]
    ann, errors = _log_in(server_browser, store, "ann", GOOD, f"/review/{inv_id}")
    ann.wait_for_selector("[form=inv][name=invoice_number]")
    _session_ends(store, "ann")
    box = ann.locator("[form=inv][name=invoice_number]")
    box.fill("TYPED-AFTER-THE-END")
    _said_ended(ann)
    store.approve_invoice(inv_id, dict(store.get_invoice(inv_id)["ai_output"]), "Bo Chen")
    store.reopen(inv_id, "Bo Chen", "Approval undone")
    _sign_in_again(ann)
    _again(ann, store, "ann")
    ann.wait_for_url(f"**/review/{inv_id}")
    ann.wait_for_selector("[data-stale]", timeout=10000)
    assert "Reopened by Bo Chen today at" in ann.inner_text("[data-stale]")
    assert box.input_value() == "TYPED-AFTER-THE-END" and box.evaluate("el => el.readOnly")
    assert errors == []


def test_in_a_browser_on_the_server_one_sees_who_else_has_it_open_and_next_passes_it_over(server_browser, store,
                                                                                          monkeypatch):  # fmt: skip
    """Two people, each logged in on a browser of their own: Bo has an invoice open; Ann's Next passes over it and
    says so; on it, Ann sees Bo's name, and her poll says when Bo opens one she has open."""
    from ap_coder.web.pages import invoice
    from tests.web.test_server_browser import _log_in

    monkeypatch.setattr(invoice, "WATCH_SECONDS", 1)  # (the page asks every 5 s at least)
    _, base = server_browser
    ids = store.listed_ids(REVIEW)
    first, second, third = ids[0], ids[1], ids[2]
    bo, bo_errors = _log_in(server_browser, store, "bo", GOOD, f"/review/{second}")
    ann, ann_errors = _log_in(server_browser, store, "ann", GOOD, f"/review/{first}")
    assert "Next skips 1 that Bo Chen has open." in ann.inner_text("[data-skipped]")
    ann.click("[data-nav=next]")
    ann.wait_for_url(f"**/review/{third}*")
    assert ann.locator("[data-others]").is_hidden()
    bo.goto(f"{base}/review/{third}")
    wait_until(ann, lambda: ann.locator("[data-others]").is_visible(), timeout=15)
    assert ann.inner_text("[data-others-text]") == "Bo Chen is working on this invoice too."
    assert ann_errors == [] and bo_errors == []
