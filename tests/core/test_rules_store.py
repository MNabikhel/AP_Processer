"""The fixed coding rules as the store keeps them: a rule's number stays its own through every change, each add,
change and removal is kept with who and when (and can be undone), and the lines a rule coded are counted."""

from __future__ import annotations

import json

from ap_coder.coding import rules
from ap_coder.coding.rules import Rule
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import Store
from ap_coder.core.store import rules as part


def _save(store: Store, monkeypatch, when: str, saved: list[Rule], actor: str) -> None:
    monkeypatch.setattr(part, "_now", lambda: when)
    store.save_coding_rules(saved, actor=actor)


def _history(store: Store) -> list[str]:
    return [rules.change_words(c) for c in store.coding_rule_history()]


def test_saving_the_rules_leaves_an_unchanged_rule_with_who_added_it_and_when(tmp_path, monkeypatch):
    """B-2231: every save wrote every rule again as added now, by the person saving."""
    store = Store(tmp_path / "r.db")
    _save(store, monkeypatch, "2026-11-03T09:00:00", [Rule("Courier", "", "7340")], "Dana")
    _save(store, monkeypatch, "2026-11-05T14:30:00", [*store.coding_rules(), Rule("", "freight", "5200")], "Lee")
    courier, freight = store.coding_rules()
    assert (courier.created_at, courier.created_by) == ("2026-11-03T09:00:00", "Dana")
    assert (freight.created_at, freight.created_by) == ("2026-11-05T14:30:00", "Lee")


def test_a_rule_keeps_its_number_through_a_change_of_its_words_its_vendor_and_its_account(tmp_path, monkeypatch):
    store = Store(tmp_path / "r.db")
    _save(store, monkeypatch, "2026-11-03T09:00:00", [Rule("Courier", "", "7340"), Rule("", "freight", "5200")], "Dana")
    courier, freight = store.coding_rules()
    edited = [Rule("Courier Express", "parcel", "7350", "CC1", id=courier.id), freight]
    _save(store, monkeypatch, "2026-11-05T14:30:00", edited, "Lee")
    again = store.coding_rules()
    assert again == [Rule("Courier Express", "parcel", "7350", "CC1", id=courier.id), freight]
    assert (again[0].created_at, again[0].created_by) == ("2026-11-03T09:00:00", "Dana")  # added then, changed since
    # a removed rule's number is never given to another rule
    store.save_coding_rules([again[0]], actor="Lee")
    store.save_coding_rules([again[0], Rule("", "freight", "5200")], actor="Lee")
    assert [r.id for r in store.coding_rules()] == [courier.id, freight.id + 1]


def test_a_rule_saved_without_its_number_is_the_saved_rule_that_says_the_same(tmp_path):
    store = Store(tmp_path / "r.db")
    store.save_coding_rules([Rule("Purolator", "", "5200"), Rule("", "freight", "5200")], actor="Dana")
    first, second = store.coding_rules()
    store.save_coding_rules([Rule("", "freight", "5200"), Rule("Purolator", "", "5200"), Rule("Bell", "", "6420")])
    assert [r.id for r in store.coding_rules()] == [first.id, second.id, second.id + 1]
    assert len(store.coding_rule_history()) == 3  # the three adds: nothing else changed
    # two rows with one number: the first is that rule, the other a new one
    store.save_coding_rules([Rule("Purolator", "", "5300", id=first.id), Rule("UPS", "", "5200", id=first.id)])
    assert {r.vendor: r.id for r in store.coding_rules()} == {"Purolator": first.id, "UPS": second.id + 2}


