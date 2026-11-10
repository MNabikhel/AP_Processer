"""The review screen of one invoice as a person uses it, for NEW tests on the made-up company (the ``chart_company``
fixture): open an invoice, read what each line shows (its account string, amount, tax and reason), set a line's
account, split a line, check, approve.

The one place that knows the screen's markup, through as few hooks as it can: the controls of the form "inv" by their
posted names (``invoice_view.LINE_INPUTS``, ``LINE_TAX_CODE``, the "Approve anyway" box), a finding by its
``data-code`` and the closed Approve by its ``data-not-ready``. A line's account is two cells today (the business
unit in "Cost center", ``object.subsidiary`` in "GL account"); when the row becomes one Account box, ``Row.account``,
``set_account`` and ``split`` change here and the tests that use them stay. The tests written before this helper
read the markup themselves and stay as they are.

    page = ReviewPage(client, invoice_id)        # GET /review/<id>
    page.lines[0].account                        # "0021061010.7620.6207", or "" while it needs one
    page.set_account(1, "0021061010.7620.6207")  # line 1, as picked on the screen
    page.split(2, {"0021061014.7830.8307": 60, "0021062110.7830.8307": 40})  # line 2 across two business units
    page.check()                                 # what the screen's own check answers; the page drawn again
    page.findings, page.not_ready                # {code: its words}; why Approve is closed ("": it is open)
    page.approve()                               # Approve & teach: the answer
    page.approve(anyway="set up today")          # ... with "Approve anyway" ticked and its reason
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any

from fastapi.testclient import TestClient

from ap_coder.core import chart
from ap_coder.core.reference_data import UNASSIGNED
from ap_coder.web.invoice_view import LINE_INPUTS, LINE_TAX_CODE

from .webclient import PAGE

FORM = "inv"
ACCOUNT, BUSINESS_UNIT = LINE_INPUTS["predicted_gl_code"], LINE_INPUTS["predicted_cost_center"]
ANYWAY, ANYWAY_REASON = "override", "override_reason"  # the "Approve anyway" box and why


@dataclass
class Row:
    """A line as its row shows it."""

    number: str
    description: str
    account: str  # BusinessUnit.Object.Subsidiary; "" while the line needs its account or its business unit
    amount: str
    tax: str  # the tax code's words: "GST", "GST + QST", "No tax charged"
    reason: str  # why it was coded so, or why it was left for a person


class _Screen(HTMLParser):
    """What the page holds for a test: the form's controls in page order as a browser posts them ([name, value, the
    words shown]; a line template's are not posted), the findings ({code: words}), why Approve is closed and what
    the "Approve anyway" box posts when ticked ("": the page has none)."""

    def __init__(self, text: str) -> None:
        super().__init__(convert_charrefs=True)
        self.controls: list[list[str]] = []
        self.findings: dict[str, str] = {}
        self.not_ready = self.anyway = ""
        self._in_form = self._template = 0
        self._select: list[Any] | None = None  # [name, its options: [value, selected, words]]
        self._text: list[str] | None = None  # the words of the option or the textarea being read
        self.feed(text)
        self.close()

    def _mine(self, at: dict[str, str | None]) -> bool:
        ours = at.get("form") == FORM if "form" in at else bool(self._in_form)
        return ours and not self._template and bool(at.get("name")) and "disabled" not in at

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        at = dict(attrs)
        if tag == "template":
            self._template += 1
        elif tag == "form" and at.get("id") == FORM:
            self._in_form = 1
        if at.get("data-code"):
            self.findings[str(at["data-code"])] = at.get("title") or ""
        if "data-not-ready" in at:
            self.not_ready = at["data-not-ready"] or ""
        if tag == "input" and self._mine(at):
            if at["name"] == ANYWAY:
                self.anyway = at.get("value") or ""
            if at.get("type") not in ("checkbox", "radio") or "checked" in at:
                self.controls.append([str(at["name"]), at.get("value") or "", at.get("value") or ""])
        elif tag == "select":
            self._select = [at["name"], []] if self._mine(at) else None
        elif tag == "option" and self._select is not None:
            self._select[1].append([at.get("value"), "selected" in at])
            self._text = []
        elif tag == "textarea" and self._mine(at):
            self.controls.append([str(at["name"]), "", ""])
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._text is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        words = "".join(self._text or []).strip()
        if tag == "template":
            self._template -= 1
        elif tag == "form":
            self._in_form = 0
        elif tag == "option" and self._select is not None and self._text is not None:
            self._select[1][-1].append(words)
            self._text = None
        elif tag == "textarea" and self._text is not None:
            self.controls[-1][1:] = [words, words]
            self._text = None
        elif tag == "select" and self._select is not None:
            name, options = self._select
            chosen = next((o for o in options if o[1]), options[0] if options else None)
            if chosen is not None:
                self.controls.append([name, chosen[2] if chosen[0] is None else chosen[0], chosen[2]])
            self._select = None


def queue_reasons(client: TestClient, address: str = "/review") -> dict[int, str]:
    """What the review queue says of each invoice it lists, by id: why it needs attention ("": it is Ready). The one
    place that knows the queue's markup: a row's link to its invoice, then its status cell's ``title``."""
    answer = client.get(address)
    assert answer.status_code == 200, answer.text
    rows = re.split(r'href="/review/(\d+)["?]', answer.text)[1:]
    said: dict[int, str] = {}
    for number, row in zip(rows[::2], rows[1::2], strict=True):
        cell = re.search(r'class="c-status"(?: title="([^"]*)")?', row)
        if cell and int(number) not in said:
            said[int(number)] = html.unescape(cell.group(1) or "")
    return said


