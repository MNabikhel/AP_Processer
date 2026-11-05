"""A manager confirms an invoice's cost allocation before AP approves it (D-138): the store's side."""

from __future__ import annotations

import threading

import pytest

from ap_coder.core.store import PARKED, REVIEW, Store
from ap_coder.core.store.managers import CONFIRMED, SENT_BACK, WAITING, WITHDRAWN, coding_lines, same_coding
from tests.webclient import add_clean

LINES = [{"line_number": 1, "description": "Office cleaning", "gl_code": "6100", "cost_center": "CC-200",
          "amount": 400.0, "tax_code": "HST"},
         {"line_number": 2, "description": "Supplies", "gl_code": "6110", "cost_center": "CC-300",
          "amount": 100.0}]  # fmt: skip


def _sent(store, **kw):
    iid = add_clean(store)
    rid = store.send_to_manager(iid, "Ann (AP)", manager_name="Bob Manager", manager_email="bob@example.com",
                                lines=LINES, note="Is this yours?", **kw)  # fmt: skip
    return iid, rid


def test_sending_parks_the_invoice_waiting_for_the_manager_with_the_lines_as_coded(store):
    iid, rid = _sent(store)
    inv = store.get_invoice(iid)
    assert inv["status"] == PARKED and inv["parked_reason"] == "Bob Manager to confirm the cost allocation"
    req = store.manager_request(rid)
    assert req["status"] == WAITING and req["sent_by"] == "Ann (AP)" and req["cost_centers"] == ["CC-200", "CC-300"]
    assert req["lines"][0] == {"line": 1, "description": "Office cleaning", "gl_code": "6100", "cost_center": "CC-200",
                               "amount": 400.0}  # fmt: skip
    assert store.waiting_manager_request(iid)["id"] == rid
    assert [r["id"] for r in store.waiting_manager_requests()] == [rid]
    assert any(e["action"] == "sent_to_manager" for e in store.events(invoice_id=iid))


def test_only_an_invoice_in_the_review_queue_can_be_sent(store):
    iid, _rid = _sent(store)
    with pytest.raises(ValueError):  # parked already, waiting for the manager
        store.send_to_manager(iid, "Ann", manager_name="Bob", lines=LINES)
    with pytest.raises(ValueError, match="who the manager is"):
        store.send_to_manager(add_clean(store, invoice_number="X-2"), "Ann", manager_name="  ", lines=LINES)


@pytest.mark.parametrize("answer", [CONFIRMED, SENT_BACK])
def test_either_answer_puts_the_invoice_back_in_the_queue_for_ap(store, answer):
    iid, rid = _sent(store)
    note = "Code line 2 to CC-200" if answer == SENT_BACK else ""
    req = store.answer_manager_request(rid, answer, by="Bob Manager", actor="Bob Manager", note=note, via="screen")
    assert req["status"] == answer and req["answered_by"] == "Bob Manager" and req["answered_at"]
    assert store.get_invoice(iid)["status"] == REVIEW
    assert store.last_manager_answer(iid)["id"] == rid and store.waiting_manager_request(iid) is None
    action = "manager_confirmed" if answer == CONFIRMED else "manager_sent_back"
    assert any(e["action"] == action for e in store.events(invoice_id=iid))
    with pytest.raises(ValueError, match="answered or withdrawn"):  # answered twice (two tabs, a late email)
        store.answer_manager_request(rid, CONFIRMED, by="Bob", actor="Bob")


def test_sent_back_says_what_to_change(store):
    _iid, rid = _sent(store)
    with pytest.raises(ValueError, match="wants changed"):
        store.answer_manager_request(rid, SENT_BACK, by="Bob", actor="Bob", note=" ")


def test_bringing_it_back_or_deleting_it_withdraws_the_request(store):
    iid, rid = _sent(store)
    store.unpark_invoice(iid, "Ann")
    assert store.manager_request(rid)["status"] == WITHDRAWN and store.get_invoice(iid)["status"] == REVIEW
    with pytest.raises(ValueError):  # the manager's late answer changes nothing
        store.answer_manager_request(rid, CONFIRMED, by="Bob", actor="Bob")
    iid2, rid2 = _sent(store)
    store.delete_invoice(iid2, actor="Ann")
    assert store.manager_request(rid2)["status"] == WITHDRAWN and store.waiting_manager_requests() == []


def test_a_manager_account_sees_only_its_own_requests(store):
    _iid, rid = _sent(store, manager_user=7)
    _sent(store, manager_user=8)
    assert [r["id"] for r in store.waiting_manager_requests(7)] == [rid]


def test_the_coding_is_the_same_only_with_the_same_accounts_centers_and_amounts():
    sent = coding_lines(LINES)
    assert same_coding(sent, [dict(li, description="retyped") for li in LINES])
    assert not same_coding(sent, [LINES[0], dict(LINES[1], cost_center="CC-200")])
    assert not same_coding(sent, [LINES[0], dict(LINES[1], amount=90.0)])


def test_a_cost_centers_manager_is_remembered(store):
    store.import_accounts("cost_centers", [{"c": "CC-200"}, {"c": "CC-300"}], "c", None)
    assert store.set_cost_center_manager(["CC-200"], "Bob  Manager", "bob@example.com", "Ann") == 1
    assert store.cost_center_managers() == {"CC-200": {"name": "Bob Manager", "email": "bob@example.com"}}


def test_the_users_page_still_lists_admins_first_and_managers_last(store):
    for name, role in (("mo", "manager"), ("cy", "clerk"), ("ad", "admin")):
        store.add_user(name, "correct horse battery", role=role)
    assert [u["role"] for u in store.list_users()] == ["admin", "clerk", "manager"]


def _answer_at(start, path, rid, answer, note, said):
    other = Store(path)  # a connection of its own, as another web worker's
    start.wait()
    try:
        other.answer_manager_request(rid, answer, by="Bob Manager", actor="Bob Manager", note=note)
        said.append("kept")
    except ValueError as exc:
        said.append(str(exc))


def test_two_managers_answering_one_request_at_once_record_one_answer(store):
    # Two browsers (or a manager on screen and AP recording the email) answer the same request at the same moment,
    # each on a connection of its own: one answer is kept and logged, the other is told it was answered meanwhile.
    iid = add_clean(store)
    rounds = 25
    for _ in range(rounds):
        rid = store.send_to_manager(iid, "Ann (AP)", manager_name="Bob Manager", lines=LINES)
        start, said = threading.Barrier(2), []
        threads = [threading.Thread(target=_answer_at, args=(start, store.path, rid, answer, note, said))
                   for answer, note in ((CONFIRMED, ""), (SENT_BACK, "Code line 2 to CC-200"))]  # fmt: skip
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        assert sorted(said) == ["This request was answered or withdrawn meanwhile.", "kept"]
        assert store.get_invoice(iid)["status"] == REVIEW
    answers = [e for e in store.events(invoice_id=iid) if e["action"] in ("manager_confirmed", "manager_sent_back")]
    assert len(answers) == rounds  # one answer logged a request, never both
