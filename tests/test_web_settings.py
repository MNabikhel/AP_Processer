"""Settings in the web dashboard: every tab renders (with LM Studio down, and with OvisOCR2 found), saving writes the
.env or the database and is logged, touchless processing needs "I understand", a restore needs RESTORE typed, the
self-test and Load call the page reader's own functions, and nothing from LM Studio or a reader is shown unescaped."""

from __future__ import annotations

import html
import io
import json
import os
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from ap_coder import jde, page_reader, page_worker, paths, reading
from ap_coder.config import Settings
from ap_coder.envfile import read_env
from ap_coder.reading import ReaderLine, ReadingStatus
from ap_coder.store import Store, load_sample_setup
from ap_coder.web.app import create_app
from ap_coder.web.pages import settings as settings_page

PAGE = {"X-AP-Coder": "1"}
MODEL = "ath-maas_ovisocr2"
TABS = ["reading", "automation", "review", "jde", "data", "about"]


@pytest.fixture(autouse=True)
def env_file(tmp_path, monkeypatch):
    """A .env of the test's own, and the environment put back afterwards (saving settings writes os.environ)."""
    path = tmp_path / "private" / ".env"
    monkeypatch.setenv("AP_ENV_FILE", str(path))
    before = dict(os.environ)
    yield path
    os.environ.clear()
    os.environ.update(before)


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    return store


@pytest.fixture
def client(store):
    s = Settings()
    return TestClient(create_app(replace(s, llm=replace(s.llm, provider="off")), store), base_url="http://localhost")


def post(client, url, data=None, **kwargs):
    return client.post(url, data=data or {}, headers=PAGE, **kwargs)


def follow(client, response):
    """The page a POST's answer leads to (with its message)."""
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["ok"], answer
    return client.get(answer["redirect"]).text


def working(**changes) -> ReadingStatus:
    """Every reader working, OvisOCR2 found and trusted."""
    status = ReadingStatus(
        readers=[
            ReaderLine("PDF text layer", "ok", "reads a digital PDF's own text"),
            ReaderLine("OCR (two engines)", "warn", "one engine only: <b>run the launcher</b> again"),
            ReaderLine("Page reader (OvisOCR2)", "ok", "reads every page as a second reader"),
        ],
        page_reader_model=MODEL, page_reader_ready=True, self_test="passed",
        self_test_detail="read 9 of 9 fields right on 2026-10-10", queue=3, eta_minutes=12.0,
        figures_agree=0.97, figures_count=120, lm_studio=True,
    )  # fmt: skip
    for name, value in changes.items():
        setattr(status, name, value)
    return status


@pytest.fixture
def ovis_found(monkeypatch):
    """LM Studio answers and has OvisOCR2 (not loaded) and a chat model (loaded)."""
    monkeypatch.setattr(reading, "reading_status", lambda settings, store, use_cache=True: working())
    running = page_reader.ReaderStatus(reachable=True, lm_studio=True, model=MODEL, document_reader=True,
                                       state="downloaded", candidates=[MODEL])  # fmt: skip
    monkeypatch.setattr(page_reader, "reader_status", lambda settings, use_cache=True: running)
    models = [
        {"model": "qwen3.5-9b", "loaded": True, "context": 8192, "vision": False, "document_reader": False,
         "used_for": "GL suggestions"},
        {"model": MODEL, "loaded": False, "context": 0, "vision": True, "document_reader": True,
         "used_for": "Reads every page <second reader>"},
        {"model": "text-embedding-qwen3-embedding-0.6b", "loaded": False, "context": 0, "vision": False,
         "document_reader": False, "used_for": "", "embedding": True},
    ]  # fmt: skip
    monkeypatch.setattr(page_reader, "lm_studio_models", lambda settings, use_cache=True: models)


# --- Every tab ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("tab", TABS)
def test_every_tab_renders_with_lm_studio_down(client, tab):
    response = client.get(f"/settings/{tab}")
    assert response.status_code == 200, response.text
    text = response.text
    assert 'aria-current="page">' in text and "/settings/automation" in text  # the tab bar
    assert "Traceback" not in text


