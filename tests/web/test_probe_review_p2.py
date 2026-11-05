"""Review probes (phase 2 part 2): not to be committed."""

from __future__ import annotations

import shutil
from pathlib import Path

from ap_coder.web import document
from ap_coder.web.document import page_png

ROOT = Path(__file__).resolve().parents[2]
SAMPLE = ROOT / "samples" / "northwind_ON_HST_NW-2026-0912.pdf"


def test_probe_a_picture_cut_short_in_the_shared_folder_is_served_as_it_is_and_never_drawn_again(tmp_path):
    """A server stopped hard (power, a reset) after the rename but before the data reached the disk leaves the
    picture empty or cut short at its own name. Every worker then serves it as it is, for good (until pruned)."""
    pdf, folder = tmp_path / "inv.pdf", tmp_path / ".cache" / "pages"
    shutil.copy(SAMPLE, pdf)
    good = page_png(pdf, 1, 1, kept_in=folder)
    (kept,) = [p for p in folder.glob("*/*") if p.suffix == ".png"]
    assert good.startswith(b"\x89PNG") and kept.read_bytes() == good
    kept.write_bytes(good[: len(good) // 3])  # cut short, as a crash leaves it
    again = page_png(pdf, 1, 1, kept_in=folder)
    assert again == good, f"served {len(again)} of {len(good)} bytes: a broken picture"


def test_probe_an_empty_picture_in_the_shared_folder_is_served_empty(tmp_path):
    pdf, folder = tmp_path / "inv.pdf", tmp_path / ".cache" / "pages"
    shutil.copy(SAMPLE, pdf)
    good = page_png(pdf, 1, 1, kept_in=folder)
    (kept,) = [p for p in folder.glob("*/*") if p.suffix == ".png"]
    kept.write_bytes(b"")
    document._sha256.cache_clear()
    assert page_png(pdf, 1, 1, kept_in=folder) == good, "an empty picture was served"


# --- The words' fetch under the 30 s look deadline (browser) ---------------------------------------------------------
import pytest  # noqa: E402

from tests.browser import browser_on, wait_until  # noqa: E402
from tests.web.test_web_viewer import _app, _id  # noqa: E402,F401
from tests.web.test_web_viewer import demo_db, store  # noqa: E402,F401


@pytest.fixture
def viewer_browser(chromium, store):  # noqa: F811
    with browser_on(chromium, _app(store)) as opened:
        yield opened


@pytest.mark.slow
def test_probe_in_a_browser_teaching_a_field_whose_words_take_over_30_s(viewer_browser, store):  # noqa: F811
    """A scan whose OCR is not kept (an OCR engine updated since it came in): /words OCRs every page, which may take
    longer than LOOK_MS. The fetch is given up at 30 s: what does the reviewer read, and is the server asked again?"""
    chromium, base = viewer_browser
    page = chromium.new_page(viewport={"width": 1366, "height": 768})
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    held, asked = [], []
    page.route("**/words", lambda r: asked.append(r.request.url) or held.append(r))
    page.clock.install()
    page.goto(f"{base}/review/{_id(store, 'Cascade')}")
    page.wait_for_selector(".iv-sheet:not(.loading)")
    page.click("[data-teach] > summary")
    page.select_option("[data-teach-field]", "invoice_number")
    page.click("[data-teach-go]")
    wait_until(page, lambda: asked)
    page.clock.run_for(30_500)
    wait_until(page, lambda: page.locator("#toasts > *").count() > 0)
    said = page.inner_text("#toasts")
    print("TOAST:", repr(said))
    page.clock.run_for(1_000)
    if not page.is_visible("[data-teach-go]"):
        page.click("[data-teach] > summary")
    page.click("[data-teach-go]")
    wait_until(page, lambda: len(asked) == 2)
    print("ASKED:", len(asked))
    for r in held:
        r.abort()
    assert "didn't answer" in said, f"the reviewer reads {said!r}"
