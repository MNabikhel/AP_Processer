"""The Azure check (ap_coder/ai/azure_check.py): Azure AI Document Intelligence as an optional second opinion, off by
default. Azure is never called: every test that "reads with Azure" hands in a fake client, or the real client
(``di_http``) asking the stand-in on 127.0.0.1 (tests/fake_di.py)."""

import datetime as dt
import json
import logging
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from ap_coder.ai import azure_check, di_http
from ap_coder.ai.pipeline import InvoicePipeline
from ap_coder.capture.supplier import AUTONOMOUS, should_auto_approve
from ap_coder.capture.types import CHECK, CaptureResult
from ap_coder.capture.workflow import AUTONOMOUS_REVIEWER, audit_pick, reader_outcome_rows
from ap_coder.core.config import Settings
from ap_coder.core.store import APPROVED, REVIEW, Store, load_sample_setup
from ap_coder.web.app import create_app
from tests import fake_di
from tests.conftest import SAMPLES
from tests.webclient import PAGE

STEM = "northwind_ON_HST_NW-2026-0912"
ENDPOINT = "https://ap-test.cognitiveservices.azure.com/"
HOST = "ap-test.cognitiveservices.azure.com"
KEY = "S3cretKeyForTests0123456789abcdWXYZ"


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


def _turn_on(store, monkeypatch, rate=5.0, cap=200, mode="unsure", invoice_pages=5):
    azure_check.save_settings(store, enabled=True, endpoint=ENDPOINT, api_key=KEY, spot_percent=rate,
                              daily_pages=cap, actor="Mia", mode=mode, invoice_pages=invoice_pages)  # fmt: skip


def _process(store, path=None, **kw):
    result = InvoicePipeline(_settings(), store.reference_data(), store=store).process(
        path or SAMPLES / f"{STEM}.pdf", **kw
    )
    assert result.ok, result.error
    return result


class FakeClient:
    """Stands in for ``di_http.DiClient``: answers with ``raw``, remembers the pages asked for each call."""

    def __init__(self, raw):
        self.raw, self.calls = raw, []

    def analyze(self, data, pages="", on_accepted=None):
        assert data
        self.calls.append(pages)
        if on_accepted is not None:  # Azure took the file, as the real client says
            on_accepted()
        return self.raw


class NeverCalled:
    def analyze(self, *args, **kwargs):
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


def test_no_azure_package_is_needed(store, monkeypatch):
    """The check asks Azure with AP Coder's own plain-HTTPS client (D-245): the optional ``azure`` extra isn't
    needed, and its absence stops nothing."""
    import builtins

    real = builtins.__import__

    def no_azure(name, *args, **kwargs):
        if name.startswith("azure"):
            raise ImportError(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_azure)
    _turn_on(store, monkeypatch)
    cfg = azure_check.load_settings(store)
    assert azure_check.status(cfg)[0] == azure_check.READY
    client = azure_check.make_client(cfg)
    assert client.host == HOST and type(client).__module__ == "ap_coder.ai.di_http"


def test_no_endpoint_or_key_or_http_endpoint_sends_nothing(store, monkeypatch):
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


def test_every_invoice_mode_sends_the_ones_ap_coder_is_sure_of_too(store, monkeypatch, tmp_path):
    _turn_on(store, monkeypatch, rate=0, mode=azure_check.ALL)
    cfg = azure_check.load_settings(store)
    assert cfg.mode == azure_check.ALL and cfg.invoice_pages == 5
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: False)
    capture = CaptureResult(fields={}, page_count=1)
    assert azure_check.decide(store, cfg, capture, SAMPLES / f"{STEM}.pdf") == azure_check.EVERY
    result = _process(store)
    check = store.azure_check(result.invoice_id)
    assert check["reason"] == azure_check.EVERY and azure_check.REASONS[check["reason"]] == "Every invoice"
    with pytest.raises(ValueError, match="which invoices"):
        azure_check.save_settings(store, enabled=True, endpoint=ENDPOINT, mode="some")
    _turn_on(store, monkeypatch, rate=0)  # back to "only those AP Coder is unsure of": a sure one isn't sent
    assert azure_check.decide(store, azure_check.load_settings(store), capture, SAMPLES / f"{STEM}.pdf") == ""


def test_a_long_invoice_sends_its_first_pages_and_its_last():
    assert azure_check.pages_to_send(3, 5) == "" and azure_check.pages_to_send(5, 5) == ""
    assert azure_check.pages_to_send(12, 5) == "1-4,12"
    assert azure_check.pages_to_send(12, 2) == "1,12" and azure_check.pages_to_send(12, 1) == "1"
    cfg = azure_check.AzureCheckSettings(invoice_pages=5)
    assert azure_check.sent_pages(12, cfg) == 5 and azure_check.sent_pages(2, cfg) == 2


def test_a_long_invoice_is_counted_by_the_pages_sent(store, monkeypatch):
    _turn_on(store, monkeypatch, invoice_pages=1)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: True)
    result = _process(store)  # two pages
    client = FakeClient(_raw_from(result.capture))
    assert azure_check.check_one(_settings(), store, client=client).status == "done"
    assert client.calls == ["1"] and store.azure_pages_on() == 1


# --- Azure's reading as one more reader (D-245) -------------------------------------------------------------------


