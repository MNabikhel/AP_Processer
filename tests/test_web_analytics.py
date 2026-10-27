"""The analytics pages of the web dashboard (Learning & accuracy, Spend, Insights, Activity) and their SVG charts:
each page on an empty and on a busy database, the filters and searches (one that matches nothing included), the
supplier controls, the downloads, and the charts as valid, accessible SVG."""

from __future__ import annotations

import copy
import io
import shutil
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from markupsafe import Markup

from ap_coder.capture.supplier import HELD, SUPERVISED
from ap_coder.capture.workflow import learn_from_approval
from ap_coder.core.config import Settings
from ap_coder.core.store import REVIEW, load_sample_setup
from ap_coder.erp.insights import Assumptions
from ap_coder.web import charts
from ap_coder.web.app import create_app
from ap_coder.web.pages.activity import controls_period, filter_events
from ap_coder.web.pages.learning import read_sheets

from .conftest import SAMPLE_STEM, SAMPLES, fill_from

PAGE = {"X-AP-Coder": "1"}
SVG = "{http://www.w3.org/2000/svg}"
PAGES = ("/learning", "/learning?tab=suppliers", "/learning?tab=readers", "/spend", "/insights", "/activity")


@pytest.fixture
def client(store):
    s = Settings()
    return TestClient(create_app(replace(s, llm=replace(s.llm, provider="off")), store), base_url="http://localhost")


def _approve(store, invoice_id, final, reviewer="Ann"):
    store.approve_invoice(invoice_id, final, reviewer)
    learn_from_approval(store, invoice_id, final, actor=reviewer)


@pytest.fixture
def busy(store, tmp_path, ground_truth, setup_demo_template):
    """Sample setup, the demo invoices, a few approvals (one corrected) and one real approved invoice."""
    fill_from(store, setup_demo_template)  # the sample setup, then the demo
    waiting = store.list_invoices(REVIEW)
    for n, row in enumerate(waiting[:3]):
        inv = store.get_invoice(row["id"])
        final = copy.deepcopy(inv["ai_output"])
        if n == 0 and final.get("line_items"):  # the reviewer changes the first line's account
            line = final["line_items"][0]
            line["predicted_gl_code"] = "6999" if line.get("predicted_gl_code") != "6999" else "6100"
        _approve(store, row["id"], final)
    pdf = tmp_path / "real" / f"{SAMPLE_STEM}.pdf"
    pdf.parent.mkdir()
    shutil.copy(SAMPLES / f"{SAMPLE_STEM}.pdf", pdf)
    real = dict(ground_truth, invoice_number="NW-REAL-1")
    invoice_id = store.add_invoice(pdf, real, {"requires_review": False})
    _approve(store, invoice_id, real, reviewer="Bob")
    store.backup_now("manual", actor="Ann")
    return store


def _svgs(html: str) -> list[ET.Element]:
    """Every inline <svg class="apc-chart"> of a page, parsed as XML (fails on invalid SVG)."""
    out, start = [], 0
    while (i := html.find('<svg class="apc-chart"', start)) >= 0:
        end = html.index("</svg>", i) + len("</svg>")
        out.append(ET.fromstring(html[i:end]))
        start = end
    return out


# --- Every page, empty and busy ------------------------------------------------------------------------------------


@pytest.mark.parametrize("path", PAGES)
def test_each_page_renders_on_an_empty_database(client, path):
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert "is on its way" not in response.text  # not the placeholder
    assert "Traceback" not in response.text


def test_the_empty_pages_say_what_comes_there(client, monkeypatch):
    assert "Nothing learned yet" in client.get("/learning").text
    assert "No supplier learned yet" in client.get("/learning?tab=suppliers").text
    assert "A tool for developers" in client.get("/learning?tab=readers").text
    monkeypatch.setenv("AP_DEVELOPER_TOOLS", "1")
    readers = client.get("/learning?tab=readers").text
    assert "No reader compared with AP yet" in readers and "Nothing approved yet." in readers
    assert "No spend yet" in client.get("/spend").text
    assert "Insights come after 20 approvals" in client.get("/insights").text  # (B-515)
    assert "No activity yet" in client.get("/activity").text


