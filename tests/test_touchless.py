"""Touchless processing: one company-wide switch, one bar for every vendor reached by itself, the invoices that always
go to a person, and what the clerk's corrections teach."""

import json
import sqlite3

import pytest

from ap_coder.capture.supplier import (
    ALWAYS_A_PERSON,
    AUTONOMOUS,
    HELD,
    READY,
    SUPERVISED,
    SUSPENDED,
    SupplierStats,
    automatic_transition,
    clean_invoices_to_go,
    fresh_streak,
    touchless_gates,
)
from ap_coder.capture.types import CaptureResult, FieldResult
from ap_coder.capture.workflow import (
    AUTONOMOUS_REVIEWER,
    TOUCHLESS_OFF,
    WAITING_FOR_PAGE_READER,
    audit_pick,
    autonomy_decision,
    learn_from_approval,
    on_reopen,
    taught_message,
)
from ap_coder.store import DEFAULT_TOUCHLESS_LIMIT, Store
from ap_coder.validation import ERROR, WARNING, Issue, ValidationReport

KEY = "id:V1"


def _outcomes(bad=False, n=13):
    return [{"field": f"f{f}", "ai_value": "a", "final_value": "b" if bad and f == 0 else "a",
             "correct": not (bad and f == 0)} for f in range(n)]  # fmt: skip


def _at(i: int, day: str = "2026-01-01") -> str:
    return f"{day}T{i // 3600:02d}:{i // 60 % 60:02d}:{i % 60:02d}"


def _ready(store: Store, n: int = 30, key: str = KEY, name: str = "Northwind", start: int = 0) -> None:
    """``n`` reviewed invoices with no correction (30 of 13 fields each meet the bar; 29 do not)."""
    base = 1000 if key == KEY else 50_000  # each vendor's own invoices
    for i in range(start, start + n):
        store.record_outcomes(key, base + i, _outcomes(), display_name=name, at=_at(i))


def _capture(total=1050.0, currency="CAD") -> CaptureResult:
    fields = {name: FieldResult(name, value, 0.99, "verified") for name, value in (
        ("vendor_name", "Northwind"), ("invoice_number", "NW-1"), ("invoice_date", "2026-09-14"),
        ("grand_total", total), ("subtotal", total), ("currency", currency))}  # fmt: skip
    return CaptureResult(fields, checks=[{"code": "TOTALS_ADD_UP", "ok": True, "detail": ""}])


def _report(*codes: str, severity: str = WARNING) -> ValidationReport:
    return ValidationReport("", 0.95, 0.95, 0.85, False, [Issue(severity, code, code) for code in codes])


@pytest.fixture
def path(tmp_path):
    """A file name the audit sample does not pick, so a decision is about the rules only."""
    for n in range(100):
        p = tmp_path / f"invoice{n}.pdf"
        if not audit_pick(p, 0.05):
            return p
    raise AssertionError("no file name outside the audit sample")


def _decide(store, path, capture=None, report=None, **kw):
    profile = store.get_supplier_profile(KEY)
    return autonomy_decision(store, KEY, profile, capture or _capture(), report or _report(), path, **kw)


# --- The switch and the limit ----------------------------------------------------------------------------------------


def test_switch_is_off_by_default_and_recorded_with_who_turned_it(tmp_path):
    store = Store(tmp_path / "s.db")
    assert not store.touchless_enabled()
    assert store.set_touchless(True, "Mia") == []  # no vendor yet
    assert Store(tmp_path / "s.db").touchless_enabled()  # kept
    on = store.events(actions=["touchless_on"])[0]
    assert on["actor"] == "Mia" and on["detail"]["policy"]["min_invoices"] == 20
    assert on["detail"]["limit"] == DEFAULT_TOUCHLESS_LIMIT
    store.set_touchless(True, "Mia")  # no change, no second event
    assert len(store.events(actions=["touchless_on"])) == 1
    store.set_touchless(False, "Bob")
    assert not store.touchless_enabled() and store.events(actions=["touchless_off"])[0]["actor"] == "Bob"


def test_touchless_limit_defaults_persists_and_is_audited(tmp_path):
    store = Store(tmp_path / "s.db")
    assert store.touchless_limit() == 5000.0
    assert store.set_touchless_limit(2500, "Mia") and Store(tmp_path / "s.db").touchless_limit() == 2500.0
    assert not store.set_touchless_limit(2500.0, "Mia")  # no change
    event = store.events(actions=["settings_changed"])[0]
    assert event["detail"] == {"keys": ["touchless_limit"], "from": 5000.0, "to": 2500.0} and event["actor"] == "Mia"
    with pytest.raises(ValueError):
        store.set_touchless_limit(0, "Mia")
    store.set_setting("touchless_limit", "nonsense")
    assert store.touchless_limit() == DEFAULT_TOUCHLESS_LIMIT