def test_azures_reading_is_kept_and_the_invoice_read_again_with_it(store, monkeypatch):
    """Azure agreeing makes the fields the rule reader alone read (likely) verified; the reading is kept, so the
    invoice read again later (the page reader, a person's Read again) has it without asking Azure again."""
    _turn_on(store, monkeypatch)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: True)
    result = _process(store)
    assert result.capture.fields["invoice_number"].status == "likely"  # one reader: never verified alone
    outcome = azure_check.check_one(_settings(), store, client=FakeClient(fake_di.recorded(STEM)))
    assert outcome.status == "done" and outcome.read_again == "now"
    inv = store.get_invoice(result.invoice_id)
    assert azure_check.cache_path(inv["file_sha256"]).is_file()
    fr = CaptureResult.from_dict(store.get_capture(result.invoice_id)).fields["invoice_number"]
    assert fr.sources == {"rules": "NW-2026-0912", "di": "NW-2026-0912"} and fr.status == "verified"
    again = InvoicePipeline(_settings(), store.reference_data(), store=store).process(
        SAMPLES / f"{STEM}.pdf", save=False, invoice_id=result.invoice_id
    )
    assert again.capture.fields["invoice_number"].status == "verified"


def test_switched_off_its_kept_reading_is_not_used(store, monkeypatch):
    _turn_on(store, monkeypatch)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: True)
    result = _process(store)
    azure_check.check_one(_settings(), store, client=FakeClient(fake_di.recorded(STEM)))
    azure_check.save_settings(store, enabled=False, endpoint=ENDPOINT)
    again = InvoicePipeline(_settings(), store.reference_data(), store=store).process(
        SAMPLES / f"{STEM}.pdf", save=False, invoice_id=result.invoice_id
    )
    # Off means ignored entirely: neither Azure's kept reading nor the comparison stored then is used.
    assert again.capture.fields["invoice_number"].status == "likely"
    assert not azure_check.kept_reading(store, SAMPLES / f"{STEM}.pdf")
    assert not any("di" in fr.sources or any(k.startswith("other:") and "di" in v for k, v in fr.sources.items())
                   for fr in again.capture.fields.values())  # fmt: skip


def test_switched_off_a_comparison_azure_disagreed_with_is_not_folded_in(store, monkeypatch):
    _turn_on(store, monkeypatch)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: True)
    result = _process(store)
    total = float(result.capture.fields["grand_total"].value)
    azure_check.check_one(_settings(), store, client=FakeClient(_raw_from(result.capture, grand_total=total + 100)))
    azure_check.save_settings(store, enabled=False, endpoint=ENDPOINT)
    again = InvoicePipeline(_settings(), store.reference_data(), store=store).process(
        SAMPLES / f"{STEM}.pdf", save=False, invoice_id=result.invoice_id
    )
    assert again.capture.fields["grand_total"].status != CHECK
    assert azure_check.ISSUE_CODE not in [i.code for i in again.report.issues]


def test_an_invoice_the_page_reader_read_goes_back_in_its_line(store, monkeypatch):
    _turn_on(store, monkeypatch)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: True)
    result = _process(store)
    store.update_after_check(result.invoice_id, None, None, (), {"page_reader": {"model": "ovisocr2", "pages": 2}})
    outcome = azure_check.check_one(_settings(), store, client=FakeClient(fake_di.recorded(STEM)))
    assert outcome.read_again == "page reader"
    assert store.next_page_read()["invoice_id"] == result.invoice_id


def _scan(monkeypatch, tmp_path, unread: tuple[int, ...]):
    """A scan of two pages read by an OCR that can't read the pages ``unread`` and reads "Invoice No. INV-7" on the
    others."""
    import io

    import pymupdf
    from PIL import Image

    from ap_coder.capture import layout
    from ap_coder.capture.types import PageLayout, Word

    png = io.BytesIO()
    Image.new("RGB", (612, 792), "white").save(png, "PNG")
    doc = pymupdf.open()
    for _ in range(2):
        doc.new_page(width=612, height=792).insert_image(pymupdf.Rect(0, 0, 612, 792), stream=png.getvalue())
    scan = tmp_path / "scan.pdf"
    doc.save(scan)

    def ocr(image, number, engine=None):
        if number in unread:
            raise RuntimeError("OCR can't read this page")
        words = [Word(text, layout.Box(number, x, 0.1, x + 0.1, 0.12), 0.95, "ocr")
                 for text, x in (("Invoice", 0.1), ("No.", 0.21), ("INV-7", 0.32))]  # fmt: skip
        return PageLayout(number, 612, 792, words, layout.group_lines(words, number))

    monkeypatch.setattr(layout, "ocr_available", lambda: True)
    monkeypatch.setattr(layout, "ocr_image", ocr)
    monkeypatch.setattr(layout, "second_read_layout", lambda path: None)
    return scan


def _azure_words(capture, **changes):
    """Azure's answer (``_raw_from``) with its words of page 2: "Total 113.00"."""
    words = [{"content": text, "polygon": [1, y, 3, y, 3, y + 0.2, 1, y + 0.2], "confidence": 0.99}
             for text, y in (("Total", 9.0), ("113.00", 9.3))]  # fmt: skip
    raw = _raw_from(capture, **changes)
    raw["pages"] = [
        {"pageNumber": 1, "width": 8.5, "height": 11, "unit": "inch", "words": []},
        {"pageNumber": 2, "width": 8.5, "height": 11, "unit": "inch", "words": words},
    ]
    return raw