@pytest.mark.parametrize("path", PAGES)
def test_each_page_renders_on_a_busy_database(client, busy, path):
    response = client.get(path)
    assert response.status_code == 200, response.text
    assert "is on its way" not in response.text


def test_learning_shows_accuracy_lessons_vendors_and_memory(client, busy):
    text = client.get("/learning").text
    for label in ("Coding accuracy", "Lines reviewed", "Corrections taught", "Approved as coded",
                  "Accuracy by week", "Latest in the coding memory", "Accuracy by vendor", "Most common corrections",
                  "Coding memory: every line", "Teach from past coding"):  # fmt: skip
        assert label in text, label
    assert "corrected" in text and "90% target" in text
    (chart,) = _svgs(text)
    assert chart.get("role") == "img" and chart.find(f"{SVG}title").text == "Coding accuracy by week"


def test_insights_shows_the_business_case_and_the_straight_through_rate(client, busy, monkeypatch):
    from ap_coder.web.pages import insights

    monkeypatch.setattr(insights, "MIN_APPROVALS", 1)  # (its figures show from 20 approvals: B-515)
    text = client.get("/insights").text
    assert "Cloud cost" not in text  # the offline build has none (B-515)
    for label in (
        "Invoices processed",
        "Approved as coded",
        "Hours saved",
        "Needs attention",
        "AP operations",
        "Waiting by age",
        "What the checks catch",
        "Approvals by week",
        "Net benefit",
        "Assumptions",
    ):
        assert label in text, label
    (chart,) = _svgs(text)
    legend = "".join(chart.itertext())
    assert "Approved as coded" in legend and "Corrected" in legend
    report = client.get("/insights/business-case")
    assert report.status_code == 200 and "attachment" in report.headers["content-disposition"]
    assert "<html" in report.text.lower()


def test_insights_assumptions_are_saved_and_checked(client, busy, monkeypatch):
    from ap_coder.web.pages import insights

    monkeypatch.setattr(insights, "MIN_APPROVALS", 1)
    form = {f: "1" for f in Assumptions.__dataclass_fields__} | {"monthly_volume": "1200", "hourly_cost": "52"}
    response = client.post("/insights/assumptions", data=form, headers=PAGE)
    assert response.status_code == 200 and response.json()["ok"]
    saved = Assumptions.load(busy)
    assert saved.monthly_volume == 1200 and saved.hourly_cost == 52.0
    assert "At 1,200 invoices a month" in client.get("/insights").text
    bad = client.post("/insights/assumptions", data=form | {"hourly_cost": "lots"}, headers=PAGE)
    assert bad.status_code == 400 and "enter a number" in bad.json()["error"]
    assert client.post("/insights/assumptions", data=form).status_code == 403  # no page header: refused


def test_insights_assumptions_refuse_an_endless_invoice_count(client, busy):
    form = {f: "1" for f in Assumptions.__dataclass_fields__}
    for endless in ("1e309", "inf", "nan"):
        bad = client.post("/insights/assumptions", data=form | {"monthly_volume": endless}, headers=PAGE)
        assert bad.status_code == 400 and "enter a number" in bad.json()["error"], endless


def test_spend_shows_the_period_charts_and_tables(client, busy):
    text = client.get("/spend?start=2000-01-01&end=2100-12-31").text
    for label in ("Spend", "Vendors", "Per month", "By month", "Top GL accounts", "Top vendors"):
        assert label in text, label
    (chart,) = _svgs(text)
    assert chart.find(f"{SVG}title").text.startswith("Spend by month")
    assert chart.findall(f".//{SVG}g[@class='bar']")


def test_spend_filters_backwards_bad_and_empty_periods(client, busy):
    assert "The start date is after the end date." in client.get("/spend?start=2026-05-01&end=2026-01-01").text
    assert "No spend yet" in client.get("/spend?start=1990-01-01&end=1990-12-31").text
    odd = client.get("/spend?start=not-a-date&currency=XYZ")
    assert odd.status_code == 200 and "could not be read" in odd.text


