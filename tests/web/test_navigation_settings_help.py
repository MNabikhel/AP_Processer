"""Before the pilot tester starts testing (docs/BUGS.md, Sweep 4, "Before testing: navigation, settings and help"):
the menu fits a laptop, the getting-started checklist counts what it shows, Settings opens on what a tester changes,
Help is grouped by what the tester does, and nothing in the offline build talks about a cloud it doesn't use."""

from __future__ import annotations

import re
import shutil
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from ap_coder.core.config import Settings
from ap_coder.core.store import Store
from ap_coder.web.app import create_app
from ap_coder.web.pages import PAGES
from ap_coder.web.pages import exports as exports_page
from tests.browser import browser_on

PAGE = {"X-AP-Coder": "1"}
CORE = ["Review queue", "Import invoices", "Invoice directory", "Exports", "GL accounts & tax", "Settings", "Help"]
MORE = ["Month-end", "Sales tax", "Vendor statements", "Vendors", "Purchase orders", "Learning & accuracy", "Spend",
        "Insights", "Activity"]  # fmt: skip


def _settings() -> Settings:
    s = Settings()
    return replace(s, llm=replace(s.llm, provider="off"))


@pytest.fixture
def client(store):
    return TestClient(create_app(_settings(), store, start_reader=False), base_url="http://localhost")


@pytest.fixture(scope="module")
def demo_db(demo_template):
    """The demo invoices, made once a run (tests/conftest.py)."""
    return demo_template


@pytest.fixture
def demo_store(demo_db, tmp_path):
    target = tmp_path / "demo" / "ap_coder.db"
    target.parent.mkdir(parents=True)
    shutil.copy(demo_db, target)
    return Store(target)


def _rail(text: str) -> str:
    return text.split('<aside class="rail"', 1)[1].split("</aside>", 1)[0]


# --- The menu (B-502, B-503) ------------------------------------------------------------------------------------------


def test_the_menu_shows_the_seven_core_pages_and_folds_the_rest_under_more(client):
    rail = _rail(client.get("/review").text)
    main, more = rail.split('id="nav-more"', 1)
    more, pinned = more.split("</details>", 1)
    labels = lambda html: re.findall(r'<span class="label">([^<]+)</span>', html)  # noqa: E731
    assert [label.replace("&amp;", "&") for label in labels(main)] == CORE[:5]
    assert [label.replace("&amp;", "&") for label in labels(more)] == ["More", *MORE]
    assert [label.replace("&amp;", "&") for label in labels(pinned)] == CORE[5:]
    assert "<details" in rail and 'id="nav-more" open' not in rail  # folded on a core page
    assert {p.title for p in PAGES} == set(CORE) | set(MORE)


def test_the_more_fold_opens_by_itself_on_one_of_its_pages(client):
    for path in ("/spend", "/activity", "/month-end"):
        rail = _rail(client.get(path).text)
        assert 'id="nav-more" open' in rail, path
        assert 'aria-current="page"' in rail.split('id="nav-more"', 1)[1].split("</details>", 1)[0], path


@pytest.fixture
def browser(chromium, demo_store):
    with browser_on(chromium, create_app(_settings(), demo_store, start_reader=False)) as opened:
        yield opened


def _visible_in_window(page, selector: str) -> bool:
    """The element is inside the window and nothing (the menu's footer) is drawn over its middle."""
    return page.evaluate(
        """(sel) => { const el = document.querySelector(sel); const r = el.getBoundingClientRect();
           if (r.bottom > innerHeight || r.top < 0) return false;
           const hit = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
           return !!hit && (hit === el || el.contains(hit)); }""",
        selector,
    )


@pytest.mark.parametrize("height", [768, 650])
def test_in_a_browser_settings_and_help_are_on_screen_on_a_laptop(browser, height):
    """At 1366 x 768 the menu scrolled under its sticky footer: Settings and Help were below the window (A2)."""
    chromium, base = browser
    page = chromium.new_page(viewport={"width": 1366, "height": height})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/review")
    for href in ("/settings", "/help"):
        assert _visible_in_window(page, f'.rail a[href="{href}"]'), (height, href)
    page.click("#nav-more > summary")  # opened: still on screen, the core pages scroll above them
    for href in ("/settings", "/help"):
        assert _visible_in_window(page, f'.rail a[href="{href}"]'), (height, href)
    page.close()
    assert errors == []


