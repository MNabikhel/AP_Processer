"""The Azure check (ap_coder/azure_check.py): Azure AI Document Intelligence as an optional second opinion, off by
default. Azure is never called: every test that "reads with Azure" hands in a fake client, and the host guard is
tested on the real client with a transport that sends nothing."""

import json
import logging
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from ap_coder import azure_check
from ap_coder.capture.supplier import AUTONOMOUS, should_auto_approve
from ap_coder.capture.types import CHECK, CaptureResult
from ap_coder.capture.workflow import AUTONOMOUS_REVIEWER, audit_pick, reader_outcome_rows
from ap_coder.config import Settings
from ap_coder.pipeline import InvoicePipeline
from ap_coder.store import APPROVED, REVIEW, Store, load_sample_setup
from ap_coder.web.app import create_app

from .conftest import SAMPLES

STEM = "northwind_ON_HST_NW-2026-0912"
ENDPOINT = "https://ap-test.cognitiveservices.azure.com/"
HOST = "ap-test.cognitiveservices.azure.com"
KEY = "S3cretKeyForTests0123456789abcdWXYZ"
PAGE = {"X-AP-Coder": "1"}


def _settings():
    s = Settings()
    return replace(s, llm=replace(s.llm, provider="off"))


@pytest.fixture
def env(monkeypatch, tmp_path):
    """A .env of the test's own, and no Azure endpoint or key in the environment (restored afterwards: saving the
    settings writes them there)."""
    monkeypatch.setenv("AP_ENV_FILE", str(tmp_path / "test.env"))
    for name in (azure_check.ENDPOINT_ENV, azure_check.KEY_ENV):
        monkeypatch.delenv(name, raising=False)
    return tmp_path / "test.env"


@pytest.fixture
def store(tmp_path, env):
    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    return store


def _turn_on(store, monkeypatch, rate=5.0, cap=200):
    monkeypatch.setattr(azure_check, "packages_installed", lambda: True)  # whether or not the extra is installed
    azure_check.save_settings(store, enabled=True, endpoint=ENDPOINT, api_key=KEY, spot_percent=rate,
                              daily_pages=cap, actor="Mia")  # fmt: skip


def _process(store, path=None, **kw):
    result = InvoicePipeline(_settings(), store.reference_data(), store=store).process(
        path or SAMPLES / f"{STEM}.pdf", **kw
    )
    assert result.ok, result.error
    return result


class FakeClient:
    """Stands in for DocumentIntelligenceClient: answers with ``raw``, remembers each call."""

    def __init__(self, raw):
        self.raw, self.calls = raw, []

    def begin_analyze_document(self, model_id, body=None, content_type=None, **kwargs):
        self.calls.append(model_id)
        raw = self.raw

        class Poller:
            def result(self):
                return type("Result", (), {"as_dict": lambda self: raw})()

        return Poller()


class NeverCalled:
    def begin_analyze_document(self, *args, **kwargs):
        raise AssertionError("Azure must not be called")


def _raw_from(capture, **changes):
    """Azure's ``prebuilt-invoice`` answer with the capture's own values (``changes``: what Azure reads otherwise)."""
    from ap_coder.capture import DI_FIELDS
    from ap_coder.capture.types import AMOUNT_FIELDS, DATE_FIELDS

    capture = capture if isinstance(capture, CaptureResult) else CaptureResult.from_dict(capture)
    fields = {}
    for di_name, name in DI_FIELDS.items():
        fr = capture.fields.get(name)
        value = changes.get(name, fr.value if fr is not None else None)
        if value in (None, ""):
            continue
        if name in AMOUNT_FIELDS:
            fields[di_name] = {"valueCurrency": {"amount": float(value)}, "confidence": 0.9}
        elif name in DATE_FIELDS:
            fields[di_name] = {"valueDate": str(value), "confidence": 0.9}
        else:
            fields[di_name] = {"valueString": str(value), "confidence": 0.9}
    return {"modelId": "prebuilt-invoice", "content": "", "pages": [{"pageNumber": 1, "words": []}],
            "documents": [{"fields": fields}]}  # fmt: skip


# --- Off by default -----------------------------------------------------------------------------------------------


