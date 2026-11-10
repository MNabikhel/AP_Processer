"""Several files that are one voucher, on the Add invoices page (D-216): the receipts of one expense claim and the
pages of one invoice, the files listed on the invoice, the same file brought again, and making them separate invoices
again."""

from __future__ import annotations

from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from ap_coder.core.config import Settings
from ap_coder.core.store import REVIEW
from ap_coder.web.app import create_app
from ap_coder.web.core import invoice_dir
from tests.pdfs import RECEIPTS, receipt
from tests.stores import sample_setup_store  # noqa: F401  (the tests' store)
from tests.webclient import PAGE

CLAIM_LINES = [
    ("Prairie Fuel Stop: Regular fuel", 60.0, ["GST"], {"GST": 3.0}),
    ("Riverbend Diner: LUNCH SPECIAL", 24.0, ["GST"], {"GST": 1.2}),
    ("Riverbend Diner: COFFEE", 4.0, ["GST"], {"GST": 0.2}),
    ("Riverbend Diner: Tip", 5.0, [], None),
    ("Lakeshore Inn: Room one night", 180.0, ["HST"], {"HST": 23.4}),
]


@pytest.fixture
def app(store):
    s = Settings()
    return create_app(replace(s, llm=replace(s.llm, provider="off")), store, start_reader=False)


@pytest.fixture
def client(app):
    return TestClient(app, base_url="http://localhost")


def finished(app, client):
    assert app.state.jobs.get("process").wait(180), "reading took too long"
    snap = client.get("/process/status").json()
    assert snap["state"] == "done", snap
    return snap


def upload(client, paths, **form):
    files = [("files", (p.name, p.read_bytes(), "application/octet-stream")) for p in paths]
    return client.post("/process/upload", files=files, data=form, headers=PAGE)


def stored(store) -> list[dict]:
    """Every invoice in the store, whole, the oldest first."""
    return [store.get_invoice(i["id"]) for i in sorted(store.list_invoices(), key=lambda i: i["id"])]


def lines_of(invoice) -> list[tuple]:
    """Each line's (description without what OCR does to a space or a digit, amount, taxes, tax read on it)."""
    return [(li["description"], li["amount"], li["taxes_applied"], li.get("taxes_read"))
            for li in invoice["ai_output"]["line_items"]]  # fmt: skip


def as_read(lines: list[tuple]) -> list[tuple]:
    return [(text.replace(" ", "").lower(), *rest) for text, *rest in lines]


def test_three_phone_pictures_of_receipts_are_read_as_one_claim(app, client, store, tmp_path):
    """Fuel with GST, a lunch with GST and a tip, a room with HST, photographed one by one and dropped together as
    the receipts of one claim: one invoice of their lines in order, each line with its own receipt's tax."""
    pictures = [receipt(tmp_path, f"{name}.jpg", rows) for name, rows in RECEIPTS.items()]
    assert upload(client, pictures, voucher="claim").json() == {"ok": True, "job": "process",
                                                               "status": "/process/status"}  # fmt: skip
    assert finished(app, client)["result"]["ok"] == 1
    (invoice,) = stored(store)
    assert invoice["status"] == REVIEW and invoice["file_name"] == "IMG_0411 and 2 more.pdf"
    assert as_read(lines_of(invoice)) == as_read(CLAIM_LINES)
    coded = invoice["ai_output"]
    assert (coded["subtotal"], coded["tax_total"], coded["grand_total"]) == (273.0, 27.8, 300.8)
    assert [(t["tax_type"], t["province"], t["tax_amount"]) for t in coded["tax_lines"]] == [
        ("GST", "", 4.4), ("HST", "ON", 23.4)]  # fmt: skip
    # Neither the supplier nor a reference is guessed from a merchant: each is asked for, in a claim's words.
    assert (coded["vendor_name"], coded["invoice_number"], coded["currency"]) == ("", "", "CAD")
    said = {i["code"]: i["message"] for i in invoice["validation"]["issues"]}
    assert "the supplier is the employee being repaid, not a merchant" in said["MISSING_VENDOR"]
    assert "An expense claim has no invoice number: type a reference" in said["MISSING_INVOICE_NUMBER"]
    assert not {"TAX_BASE_MISMATCH", "TAX_MISMATCH", "TOTAL_MISMATCH", "SUBTOTAL_MISMATCH"} & set(said)
    # The pictures are kept as they came and listed on the invoice; the viewer shows them as one document, in order.
    folder = invoice_dir(store)
    assert sorted(p.name for p in (folder / "originals").iterdir()) == ["IMG_0411.jpg", "IMG_0412.jpg", "IMG_0413.jpg"]
    page = client.get(f"/review/{invoice['id']}").text
    assert "Receipts of one claim" in page and "page 2 is from <b>IMG_0412.jpg</b>" in page
    assert "Make these separate invoices again" in page and "the supplier is the employee being repaid" in page
    assert client.get(f"/review/{invoice['id']}/page/3.png").status_code == 200
    assert client.get(f"/review/{invoice['id']}/page/4.png").status_code == 404