def test_in_a_browser_the_more_fold_is_remembered(browser):
    chromium, base = browser
    page = chromium.new_page(viewport={"width": 1366, "height": 768})
    page.goto(base + "/review")
    assert not page.evaluate("document.getElementById('nav-more').open")
    page.click("#nav-more > summary")
    page.goto(base + "/exports")
    assert page.evaluate("document.getElementById('nav-more').open")  # opened stays open on the next page
    page.click("#nav-more > summary")
    page.goto(base + "/spend")  # a page under More opens it, without changing what was chosen
    assert page.evaluate("document.getElementById('nav-more').open")
    page.goto(base + "/review")
    assert not page.evaluate("document.getElementById('nav-more').open")
    page.close()


# --- Getting started: LM Studio first, a counter that counts what it shows (B-504, B-505) ----------------------------


def _step_rows(html: str) -> list[str]:
    return re.findall(r"<div class='apc-step'><span class='dot (\w+)'", html)


def _step_labels(html: str) -> list[str]:
    row = r"<div class='apc-step'><span class='dot \w+' aria-hidden='true'>[^<]*</span>(?:<a [^>]*>)?([^<]+)"
    return re.findall(row, html)


def test_the_welcome_checklist_counts_exactly_the_rows_it_shows_with_lm_studio_first(client):
    page = client.get("/review").text
    card = page.split("Getting started", 1)[1]
    rows = _step_rows(card)
    done = rows.count("ok")
    assert f"aria-label='{done}/{len(rows)}'" in card, rows
    labels = _step_labels(card)
    assert labels[0] == "LM Studio running" and "OvisOCR2" not in labels and "Your province" in labels
    nxt = page.split('class="card next-step"', 1)[1]  # how to start it: the Next step card above (B-529)
    assert "Developer" in nxt and "Start server" in nxt
    assert 'action="/settings/reading/check"' in nxt and "Check again" in nxt


def test_import_invoices_counts_the_same_rows(client):
    page = client.get("/process").text
    card = page.split("<h4>Setup</h4>", 1)[1].split("</section>", 1)[0]
    rows = _step_rows(card)
    assert f"{rows.count('ok')} of {len(rows)} done" in card
    assert _step_labels(card)[0] == "LM Studio running"


def test_the_province_counts_once_given(client, store):
    count = lambda html: int(re.search(r"aria-label='(\d+)/\d+'", html).group(1))  # noqa: E731
    before = count(client.get("/review").text)
    store.set_setting("our_province", "ON")
    assert count(client.get("/review").text) == before + 1


def test_check_again_from_the_welcome_page_comes_back_to_it(client):
    answer = client.post("/settings/reading/check", data={"back": "/review"}, headers=PAGE).json()
    assert answer["redirect"].startswith("/review")
    answer = client.post("/settings/reading/check", data={"back": "https://elsewhere.example/"}, headers=PAGE).json()
    assert answer["redirect"].startswith("/settings/reading")


def test_without_lm_studio_the_ai_reviewer_says_to_start_it_and_check_again(client):
    from ap_coder.ai import reading

    status = reading.reading_status(_settings(), None)
    line = status.reader(reading.REVIEWER)
    assert line.state == reading.WAITING and "Start server" in line.detail and "Check again" in line.detail
    assert "Waiting for LM Studio" in client.get("/settings/reading").text


def test_the_reading_queue_counts_only_invoices_still_to_approve(store, tmp_path):
    from ap_coder.core.store import APPROVED

    ids = []
    for name in ("a", "b"):
        pdf = tmp_path / f"{name}.pdf"
        pdf.write_bytes(b"%PDF-1.4 " + name.encode())
        coding = {"vendor_name": name, "invoice_number": "1", "grand_total": 1}
        ids.append(store.add_invoice(pdf, coding, {"issues": []}))
        store.queue_page_read(ids[-1], "new")
    with store._conn() as conn:
        conn.execute("UPDATE invoices SET status = ? WHERE id = ?", (APPROVED, ids[1]))
    assert store.page_reads_waiting() == 2  # the reader still reads both (its record against approved values)
    assert store.page_reads_waiting(still_open=True) == 1  # what the tester waits for


# --- Settings (B-506, B-507, B-508, B-511, B-521, B-522, D-52) --------------------------------------------------------


def _tabs(html: str) -> list[str]:
    bar = html.split('<nav class="tabs" aria-label="Settings">', 1)[1].split("</nav>", 1)[0]
    return re.findall(r'href="/settings/(\w+)"', bar)