def test_spend_workbook_downloads(client, busy):
    response = client.get("/spend/data.xlsx")
    assert response.status_code == 200 and response.content[:2] == b"PK"
    assert "attachment" in response.headers["content-disposition"]


# --- Activity ----------------------------------------------------------------------------------------------------


def test_activity_lists_filters_and_searches(client, busy):
    text = client.get("/activity").text
    for label in ("Events", "Invoices approved", "People", "Deletions", "Controls report", "Duplicate payment audit",
                  "Timeline", "Table"):  # fmt: skip
        assert label in text, label
    backups = client.get("/activity?group=Backups").text
    assert "Backup made" in backups and "Approved ·" not in backups
    by_bob = client.get("/activity?person=Bob&view=table").text
    assert "NW-REAL-1" in by_bob and "Ann</td>" not in by_bob
    found = client.get("/activity", params={"q": "NW-REAL-1"}).text
    assert "NW-REAL-1" in found and "No events match" not in found


def test_an_activity_search_that_matches_nothing_says_so(client, busy):
    """It crashed in the earlier dashboard (an empty timeline): here it is a plain "No events match"."""
    for params in ({"q": "nothing like this"}, {"q": "nothing like this", "view": "table"},
                   {"group": "Backups", "person": "Nobody"}, {"group": "Not a group", "view": "nonsense"}):  # fmt: skip
        response = client.get("/activity", params=params)
        assert response.status_code == 200
    assert "No events match" in client.get("/activity", params={"q": "nothing like this"}).text
    csv = client.get("/activity/events.csv", params={"q": "nothing like this"})
    assert csv.status_code == 200 and csv.text.lstrip("﻿").strip() == "When,Who,What,Invoice,Details"


def test_activity_csv_has_the_filtered_events(client, busy):
    csv = client.get("/activity/events.csv", params={"group": "Backups"})
    assert csv.status_code == 200 and "attachment" in csv.headers["content-disposition"]
    lines = csv.text.lstrip("﻿").strip().splitlines()
    assert len(lines) == 2 and "Backup made" in lines[1]


def test_activity_controls_report_and_duplicate_audit(client, busy):
    page = client.get("/activity?start=2000-01-01&end=2100-01-01").text
    assert "Errors overridden" in page and "Second approvals" in page
    assert "Pick the first and last day" in client.get("/activity?start=2026-05-02&end=2026-05-01").text
    report = client.get("/activity/controls-report?start=2000-01-01&end=2100-01-01")
    assert report.status_code == 200 and "attachment" in report.headers["content-disposition"]
    audit = client.get("/exports?audit=1").text  # the audit is on Exports, Activity links there (B-513)
    assert "Results of" in audit and ("No likely duplicates found" in audit or "Possible duplicate" in audit)
    assert 'href="/exports?audit=1#audit"' in client.get("/activity").text
    dup_csv = client.get("/exports/duplicates.csv")  # (the audit's one download: Activity links to Exports)
    assert dup_csv.status_code == 200 and dup_csv.text.lstrip("﻿").startswith("Why,Amount")


def test_activity_text_is_escaped(client, store):
    load_sample_setup(store)
    store.backup_now("manual", actor="<script>alert(1)</script>")
    text = client.get("/activity").text
    assert "<script>alert(1)</script>" not in text and "&lt;script&gt;alert(1)&lt;/script&gt;" in text


def test_filter_events_and_controls_period_helpers():
    events = [{"action": "backup_made", "actor": "Ann", "detail": {}, "invoice_id": None, "created_at": "2026-01-01"}]
    assert filter_events(events, "Backups", "Ann", "") == events
    assert filter_events(events, "Invoices", "Everyone", "") == []
    assert filter_events(events, "All", "Everyone", "zzz") == []
    import datetime as dt

    today = dt.date(2026, 3, 15)
    assert controls_period(None, None, today) == (dt.date(2026, 3, 1), today)
    assert controls_period("bad", "2099-01-01", today) == (dt.date(2026, 3, 1), today)


