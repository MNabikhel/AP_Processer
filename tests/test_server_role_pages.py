"""What each role sees on the company server (D-98, D-102): every page a role may open, walked as that role, offers no
form, button or link to a route the role is refused. The routes, the pages and who may use them all come from
``web/roles.py``, so the test stays true when a rule or a page changes."""

from __future__ import annotations

import collections
import re
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from ap_coder.core.store import Store, load_sample_setup
from ap_coder.core.store.users import ADMIN, ROLES
from ap_coder.ops.demo import load_demo
from ap_coder.web import roles
from ap_coder.web.app import create_app
from ap_coder.web.core import SHOW_JDE

from .webclient import PAGE, web_settings

GOOD = "correct horse battery"
FILES = (".csv", ".png", ".pdf", ".xlsx", ".zip", ".txt")
NOT_PAGES = {"/login", "/logout", "/who", "/healthz", "/jobs/{name}", "/review/{invoice_id:int}/words",
             "/review/{invoice_id:int}/reader", "/help/report/setup", "/settings/reading/self-test",
             "/process/status"}  # fmt: skip


class Targets(HTMLParser):
    """Each form's action (with its method), each link and each button that posts by script, of a page."""

    def __init__(self) -> None:
        super().__init__()
        self.found: list[tuple[str, str, str]] = []  # (method, address, what)

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "form" and a.get("action"):
            self.found.append(((a.get("method") or "get").upper(), a["action"], "form"))
        elif tag == "a" and a.get("href"):
            self.found.append(("GET", a["href"], "link"))
        if a.get("data-grid-action"):  # the review screen's buttons that post (invoice.js)
            self.found.append(("POST", a["data-grid-action"], "data-grid-action"))
        if a.get("formaction"):
            self.found.append(("POST", a["formaction"], "formaction"))


def targets(html: str) -> list[tuple[str, str, str, str]]:
    """(method, path, what, the address with its query) of each, on AP Coder itself."""
    parser = Targets()
    parser.feed(html)
    found = []
    for method, url, what in parser.found:
        parts = urlsplit(url)
        if parts.scheme or parts.netloc or not parts.path.startswith("/"):
            continue  # another site, a #part of the page, mailto:
        found.append((method, parts.path, what, parts.path + (f"?{parts.query}" if parts.query else "")))
    return found


def _server(store: Store) -> tuple:
    app = create_app(web_settings(), store, start_reader=False, server=True)
    clients = {}
    for role in ROLES:
        uid = store.add_user(f"the.{role}", GOOD, role=role, display_name=f"The {role.title()}")
        with store._conn() as conn:
            conn.execute("UPDATE users SET must_change = 0 WHERE id = ?", (uid,))
        client = TestClient(app, base_url="https://localhost", client=("10.0.0.5", 50000), follow_redirects=False)
        answer = client.post("/login", data={"username": f"the.{role}", "password": GOOD}, headers=PAGE)
        assert answer.status_code in (200, 303), answer.text
        clients[role] = client
    return app, clients


@pytest.fixture(scope="module")
def servers(tmp_path_factory):
    """Two servers: one with the demo invoices, JD Edwards shown and an export batch made; one just set up, empty."""
    full = Store(tmp_path_factory.mktemp("role-pages") / "private" / "ap_coder.db")
    load_sample_setup(full)
    load_demo(full)
    full.set_setting(SHOW_JDE, "1")
    approved = [i["id"] for i in full.list_invoices("approved")]  # a batch: the Exports page shows its card (B-1214)
    full.create_export_batch(approved[:1], "xlsx")
    empty = Store(tmp_path_factory.mktemp("role-pages-empty") / "private" / "ap_coder.db")
    return {"full": (full, *_server(full)), "empty": (empty, *_server(empty))}


