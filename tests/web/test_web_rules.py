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


def _undo(client, change_id: int, **more: str):
    """An entry's Undo pressed on the page as it is now."""
    return post(client, "/rules/undo", {"change": str(change_id), "version": _version(client), **more})


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
    page = follow(client, _undo(client, change.id))
    assert store.coding_rules()[0] == courier
    assert f"Undone. {who} changed ‘Courier’: GL account {second} → {first}." in unescape(page)
    assert _history(page)[0] == f"{who} changed ‘Courier’: GL account {second} → {first} (an undo)"
    # the entry undone has nothing to undo any more; the undo itself can be undone
    assert "nothing to undo" in _entry(page, change.id) and 'action="/rules/undo"' not in _entry(page, change.id)
    undo = store.coding_rule_history(limit=1)[0]
    follow(client, _undo(client, undo.id))
    assert store.coding_rules()[0].gl_code == second
    assert _undo(client, 999).status_code == 404


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
    answer = _undo(client, older.id)
    assert answer.status_code == 409 and "this rule changed again since that entry" in answer.json()["error"]
    assert store.coding_rules()[0].gl_code == "7360"
    page = follow(client, _undo(client, older.id, overwrite="1"))
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
    page = follow(client, _undo(client, removal.id))
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
    answer = _undo(client, change.id)
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
    undo = {"change": str(added.id), "version": str(store.rules_seen())}
    assert client.post("/rules/undo", data=undo, headers=lee).json()["ok"]
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


# --- Two people at once, a rule changed into another, an undo that would double a rule (B-2292 to B-2294) ------------


def _post_rows(client, rows: list[dict[str, str]], version: str):
    names = ("id", "vendor", "contains", "gl_code")
    return post(client, "/rules", {**{n: [str(r.get(n, "")) for r in rows] for n in names}, "version": version})


def _form_rows(page: str) -> tuple[list[dict[str, str]], str]:
    """(the rows of the page's form that say something: id, vendor, gl_code; the stamp it would post)."""
    table = page.split('id="rules"', 1)[1].split("</table>", 1)[0]
    rows = []
    for row in table.split("<tr")[2:]:
        found = {"id": re.search(r'name="id" value="(\d*)"', row).group(1),
                 "vendor": re.search(r'name="vendor" value="([^"]*)"', row).group(1),
                 "gl_code": (re.search(r'<option value="([^"]+)" selected>', row) or [None, ""])[1]}  # fmt: skip
        if any(found.values()):
            rows.append(found)
    return rows, re.search(r'name="version" value="([^"]+)"', page).group(1)