def test_off_by_default_nothing_is_queued_or_sent(store, monkeypatch):
    monkeypatch.setattr(azure_check, "packages_installed", lambda: True)
    monkeypatch.setenv(azure_check.ENDPOINT_ENV, ENDPOINT)  # configured, but not turned on
    monkeypatch.setenv(azure_check.KEY_ENV, KEY)
    cfg = azure_check.load_settings(store)
    assert cfg.enabled is False and cfg.model == "prebuilt-invoice" and cfg.spot_rate == 0.05
    assert cfg.daily_pages == 200
    assert azure_check.status(cfg) == (
        azure_check.OFF,
        "Off — AP Coder works fully offline without it. Switch it on (and save) to test the connection.",
    )
    result = _process(store)
    assert store.azure_check(result.invoice_id) is None
    assert azure_check.check_one(_settings(), store, client=NeverCalled()) is None
    assert store.azure_check_counts()["waiting"] == 0


def test_missing_package_is_said_plainly_and_nothing_happens(store, monkeypatch):
    _turn_on(store, monkeypatch)
    monkeypatch.setattr(azure_check, "packages_installed", lambda: False)
    state, why = azure_check.status(azure_check.load_settings(store))
    assert state == azure_check.NO_PACKAGE and "aren't installed" in why and "offline" in why
    result = _process(store)
    assert store.azure_check(result.invoice_id) is None  # nothing queued
    assert azure_check.check_one(_settings(), store, client=NeverCalled()) is None
    record = azure_check.connection_test(store, _settings(), client=NeverCalled())
    assert record["ok"] is False and "aren't installed" in record["error"]
    with pytest.raises(azure_check.AzureUnavailable, match="aren't installed"):
        azure_check.make_client(azure_check.load_settings(store))


def test_no_endpoint_or_key_or_http_endpoint_sends_nothing(store, monkeypatch):
    monkeypatch.setattr(azure_check, "packages_installed", lambda: True)
    azure_check.save_settings(store, enabled=True, endpoint="", api_key="")
    assert azure_check.status(azure_check.load_settings(store))[0] == azure_check.NO_ENDPOINT
    azure_check.save_settings(store, enabled=True, endpoint=ENDPOINT, api_key="")
    assert azure_check.status(azure_check.load_settings(store))[0] == azure_check.NO_KEY
    with pytest.raises(ValueError, match="https://"):
        azure_check.save_settings(store, enabled=True, endpoint="http://ap-test.example.com/", api_key=KEY)
    assert not azure_check.usable(azure_check.load_settings(store))


# --- Which invoices -----------------------------------------------------------------------------------------------


def test_a_low_confidence_invoice_is_queued_when_on(store, monkeypatch):
    _turn_on(store, monkeypatch)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: True)
    result = _process(store)
    check = store.azure_check(result.invoice_id)
    assert check["status"] == "waiting" and check["reason"] == azure_check.LOW


def test_low_confidence_means_a_field_not_verified_or_a_failed_check(store):
    capture = CaptureResult.from_dict(_process(store).capture.to_dict())
    for fr in capture.fields.values():
        fr.status = "verified" if fr.value not in (None, "") else "missing"
    capture.checks = [{"code": "TOTALS_ADD_UP", "ok": True, "detail": ""}]
    assert not azure_check.low_confidence(capture)
    capture.fields["invoice_date"].status = "likely"
    assert azure_check.low_confidence(capture)
    capture.fields["invoice_date"].status = "verified"
    capture.checks.append({"code": "TAXES_ADD_UP", "ok": False, "detail": "no"})
    assert azure_check.low_confidence(capture)


def test_spot_checks_are_a_deterministic_share_of_verified_invoices(store, monkeypatch, tmp_path):
    files = []
    for n in range(400):
        path = tmp_path / f"inv{n}.pdf"
        path.write_bytes(b"%PDF invoice " + str(n).encode())
        files.append(path)
    picked = [p for p in files if azure_check.spot_pick(p, 0.05)]
    assert 8 <= len(picked) <= 35  # about 5%
    assert picked == [p for p in files if azure_check.spot_pick(p, 0.05)]  # the same every time
    assert not any(azure_check.spot_pick(p, 0) for p in files) and all(azure_check.spot_pick(p, 1) for p in files)
    assert picked != [p for p in files if audit_pick(p, 0.05)]  # not the touchless audit's sample

    _turn_on(store, monkeypatch, rate=5)
    cfg = azure_check.load_settings(store)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: False)
    capture = CaptureResult(fields={}, page_count=1)
    assert [azure_check.decide(store, cfg, capture, p) for p in files] == [
        azure_check.SPOT if p in picked else "" for p in files
    ]