def _receipts_in_folder(store) -> list:
    folder = invoice_dir(store)
    folder.mkdir(parents=True, exist_ok=True)
    return [receipt(folder, f"{name}.pdf", rows) for name, rows in RECEIPTS.items()]


def test_files_ticked_in_the_folder_are_one_claim_then_separate_invoices_again(app, client, store, tmp_path):
    saved = _receipts_in_folder(store)
    page = client.get("/process").text
    assert 'name="names" value="IMG_0412.pdf" form="voucher-form"' in page and 'action="/process/voucher"' in page
    assert "Receipts of one claim" in page and "Pages of one invoice" in page
    # Ticked in another order than the folder lists them: the order sent is the order joined.
    names = ["IMG_0413.pdf", "IMG_0411.pdf", "IMG_0412.pdf"]
    started = client.post("/process/voucher", data={"names": names, "kind": "claim"}, headers=PAGE)
    assert started.json() == {"ok": True, "job": "process", "status": "/process/status"}
    assert finished(app, client)["result"]["ok"] == 1
    (invoice,) = stored(store)
    assert [text.split(":")[0] for text, *_ in lines_of(invoice)] == [
        "Lakeshore Inn", "Prairie Fuel Stop", "Riverbend Diner", "Riverbend Diner", "Riverbend Diner"]  # fmt: skip
    assert invoice["ai_output"]["grand_total"] == 300.8 and invoice["file_name"] == "IMG_0413 and 2 more.pdf"
    assert "No new files" in client.get("/process").text  # the three files are no longer waiting
    listed = client.get(f"/review/{invoice['id']}").text
    assert "page 1 is from <b>IMG_0413.pdf</b>" in listed and "page 3 is from <b>IMG_0412.pdf</b>" in listed

    # The same receipt dropped again, alone: already in AP Coder, no second invoice.
    again = tmp_path / "IMG_0412.pdf"
    again.write_bytes((invoice_dir(store) / "originals" / "IMG_0412.pdf").read_bytes())
    assert upload(client, [again]).status_code == 200
    snap = finished(app, client)
    assert snap["result"]["label"] == "Nothing new to read" and len(store.list_invoices()) == 1
    assert [m["details"] for m in snap["result"]["messages"] if m["text"].startswith("Skipped")] == [["IMG_0412.pdf"]]
    # Dropped again as one of another voucher's files: said where it is, and nothing is joined.
    other = receipt(tmp_path, "taxi.pdf", RECEIPTS["IMG_0411"][:-1] + ["Total|63.01"])
    refused = upload(client, [other, again], voucher="pages")
    assert refused.status_code == 400 and refused.json()["error"].startswith(
        f"IMG_0412.pdf is already in AP Coder: it is page 3 of invoice #{invoice['id']} (IMG_0413 and 2 more.pdf)."
    )
    assert len(store.list_invoices()) == 1

    # Make these separate invoices again: the voucher goes, and each file is read as an invoice of its own.
    undone = client.post(f"/review/{invoice['id']}/separate", headers=PAGE)
    assert undone.json()["redirect"].startswith("/process")
    finished(app, client)
    invoices = {i["file_name"]: i for i in stored(store)}
    assert sorted(invoices) == ["IMG_0411.pdf", "IMG_0412.pdf", "IMG_0413.pdf"]
    assert [invoices[p.name]["vendor_name"] for p in saved] == ["Prairie Fuel Stop", "Riverbend Diner", "Lakeshore Inn"]
    assert all(p.is_file() for p in saved) and not (invoice_dir(store) / "IMG_0413 and 2 more.pdf").exists()
    assert store.joined_files(invoice_id=invoices["IMG_0412.pdf"]["id"]) == []
    assert "joined-files" not in client.get(f"/review/{invoices['IMG_0412.pdf']['id']}").text
    # An invoice of one file has nothing to separate.
    assert client.post(f"/review/{invoices['IMG_0412.pdf']['id']}/separate", headers=PAGE).status_code == 409


