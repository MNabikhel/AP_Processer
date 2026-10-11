"""One way to read every invoice: the page reader always reads (and decides touchless approval once it has), every
file type goes through the same readers, OCR or the photo add-on missing is said plainly, and a digital PDF's hidden
text is checked against the page as printed."""

import datetime as dt
import io
import json
import sys
import types
from dataclasses import replace

import pytest
from PIL import Image

from ap_coder import page_reader, page_worker, pipeline
from ap_coder.capture import CannotRead, analyze, layout
from ap_coder.capture.bridge import review_issues
from ap_coder.capture.supplier import AUTONOMOUS, should_auto_approve
from ap_coder.capture.types import CHECK, VERIFIED
from ap_coder.capture.workflow import WAITING_FOR_PAGE_READER
from ap_coder.config import DocumentIntelligenceSettings, Settings
from ap_coder.extraction import DocumentExtractor
from ap_coder.help import help_for
from ap_coder.pipeline import InvoicePipeline
from ap_coder.store import APPROVED, REVIEW, Store, load_sample_setup

from .conftest import SAMPLES

STEM = "harbourview_NS_HST_HPS-2026-0347"
PDF = SAMPLES / f"{STEM}.pdf"
TODAY = dt.date(2026, 10, 11)
MODEL = "ath-maas_ovisocr2"


def _settings():
    s = Settings()
    return replace(s, llm=replace(s.llm, provider="off"))


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "ap.db")
    load_sample_setup(store)
    page_worker.save_test(store, {"model": MODEL, "ok": True, "fields_right": 9, "fields_total": 9})
    return store


# --- Nothing is approved without a person before the page reader has read it ------------------------------------


@pytest.fixture
def touchless(monkeypatch):
    """Every supplier touchless and every invoice passing the bar: only the page reader's wait holds it back."""
    seen = []

    def decide(store, key, profile, capture, report, path, *, awaiting_page_reader=False, output=None):
        assert output is not None  # the coding an approval would post: its total and currency
        seen.append(awaiting_page_reader)
        if awaiting_page_reader:
            return {"state": AUTONOMOUS, "auto": False, "audit": False, "reason": WAITING_FOR_PAGE_READER}
        return {"state": AUTONOMOUS, "auto": True, "audit": False, "reason": "every header field verified"}

    monkeypatch.setattr(pipeline, "autonomy_decision", decide)
    return seen


def test_a_new_invoice_waits_for_the_page_reader_then_it_decides(store, touchless, monkeypatch):
    result = InvoicePipeline(_settings(), store.reference_data(), store=store).process(PDF)
    assert result.ok and touchless == [True]
    assert result.autonomy["reason"] == WAITING_FOR_PAGE_READER and not result.autonomy["auto"]
    assert store.get_invoice(result.invoice_id)["status"] == REVIEW  # with a person, not approved
    assert store.page_read(result.invoice_id)["status"] == "waiting"

    transcript = (SAMPLES / f"{STEM}.md").read_text(encoding="utf-8")
    status = page_reader.ReaderStatus(reachable=True, lm_studio=True, model=MODEL, document_reader=True,
                                      state="loaded", candidates=[MODEL])  # fmt: skip
    monkeypatch.setattr(page_reader, "reader_status", lambda settings, use_cache=True: status)
    monkeypatch.setattr(page_reader, "load_reader", lambda settings, model=None: "")
    monkeypatch.setattr(page_reader, "read_document", lambda settings, path, **kw: page_reader.PageReading(
        model=MODEL, pages=[transcript], seconds=[90.0], page_count=1))  # fmt: skip
    outcome = page_worker.read_one(_settings(), store)
    assert outcome.status == "done" and outcome.updated and outcome.auto_approved
    assert touchless == [True, False]  # decided once the page reader had read it
    assert store.get_invoice(result.invoice_id)["status"] == APPROVED


def test_an_edited_invoice_is_never_approved_by_the_page_reader(store, touchless, monkeypatch):
    result = InvoicePipeline(_settings(), store.reference_data(), store=store).process(PDF)
    store.approve_invoice(result.invoice_id, dict(result.output, invoice_number="TYPED-BY-AP"), "Pat")
    store.reopen(result.invoice_id, "Pat", "wrong cost center")  # AP worked on it meanwhile
    status = page_reader.ReaderStatus(reachable=True, lm_studio=True, model=MODEL, document_reader=True,
                                      state="loaded", candidates=[MODEL])  # fmt: skip
    monkeypatch.setattr(page_reader, "reader_status", lambda settings, use_cache=True: status)
    monkeypatch.setattr(page_reader, "read_document", lambda *a, **k: pytest.fail("a decided invoice isn't read"))
    outcome = page_worker.read_one(_settings(), store)
    assert outcome.status == "skipped" and not outcome.auto_approved
    assert store.get_invoice(result.invoice_id)["status"] == REVIEW