def test_a_scans_page_ocr_cant_read_is_read_again_with_azures_words(store, monkeypatch, tmp_path):
    """D-245: Azure's words stand in for a page this computer's OCR can't read, when the invoice is read again with
    Azure's kept reading: page 2 of this scan was left unread, Azure read it."""
    scan = _scan(monkeypatch, tmp_path, unread=(2,))
    _turn_on(store, monkeypatch)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: True)
    result = _process(store, scan)
    assert result.capture.pages_not_read == [2] and result.capture.fields["invoice_number"].value == "INV-7"
    raw = _azure_words(result.capture, invoice_number="INV-7", grand_total=113.0)
    outcome = azure_check.check_one(_settings(), store, client=FakeClient(raw))
    assert outcome.status == "done" and outcome.read_again == "now"
    again = CaptureResult.from_dict(store.get_capture(result.invoice_id))
    assert again.pages_not_read == [] and again.layout_source == "mixed"


def test_a_scan_the_readers_here_read_no_field_of_is_read_again_with_azures(store, monkeypatch, tmp_path):
    """OCR read no page of this scan, so no field Azure read could be compared: the invoice was never read again,
    and none of Azure's words or fields was used. It is read again as when a field was compared."""
    scan = _scan(monkeypatch, tmp_path, unread=(1, 2))
    _turn_on(store, monkeypatch)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: True)
    result = _process(store, scan)
    assert result.capture.pages_not_read == [1, 2] and not result.capture.fields["invoice_number"].value
    raw = _azure_words(result.capture, invoice_number="INV-7", grand_total=113.0)
    outcome = azure_check.check_one(_settings(), store, client=FakeClient(raw))
    assert outcome.status == "done" and outcome.read_again == "now"
    again = CaptureResult.from_dict(store.get_capture(result.invoice_id))
    assert again.pages_not_read == [1] and again.fields["invoice_number"].value == "INV-7"


def test_an_invoice_a_person_worked_on_is_not_read_again(store, monkeypatch):
    _turn_on(store, monkeypatch)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: True)
    result = _process(store)
    with store._conn() as conn:
        conn.execute("UPDATE invoices SET reviewer = 'Mia' WHERE id = ?", (result.invoice_id,))
    outcome = azure_check.check_one(_settings(), store, client=FakeClient(fake_di.recorded(STEM)))
    assert outcome.status == "done" and outcome.read_again == ""


# --- Comparing --------------------------------------------------------------------------------------------------


def test_agreement_is_recorded(store, monkeypatch):
    _turn_on(store, monkeypatch)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: True)
    result = _process(store)
    client = FakeClient(_raw_from(result.capture))
    outcome = azure_check.check_one(_settings(), store, client=client)
    assert outcome.status == "done" and outcome.differ == [] and client.calls == [""]
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
    said = f"Azure Document Intelligence read {float(total) + 100:,.2f}"
    assert fr.status == CHECK and any(r.startswith(said) for r in fr.reasons)
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
    _code_the_lines(store, result.invoice_id)
    _touchless_vendor(store, result.output)
    # As if touchless processing had approved it but for the spot-check (the page reader's decision).
    store.update_after_check(result.invoice_id, None, None, (azure_check.ISSUE_CODE,),
                             {"capture": {"state": AUTONOMOUS, "azure_hold": True}})  # fmt: skip
    outcome = azure_check.check_one(_settings(), store, client=FakeClient(_raw_from(result.capture)))
    assert outcome.approved is True
    inv = store.get_invoice(result.invoice_id)
    assert inv["status"] == APPROVED and inv["reviewer"] == AUTONOMOUS_REVIEWER


def _companies_loaded(store):
    """JD Edwards' made-up companies (report 9, ``jde_group``)."""
    from ap_coder.bench import jde_group
    from ap_coder.coding import companies
    from ap_coder.core.headers import match_columns

    rows = jde_group.companies_rows()
    titles = list(jde_group.TITLES["build"])
    table, _ = companies.company_table([dict(zip(titles, r, strict=True)) for r in rows[1:]],
                                       match_columns(titles, companies.COMPANY_ALIASES))  # fmt: skip
    store.set_setting(companies.COMPANIES_SETTING, json.dumps(table))