def test_the_bar_is_fixed_whatever_an_older_setting_says(tmp_path):
    store = Store(tmp_path / "s.db")
    store.set_setting("autonomy_policy", json.dumps({"min_invoices": 1, "clean_streak": 1}))
    policy = store.autonomy_policy()
    assert (policy.min_invoices, policy.min_lower_bound, policy.clean_streak, policy.audit_rate) == (20, 0.99, 10, 0.05)


# --- Switch off: nothing touchless ---------------------------------------------------------------------------------


def test_switch_off_never_approves_without_a_person(tmp_path, path):
    store = Store(tmp_path / "s.db")
    _ready(store)
    assert store.get_supplier_profile(KEY)["state"] == SUPERVISED  # nothing promoted while off
    decision = _decide(store, path)
    assert decision == {"state": READY, "auto": False, "audit": False, "reason": TOUCHLESS_OFF}
    # A vendor a manager turned touchless before the switch existed is not touchless either.
    store.set_supplier_state(KEY, AUTONOMOUS, "Mia")
    assert _decide(store, path)["auto"] is False and _decide(store, path)["state"] == READY


# --- Switch on: promotion, demotion, override -----------------------------------------------------------------------


def test_switch_on_makes_every_vendor_that_meets_the_bar_touchless(tmp_path, path):
    store = Store(tmp_path / "s.db")
    _ready(store)
    _ready(store, 12, key="id:V2", name="Chinook")  # too few invoices
    changes = store.set_touchless(True, "Mia")
    assert [(c["key"], c["to"]) for c in changes] == [(KEY, AUTONOMOUS)]
    event = store.events(actions=["autonomy_on"])[0]
    assert event["actor"] == "AP Coder" and "meets the bar" in event["detail"]["reason"]
    assert store.get_supplier_profile("id:V2")["state"] == SUPERVISED
    decision = _decide(store, path)
    assert decision["auto"] and decision["state"] == AUTONOMOUS and not decision["audit"]
    assert decision["reason"] == "autonomous supplier: every header field verified and every check passed"


def test_a_vendor_reaching_the_bar_goes_touchless_on_the_approval_that_gets_it_there(tmp_path):
    store = Store(tmp_path / "s.db")
    store.set_touchless(True, "Mia")
    _ready(store, 29)
    assert store.get_supplier_profile(KEY)["state"] == SUPERVISED
    result = store.record_outcomes(KEY, 2000, _outcomes(), at=_at(29))  # the 30th clean invoice: the 99% bound
    assert result["touchless"] and store.get_supplier_profile(KEY)["state"] == AUTONOMOUS


def test_a_correction_suspends_and_a_fresh_clean_streak_brings_it_back(tmp_path, path):
    store = Store(tmp_path / "s.db")
    _ready(store, 60)  # enough that one correction keeps the accuracy bound: the clean streak decides
    store.set_touchless(True, "Mia")
    result = store.record_outcomes(KEY, 3000, _outcomes(bad=True), source="audit", at="2099-01-01T00:00:00")
    assert result["suspended"] and store.get_supplier_profile(KEY)["state"] == SUSPENDED
    assert _decide(store, path)["auto"] is False and _decide(store, path)["state"] == SUSPENDED
    for i in range(9):  # 9 clean ones: not yet
        store.record_outcomes(KEY, 3001 + i, _outcomes(), at=_at(i + 1, "2099-01-01"))
    assert store.get_supplier_profile(KEY)["state"] == SUSPENDED
    assert store.record_outcomes(KEY, 3010, _outcomes(), at=_at(10, "2099-01-01"))["touchless"]  # the 10th
    assert store.get_supplier_profile(KEY)["state"] == AUTONOMOUS
    assert "again" in store.events(actions=["autonomy_on"])[0]["detail"]["reason"]