def pages_for(role: str, invoice_ids: list[int]) -> list[str]:
    """Every page (GET, HTML) the role may open, from the rules: an invoice's, for each invoice."""
    found = []
    for (method, path), need in roles.RULES.items():
        if method != "GET" or path in NOT_PAGES or path.endswith(FILES) or not roles.allowed(role, need):
            continue
        if "{invoice_id" in path:
            found += [re.sub(r"\{invoice_id[^}]*\}", str(i), path) for i in invoice_ids]
        elif "{" not in path:  # /settings/{tab} and the like: their links are checked on the pages that show them
            found.append(path)
    if roles.allowed(role, roles.RULES[("GET", "/exports")]):
        found += [f"/exports?fmt={fmt}" for fmt in ("jde", "csv", "custom")]
    return sorted(found)


@pytest.mark.parametrize("state", ["full", "empty"])
@pytest.mark.parametrize("role", [r for r in ROLES if r != ADMIN])
def test_no_page_offers_a_role_what_it_is_refused(servers, role, state):
    """Each page the role may open, and the pages its links lead to (tabs, filters: a few of each), as the role sees
    them: every form, link and button that posts points to a route the role may use."""
    store, app, clients = servers[state]
    client = clients[role]
    wrong = []
    todo = pages_for(role, [i["id"] for i in store.list_invoices_full()])
    seen, per_path = set(todo), collections.Counter()
    walked = 0
    while todo:
        url = todo.pop()
        answer = client.get(url, headers={"Accept": "text/html"})
        if answer.status_code in (302, 303, 307):
            continue
        assert answer.status_code == 200, f"{role} {url}: {answer.status_code}"
        if "text/html" not in answer.headers.get("content-type", ""):
            continue
        walked += 1
        for method, path, what, link in targets(answer.text):
            if not roles.can(app, role, method, path):
                wrong.append(f"{url.split('?')[0]}: {what} {method} {path}")
            elif what == "link" and "?" in link and link not in seen and not path.endswith(FILES):
                if path in NOT_PAGES or per_path[path] >= 6:
                    continue
                seen.add(link)
                per_path[path] += 1
                todo.append(link.split("#")[0])
    assert walked > 10
    assert sorted(set(wrong)) == [], "\n".join(sorted(set(wrong)))


# --- The review screen for Read-only (B-1100, B-1101) ----------------------------------------------------------------

EDITING = ("data-vendor-add", 'data-add-row="lines"', "data-split>", 'data-add-row="taxes"', "data-teach>",
           "data-note-form", "rvw-status-group block", "Press G to go")  # fmt: skip
# (Add new vendor, Add a line, Split a line, Add a tax line, Teach a field, the notes form, the "Before you can
# approve" panel)


def test_read_only_opens_an_invoice_read_only_and_posts_nothing(servers):
    """B-1100: invoice.js posted /review/<id>/check on opening, which Read-only is refused (a 403 toast); the screen
    is marked read-only and the script posts nothing. B-1101: the fields could be edited and the tools to change the
    invoice were offered."""
    store, _, clients = servers["full"]
    review = [i["id"] for i in store.list_invoices("review")]
    seen = {text: False for text in EDITING}
    for invoice_id in review:
        clerk = clients["clerk"].get(f"/review/{invoice_id}").text
        reader = clients["readonly"].get(f"/review/{invoice_id}").text
        form = re.search(r'<form id="inv"[^>]*>', reader).group(0)
        assert "data-readonly" in form and "action=" not in form
        assert "data-readonly" not in re.search(r'<form id="inv"[^>]*>', clerk).group(0)
        for text in EDITING:
            seen[text] = seen[text] or text in clerk
            assert text not in reader, (invoice_id, text)
    assert all(seen.values()), seen  # each is on a clerk's screen of one of these invoices
    script = (Path(__file__).resolve().parent.parent / "ap_coder/web/static/js/invoice.js").read_text(encoding="utf-8")
    assert "if (!form || readOnly || form.dataset.sending) return;" in script
    assert "if (readOnly) lockFields();\nelse if (form) check()" in script