def test_settings_opens_on_reading_and_says_lm_studio_is_down(client):
    text = html.unescape(client.get("/settings").text)
    assert "How AP Coder reads every invoice" in text and "The page's own words." in text
    assert "PDF text layer" in text  # the readers table, from the real reading_status
    assert "No self-test: LM Studio isn't running" in text and "To start LM Studio on this computer" in text
    assert "LM Studio isn't answering" in text  # Models in LM Studio
    assert "On a new computer" in text
    assert 'id="self-test"' in text and "Run the self-test now" in text  # never run: not "again"
    assert "Run the self-test again" not in text
    # The button is disabled: there is no OvisOCR2 to test.
    button = text.split('action="/settings/reading/self-test"', 1)[1].split("</button>", 1)[0]
    assert " disabled" in button
    assert client.get("/settings/nope").status_code == 404


def test_reading_tab_with_ovisocr2_found(client, ovis_found):
    text = client.get("/settings/reading").text
    assert "Found in LM Studio" in text and MODEL in text
    assert "Self-test passed" in text and "read 9 of 9 fields right" in text
    assert "3 invoices waiting" in text and "about 12 minutes" in text
    assert "97% of figures" in text
    button = text.split('action="/settings/reading/self-test"', 1)[1].split("</button>", 1)[0]
    assert " disabled" not in button
    # Models in LM Studio: both listed, the one not loaded offered to Load.
    assert "qwen3.5-9b" in text and "yes, 8,192 tokens" in text and "document reader" in text
    load = text.split('action="/settings/reading/load-model"', 1)[1].split("</form>", 1)[0]
    assert f'<option value="{MODEL}">' in load and "qwen3.5-9b" not in load
    # The embedding model is listed as one AP Coder doesn't use, and never offered to load.
    row = text.split("<b>text-embedding-qwen3-embedding-0.6b</b>", 1)[1].split("</tr>", 1)[0]
    assert "not used (an embedding model)" in row
    assert "embedding" not in load
    # Text from LM Studio and the readers is escaped.
    assert "&lt;b&gt;run the launcher&lt;/b&gt;" in text and "<b>run the launcher</b>" not in text
    assert "&lt;second reader&gt;" in text


