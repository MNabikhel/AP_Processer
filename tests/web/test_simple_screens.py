"""Simple screens (Settings → About → "What is shown"): the short menu of one person coding invoices to JD Edwards.
A database that never chose shows everything, as before; a laptop's new data folder starts Simple; the pages left
out of the menu still open by their address, saying so in one line."""

from __future__ import annotations

import re
import shutil

from ap_coder.core.store import REVIEW, Store
from ap_coder.erp import jde
from ap_coder.web import core
from ap_coder.web.app import create_app
from ap_coder.web.pages import settings as settings_page
from tests.browser import browser_on
from tests.webclient import PAGE, client_for, web_settings

SIMPLE_MENU = ["To review", "Add invoices", "To key into JDE", "Find an invoice", "What was learned",
               "GL accounts & tax", "JD Edwards files", "Vendors", "Settings", "Help"]  # fmt: skip
SIMPLE_PATHS = ["/review", "/process", "/exports", "/search", "/learning", "/accounts", "/jde-files", "/vendors",
                "/settings", "/help"]  # fmt: skip
HIDDEN = ["/purchase-orders", "/statements", "/month-end", "/spend", "/insights", "/sales-tax", "/manager",
          "/activity", "/settings/hub", "/settings/automation"]  # fmt: skip
SAYS = "Hidden in Simple screens: Settings → About shows everything"


def _rail(text: str) -> str:
    return text.split('<aside class="rail"', 1)[1].split("</aside>", 1)[0]


def _labels(html: str) -> list[str]:
    return [label.replace("&amp;", "&") for label in re.findall(r'<span class="label">([^<]+)</span>', html)]


def _tabs(store: Store) -> list[str]:
    return [key for key, _label in settings_page.shown_tabs(store)]


def _simple(store: Store) -> None:
    store.set_setting(core.SCREENS, core.SIMPLE)


def test_a_database_that_never_chose_shows_everything(client, store):
    assert store.get_setting(core.SCREENS) == "" and not core.simple_screens(store)
    rail = _rail(client.get("/review").text)
    assert 'id="nav-more"' in rail and 'id="nav-setup"' not in rail and "Review queue" in _labels(rail)
    assert _tabs(store) == ["review", "reading", "automation", "data", "about", "hub"]
    assert SAYS not in client.get("/spend").text


def test_simple_screens_show_the_short_menu_in_its_order_with_no_more(client, store):
    _simple(store)
    page = client.get("/exports").text
    rail = _rail(page)
    assert _labels(rail) == SIMPLE_MENU
    assert re.findall(r'<a href="([^"]+)" class="nav-link', rail) == SIMPLE_PATHS
    assert 'id="nav-more"' not in rail and "nav_more.js" not in rail
    main, setup = rail.split('id="nav-setup">Setup<', 1)
    assert _labels(main) == SIMPLE_MENU[:5] and _labels(setup) == SIMPLE_MENU[5:]
    assert "To key into JDE" in page.split("<title>", 1)[1].split("</title>", 1)[0]  # the page under that name too
    assert 'aria-current="page"' in rail


def test_simple_screens_leave_automation_and_the_finance_hub_out_of_settings(client, store):
    _simple(store)
    assert _tabs(store) == ["review", "reading", "data", "about"]
    bar = client.get("/settings").text.split('<nav class="tabs" aria-label="Settings">', 1)[1].split("</nav>", 1)[0]
    assert re.findall(r'href="/settings/(\w+)"', bar) == ["review", "reading", "data", "about"]


def test_a_page_left_out_still_opens_by_its_address_and_says_so(client, store):
    assert not any(SAYS in client.get(path).text for path in HIDDEN)
    _simple(store)
    for path in HIDDEN:
        page = client.get(path)
        assert page.status_code == 200 and SAYS in page.text, path
        assert 'href="/settings/about#show"' in page.text, path
    for path in (*SIMPLE_PATHS, "/settings/about", "/settings/data"):
        assert SAYS not in client.get(path).text, path


def test_settings_about_switches_between_simple_and_everything(client, store):
    assert 'name="screens" value="everything" checked' in client.get("/settings/about").text
    answer = client.post("/settings/about/show", data={"screens": "simple"}, headers=PAGE).json()
    assert answer["ok"] and core.simple_screens(store)
    assert store.events(actions=["settings_changed"])[0]["detail"]["keys"] == [core.SCREENS]
    assert 'name="screens" value="simple" checked' in client.get("/settings/about").text
    client.post("/settings/about/show", data={"show_jde": "1"}, headers=PAGE)  # not sent: the screens as they are
    assert core.simple_screens(store) and core.jde_shown(store)
    client.post("/settings/about/show", data={"screens": "everything", "show_jde": "1"}, headers=PAGE)
    assert store.get_setting(core.SCREENS) == core.EVERYTHING and not core.simple_screens(store)
    assert 'id="nav-more"' in client.get("/review").text


