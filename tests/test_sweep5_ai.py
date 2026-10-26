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

    from .test_capture_edge_cases import TODAY, _unicode_pdf

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