def test_a_text_invoice_does_not_wait_for_the_page_reader(store, touchless):
    result = InvoicePipeline(_settings(), store.reference_data(), store=store).process(
        SAMPLES / "pacific_BC_GST_PST_PO-77120.md"
    )
    assert result.ok and touchless == [False] and store.page_read(result.invoice_id) is None


# --- The same pipeline for every file type ------------------------------------------------------------------------


def test_page_images_are_rendered_only_for_a_coder_that_looks_at_them(store, monkeypatch):
    """The local readers read the page's text: rendering every page as an image for them was time for nothing."""
    monkeypatch.setattr(pipeline, "render_page_images", lambda *a, **k: pytest.fail("rendered for nothing"))
    pipe = InvoicePipeline(Settings(), store.reference_data(), store=store)
    monkeypatch.setattr(pipe.coder, "wants_images", lambda: True)  # a chat model that can see pages
    assert pipe.process(PDF).ok


def test_an_azure_endpoint_is_not_used_offline(monkeypatch):
    settings = Settings(document_intelligence=DocumentIntelligenceSettings(endpoint="https://di.example/"))
    extractor = DocumentExtractor(settings.document_intelligence)
    pipe = InvoicePipeline(settings, object(), extractor=extractor, coder=object())
    assert pipe._reads_locally(PDF)  # this offline build: read on this computer
    monkeypatch.setenv("AP_ALLOW_INTERNET", "1")
    assert not pipe._reads_locally(PDF)


def _png(width=400, height=300) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), "white").save(buf, "PNG")
    return buf.getvalue()


def test_ocr_not_installed_is_said_plainly_never_an_empty_reading(tmp_path, monkeypatch, store):
    photo = tmp_path / "photo.png"
    photo.write_bytes(_png())
    monkeypatch.setattr(layout, "ocr_available", lambda: False)
    with pytest.raises(CannotRead, match="OCR isn't installed.*run APProcessor.bat"):
        layout.build_layout(photo)
    assert layout.build_layout(photo, ocr=False).source == "none"  # asked not to OCR: nothing read, as asked

    import pymupdf

    scan = tmp_path / "scan.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_image(page.rect, stream=_png(850, 1100))  # a scanned page: a picture, no text
    doc.save(scan)
    with pytest.raises(CannotRead, match="OCR isn't installed"):
        layout.build_layout(scan)
    blank = tmp_path / "blank_back.pdf"
    doc = pymupdf.open(PDF)
    doc.new_page()  # the blank back of the sheet: no picture, nothing to OCR
    doc.save(blank)
    assert layout.build_layout(blank).source == "text"

    result = InvoicePipeline(_settings(), store.reference_data(), store=store).process(photo)
    assert not result.ok and result.error == layout.NO_OCR  # no "CannotRead:" in front of it