def test_the_daily_cap_bounds_the_pages_sent(store, monkeypatch, tmp_path):
    _turn_on(store, monkeypatch, rate=100, cap=2)
    cfg = azure_check.load_settings(store)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: False)
    first = _process(store)
    assert store.azure_check(first.invoice_id)["reason"] == azure_check.SPOT
    client = FakeClient(_raw_from(first.capture))
    client.raw["pages"] = [{"pageNumber": 1}, {"pageNumber": 2}]  # two pages billed: the cap is reached
    assert azure_check.check_one(_settings(), store, client=client).status == "done"
    assert store.azure_pages_on() == 2
    # A spot-check is no longer picked today; a low-confidence invoice waits until tomorrow, nothing sent.
    assert azure_check.decide(store, cfg, first.capture, SAMPLES / f"{STEM}.pdf") == ""
    second = _process(store, SAMPLES / "harbourview_NS_HST_HPS-2026-0347.pdf")
    assert store.azure_check(second.invoice_id) is None
    store.queue_azure_check(second.invoice_id, azure_check.LOW)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: True)
    outcome = azure_check.check_one(_settings(), store, client=NeverCalled())
    assert outcome.status == "postponed" and "cap of 2 pages" in outcome.message
    assert store.azure_check(second.invoice_id)["status"] == "waiting"
    assert len(client.calls) == 1


# --- Comparing --------------------------------------------------------------------------------------------------


def test_agreement_is_recorded(store, monkeypatch):
    _turn_on(store, monkeypatch)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: True)
    result = _process(store)
    client = FakeClient(_raw_from(result.capture))
    outcome = azure_check.check_one(_settings(), store, client=client)
    assert outcome.status == "done" and outcome.differ == [] and client.calls == ["prebuilt-invoice"]
    check = store.azure_check(result.invoice_id)
    assert check["compared"] >= 5 and check["agreed"] == check["compared"]
    assert check["result"]["summary"] == f"Azure agrees on {check['compared']} of {check['compared']} fields"
    inv = store.get_invoice(result.invoice_id)
    assert inv["meta"]["azure_check"]["summary"].startswith("Azure agrees on")
    assert not any(i["code"] == azure_check.ISSUE_CODE for i in inv["validation"]["issues"])
    view = azure_check.overview(store)
    assert (view["checked"], view["agreed"], view["disagreements"]) == (1, 1, [])
    logged = store.events(invoice_id=result.invoice_id, actions=["azure_checked"])
    assert logged and logged[0]["detail"]["agreed"] == check["agreed"]


def test_a_disagreement_marks_the_field_check_and_blocks_touchless(store, monkeypatch):
    _turn_on(store, monkeypatch)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: True)
    result = _process(store)
    total = result.capture.fields["grand_total"].value
    client = FakeClient(_raw_from(result.capture, grand_total=float(total) + 100, invoice_number="NW-9999"))
    outcome = azure_check.check_one(_settings(), store, client=client)
    assert sorted(outcome.differ) == ["grand_total", "invoice_number"]
    capture = CaptureResult.from_dict(store.get_capture(result.invoice_id))
    fr = capture.fields["grand_total"]
    assert fr.status == CHECK and f"Azure Document Intelligence read {float(total) + 100:,.2f}" in fr.reasons
    assert "di" in fr.sources[f"other:{float(total) + 100}"]
    issues = store.get_invoice(result.invoice_id)["validation"]["issues"]
    issue = next(i for i in issues if i["code"] == azure_check.ISSUE_CODE)
    assert "NW-9999" in issue["message"] and issue["severity"] == "warning"
    for name, other in capture.fields.items():  # every other field verified: Azure alone keeps it from touchless
        if name not in ("grand_total", "invoice_number") and other.value not in (None, ""):
            other.status = "verified"
    capture.checks = [c for c in capture.checks if c.get("ok", True)]
    ok, why = should_auto_approve(AUTONOMOUS, capture, checks_ok=True)
    assert not ok and why.endswith("is check, not verified")
    # Read again (the page reader): the check is folded in again, the field stays Check.
    again = InvoicePipeline(_settings(), store.reference_data(), store=store).process(
        SAMPLES / f"{STEM}.pdf", save=False, invoice_id=result.invoice_id
    )
    assert again.capture.fields["grand_total"].status == CHECK
    assert any(i.code == azure_check.ISSUE_CODE for i in again.report.issues)
    view = azure_check.overview(store)
    assert view["disagreements"][0]["invoice_id"] == result.invoice_id
    page = _client(store).get("/settings/azure-check").text
    assert f'href="/review/{result.invoice_id}"' in page and "NW-9999" in page