# --- Learning: suppliers, memory, history, training export ---------------------------------------------------------


def test_keep_supervised_and_allow_again_persist_and_are_logged(client, busy):
    page = client.get("/learning?tab=suppliers").text
    assert "Keep supervised" in page and "What the clerk corrected" in page
    key = busy.list_supplier_profiles()[0]["key"]
    response = client.post("/learning/suppliers/hold", data={"key": key, "reason": "new contract"}, headers=PAGE)
    assert response.status_code == 200 and response.json()["ok"]
    assert busy.get_supplier_profile(key)["state"] == HELD
    held = busy.events(actions=["autonomy_held"])[0]
    assert "new contract" in str(held["detail"]) and held["actor"]
    page = client.get(response.json()["redirect"]).text
    assert "is kept supervised" in page and "Allow again" in page and "Kept supervised" in page
    response = client.post("/learning/suppliers/allow", data={"key": key}, headers=PAGE)
    assert response.status_code == 200 and busy.get_supplier_profile(key)["state"] == SUPERVISED
    assert busy.events(actions=["autonomy_allowed"])
    assert client.post("/learning/suppliers/hold", data={"key": "nobody"}, headers=PAGE).status_code == 404
    assert client.post("/learning/suppliers/hold", data={"key": key}).status_code == 403


def test_memory_search_and_forget(client, busy):
    rows = busy.feedback_rows()
    assert "No lines match" in client.get("/learning", params={"mem_q": "zzz-nothing"}).text
    found = client.get("/learning", params={"mem_q": rows[0]["vendor_name"]}).text
    assert f'value="{rows[0]["id"]}"' in found
    assert client.post("/learning/memory/forget", headers=PAGE).status_code == 400
    response = client.post("/learning/memory/forget", data={"ids": [rows[0]["id"]]}, headers=PAGE)
    assert response.status_code == 200 and len(busy.feedback_rows()) == len(rows) - 1
    assert busy.events(actions=["lessons_forgotten"])


def test_history_upload_columns_teach_and_forget(client, busy):
    csv = "Supplier;Memo;GL Account;Dept\nAcme;Paper;6100;100\nAcme;Toner;6100;\n;missing vendor;6100;\n"
    response = client.post("/learning/history/upload", files={"file": ("ap.csv", csv.encode(), "text/csv")},
                           headers=PAGE)  # fmt: skip
    assert response.status_code == 200, response.text
    page = client.get(response.json()["redirect"]).text
    assert "Teach 3 lines" in page and '<option value="Supplier" selected>' in page
    token = response.json()["redirect"].split("history=")[1].split("&")[0].split("#")[0]
    missing = client.post("/learning/history/teach", data={"token": token, "col_vendor_name": "Supplier"}, headers=PAGE)
    assert missing.status_code == 400 and "Pick the column for" in missing.json()["error"]
    form = {"token": token, "col_vendor_name": "Supplier", "col_description": "Memo", "col_gl_code": "GL Account",
            "col_cost_center": "Dept"}  # fmt: skip
    taught = client.post("/learning/history/teach", data=form, headers=PAGE)
    assert taught.status_code == 200 and busy.history_count() == 2
    assert "Taught 2 past lines" in client.get(taught.json()["redirect"]).text
    assert client.post("/learning/history/teach", data=form, headers=PAGE).status_code == 400  # used up
    bad = client.post("/learning/history/upload", files={"file": ("x.pdf", b"%PDF", "application/pdf")}, headers=PAGE)
    assert bad.status_code == 400
    empty = client.post("/learning/history/upload", files={"file": ("x.csv", b"", "text/csv")}, headers=PAGE)
    assert empty.status_code == 400 and "empty" in empty.json()["error"]
    assert client.post("/learning/history/forget", headers=PAGE).status_code == 200 and busy.history_count() == 0


