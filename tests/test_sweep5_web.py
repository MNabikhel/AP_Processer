"""Sweep 5 of the dashboard's Python side (ap_coder/web): each bug found reading it, with the test that keeps it
fixed (B-1400 on)."""

from __future__ import annotations

import pytest

from ap_coder.core.store import Store, load_sample_setup
from ap_coder.web import files
from ap_coder.web.pages import learning, settings_jde

from .webclient import PAGE, client_for, follow

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