def test_iphone_photos_need_the_photo_add_on(tmp_path, monkeypatch):
    heic = tmp_path / "IMG_0001.HEIC"
    heic.write_bytes(b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00mif1heic")
    monkeypatch.setattr(layout, "_heif_registered", False)  # pillow-heif isn't installed
    monkeypatch.setattr(layout, "ocr_available", lambda: True)
    with pytest.raises(CannotRead, match="pillow-heif"):
        layout.build_layout(heic)
    with pytest.raises(page_reader.PageReaderError, match="HEIC"):
        page_reader.render_pages(heic, page_reader.OVIS, 1)
    reading = page_reader.read_document(Settings(), heic, model=MODEL)
    assert "pillow-heif" in reading.error and not reading.complete


def test_iphone_photos_are_read_like_any_photo(tmp_path, monkeypatch):
    """pillow-heif's opener is registered once before a picture is opened; the photo is then OCR'd like a JPEG
    (a PNG stands in for the HEIC photo here: Pillow tells a picture by its content)."""
    registered = []
    fake = types.ModuleType("pillow_heif")
    fake.register_heif_opener = lambda: registered.append(1)
    monkeypatch.setitem(sys.modules, "pillow_heif", fake)
    monkeypatch.setattr(layout, "_heif_registered", None)
    monkeypatch.setattr(layout, "ocr_available", lambda: True)
    monkeypatch.setattr(layout, "ocr_image", lambda png, number, engine=None: layout.PageLayout(number, 1, 1, [], []))
    photo = tmp_path / "IMG_0002.heic"
    photo.write_bytes(_png())
    assert ".heic" in layout.IMAGE_EXTENSIONS and ".heif" in layout.IMAGE_EXTENSIONS
    doc = layout.build_layout(photo)
    assert doc.source == "ocr" and len(doc.pages) == 1 and registered == [1]
    assert page_reader.render_pages(photo, page_reader.OVIS, 1) and registered == [1]  # registered once
    assert page_worker.wants_reading(Settings(), photo)


# --- A digital PDF's hidden text against the page as printed ----------------------------------------------------


def _transcript() -> str:
    return (SAMPLES / f"{STEM}.md").read_text(encoding="utf-8")


def _check(capture):
    return next(c for c in capture.checks if c["code"] == "TEXT_LAYER_MATCHES_PAGE")


def test_the_hidden_text_matches_the_page(monkeypatch):
    capture = analyze(PDF, ocr=False, page_text=[_transcript()], today=TODAY)
    check = _check(capture)
    assert check["ok"] and check["figures"] >= 8 and check["confirmed"] == check["figures"]
    assert not [i for i in review_issues(capture) if i[1] == "TEXT_LAYER_MATCHES_PAGE"]
    assert "TEXT_LAYER_MATCHES_PAGE" not in {c["code"] for c in analyze(PDF, ocr=False, today=TODAY).checks}


def test_totals_the_page_doesnt_show_fail_the_check():
    """The PDF's text says one total, the page as printed another: the classic edited-PDF invoice."""
    plain = analyze(PDF, ocr=False, today=TODAY)
    sub, total = (f"{plain.fields[f].value:,.2f}" for f in ("subtotal", "grand_total"))
    printed = _transcript().replace(sub, "9,164.50").replace(total, "10,510.53")
    capture = analyze(PDF, ocr=False, page_text=[printed], today=TODAY)
    check = _check(capture)
    assert not check["ok"] and set(check["fields"]) >= {"subtotal", "grand_total"}
    assert "hidden text" in check["detail"]
    for name in check["fields"]:
        assert capture.fields[name].status == CHECK
        assert "the page as printed doesn't show the amount in the PDF's text" in capture.fields[name].reasons
    issues = [i for i in review_issues(capture) if i[1] == "TEXT_LAYER_MATCHES_PAGE"]
    assert issues and issues[0][0] == "warning"
    ok, reason = should_auto_approve(AUTONOMOUS, capture, checks_ok=True)
    assert not ok and "TEXT_LAYER_MATCHES_PAGE" in reason
    assert help_for("TEXT_LAYER_MATCHES_PAGE").area == "Duplicates & fraud"


def test_one_figure_read_otherwise_is_no_alarm():
    """OvisOCR2 can misread one digit: that field's own disagreement marks it Check, the document check passes."""
    plain = analyze(PDF, ocr=False, today=TODAY)
    total = f"{plain.fields['grand_total'].value:,.2f}"
    misread = _transcript().replace(total, total[:-2] + ("8" if total[-2] != "8" else "6") + total[-1])
    capture = analyze(PDF, ocr=False, page_text=[misread], today=TODAY)
    assert _check(capture)["ok"]
    assert capture.fields["grand_total"].status == CHECK  # the page reader disagreed on it
    assert capture.fields["subtotal"].status == VERIFIED


def test_a_page_that_shows_other_figures_fails_the_check():
    import re

    other = re.sub(r"\d", lambda m: str((int(m.group(0)) + 3) % 10), _transcript())
    check = _check(analyze(PDF, ocr=False, page_text=[other], today=TODAY))
    assert not check["ok"] and "only" in check["detail"] and check["figures"] >= 8


def test_too_few_figures_are_not_judged(tmp_path):
    import pymupdf

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "Invoice 104 from Harbourview Plumbing. Subtotal 11.00 Tax 1.00 Total 12.00")
    small = tmp_path / "small.pdf"
    doc.save(small)
    read = ["Invoice 104. Subtotal 17.00 Tax 1.00 Total 18.00"]
    check = _check(analyze(small, ocr=False, page_text=read, today=TODAY))
    assert check["ok"] and check["figures"] < 8


def test_a_page_reader_that_sees_nothing_of_the_hidden_text_fails_the_check():
    """Text a reader of the PDF finds but the page doesn't show (white or off-page text): the page reader, which reads
    the page as printed, doesn't see it."""
    check = _check(analyze(PDF, ocr=False, page_text=["Invoice\nThank you for your business. 1.00"], today=TODAY))
    assert not check["ok"] and check["confirmed"] == 0


