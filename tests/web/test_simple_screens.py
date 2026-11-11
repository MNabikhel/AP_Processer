"""Simple screens (Settings → About → "What is shown"): the short menu of one person coding invoices to JD Edwards.
A database that never chose shows everything, as before; a laptop's new data folder starts Simple; the pages left
out of the menu still open by their address, saying so in one line."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from ap_coder.core.store import REVIEW, Store
from ap_coder.erp import jde_prototype, jde_settings
from ap_coder.web import core
from ap_coder.web.app import create_app
from ap_coder.web.pages import settings as settings_page
from tests.browser import browser_on
from tests.webclient import PAGE, add_clean, client_for, web_settings

SIMPLE_MENU = ["To review", "Add invoices", "To key into JDE", "Find an invoice", "What was learned", "Rules",
               "GL accounts & tax", "JD Edwards files", "Vendors", "Activity", "Settings", "Help"]  # fmt: skip
SIMPLE_PATHS = ["/review", "/process", "/exports", "/search", "/learning", "/rules", "/accounts", "/jde-files",
                "/vendors", "/activity", "/settings", "/help"]  # fmt: skip
HIDDEN = ["/purchase-orders", "/statements", "/month-end", "/spend", "/insights", "/sales-tax", "/manager",
          "/settings/hub", "/settings/automation"]  # fmt: skip
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
    assert _labels(main) == SIMPLE_MENU[:6] and _labels(setup) == SIMPLE_MENU[6:]
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


def test_settings_about_names_the_short_menus_pages_as_the_menu_shows_them(client, store):
    """B-2316: Settings → About said the Simple screens were "To review, Add invoices, To key into JDE, Find an
    invoice, What was learned, then Setup and Help", without Rules (in the short menu since D-217), and counted
    Activity, which is under Setup there, among the pages only Everything shows."""
    page = client.get("/settings/about").text
    simple, everything = (" ".join(re.sub(r"<[^>]+>", " ", part).split()) for part in
                          page.split('value="simple"', 1)[1].split('value="everything"', 1))  # fmt: skip
    everything = everything.split("</label>", 1)[0].lower()
    assert all(name in simple for name in SIMPLE_MENU[:6]), simple
    assert not any(name.lower() in everything for name in SIMPLE_MENU if name not in ("Settings", "Help")), everything


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
    assert core.jde_shown(store) and jde_settings.load(store) == jde_prototype.prototype_settings(
        jde_settings.JdeSettings()
    )
    store.set_setting(core.SCREENS, core.EVERYTHING)  # chosen since: the next start leaves it
    assert core.first_run(db, server=False).get_setting(core.SCREENS) == core.EVERYTHING


def test_an_existing_database_and_the_company_server_start_as_they_are(tmp_path):
    old = tmp_path / "old" / "ap_coder.db"
    Store(old).set_setting("something", "kept")
    for db, server in ((old, False), (tmp_path / "server" / "ap_coder.db", True)):
        store = core.first_run(db, server=server)
        assert store.get_setting(core.SCREENS) == "" and not store.get_setting(jde_settings.SETTING_KEY), db
        assert not core.simple_screens(store) and store.get_setting(core.SHOW_JDE, "unset") == "unset", db


# --- A data folder an earlier AP Coder made: asked once, on the queue page (D-221) ------------------------------------

CARD = 'id="choose-screens"'


def _old_folder(tmp_path) -> Store:
    """A laptop's database made by an earlier AP Coder: it exists, and its Screens choice was never saved."""
    old = tmp_path / "old" / "ap_coder.db"
    Store(old).set_setting("something", "kept")
    return core.first_run(old, server=False)


def test_an_older_data_folder_is_asked_once_which_screens_to_show(tmp_path):
    store = _old_folder(tmp_path)
    client = client_for(store)
    page = client.get("/review").text
    card = page.split(CARD, 1)[1].split("</section>", 1)[0]
    assert "A shorter menu" in card and "One Account box a line" in card and "JD Edwards chart of accounts" in card
    assert "Use the simple screens" in card and "Keep every page" in card and "Settings → About → Screens" in card
    assert re.findall(r'action="([^"]+)"', card) == ["/settings/about/screens"] * 2
    assert re.findall(r'name="screens" value="(\w+)"', card) == [core.SIMPLE, core.EVERYTHING]
    for path in ("/process", "/exports", "/settings/about"):  # the queue page only
        assert CARD not in client.get(path).text, path
    answer = client.post("/settings/about/screens", data={"screens": core.SIMPLE}, headers=PAGE).json()
    assert answer["ok"] and answer["redirect"].startswith("/review?flash=") and core.simple_screens(store)
    page = client.get(answer["redirect"]).text
    assert CARD not in page and "Settings → About → Screens" in page and _labels(_rail(page)) == SIMPLE_MENU
    assert store.events(actions=["settings_changed"])[0]["detail"]["keys"] == [core.SCREENS]
    assert CARD not in client.get("/review").text


