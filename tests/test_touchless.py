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
    should_auto_approve,
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
from ap_coder.coding.validation import ERROR, WARNING, Issue, ValidationReport
from ap_coder.core.store import DEFAULT_TOUCHLESS_LIMIT, Store

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
    # A vendor an AP lead turned touchless before the switch existed is not touchless either.
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
    before = store.get_invoice(invoice_id)  # what the review screen does: reopen it, then suspend its vendor
    store.reopen(invoice_id, "Ann", "wrong PO")
    on_reopen(store, invoice_id, "Ann", "wrong PO", before=before)
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


def test_keeping_a_suspended_vendor_supervised_then_allowing_it_still_needs_a_fresh_streak(tmp_path):
    """Kept supervised while suspended, then allowed again: its clean streak from before the suspension still doesn't
    count (allowing it again is no shortcut past the fresh streak)."""
    store = Store(tmp_path / "s.db")
    _ready(store)
    store.set_touchless(True, "Mia")
    store.set_supplier_state(KEY, SUSPENDED, "Ann", reason="an invoice approved without review was reopened")
    store.set_supplier_state(KEY, HELD, "Mia")
    store.set_supplier_state(KEY, SUPERVISED, "Mia", reason="allowed again")
    assert store.get_supplier_profile(KEY)["state"] == SUPERVISED
    for i in range(10):
        store.record_outcomes(KEY, 4000 + i, _outcomes(), at=_at(i, "2099-01-01"))
    assert store.get_supplier_profile(KEY)["state"] == AUTONOMOUS
    assert store.get_supplier_profile(KEY)["suspended_at"] is None  # touchless again: a later one starts afresh


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


def test_a_vendor_suspended_before_the_column_existed_still_needs_a_fresh_streak(tmp_path):
    """An older version suspended a vendor (a touchless invoice reopened: no correction recorded) without noting
    when: the upgrade takes the time from the Activity log, so the clean streak before it doesn't bring it back."""
    path = tmp_path / "old.db"
    store = Store(path)
    _ready(store)
    with sqlite3.connect(path) as conn:
        conn.execute("ALTER TABLE supplier_profiles DROP COLUMN suspended_at")
        conn.execute("UPDATE supplier_profiles SET state = 'suspended', updated_at = '2026-05-02T00:00:00'")
        conn.execute(
            "INSERT INTO events (action, actor, detail, created_at) VALUES ('autonomy_suspended', 'Ann', ?, "
            "'2026-05-01T09:00:00')",
            (json.dumps({"supplier": "Northwind", "key": KEY, "from": "autonomous", "to": "suspended"}),),
        )
    store = Store(path)
    assert store.get_supplier_profile(KEY)["suspended_at"] == "2026-05-01T09:00:00"
    store.set_touchless(True, "Mia")
    assert store.get_supplier_profile(KEY)["state"] == SUSPENDED  # its 30 clean invoices were before it


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
        ("VENDOR_BANK_CHANGED", "the vendor's bank details changed"),
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
    from ap_coder.ops.help import CHECKS

    assert set(ALWAYS_A_PERSON) <= set(CHECKS)


def test_a_credit_note_always_needs_a_person(touchless, path):
    decision = _decide(touchless, path, capture=_capture(total=-250.0))
    assert not decision["auto"] and decision["reason"] == "always a person: it is a credit note"
    decision = _decide(touchless, path, output={"grand_total": -250.0, "currency": "CAD"})
    assert decision["reason"] == "always a person: it is a credit note"  # the coding's total wins


def test_a_document_titled_as_a_credit_needs_a_person_whatever_its_total(touchless, path):
    """A credit note is recognised by its title, not only by the sign of one number: a total read positive (an
    adjustment line that kept the signs as printed) does not make it an invoice to pay."""
    credit = _capture(total=250.0)
    credit.credit_note = True
    decision = _decide(touchless, path, capture=credit)
    assert not decision["auto"] and decision["reason"] == "always a person: it is a credit note"
    assert should_auto_approve(AUTONOMOUS, credit, True, [])[1] == "always a person: it is a credit note"
    assert CaptureResult.from_dict(credit.to_dict()).credit_note