def test_azure_agreeing_leaves_an_invoice_whose_company_was_decided_again_for_a_person(store, monkeypatch):
    """B-2930: B-2926 approved a held invoice under the company its checks decided again, but with the rest of its
    coding as worked out under the other: its PO-matched voucher (``VOUCHER_MATCH``, D-284) went out keyed under 00220
    and matched against 00210's order of the same number, which always goes to a person (D-285). An invoice whose
    company changed while it was held is not approved without a person (the AI reviewer's case says why: below)."""
    from ap_coder.core.memory import vendor_key
    from ap_coder.core.schema import VOUCHER_MATCH
    from ap_coder.erp import jde_settings

    vendor = "Northwind IT Solutions Inc."
    items = [("Dell Latitude 7450 laptop, 32GB RAM", 3, 1450.0), ("Dell PowerEdge R760 rack server - datacentre", 1,
             8900.0), ("Microsoft 365 E3 licences - monthly", 25, 32.0),
             ("Implementation consulting - endpoint rollout (hours)", 12, 150.0),
             ("Delivery & handling", 1, 95.0)]  # fmt: skip

    def order(company, first):  # PO-88213 as JD Edwards numbers it in ``company``
        return [{"po_number": "PO-88213", "vendor_name": vendor, "supplier_number": "", "order_type": "OP",
                 "order_company": company, "line_number": n + 1, "erp_line": f"{first + n * 10}.000",
                 "description": d, "quantity": q, "unit_price": p, "amount": q * p, "received_quantity": q,
                 "gl_code": "", "cost_center": "", "status": "open", "currency": "CAD"}
                for n, (d, q, p) in enumerate(items)]  # fmt: skip

    _companies_loaded(store)
    store.import_erp_vouchers([{"key": "00210-PV-700101-001", "vendor_name": vendor, "invoice_number": "NW-2025-0001",
                                "total": 100.0, "invoice_date": "2025-01-02", "an8": "",
                                "document": "700101"}])  # fmt: skip
    store.import_purchase_orders(order("00210", 1) + order("00220", 100))
    js = jde_settings.load(store)
    js.user, js.company, js.default_bu, js.use_default_rule = "APCODER", "00001", "HQ", True
    js.amount_mode, js.po_matched = jde_settings.AMOUNT_TAX, True
    js.po_charges, js.po_charge_account = jde_settings.CHARGES_GL, "HQ.6010"
    js.an8_overrides = {vendor_key(vendor): "10023"}
    jde_settings.save(store, js)
    store.set_setting(jde_settings.SHOW_SETTING, "1")
    _turn_on(store, monkeypatch, rate=100)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: False)
    result = _process(store)
    held = result.output
    assert (held["company"], {r["order_company"] for r in held[VOUCHER_MATCH]["rows"]}) == ("00210", {"00210"})
    _code_the_lines(store, result.invoice_id)
    _touchless_vendor(store, result.output)
    store.update_after_check(result.invoice_id, None, None, (azure_check.ISSUE_CODE,),
                             {"capture": {"state": AUTONOMOUS, "azure_hold": True}})  # fmt: skip
    store.set_setting("our_names", "Fabrikam Canada = 220")  # the bill-to printed: 00220's
    outcome = azure_check.check_one(_settings(), store, client=FakeClient(_raw_from(result.capture)))
    assert outcome.status == "done" and outcome.approved is False
    inv = store.get_invoice(result.invoice_id)
    assert inv["status"] == REVIEW and not inv["final_output"]
    # then read again with Azure's reading (D-245): its proposal all 00220's, its PO match too, for what decides next
    now = inv["ai_output"]
    assert (now["company"], {r["order_company"] for r in now[VOUCHER_MATCH]["rows"]}) == ("00220", {"00220"})


def _code_the_lines(store, invoice_id):
    """Each line of the invoice's proposal given an account and a cost center (the local model is off here, so it
    leaves them unassigned, and an invoice with a line to code is never approved without a person)."""
    output = store.get_invoice(invoice_id)["ai_output"]
    for li in output["line_items"]:
        li.update(predicted_gl_code="6010", predicted_cost_center="CC100")
    with store._conn() as conn:
        conn.execute("UPDATE invoices SET ai_output = ? WHERE id = ?", (json.dumps(output), invoice_id))
    return output


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


@pytest.mark.parametrize("change", ["switch off", "vendor held", "limit lowered", "vendor on hold", "in the ERP"])
def test_a_held_invoice_is_not_approved_once_touchless_no_longer_applies(store, monkeypatch, change):
    """Held for a spot-check, then the switch was turned off, an AP lead kept the vendor supervised or the touchless
    limit was lowered below its total: Azure agreeing does not approve it without a person. Nor once the vendor was
    put on hold in the vendor list, or the same invoice turned up in the ERP register: its checks are done again, as
    they come out today (B-951)."""
    _turn_on(store, monkeypatch, rate=100)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: False)
    result = _process(store)
    _code_the_lines(store, result.invoice_id)  # else its lines to code alone keep it for a person
    key = _touchless_vendor(store, result.output)
    store.update_after_check(result.invoice_id, None, None, (azure_check.ISSUE_CODE,),
                             {"capture": {"state": AUTONOMOUS, "azure_hold": True}})  # fmt: skip
    if change == "switch off":
        store.set_touchless(False, "Bob")
    elif change == "vendor held":
        store.set_supplier_state(key, "held", "Bob", "new contract")
    elif change == "vendor on hold":
        from ap_coder.core.memory import vendor_key

        name = result.output["vendor_name"]
        store.save_vendor(vendor_key(name), name, status="on_hold", notes="bank fraud attempt", actor="Bob")
    elif change == "in the ERP":
        out = result.output
        store.import_erp_register([{"vendor_name": out["vendor_name"], "invoice_number": out["invoice_number"],
                                    "total": out["grand_total"], "invoice_date": out["invoice_date"]}])  # fmt: skip
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
        def analyze(self, *args, **kwargs):
            store.restore_from(backup)
            return super().analyze(*args, **kwargs)

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
    store.update_after_check(result.invoice_id, None, None, (azure_check.ISSUE_CODE,),
                             {"capture": {"state": AUTONOMOUS, "azure_hold": True}})  # fmt: skip
    raw = _raw_from(result.capture, invoice_number="OTHER-1")
    assert azure_check.check_one(_settings(), store, client=FakeClient(raw)).approved is False
    inv = store.get_invoice(result.invoice_id)
    assert inv["status"] == REVIEW and "azure_hold" not in inv["meta"]["capture"]


