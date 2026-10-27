"""Sweep 5 of reading and AI (ap_coder/ai, capture, bench): the bugs B-1340 on, each kept fixed by a test here."""

from __future__ import annotations

import pytest

from ap_coder.capture.normalize import find_gst_numbers, find_pst_numbers, find_qst_numbers, parse_dates


@pytest.mark.parametrize(
    ("text", "printed"),
    [
        ("Invoice Date………… Oct 15, 2026", "Oct 15, 2026"),  # Word's "…" leaders: one character, three once read
        ("Main-d’œuvre le 15 octobre 2026", "15 octobre 2026"),  # "œ" is two letters once its accents are off
        ("Straße 5, 15.10.2026", "15.10.2026"),
        ("Acme™ Oct 15, 2026", "Oct 15, 2026"),
    ],
)
def test_a_date_after_a_character_written_as_several_keeps_its_place(text, printed):
    """B-1340: the date's place on its line is where it is printed, after a character that is several once read
    ("…", "œ", "ß", "™"): its box covered "2026" alone, and the raw text shown was cut ("ct 15, 2026")."""
    assert [(d.isoformat(), text[a:b]) for d, a, b, _ in parse_dates(text)] == [("2026-10-15", printed)]


def test_a_registration_number_after_a_character_written_as_several_keeps_its_place():
    """B-1340: the GST/HST, QST and PST numbers too ("GST/HST No……… 123456782 RT0001" gave "782 RT0001")."""
    text = "GST/HST No……… 123456782 RT0001"
    assert [(v, text[a:b]) for v, a, b, _ok in find_gst_numbers(text)] == [("123456782RT0001", "123456782 RT0001")]
    text = "QST…… 1234567890 TQ 0001"
    assert [(v, text[a:b]) for v, a, b in find_qst_numbers(text)] == [("1234567890TQ0001", "1234567890 TQ 0001")]
    text = "Acme™ PST No. PST-1234-5678"
    assert [(v, text[a:b]) for v, a, b in find_pst_numbers(text)] == [("PST-1234-5678", "PST-1234-5678")]


def test_the_date_s_box_covers_the_whole_date_after_leaders(tmp_path):
    """B-1340, as read from a PDF: the box shown (and learned by the supplier's template) is the whole date."""
    from ap_coder.capture import analyze, build_layout
    from tests.capture.test_capture_edge_cases import TODAY, _unicode_pdf

    lines = [(60, 60, "Acme Supply Ltd."), (300, 100, "Invoice No: 4512"), (300, 120, "Invoice Date………… Oct 15, 2026"),
             (380, 500, "Subtotal"), (500, 500, "1,000.00"), (380, 515, "GST 5%"), (500, 515, "50.00"),
             (380, 530, "Total"), (500, 530, "1,050.00")]  # fmt: skip
    pdf = _unicode_pdf(tmp_path, lines, "leaders.pdf")
    capture = analyze(pdf, ocr=False, today=TODAY)
    words = [w for ln in build_layout(pdf, ocr=False).pages[0].lines if "Date" in ln.text for w in ln.words]
    date = capture.fields["invoice_date"]
    assert date.value == "2026-10-15"
    (box,) = date.boxes
    covered = [w.text for w in words if w.box.x0 >= box.x0 - 0.001 and w.box.x1 <= box.x1 + 0.001]
    assert covered == ["Oct", "15,", "2026"]


def test_an_empty_labels_workbook_reads_as_no_invoices(tmp_path):
    """B-1341: an empty labels workbook stopped ``evaluate`` with a bare StopIteration (an empty CSV reads as no
    invoices), and the workbook was left open."""
    from openpyxl import Workbook

    from ap_coder.ai.labels import SHEET, load_labels

    wb = Workbook()
    wb.active.title = SHEET
    wb.save(tmp_path / "labels.xlsx")
    assert load_labels(tmp_path / "labels.xlsx") == ({}, [])
    (tmp_path / "labels.csv").write_text("", encoding="utf-8")
    assert load_labels(tmp_path / "labels.csv") == ({}, [])


def test_a_later_batch_the_model_fails_keeps_the_accounts_it_suggested_before(reference):
    """B-1342: an invoice of more than 40 lines is asked in batches; the chat model timing out on the second (or
    writing a row that is not an object) threw away the first batch's accounts too: every line was left for AP."""
    import json

    from ap_coder.ai.inference import SUGGEST_BATCH_LINES, CodingError, suggest_accounts
    from tests.ai.test_local_llm import _PromptCoder, make_completion

    class FailsAfter(_PromptCoder):
        def __init__(self, reference, answered):
            super().__init__(reference)
            self.answered = answered

        def _local_create(self, model, messages, schema=None, **kw):
            if len(self.prompts) >= self.answered:
                raise CodingError("The local model took longer than 300s.", temporary=True)
            return super()._local_create(model, messages, schema, **kw)

    lines = [(n, f"Filter cartridge {n}", 10.0 * n) for n in range(1, SUGGEST_BATCH_LINES + 6)]
    picks = suggest_accounts(FailsAfter(reference, 1), "Staples", lines)
    assert sorted(picks) == list(range(1, SUGGEST_BATCH_LINES + 1))
    with pytest.raises(CodingError):  # no answer at all is still said
        suggest_accounts(FailsAfter(reference, 0), "Staples", lines)

    class OddRows(_PromptCoder):
        def _local_create(self, model, messages, schema=None, **kw):
            rows = [7, "6000", {"line_number": 1, "gl_code": "6000", "cost_center": "", "reason": "filters"}]
            return make_completion(json.dumps({"lines": rows}))

    got = suggest_accounts(OddRows(reference), "Staples", [(1, "Filter", 10.0), (2, "Hose", 5.0)])
    assert got == {1: ("6000", "", "filters")}


@pytest.mark.parametrize(
    "text", ["30 de septiembre de 2026", "septiembre 30, 2026", "30 septiembre 2026", "30-septiembre-2026"]
)
def test_a_spanish_september_is_read(text):
    """B-1343: "septiembre" is the one month name of ten letters; the date patterns took nine at most, so a Spanish
    invoice dated in September had no date read."""
    from ap_coder.capture.normalize import parse_date

    assert parse_date(text) == "2026-09-30"


def test_the_page_reader_finds_blank_pages_in_the_ocr_kept_when_the_invoice_was_read(monkeypatch, tmp_path):
    """B-1344: before OvisOCR2 reads a scanned invoice, its pages are looked at for blanks with OCR; that OCR ran
    without the data folder's OCR cache, so every page was OCR'd again (seconds a page, beside LM Studio), each time
    the invoice was taken from the queue. It reads the OCR the pipeline kept when the invoice came in, as B-1294."""
    from ap_coder.ai import page_worker
    from ap_coder.capture import layout
    from ap_coder.core.config import Settings
    from ap_coder.core.paths import private_dir

    seen = []

    def fake_build_layout(path, **_kw):
        seen.append(layout._ocr_cache_folder.get())
        raise layout.CannotRead("stop here")

    monkeypatch.setattr(layout, "ocr_available", lambda: True)
    monkeypatch.setattr(layout, "build_layout", fake_build_layout)
    assert page_worker.blank_pages(Settings(), tmp_path / "scan.pdf") == (set(), 0)
    assert seen == [private_dir() / ".cache" / "ocr"]