def test_a_page_the_page_reader_found_blank_is_left_out_of_the_comparison(tmp_path):
    import pymupdf

    doc = pymupdf.open(PDF)
    doc.new_page().insert_text((72, 72), "Terms: 2% 10 net 30. Remit to 1,234.00 7,777.00 3,333.33 Account 4,444.44")
    two = tmp_path / "two_pages.pdf"
    doc.save(two)
    check = _check(analyze(two, ocr=False, page_text=[_transcript(), ""], today=TODAY))
    assert check["ok"] and check["confirmed"] == check["figures"]


# --- How every reader is doing: reading_status ---------------------------------------------------------------------


def _ovis(state="loaded"):
    return page_reader.ReaderStatus(reachable=True, lm_studio=True, model=MODEL, document_reader=True, state=state,
                                    candidates=[MODEL])  # fmt: skip


def test_reading_status_with_nothing_running(store):
    from ap_coder.reading import OFF, ReadingStatus, reading_status

    status = reading_status(Settings(), store)
    assert isinstance(status, ReadingStatus)
    assert [r.name for r in status.readers] == ["PDF text layer", "OCR (two engines)", "Page reader (OvisOCR2)",
                                                "Rule reader", "Supplier templates", "Business checks",
                                                "GL coding model"]  # fmt: skip
    assert (status.page_reader_model, status.page_reader_ready, status.self_test) == ("", False, "no model")
    assert status.reader("Page reader (OvisOCR2)").state == OFF and status.reader("GL coding model").state == OFF
    assert status.banner == "LM Studio isn't running: invoices are read by OCR only and wait for a person."
    assert status.queue == 0 and status.eta_minutes is None and status.figures_agree is None


def test_reading_status_when_the_page_reader_is_ready(store, monkeypatch):
    from ap_coder.reading import OK, reading_status

    monkeypatch.setattr(page_reader, "reader_status", lambda settings, use_cache=True: _ovis())
    monkeypatch.setattr(page_reader, "page_seconds_estimate", lambda settings, model=None: 180.0)
    for _ in range(2):
        InvoicePipeline(_settings(), store.reference_data(), store=store).process(PDF)
    store.set_setting(page_worker.FIGURES_KEY, json.dumps({"digital": [98, 100], "scans": [40, 50]}))
    status = reading_status(_settings(), store)
    assert status.page_reader_ready and status.self_test == "passed"
    assert "passed its self-test" in status.self_test_detail
    assert status.banner == "" and status.reader("Page reader (OvisOCR2)").state == OK
    assert (status.queue, status.eta_minutes) == (2, 6.0)  # two invoices of a page, 3 minutes a page
    assert (status.figures_agree, status.figures_count) == (0.98, 100)


@pytest.mark.parametrize(
    ("test", "state", "banner"),
    [
        (None, "pending", "OvisOCR2 is testing itself before it reads invoices"),
        ({"model": MODEL, "ok": False, "fields_right": 3, "fields_total": 9, "when": "2026-10-11T08:00:00"},
         "failed", "OvisOCR2 failed its self-test"),
    ],
)  # fmt: skip
def test_reading_status_while_the_self_test_has_not_passed(store, monkeypatch, test, state, banner):
    from ap_coder.reading import WARN, reading_status

    monkeypatch.setattr(page_reader, "reader_status", lambda settings, use_cache=True: _ovis("downloaded"))
    store.set_setting(page_worker.TEST_KEY, json.dumps({"models": {MODEL: test}}) if test else "")
    status = reading_status(_settings(), store)
    assert (status.self_test, status.page_reader_ready, status.page_reader_model) == (state, False, MODEL)
    assert status.banner.startswith(banner) and status.reader("Page reader (OvisOCR2)").state == WARN


def test_reading_status_never_raises(monkeypatch):
    from ap_coder.reading import reading_status

    class Broken:
        def __getattr__(self, name):
            raise RuntimeError("database is locked")

    monkeypatch.setattr(page_reader, "reader_status", lambda settings, use_cache=True: _ovis())
    status = reading_status(Settings(), Broken())
    assert len(status.readers) == 7 and not status.page_reader_ready and status.banner
    assert reading_status(Settings(), None).readers  # no database at all
    monkeypatch.setattr(page_reader, "reader_status", lambda *a, **k: (_ for _ in ()).throw(OSError("no")))
    assert reading_status(Settings(), None).self_test == "pending"


def test_supplier_templates_are_counted(store):
    from ap_coder.reading import reading_status

    assert "none yet" in reading_status(_settings(), store).reader("Supplier templates").detail
    store.save_supplier_profile("name:harbourview", "Harbourview", template={"fields": {"invoice_number": {}}})
    store.save_supplier_profile("name:other", "Other")
    assert store.supplier_templates_count() == 1
    assert "1 supplier has a template" in reading_status(_settings(), store).reader("Supplier templates").detail