def test_nothing_compared_is_not_nothing_differs(store, monkeypatch):
    """Azure read none of the fields AP Coder read (a layout or read model, or an answer with no document): the
    invoice held for the check is not approved without a person, stays held, and gets a review issue saying so."""
    _turn_on(store, monkeypatch, rate=100)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: False)
    result = _process(store)
    _touchless_vendor(store, result.output)
    store.update_after_check(result.invoice_id, None, None, (azure_check.ISSUE_CODE,),
                             {"capture": {"state": AUTONOMOUS, "azure_hold": True}})  # fmt: skip
    raw = {"modelId": "prebuilt-layout", "content": "", "pages": [{"pageNumber": 1, "words": []}], "documents": []}
    outcome = azure_check.check_one(_settings(), store, client=FakeClient(raw))
    assert outcome.status == "done" and outcome.approved is False
    inv = store.get_invoice(result.invoice_id)
    assert inv["status"] == REVIEW and inv["meta"]["capture"].get("azure_hold")
    assert any(i["code"] == azure_check.NOT_COMPARED_CODE for i in inv["validation"]["issues"])
    again = _process(store, page_text=None, save=False, invoice_id=result.invoice_id)  # read again: still flagged
    assert any(i.code == azure_check.NOT_COMPARED_CODE for i in again.report.issues) and again.report.requires_review


# --- When Azure can't be reached ----------------------------------------------------------------------------------


class Down:
    """Azure not reached (``di_http`` already tried again): each call fails as ``kind`` says."""

    def __init__(self, kind=di_http.UNREACHABLE):
        self.kind, self.calls = kind, 0

    def analyze(self, *args, **kwargs):
        self.calls += 1
        raise di_http.DiError(self.kind, "ap-test.cognitiveservices.azure.com can't be reached (ConnectionError).")


def test_azure_down_leaves_the_invoice_read_locally_and_in_line_then_pauses(store, monkeypatch):
    from ap_coder.ai import reading

    _turn_on(store, monkeypatch)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: True)
    result = _process(store)
    before = store.get_invoice(result.invoice_id)
    down = Down()
    for _ in range(azure_check.FAILURES_TO_PAUSE):
        outcome = azure_check.check_one(_settings(), store, client=down)
        assert outcome.status == "postponed" and "read by AP Coder's own readers" in outcome.message
        check = store.azure_check(result.invoice_id)
        assert check["status"] == "waiting" and "not reached" in check["error"]
    # Three failures in a row: the queue pauses (15 minutes), nothing is asked meanwhile.
    assert azure_check.check_one(_settings(), store, client=NeverCalled()) is None
    assert down.calls == azure_check.FAILURES_TO_PAUSE
    after = store.get_invoice(result.invoice_id)
    assert after["status"] == REVIEW and after["ai_output"] == before["ai_output"]  # read on this computer, as it was
    assert not any(i["code"] in azure_check.CODES for i in after["validation"]["issues"])  # no finding of its own
    line = reading.reading_status(_settings(), store).reader(reading.AZURE_DI)
    assert line.state == reading.WARN and "not reached since" in line.detail and "on this computer" in line.detail
    assert "not reached since" in azure_check.overview(store, _settings())["outage"]
    # After the pause, and once Azure answers, the outage is over.
    later = dt.datetime.now() + dt.timedelta(minutes=azure_check.PAUSE_MINUTES + 1)
    assert azure_check.paused(store, now=later) == ""
    monkeypatch.setattr(azure_check, "paused", lambda store, now=None: "")
    assert azure_check.check_one(_settings(), store, client=FakeClient(_raw_from(result.capture))).status == "done"
    assert azure_check.outage(store) is None
    assert reading.reading_status(_settings(), store).reader(reading.AZURE_DI).state == reading.OK


def test_a_refused_key_pauses_the_queue_until_the_settings_change(store, monkeypatch):
    _turn_on(store, monkeypatch)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: True)
    _process(store)
    outcome = azure_check.check_one(_settings(), store, client=Down(di_http.KEY))
    assert outcome.status == "postponed"
    assert "key" in azure_check.paused(store)
    assert azure_check.check_one(_settings(), store, client=NeverCalled()) is None
    later = dt.datetime.now() + dt.timedelta(days=1)
    assert azure_check.paused(store, now=later)  # not by time: by a new key
    azure_check.save_settings(store, enabled=True, endpoint=ENDPOINT, api_key="AnotherKey0123456789abcdWXYZ")
    assert azure_check.paused(store) == ""


def test_azure_not_reading_a_file_fails_that_invoice_only(store, monkeypatch):
    _turn_on(store, monkeypatch)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: True)
    result = _process(store)
    outcome = azure_check.check_one(_settings(), store, client=Down(di_http.FAILED))
    assert outcome.status == "failed" and store.azure_check(result.invoice_id)["status"] == "failed"
    assert azure_check.outage(store) is None


def _real_client(fake):
    return lambda cfg: di_http.DiClient(cfg.endpoint, cfg.api_key, ca_file=str(fake_di.CA_FILE),
                                        connect_to=fake.connect_to, sleep=lambda s: None)  # fmt: skip


