"""Sweep 5 of the dashboard's Python side (ap_coder/web): each bug found reading it, with the test that keeps it
fixed (B-1400 on)."""

from __future__ import annotations

import shutil

import pytest
from fastapi.testclient import TestClient

from ap_coder.core.store import Store, load_sample_setup
from ap_coder.web import files
from ap_coder.web.app import create_app
from ap_coder.web.pages import learning, settings_jde

from .conftest import SAMPLE_STEM, SAMPLES
from .webclient import PAGE, add_clean, client_for, follow, gt, post, web_settings

# French-Canadian Excel saves a CSV with semicolons; a header cell may hold a comma.
SEMICOLON_HISTORY = (
    "Fournisseur;Description;Compte GL;Montant (avant taxes, $)\nAcme;Gants;6100;12,50\nBeta;Papier;6200;3,00\n"
)
SEMICOLON_GL_MAP = "GL code;Cost center;BU;Object;Note (internal, keep)\n6100;;100;6100;x\n6200;;200;6200;y\n"


@pytest.fixture
def store(tmp_path):
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    return store


def _colleague(app, name: str = "Ana") -> TestClient:
    """A browser on another computer of the office network, its name given on /who."""
    client = TestClient(app, base_url="http://localhost", client=("10.0.0.5", 50000), follow_redirects=False)
    assert client.post("/who", data={"name": name}, headers=PAGE).json()["ok"]
    return client


def _gone(store: Store, tmp_path) -> int:
    """An invoice whose file was in a folder of this computer and is no longer there."""
    secret = tmp_path / "Secret Folder" / "Supplier 123.pdf"
    secret.parent.mkdir()
    shutil.copy(SAMPLES / f"{SAMPLE_STEM}.pdf", secret)
    invoice_id = store.add_invoice(secret, {"line_items": []}, {})
    secret.unlink()
    return invoice_id


# --- B-1400: a semicolon CSV whose header has a comma in a cell ------------------------------------------------------


def test_past_coding_from_a_semicolon_csv_with_a_comma_in_a_header_keeps_its_columns(store):
    sheets = learning.read_sheets("lignes.csv", SEMICOLON_HISTORY.encode("utf-8"))
    columns, rows = sheets["CSV"]
    assert columns == ["Fournisseur", "Description", "Compte GL", "Montant (avant taxes, $)"]
    assert rows[0]["Compte GL"] == "6100" and rows[1]["Montant (avant taxes, $)"] == "3,00"
    client = client_for(store)
    answer = client.post("/learning/history/upload", files={"file": ("lignes.csv", SEMICOLON_HISTORY.encode())},
                         headers=PAGE)  # fmt: skip
    assert answer.status_code == 200, answer.text
    page = client.get(answer.json()["redirect"]).text
    assert '<option value="Compte GL"' in page and "Fournisseur;Description" not in page


def test_the_jd_edwards_mapping_from_a_semicolon_csv_with_a_comma_in_a_header_is_imported(store):
    rows = settings_jde.read_sheet("map.csv", SEMICOLON_GL_MAP.encode("utf-8"))
    assert rows[0]["GL code"] == "6100" and rows[1]["BU"] == "200"
    client = client_for(store)
    answer = client.post("/settings/jde/gl-map/import", files={"file": ("map.csv", SEMICOLON_GL_MAP.encode())},
                         headers=PAGE)  # fmt: skip
    assert "2 mapping rows imported." in follow(client, answer)


# --- B-1401: the past coding and the JD Edwards mapping were read whole, however large ------------------------------


def test_past_coding_and_the_jd_edwards_mapping_larger_than_an_upload_are_not_read(store, monkeypatch):
    monkeypatch.setattr(files, "_MAX_BYTES", 20)
    client = client_for(store)
    big = SEMICOLON_HISTORY.encode()
    for url in ("/learning/history/upload", "/settings/jde/gl-map/import"):
        answer = client.post(url, files={"file": ("big.csv", big)}, headers=PAGE)
        assert answer.status_code == 400 and "larger than" in answer.json()["error"], (url, answer.text)


# --- B-1402: a line split on an invoice no longer in the review queue -------------------------------------------------