def test_two_people_saving_the_rules_at_the_same_moment_neither_removes_the_others_rule(client, store, monkeypatch):
    """B-2292: both saves passed the page's check before either wrote; the second then removed the rule the first had
    just added, and both were told "saved"."""
    import threading

    from ap_coder.web.pages import rules as page
    from tests.webclient import client_for

    load_sample_setup(store, cost_centers=False)
    first, second = (a["code"] for a in store.list_accounts("gl_accounts")[:2])
    store.save_coding_rules([Rule("Courier", "", first)], actor="Dana")
    (courier,) = store.coding_rules()
    version = _version(client)
    both, real, answers = threading.Barrier(2, timeout=10), page.rules_from_form, {}

    def read(form, reference=None):
        both.wait()  # the other request is as far: neither has saved yet
        return real(form, reference)

    def send(vendor: str, browser) -> None:
        rows = [{"id": courier.id, "vendor": "Courier", "gl_code": first}, {"vendor": vendor, "gl_code": second}]
        answers[vendor] = _post_rows(browser, rows, version)

    monkeypatch.setattr(page, "rules_from_form", read)
    threads = [threading.Thread(target=send, args=(vendor, client_for(store))) for vendor in ("Freight", "Movers")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    monkeypatch.setattr(page, "rules_from_form", real)
    assert sorted(a.status_code for a in answers.values()) == [200, 409], {k: a.text for k, a in answers.items()}
    (won,) = (v for v, a in answers.items() if a.status_code == 200)
    (lost,) = (v for v, a in answers.items() if a.status_code == 409)
    assert [r.vendor for r in store.coding_rules()] == ["Courier", won]
    assert not any("removed" in words for words in (rules.change_words(c) for c in store.coding_rule_history()))
    refused = answers[lost].json()
    assert not refused["ok"]
    assert "Nothing was saved: the rules were changed since you opened this page" in refused["error"]
    # The page comes back: who changed what, the rules as they are now, and this person's own row still in the form.
    again = unescape(client.get(refused["redirect"]).text)
    assert f"{core.reviewer()} added ‘{won}’: GL account {second}" in again.split('id="rules"', 1)[0]
    rows, stamp = _form_rows(again)
    assert [(r["vendor"], r["gl_code"], bool(r["id"])) for r in rows] == [
        ("Courier", first, True), (won, second, True), (lost, second, False)]  # fmt: skip
    assert [r.vendor for r in store.coding_rules()] == ["Courier", won]  # shown, not saved
    follow(client, _post_rows(client, rows, stamp))
    assert [r.vendor for r in store.coding_rules()] == ["Courier", won, lost]


def test_a_save_made_on_an_older_page_keeps_the_others_changes_and_the_persons_own_in_the_form(client, store):
    load_sample_setup(store, cost_centers=False)
    a, b, c = (x["code"] for x in store.list_accounts("gl_accounts")[:3])
    store.save_coding_rules([Rule("Courier", "", a), Rule("Bell", "", a), Rule("Hydro", "", a)], actor="Dana")
    courier, bell, hydro = store.coding_rules()
    version = _version(client)  # this person's page
    # Meanwhile Lee changes Bell, removes Hydro and adds Freight.
    store.save_coding_rules([courier, Rule("Bell", "", b, id=bell.id), Rule("Freight", "", b)], actor="Lee")
    as_lee_left_them = store.coding_rules()
    # This person changed Courier's account and added Movers; Bell and Hydro are as their page showed them.
    mine = [{"id": courier.id, "vendor": "Courier", "gl_code": c}, {"id": bell.id, "vendor": "Bell", "gl_code": a},
            {"id": hydro.id, "vendor": "Hydro", "gl_code": a}, {"vendor": "Movers", "gl_code": c}]  # fmt: skip
    answer = _post_rows(client, mine, version)
    assert answer.status_code == 409 and store.coding_rules() == as_lee_left_them
    page = unescape(client.get(answer.json()["redirect"]).text)
    said = page.split('id="rules"', 1)[0]
    assert "Nothing was saved: the rules were changed since you opened this page." in said
    assert "Your own changes are still in the form" in said
    for words in (f"Lee changed ‘Bell’: GL account {a} → {b}", f"Lee removed ‘Hydro’ (GL account {a})",
                  f"Lee added ‘Freight’: GL account {b}"):  # fmt: skip
        assert words in said, words
    rows, stamp = _form_rows(page)
    assert [(r["vendor"], r["gl_code"]) for r in rows] == [("Courier", c), ("Bell", b), ("Freight", b), ("Movers", c)]
    follow(client, _post_rows(client, rows, stamp))
    assert [(r.vendor, r.gl_code) for r in store.coding_rules()] == [("Courier", c), ("Bell", b), ("Freight", b),
                                                                     ("Movers", c)]  # fmt: skip
    assert store.coding_rules()[0].id == courier.id and store.coding_rules()[1].id == bell.id
    # A row this person cleared stays cleared in the form; a save that names no page is not made at all.
    rows, version = _form_rows(client.get("/rules").text)
    store.save_coding_rules([*store.coding_rules(), Rule("Parcel", "", a)], actor="Lee")
    cleared = [{"id": courier.id}, *rows[1:]]
    back = unescape(client.get(_post_rows(client, cleared, version).json()["redirect"]).text)
    assert [r["vendor"] for r in _form_rows(back)[0]] == ["", "Bell", "Freight", "Movers", "Parcel"]
    assert _form_rows(back)[0][0]["id"] == str(courier.id) and len(store.coding_rules()) == 5
    names = ("id", "vendor", "contains", "gl_code")
    blind = post(client, "/rules", {n: [str(r.get(n, "")) for r in rows[:1]] for n in names})
    assert blind.status_code == 409 and "Open the rules page again" in blind.json()["error"]
    assert len(store.coding_rules()) == 5


def test_an_undo_asked_on_an_older_page_is_not_done_and_says_who_changed_what(client, store):
    load_sample_setup(store, cost_centers=False)
    a, b = (x["code"] for x in store.list_accounts("gl_accounts")[:2])
    store.save_coding_rules([Rule("Courier", "", a)], actor="Dana")
    added = store.coding_rule_history()[0]
    version = _version(client)
    store.save_coding_rules([*store.coding_rules(), Rule("Freight", "", b)], actor="Lee")
    answer = post(client, "/rules/undo", {"change": str(added.id), "version": version})
    assert answer.status_code == 409 and len(store.coding_rules()) == 2
    assert "Not undone: the rules were changed since you opened this page" in answer.json()["error"]
    page = unescape(client.get(answer.json()["redirect"]).text)
    assert f"Lee added ‘Freight’: GL account {b}" in page.split('id="rules"', 1)[0]
    blind = post(client, "/rules/undo", {"change": str(added.id)})
    assert blind.status_code == 409 and len(store.coding_rules()) == 2
    follow(client, _undo(client, added.id))  # asked again on the page as it is now
    assert [r.vendor for r in store.coding_rules()] == ["Freight"]


def test_an_undo_that_would_make_a_rule_say_the_same_as_another_is_refused_in_plain_words(client, store):
    """B-2294."""
    load_sample_setup(store, cost_centers=False)
    a, b = (x["code"] for x in store.list_accounts("gl_accounts")[:2])
    follow(client, _save(client, [{"vendor": "Courier", "gl_code": a}]))
    (courier,) = store.coding_rules()
    follow(client, _save(client, [{"id": str(courier.id), "vendor": "Courier", "gl_code": b}]))
    change = store.coding_rule_history()[0]
    follow(client, _save(client, [{"id": str(courier.id), "vendor": "Courier", "gl_code": b},
                                  {"vendor": "Courier", "gl_code": a}]))  # fmt: skip
    before = store.coding_rules()
    answer = _undo(client, change.id)
    assert answer.status_code == 409 and store.coding_rules() == before
    assert answer.json()["error"] == (f"Not undone: the rule would then say ‘Courier’: GL account {a}, which another "
                                      "rule says already. Change or remove that rule first, then undo.")  # fmt: skip
    assert "An undo that would make a rule say the same as another is not done" in client.get("/rules").text


def test_a_rule_changed_into_another_says_what_it_coded_as_it_is_now_and_what_before(client, store):
    """B-2293: rule 1, changed in place to another vendor and account, still said "2 lines on 2 invoices" and the
    search named the new rule over the old rule's invoices."""
    load_sample_setup(store, cost_centers=False)
    store.save_coding_rules([Rule("", "delivery", "6900")], actor="Dana")
    (rule,) = store.coding_rules()
    folder = store.path.parent

    def coded(name: str) -> int:
        doc, _ = rules.apply(InvoiceCoding.model_validate(gt()), store.coding_rules())
        return store.add_invoice(folder / f"{name}.pdf", doc.to_output(), {})

    old = [coded(n) for n in "ab"]
    store.save_coding_rules([Rule("Someone Else", "", "6010", id=rule.id)], actor="Lee")
    cell = unescape(client.get("/rules").text).split(f'name="id" value="{rule.id}"', 1)[1].split("</tr>", 1)[0]
    cell = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", cell))
    assert "None yet as it is now" in cell and "2 lines on 2 invoices before it was changed (today at " in cell
    assert "; it was ‘any vendor, line contains “delivery”’: GL account 6900)" in cell
    page = client.get("/rules").text
    assert f'href="/search?rule={rule.id}&amp;was=1#directory"' in page
    assert f'href="/search?rule={rule.id}#directory"' not in page  # nothing as it is now: no link that finds nothing
    now = unescape(client.get(f"/search?rule={rule.id}").text)
    assert "Only the invoices with a line coded by the rule ‘Someone Else’ as it is now." in now
    assert "The 2 lines it coded before it was changed" in now and not any(f'href="/review/{i}"' in now for i in old)
    was = unescape(client.get(f"/search?rule={rule.id}&was=1").text)
    assert ("Only the invoices with a line coded by the rule ‘Someone Else’ before it was changed, when it said "
            "something else.") in was  # fmt: skip
    assert all(f'href="/review/{i}"' in was for i in old) and '<input type="hidden" name="was" value="1">' in was
    assert "The lines it coded as it is now" in was
    # Changed back: the lines are what it codes now again, and nothing is "before".
    store.save_coding_rules([Rule("", "delivery", "6900", id=rule.id)], actor="Lee")
    page = client.get("/rules").text
    assert "2 lines on 2 invoices</a>" in page and "before it was changed" not in page and "as it is now" not in page