def test_one_file_alone_is_read_as_it_always_was(app, client, store, tmp_path):
    alone = receipt(tmp_path, "IMG_0411.pdf", RECEIPTS["IMG_0411"])
    assert upload(client, [alone]).json() == {"ok": True, "job": "process", "status": "/process/status"}
    finished(app, client)
    (invoice,) = stored(store)
    assert (invoice["vendor_name"], invoice["invoice_number"], invoice["grand_total"]) == (
        "Prairie Fuel Stop", "48213", 63.0)  # fmt: skip
    assert invoice["file_name"] == "IMG_0411.pdf" and store.joined_files(invoice_id=invoice["id"]) == []
    assert not (invoice_dir(store) / "originals").exists()
    said = {i["code"]: i["message"] for i in invoice["validation"]["issues"]}
    assert "MISSING_VENDOR" not in said and "CLAIM_RECEIPT_NOT_ADDED" not in said
    assert "joined-files" not in client.get(f"/review/{invoice['id']}").text
    # One file is never a voucher of several, and the ticks need two files in the folder.
    second = receipt(tmp_path, "IMG_0413.pdf", RECEIPTS["IMG_0413"])
    refused = upload(client, [second], voucher="claim")
    assert refused.status_code == 400 and "at least two files" in refused.json()["error"]
    assert 'action="/process/voucher"' not in client.get("/process").text
    gone = client.post("/process/voucher", data={"names": ["IMG_0413.pdf", "../ap_coder.db"], "kind": "pages"},
                       headers=PAGE)  # fmt: skip
    assert gone.status_code == 409 and "../ap_coder.db is no longer in the invoices folder" in gone.json()["error"]


def test_in_a_browser_files_are_ticked_or_picked_put_in_order_and_sent_as_one_voucher(chromium, app, store, tmp_path):
    """The page's own part: the button opens once two files are ticked, an arrow moves a file, and what is sent is
    the order shown, from the invoices folder's list and from the files chosen to upload."""
    from tests.browser import browser_on, wait_until

    _receipts_in_folder(store)

    def joined_as() -> list[list[str]]:
        return [[f["name"] for f in store.joined_files(invoice_id=i["id"])] for i in stored(store)]

    with browser_on(chromium, app) as (browser, base):
        page = browser.new_page()
        page.goto(base + "/process")
        go = page.locator("#voucher-go")
        assert go.is_disabled()
        page.check("input[name=names][value='IMG_0411.pdf']")
        assert go.is_disabled()  # one file alone is read as it is
        page.check("input[name=names][value='IMG_0413.pdf']")
        assert go.is_enabled()
        page.click("button[aria-label='Move IMG_0413.pdf up']")
        page.click("button[aria-label='Move IMG_0413.pdf up']")
        page.select_option("#voucher-form select[name=kind]", "claim")
        go.click()
        wait_until(page, lambda: len(store.list_invoices()) == 1, timeout=60)
        assert joined_as() == [["IMG_0413.pdf", "IMG_0411.pdf"]]
        assert stored(store)[0]["ai_output"]["grand_total"] == 266.4  # the room and the fuel; the lunch still waits

        # The files chosen to upload: offered once there are two, joined in the order the arrows leave them in.
        first = receipt(tmp_path, "page_1.pdf", RECEIPTS["IMG_0411"][:-1] + ["Total|63.01"])
        second = receipt(tmp_path, "page_2.pdf", RECEIPTS["IMG_0413"][:-1] + ["Total|203.41"])
        page.goto(base + "/process")
        assert page.locator("#voucher-row").is_hidden()
        page.set_input_files("#upload-files", [str(first), str(second)])
        assert page.locator("#voucher-row").is_visible() and page.locator("#picked button").count() == 0
        assert page.locator("#upload-label").inner_text() == "Process 2 uploaded invoices"
        page.select_option("#upload-voucher", "pages")
        assert page.locator("#upload-label").inner_text() == "Process 2 files as one voucher"
        page.click("button[aria-label='Move page_1.pdf down']")
        assert page.locator("#picked > li > span:first-child").all_inner_texts() == ["page_2.pdf", "page_1.pdf"]
        page.click("#upload-go")
        wait_until(page, lambda: len(store.list_invoices()) == 2, timeout=60)
        assert joined_as()[1] == ["page_2.pdf", "page_1.pdf"]