def test_a_spot_checked_invoice_waits_for_azure_then_goes_touchless(store, monkeypatch):
    _turn_on(store, monkeypatch, rate=100)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: False)
    plan = azure_check.Plan(reason=azure_check.SPOT, hold=True)
    held = azure_check.hold({"state": AUTONOMOUS, "auto": True, "audit": False, "reason": "ok"}, plan)
    assert held["auto"] is False and held["azure_hold"] and "spot-check" in held["reason"]
    assert azure_check.hold({"auto": False, "reason": "x"}, plan) == {"auto": False, "reason": "x"}
    result = _process(store)
    assert store.azure_check(result.invoice_id)["reason"] == azure_check.SPOT
    _touchless_vendor(store, result.output)
    # As if touchless processing had approved it but for the spot-check (the page reader's decision).
    store.update_after_azure_check(result.invoice_id, None, None, azure_check.ISSUE_CODE,
                                   {"capture": {"state": AUTONOMOUS, "azure_hold": True}})  # fmt: skip
    outcome = azure_check.check_one(_settings(), store, client=FakeClient(_raw_from(result.capture)))
    assert outcome.approved is True
    inv = store.get_invoice(result.invoice_id)
    assert inv["status"] == APPROVED and inv["reviewer"] == AUTONOMOUS_REVIEWER


def _touchless_vendor(store, output):
    """Touchless processing on, with a limit above the sample's total, and the invoice's vendor touchless (30 clean
    reviewed invoices)."""
    store.set_touchless_limit(100_000, "Mia")
    key = store.supplier_key_for(output["vendor_name"], output.get("gst_hst_registration_number"))
    rows = [{"field": f"f{n}", "ai_value": "a", "final_value": "a", "correct": True} for n in range(13)]
    for i in range(30):
        store.record_outcomes(key, 10_000 + i, rows, display_name=output["vendor_name"], at=f"2026-01-01T00:00:{i:02d}")
    store.set_touchless(True, "Mia")
    assert store.get_supplier_profile(key)["state"] == AUTONOMOUS
    return key


@pytest.mark.parametrize("change", ["switch off", "vendor held", "limit lowered"])
def test_a_held_invoice_is_not_approved_once_touchless_no_longer_applies(store, monkeypatch, change):
    """Held for a spot-check, then the switch was turned off, an AP lead kept the vendor supervised or the touchless
    limit was lowered below its total: Azure agreeing does not approve it without a person."""
    _turn_on(store, monkeypatch, rate=100)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: False)
    result = _process(store)
    key = _touchless_vendor(store, result.output)
    store.update_after_azure_check(result.invoice_id, None, None, azure_check.ISSUE_CODE,
                                   {"capture": {"state": AUTONOMOUS, "azure_hold": True}})  # fmt: skip
    if change == "switch off":
        store.set_touchless(False, "Bob")
    elif change == "vendor held":
        store.set_supplier_state(key, "held", "Bob", "new contract")
    else:
        store.set_touchless_limit(float(result.output["grand_total"]) / 2, "Bob")
    outcome = azure_check.check_one(_settings(), store, client=FakeClient(_raw_from(result.capture)))
    assert outcome.status == "done" and outcome.approved is False
    assert store.get_invoice(result.invoice_id)["status"] == REVIEW