def test_settings_opens_on_review_and_hides_jd_edwards_until_asked(client, store):
    page = client.get("/settings").text
    assert _tabs(page) == ["review", "reading", "automation", "data", "about", "hub"]  # the finance hub last (D-103)
    assert 'href="/settings/review" class="on" aria-current="page"' in page
    hidden = client.get("/settings/jde")  # still there (nothing removed), and says how to show it
    assert hidden.status_code == 200 and "Show JD Edwards settings" in hidden.text
    assert "jde" not in exports_page.shown_formats(store)  # nor offered on Exports
    answer = client.post("/settings/about/show", data={"show_jde": "1"}, headers=PAGE).json()
    assert answer["ok"] and "jde" in _tabs(client.get("/settings").text)
    assert "jde" in exports_page.shown_formats(store)
    assert "Show JD Edwards settings" not in client.get("/settings/jde").text.split("<form", 1)[0]


def test_the_review_tab_is_one_form_with_one_save_and_the_risky_settings_folded(client):
    page = client.get("/settings/review").text
    tab = page.split('class="st-tab"', 1)[1]
    assert tab.count('method="post"') == 1 and tab.count('type="submit"') == 1
    advanced = tab.split('id="advanced"', 1)[1].split("</details>", 1)[0]
    for name in ('name="threshold"', 'name="constrain"', 'name="why_after"'):
        assert name in advanced, name
    assert "Ask why after the same correction (times)" not in page
    assert 'href="/who"' in tab  # the Who are you? page is linked from here


def test_saving_your_name_says_your_name_was_saved(client):
    form = {"reviewer_name": "Dana Clerk", "threshold": "85", "constrain": "1", "approval_limit": "0",
            "terms_days": "30", "fx_rates": "", "why_after": "3", "our_province": ""}  # fmt: skip
    answer = client.post("/settings/review", data=form, headers=PAGE).json()
    text = client.get(answer["redirect"]).text
    assert "Saved your name." in text and "Invoices read from now on" not in text
    answer = client.post("/settings/review", data={**form, "our_province": "ON"}, headers=PAGE).json()
    assert "Saved your province: invoices read from now on use it." in client.get(answer["redirect"]).text
    answer = client.post("/settings/review", data={**form, "our_province": "ON"}, headers=PAGE).json()
    assert "Nothing changed." in client.get(answer["redirect"]).text


def test_reading_says_whether_lm_studio_runs_and_folds_the_rest_for_it(client):
    page = client.get("/settings/reading").text
    head = page.split('id="for-it"', 1)[0]
    assert "LM Studio: not running" in head and "Check again" in head and "Start server" in head
    assert "<th>Reader</th>" not in head and 'name="rounds"' not in head.split('id="reviewer"', 1)[0]
    folded = page.split('id="for-it"', 1)[1]
    assert "<th>Reader</th>" in folded and "Models in LM Studio" in folded


def test_touchless_is_not_the_pages_main_button_and_says_to_leave_it_off(client):
    page = client.get("/settings/automation").text
    assert '<summary class="btn"><span class="ms" aria-hidden="true">bolt</span>Turn on' in page
    assert "Leave it off during the pilot." in page and "30 to 40" in page


def test_supplier_learning_says_how_many_more_invoices_once(client, demo_store):
    demo = TestClient(create_app(_settings(), demo_store, start_reader=False), base_url="http://localhost")
    page = demo.get("/learning?tab=suppliers").text
    assert "accuracy at least" not in page
    rows = page.split("an-supplier")[1:]
    assert rows and all(row.count("more invoice") <= 1 for row in rows)


def test_the_readers_tab_and_the_training_data_are_developer_tools(client, store, monkeypatch):
    page = client.get("/learning").text
    assert "tab=readers" not in page
    assert "A tool for developers" in client.get("/learning?tab=readers").text
    assert client.get("/learning/training.zip").status_code == 404
    monkeypatch.setenv("AP_DEVELOPER_TOOLS", "1")
    assert "tab=readers" in client.get("/learning").text
    assert client.get("/learning/training.zip").status_code == 200


def test_about_folds_the_full_paths_for_it(client, store):
    page = client.get("/settings/about").text
    tab = page.split('class="st-tab"', 1)[1]
    head, folded = tab.split('id="for-it"', 1)
    assert str(store.path.parent) not in head and "Data folder" in folded
    assert "close AP Coder" not in tab  # an update may be started with it open


def test_who_is_a_proper_page_linking_to_settings(client, monkeypatch):
    from ap_coder.web import identity

    monkeypatch.setattr(identity, "from_this_computer", lambda scope: True)  # (the test client is no browser here)
    page = client.get("/who").text
    assert "Who are you?" in page and 'href="/settings/review"' in page and "Activity log" in page