def test_every_add_change_and_removal_is_kept_newest_first_in_plain_words(tmp_path, monkeypatch):
    store = Store(tmp_path / "r.db")
    _save(store, monkeypatch, "2026-11-03T09:00:00", [Rule("Courier", "", "7340", "0021061010")], "Dana")
    (courier,) = store.coding_rules()
    _save(store, monkeypatch, "2026-11-04T10:00:00", [Rule("Courier", "", "7350", "0021061010", id=courier.id),
                                                      Rule("", "freight", "5200")], "Lee")  # fmt: skip
    _save(store, monkeypatch, "2026-11-05T11:00:00", [Rule("", "freight", "5200")], "Dana")
    newest, *_, oldest = history = store.coding_rule_history()
    assert [(c.rule_id, c.at, c.actor) for c in history] == [
        (courier.id, "2026-11-05T11:00:00", "Dana"), (courier.id + 1, "2026-11-04T10:00:00", "Lee"),
        (courier.id, "2026-11-04T10:00:00", "Lee"), (courier.id, "2026-11-03T09:00:00", "Dana")]  # fmt: skip
    assert (oldest.before, oldest.after.gl_code, newest.before.gl_code, newest.after) == (None, "7340", "7350", None)
    assert [rules.change_words(c, whole=True) for c in history] == [
        "Dana removed ‘Courier’ (account 0021061010.7350)",
        "Lee added ‘any vendor, line contains “freight”’: account 5200",
        "Lee changed ‘Courier’: account 0021061010.7340 → 0021061010.7350",
        "Dana added ‘Courier’: account 0021061010.7340",
    ]
    assert rules.change_words(history[2]) == "Lee changed ‘Courier’: GL account 7340 → 7350"
    assert [c.id for c in store.coding_rule_history(courier.id)] == [history[0].id, history[2].id, history[3].id]
    assert store.coding_rule_history(limit=1) == [newest] and store.coding_rule_change(newest.id) == newest
    store.save_coding_rules(store.coding_rules(), actor="Lee")  # saved with nothing changed: no entry
    assert len(store.coding_rule_history()) == 4


def test_a_change_of_what_a_rule_matches_and_of_its_cost_center_is_said_too(tmp_path):
    store = Store(tmp_path / "r.db")
    store.save_coding_rules([Rule("Courier", "", "7340")], actor="Dana")
    (courier,) = store.coding_rules()
    store.save_coding_rules([Rule("Courier", "parcel", "7340", "CC2", id=courier.id)], actor="")
    assert _history(store)[0] == ("Someone changed ‘Courier’: now ‘Courier, line contains “parcel”’; "
                                  "cost center none → CC2")  # fmt: skip


def test_undo_puts_the_rule_back_as_it_was_and_is_an_entry_that_can_be_undone(tmp_path):
    store = Store(tmp_path / "r.db")
    store.save_coding_rules([Rule("Courier", "", "7340")], actor="Dana")
    (courier,) = store.coding_rules()
    store.save_coding_rules([Rule("Courier", "", "7350", id=courier.id)], actor="Lee")
    change = store.coding_rule_history()[0]
    did, undo = store.undo_rule_change(change.id, actor="Dana")
    assert did == part.UNDONE and store.coding_rules() == [courier]
    assert undo == store.coding_rule_history()[0] and undo.undo_of == change.id
    assert rules.change_words(undo) == "Dana changed ‘Courier’: GL account 7350 → 7340 (an undo)"
    assert store.undo_rule_change(change.id, actor="Dana") == (part.SAME, None)  # undone already: nothing to do
    assert store.undo_rule_change(undo.id, actor="Lee")[0] == part.UNDONE  # the undo undone
    assert [r.gl_code for r in store.coding_rules()] == ["7350"] and len(store.coding_rule_history()) == 4
    assert store.undo_rule_change(999, actor="Lee") == (part.GONE, None)
    assert store.events(limit=1)[0]["action"] == "rules_changed"


def test_undo_of_a_removal_brings_the_rule_back_with_its_number_and_of_an_add_removes_it(tmp_path, monkeypatch):
    store = Store(tmp_path / "r.db")
    _save(store, monkeypatch, "2026-11-03T09:00:00", [Rule("Courier", "", "7340"), Rule("", "freight", "5200")], "Dana")
    courier, freight = store.coding_rules()
    _save(store, monkeypatch, "2026-11-05T11:00:00", [freight, Rule("Bell", "", "6420")], "Lee")
    removed, added = store.coding_rule_history(limit=2)  # (a save's adds and changes first, then its removals)
    assert store.undo_rule_change(removed.id, actor="Lee")[0] == part.UNDONE
    back = store.coding_rules()[0]
    assert back == courier and (back.created_at, back.created_by) == ("2026-11-03T09:00:00", "Dana")
    assert store.undo_rule_change(added.id, actor="Lee")[0] == part.UNDONE
    assert store.coding_rules() == [courier, freight]
    assert _history(store)[:2] == ["Lee removed ‘Bell’ (GL account 6420) (an undo)",
                                   "Lee added ‘Courier’: GL account 7340 (an undo)"]  # fmt: skip


