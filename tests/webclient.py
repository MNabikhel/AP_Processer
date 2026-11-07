"""Driving the dashboard (ap_coder/web) in tests: a TestClient on a database of the test's own, the header every
action of the page sends, and the page an action leads to (with its message)."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from fastapi.testclient import TestClient

from ap_coder.coding.line_tax import tax_code_of
from ap_coder.core.config import Settings
from ap_coder.core.store import Store
from ap_coder.web import identity
from ap_coder.web.app import create_app

from .conftest import SAMPLE_STEM, SAMPLES

PAGE = {"X-AP-Coder": "1"}  # what AP Coder's own page sends with every action


def web_settings() -> Settings:
    """Settings without a chat model (no LM Studio is asked)."""
    s = Settings()
    return replace(s, llm=replace(s.llm, provider="off"))


def client_for(store: Store, settings: Settings | None = None) -> TestClient:
    """The dashboard on ``store`` (no page reader thread), as this computer's browser opens it."""
    app = create_app(settings or web_settings(), store, start_reader=False)
    return TestClient(app, base_url="http://localhost")


def post(client: TestClient, url: str, data: dict[str, Any] | None = None, **kwargs: Any) -> Any:
    return client.post(url, data=data or {}, headers=PAGE, **kwargs)


def enrol(store: Store, user_id: int) -> str:
    """Account ``user_id`` with its authenticator app set up already (server mode's second step, D-146): its secret,
    to work out its codes (``authenticator_code``)."""
    secret = store.authenticator_secret(user_id)
    with store._conn() as conn:
        conn.execute("UPDATE users SET totp_on = 1 WHERE id = ?", (user_id,))
    return secret


def authenticator_code(store: Store, username: str) -> str:
    """The code ``username``'s authenticator app shows now (the store's clock). The step it last used is forgotten
    first, so a test may log the same person in twice within 30 seconds; the refusal of a code used already is
    tested on its own (tests/core/test_users.py)."""
    from ap_coder.core import authenticator
    from ap_coder.core.store import users

    name = users.fold_username(username)
    with store._conn() as conn:
        (sealed,) = conn.execute("SELECT totp_secret FROM users WHERE username = ?", (name,)).fetchone()
        conn.execute("UPDATE users SET totp_step = 0 WHERE username = ?", (name,))
    secret = authenticator.open_secret(authenticator.load_key(store.path.parent), sealed)
    return authenticator.code(secret, users._clock())


def sign_in(client: TestClient, username: str, password: str, next: str = "/review", **data: Any) -> Any:  # noqa: A002
    """A login in server mode: the password, then (when it was right) the authenticator's code (D-146). The answer
    of the last step taken."""
    answer = client.post("/login", data={"username": username, "password": password, "next": next, **data},
                         headers=PAGE)  # fmt: skip
    if answer.status_code != 200 or not str(answer.json().get("redirect", "")).startswith("/login/code"):
        return answer
    code = authenticator_code(client.app.state.store, username)
    # The login half way's cookie sent as it was given: over plain HTTP the client keeps a Secure one but doesn't send
    # it back (an explicit Cookie header is left as it is).
    names = (identity.PENDING_COOKIE, identity.HTTP_PENDING_COOKIE)
    pending = "; ".join(f"{name}={client.cookies.get(name)}" for name in names if client.cookies.get(name))
    return client.post("/login/code", data={"code": code, "next": next}, headers={**PAGE, "Cookie": pending})


def follow(client: TestClient, response: Any) -> str:
    """The page an action leads to, with its message (the action must have worked)."""
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer.get("ok", True), answer
    return client.get(answer["redirect"]).text


def draft_form(client: TestClient, invoice_id: int) -> dict[str, Any]:
    """The review form as the page posts it after a grid action: the draft the screen keeps for the invoice."""
    from ap_coder.web import invoice_view

    saved = client.app.state.invoice_drafts[invoice_id]
    rows, taxes = saved["lines"], saved["taxes"]
    data: dict[str, Any] = {**saved["header"], "grids": "1", "line_key": [f"r{i}" for i in range(len(rows))]}
    for column, name in invoice_view.LINE_INPUTS.items():
        data[name] = ["" if r.get(column) is None else str(r.get(column)) for r in rows]
    data[invoice_view.LINE_TAX_CODE] = [tax_code_of(r) for r in rows]  # one tax code a line (D-64)
    for column, name in invoice_view.TAX_INPUTS.items():
        data[name] = ["" if t.get(column) is None else str(t.get(column)) for t in taxes]
    return data


def gt(stem: str = SAMPLE_STEM, **changes: Any) -> dict[str, Any]:
    """A sample's ground truth (samples/ground_truth), a new copy each time, with ``changes`` made to it."""
    return {**json.loads((SAMPLES / "ground_truth" / f"{stem}.json").read_text()), **changes}


def known_vendors(store: Store, *stems: str) -> None:
    """Put the vendors of these samples in the ERP vendor master, so their first invoice is not VENDOR_NEW (which
    always goes to a person, B-80) and bulk approval may take it."""
    rows = [
        {"vendor_name": gt(stem)["vendor_name"], "erp_id": f"V{i}"} for i, stem in enumerate(stems or (SAMPLE_STEM,))
    ]
    store.import_vendor_master(rows)


def add_clean(store: Store, stem: str = SAMPLE_STEM, **changes: Any) -> int:
    """A sample invoice in the queue that may be approved as it is (bulk approval takes it)."""
    doc = {**gt(stem), **changes}
    return store.add_invoice(SAMPLES / f"{stem}.pdf", doc, {"requires_review": False, "adjusted_confidence": 0.97})