@pytest.mark.parametrize("step", ["analyze 400", "poll 404"])
def test_a_file_azure_refuses_fails_that_invoice_and_the_queue_carries_on(store, monkeypatch, step):
    _turn_on(store, monkeypatch)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: True)
    first = _process(store)
    second = _process(store, SAMPLES / "pacific_BC_GST_PST_PO-77120.pdf")
    where, status = step.split()
    with fake_di.FakeDi() as fake:
        fake.script(where, int(status))
        monkeypatch.setattr(azure_check, "make_client", _real_client(fake))
        refused = azure_check.check_one(_settings(), store)
        assert refused.invoice_id == first.invoice_id and refused.status == "failed"
        assert azure_check.paused(store) == "" and azure_check.outage(store) is None
        assert "Azure" in store.azure_check(first.invoice_id)["error"]
        assert azure_check.check_one(_settings(), store).invoice_id == second.invoice_id
    assert store.azure_check(second.invoice_id)["status"] == "done"


def test_a_wrong_endpoint_pauses_the_queue_until_the_settings_change(store, monkeypatch):
    _turn_on(store, monkeypatch)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: True)
    _process(store)
    with fake_di.FakeDi() as fake:
        fake.script("analyze", 404)
        monkeypatch.setattr(azure_check, "make_client", _real_client(fake))
        assert azure_check.check_one(_settings(), store).status == "postponed"
    assert "endpoint" in azure_check.paused(store)


def test_a_file_azure_took_is_counted_at_once_and_never_sent_again(store, monkeypatch):
    """Azure took the file (202), then didn't finish in time: the pages count against the daily cap (they are
    billed), and the invoice is not sent again: it fails, saying why."""
    _turn_on(store, monkeypatch, cap=2)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: True)
    result = _process(store)  # two pages
    counted = []
    with fake_di.FakeDi(running_polls=10**6) as fake:

        def client(cfg):
            made = _real_client(fake)(cfg)
            made.poll_seconds = 0.2
            return made

        monkeypatch.setattr(azure_check, "make_client", client)
        real = store.azure_pages_on
        for _ in range(4):
            monkeypatch.setattr(azure_check, "paused", lambda store, now=None: "")
            outcome = azure_check.check_one(_settings(), store)
            counted.append(real())
    assert len(fake.steps("POST")) == 1 and counted[0] == 2
    check = store.azure_check(result.invoice_id)
    assert check["status"] == "failed" and "not sent again" in check["error"]
    assert outcome is None


def test_the_daily_cap_counts_a_file_while_azure_reads_it(store, monkeypatch):
    _turn_on(store, monkeypatch)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: True)
    _process(store)
    seen = []

    class Watch(FakeClient):
        def analyze(self, data, pages="", on_accepted=None):
            on_accepted()
            seen.append(store.azure_pages_on())
            return super().analyze(data, pages)

    assert azure_check.check_one(_settings(), store, client=Watch(fake_di.recorded(STEM))).status == "done"
    assert seen == [2] and store.azure_pages_on() == 2  # counted at the 202, not twice


def test_a_key_with_anything_but_letters_and_digits_is_refused_when_saved(store, monkeypatch):
    for bad in ("S3cretKeyForTests0123456789abcd\u200bWXYZ", "S3cretKey-ForTests0123456789abcd"):
        with pytest.raises(ValueError, match="letters and digits"):
            azure_check.save_settings(store, enabled=True, endpoint=ENDPOINT, api_key=bad)
    assert azure_check.load_settings(store).api_key == ""


def test_a_key_that_cant_be_sent_fails_as_a_key_and_the_doctor_says_so(store, monkeypatch):
    from ap_coder.ops import doctor

    _turn_on(store, monkeypatch)
    monkeypatch.setenv(azure_check.KEY_ENV, "S3cretKeyForTests0123456789abcd\u200bWXYZ")  # put in the .env by hand
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: True)
    _process(store)
    with fake_di.FakeDi() as fake:
        monkeypatch.setattr(azure_check, "make_client", _real_client(fake))
        assert azure_check.check_one(_settings(), store).status == "postponed"
        assert "key" in azure_check.paused(store)
        real = di_http.DiClient
        monkeypatch.setattr(di_http, "DiClient", lambda endpoint, key: real(
            endpoint, key, ca_file=str(fake_di.CA_FILE), connect_to=fake.connect_to, sleep=lambda s: None))  # fmt: skip
        line = doctor.azure_checks(store, _settings(), online=True)[-1]
    assert line.status == doctor.FAIL and "key" in line.detail


@pytest.mark.parametrize("busy", ["open", "draft"])
def test_an_invoice_a_person_has_open_or_a_draft_of_is_not_read_again(store, monkeypatch, busy):
    _turn_on(store, monkeypatch)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: True)
    result = _process(store)
    iid = result.invoice_id
    before = store.get_invoice(iid)["ai_output"]
    if busy == "open":
        store.keep_open(iid, "mia-browser", "Mia", 300, 30)
    else:
        draft = dict(before)
        draft["invoice_number"] = "MIA-TYPED-THIS"
        store.save_invoice_draft(iid, "mia-browser", draft)
    outcome = azure_check.check_one(_settings(), store, client=FakeClient(fake_di.recorded(STEM)))
    assert outcome.status == "done" and outcome.read_again == ""
    assert store.get_invoice(iid)["ai_output"] == before