def test_a_split_posted_after_the_invoice_was_approved_is_refused_and_keeps_no_draft(store):
    client = client_for(store)
    invoice_id = add_clean(store)
    assert client.get(f"/review/{invoice_id}").status_code == 200
    store.approve_invoice(invoice_id, gt(), "Jordan Lee")  # in another tab, meanwhile
    lines = gt()["line_items"]
    data = {
        "split_line": str(lines[0]["line_number"]),
        "part_gl": ["6020", "6800"],
        "part_cc": ["", ""],
        "part_pct": ["60", "40"],
    }  # (the grids as stored: the form posts no rows of its own)
    answer = post(client, f"/review/{invoice_id}/split", data)
    assert answer.status_code == 409 and "no longer in the review queue" in answer.json()["error"]
    assert invoice_id not in client.app.state.invoice_drafts
    assert all(kept[0] != invoice_id for kept in store.invoice_drafts())


# --- B-1403: the words on a page whose file is gone named this computer's folder ------------------------------------


def test_the_words_of_a_missing_file_name_it_without_its_folder_to_a_colleague(store, tmp_path):
    app = create_app(web_settings(), store, address="0.0.0.0", start_reader=False)
    invoice_id = _gone(store, tmp_path)
    answer = _colleague(app).get(f"/review/{invoice_id}/words").json()
    assert not answer["ok"] and answer["words"] == []
    assert "Supplier 123.pdf" in answer["error"] and "Secret Folder" not in answer["error"]


# --- B-1404: a setting another web worker saved never applied in this one ---------------------------------------------


def _saved_elsewhere(env, updates: dict[str, str]) -> None:
    """Another web worker of the server saved Settings: its .env changed, not this process's environment."""
    import os

    from ap_coder.core.envfile import write_env

    write_env(env, updates)
    later = env.stat().st_mtime_ns + 5_000_000_000
    os.utime(env, ns=(later, later))


def test_a_setting_another_web_worker_saved_applies_in_this_one(tmp_path, monkeypatch):
    from ap_coder.core.config import Settings
    from ap_coder.web.app import EnvSettings

    env = tmp_path / ".env"
    env.write_text("AP_REVIEW_THRESHOLD=0.80\nAP_CONSTRAIN_CODES=true\n", encoding="utf-8")
    monkeypatch.setenv("AP_ENV_FILE", str(env))
    for name in ("AP_REVIEW_THRESHOLD", "AP_CONSTRAIN_CODES"):
        monkeypatch.setenv(name, "")  # (put back as it was after the test)
        monkeypatch.delenv(name)
    Settings.from_env()  # as `serve` starts: the .env read into this process's environment
    settings = EnvSettings()
    assert settings().engine.review_threshold == 0.80
    _saved_elsewhere(env, {"AP_REVIEW_THRESHOLD": "0.90", "AP_CONSTRAIN_CODES": "false"})
    now = settings()
    assert now.engine.review_threshold == 0.90 and now.engine.constrain_codes is False


def test_a_setting_given_to_the_process_still_wins_over_the_env_file(tmp_path, monkeypatch):
    from ap_coder.web.app import EnvSettings

    env = tmp_path / ".env"
    env.write_text("AP_REVIEW_THRESHOLD=0.80\n", encoding="utf-8")
    monkeypatch.setenv("AP_ENV_FILE", str(env))
    monkeypatch.setenv("AP_REVIEW_THRESHOLD", "0.70")  # set for the service itself
    settings = EnvSettings()
    assert settings().engine.review_threshold == 0.70
    _saved_elsewhere(env, {"AP_REVIEW_THRESHOLD": "0.90"})
    assert settings().engine.review_threshold == 0.70


# --- B-1405: replacing the ERP register with a file of no usable row emptied it -------------------------------------


def _register(client, sheet: str, **form: str):
    upload = client.post("/exports/register/upload", files={"file": ("erp.csv", sheet.encode())}, headers=PAGE)
    token = upload.json()["redirect"].split("register=")[1].split("#")[0].split("&")[0]
    data = {"file": token, "col_vendor_name": "Supplier", "col_invoice_number": "Doc No", "col_total": "Amount",
            **form}  # fmt: skip
    return post(client, "/exports/register/import", data)


def test_replacing_the_erp_register_with_a_file_of_no_usable_row_keeps_it(store):
    client = client_for(store)
    good = "Supplier,Doc No,Amount\nAcme Ltd,A-1,100.00\nBeta Inc,B-7,55.10\n"
    assert _register(client, good).status_code == 200 and store.erp_register_count() == 2
    unreadable = "Supplier,Doc No,Amount\nAcme Ltd,A-2,n/a\nBeta Inc,B-8,—\n"  # the wrong column picked, say
    answer = _register(client, unreadable, replace="1")
    assert answer.status_code == 400 and "nothing was imported" in answer.json()["error"]
    assert _register(client, "Supplier,Doc No,Amount\n", replace="1").status_code == 400  # no rows at all
    assert store.erp_register_count() == 2  # the duplicate checks still know the bills in the ERP
