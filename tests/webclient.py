"""Driving the dashboard (ap_coder/web) in tests: a TestClient on a database of the test's own, the header every
action of the page sends, and the page an action leads to (with its message)."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

from fastapi.testclient import TestClient

from ap_coder.config import Settings
from ap_coder.store import Store
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
    for i, r in enumerate(rows):
        data[f"line_tax_r{i}"] = list(r.get("taxes_applied") or [])
    for column, name in invoice_view.TAX_INPUTS.items():
        data[name] = ["" if t.get(column) is None else str(t.get(column)) for t in taxes]
    return data


def gt(stem: str = SAMPLE_STEM) -> dict[str, Any]:
    return json.loads((SAMPLES / "ground_truth" / f"{stem}.json").read_text())


def add_clean(store: Store, stem: str = SAMPLE_STEM, **changes: Any) -> int:
    """A sample invoice in the queue that may be approved as it is (bulk approval takes it)."""
    doc = {**gt(stem), **changes}
    return store.add_invoice(SAMPLES / f"{stem}.pdf", doc, {"requires_review": False, "adjusted_confidence": 0.97})