# --- Plain words, one place for each thing (B-512, B-513, B-515, B-517, B-519, M4) ---------------------------------


def _demo_client(demo_store) -> TestClient:
    return TestClient(create_app(_settings(), demo_store, start_reader=False), base_url="http://localhost")


def test_the_queue_figures_are_plain_and_the_coding_memory_has_one_name(demo_store):
    client = _demo_client(demo_store)
    queue = client.get("/review").text
    assert "fully approved in all" not in queue and "Lessons learned" not in queue
    assert "still to review" in queue and "fully approved" in queue and "Coding memory" in queue  # (B-1006)
    learning = client.get("/learning").text
    assert "Recent lessons" not in learning and "Memory: every lesson" not in learning
    assert "Coding memory: every line AP confirmed, corrected or coded" in learning
    assert "Coding memory" in client.get("/settings/data").text
    assert "Lesson" in client.get("/learning?tab=lessons").text  # lessons: the repeated corrections, their own tab


def test_the_duplicate_payment_audit_is_in_one_place(demo_store):
    client = _demo_client(demo_store)
    assert 'href="/exports?audit=1#audit"' in client.get("/activity").text
    assert "Run the audit" not in client.get("/activity?audit=1").text
    assert "Duplicate payment audit" in client.get("/exports").text


def test_insights_has_no_cloud_cost_offline_and_waits_for_twenty_approvals(demo_store, monkeypatch):
    from ap_coder.web.pages import insights

    client = _demo_client(demo_store)
    early = client.get("/insights").text
    assert "Insights come after 20 approvals" in early and "Cloud cost" not in early
    monkeypatch.setattr(insights, "MIN_APPROVALS", 1)
    page = client.get("/insights").text
    assert "Hours saved" in page and "Cloud cost" not in page and "Azure" not in page
    answer = client.post("/insights/assumptions", headers=PAGE, data={  # the hidden prices are posted as they are
        f["name"]: f["value"] for f in insights.assumption_fields(insights.Assumptions())}).json()  # fmt: skip
    assert answer["ok"]


def test_vendors_say_where_ask_the_vendor_is(demo_store, tmp_path):
    doc = demo_store.get_invoice(demo_store.list_invoices()[0]["id"])["ai_output"]
    for n in (1, 2):  # a vendor that makes work, still to review (B-1183)
        demo_store.add_invoice(tmp_path / f"{n}.pdf", {**doc, "invoice_number": f"W-{n}"},
                               {"issues": [{"severity": "error", "code": "GST_HST_NUMBER_MISSING"}]})  # fmt: skip
    page = _demo_client(demo_store).get("/vendors").text
    assert "Ask the vendor</i> above drafts" not in page and "open a vendor in the list above" in page


def test_exports_open_on_the_last_format_used(demo_store):
    client = _demo_client(demo_store)
    page = client.get("/exports").text
    assert 'href="/exports?fmt=xlsx#ready" class="on"' in page  # Excel at first
    ids = [str(r["id"]) for r in demo_store.unexported_approved()]
    answer = client.post("/exports/create", data={"fmt": "csv", "ids": ids}, headers=PAGE).json()
    assert answer["ok"] and exports_page.last_format(demo_store) == "csv"
    assert "CSV" in client.get("/exports").text


# --- Help, the setup report and the offline build (B-514, B-516, B-520) ---------------------------------------------


def test_help_groups_the_checks_by_what_to_do_folded_without_codes(client):
    from ap_coder.ops.help import CLOUD_CHECKS, QUICK_START

    page = client.get("/help").text
    checks = page.split('id="checks"', 1)[1].split("</section>", 1)[0]
    assert checks.count('<details class="hp-group"') >= 5 and '<details class="hp-group" open' not in checks
    assert "Fix the coding" in checks and "Before you pay: duplicates and fraud" in checks
    assert "hp-code" not in checks  # the codes are not shown (still searchable, and anchors)
    assert "DUPLICATE_INVOICE</div>" not in checks and 'id="check-DUPLICATE_INVOICE"' in checks
    for code in CLOUD_CHECKS:  # Azure's checks: not in the offline build
        assert f'id="check-{code}"' not in checks, code
    assert "open" in client.get("/help?q=duplicate").text.split('class="hp-group"', 1)[1][:10]
    # Getting started in the Quick start's order, LM Studio first; the developer changelog only for developers
    started = page.split("Getting started", 1)[1].split("</section>", 1)[0]
    assert [t for t, _, _ in QUICK_START][0] == "Start LM Studio" and "Start LM Studio" in started
    assert started.index("Your name and province") < started.index("Your GL accounts and sales tax")
    assert "What's new for testers" in page and "developer changelog" not in page
    assert "PR #" not in page and "schema v" not in page