def test_a_check_under_way_when_ap_restores_is_dropped_and_none_starts_meanwhile(store, monkeypatch, tmp_path):
    """Azure read invoice #1 while AP restored a backup in which #1 is another invoice: its fields must not be
    folded into that one. While the restore runs, no check starts."""
    _turn_on(store, monkeypatch, rate=100)
    result = _process(store)
    _process(store, SAMPLES / "harbourview_NS_HST_HPS-2026-0347.pdf")  # waits in line behind it
    other = Store(tmp_path / "other" / "ap.db")
    load_sample_setup(other)
    assert _process(other, SAMPLES / "harbourview_NS_HST_HPS-2026-0347.pdf").invoice_id == result.invoice_id
    backup = other.backup_to(tmp_path / "other-backup.db")
    raw = _raw_from(result.capture, invoice_number="OTHER-1")  # Azure disagrees: it would mark a field Check

    seen = {}
    check = Store._check_backup_file

    def probe(path, name):
        seen["restoring"] = store.restoring()
        seen["started"] = azure_check.check_one(_settings(), store, client=NeverCalled())
        check(path, name)

    monkeypatch.setattr(Store, "_check_backup_file", staticmethod(probe))

    class Client(FakeClient):
        def begin_analyze_document(self, *args, **kwargs):
            store.restore_from(backup)
            return super().begin_analyze_document(*args, **kwargs)

    outcome = azure_check.check_one(_settings(), store, client=Client(raw))
    assert seen == {"restoring": True, "started": None}
    assert outcome.status == "skipped" and not outcome.approved
    inv = store.get_invoice(result.invoice_id)
    assert inv["vendor_name"].startswith("Harbourview")
    assert not any(i["code"] == azure_check.ISSUE_CODE for i in inv["validation"]["issues"])
    assert "azure_check" not in inv["meta"]


@pytest.mark.parametrize("reviewer", ["still to review", "disagrees"])
def test_azure_agreeing_does_not_approve_past_the_ai_reviewer(store, monkeypatch, reviewer):
    """Held for Azure and for the AI reviewer: Azure agreeing approves nothing while the reviewer hasn't agreed (its
    hold still pending, or its VERIFIER_DISAGREES issue open)."""
    _turn_on(store, monkeypatch, rate=100)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: False)
    result = _process(store)
    _touchless_vendor(store, result.output)
    if reviewer == "still to review":
        notes, issue = {"state": AUTONOMOUS, "azure_hold": True, "verifier_hold": True}, None
    else:
        notes = {"state": AUTONOMOUS, "azure_hold": True}
        issue = {"severity": "warning", "code": "VERIFIER_DISAGREES", "message": "total", "line_number": None}
    store.update_after_check(result.invoice_id, None, issue, ("VERIFIER_DISAGREES",), {"capture": notes})
    outcome = azure_check.check_one(_settings(), store, client=FakeClient(_raw_from(result.capture)))
    assert outcome.status == "done" and outcome.approved is False
    inv = store.get_invoice(result.invoice_id)
    assert inv["status"] == REVIEW and "azure_hold" not in inv["meta"]["capture"]
    assert bool(inv["meta"]["capture"].get("verifier_hold")) == (reviewer == "still to review")


def test_a_held_invoice_azure_disagrees_with_stays_for_a_person(store, monkeypatch):
    _turn_on(store, monkeypatch, rate=100)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: False)
    result = _process(store)
    store.update_after_azure_check(result.invoice_id, None, None, azure_check.ISSUE_CODE,
                                   {"capture": {"state": AUTONOMOUS, "azure_hold": True}})  # fmt: skip
    raw = _raw_from(result.capture, invoice_number="OTHER-1")
    assert azure_check.check_one(_settings(), store, client=FakeClient(raw)).approved is False
    inv = store.get_invoice(result.invoice_id)
    assert inv["status"] == REVIEW and "azure_hold" not in inv["meta"]["capture"]


# --- Readers scorecard --------------------------------------------------------------------------------------------


def test_azures_reading_is_scored_as_reader_di(store, monkeypatch):
    _turn_on(store, monkeypatch)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: True)
    result = _process(store)
    raw = _raw_from(result.capture, invoice_number="NW-9999")
    azure_check.check_one(_settings(), store, client=FakeClient(raw))
    final = result.output
    rows = [r for r in reader_outcome_rows(store.get_capture(result.invoice_id), final) if r["reader"] == "di"]
    assert rows and {r["field"] for r in rows} >= {"invoice_number", "grand_total"}
    wrong = next(r for r in rows if r["field"] == "invoice_number")
    assert wrong["read_value"] == "NW-9999" and wrong["correct"] is False
    assert next(r for r in rows if r["field"] == "grand_total")["correct"] is True