def test_a_proposal_is_not_replaced_once_a_person_opened_it_meanwhile(store, monkeypatch):
    """Checked in the same transaction as the replacement: a person opening the invoice while it is read again."""
    _turn_on(store, monkeypatch)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: True)
    result = _process(store)
    iid = result.invoice_id
    before = store.get_invoice(iid)["ai_output"]
    real = InvoicePipeline.process

    def opened_meanwhile(self, *args, **kwargs):
        done = real(self, *args, **kwargs)
        store.keep_open(iid, "mia-browser", "Mia", 300, 30)
        return done

    monkeypatch.setattr(InvoicePipeline, "process", opened_meanwhile)
    outcome = azure_check.check_one(_settings(), store, client=FakeClient(fake_di.recorded(STEM)))
    assert outcome.read_again == "" and store.get_invoice(iid)["ai_output"] == before


def test_an_answer_that_cant_be_read_is_not_kept_and_fails_that_invoice(store, monkeypatch):
    _turn_on(store, monkeypatch)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: True)
    result = _process(store)
    raw = fake_di.recorded(STEM)
    raw["documents"] = "x"
    outcome = azure_check.check_one(_settings(), store, client=FakeClient(raw))
    assert outcome.status == "failed" and "couldn't be read" in outcome.message
    assert not azure_check.cache_path(store.get_invoice(result.invoice_id)["file_sha256"]).exists()
    again = InvoicePipeline(_settings(), store.reference_data(), store=store).process(
        SAMPLES / f"{STEM}.pdf", save=False, invoice_id=result.invoice_id
    )
    assert again.ok and again.capture.fields["invoice_number"].value == "NW-2026-0912"


def test_off_there_is_no_reading_status_line(store):
    from ap_coder.ai import reading

    assert reading.reading_status(_settings(), store).reader(reading.AZURE_DI) is None


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
    caplog.set_level(logging.DEBUG)
    client = _client(store)
    monkeypatch.setenv("AP_DEVELOPER_TOOLS", "1")  # IT sets it up with the developer tools on (B-516)
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
        def analyze(self, *args, **kwargs):
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


def test_the_check_reads_through_the_real_client_and_contacts_only_its_host(store, monkeypatch):
    """The real client (``di_http``) against the stand-in for Azure on 127.0.0.1: the invoice is read (and the
    reading deleted from Azure), and every socket opened is for the endpoint's host."""
    from ap_coder.ai import di_http
    from tests import fake_di

    _turn_on(store, monkeypatch)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: True)
    result = _process(store)
    asked = []
    with fake_di.FakeDi() as fake:

        def client(cfg):
            def connect_to(host, port):
                asked.append(host)
                return fake.connect_to(host, port)

            return di_http.DiClient(cfg.endpoint, cfg.api_key, ca_file=str(fake_di.CA_FILE), connect_to=connect_to,
                                    sleep=lambda s: None)  # fmt: skip

        monkeypatch.setattr(azure_check, "make_client", client)
        outcome = azure_check.check_one(_settings(), store)
    assert outcome.status == "done", outcome.message
    assert set(asked) == {HOST} and fake.deleted
    assert [m for m, *_ in fake.calls] == ["POST", "GET", "GET", "DELETE"]
    assert store.azure_check(result.invoice_id)["result"]["azure"]["invoice_number"] == "NW-2026-0912"


def test_ap_allow_internet_is_not_needed_nor_changed(store, monkeypatch):
    import os

    _turn_on(store, monkeypatch)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: True)
    result = _process(store)
    azure_check.check_one(_settings(), store, client=FakeClient(_raw_from(result.capture)))
    assert "AP_ALLOW_INTERNET" not in os.environ


# --- Test connection and the card ------------------------------------------------------------------------------


def test_test_connection_reads_the_sample_and_shows_the_comparison(store, monkeypatch):
    client = _client(store)
    assert client.post("/settings/azure-check/test", headers=PAGE).json()["ok"] is False  # no endpoint yet
    azure_check.save_settings(store, enabled=False, endpoint=ENDPOINT, api_key=KEY)
    ours = azure_check.connection_test  # the real comparison, Azure faked
    from ap_coder.capture import analyze

    fake = FakeClient(_raw_from(analyze(azure_check.SAMPLE_INVOICE)))
    monkeypatch.setattr(azure_check, "make_client", lambda cfg: fake)
    refused = client.post("/settings/azure-check/test", headers=PAGE).json()  # off: nothing is sent, not even a test
    assert refused["ok"] is False and "Switch it on" in refused["error"] and fake.calls == []
    monkeypatch.setenv("AP_DEVELOPER_TOOLS", "1")  # (the page with the check off: the developer tools, B-516)
    assert "nothing is sent to Azure while it is off" in client.get("/settings/azure-check").text
    azure_check.save_settings(store, enabled=True, endpoint=ENDPOINT, api_key=KEY)
    answer = client.post("/settings/azure-check/test", headers=PAGE).json()
    assert answer == {"ok": True, "job": "azure-test", "status": "/jobs/azure-test"}
    app_jobs = client.app.state.jobs
    assert app_jobs.get("azure-test").wait(60)
    snap = client.get("/jobs/azure-test").json()
    assert snap["state"] == "done" and snap["result"]["messages"][0]["text"].startswith("Azure agrees on")
    record = azure_check.last_test(store)
    assert record["ok"] and record["host"] == HOST and fake.calls == [""]
    page = client.get("/settings/azure-check").text
    assert "Connected" in page and "Azure agrees on" in page and KEY not in page
    assert ours is azure_check.connection_test