def test_keeping_every_page_is_saved_too_so_the_card_never_returns(tmp_path, demo_db):
    old = _old_folder(tmp_path)
    client = client_for(old)
    refused = client.post("/settings/about/screens", data={"screens": "neither"}, headers=PAGE)
    assert refused.status_code == 400 and old.get_setting(core.SCREENS) == "" and CARD in client.get("/review").text
    assert client.post("/settings/about/screens", data={"screens": core.EVERYTHING}, headers=PAGE).json()["ok"]
    assert old.get_setting(core.SCREENS) == core.EVERYTHING and not core.simple_screens(old)
    page = client.get("/review").text
    assert CARD not in page and 'id="nav-more"' in _rail(page)
    # the queue with invoices in it shows the card too; Everything saved on Settings → About answers it as well
    target = tmp_path / "demo" / "ap_coder.db"
    target.parent.mkdir(parents=True)
    shutil.copy(demo_db, target)
    full = Store(target)
    client = client_for(full)
    assert CARD in client.get("/review").text and "rq-kpis" in client.get("/review").text
    client.post("/settings/about/show", data={"screens": core.EVERYTHING, "show_jde": "1"}, headers=PAGE)
    assert full.get_setting(core.SCREENS) == core.EVERYTHING and CARD not in client.get("/review").text


KEYING = "also sets up keying for JD Edwards (the keying sheet)"


def test_the_simple_screens_chosen_on_the_card_set_up_jd_edwards_keying_unless_it_was_set_up(tmp_path):
    """B-2378: "Use the simple screens" on an older data folder gave the short menu, but not the department's JD
    Edwards set-up a new folder gets (``use_prototype``): no JD Edwards, no keying sheet, the QST recovered. It does
    now, said on the card, and never over JD Edwards settings someone saved; "Keep every page" changes nothing."""
    store = _old_folder(tmp_path / "a")
    client = client_for(store)
    assert KEYING in client.get("/review").text.split(CARD, 1)[1].split("</section>", 1)[0]
    answer = client.post("/settings/about/screens", data={"screens": core.SIMPLE}, headers=PAGE).json()
    assert answer["ok"] and "keying sheet" in client.get(answer["redirect"]).text
    assert jde_settings.load(store) == jde_prototype.prototype_settings() and jde_settings.in_use(store)
    assert store.get_setting(jde_settings.EXPORT_FORMAT) == "jde_keying"
    assert store.tax_treatments()["QST"].treatment == "expense_to_line"

    saved = _old_folder(tmp_path / "b")
    theirs = jde_settings.JdeSettings(company="00070", user="PAT")
    jde_settings.save(saved, theirs)
    jde_settings.show(saved, False)
    client = client_for(saved)
    assert KEYING not in client.get("/review").text  # (the card still asks: their settings are not mentioned)
    assert client.post("/settings/about/screens", data={"screens": core.SIMPLE}, headers=PAGE).json()["ok"]
    assert core.simple_screens(saved) and jde_settings.load(saved) == theirs and not jde_settings.in_use(saved)
    assert saved.get_setting(jde_settings.EXPORT_FORMAT) == "" and saved.tax_treatments()["QST"].treatment != (
        "expense_to_line"
    )

    kept = _old_folder(tmp_path / "c")
    answer = client_for(kept).post("/settings/about/screens", data={"screens": core.EVERYTHING}, headers=PAGE)
    assert answer.json()["ok"]
    assert not kept.get_setting(jde_settings.SETTING_KEY) and kept.get_setting(core.SHOW_JDE, "unset") == "unset"
    assert kept.get_setting(jde_settings.EXPORT_FORMAT) == "" and kept.tax_treatments()["QST"].treatment != (
        "expense_to_line"
    )


def test_while_demo_invoices_are_loaded_the_rail_and_the_card_lead_to_removing_only_them(tmp_path, demo_db):
    """D-232: an older data folder with the demo invoices in it opened with "Demo invoices loaded" at the rail's foot
    and nothing to click; their removal was on Settings → Data & backups only. The rail's words lead there, and the
    queue's screens card offers it too, saying only the demo invoices go. Your own invoices and vendors stay."""
    target = tmp_path / "old" / "ap_coder.db"
    target.parent.mkdir(parents=True)
    shutil.copy(demo_db, target)
    store = core.first_run(target, server=False)
    demo = store.demo_count()
    real = add_clean(store, invoice_number="REAL-0001")
    store.import_vendor_master([{"vendor_name": "Real Supplier Ltd.", "erp_id": "70001"}], only_new=True)
    client = client_for(store)
    page = client.get("/review").text
    assert re.search(r'<a [^>]*href="/settings/data#demo"[^>]*>.*?Demo invoices loaded', _rail(page), re.S)
    card = page.split(CARD, 1)[1].split("</section>", 1)[0]
    assert f"Remove the {demo} demo invoices only" in card and "your own invoices" in card
    form = card.split('action="/demo/remove"', 1)[1].split("</form>", 1)[0]
    answer = client.post("/demo/remove", data=dict(re.findall(r'name="(\w+)" value="([^"]*)"', form)), headers=PAGE)
    assert answer.json()["redirect"].startswith("/review?flash=")
    assert store.demo_count() == 0 and [i["id"] for i in store.list_invoices()] == [real]
    assert "Real Supplier Ltd." in {v["display_name"] for v in store.master_vendors()}
    page = client.get(answer.json()["redirect"]).text
    assert f"Removed {demo} demo invoices" in page and "Demo invoices loaded" not in page
    assert "/demo/remove" not in page and CARD in page  # (the screens are still to choose)