def test_a_reopened_touchless_invoice_suspends_until_a_fresh_streak(tmp_path, ground_truth):
    store = Store(tmp_path / "s.db")
    _ready(store)
    store.set_touchless(True, "Mia")
    ground_truth["gst_hst_registration_number"] = ""
    store.import_vendor_master([{"vendor_name": ground_truth["vendor_name"], "erp_id": "V1"}])
    invoice_id = store.add_invoice(tmp_path / "a.pdf", ground_truth, {"requires_review": False})
    store.approve_invoice(invoice_id, ground_truth, AUTONOMOUS_REVIEWER, login="ap-coder")
    on_reopen(store, invoice_id, "Ann", "wrong PO")  # what the review screen does, then it reopens it
    store.reopen(invoice_id, "Ann", "wrong PO")
    assert "reopened" in store.events(actions=["autonomy_suspended"])[0]["detail"]["reason"]
    profile = store.get_supplier_profile(KEY)
    assert profile["state"] == SUSPENDED and profile["suspended_at"]
    # No correction was recorded: its old clean streak does not count, only clean invoices since.
    assert store.sync_autonomy(KEY) == [] and store.get_supplier_profile(KEY)["state"] == SUSPENDED
    for i in range(10):
        store.record_outcomes(KEY, 4000 + i, _outcomes(), at=_at(i, "2099-01-01"))
    assert store.get_supplier_profile(KEY)["state"] == AUTONOMOUS


def test_keep_supervised_overrides_the_bar_until_allowed_again(tmp_path, path):
    store = Store(tmp_path / "s.db")
    _ready(store)
    store.set_supplier_state(KEY, HELD, "Mia", reason="new contract")
    store.set_touchless(True, "Mia")
    assert store.get_supplier_profile(KEY)["state"] == HELD
    assert _decide(store, path)["state"] == HELD and not _decide(store, path)["auto"]
    held = store.events(actions=["autonomy_held"])[0]
    assert held["actor"] == "Mia" and held["detail"]["reason"] == "new contract"
    store.set_supplier_state(KEY, SUPERVISED, "Mia", reason="allowed again")
    assert store.events(actions=["autonomy_allowed"])[0]["actor"] == "Mia"
    assert store.get_supplier_profile(KEY)["state"] == AUTONOMOUS  # it meets the bar: touchless at once


def test_a_vendor_made_touchless_by_hand_under_another_bar_goes_back_to_review(tmp_path):
    store = Store(tmp_path / "s.db")
    _ready(store, 5)
    with sqlite3.connect(tmp_path / "s.db") as conn:  # what an older version with a lenient policy left behind
        conn.execute("UPDATE supplier_profiles SET state = 'autonomous', autonomous_since = '2026-01-01T00:00:00'")
    changes = store.set_touchless(True, "Mia")
    assert [(c["from"], c["to"]) for c in changes] == [(AUTONOMOUS, SUPERVISED)]
    assert store.get_supplier_profile(KEY)["state"] == SUPERVISED


def test_suspended_at_column_is_added_to_an_older_database(tmp_path):
    path = tmp_path / "old.db"
    Store(path)
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE supplier_profiles")
        conn.execute(
            "CREATE TABLE supplier_profiles (key TEXT PRIMARY KEY, display_name TEXT NOT NULL DEFAULT '', vendor_id "
            "TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT 'supervised', autonomous_since TEXT, audit_rate "
            "REAL, template_json TEXT, updated_at TEXT NOT NULL, updated_by TEXT)"
        )
    store = Store(path)
    _ready(store, 1)
    assert "suspended_at" in store.get_supplier_profile(KEY)


# --- The pure rules ------------------------------------------------------------------------------------------------


def _stats(clean: int, bad_at: tuple[int, ...] = (), day: str = "2026-01-01") -> SupplierStats:
    rows = [{"invoice_id": i, "field": f"f{f}", "correct": not (i in bad_at and f == 0), "at": _at(i, day)}
            for i in range(clean) for f in range(13)]  # fmt: skip
    return SupplierStats.from_rows(rows)


def test_automatic_transition_rules():
    clean = _stats(30)
    assert automatic_transition(clean, stored_state=SUPERVISED)[0] == AUTONOMOUS
    assert automatic_transition(clean, stored_state=HELD) is None
    assert automatic_transition(clean, stored_state=AUTONOMOUS, autonomous_since=_at(1)) is None
    assert automatic_transition(_stats(30, (29,)), stored_state=AUTONOMOUS, autonomous_since=_at(20))[0] == SUSPENDED
    assert automatic_transition(_stats(12), stored_state=SUPERVISED) is None
    # suspended at invoice 25's time: only the 4 reviewed after count towards the streak
    assert fresh_streak(clean, _at(25)) == 4 and fresh_streak(clean, None) == 30
    assert automatic_transition(clean, stored_state=SUSPENDED, suspended_at=_at(25)) is None
    assert automatic_transition(clean, stored_state=SUSPENDED, suspended_at=_at(19))[0] == AUTONOMOUS
    assert clean_invoices_to_go(_stats(12)) == 18 and clean_invoices_to_go(clean) == 0  # the 99% bound: 18 more
    assert clean_invoices_to_go(_stats(60, (59,))) == 10  # one correction among many: the clean streak decides