def test_reading_tab_when_the_status_cannot_be_worked_out(client, monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(reading, "reading_status", broken)
    text = html.unescape(client.get("/settings/reading").text)
    assert "couldn't check the readers just now" in text
    assert "Models in LM Studio" in text


def test_reading_tab_leaves_a_slot_for_the_azure_check(client):
    template = (settings_page.__file__.rsplit("pages", 1)[0]) + "templates/settings_reading.html"
    with open(template, encoding="utf-8") as f:
        assert '{% include "partials/azure_check.html" ignore missing %}' in f.read()
    assert client.get("/settings/reading").status_code == 200  # renders without the partial


def test_check_again_asks_lm_studio_afresh(client, monkeypatch):
    forgot = []
    monkeypatch.setattr(page_reader, "forget_status", lambda: forgot.append(True))
    text = follow(client, post(client, "/settings/reading/check"))
    assert forgot and "Checked again just now." in text


# --- The self-test and Load ---------------------------------------------------------------------------------------


def test_run_the_self_test_again_starts_it_in_the_background(client, store, ovis_found, monkeypatch):
    calls = []
    monkeypatch.setattr(page_worker, "start_test", lambda settings, st, model: calls.append((st.path, model)) or True)
    text = follow(client, post(client, "/settings/reading/self-test"))
    assert calls == [(store.path, MODEL)]
    assert f"Self-test of {MODEL} started in the background" in text

    monkeypatch.setattr(page_worker, "start_test", lambda settings, st, model: False)
    answer = post(client, "/settings/reading/self-test").json()
    assert answer["ok"] and "already running" in answer["toast"]["text"]


def test_no_self_test_without_ovisocr2(client, monkeypatch):
    monkeypatch.setattr(page_worker, "start_test", lambda *a: pytest.fail("nothing to test"))
    response = post(client, "/settings/reading/self-test")
    assert response.status_code == 400 and "isn't in LM Studio" in response.json()["error"]


def test_the_page_polls_the_self_test_under_way(client, store, ovis_found, monkeypatch):
    assert client.get("/settings/reading/self-test").json() == {"state": "idle"}
    testing = {"model": MODEL, "started": "2026-10-11T09:41:00", "done": 1, "pages": 2}
    monkeypatch.setattr(page_worker, "test_under_way", lambda st: testing)
    snap = client.get("/settings/reading/self-test").json()
    assert snap == {"state": "running", "model": MODEL, "started": "09:41", "done": 1, "pages": 2}
    text = client.get("/settings/reading").text
    assert 'data-state="running"' in text and "1 of 2 pages read" in text
    button = text.split('action="/settings/reading/self-test"', 1)[1].split("</button>", 1)[0]
    assert " disabled" in button  # one test at a time


def test_load_calls_the_page_readers_load_model(client, monkeypatch):
    calls = []
    monkeypatch.setattr(page_reader, "load_model", lambda settings, model: calls.append(model) or "")
    text = follow(client, post(client, "/settings/reading/load-model", {"model": MODEL}))
    assert calls == [MODEL] and f"{MODEL} is loaded." in text

    monkeypatch.setattr(page_reader, "load_model", lambda settings, model: "LM Studio says: not enough memory")
    response = post(client, "/settings/reading/load-model", {"model": MODEL})
    assert response.status_code == 400 and response.json()["error"] == "LM Studio says: not enough memory"
    assert post(client, "/settings/reading/load-model", {"model": " "}).status_code == 400


def test_a_post_without_the_page_header_is_refused(client, monkeypatch):
    monkeypatch.setattr(page_reader, "load_model", lambda *a: pytest.fail("must not load"))
    assert client.post("/settings/reading/load-model", data={"model": MODEL}).status_code == 403


# --- Developer build: Azure ---------------------------------------------------------------------------------------


def test_cloud_services_only_in_a_developer_build(client, env_file, monkeypatch):
    assert "Developer: cloud services" not in client.get("/settings/reading").text
    data = {"di_endpoint": "https://x.cognitiveservices.azure.com/", "di_key": "secret-key-1234"}
    assert post(client, "/settings/reading/azure", data).status_code == 403
    assert not env_file.exists()

    monkeypatch.setenv("AP_ALLOW_INTERNET", "1")
    text = client.get("/settings/reading").text
    assert "Developer: cloud services" in text and "Save Azure settings" in text
    follow(client, post(client, "/settings/reading/azure", data))
    env = read_env(env_file)
    assert env["AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT"].startswith("https://x.cognitiveservices.azure.com")
    assert env["AZURE_DOCUMENT_INTELLIGENCE_KEY"] == "secret-key-1234"
    text = client.get("/settings/reading").text
    assert "secret-key-1234" not in text and "ending …1234" in text  # a key shows only its last 4 characters


# --- Automation ---------------------------------------------------------------------------------------------------


def test_automation_tab_shows_the_switch_the_bar_and_the_numbers(client):
    text = client.get("/settings/automation").text
    assert "Off: every invoice is reviewed by a person" in text
    assert "Turn on touchless processing…" in text and "I understand" in text
    assert "The bar every vendor must meet" in text and "Always a person" in text
    assert "Vendors touchless" in text and "Touchless, last 30 days" in text
    assert "Largest invoice approved without a person" in text


def test_touchless_needs_i_understand_and_is_logged(client, store):
    response = post(client, "/settings/automation/touchless", {"turn": "on"})
    assert response.status_code == 400 and "I understand" in response.json()["error"]
    assert not store.touchless_enabled()

    text = follow(client, post(client, "/settings/automation/touchless", {"turn": "on", "sure": "1"}))
    assert store.touchless_enabled()
    assert "Touchless processing is on" in text and "On: a vendor that meets the bar" in text
    events = store.events(actions=["touchless_on"])
    assert len(events) == 1 and events[0]["actor"]

    response = post(client, "/settings/automation/touchless", {"turn": "off"})
    assert response.status_code == 400 and store.touchless_enabled()  # turning off needs it ticked too
    text = follow(client, post(client, "/settings/automation/touchless", {"turn": "off", "sure": "1"}))
    assert not store.touchless_enabled() and "every invoice is reviewed" in text
    assert len(store.events(actions=["touchless_off"])) == 1


def test_touchless_limit_is_saved_and_checked(client, store):
    text = follow(client, post(client, "/settings/automation/limit", {"limit": "7500"}))
    assert store.touchless_limit() == 7500.0 and "Saved: 7,500.00 CAD." in text
    assert "Nothing changed." in follow(client, post(client, "/settings/automation/limit", {"limit": "7500.00"}))
    for bad in ("0", "-5", "lots"):
        assert post(client, "/settings/automation/limit", {"limit": bad}).status_code == 400
    assert store.touchless_limit() == 7500.0


# --- Review -------------------------------------------------------------------------------------------------------


def test_review_settings_are_written_to_the_env_and_logged(client, store, env_file):
    text = client.get("/settings/review").text
    assert "Review and AI behaviour" in text and 'name="threshold"' in text
    data = {"reviewer_name": "Dana Clerk", "threshold": "90"}  # constrain unticked
    text = follow(client, post(client, "/settings/review", data))
    assert "Saved 3 changes." in text
    env = read_env(env_file)
    assert env["AP_REVIEW_THRESHOLD"] == "0.90" and env["AP_CONSTRAIN_CODES"] == "false"
    assert os.environ["AP_REVIEW_THRESHOLD"] == "0.90"  # applied to the running dashboard
    assert paths.read_user_settings()["reviewer"] == "Dana Clerk"
    events = store.events(actions=["settings_changed"])
    assert any(e["detail"].get("reviewer", {}).get("to") == "Dana Clerk" for e in events)
    assert any("AP_REVIEW_THRESHOLD" in e["detail"].get("keys", []) for e in events)
    assert post(client, "/settings/review", {"threshold": "40"}).status_code == 400


def test_approval_settings_are_saved(client, store):
    data = {"approval_limit": "25000", "terms_days": "45", "fx_rates": "USD=1.37"}
    text = follow(client, post(client, "/settings/review/approval", data))
    assert "Saved." in text
    assert store.approval_limit() == 25000.0 and store.default_terms_days() == 45
    assert store.get_setting("fx_rates") == "USD=1.37"
    assert "Nothing changed." in follow(client, post(client, "/settings/review/approval", data))
    assert post(client, "/settings/review/approval", {"terms_days": "400"}).status_code == 400
    assert post(client, "/settings/review/approval", {"approval_limit": "-1"}).status_code == 400


# --- JD Edwards E1 ------------------------------------------------------------------------------------------------


def general_form(**changes) -> dict[str, str]:
    js = jde.JdeSettings()
    form = {
        "company": js.company, "user": js.user, "batch_prefix": js.batch_prefix, "document_type": js.document_type,
        "domestic_currency": js.domestic_currency, "currencies": "CAD, USD", "amount_mode": js.amount_mode,
        "decimals": js.decimals, "account_mode": js.account_mode, "line_numbering": js.line_numbering,
        "gl_date": js.gl_date, "use_default_rule": "1", "bu_from_cost_center": "1", "send_due_date": "1",
        "send_po_reference": "1", "pay_status": "A", "po_document_type": "OP", "self_assessed_code": "B",
        "exempt_code": "E", "extras": "VLATFLG=, VLPSTE=",
    }  # fmt: skip
    form.update(changes)
    return form


def test_jde_tab_shows_every_part(client):
    text = client.get("/settings/jde").text
    for part in ("JD Edwards E1 vouchers", "GL accounts → BU.Object.Subsidiary", "Province → tax area",
                 "Supplier address numbers (AN8)", "Before the first live load"):  # fmt: skip
        assert part in text
    assert "<b>Company numbers</b>" in text  # the checklist's own Markdown


def test_jde_general_settings_are_saved_and_checked(client, store):
    form = general_form(company="42", po_matched="1", currencies="cad;eur")
    text = follow(client, post(client, "/settings/jde", form))
    assert "JD Edwards settings saved." in text
    js = jde.load(store)
    assert js.company == "42" and js.po_matched and js.currencies == ["CAD", "EUR"]
    assert not js.dist_tax_columns  # unticked
    response = post(client, "/settings/jde", general_form(company="ABC"))
    assert response.status_code == 400 and "Company (VLCO)" in response.json()["error"]
    assert jde.load(store).company == "42"
    assert post(client, "/settings/jde", general_form(amount_mode="both")).status_code == 400


def test_jde_gl_mapping_tax_areas_and_address_numbers(client, store):
    rows = {"gl_code": ["6100", "", "6200"], "cost_center": ["", "", "OPS"], "bu": ["100", "", "200"],
            "obj": ["6100", "", ""], "sub": ["", "", ""], "sbl": ["", "", ""], "sblt": ["", "", ""]}  # fmt: skip
    text = follow(client, post(client, "/settings/jde/gl-map", rows))
    assert "2 mapping rows saved." in text  # the row without a GL code is dropped
    assert [(m.gl_code, m.cost_center, m.bu) for m in jde.load(store).gl_map] == [
        ("6100", "", "100"),
        ("6200", "OPS", "200"),
    ]
    csv = b"GL code,Cost center,BU,Object\n6100,,999,6100\n7000,,300,7000\n"
    response = client.post("/settings/jde/gl-map/import", files={"file": ("map.csv", io.BytesIO(csv), "text/csv")},
                           headers=PAGE)  # fmt: skip
    assert "2 mapping rows imported." in follow(client, response)
    assert {m.gl_code: m.bu for m in jde.load(store).gl_map} == {"6100": "999", "6200": "200", "7000": "300"}
    bad = client.post("/settings/jde/gl-map/import", files={"file": ("map.txt", io.BytesIO(b"x"), "text/plain")},
                      headers=PAGE)  # fmt: skip
    assert bad.status_code == 400

    tax_map = jde.load(store).tax_map
    provinces = list(tax_map)
    areas = ["on-hst-x" if p == "ON" else tax_map[p].tax_area for p in provinces]
    codes = [tax_map[p].code for p in provinces]
    table = {"province": provinces, "tax_area": areas, "code": codes}
    text = follow(client, post(client, "/settings/jde/tax-map", table))
    assert "Tax areas saved." in text and jde.load(store).tax_map["ON"].tax_area == "ON-HST-X"
    codes[0] = "Z"
    assert post(client, "/settings/jde/tax-map", table).status_code == 400

    text = follow(client, post(client, "/settings/jde/an8", {"vendor": ["Northwind Supply", ""], "an8": ["4100", "9"]}))
    assert "Address numbers saved." in text
    assert list(jde.load(store).an8_overrides.values()) == ["4100"]
    assert 'value="4100"' in client.get("/settings/jde").text


# --- Data & backups -----------------------------------------------------------------------------------------------


def test_data_tab_backup_now_and_download(client, store):
    text = client.get("/settings/data").text
    assert "Data folder" in text and "No backups yet" in text
    text = follow(client, post(client, "/settings/data/backup"))
    backups = store.list_backups()
    assert len(backups) == 1 and f"Backed up to {backups[0].name}." in text
    assert backups[0].name in text and "Restore a backup…" in text
    download = client.get(f"/settings/data/backups/{backups[0].name}")
    assert download.status_code == 200 and download.content == backups[0].read_bytes()
    assert client.get("/settings/data/backups/..%2Fap_coder.db").status_code == 404
    assert client.get("/settings/data/backups/nothing.db").status_code == 404


def test_restore_needs_restore_typed(client, store):
    backup = store.backup_now("manual")
    store.set_setting("fx_rates", "USD=9.99")  # done after the backup: undone by the restore
    for typed in ("", "restor", "yes"):
        response = post(client, "/settings/data/restore", {"name": backup.name, "confirm": typed})
        assert response.status_code == 400 and "Type RESTORE" in response.json()["error"]
    assert store.get_setting("fx_rates") == "USD=9.99"  # nothing restored
    assert post(client, "/settings/data/restore", {"name": "other.db", "confirm": "RESTORE"}).status_code == 404

    text = follow(client, post(client, "/settings/data/restore", {"name": backup.name, "confirm": " restore "}))
    assert f"Restored {backup.name}." in text and "before-restore" in text
    assert store.get_setting("fx_rates") == ""
    assert store.events(actions=["backup_restored"])


def test_second_backup_folder(client, store, tmp_path):
    response = post(client, "/settings/data/backup-copy", {"folder": str(tmp_path / "missing")})
    assert response.status_code == 400 and "does not exist" in response.json()["error"]
    store.backup_dir().mkdir(parents=True, exist_ok=True)
    response = post(client, "/settings/data/backup-copy", {"folder": str(store.backup_dir())})
    assert response.status_code == 400 and "local backups folder" in response.json()["error"]
    other = tmp_path / "onedrive"
    other.mkdir()
    text = follow(client, post(client, "/settings/data/backup-copy", {"folder": f'"{other}"'}))
    assert store.get_setting("backup_copy_dir") == str(other) and "copied there too" in text
    assert list(other.glob("ap_coder-*.db"))  # a first copy straight away


# --- About --------------------------------------------------------------------------------------------------------


def test_about_tab(client, env_file):
    from ap_coder import __version__

    text = client.get("/settings/about").text
    assert "About this installation" in text and __version__ in text
    assert str(env_file) in text and "FastAPI" in text


def test_the_settings_page_writes_the_env_ap_coder_reads(env_file):
    """The web page writes the .env AP Coder reads (AP_ENV_FILE, else the data folder's)."""
    assert settings_page.env_path() == env_file
    assert json.dumps(sorted(settings_page.TAB_KEYS)) == json.dumps(sorted(TABS))
