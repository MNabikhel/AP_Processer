"""The review screen fits the window it is given (D-214): swept in a real Chromium with a 17 px scrollbar (Windows'
own) over 12 widths from 1000 to 2560 px and 3 heights, on the made-up company's four-line and fifteen-line invoices
(a chart of accounts: one Account box a line) and on a demo invoice of an install without a chart.

At every window: the page never scrolls sideways and nothing is drawn outside its card. Where the two panes sit side
by side (a window of 1200 px or more), the four-line invoice shows its first line and the Approve button without
scrolling."""

import pytest

from ap_coder.core.store import REVIEW
from ap_coder.web.app import create_app
from tests.browser import browser_on
from tests.conftest import chromium_path, copy_database
from tests.webclient import web_settings

pytestmark = pytest.mark.slow

SCROLLBAR = 17
WIDTHS = (1000, 1100, 1183, 1200, 1217, 1280, 1366, 1440, 1536, 1819, 1920, 2560)
HEIGHTS = (640, 800, 1080)
TWO_PANES = 1200  # the window's width from which the invoice and the form are side by side
BAR = ("html {overflow-y: scroll !important;} "
       f"::-webkit-scrollbar {{width: {SCROLLBAR}px; height: {SCROLLBAR}px; background: #e5e7eb;}} "
       "::-webkit-scrollbar-thumb {background: #9ca3af;}")  # fmt: skip

# What one window shows: the page's widths, what is drawn outside its card (a box, a button, a cell or a label whose
# edge is past its card's and that nothing of the card clips), and where the first line and Approve are.
MEASURE = """() => {
  window.scrollTo(0, 0);
  const de = document.documentElement;
  const seen = (e) => e && e.getClientRects().length > 0 && getComputedStyle(e).visibility !== "hidden";
  const clipped = (e, card) => {
    for (let p = e.parentElement; p && p !== card; p = p.parentElement) {
      if (getComputedStyle(p).overflowX !== "visible") return true;
    }
    return false;
  };
  const outside = [];
  for (const card of document.querySelectorAll(".inv-split .card, .inv-actionbar, .inv-top")) {
    if (!seen(card) || card.parentElement.closest(".card")) continue;
    const c = card.getBoundingClientRect();
    for (const e of card.querySelectorAll("input, select, textarea, button, .btn, td, th, label, summary, .apc-pill")) {
      if (!seen(e) || e.closest(".pop-body, [hidden]") || clipped(e, card)) continue;
      const r = e.getBoundingClientRect();
      if (r.width && (r.right > c.right + 1 || r.left < c.left - 1)) {
        const what = `${e.tagName.toLowerCase()}.${e.className || e.name || ""}`;
        const of = card.className.split(" ").slice(0, 3).join(".");
        const at = (x) => `${Math.round(x.left)}-${Math.round(x.right)}`;
        outside.push(`${what} ${at(r)} of ${of} ${at(c)}`);
      }
    }
  }
  const box = (e) => {
    if (!seen(e)) return null;
    const r = e.getBoundingClientRect();
    return [r.top, r.bottom, r.left, r.right];
  };
  return {
    inner: innerWidth, client: de.clientWidth, scroll: de.scrollWidth, height: innerHeight,
    outside: outside.slice(0, 6),
    line: box(document.querySelector('[data-grid="lines"] [data-row] input[data-account]')),
    approve: box(document.querySelector("[data-approve]")),
    bar: box(document.querySelector(".inv-actionbar")),
    panes: (() => { const d = document.querySelector(".inv-doc"), f = document.querySelector(".inv-split > .col");
      return d && f ? [d.getBoundingClientRect().width, f.getBoundingClientRect().width,
                       Math.abs(d.getBoundingClientRect().top - f.getBoundingClientRect().top) < 2] : null; })(),
  };
}"""


@pytest.fixture(scope="module")
def chromium():
    """A Chromium that draws its scrollbars (the shared one hides them), for this file."""
    path = chromium_path()
    if path is None:
        pytest.skip("Playwright with Chromium isn't installed")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=path, args=["--no-proxy-server"],
                                    ignore_default_args=["--hide-scrollbars"])  # fmt: skip
        try:
            yield browser
        finally:
            browser.close()