def test_undo_of_an_entry_the_rule_changed_again_since_waits_to_be_told_to_overwrite(tmp_path):
    store = Store(tmp_path / "r.db")
    store.save_coding_rules([Rule("Courier", "", "7340")], actor="Dana")
    (courier,) = store.coding_rules()
    store.save_coding_rules([Rule("Courier", "", "7350", id=courier.id)], actor="Lee")
    first_change = store.coding_rule_history()[0]
    store.save_coding_rules([Rule("Courier", "", "7360", id=courier.id)], actor="Sam")
    assert store.undo_rule_change(first_change.id, actor="Dana") == (part.CHANGED_SINCE, None)
    assert [r.gl_code for r in store.coding_rules()] == ["7360"] and len(store.coding_rule_history()) == 3
    did, undo = store.undo_rule_change(first_change.id, actor="Dana", overwrite=True)
    assert did == part.UNDONE and store.coding_rules() == [courier]
    assert (undo.before.gl_code, undo.after.gl_code) == ("7360", "7340")  # what it overwrote is in its entry
    # removed since: the undo of the older change brings it back as it was before that change
    store.save_coding_rules([], actor="Sam")
    assert store.undo_rule_change(first_change.id, actor="Dana") == (part.CHANGED_SINCE, None)
    assert store.undo_rule_change(first_change.id, actor="Dana", overwrite=True)[0] == part.UNDONE
    assert store.coding_rules() == [courier]


