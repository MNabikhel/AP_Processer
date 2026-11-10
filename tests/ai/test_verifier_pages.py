"""Which pages of a long invoice the chat model is shown (B-2216): the AI reviewer the first page, the LAST (the
totals) and the page with the most line items, told which pages it has; the header model the first and the last.

The model here is the house's stand-in (``FakeCoder``): what a real model makes of the instruction is not shown."""

from __future__ import annotations

import pytest

from ap_coder.ai import verifier
from ap_coder.ai.imaging import pages_to_show, render_page_images
from ap_coder.ai.pipeline import InvoicePipeline
from tests.ai.test_uniform_reading import _settings, store  # noqa: F401 - fixtures
from tests.ai.test_verifier import SHOWN, FakeCoder, Script, _answer
from tests.pdfs import long_invoice, text_pdf


class Watched(FakeCoder):
    """The stand-in chat model, keeping what it was sent."""

    def _local_create(self, model, messages, schema=None, **more):
        self.messages, self.schema = messages, schema
        return super()._local_create(model, messages, schema, **more)

    def pages(self, path) -> list[int]:
        """The page numbers of the images in the last request: each image is one of the file's pages, rendered."""
        count = len(render_page_images(path, 500))
        rendered = {image.to_data_url(): image.page_number for image in render_page_images(path, count)}
        return [rendered[part["image_url"]["url"]] for part in self.messages[-1]["content"][1:]]

    @property
    def text(self) -> str:
        return self.messages[-1]["content"][0]["text"]


def _invoice(tmp_path, pages: int, busy: int | None = None):
    """A made-up invoice of ``pages`` pages, two lines a page, ``busy`` (a page) with five; its totals on the last."""
    if busy is None:
        return long_invoice(tmp_path, pages, per_page=2, name=f"p{pages}.pdf")[0]
    items = [(50, 60, "Rapidway Courier Canada Ltd."), (400, 60, "INVOICE"), (400, 76, "Invoice No: RW-88214"),
             (400, 92, "Invoice Date: 2026-05-04")]  # fmt: skip
    cents = 0
    for page in range(1, pages + 1):
        items += [(50, 150, "Description", 10, page), (350, 150, "Qty", 10, page), (420, 150, "Unit Price", 10, page),
                  (500, 150, "Amount", 10, page)]  # fmt: skip
        for row in range(5 if page == busy else 1):
            cents += 1025
            items += [(50, 168 + 16 * row, f"Ground shipment waybill {page}0{row}", 10, page),
                      (350, 168 + 16 * row, "1", 10, page), (420, 168 + 16 * row, "10.25", 10, page),
                      (500, 168 + 16 * row, "10.25", 10, page)]  # fmt: skip
    gst = round(cents * 0.05)
    items += [(400, 300, "Subtotal", 10, pages), (500, 300, f"{cents / 100:,.2f}", 10, pages),
              (400, 316, "GST 5%", 10, pages), (500, 316, f"{gst / 100:,.2f}", 10, pages),
              (400, 332, "Total Due", 10, pages), (500, 332, f"{(cents + gst) / 100:,.2f}", 10, pages)]  # fmt: skip
    return text_pdf(tmp_path, items, name=f"busy{pages}.pdf", pages=pages)


def _reviewed(store, path, *answers):  # noqa: F811
    iid = InvoicePipeline(_settings(), store.reference_data(), store=store).process(path).invoice_id
    store.queue_verification(iid)
    coder = Watched(*(answers or (_answer(),)))
    return iid, coder, verifier.verify_one(_settings(), store, coder=coder, after_page_reader=False)