class ReviewPage:
    """The review screen of invoice ``invoice_id`` on ``client``, opened."""

    def __init__(self, client: TestClient, invoice_id: int) -> None:
        self.client, self.id = client, invoice_id
        self.open()

    def open(self) -> ReviewPage:
        """The page as served now (what a check kept of a person's edits is in it): ``status`` is the answer's."""
        answer = self.client.get(f"/review/{self.id}")
        self.status, self.text = answer.status_code, answer.text
        screen = _Screen(self.text)
        self._controls, self.findings, self.not_ready = screen.controls, screen.findings, screen.not_ready
        self._anyway = screen.anyway
        return self

    def _cells(self, name: str) -> list[list[str]]:
        return [control for control in self._controls if control[0] == name]

    @property
    def lines(self) -> list[Row]:
        shown = {column: [cell[2] for cell in self._cells(name)] for column, name in LINE_INPUTS.items()}
        units, codes = (self._cells(name) for name in (BUSINESS_UNIT, ACCOUNT))
        accounts = ["" if UNASSIGNED in (unit[1], code[1]) else chart.join_account(unit[1], code[1])
                    for unit, code in zip(units, codes, strict=True)]  # fmt: skip
        taxes = [cell[2] for cell in self._cells(LINE_TAX_CODE)]
        return [Row(shown["line_number"][n], shown["description"][n], accounts[n], shown["amount"][n], taxes[n],
                    shown["reasoning_justification"][n]) for n in range(len(accounts))]  # fmt: skip

    def set_account(self, line: int, account: str) -> None:
        """Put ``account`` (BusinessUnit.Object.Subsidiary) in line ``line`` (the first is 1), as picked on the
        screen: nothing is sent until ``check`` or ``approve``."""
        unit, code = chart.split_account(account)
        self._cells(BUSINESS_UNIT)[line - 1][1:] = [unit, unit]
        self._cells(ACCOUNT)[line - 1][1:] = [code, code]

    @property
    def form(self) -> dict[str, list[str]]:
        """The form as the page posts it: every control's value under its name, in page order."""
        posted: dict[str, list[str]] = {}
        for name, value, _ in self._controls:
            posted.setdefault(name, []).append(value)
        return posted

    def check(self) -> dict[str, Any]:
        """What the screen's check answers for what is on it now (``allow``: Approve is open), kept as the person's
        draft; then the page again, which shows that draft with its findings."""
        answer = self.client.post(f"/review/{self.id}/check", data=self.form, headers=PAGE)
        assert answer.status_code == 200, answer.text
        self.open()
        return answer.json()

    def split(self, line: int, shares: dict[str, float]) -> None:
        """Split line ``line`` (the first is 1) across the account strings of ``shares``, each its percentage, with
        the screen's "Split a line"; then the page again, with the lines it made."""
        units, codes = zip(*(chart.split_account(account) for account in shares), strict=True)
        data = {**self.form, "split_line": self.lines[line - 1].number, "part_cc": list(units),
                "part_gl": list(codes), "part_pct": [str(share) for share in shares.values()]}  # fmt: skip
        answer = self.client.post(f"/review/{self.id}/split", data=data, headers=PAGE)
        assert answer.status_code == 200, answer.text
        self.open()

    def approve(self, anyway: str = "") -> dict[str, Any]:
        """Approve & teach with what is on the screen; ``anyway``: with "Approve anyway" ticked, for this reason.
        The answer: ``redirect`` when it was approved, else ``ok`` False with ``error``, why not."""
        more = {ANYWAY: self._anyway, ANYWAY_REASON: anyway} if anyway else {}
        answer = self.client.post(f"/review/{self.id}/approve", data={**self.form, **more}, headers=PAGE)
        assert answer.status_code in (200, 400, 403, 409), answer.text
        return answer.json()