def test_a_database_from_before_the_history_gets_it_and_keeps_its_rules_and_their_numbers(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    store = Store(path)
    store.save_coding_rules([Rule("Courier", "", "7340"), Rule("", "freight", "5200")], actor="Dana")
    before = store.coding_rules()
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE coding_rule_history")
        conn.execute("DROP TABLE summary_rules")
        conn.execute("DELETE FROM settings WHERE key = ?", (part.USE_SINCE,))
        version = conn.execute("SELECT value FROM settings WHERE key = 'schema_version'").fetchone()[0]
    store = Store(path)
    assert store.coding_rules() == before and store.coding_rule_history() == [] and store.rule_use() == {}
    assert store.get_setting("schema_version") == version  # no version of its own
    since = store.get_setting(part.USE_SINCE)
    assert since[:4].isdigit() and Store(path).get_setting(part.USE_SINCE) == since  # written once
    store.save_coding_rules(before[:1], actor="Lee")  # a rule from before the history, removed: its undo has it whole
    assert store.undo_rule_change(store.coding_rule_history()[0].id, actor="Lee")[0] == part.UNDONE
    assert store.coding_rules() == before and store.coding_rules()[1].created_by == "Dana"


def test_the_lines_each_rule_coded_are_counted_by_invoice_from_what_each_line_says_decided_it(tmp_path, ground_truth):
    store = Store(tmp_path / "r.db")
    store.save_coding_rules([Rule("", "delivery", "6900"), Rule("", "latitude", "6010")], actor="Dana")
    delivery, latitude = saved = store.coding_rules()
    coded, _ = rules.apply(InvoiceCoding.model_validate(ground_truth), saved)
    assert [li.decided_by.get("rule_id") for li in coded.line_items] == [latitude.id, None, None, None, delivery.id]
    first = store.add_invoice(tmp_path / "a.pdf", coded.to_output(), {})
    second = store.add_invoice(tmp_path / "b.pdf", coded.to_output(), {})
    store.add_invoice(tmp_path / "c.pdf", ground_truth, {})  # coded by no rule
    assert store.rule_use() == {delivery.id: (2, 2), latitude.id: (2, 2)}
    # approved with line 5 coded by a person: the delivery rule no longer stands behind it
    final = coded.to_output()
    final["line_items"][4].update(predicted_gl_code="6800", decided_by={"source": "person", "was": "rule"})
    store.approve_invoice(second, final, "Jane")
    assert store.rule_use() == {delivery.id: (1, 1), latitude.id: (2, 2)}
    store.delete_invoice(first)
    assert store.rule_use() == {latitude.id: (1, 1)}


def test_a_rule_not_saved_and_a_coding_without_the_rules_number_are_not_counted():
    line = {"decided_by": {"source": "rule", "rule_id": 3, "rule": "Courier"}}
    doc = {"line_items": [line, line, {"decided_by": {"source": "rule", "rule_id": 0}}, {"decided_by": {}},
                          {"decided_by": {"source": "rule", "rule": "Courier"}},  # coded before lines kept the number
                          {"decided_by": {"source": "person", "rule_id": 3}}, "odd"]}  # fmt: skip
    assert part.rule_rows(7, json.dumps(doc)) == [(7, 3, 2, "Courier", "", "")]
    assert part.rule_rows(7, None) == part.rule_rows(7, '{"rule_id" broken') == part.rule_rows(7, '["rule_id"]') == []


def test_the_counts_and_the_directorys_filter_by_rule_are_read_off_the_index_not_every_invoice(tmp_path):
    import sqlite3

    store = Store(tmp_path / "r.db")
    rule = Rule("Courier", "", "7340", id=3)
    with sqlite3.connect(store.path) as conn:

        def plan(sql: str, *params) -> str:
            return " | ".join(str(r[-1]) for r in conn.execute("EXPLAIN QUERY PLAN " + sql, params))

        counted = plan(part.USE_SQL, *part.as_now(rule), 3)
        for before in (False, True):
            sql, params = part.rule_filter(3, rule, before=before)
            found = plan(f"SELECT id FROM invoice_summary WHERE {sql} ORDER BY id DESC LIMIT 100", *params)
            assert "summary_rules_rule (rule_id=?)" in found and "SCAN invoice_summary" not in found, found
        one = plan("SELECT * FROM coding_rule_history WHERE rule_id = ? ORDER BY id DESC LIMIT ?", 3, 100)
    assert "COVERING INDEX summary_rules_rule (rule_id=?)" in counted and "SCAN" not in counted, counted
    assert "coding_rule_history_rule (rule_id=?)" in one and "SCAN" not in one, one


def _coded(store: Store, folder, ground_truth, name: str) -> int:
    """The sample invoice as the saved rules code it now, in the queue."""
    coded, _ = rules.apply(InvoiceCoding.model_validate(ground_truth), store.coding_rules())
    return store.add_invoice(folder / f"{name}.pdf", coded.to_output(), {})


def test_a_rule_changed_into_another_counts_what_it_coded_as_it_is_now_and_what_it_coded_before(tmp_path, ground_truth):
    """B-2293: a rule changed in place kept the count and the invoices of what it said before."""
    store = Store(tmp_path / "r.db")
    store.save_coding_rules([Rule("", "delivery", "6900")], actor="Dana")
    (rule,) = store.coding_rules()
    first = [_coded(store, tmp_path, ground_truth, n) for n in "ab"]
    assert store.rule_use() == {rule.id: (2, 2)} and store.rule_use(before=True) == {}
    store.save_coding_rules([Rule("", "latitude", "6010", id=rule.id)], actor="Lee")  # its words and its account
    assert store.rule_use() == {} and store.rule_use(before=True) == {rule.id: (2, 2)}
    third = _coded(store, tmp_path, ground_truth, "c")
    assert store.rule_use() == {rule.id: (1, 1)} and store.rule_use(before=True) == {rule.id: (2, 2)}

    def found(before: bool) -> list[int]:
        sql, params = part.rule_filter(rule.id, store.coding_rule(rule.id), before=before)
        with store._conn() as conn:
            return [r[0] for r in conn.execute(f"SELECT id FROM invoice_summary WHERE {sql} ORDER BY id", params)]

    assert found(False) == [third] and found(True) == first
    # Only its account changed: what it coded to the other account is from before too.
    store.save_coding_rules([Rule("", "latitude", "6020", id=rule.id)], actor="Lee")
    assert store.rule_use() == {} and store.rule_use(before=True) == {rule.id: (3, 3)}
    # Back to what it said first (an undo, or typed again): those lines are what it codes now, again.
    store.save_coding_rules([Rule("", "delivery", "6900", id=rule.id)], actor="Dana")
    assert store.rule_use() == {rule.id: (2, 2)} and store.rule_use(before=True) == {rule.id: (1, 1)}
    # A rule with no cost center leaves each line its own: lines it coded to two of them are its lines, on one invoice.
    doc = json.loads(json.dumps(ground_truth))
    by = {"source": "rule", "rule_id": rule.id, "rule": rule.label}
    doc["line_items"][0].update(predicted_gl_code="6900", predicted_cost_center="CC100", decided_by=by)
    doc["line_items"][1].update(predicted_gl_code="6900", predicted_cost_center="CC200", decided_by=by)
    store.add_invoice(tmp_path / "d.pdf", doc, {})
    assert store.rule_use() == {rule.id: (4, 3)}
    store.save_coding_rules([Rule("", "delivery", "6900", "CC200", id=rule.id)], actor="Dana")  # now to one of them
    assert store.rule_use() == {rule.id: (1, 1)} and store.rule_use(before=True) == {rule.id: (4, 4)}
    store.save_coding_rules([], actor="Dana")  # removed: nothing is "as it is now"; the search still finds its lines
    assert store.rule_use() == {} == store.rule_use(before=True) and len(found(False)) == 4


def test_a_save_made_on_rules_that_changed_since_writes_nothing_and_says_who_changed_what(tmp_path):
    """B-2292: the whole list is written, so the second of two saves made on the same rules removed the rule the
    first had just added."""
    store = Store(tmp_path / "r.db")
    store.save_coding_rules([Rule("Courier", "", "7340")], actor="Dana")
    (courier,) = store.coding_rules()
    seen = store.rules_seen()
    assert store.save_coding_rules([courier, Rule("Freight", "", "5200")], actor="Lee", seen=seen) == 2
    try:
        store.save_coding_rules([courier, Rule("Movers", "", "5300")], actor="Sam", seen=seen)
    except part.RulesChanged as newer:
        assert [rules.change_words(c) for c in newer.changes] == ["Lee added ‘Freight’: GL account 5200"]
    else:
        raise AssertionError("saved over a change it had not seen")
    assert [r.vendor for r in store.coding_rules()] == ["Courier", "Freight"]
    assert _history(store) == ["Lee added ‘Freight’: GL account 5200", "Dana added ‘Courier’: GL account 7340"]
    # An undo made on rules that changed since is not done either.
    try:
        store.undo_rule_change(store.coding_rule_history()[-1].id, actor="Sam", seen=seen)
    except part.RulesChanged as newer:
        assert [c.actor for c in newer.changes] == ["Lee"]
    else:
        raise AssertionError("undone over a change it had not seen")
    assert len(store.coding_rules()) == 2 and len(store.coding_rule_history()) == 2
    # On the rules as they are, both are done.
    assert store.save_coding_rules([*store.coding_rules(), Rule("Movers", "", "5300")], "Sam", store.rules_seen()) == 3
    assert store.undo_rule_change(store.coding_rule_history()[0].id, "Sam", seen=store.rules_seen())[0] == part.UNDONE


def test_an_undo_that_would_leave_two_rules_saying_the_same_is_refused(tmp_path):
    """B-2294: rule 1 changed, a new rule added that says what rule 1 said, the change undone: two rules said the
    same, and the next save removed one without being asked."""
    store = Store(tmp_path / "r.db")
    store.save_coding_rules([Rule("Courier", "", "7340")], actor="Dana")
    (courier,) = store.coding_rules()
    store.save_coding_rules([Rule("Courier", "", "7350", id=courier.id)], actor="Lee")
    change = store.coding_rule_history()[0]
    store.save_coding_rules([*store.coding_rules(), Rule("Courier", "", "7340")], actor="Sam")
    before = store.coding_rules()
    assert store.undo_rule_change(change.id, actor="Dana") == (part.TWICE, None)
    assert store.coding_rules() == before and len(store.coding_rule_history()) == 3
    # the same for a removed rule that another now says: its removal is not undone
    store.save_coding_rules([before[0]], actor="Sam")
    removal = store.coding_rule_history()[0]
    store.save_coding_rules([Rule("Courier", "", "7340", id=courier.id)], actor="Sam")
    assert store.undo_rule_change(removal.id, actor="Dana") == (part.TWICE, None)
    assert store.coding_rules() == [courier]
