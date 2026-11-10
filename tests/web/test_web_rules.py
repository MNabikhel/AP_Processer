"""The coding rules' page (D-217): the rules saved under their numbers, the history of their changes with Undo, the
lines each rule coded with a link to the invoices, the account as one string with a chart of accounts loaded, who may
change them on the company server, and its place in the Simple menu."""

from __future__ import annotations

import re
from html import unescape

from fastapi.testclient import TestClient

from ap_coder.coding import rules
from ap_coder.coding.rules import Rule
from ap_coder.core import chart
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import Store, load_sample_setup
from ap_coder.erp import directory
from ap_coder.web import core
from ap_coder.web.app import create_app
from ap_coder.web.identity import SESSION_COOKIE
from tests.webclient import PAGE, enrol, follow, gt, post, web_settings


def _version(client) -> str:
    return re.search(r'name="version" value="([^"]+)"', client.get("/rules").text).group(1)


def _save(client, rows: list[dict[str, str]], **kwargs):
    """The page's form posted with these rows (a row: id, vendor, contains, and gl_code or account)."""
    names = ("id", "vendor", "contains", "gl_code", "cost_center", "account")
    data = {name: [row.get(name, "") for row in rows] for name in names if any(name in row for row in rows)}
    return post(client, "/rules", {"vendor": [], **data, "version": _version(client)}, **kwargs)


def _history(page: str) -> list[str]:
    """Each entry of the history as the page says it, newest first: its words, then what is under them."""
    section = page.split('id="history"', 1)[1].split("</section>", 1)[0]
    entry = r'<div class="suggestion" data-change="\d+">\s*<div>(.*?)<div class="muted small">'
    found = re.findall(entry, section, re.S)
    return [unescape(words).strip() for words in found]


def _entry(page: str, change_id: int) -> str:
    return page.split(f'data-change="{change_id}"', 1)[1].split('<div class="suggestion"', 1)[0]


def test_a_rule_changed_on_the_page_keeps_its_number_and_the_history_lists_it_with_undo(client, store):
    load_sample_setup(store, cost_centers=False)
    first, second = (a["code"] for a in store.list_accounts("gl_accounts")[:2])
    page = follow(client, _save(client, [{"vendor": "Courier", "gl_code": first}]))
    (courier,) = store.coding_rules()
    assert "1 coding rule saved" in page and f'name="id" value="{courier.id}"' in page
    page = follow(client, _save(client, [{"id": str(courier.id), "vendor": "Courier", "gl_code": second},
                                         {"contains": "freight", "gl_code": first}]))  # fmt: skip
    assert [r.id for r in store.coding_rules()] == [courier.id, courier.id + 1]
    who = core.reviewer()
    assert _history(page) == [
        f"{who} added ‘any vendor, line contains “freight”’: GL account {first}",
        f"{who} changed ‘Courier’: GL account {first} → {second}",
        f"{who} added ‘Courier’: GL account {first}",
    ]
    change = store.coding_rule_history(courier.id)[0]
    drawn = _entry(page, change.id)
    assert f'name="change" value="{change.id}"' in drawn and "data-confirm" not in drawn
    page = follow(client, post(client, "/rules/undo", {"change": str(change.id)}))
    assert store.coding_rules()[0] == courier
    assert f"Undone. {who} changed ‘Courier’: GL account {second} → {first}." in unescape(page)
    assert _history(page)[0] == f"{who} changed ‘Courier’: GL account {second} → {first} (an undo)"
    # the entry undone has nothing to undo any more; the undo itself can be undone
    assert "nothing to undo" in _entry(page, change.id) and 'action="/rules/undo"' not in _entry(page, change.id)
    undo = store.coding_rule_history(limit=1)[0]
    follow(client, post(client, "/rules/undo", {"change": str(undo.id)}))
    assert store.coding_rules()[0].gl_code == second
    assert post(client, "/rules/undo", {"change": "999"}).status_code == 404


def test_undo_says_the_rule_changed_again_since_and_asks_before_it_overwrites_the_newer_change(client, store):
    store.save_coding_rules([Rule("Courier", "", "7340")], actor="Dana")
    (courier,) = store.coding_rules()
    store.save_coding_rules([Rule("Courier", "", "7350", id=courier.id)], actor="Lee")
    older = store.coding_rule_history()[0]
    store.save_coding_rules([Rule("Courier", "", "7360", id=courier.id)], actor="Sam")
    entry = unescape(_entry(client.get("/rules").text, older.id))
    assert "changed again since" in entry and 'name="overwrite" value="1"' in entry
    assert 'data-confirm="This rule changed again since: Sam changed ‘Courier’: GL account 7350 → 7360, today' in entry
    assert "Undo this older change anyway?" in entry and "the newer change is overwritten" in entry
    # a page that did not ask (the rule changed after it was drawn) is refused in plain words: nothing is undone
    answer = post(client, "/rules/undo", {"change": str(older.id)})
    assert answer.status_code == 409 and "this rule changed again since that entry" in answer.json()["error"]
    assert store.coding_rules()[0].gl_code == "7360"
    page = follow(client, post(client, "/rules/undo", {"change": str(older.id), "overwrite": "1"}))
    assert store.coding_rules() == [courier]
    assert _history(page)[0] == f"{core.reviewer()} changed ‘Courier’: GL account 7360 → 7340 (an undo)"


