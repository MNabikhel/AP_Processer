"""Dashboard HTML building blocks: escaping and the small calculations behind the visuals."""

import base64
import datetime as dt

from ap_coder import ui


def test_all_text_is_escaped():
    evil = "<script>alert(1)</script>"
    inv = {"id": 1, "vendor_name": evil, "invoice_number": evil, "invoice_date": "2026-01-01", "grand_total": 1,
           "currency": evil, "adjusted_confidence": 0.9, "requires_review": 0, "file_name": "x.pdf"}  # fmt: skip
    outputs = [
        ui.queue_card(inv, ["GST"], evil),
        ui.check("error", evil, evil, evil),
        ui.pill(evil),
        ui.page_header(evil, evil, evil),
        ui.invoice_hero(evil, [("event", evil)], [], 0.5, 10, evil),
        ui.split_bar([(evil, 5.0)]),
        ui.vendor_row(evil, 3, 0.5, 1),
        ui.sidebar_profile(evil, 1, 2),
        ui.step("ok", evil, evil),
        ui.empty_state(evil, evil),
    ]
    for html in outputs:
        assert "<script>" not in html


def test_no_inline_svg_reaches_the_page():
    """Streamlit's sanitiser strips inline <svg>; graphics must be CSS or data-URI images."""
    html = ui.tile("x", 1, "inbox", spark=[0.1, 0.5, 0.9]) + ui.empty_state("a", "b") + ui.ring(0.5)
    assert "<svg" not in html
    assert "data:image/svg+xml;base64," in html


def test_sparkline_is_a_valid_svg_image():
    html = ui.sparkline([0.2, 0.4, 0.8])
    data = html.split("base64,")[1].split("'")[0]
    svg = base64.b64decode(data).decode()
    assert svg.startswith("<svg xmlns='http://www.w3.org/2000/svg'") and "<polyline" in svg
    assert ui.sparkline([0.5]) == ""


def test_ring_clamps_and_labels():
    assert "--p:1.0000" in ui.ring(1.7)
    assert "--p:0.0000" in ui.ring(-1)
    assert ">81%<" in ui.ring(0.81)
    assert ">3/4<" in ui.ring(0.75, label="3/4")


def test_split_bar_folds_small_segments_into_other():
    segments = [(f"GL {i}", float(10 - i)) for i in range(8)]
    html = ui.split_bar(segments, max_segments=4)
    assert html.count("<i style=") == 4 and "Other" in html
    assert "GL 0" in html and "GL 7" not in html


def test_initials_and_stable_avatar_colours():
    assert ui.initials("Northwind IT Solutions Inc.") == "NI"
    assert ui.initials("acme") == "AC"
    assert ui.initials("") == "?"
    assert ui.avatar("Pacific Office") == ui.avatar("pacific office")


def test_time_ago():
    now = dt.datetime(2026, 10, 6, 12, 0, 0)
    assert ui.time_ago("2026-10-06T11:59:30", now) == "just now"
    assert ui.time_ago("2026-10-06T10:00:00", now) == "2 hours ago"
    assert ui.time_ago("2026-10-05T12:00:00", now) == "1 day ago"
    assert ui.time_ago(None, now) == ""


def test_greeting():
    assert ui.greeting(dt.datetime(2026, 1, 1, 9)) == "Good morning"
    assert ui.greeting(dt.datetime(2026, 1, 1, 14)) == "Good afternoon"
    assert ui.greeting(dt.datetime(2026, 1, 1, 20)) == "Good evening"