def test_report_a_problem_opens_at_once_and_fetches_the_setup_report(client, monkeypatch):
    from ap_coder.web.pages import help as help_page

    made = []
    monkeypatch.setattr(help_page, "_make_setup_report", lambda store: made.append(1) or "## report")
    help_page._setup_cache.clear()
    page = client.get("/help/report").text
    assert made == [] and 'data-url="/help/report/setup"' in page  # not made while the page is drawn
    assert client.get("/help/report/setup").text == "## report"
    assert client.get("/help/report/setup").text == "## report" and made == [1]  # kept
    assert "## report" in client.get("/help/report").text  # once made, the page has it at once


def test_the_offline_doctor_report_has_no_cloud_lines(monkeypatch):
    from ap_coder.ops.doctor import FAIL, PASS, SKIP, Check, format_checks

    checks = [Check("package openai", PASS, "3.0"), Check("DI endpoint", SKIP, "not set"),
              Check("AOAI connectivity", SKIP, "run with --online"), Check("prompt size", PASS, "~2,450 tokens"),
              Check("GL accounts", PASS, "26 active rows; enforced as schema enum"), Check("x", FAIL, "y")]  # fmt: skip
    text = format_checks(checks)
    for gone in ("DI endpoint", "AOAI", "prompt size", "schema enum"):
        assert gone not in text, gone
    assert "LM Studio's client" in text and "1 failure(s)" in text
    monkeypatch.setenv("AP_ALLOW_INTERNET", "1")
    assert "AOAI connectivity" in format_checks(checks)


def test_the_azure_check_page_says_it_is_not_part_of_the_offline_build(client, monkeypatch):
    page = client.get("/settings/azure-check").text
    assert "Not part of this build" in page and 'id="azure-check"' not in page
    monkeypatch.setenv("AP_DEVELOPER_TOOLS", "1")
    assert 'id="azure-check"' in client.get("/settings/azure-check").text


# --- No cost centers for the pilot (D-53) ---------------------------------------------------------------------------


@pytest.mark.parametrize("url", ["/review/sample-gl", "/accounts/sample"])
def test_the_sample_gl_accounts_come_without_cost_centers(client, store, url):
    assert client.post(url, headers=PAGE).json()["ok"]
    assert store.list_accounts("gl_accounts") and store.list_accounts("cost_centers") == []
    assert store.reference_data().cost_centers is None  # a line needs only its GL account


def test_the_demo_needs_no_cost_centers(demo_store):
    from ap_coder.core.store import REVIEW

    assert demo_store.list_accounts("cost_centers") == []
    reports = [demo_store.get_invoice(i["id"])["validation"] or {} for i in demo_store.list_invoices(REVIEW)]
    codes = {i.get("code") for r in reports for i in r.get("issues", [])}
    assert reports and not codes & {"CC_UNASSIGNED", "CC_UNKNOWN"}
    page = _demo_client(demo_store).get(f"/review/{demo_store.list_invoices(REVIEW)[0]['id']}")
    assert page.status_code == 200


def test_the_accounts_page_names_its_tax_tab_apart_from_the_sales_tax_page(client):
    page = client.get("/accounts").text
    assert ">Sales tax accounts</a>" in page and "sample GL accounts, cost centers" not in page


# --- The guides say what the app does (B-505, B-518, B-525) ---------------------------------------------------------


def test_the_page_modules_hold_the_real_reviewer_whichever_test_ran_first():
    """B-581: the page modules are imported by ``create_app`` the first time it runs. In a test whose ``me`` fixture had
    patched ``web.core.reviewer`` (test_web_invoice, test_web_pilot_walkthrough), ``from ap_coder.web.core import
    reviewer`` copied the patched one into pages/settings.py for the rest of the run, and saving your name a second time
    said it was saved again. conftest.py imports them first."""
    import importlib

    from ap_coder.web import core
    from ap_coder.web.pages import MORE_MODULES, PAGES

    for name in {*(p.module for p in PAGES), *MORE_MODULES}:
        module = importlib.import_module(f"ap_coder.web.pages.{name}")
        for attr in ("reviewer", "login"):
            if hasattr(module, attr):
                assert getattr(module, attr) is getattr(core, attr), (name, attr)