# --- A data folder an earlier AP Coder made, at a start (the launcher, before it serves: D-232) ----------------------

SET_UP = "Set up for JD Edwards keying: the short menu, the keying sheet"


def _copy_of(demo_db, folder) -> Path:
    target = folder / "ap_coder.db"
    target.parent.mkdir(parents=True)
    shutil.copy(demo_db, target)
    return target


def _set_up_for_keying(store: Store) -> None:
    assert core.simple_screens(store) and store.get_setting(core.PILOT_SETUP) == "done"
    assert jde_settings.load(store) == jde_prototype.prototype_settings() and jde_settings.in_use(store)
    assert store.get_setting(jde_settings.EXPORT_FORMAT) == "jde_keying"
    page = client_for(store).get("/review").text
    assert CARD not in page and _labels(_rail(page)) == SIMPLE_MENU


def test_an_older_folder_holding_only_the_demo_invoices_is_set_up_as_a_new_one_without_a_question(tmp_path, demo_db):
    """The pilot tester's laptop: AP Coder from GitHub's ZIP opened an earlier one's data folder, the demo invoices in
    it, with every page and no JD Edwards. A folder with no invoices but the demo's (or none) starts as a new one: the
    demo invoices removed, the short menu and the keying sheet, said in one line, once; nothing is asked."""
    db = _copy_of(demo_db, tmp_path / "old")
    assert Store(db).demo_count() and not Store(db).get_setting(core.SCREENS)
    asked: list[str] = []
    assert core.older_folder_start(db, asked.append) == f"{SET_UP}; the demo invoices removed."
    store = Store(db)
    assert asked == [] and store.demo_count() == 0 and store.list_invoices() == []
    assert store.list_accounts("gl_accounts") == []  # (the demo's sample accounts went with it: none was typed)
    _set_up_for_keying(store)
    assert "Demo invoices loaded" not in client_for(store).get("/review").text
    assert core.older_folder_start(db, asked.append) == "" and asked == []  # once

    empty = tmp_path / "empty" / "ap_coder.db"
    theirs = jde_settings.JdeSettings(company="00070", user="PAT")
    jde_settings.save(Store(empty), theirs)  # JD Edwards settings someone saved: kept as they are
    said = core.older_folder_start(empty)
    assert said == "Set up for JD Edwards keying: the short menu, your JD Edwards settings kept as they are."
    assert core.simple_screens(Store(empty)) and jde_settings.load(Store(empty)) == theirs
    new = tmp_path / "new" / "ap_coder.db"
    assert core.older_folder_start(new) == "" and not new.exists()  # a new folder is first_run's


def test_an_older_folder_with_invoices_of_its_own_is_asked_once_and_each_answer_does_what_it_says(tmp_path, demo_db):
    """Its own invoices there: asked once at the start (Enter for yes). Yes is the card's "Use the simple screens";
    no is recorded and the card still asks; nobody to ask (``ask`` None: --yes, AP_NO_PAUSE, no window) leaves it to
    the card, as before. Every invoice is kept, the demo's too (the rail leads to their removal)."""
    for answer in (True, False, None):
        db = _copy_of(demo_db, tmp_path / str(answer))
        real = add_clean(Store(db), invoice_number="REAL-0001")
        demo = Store(db).demo_count()
        asked: list[str] = []
        ask = None if answer is None else (lambda q, answer=answer, asked=asked: asked.append(q) or answer)
        assert core.older_folder_start(db, ask) == (f"{SET_UP}." if answer else ""), answer
        assert asked == ([] if answer is None else [core.ASK_SIMPLE]), answer
        assert "Your invoices and settings are kept. [Y/n]" in core.ASK_SIMPLE
        store = Store(db)
        assert real in {i["id"] for i in store.list_invoices()} and store.demo_count() == demo, answer
        if answer:
            _set_up_for_keying(store)
        else:
            assert not core.simple_screens(store) and not store.get_setting(jde_settings.SETTING_KEY), answer
            assert store.get_setting(core.PILOT_SETUP) == ("declined" if answer is False else ""), answer
            assert CARD in client_for(store).get("/review").text, answer
        assert core.older_folder_start(db, ask) == "" and len(asked) == (0 if answer is None else 1), answer


def test_a_new_data_folder_and_the_company_server_are_never_asked(tmp_path):
    new = core.first_run(tmp_path / "new" / "ap_coder.db", server=False)
    assert CARD not in client_for(new).get("/review").text
    old = _old_folder(tmp_path)
    assert CARD in client_for(old).get("/review").text  # (the same database on a laptop: asked)
    app = create_app(web_settings(), old, start_reader=False, server=True)
    request = type("Request", (), {"app": app})()
    assert not core.screens_to_choose(request, old) and old.get_setting(core.SCREENS) == ""


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