def test_read_sheets_reads_csv_and_excel():
    import pandas as pd

    assert read_sheets("a.csv", "a,b\n1,2\n".encode("cp1252"))["CSV"] == (["a", "b"], [{"a": "1", "b": "2"}])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf) as writer:
        pd.DataFrame({"Vendor": ["Acme"], 2024: ["x"]}).to_excel(writer, sheet_name="Lines", index=False)
        pd.DataFrame().to_excel(writer, sheet_name="Empty", index=False)
    sheets = read_sheets("a.xlsx", buf.getvalue())
    assert list(sheets) == ["Lines"] and sheets["Lines"][0] == ["Vendor", "2024"]
    with pytest.raises(ValueError):
        read_sheets("a.xlsx", b"not a workbook")


def test_readers_tab_and_training_export_download_a_zip(client, busy, monkeypatch):
    monkeypatch.setenv("AP_DEVELOPER_TOOLS", "1")  # a developer tool (B-511)
    page = client.get("/learning?tab=readers").text
    assert "Scorecard" in page and "Field by field" in page and "Download training data (ZIP)" in page
    assert "Demo invoices and invoices approved without a person are never included." in page
    picked = client.get("/learning", params={"tab": "readers", "reader": "OCR + rules"})
    assert picked.status_code == 200
    response = client.get("/learning/training.zip")
    assert response.status_code == 200 and response.headers["content-type"] == "application/zip"
    assert "attachment" in response.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
        names = z.namelist()
    assert "fields.jsonl" in names and any(n.endswith(".png") for n in names)


# --- Charts ------------------------------------------------------------------------------------------------------


def test_charts_are_valid_accessible_svg():
    line = charts.line_chart(["W1", "W2", "W3"], charts.Series("Accuracy", [0.8, None, 0.95]), title="Accuracy",
                             desc="Weekly accuracy", y_domain=(0, 1), y_format=charts.percent,
                             target=(0.9, "90% target"))  # fmt: skip
    stacked = charts.bar_chart(["Jan", "Feb"], [charts.Series("A", [5, -2]), charts.Series("B", [3, 4], "#eb6834")],
                               title="Stacked", mode="stacked")  # fmt: skip
    grouped = charts.bar_chart(
        ["Q1"], [charts.Series("A", [1200]), charts.Series("B", [900])], title="Group", mode="grouped"
    )
    for chart in (line, stacked, grouped):
        assert isinstance(chart, Markup)
        root = ET.fromstring(str(chart))
        assert root.tag == f"{SVG}svg" and root.get("role") == "img"
        title_id, desc_id = root.get("aria-labelledby").split()
        assert root.find(f"{SVG}title").get("id") == title_id and root.find(f"{SVG}desc").get("id") == desc_id
    root = ET.fromstring(str(line))
    assert len(root.findall(f".//{SVG}g[@class='pt']")) == 2  # no point for the week without a value
    assert "90% target" in "".join(root.itertext())
    assert len(ET.fromstring(str(stacked)).findall(f".//{SVG}g[@class='bar']")) == 4
    assert "1.2K" in "".join(ET.fromstring(str(grouped)).itertext())


def test_chart_text_is_escaped_and_empty_charts_draw():
    chart = charts.bar_chart(["<b>&"], charts.Series('"><script>', [3]), title="<script>x</script>")
    assert "<script>" not in str(chart) and ET.fromstring(str(chart)) is not None
    empty = charts.line_chart([], charts.Series("None", []), title="Empty")
    assert ET.fromstring(str(empty)).get("role") == "img"
    with pytest.raises(ValueError):
        charts.bar_chart(["a"], charts.Series("A", [1]), title="t", mode="pie")


def test_nice_ticks_and_compact():
    assert charts.nice_ticks(0, 1) == [0, 0.25, 0.5, 0.75, 1.0]
    ticks = charts.nice_ticks(-30, 95)
    assert 0 in ticks and ticks[0] <= -30 and ticks[-1] >= 95
    assert [charts.compact(v) for v in (0, 950, 1200, 12000, 3_400_000)] == ["0", "950", "1.2K", "12K", "3.4M"]