def test_the_card_saves_which_invoices_and_the_pages_and_shows_the_model_fixed(store, monkeypatch):
    client = _client(store)
    monkeypatch.setenv("AP_DEVELOPER_TOOLS", "1")
    page = client.get("/settings/azure-check").text
    assert "Azure Document Intelligence" in page and "Only those AP Coder is unsure of" in page
    assert "prebuilt-invoice, API 2024-11-30" in page and 'name="model"' not in page
    answer = client.post("/settings/azure-check", headers=PAGE, data={
        "enabled": "on", "endpoint": ENDPOINT, "api_key": KEY, "mode": "every", "invoice_pages": "3",
        "spot_percent": "5", "daily_pages": "200"})  # fmt: skip
    assert answer.json()["ok"]
    cfg = azure_check.load_settings(store)
    assert (cfg.mode, cfg.invoice_pages) == (azure_check.ALL, 3)
    assert 'value="every" checked' in client.get("/settings/azure-check").text
    refused = client.post("/settings/azure-check", headers=PAGE, data={"enabled": "on", "endpoint": ENDPOINT,
                                                                       "invoice_pages": "0"})  # fmt: skip
    assert refused.json()["ok"] is False and "pages sent of an invoice" in refused.json()["error"]


def test_the_card_says_when_azure_is_not_reached(store, monkeypatch):
    _turn_on(store, monkeypatch)
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: True)
    _process(store)
    azure_check.check_one(_settings(), store, client=Down())
    monkeypatch.setenv("AP_DEVELOPER_TOOLS", "1")
    page = _client(store).get("/settings/azure-check").text
    assert "not reached since" in page and "invoices are read on this computer" in page


def test_the_doctor_says_off_ready_or_not_reached_without_asking_azure(store, monkeypatch):
    from ap_coder.ops import doctor

    (line,) = doctor.azure_checks(store, _settings())
    assert (line.area, line.status) == ("Azure Document Intelligence", doctor.SKIP) and "off" in line.detail
    _turn_on(store, monkeypatch)
    (line,) = doctor.azure_checks(store, _settings())
    assert line.status == doctor.PASS and HOST in line.detail and "0 of 200 pages today" in line.detail
    monkeypatch.setattr(azure_check, "low_confidence", lambda capture: True)
    _process(store)
    azure_check.check_one(_settings(), store, client=Down())
    (line,) = doctor.azure_checks(store, _settings())
    assert line.status == doctor.WARN and "not reached since" in line.detail and KEY not in line.detail
    azure_check.save_settings(store, enabled=True, endpoint="", api_key=KEY)
    assert doctor.azure_checks(store, _settings())[0].status == doctor.FAIL


def test_the_test_says_the_region_azure_names():
    assert azure_check.region_note("canadacentral")["region_ok"] is True
    assert azure_check.region_note("Canada Central")["region_ok"] is True
    east = azure_check.region_note("eastus")
    assert east["region_ok"] is False and "not Canada Central" in east["region_note"]
    unnamed = azure_check.region_note("")
    assert unnamed["region_ok"] is None and "confirm in the Azure portal" in unnamed["region_note"]


def test_test_connection_through_the_real_client_records_the_region(store, monkeypatch):
    from tests import fake_di

    _turn_on(store, monkeypatch)
    with fake_di.FakeDi(region="canadacentral") as fake:
        client = di_http.DiClient(ENDPOINT, KEY, ca_file=str(fake_di.CA_FILE), connect_to=fake.connect_to,
                                  sleep=lambda s: None)  # fmt: skip
        record = azure_check.connection_test(store, _settings(), client=client)
    assert record["ok"] and record["region"] == "canadacentral" and record["region_ok"] is True
    assert record["summary"].startswith("Azure agrees on") and fake.deleted


def test_the_card_can_be_included_anywhere_without_context(store):
    from ap_coder.web.core import templates

    client = _client(store)
    assert client.get("/settings/azure-check").status_code == 200
    source = '{% include "partials/azure_check.html" ignore missing %}'
    html = templates.env.from_string(source).render(request=type("R", (), {"app": client.app, "url": type(
        "U", (), {"path": "/settings"})()})())  # fmt: skip
    assert 'id="azure-check"' in html and "Off — AP Coder works fully offline without it." in html


def test_settings_reading_shows_the_azure_card_and_its_own_page_is_not_taken_for_a_tab(store, monkeypatch):
    """Settings → Reading includes the card for the Admin, off as it is, in the offline build too (D-245: the owner's
    decision; the developers' own cloud card stays where the internet is allowed, B-516); /settings/azure-check is
    the card's page, not "Settings has no such tab" from Settings' /settings/{tab}."""
    client = _client(store)
    reading = client.get("/settings/reading")
    assert reading.status_code == 200 and 'id="azure-check"' in reading.text
    assert "works fully offline without it" in reading.text
    page = client.get("/settings/azure-check")
    assert page.status_code == 200 and 'id="azure-check"' in page.text