def test_an_undo_of_a_removal_brings_the_rule_back_with_its_number(client, store):
    load_sample_setup(store, cost_centers=False)
    gl = store.list_accounts("gl_accounts")[0]["code"]
    follow(client, _save(client, [{"vendor": "Courier", "gl_code": gl}, {"vendor": "Bell", "gl_code": gl}]))
    courier, bell = store.coding_rules()
    page = follow(client, _save(client, [{"id": str(bell.id), "vendor": "Bell", "gl_code": gl}, {}]))  # its row cleared
    assert store.coding_rules() == [bell] and _history(page)[0].endswith(f"removed ‘Courier’ (GL account {gl})")
    removal = store.coding_rule_history(limit=1)[0]
    page = follow(client, post(client, "/rules/undo", {"change": str(removal.id)}))
    assert store.coding_rules() == [courier, bell]
    assert _history(page)[0].endswith(f"added ‘Courier’: GL account {gl} (an undo)")
    own = _history(client.get(f"/rules?rule={bell.id}").text)  # one rule's own history
    assert own == [words for words in _history(page) if "Bell" in words] and len(own) == 1


def test_each_rule_says_how_many_lines_it_coded_and_links_to_those_invoices_in_the_search(client, store):
    load_sample_setup(store, cost_centers=False)
    store.save_coding_rules([Rule("", "delivery", "6900"), Rule("", "nothing has this", "6010")], actor="Dana")
    delivery, unused = saved = store.coding_rules()
    coded, _ = rules.apply(InvoiceCoding.model_validate(gt()), saved)
    folder = store.path.parent
    with_rule = [store.add_invoice(folder / f"{n}.pdf", coded.to_output(), {}) for n in "ab"]
    without = store.add_invoice(folder / "c.pdf", gt(), {})
    page = client.get("/rules").text
    assert f'<a class="link" href="/search?rule={delivery.id}#directory"' in page and "1 line on 1 invoice" not in page
    assert "2 lines on 2 invoices" in page and f"/search?rule={unused.id}" not in page and "None yet" in page
    assert "Lines are counted since today: an invoice coded before that did not keep which rule coded its lines" in page
    query = directory.Query.from_params({"rule": str(delivery.id)})
    assert sorted(r["id"] for r in directory.run(store, query).rows) == with_rule
    assert f"rule={delivery.id}" in query.query_string() and "rule=" not in directory.Query().query_string()
    assert [r["id"] for r in directory.export_rows(store, query)] == sorted(with_rule, reverse=True)
    found = unescape(client.get(f"/search?rule={delivery.id}").text)
    assert "Only the invoices with a line coded by the rule ‘any vendor, line contains “delivery”’." in found
    assert f'<input type="hidden" name="rule" value="{delivery.id}">' in found  # kept while filtering further
    assert all(f'href="/review/{i}"' in found for i in with_rule) and f'href="/review/{without}"' not in found
    assert "Show every invoice" in found
    assert client.get("/search").text.count('id="by-rule"') == 0
    assert directory.run(store, directory.Query.from_params({"rule": "nope"})).total == 3  # not a number: no filter
    store.save_coding_rules([unused], actor="Dana")  # removed: its invoices are still found, the rule named as it was
    gone = unescape(client.get(f"/search?rule={delivery.id}").text)
    assert "the rule ‘any vendor, line contains “delivery”’ (removed since)" in gone
    assert all(f'href="/review/{i}"' in gone for i in with_rule)


def test_with_a_chart_a_rule_codes_to_one_account_string_and_one_the_chart_refuses_is_not_saved(
    chart_company, chart_company_client
):
    client, store, truth = chart_company_client, chart_company.store, chart_company.truth
    good, other = truth.suggestable()[0], truth.suggestable()[1]
    page = follow(client, _save(client, [{"vendor": "Courier", "account": f"  {good.string} "}]))
    (courier,) = store.coding_rules()
    assert (courier.cost_center, courier.gl_code) == chart.split_account(good.string) and courier.target == good.string
    assert f'name="account" value="{good.string}"' in page and 'name="gl_code"' not in page
    assert unescape(_history(page)[0]).endswith(f"added ‘Courier’: account {good.string}")
    row = {"id": str(courier.id), "vendor": "Courier"}
    main = truth.company.main_business_unit
    moved = next(a for a in truth.accounts if a.object == "1450" and a.subsidiary)
    title = next(a for a in truth.accounts if a.object and a.posting_edit == "N")
    closed = next(u.number for u in truth.business_units if u.status != "ACT")
    refused = {
        f"{main}.{moved.object}.{moved.subsidiary}": "is not in the chart of accounts",
        title.string: "cannot be posted to",
        f"{closed}.{chart.split_account(good.string)[1]}": f"business unit {closed} is not active in JD Edwards",
        chart.split_account(good.string)[1]: "needs the whole account, business unit.object.subsidiary",
        "courier fees": "“courier fees” is not an account",
    }
    for typed, why in refused.items():
        answer = _save(client, [{**row, "account": typed}])
        assert answer.status_code == 400 and "Nothing was saved" in answer.json()["error"], typed
        assert why in answer.json()["error"], (typed, answer.json()["error"])
    assert store.coding_rules() == [courier] and len(store.coding_rule_history()) == 1
    page = follow(client, _save(client, [{**row, "account": other.string}]))
    assert store.coding_rules()[0].target == other.string and store.coding_rules()[0].id == courier.id
    assert unescape(_history(page)[0]).endswith(f"changed ‘Courier’: account {good.string} → {other.string}")