def test_a_total_over_the_touchless_limit_needs_a_person_in_cad(touchless, path):
    assert _decide(touchless, path, capture=_capture(total=5000.0))["auto"]  # at the limit: fine
    decision = _decide(touchless, path, capture=_capture(total=5000.01))
    assert decision["reason"] == "always a person: the total is over the touchless limit of 5,000.00"
    touchless.set_setting("fx_rates", "USD=1.40")
    assert _decide(touchless, path, capture=_capture(total=4000.0, currency="USD"))["reason"].endswith(
        "over the touchless limit of 5,000.00"
    )  # 5,600 CAD
    assert _decide(touchless, path, capture=_capture(total=3500.0, currency="USD"))["auto"]  # 4,900 CAD


def test_a_foreign_total_without_an_exchange_rate_needs_a_person(touchless, path):
    """The touchless limit is in CAD: 4,000 USD (about 5,500 CAD) can't be compared with it without a rate."""
    decision = _decide(touchless, path, capture=_capture(total=4000.0, currency="USD"))
    assert not decision["auto"]
    assert decision["reason"] == (
        "always a person: no exchange rate for USD (Settings → Review) to compare its total with the touchless limit"
    )
    touchless.set_setting("fx_rates", "USD=1.30")
    assert _decide(touchless, path, capture=_capture(total=3500.0, currency="USD"))["auto"]  # 4,550 CAD
    assert _decide(touchless, path, capture=_capture(total=3500.0, currency="cad"))["auto"]


def test_over_the_approval_limit_needs_a_person(touchless, path):
    touchless.set_setting("approval_limit", "1000")
    decision = _decide(touchless, path)
    assert decision["reason"] == "always a person: it is over the approval limit and needs a second approver"


def test_not_read_by_the_page_reader_yet_needs_a_person(touchless, path):
    assert _decide(touchless, path, awaiting_page_reader=True)["reason"] == WAITING_FOR_PAGE_READER
    assert touchless_gates(awaiting_page_reader=True) == ["the page reader has not read it yet"]


def test_pages_the_page_reader_did_not_read_need_a_person(touchless, path):
    """OvisOCR2 reads at most AP_PAGE_READER_MAX_PAGES pages: an invoice with more was not read by every reader."""
    capture = _capture()
    capture.page_count = 7
    decision = _decide(touchless, path, capture=capture, pages_read=5)
    assert not decision["auto"] and decision["reason"] == "always a person: 2 of its pages were not read by OvisOCR2"
    assert _decide(touchless, path, capture=capture, pages_read=7)["auto"]
    assert touchless_gates(pages_unread=1) == ["1 of its pages was not read by OvisOCR2"]


def test_pages_past_the_local_readers_limit_need_a_person(touchless, path, tmp_path):
    """The local readers read a file's first 20 pages. A 25-page invoice that the page reader read whole (its limit
    raised to 30) was approved without a person, though pages 21-25 were read by one reader only: the capture counted
    20 pages, so no page looked unread."""
    import pymupdf

    from ap_coder.capture import analyze

    doc = pymupdf.open()
    for n in range(1, 26):
        doc.new_page().insert_text((60, 80), f"Acme Supply Ltd. - Invoice AC-1001 - page {n} of 25", fontsize=10)
    doc.save(tmp_path / "long.pdf")
    read = analyze(tmp_path / "long.pdf", ocr=False, tables=False, second_read=False)
    assert (read.page_count, read.pages_left_out) == (25, 5)
    capture = _capture()
    capture.page_count, capture.pages_left_out = 25, 5
    decision = _decide(touchless, path, capture=capture, pages_read=25)
    assert decision["reason"] == "always a person: 5 of its pages were not read by the local readers"
    assert should_auto_approve(AUTONOMOUS, capture, True, [])[0] is False
    capture.page_count, capture.pages_left_out = 25, 0
    assert _decide(touchless, path, capture=capture, pages_read=5)["reason"] == (
        "always a person: 20 of its pages were not read by OvisOCR2")  # fmt: skip