# --- Always a person ---------------------------------------------------------------------------------------------


@pytest.fixture
def touchless(tmp_path):
    store = Store(tmp_path / "s.db")
    _ready(store)
    store.set_touchless(True, "Mia")
    assert store.get_supplier_profile(KEY)["state"] == AUTONOMOUS
    return store


@pytest.mark.parametrize(
    "code, reason",
    [
        ("VENDOR_BANK_CHANGED", "the bank account to pay into changed"),
        ("VENDOR_TAX_NUMBER_CHANGED", "the GST/HST number changed"),
        ("DUPLICATE_INVOICE", "it may be a duplicate"),
        ("DUPLICATE_IN_ERP", "it may be a duplicate"),
        ("DUPLICATE_OTHER_VENDOR", "it may be a duplicate"),
        ("POSSIBLE_DUPLICATE_AMOUNT", "it may be a duplicate"),
        ("AMOUNT_UNUSUAL", "the amount is unusual for this vendor"),
        ("VENDOR_NOT_IN_MASTER", "the vendor is not in the vendor master"),
    ],
)
def test_findings_that_always_need_a_person(touchless, path, code, reason):
    decision = _decide(touchless, path, report=_report(code))
    assert not decision["auto"] and decision["reason"] == f"always a person: {reason}"


def test_every_always_a_person_code_is_a_check_the_engine_raises():
    from ap_coder.help import CHECKS

    assert set(ALWAYS_A_PERSON) <= set(CHECKS)


def test_a_credit_note_always_needs_a_person(touchless, path):
    decision = _decide(touchless, path, capture=_capture(total=-250.0))
    assert not decision["auto"] and decision["reason"] == "always a person: it is a credit note"
    decision = _decide(touchless, path, output={"grand_total": -250.0, "currency": "CAD"})
    assert decision["reason"] == "always a person: it is a credit note"  # the coding's total wins


def test_a_total_over_the_touchless_limit_needs_a_person_in_cad(touchless, path):
    assert _decide(touchless, path, capture=_capture(total=5000.0))["auto"]  # at the limit: fine
    decision = _decide(touchless, path, capture=_capture(total=5000.01))
    assert decision["reason"] == "always a person: the total is over the touchless limit of 5,000.00"
    touchless.set_setting("fx_rates", "USD=1.40")
    assert _decide(touchless, path, capture=_capture(total=4000.0, currency="USD"))["reason"].endswith(
        "over the touchless limit of 5,000.00"
    )  # 5,600 CAD
    assert _decide(touchless, path, capture=_capture(total=3500.0, currency="USD"))["auto"]  # 4,900 CAD


def test_over_the_approval_limit_needs_a_person(touchless, path):
    touchless.set_setting("approval_limit", "1000")
    decision = _decide(touchless, path)
    assert decision["reason"] == "always a person: it is over the approval limit and needs a second approver"


def test_not_read_by_the_page_reader_yet_needs_a_person(touchless, path):
    assert _decide(touchless, path, awaiting_page_reader=True)["reason"] == WAITING_FOR_PAGE_READER
    assert touchless_gates(awaiting_page_reader=True) == ["the page reader has not read it yet"]


def test_an_error_still_blocks_and_the_audit_sample_still_applies(touchless, path, tmp_path):
    assert _decide(touchless, path, report=_report("GL_UNKNOWN", severity=ERROR))["reason"] == "a check failed"
    picked = next(tmp_path / f"p{n}.pdf" for n in range(500) if audit_pick(tmp_path / f"p{n}.pdf", 0.05))
    decision = _decide(touchless, picked)
    assert decision["audit"] and not decision["auto"] and decision["reason"] == "picked for the audit sample"


def test_several_gates_are_all_named():
    gates = touchless_gates([{"code": "VENDOR_BANK_CHANGED"}, {"code": "DUPLICATE_INVOICE"},
                             {"code": "POSSIBLE_DUPLICATE_AMOUNT"}], grand_total=-5, over_limit=5000.0)  # fmt: skip
    assert gates == ["the bank account to pay into changed", "it may be a duplicate", "it is a credit note",
                     "the total is over the touchless limit of 5,000.00"]  # fmt: skip


# --- Learning from the clerk ---------------------------------------------------------------------------------------