def test_a_rule_from_before_the_chart_is_said_on_its_row_and_does_not_stop_the_others_being_saved(
    chart_company, chart_company_client
):
    client, store, truth = chart_company_client, chart_company.store, chart_company.truth
    good = truth.suggestable()[0]
    code = chart.split_account(good.string)[1]
    store.save_coding_rules([Rule("Old Vendor", "", code)], actor="Dana")  # no business unit: as before the chart
    (old,) = store.coding_rules()
    page = unescape(client.get("/rules").text)
    assert f'name="account" value="{code}"' in page
    assert f"The rule ‘Old Vendor’ needs the whole account, business unit.object.subsidiary: {code} names no" in page
    rows = [{"id": str(old.id), "vendor": "Old Vendor", "account": code}, {"vendor": "Courier", "account": good.string}]
    follow(client, _save(client, rows))
    assert [r.target for r in store.coding_rules()] == [code, good.string]
    # made whole, then the change undone: the rule as it was can no longer be saved, and the undo says why
    follow(client, _save(client, [{**rows[0], "account": good.string}, {**rows[1], "id": str(old.id + 1)}]))
    change = store.coding_rule_history(old.id)[0]
    answer = post(client, "/rules/undo", {"change": str(change.id)})
    assert answer.status_code == 400 and "Not undone" in answer.json()["error"]
    assert "needs the whole account" in answer.json()["error"] and store.coding_rules()[0].target == good.string


def _signed_in(store: Store, username: str, role: str) -> dict[str, str]:
    uid = store.add_user(username, "correct horse battery", role=role, display_name=username.title(), must_change=False)
    enrol(store, uid)
    return {**PAGE, "Cookie": f"{SESSION_COOKIE}={store.start_session(uid)}"}


def test_on_the_company_server_anyone_who_reviews_invoices_changes_the_rules_and_the_history_says_who(store):
    load_sample_setup(store, cost_centers=False)
    gl = store.list_accounts("gl_accounts")[0]["code"]
    app = create_app(web_settings(), store, start_reader=False, server=True)
    client = TestClient(app, base_url="http://localhost", client=("10.0.0.5", 50000), follow_redirects=False)
    dana, lee, rob = (_signed_in(store, name, role) for name, role in (("dana", "clerk"), ("lee", "approver"),
                                                                        ("rob", "readonly")))  # fmt: skip
    version = re.search(r'name="version" value="([^"]+)"', client.get("/rules", headers=dana).text).group(1)
    form = {"id": [""], "vendor": ["Courier"], "contains": [""], "gl_code": [gl], "version": version}
    assert client.post("/rules", data=form, headers=rob).status_code == 403
    assert client.get("/rules", headers=rob).status_code == 403
    assert client.post("/rules", data=form, headers=dana).json()["ok"]
    (courier,) = store.coding_rules()
    assert courier.created_by == "Dana"
    added = store.coding_rule_history()[0]
    assert client.post("/rules/undo", data={"change": str(added.id)}, headers=lee).json()["ok"]
    assert store.coding_rules() == []
    page = unescape(client.get("/rules", headers=lee).text)
    assert _history(page) == [
        f"Lee removed ‘Courier’ (GL account {gl}) (an undo)",
        f"Dana added ‘Courier’: GL account {gl}",
    ]
    assert 'action="/rules/undo"' in page and '<a href="/rules" class="nav-link' in page


def test_the_rules_are_one_click_from_the_simple_menu(client, store):
    store.set_setting(core.SCREENS, core.SIMPLE)
    page = client.get("/review").text
    rail = page.split('<aside class="rail"', 1)[1].split("</aside>", 1)[0]
    main = rail.split('id="nav-setup">Setup<', 1)[0]
    assert re.search(r'<a href="/rules" class="nav-link[^>]*>.*?<span class="label">Rules</span>', main, re.S)
    opened = client.get("/rules")
    assert opened.status_code == 200 and "Hidden in Simple screens" not in opened.text
    assert "<h4>History</h4>" in opened.text and 'aria-current="page"' in opened.text.split('href="/rules"', 1)[1][:200]
    assert client.get("/accounts?tab=rules", follow_redirects=False).headers["location"] == "/rules"  # an older link
