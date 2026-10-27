"""Help in the web dashboard: every check of the catalog with what it means and what to do, the search and the area
filter, the questions, getting started with links to the pages, and What's new from docs/WHATS_NEW.md."""

from __future__ import annotations

import html
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from ap_coder.core.config import Settings
from ap_coder.core.store import Store
from ap_coder.ops.help import AREAS, CHECKS, FAQ, QUICK_START, TAX
from ap_coder.web import minimd
from ap_coder.web.app import create_app
from ap_coder.web.pages.help import matching_checks


@pytest.fixture
def client(tmp_path):
    s = Settings()
    store = Store(tmp_path / "private" / "ap_coder.db")
    return TestClient(create_app(replace(s, llm=replace(s.llm, provider="off")), store), base_url="http://localhost")


def page(client, url="/help"):
    response = client.get(url)
    assert response.status_code == 200, response.text
    return response.text


def test_help_lists_every_check_code_with_its_meaning_and_action(client, monkeypatch):
    monkeypatch.setenv("AP_ALLOW_INTERNET", "1")  # (offline, the Azure check's own checks are left out: B-514)
    text = page(client)
    plain = html.unescape(text)
    for code, about in CHECKS.items():
        assert f'id="check-{code}"' in text, code
        assert about.title in plain and about.meaning in plain and about.action in plain, code
    assert f"{len(CHECKS)} of {len(CHECKS)} checks" in text


def test_help_shows_getting_started_questions_and_where_the_data_is(client):
    plain = html.unescape(page(client))
    for title, _, path in QUICK_START:
        assert title in plain and f'href="{path}"' in plain
    assert 'href="/accounts"' in plain and 'href="/exports"' in plain  # each step opens its page
    for question, answer in FAQ:
        assert question in plain and answer in plain
    assert "How every invoice is read" in plain and "OvisOCR2 reads every page too," in plain
    assert "Your AP routine" in plain and "Every month" in plain
    assert "Shortcuts" in plain and "apc-kbd" in plain
    assert "Where your data is" in plain and "ap_coder.db" in plain  # (folded under For IT)
    assert "What's new for testers" in plain and "What's new in AP Coder" not in plain  # the changelog: developers


def test_the_search_and_the_area_filter_the_checks(client):
    text = page(client, "/help?q=duplicate")
    assert 'id="check-DUPLICATE_INVOICE"' in text and 'id="check-TAX_CALC_MISMATCH"' not in text
    text = page(client, f"/help?area={TAX}")
    assert 'id="check-TAX_CALC_MISMATCH"' in text and 'id="check-DUPLICATE_INVOICE"' not in text

    text = page(client, "/help?q=no-such-thing-anywhere")
    assert "No check matches" in text and "<tr data-text" not in text
    assert 'id="check-DUPLICATE_INVOICE"' in page(client, "/help?area=Nowhere")  # an unknown area: all areas
    assert {code for code, _ in matching_checks("qst", None)} >= {"QST_NUMBER_MISSING", "QST_ON_GST_INCLUSIVE"}
    assert all(about.area in AREAS for _, about in matching_checks())


def test_the_search_text_is_escaped(client):
    text = page(client, "/help?q=%3Cscript%3Ealert(1)%3C/script%3E")
    assert "<script>alert(1)</script>" not in text and "&lt;script&gt;" in text


def test_minimd_escapes_and_formats():
    text = "# Title\n\nSome **bold** and `code` <b>x</b> [a link](DOC.md).\n\n- one\n  goes on\n- two"
    out = str(minimd.render(text))
    assert "<h4>Title</h4>" in out and "<b>bold</b>" in out and "<code>code</code>" in out
    assert "&lt;b&gt;x&lt;/b&gt;" in out and "a link" in out and "DOC.md" not in out
    assert "<ul><li>one goes on</li><li>two</li></ul>" in out


def test_the_shortcuts_list_has_the_page_viewers_keys_worded_as_on_the_review_screen(client, tmp_path):
    from ap_coder.core.config import Settings as _Settings
    from ap_coder.ops.demo import load_demo
    from ap_coder.web.pages.help import SHORTCUT_ROWS, shortcut_keys

    assert shortcut_keys("[Ctrl] + wheel") == [{"key": True, "text": "Ctrl"}, {"key": False, "text": "+ wheel"}]
    assert shortcut_keys("[H] or hold [Space]") == [{"key": True, "text": "H"}, {"key": False, "text": "or hold"},
                                                    {"key": True, "text": "Space"}]  # fmt: skip
    help_rows = html.unescape(page(client).split("<h4>Shortcuts</h4>", 1)[1].split("</table>", 1)[0])
    store = Store(tmp_path / "demo" / "ap_coder.db")
    load_demo(store, _Settings())
    review = TestClient(create_app(_Settings(), store, start_reader=False), base_url="http://localhost")
    inv = store.list_invoices("review")[0]
    review_rows = html.unescape(
        review.get(f"/review/{inv['id']}").text.split("Shortcuts", 1)[1].split("</table>", 1)[0]
    )
    viewer = SHORTCUT_ROWS[4:]  # after the four invoice keys: the viewer's, then the menus, Undo, Park, Reject
    assert len(viewer) == 11  # with Alt+PgUp/PgDn and the line keys (D-76)
    for spec, what in viewer:
        for rows in (help_rows, review_rows):
            assert what in rows, (what, rows)
            for part in shortcut_keys(spec):
                assert part["text"] in rows, (part, rows)
    assert help_rows.count("apc-kbd") >= 4 * 2 + 9  # the four invoice keys, and the viewer's nine