def test_a_page_ocr_could_not_read_counts_as_unread(monkeypatch, tmp_path):
    """A scanned page whose OCR failed was kept as an empty page and nothing said so: the capture looked complete
    (2 pages, none left out), so no gate fired though the local readers never read page 2."""
    import io

    import pymupdf
    from PIL import Image

    from ap_coder.capture import analyze, layout

    with pymupdf.open() as doc:
        for _ in range(2):
            png = io.BytesIO()
            Image.new("L", (850, 1100), 255).save(png, format="PNG")
            doc.new_page(width=612, height=792).insert_image(pymupdf.Rect(0, 0, 612, 792), stream=png.getvalue())
        doc.save(tmp_path / "scan.pdf")

    def ocr(png, number, engine=None):
        if number == 2:
            raise RuntimeError("onnxruntime: bad allocation")
        return layout.PageLayout(number, 1, 1, [], [])

    monkeypatch.setattr(layout, "ocr_image", ocr)
    monkeypatch.setattr(layout, "ocr_available", lambda: True)
    read = analyze(tmp_path / "scan.pdf", ocr=True, tables=False, second_read=False)
    assert (read.page_count, read.pages_left_out) == (2, 1)
    assert should_auto_approve(AUTONOMOUS, read, True, [])[1] == (
        "always a person: 1 of its pages was not read by the local readers")  # fmt: skip


def test_a_text_file_invoice_needs_a_person(touchless, path):
    """A .md/.txt invoice has no page for OvisOCR2 to read: only one reader read it, so a person approves it."""
    text = path.with_suffix(".txt")
    text.write_text("Invoice", encoding="utf-8")
    decision = _decide(touchless, text)
    assert not decision["auto"]
    assert decision["reason"] == "always a person: it is a text file: OvisOCR2 has no page to read"
    assert _decide(touchless, path)["auto"]


def test_an_error_still_blocks_and_the_audit_sample_still_applies(touchless, path, tmp_path):
    assert _decide(touchless, path, report=_report("GL_UNKNOWN", severity=ERROR))["reason"] == "a check failed"
    picked = next(tmp_path / f"p{n}.pdf" for n in range(500) if audit_pick(tmp_path / f"p{n}.pdf", 0.05))
    decision = _decide(touchless, picked)
    assert decision["audit"] and not decision["auto"] and decision["reason"] == "picked for the audit sample"


@pytest.mark.parametrize("code", ["GL_UNASSIGNED", "CC_UNASSIGNED"])
def test_a_line_without_an_account_is_never_approved_without_a_person(touchless, path, code):
    """The review screen never lets a person post a line to UNASSIGNED (the approve bar asks for an account first);
    touchless processing must not either. The finding is only a warning, so it was approved."""
    decision = _decide(touchless, path, report=_report(code))
    assert not decision["auto"] and not decision["audit"]
    assert "GL account" in decision["reason"] or "cost center" in decision["reason"]


def test_a_line_only_the_local_model_coded_is_never_approved_without_a_person(touchless, path):
    """A line neither AP's history nor a coding rule could code gets the local chat model's guess (right on 11 of
    41 sample lines): a valid account, so no finding, and the touchless bar measures header fields only."""
    from ap_coder.ai.pipeline import MODEL_SUGGESTED

    line = {"line_number": 1, "predicted_gl_code": "6010", "reasoning_justification": f"{MODEL_SUGGESTED}: hardware"}
    output = {"grand_total": 1050.0, "currency": "CAD", "line_items": [line]}
    decision = _decide(touchless, path, output=output)
    assert not decision["auto"] and "local model" in decision["reason"]
    line["reasoning_justification"] = "Approved 4 times for this vendor"
    assert _decide(touchless, path, output=output)["auto"]


def test_a_line_coded_from_a_weak_match_is_never_approved_without_a_person(touchless, path, tmp_path):
    """Sweep: with the chat model loaded, an older version coded a line from a weak match (an account's name, the
    vendor's default) and nothing held the invoice back. Such an invoice may still be in the queue (B-463 no longer
    codes one)."""
    from ap_coder.ai.offline_coder import WEAK_MATCH

    line = {"line_number": 1, "description": "Copy paper", "amount": 1000.0, "predicted_gl_code": "6000",
            "reasoning_justification": f"{WEAK_MATCH}: account description mentions paper"}  # fmt: skip
    output = {"grand_total": 1050.0, "currency": "CAD", "line_items": [line]}
    decision = _decide(touchless, path, output=output)
    assert not decision["auto"] and "weak match" in decision["reason"]