def test_which_pages_a_model_is_shown():
    assert [pages_to_show(n, 3) for n in (1, 2, 3, 4, 10, 30)] == [
        [1], [1, 2], [1, 2, 3], [1, 2, 4], [1, 2, 10], [1, 2, 30]]  # fmt: skip
    assert [pages_to_show(n, 3, busiest=n // 2) for n in (4, 10, 30)] == [[1, 2, 4], [1, 5, 10], [1, 15, 30]]
    assert pages_to_show(30, 3, busiest=30) == [1, 2, 30] == pages_to_show(30, 3, busiest=1)
    assert [pages_to_show(n, 2, busiest=3) for n in (1, 2, 3, 4, 10, 30)] == [
        [1], [1, 2], [1, 3], [1, 4], [1, 10], [1, 30]]  # fmt: skip


@pytest.mark.parametrize(("pages", "sent"), [(1, [1]), (2, [1, 2]), (3, [1, 2, 3]), (4, [1, 2, 4]), (10, [1, 2, 10]),
                                             (30, [1, 2, 30])])  # fmt: skip
def test_the_reviewer_is_sent_the_first_and_the_last_page(store, tmp_path, pages, sent):  # noqa: F811
    path = _invoice(tmp_path, pages)
    _, coder, done = _reviewed(store, path)
    assert coder.pages(path) == sent
    told = "You are given pages 1, 2 and " + f"{pages} of this invoice's {pages} pages, not the others."
    assert (told in coder.text) == (pages > 3)
    item = coder.schema["properties"]["fields"]["items"]
    assert ("not_shown" in item["properties"]) == (pages > 3) == ("not_shown" in item["required"])
    assert done.status == verifier.AGREED  # (the stand-in agrees to all it is asked)


def test_room_allowing_it_is_sent_the_page_with_the_most_lines(store, tmp_path):  # noqa: F811
    path = _invoice(tmp_path, 10, busy=6)
    iid, coder, _ = _reviewed(store, path)
    assert verifier.busiest_page(store.get_capture(iid)) == 6
    assert coder.pages(path) == [1, 6, 10]
    assert "You are given pages 1, 6 and 10 of this invoice's 10 pages, not the others." in coder.text
    assert '"pages"' not in coder.text.split("For reference")[1]  # said in words, not among the figures


def _hidden(data: dict, fields=(), lines=()) -> dict:
    """An answer with ``not_shown`` on each entry: true for these fields and lines."""
    return {**data, "fields": [{**f, "not_shown": f["field"] in fields} for f in data["fields"]],
            "lines": [{**li, "not_shown": li["line_number"] in lines} for li in data["lines"]]}  # fmt: skip


LONG = {**SHOWN, "pages": {"given": [1, 6, 10], "count": 10},
        "lines": [{"line_number": n, "description": "Freight", "amount": 10.25} for n in (1, 2, 3, 4)]}  # fmt: skip


def test_what_is_on_a_page_it_was_not_given_is_not_shown_not_wrong():
    """A line it cannot find is on a page it does not have: no disagreement, no second and third look at the same
    pages; it is not sure, and says which pages it had and what it did not check."""
    ask = Script(_hidden(_answer(lines={3: "not on these pages", 4: "not on these pages"}, numbers=(1, 2, 3, 4)),
                         lines=(3, 4)))  # fmt: skip
    found = verifier.review(ask, LONG)
    assert (found.status, found.rounds, found.fields, found.lines) == (verifier.UNSURE, 1, {}, {})
    assert found.why == "it was shown pages 1, 6 and 10 of the 10; not on them, so not checked: lines 3 and 4"
    assert verifier.issues(found.to_dict()) == [(
        "warning", "VERIFIER_UNSURE", "the AI reviewer is not sure (it was shown pages 1, 6 and 10 of the 10; not on "
        "them, so not checked: lines 3 and 4): it could not confirm the invoice")]  # fmt: skip


def test_a_long_invoice_it_saw_all_of_is_agreed_and_one_it_reads_differently_is_still_disputed():
    whole = _hidden(_answer(numbers=(1, 2, 3, 4)))
    assert verifier.review(Script(whole), LONG).status == verifier.AGREED
    wrong = {"grand_total": "9,999.00"}
    twice = lambda fields: _hidden(_answer(wrong, fields=fields, numbers=(1, 2, 3, 4)), lines=(3,))  # noqa: E731
    found = verifier.review(Script(twice, twice), LONG)
    assert found.status == verifier.DISAGREES and list(found.fields) == ["grand_total"] and not found.lines


def test_not_shown_means_nothing_when_it_has_every_page():
    """An answer saying "not shown" of an invoice it was shown whole is not an agreement."""
    ask = Script(*[_hidden(_answer(), fields=("grand_total",))] * 3)
    found = verifier.review(ask, SHOWN)
    assert found.status == verifier.AGREED  # as before B-2216: the entry's agree is what counts


def test_the_header_model_reads_the_first_and_the_last_page(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from ap_coder.ai import inference

    seen = []
    monkeypatch.setattr(inference, "read_header", lambda coder, images: seen.append(
        [image.page_number for image in images]) or {"vendor_name": "Rapidway"})  # fmt: skip
    coder = SimpleNamespace(_prepare_local=lambda: "qwen3.5-9b", profile=SimpleNamespace(supports_vision=True))
    for pages in (1, 2, 3, 4, 10, 30):
        path = _invoice(tmp_path, pages)
        assert InvoicePipeline._model_reading(SimpleNamespace(coder=coder), path, f"{pages:064d}")
    assert seen == [[1], [1, 2], [1, 3], [1, 4], [1, 10], [1, 30]]