def _sweep(chromium, store, invoice_id: int, first: str = "") -> list[dict]:
    """The invoice's review screen measured at every window, each with ``window`` (width, height); ``first``: a
    script run on the page before it is measured."""
    out = []
    with browser_on(chromium, create_app(web_settings(), store, start_reader=False)) as (browser, base):
        page = browser.new_page(viewport={"width": 1366, "height": 768})
        page.goto(f"{base}/review/{invoice_id}")
        page.wait_for_load_state("networkidle")
        page.add_style_tag(content=BAR)
        if first:
            page.evaluate(first)
        for width in WIDTHS:
            for height in HEIGHTS:
                page.set_viewport_size({"width": width, "height": height})
                page.wait_for_timeout(60)  # the viewer fits its pane again
                out.append({**page.evaluate(MEASURE), "window": (width, height)})
    return out


def _faults(measures: list[dict]) -> list[str]:
    """What is wrong at any window: a sideways scroll, something drawn outside its card."""
    faults = []
    for m in measures:
        assert m["inner"] - m["client"] == SCROLLBAR, "the sweep needs a real scrollbar"
        if m["scroll"] > m["client"]:
            faults.append(f"{m['window']}: the page scrolls sideways ({m['scroll']} of {m['client']} px)")
        faults += [f"{m['window']}: outside its card: {what}" for what in m["outside"]]
    return faults


LONG_NOTE = """() => {
  const slot = document.querySelector('[data-grid="lines"] [data-row] td.c-slot');
  slot.innerHTML = '<div class="acct-note fix"><p><b>To fix:</b> <span data-acct-why>' +
    "This supplier's 3 lines last year went to 3 accounts: 33% / 33% / 33%: pick the account. The chat model was " +
    "asked and gave no answer, so nothing here is its pick; the three accounts are offered below." +
    '</span></p></div>';
}"""


def test_a_long_sentence_under_a_line_wraps_in_every_window(chromium, chart_company):
    """B-2209: a line left to a person says why in a sentence as long as it needs: it wraps inside the line (the
    template's own markup, with a long sentence), and the page never scrolls sideways."""
    faults = _faults(_sweep(chromium, chart_company.store, chart_company.invoices["four_lines"][1], LONG_NOTE))
    assert not faults, "\n".join(faults[:25])


@pytest.mark.parametrize("name", ["four_lines", "fifteen_lines"])
def test_the_review_screen_with_a_chart_fits_every_window(chromium, chart_company, name):
    measures = _sweep(chromium, chart_company.store, chart_company.invoices[name][1])
    faults = _faults(measures)
    for m in measures:
        if m["window"][0] < TWO_PANES:
            continue
        doc, form, beside = m["panes"]
        if not (beside and doc >= 400 and form >= 690):
            faults.append(f"{m['window']}: the two panes are {doc:.0f} and {form:.0f} px, side by side: {beside}")
        if name == "four_lines":
            top, bottom = m["line"][0], m["line"][1]
            if not (0 <= top and bottom <= m["bar"][0]):
                faults.append(
                    f"{m['window']}: the first line is at {top:.0f}-{bottom:.0f}, the bar at {m['bar'][0]:.0f}"
                )
            if not (m["approve"] and 0 <= m["approve"][0] and m["approve"][1] <= m["height"]
                    and m["approve"][3] <= m["client"]):  # fmt: skip
                faults.append(f"{m['window']}: Approve is at {m['approve']}")
    assert not faults, "\n".join(faults[:25])


def test_the_review_screen_without_a_chart_fits_every_window(chromium, demo_template, tmp_path):
    store = copy_database(demo_template, tmp_path / "private" / "ap_coder.db")
    invoice_id = next(inv["id"] for inv in store.list_invoices() if inv["status"] == REVIEW)
    faults = _faults(_sweep(chromium, store, invoice_id))
    assert not faults, "\n".join(faults[:25])