def test_several_gates_are_all_named():
    gates = touchless_gates([{"code": "VENDOR_BANK_CHANGED"}, {"code": "DUPLICATE_INVOICE"},
                             {"code": "POSSIBLE_DUPLICATE_AMOUNT"}], grand_total=-5, over_limit=5000.0)  # fmt: skip
    assert gates == ["the vendor's bank details changed", "it may be a duplicate", "it is a credit note",
                     "the total is over the touchless limit of 5,000.00"]  # fmt: skip


# --- Learning from the clerk ---------------------------------------------------------------------------------------


def test_bulk_approvals_teach_but_only_a_correction_counts(tmp_path, ground_truth):
    store = Store(tmp_path / "s.db")
    clean = store.add_invoice(tmp_path / "a.pdf", ground_truth, {"requires_review": False})
    store.approve_invoice(clean, ground_truth, "Ann", bulk=True)  # learned from only once approved (B-782)
    learned = learn_from_approval(store, clean, ground_truth, actor="Ann", bulk=True)
    assert learned["supplier"] and not learned["counted"] and learned["corrected"] == []
    assert store.supplier_stats(learned["supplier"]).invoices == 0
    fixed = {**ground_truth, "invoice_number": "NW-2026-0999"}
    other = store.add_invoice(tmp_path / "b.pdf", ground_truth, {"requires_review": False})
    store.approve_invoice(other, fixed, "Ann", bulk=True)
    learned = learn_from_approval(store, other, fixed, actor="Ann", bulk=True)
    assert learned["counted"] and learned["corrected"] == ["invoice_number"]
    stats = store.supplier_stats(learned["supplier"])
    assert stats.invoices == 1 and stats.fields["invoice_number"].correct == 0  # recorded per field


def test_an_audited_invoice_is_recorded_as_an_audit(tmp_path, ground_truth):
    store = Store(tmp_path / "s.db")
    invoice_id = store.add_invoice(tmp_path / "a.pdf", ground_truth, {"requires_review": False},
                                   meta={"capture": {"audit": True}})  # fmt: skip
    store.approve_invoice(invoice_id, {**ground_truth, "due_date": "2099-01-01"}, "Ann")
    learn_from_approval(store, invoice_id, {**ground_truth, "due_date": "2099-01-01"}, actor="Ann")
    rows = store.supplier_outcomes(store.supplier_key_for(ground_truth["vendor_name"],
                                                          ground_truth["gst_hst_registration_number"]))  # fmt: skip
    assert {r["source"] for r in rows} == {"audit"} and [r["field"] for r in rows if not r["correct"]] == ["due_date"]
    assert store.touchless_summary()["audited"] == 1 and store.touchless_summary()["audit_errors"] == 1


def test_taught_message_says_what_was_learned(tmp_path, ground_truth):
    store = Store(tmp_path / "s.db")
    invoice_id = store.add_invoice(tmp_path / "a.pdf", ground_truth, {"requires_review": False})
    final = {**ground_truth, "invoice_number": "NW-X", "due_date": "2099-01-01"}
    store.approve_invoice(invoice_id, final, "Ann")
    message = taught_message(store, learn_from_approval(store, invoice_id, final, actor="Ann"))
    name = ground_truth["vendor_name"].rstrip(".")
    assert message == (
        f"Learned from your 2 corrections (invoice number, due date). {name}: 1 invoice reviewed, "
        "0 of about 60 clean approvals toward skipping review."
    )
    key = store.supplier_key_for(ground_truth["vendor_name"], ground_truth["gst_hst_registration_number"])
    for i in range(14):
        store.record_outcomes(key, 100 + i, _outcomes(), at=_at(i, "2099-01-02"))
    second = store.add_invoice(tmp_path / "b.pdf", ground_truth, {"requires_review": False})
    store.approve_invoice(second, ground_truth, "Ann")
    message = taught_message(store, learn_from_approval(store, second, ground_truth, actor="Ann"))
    assert message.startswith(f"You changed no header field. {name}: 16 invoices reviewed, ")
    assert message.endswith("clean approvals toward skipping review.")  # B-666
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