def test_simple_screens_leave_the_manager_out_of_park(demo_db, tmp_path):
    target = tmp_path / "demo" / "ap_coder.db"
    target.parent.mkdir(parents=True)
    shutil.copy(demo_db, target)
    store = Store(target)
    client, invoice = client_for(store), store.list_invoices(REVIEW)[0]["id"]
    assert "Or send it to the manager" in client.get(f"/review/{invoice}").text
    _simple(store)
    page = client.get(f"/review/{invoice}").text
    assert "Or send it to the manager" not in page and "data-park" in page  # Park itself stays


# --- The first run of a new data folder -------------------------------------------------------------------------------


def test_a_laptops_new_data_folder_starts_simple_with_the_departments_jd_edwards_settings(tmp_path):
    db = tmp_path / "new" / "ap_coder.db"
    store = core.first_run(db, server=False)
    assert db.exists() and store.get_setting(core.SCREENS) == core.SIMPLE
    assert core.jde_shown(store) and jde.load(store) == jde.prototype_settings(jde.JdeSettings())
    store.set_setting(core.SCREENS, core.EVERYTHING)  # chosen since: the next start leaves it
    assert core.first_run(db, server=False).get_setting(core.SCREENS) == core.EVERYTHING


def test_an_existing_database_and_the_company_server_start_as_they_are(tmp_path):
    old = tmp_path / "old" / "ap_coder.db"
    Store(old).set_setting("something", "kept")
    for db, server in ((old, False), (tmp_path / "server" / "ap_coder.db", True)):
        store = core.first_run(db, server=server)
        assert store.get_setting(core.SCREENS) == "" and not store.get_setting(jde.SETTING_KEY), db
        assert not core.simple_screens(store) and store.get_setting(core.SHOW_JDE, "unset") == "unset", db


def test_serve_does_the_first_run_of_a_new_laptop_folder(monkeypatch, tmp_path):
    from ap_coder.ops import cli
    from ap_coder.web import server

    started: dict[str, Store] = {}
    monkeypatch.setattr(server, "serve", lambda host, port, **kwargs: started.update(kwargs) or 0)
    db = tmp_path / "private" / "ap_coder.db"
    args = ["--db", str(db), "serve", "--no-browser", "--port", "8643"]
    assert cli.cmd_serve(cli.build_parser().parse_args(args)) == 0
    assert started["store"].path == db and core.simple_screens(Store(db))
    Store(db).set_setting(core.SCREENS, core.EVERYTHING)
    cli.cmd_serve(cli.build_parser().parse_args(args))  # the next start: an existing database, left as it is
    assert not core.simple_screens(Store(db))


# --- In a browser -----------------------------------------------------------------------------------------------------

_MENU_FITS = """() => { const nav = document.querySelector('.rail > .nav');
    return nav.scrollHeight <= nav.clientHeight && !document.getElementById('nav-more')
        && [...document.querySelectorAll('.rail .nav-link')].every((a) => {
            const r = a.getBoundingClientRect(); return r.top >= 0 && r.bottom <= innerHeight; }); }"""


def test_in_a_browser_the_simple_menu_and_the_jd_edwards_files_page_fit_a_laptop(chromium, store, tmp_path):
    """No sideways scroll at 1280 x 700, and the whole Simple menu on screen with no More (at 1366 x 768 too), with
    the made-up company's chart, business units and journal loaded."""
    from ap_coder.bench import jde_exports as made
    from ap_coder.ops import chart_cmd, journal_cmd

    made.write_all(tmp_path / "jde", size="small")
    chart_cmd.import_files(store, tmp_path / "jde" / made.CHART_FILE, tmp_path / "jde" / made.UNITS_FILE)
    journal_cmd.load(store, (tmp_path / "jde" / made.JOURNAL_FILE).read_bytes(), made.JOURNAL_FILE)
    assert store.journal_files()
    _simple(store)
    with browser_on(chromium, create_app(web_settings(), store, start_reader=False)) as (browser, base):
        errors: list[str] = []
        for width, height in ((1280, 700), (1366, 768)):
            page = browser.new_page(viewport={"width": width, "height": height})
            page.on("pageerror", lambda e: errors.append(str(e)))
            for path in ("/jde-files", "/review", "/spend"):
                page.goto(base + path)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), (width, path)
                assert page.evaluate(_MENU_FITS), (width, height, path)
            page.close()
        assert errors == []