def test_an_invoice_already_approved_gets_reader_di_scored_beside_the_others(store, monkeypatch):
    _turn_on(store, monkeypatch, rate=100)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: False)
    result = _process(store)
    store.approve_invoice(result.invoice_id, result.output, "Mia")
    store.record_reader_outcomes(result.invoice_id, [{"reader": "rules", "field": "grand_total", "read_value": "1",
                                                      "final_value": "1", "correct": True}])  # fmt: skip
    azure_check.check_one(_settings(), store, client=FakeClient(_raw_from(result.capture)))
    readers = {r["reader"]: r for r in store.reader_scorecard()}
    assert readers["di"]["fields"] >= 5 and readers["di"]["rate"] == 1.0
    assert readers["rules"]["fields"] == 1  # the other readers' rows are kept


# --- The key and the network -----------------------------------------------------------------------------------


def _client(store):
    return TestClient(create_app(_settings(), store), base_url="http://localhost")


def test_the_key_never_appears_in_the_page_logs_or_activity(store, monkeypatch, caplog, env):
    monkeypatch.setattr(azure_check, "packages_installed", lambda: True)
    caplog.set_level(logging.DEBUG)
    client = _client(store)
    page = client.get("/settings/azure-check")
    assert page.status_code == 200 and "Off — AP Coder works fully offline without it." in page.text
    answer = client.post("/settings/azure-check", headers=PAGE, data={
        "enabled": "on", "endpoint": ENDPOINT, "api_key": KEY, "model": "prebuilt-invoice", "spot_percent": "5",
        "daily_pages": "200", "back": "/settings#azure-check"})  # fmt: skip
    assert answer.json()["ok"] and answer.json()["redirect"].startswith("/settings")
    assert KEY in env.read_text()  # kept in the .env, like the other Azure settings
    text = client.get("/settings/azure-check").text
    assert KEY not in text and KEY[:12] not in text and "…WXYZ" in text and HOST in text
    assert repr(azure_check.load_settings(store)).count(KEY) == 0
    events = store.events(actions=["settings_changed"])
    assert events and "key" in events[0]["detail"]["azure_check"] and KEY not in json.dumps(events, default=str)
    # An error that quotes the key is scrubbed before it is stored or shown.
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: True)
    result = _process(store)

    class Leaky:
        def begin_analyze_document(self, *args, **kwargs):
            raise RuntimeError(f"401: bad key {KEY}")

    outcome = azure_check.check_one(_settings(), store, client=Leaky())
    assert outcome.status == "failed" and KEY not in outcome.message and "••••" in outcome.message
    assert KEY not in store.azure_check(result.invoice_id)["error"]
    assert KEY not in caplog.text
    # Saving again with the key field empty keeps it; "Remove the saved key" removes it.
    client.post("/settings/azure-check", headers=PAGE, data={"enabled": "on", "endpoint": ENDPOINT})
    assert azure_check.load_settings(store).api_key == KEY
    client.post("/settings/azure-check", headers=PAGE, data={"enabled": "on", "endpoint": ENDPOINT, "forget_key": "1"})
    assert azure_check.load_settings(store).api_key == ""


def test_only_the_configured_host_is_contacted():
    check = azure_check.only_host(HOST)
    check(type("R", (), {"http_request": type("H", (), {"url": f"https://{HOST}/documentintelligence/x"})()})())
    for url in ("https://evil.example.com/x", f"http://{HOST}/x", f"https://{HOST}.evil.com/x"):
        with pytest.raises(azure_check.AzureUnavailable, match="only contacts"):
            check(type("R", (), {"http_request": type("H", (), {"url": url})()})())
    assert azure_check.endpoint_host("https://user:pw@evil.com@x/") == ""