TAX_TO_LOOK_AT = ["ITC_AT_RISK", "TAX_RATE_NONSTANDARD", "TAX_TYPE_NOT_LEVIED", "HST_NOT_CHARGED",
                  "TAX_REGIME_MISMATCH", "PROVINCIAL_TAX_NOT_CHARGED", "TAX_CALC_MISMATCH", "GST_HST_NUMBER_MISSING",
                  "GST_HST_NUMBER_FORMAT", "GST_HST_NUMBER_NOT_PRINTED"]  # fmt: skip


@pytest.mark.parametrize("code", TAX_TO_LOOK_AT)
def test_a_tax_finding_always_goes_to_a_person(touchless, path, code):
    """A tax charged at the wrong rate, of the wrong regime, not charged, or without a valid GST/HST number was
    approved without a person: only errors and the gates blocked (B-950)."""
    assert code in ALWAYS_A_PERSON
    decision = _decide(touchless, path, report=_report(code))
    assert not decision["auto"] and decision["reason"].startswith("always a person: ")


def test_any_warning_or_a_review_flag_keeps_an_invoice_for_a_person(touchless, path):
    """Touchless refuses what bulk approval refuses: any warning, and an invoice its checks flag for review (B-950)."""
    decision = _decide(touchless, path, report=_report("TAX_PROVINCE_DIFFERS"))
    assert not decision["auto"] and not decision["audit"] and "TAX_PROVINCE_DIFFERS" not in decision["reason"]
    assert decision["reason"].startswith("a check needs a person: ")
    flagged = ValidationReport("", 0.95, 0.7, 0.85, True, [])
    decision = _decide(touchless, path, report=flagged)
    assert not decision["auto"] and "confidence" in decision["reason"]
    assert _decide(touchless, path, report=_report("SOMETHING_INFO", severity="info"))["auto"]


@pytest.mark.parametrize("province, tax, date, number", [
    ("NS", ("HST", "NS", 0.15, 150.0), "2025-06-10", "123456782RT0001"),  # NS cut HST to 14% on 2025-04-01
    ("AB", ("HST", "ON", 0.13, 130.0), "2026-05-01", "123456782RT0001"),  # HST charged in a GST province
    ("ON", ("GST", "", 0.05, 50.0), "2026-05-01", "123456782RT0001"),  # GST only where HST is due
    ("BC", ("GST", "", 0.05, 50.0), "2026-05-01", "123456782RT0001"),  # BC PST due, not charged
    ("AB", ("GST", "", 0.05, 50.0), "2026-05-01", ""),  # $1,050 with no GST/HST number
])  # fmt: skip
def test_tax_problems_found_by_the_checks_are_never_touchless(tmp_path, province, tax, date, number):
    from ap_coder.coding.validation import validate_coding
    from ap_coder.core.schema import InvoiceCoding
    from ap_coder.core.store import load_sample_setup

    store = Store(tmp_path / "t.db")
    load_sample_setup(store)
    kind, prov, rate, amount = tax
    line = {
        "line_number": 1,
        "description": "item",
        "quantity": 1,
        "unit_price": 1000.0,
        "amount": 1000.0,
        "predicted_gl_code": "6010",
        "predicted_cost_center": "CC400",
        "taxes_applied": [kind],
    }
    doc = {"vendor_name": "Acme Ltd", "invoice_number": "A-1", "invoice_date": date, "currency": "CAD",
           "supplier_province": province, "ship_to_province": province, "gst_hst_registration_number": number,
           "subtotal": 1000.0, "tax_total": amount, "grand_total": 1000.0 + amount, "confidence_score": 0.99,
           "line_items": [line], "tax_lines": [{"tax_type": kind, "province": prov, "rate": rate,
                                                "taxable_amount": 1000.0, "tax_amount": amount}]}  # fmt: skip
    report = validate_coding(InvoiceCoding.model_validate(doc), store.reference_data(), review_threshold=0.85)
    fields = {k: FieldResult(k, doc[k], 0.99, "verified") for k in ("vendor_name", "invoice_number", "invoice_date",
                                                                       "grand_total", "subtotal")}  # fmt: skip
    capture = CaptureResult(fields, checks=[{"code": "TOTALS_ADD_UP", "ok": True}])
    issues = [{"code": i.code, "severity": i.severity} for i in report.issues]
    ok, why = should_auto_approve(AUTONOMOUS, capture, checks_ok=not report.errors, issues=issues,
                                  requires_review=report.requires_review)  # fmt: skip
    assert not ok, why