def test_bulk_approvals_teach_but_only_a_correction_counts(tmp_path, ground_truth):
    store = Store(tmp_path / "s.db")
    clean = store.add_invoice(tmp_path / "a.pdf", ground_truth, {"requires_review": False})
    learned = learn_from_approval(store, clean, ground_truth, actor="Ann", bulk=True)
    assert learned["supplier"] and not learned["counted"] and learned["corrected"] == []
    assert store.supplier_stats(learned["supplier"]).invoices == 0
    fixed = {**ground_truth, "invoice_number": "NW-2026-0999"}
    other = store.add_invoice(tmp_path / "b.pdf", ground_truth, {"requires_review": False})
    learned = learn_from_approval(store, other, fixed, actor="Ann", bulk=True)
    assert learned["counted"] and learned["corrected"] == ["invoice_number"]
    stats = store.supplier_stats(learned["supplier"])
    assert stats.invoices == 1 and stats.fields["invoice_number"].correct == 0  # recorded per field


def test_an_audited_invoice_is_recorded_as_an_audit(tmp_path, ground_truth):
    store = Store(tmp_path / "s.db")
    invoice_id = store.add_invoice(tmp_path / "a.pdf", ground_truth, {"requires_review": False},
                                   meta={"capture": {"audit": True}})  # fmt: skip
    learn_from_approval(store, invoice_id, {**ground_truth, "due_date": "2099-01-01"}, actor="Ann")
    rows = store.supplier_outcomes(store.supplier_key_for(ground_truth["vendor_name"],
                                                          ground_truth["gst_hst_registration_number"]))  # fmt: skip
    assert {r["source"] for r in rows} == {"audit"} and [r["field"] for r in rows if not r["correct"]] == ["due_date"]
    assert store.touchless_summary()["audited"] == 1 and store.touchless_summary()["audit_errors"] == 1


def test_taught_message_says_what_was_learned(tmp_path, ground_truth):
    store = Store(tmp_path / "s.db")
    invoice_id = store.add_invoice(tmp_path / "a.pdf", ground_truth, {"requires_review": False})
    final = {**ground_truth, "invoice_number": "NW-X", "due_date": "2099-01-01"}
    message = taught_message(store, learn_from_approval(store, invoice_id, final, actor="Ann"))
    name = ground_truth["vendor_name"].rstrip(".")
    assert message == (
        f"Learned from your 2 corrections (invoice number, due date). {name}: 1 invoice reviewed, "
        "about 60 more clean invoices to go touchless."
    )
    key = store.supplier_key_for(ground_truth["vendor_name"], ground_truth["gst_hst_registration_number"])
    for i in range(14):
        store.record_outcomes(key, 100 + i, _outcomes(), at=_at(i, "2099-01-02"))
    second = store.add_invoice(tmp_path / "b.pdf", ground_truth, {"requires_review": False})
    message = taught_message(store, learn_from_approval(store, second, ground_truth, actor="Ann"))
    assert message.startswith(f"Every header field was read right. {name}: 16 invoices reviewed, about ")
    assert message.endswith("more clean invoices to go touchless.")
    assert taught_message(store, {"supplier": ""}) == ""


def test_touchless_summary_counts_the_last_30_days(touchless, tmp_path, ground_truth):
    ground_truth["gst_hst_registration_number"] = ""
    ids = [touchless.add_invoice(tmp_path / f"t{n}.pdf", ground_truth, {"requires_review": False}) for n in range(4)]
    for invoice_id in ids[:2]:
        touchless.approve_invoice(invoice_id, ground_truth, AUTONOMOUS_REVIEWER, login="ap-coder")
    touchless.reopen(ids[0], "Ann", "wrong total")
    s = touchless.touchless_summary()
    assert (s["processed"], s["touchless"], s["reopened"]) == (4, 2, 1) and s["touchless_rate"] == 0.5


def test_bench_learn_promotes_a_supplier_by_itself(tmp_path):
    """The supplier-learning simulation applies the same automatic promotion as the app (a lenient bar here, so a few
    invoices reach it)."""
    from ap_coder.bench.learning import run_learning

    lenient = {"min_invoices": 2, "min_lower_bound": 0.5, "clean_streak": 2, "audit_rate": 0.0}
    report = run_learning(suppliers=1, invoices=5, seed=1, out=tmp_path / "learn", policy=lenient)
    rows = [json.loads(x) for x in (tmp_path / "learn" / "invoices.jsonl").read_text(encoding="utf-8").splitlines()]
    on = [r["index"] for r in rows if r["turned_on"]]
    assert on and report["summary"]["autonomy"]["suppliers_ready"] == 1
    assert rows[on[0] - 1]["state"] == AUTONOMOUS
    assert "goes touchless by itself" in (tmp_path / "learn" / "report.md").read_text(encoding="utf-8")