def test_the_azure_client_refuses_any_other_host_before_sending():
    """The real client, with a transport that records what would be sent: a request to another host never reaches
    it (no network is used either way)."""
    pytest.importorskip("azure.ai.documentintelligence")
    from azure.ai.documentintelligence import DocumentIntelligenceClient
    from azure.core.credentials import AzureKeyCredential
    from azure.core.pipeline.transport import HttpTransport

    sent = []

    class Recorder(HttpTransport):
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def open(self):
            pass

        def close(self):
            pass

        def send(self, request, **kwargs):
            sent.append(request.url)
            raise ConnectionError("not sent in tests")

    cfg = azure_check.AzureCheckSettings(enabled=True, endpoint=ENDPOINT, api_key=KEY)
    client = azure_check.make_client(cfg)  # built offline; the hook is the host guard
    assert client._config.custom_hook_policy._request_callback is not None
    other = DocumentIntelligenceClient(endpoint="https://evil.example.com", credential=AzureKeyCredential("x"),
                                       raw_request_hook=azure_check.only_host(HOST), transport=Recorder(),
                                       retry_total=0)  # fmt: skip
    with pytest.raises(azure_check.AzureUnavailable):
        other.begin_analyze_document("prebuilt-invoice", body=b"%PDF", content_type="application/octet-stream")
    assert sent == []
    same = DocumentIntelligenceClient(endpoint=ENDPOINT.rstrip("/"), credential=AzureKeyCredential("x"),
                                      raw_request_hook=azure_check.only_host(HOST), transport=Recorder(),
                                      retry_total=0)  # fmt: skip
    with pytest.raises(ConnectionError):
        same.begin_analyze_document("prebuilt-invoice", body=b"%PDF", content_type="application/octet-stream")
    assert len(sent) == 1 and sent[0].startswith(f"https://{HOST}/documentintelligence/")


def test_ap_allow_internet_is_not_needed_nor_changed(store, monkeypatch):
    import os

    _turn_on(store, monkeypatch)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: True)
    result = _process(store)
    azure_check.check_one(_settings(), store, client=FakeClient(_raw_from(result.capture)))
    assert "AP_ALLOW_INTERNET" not in os.environ


# --- Test connection and the card ------------------------------------------------------------------------------


def test_test_connection_reads_the_sample_and_shows_the_comparison(store, monkeypatch):
    monkeypatch.setattr(azure_check, "packages_installed", lambda: True)
    client = _client(store)
    assert client.post("/settings/azure-check/test", headers=PAGE).json()["ok"] is False  # no endpoint yet
    azure_check.save_settings(store, enabled=False, endpoint=ENDPOINT, api_key=KEY)
    ours = azure_check.connection_test  # the real comparison, Azure faked
    from ap_coder.capture import analyze

    fake = FakeClient(_raw_from(analyze(azure_check.SAMPLE_INVOICE)))
    monkeypatch.setattr(azure_check, "make_client", lambda cfg: fake)
    refused = client.post("/settings/azure-check/test", headers=PAGE).json()  # off: nothing is sent, not even a test
    assert refused["ok"] is False and "Switch it on" in refused["error"] and fake.calls == []
    assert "nothing is sent to Azure while it is off" in client.get("/settings/azure-check").text
    azure_check.save_settings(store, enabled=True, endpoint=ENDPOINT, api_key=KEY)
    answer = client.post("/settings/azure-check/test", headers=PAGE).json()
    assert answer == {"ok": True, "job": "azure-test", "status": "/jobs/azure-test"}
    app_jobs = client.app.state.jobs
    assert app_jobs.get("azure-test").wait(60)
    snap = client.get("/jobs/azure-test").json()
    assert snap["state"] == "done" and snap["result"]["messages"][0]["text"].startswith("Azure agrees on")
    record = azure_check.last_test(store)
    assert record["ok"] and record["host"] == HOST and fake.calls == ["prebuilt-invoice"]
    page = client.get("/settings/azure-check").text
    assert "Connected" in page and "Azure agrees on" in page and KEY not in page
    assert ours is azure_check.connection_test


def test_the_card_can_be_included_anywhere_without_context(store):
    from ap_coder.web.core import templates

    client = _client(store)
    assert client.get("/settings/azure-check").status_code == 200
    source = '{% include "partials/azure_check.html" ignore missing %}'
    html = templates.env.from_string(source).render(request=type("R", (), {"app": client.app, "url": type(
        "U", (), {"path": "/settings"})()})())  # fmt: skip
    assert 'id="azure-check"' in html and "Off — AP Coder works fully offline without it." in html


def test_settings_reading_shows_the_azure_card_and_its_own_page_is_not_taken_for_a_tab(store):
    """Settings → Reading includes the card; /settings/azure-check is the card's page, not "Settings has no such tab"
    from Settings' /settings/{tab}."""
    client = _client(store)
    reading = client.get("/settings/reading")
    assert reading.status_code == 200 and 'id="azure-check"' in reading.text
    assert "works fully offline without it" in reading.text
    page = client.get("/settings/azure-check")
    assert page.status_code == 200 and 'id="azure-check"' in page.text
