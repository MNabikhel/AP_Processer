"""Several files joined into one voucher (D-216, ``intake/joined.py``): the document made of them, the files kept,
what is refused, and the pages of one invoice read as the same invoice scanned as one file."""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pymupdf
import pytest

from ap_coder.ai.pipeline import InvoicePipeline
from ap_coder.core.audit import ACTIONS, describe
from ap_coder.core.config import Settings
from ap_coder.core.store import Store, load_sample_setup
from ap_coder.core.store.folder import read_file
from ap_coder.intake.joined import CLAIM, PAGES, CannotJoin, join, pages_of, removed, separate
from tests.pdfs import RECEIPTS, long_invoice, receipt


@pytest.fixture
def folder(tmp_path):
    made = tmp_path / "private" / "invoices"
    made.mkdir(parents=True)
    return made


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_files_are_joined_in_order_into_one_document_and_kept_as_they_came(store, folder):
    picture = receipt(folder, "IMG_0411.jpg", RECEIPTS["IMG_0411"])
    two_pages, _, _ = long_invoice(folder, pages=2, per_page=3, name="scan.pdf")
    last = receipt(folder, "IMG_0413.jpg", RECEIPTS["IMG_0413"])
    shas = [_sha(p) for p in (picture, two_pages, last)]
    made = join(store, folder, [picture, two_pages, last], CLAIM)
    assert made == folder / "IMG_0411 and 2 more.pdf"
    with pymupdf.open(made) as doc:
        assert doc.page_count == 4 and "Rapidway Courier" in doc[1].get_text()  # the PDF's pages as they are
    files = store.joined_files(_sha(made))
    assert [(f["name"], f["first_page"], f["pages"], f["kind"], f["sha256"]) for f in files] == [
        ("IMG_0411.jpg", 1, 1, CLAIM, shas[0]), ("scan.pdf", 2, 2, CLAIM, shas[1]),
        ("IMG_0413.jpg", 4, 1, CLAIM, shas[2])]  # fmt: skip
    # Each file is kept as it came, out of the folder's list of new files.
    assert sorted(p.name for p in folder.iterdir()) == ["IMG_0411 and 2 more.pdf", "originals"]
    assert [_sha(folder / f["kept"]) for f in files] == shas
    with pymupdf.open(pages_of(made, 2, 2, folder.parent / "one.pdf")) as part:  # one receipt, to read on its own
        assert part.page_count == 2 and "Subtotal" in part[1].get_text()
    # An invoice whose pages were set upright keeps the document as it was joined as its original: still found.
    upright = folder.parent / "upright.pdf"
    upright.write_bytes(b"%PDF-1.4 turned")
    turned = store.add_invoice(upright, {"vendor_name": ""}, None, original_sha256=_sha(made))
    assert [f["name"] for f in store.joined_files(invoice_id=turned)] == ["IMG_0411.jpg", "scan.pdf", "IMG_0413.jpg"]
    assert store.joined_where(shas[1])["invoice_id"] == turned and store.known_files(shas) == set(shas)


def test_what_cannot_be_joined_is_said_and_nothing_is_joined(store, folder):
    first = receipt(folder, "a.pdf", RECEIPTS["IMG_0411"])
    second = receipt(folder, "b.pdf", RECEIPTS["IMG_0412"])
    copy = folder / "a again.pdf"
    copy.write_bytes(first.read_bytes())
    notes = folder / "notes.txt"
    notes.write_text("Total 12.00")
    for paths, kind, said in (
        ([first], PAGES, "at least two files"),
        ([first, second], "", "Choose what the files are"),
        ([first, notes], PAGES, "notes.txt is not a PDF or a picture"),
        ([first, copy], PAGES, "a again.pdf is the same file as a.pdf"),
    ):
        with pytest.raises(CannotJoin, match=said):
            join(store, folder, paths, kind)
    assert first.exists() and second.exists() and not (folder / "originals").exists()
    store.queue_imports([(str(second), 1, 1, _sha(second))], source="upload")  # in line to be read on its own
    with pytest.raises(CannotJoin, match="b.pdf is being read as an invoice of its own"):
        join(store, folder, [first, second], PAGES)


def test_a_file_already_part_of_an_invoice_is_said_where_it_is_and_counts_as_in_ap_coder(store, folder):
    first = receipt(folder, "a.pdf", RECEIPTS["IMG_0411"])
    second = receipt(folder, "b.pdf", RECEIPTS["IMG_0412"])
    second_sha = _sha(second)
    made = join(store, folder, [first, second], CLAIM)
    assert store.known_files([second_sha]) == set()  # no invoice was made from the document yet
    invoice_id = store.add_invoice(made, {"vendor_name": "", "grand_total": 97.4}, None)
    assert store.known_files([second_sha, "0" * 64]) == {second_sha}
    again = folder / "b.pdf"  # the same receipt saved in the folder again
    again.write_bytes((folder / "originals" / "b.pdf").read_bytes())
    seen = again.stat()
    store.note_files([(str(again), seen.st_size, seen.st_mtime_ns, second_sha)])
    assert store.unseen_files([(str(again), seen.st_size, seen.st_mtime_ns)]) == set()  # not listed as a new file
    assert store.queue_imports([(str(again), seen.st_size, seen.st_mtime_ns, second_sha)], source="upload") == {
        "batch": 1, "run": 1, "queued": 0, "skipped": ["b.pdf"], "ahead": 0}  # fmt: skip
    other = receipt(folder, "c.pdf", RECEIPTS["IMG_0413"])
    with pytest.raises(CannotJoin) as refused:
        join(store, folder, [other, again], PAGES)
    assert str(refused.value).startswith(f"b.pdf is already in AP Coder: it is page 2 of invoice #{invoice_id} "
                                         "(a and 1 more.pdf).")  # fmt: skip
    # An invoice made of one file and no longer in review is said the same way.
    alone = store.add_invoice(other, {"vendor_name": "Lakeshore Inn", "grand_total": 203.4}, None)
    store.reject_invoice(alone, "Dana", "not ours")
    spare = receipt(folder, "d.pdf", RECEIPTS["IMG_0411"][:-1] + ["Total|63.01"])
    with pytest.raises(CannotJoin, match=f"c.pdf is already in AP Coder: it is invoice #{alone}."):
        join(store, folder, [spare, other], PAGES)


def test_separating_a_voucher_puts_its_files_back_and_removes_it(store, folder):
    first = receipt(folder, "a.pdf", RECEIPTS["IMG_0411"])
    second = receipt(folder, "b.jpg", RECEIPTS["IMG_0412"])
    shas = [_sha(first), _sha(second)]
    made = join(store, folder, [first, second], PAGES)
    invoice_id = store.add_invoice(made, {"vendor_name": "Prairie Fuel Stop", "grand_total": 97.4}, None)
    assert [f["name"] for f in store.joined_files(invoice_id=invoice_id)] == ["a.pdf", "b.jpg"]
    back = separate(store, store.get_invoice(invoice_id), "Dana")
    assert back == [first, second] and [_sha(p) for p in back] == shas
    assert not made.exists() and store.get_invoice(invoice_id) is None
    assert store.joined_files(_sha(first)) == [] and store.known_files(shas) == set()  # each is read on its own
    with pytest.raises(CannotJoin, match="not made of several files"):
        separate(store, {"id": store.add_invoice(first, {"vendor_name": "Prairie Fuel Stop"}, None),
                         "source_path": str(first)})  # fmt: skip


def test_a_file_is_part_of_its_voucher_from_the_moment_it_is_joined(store, folder):
    """B-2283: the voucher still waits to be read, and the same receipt comes again: it is not joined into a second
    voucher (two people with the same receipts), nor read as an invoice of its own."""
    first = receipt(folder, "a.pdf", RECEIPTS["IMG_0411"])
    second = receipt(folder, "b.pdf", RECEIPTS["IMG_0412"])
    lunch = second.read_bytes()
    made = join(store, folder, [first, second], CLAIM)
    assert store.queue_imports([read_file(made)], source="upload")["queued"] == 1  # in line: no invoice yet
    again = folder / "b (colleague).pdf"
    again.write_bytes(lunch)
    other = receipt(folder, "c.pdf", RECEIPTS["IMG_0413"])
    with pytest.raises(CannotJoin) as refused:
        join(store, folder, [again, other], CLAIM)
    assert str(refused.value) == (
        "b (colleague).pdf is already in AP Coder: it is page 2 of a and 1 more.pdf, a "
        "voucher waiting to be read. Nothing was joined: leave it out and join the others."
    )
    assert again.is_file() and other.is_file() and len(store.joined_files(_sha(made))) == 2
    alone = read_file(again)
    assert store.queue_imports([alone], source="upload")["skipped"] == ["b (colleague).pdf"]
    assert store.unseen_files([alone[:3]]) == set()  # nor listed as a new file of the folder


def test_who_joined_and_who_separated_which_files_is_in_the_audit_trail(store, folder):
    """B-2284: the join named no file and no person, and a separation was a plain "deleted"."""
    from ap_coder.web.pages.activity import GROUPS

    first = receipt(folder, "a.pdf", RECEIPTS["IMG_0411"])
    second = receipt(folder, "b.jpg", RECEIPTS["IMG_0412"])
    made = join(store, folder, [first, second], CLAIM, "Ann Lee")
    (event,) = store.events(actions=["joined"])
    assert (event["actor"], event["invoice_id"]) == ("Ann Lee", None)  # no invoice yet: on Activity all the same
    assert describe(event) == "a and 1 more.pdf: a.pdf, b.jpg joined as the receipts of one claim"
    invoice_id = store.add_invoice(made, {"vendor_name": "", "grand_total": 97.4}, None)
    store.joined_read(invoice_id)  # as the reading does: the event is in the invoice's history
    assert [(e["action"], e["actor"]) for e in store.events(invoice_id)] == [("processed", None), ("joined", "Ann Lee")]
    separate(store, store.get_invoice(invoice_id), "Dana")
    (event,) = store.events(actions=["separated"])
    assert (event["actor"], event["invoice_id"]) == ("Dana", invoice_id)
    assert describe(event) == ("a and 1 more.pdf (the receipts of one claim): a.pdf, b.jpg back in line, each read "
                               "as an invoice of its own")  # fmt: skip
    assert {"joined", "separated"} <= set(ACTIONS) and {"joined", "separated"} <= set(GROUPS["Invoices"])


def test_nothing_of_a_voucher_is_left_in_originals_once_it_is_separated_or_deleted(store, folder):
    """B-2289: the copy of the document as joined stayed after a separation (its pages had been set upright), and a
    deleted voucher left its files there, still a voucher's files."""
    first = receipt(folder, "scan_1.pdf", RECEIPTS["IMG_0411"])
    second = receipt(folder, "scan_2.jpg", RECEIPTS["IMG_0412"])
    made = join(store, folder, [first, second], PAGES)
    as_joined, copy = _sha(made), folder / "originals" / made.name
    copy.write_bytes(made.read_bytes())  # what setting its pages upright keeps (``pictures.turn_upright``)
    made.write_bytes(b"%PDF-1.4 its pages set upright")
    invoice_id = store.add_invoice(
        made,
        {"vendor_name": "Prairie Fuel Stop"},
        None,
        original_sha256=as_joined,
        meta={"original": {"file": made.name, "kept": str(copy), "sha256": as_joined}},
    )
    back = separate(store, store.get_invoice(invoice_id), "Dana")
    assert sorted(p.name for p in folder.iterdir()) == ["originals", "scan_1.pdf", "scan_2.jpg"]
    assert list((folder / "originals").iterdir()) == []
    # Deleted: its files go where a deleted invoice's file goes, and can come in again.
    made = join(store, folder, back, CLAIM)
    shas = [f["sha256"] for f in store.joined_files(_sha(made))]
    invoice_id = store.add_invoice(made, {"vendor_name": ""}, None)
    invoice, parts = store.get_invoice(invoice_id), store.joined_files(invoice_id=invoice_id)
    store.delete_invoice(invoice_id)
    removed(store, invoice, parts)
    assert list((folder / "originals").iterdir()) == [] and store.joined_files(_sha(made)) == []
    assert sorted(p.name for p in (folder / "deleted").iterdir()) == ["scan_1.pdf", "scan_2.jpg"]
    assert store.known_files(shas) == set() and all(store.joined_where(sha) is None for sha in shas)


def test_a_restore_brings_a_vouchers_files_back_with_it(store, folder):
    """A backup made while a voucher was joined holds its files in ``originals/``: restored after the voucher was
    separated, it is the voucher again, its files where they were kept, and it can be separated again."""
    first = receipt(folder, "a.pdf", RECEIPTS["IMG_0411"])
    second = receipt(folder, "b.jpg", RECEIPTS["IMG_0412"])
    shas = [_sha(first), _sha(second)]
    made = join(store, folder, [first, second], CLAIM)
    invoice_id = store.add_invoice(made, {"vendor_name": "", "grand_total": 97.4}, None)
    backup = store.backup_now("manual")
    separate(store, store.get_invoice(invoice_id), "Dana")
    assert not made.exists() and list((folder / "originals").iterdir()) == []
    store.restore_state(backup)
    files = store.joined_files(invoice_id=invoice_id)
    assert [f["name"] for f in files] == ["a.pdf", "b.jpg"] and made.is_file()
    assert [_sha(folder / f["kept"]) for f in files] == shas
    assert not first.exists() and not second.exists()  # as they were then: kept, not in the folder
    assert [_sha(p) for p in separate(store, store.get_invoice(invoice_id), "Dana")] == shas


def test_a_claim_is_still_a_claim_when_it_is_read_again_and_names_the_receipt_it_could_not_add(store, folder):
    """Read again (the page reader's pass, with each page's text): each receipt takes its own pages' text. A receipt
    in another currency adds nothing, and is named on the first reading and on every check after it."""
    from ap_coder.ai.pipeline import finalise_coding
    from ap_coder.core.schema import InvoiceCoding

    load_sample_setup(store)
    taxi = ["Airport Taxi Co", "1 Terminal Road, Seattle, WA 98158", "", "Receipt No: 5521", "Date: 2026-05-07", "",
            "Fare|45.00", "", "Total USD|45.00"]  # fmt: skip
    paths = [receipt(folder, "fuel.pdf", RECEIPTS["IMG_0411"]), receipt(folder, "taxi.pdf", taxi),
             receipt(folder, "room.pdf", RECEIPTS["IMG_0413"])]  # fmt: skip
    made = join(store, folder, paths, CLAIM)
    settings = replace(Settings(), llm=replace(Settings().llm, provider="off"))
    pipeline = InvoicePipeline(settings, store.reference_data(), store=store)
    first = pipeline.process(made)
    assert first.error is None and first.output["currency"] == "CAD"
    assert (first.output["subtotal"], first.output["tax_total"], first.output["grand_total"]) == (240.0, 26.4, 266.4)
    left_out = "taxi.pdf (page 2) is not in the claim's total: it is in USD (45.00) and the claim is in CAD."
    assert [i.message for i in first.report.issues if i.code == "CLAIM_RECEIPT_NOT_ADDED"] == [
        f"{left_out} Type its amount on its line, or leave it at 0.00."]  # fmt: skip
    assert [(li["description"], li["amount"]) for li in first.output["line_items"]] == [
        ("Prairie Fuel Stop: Regular fuel", 60.0), ("taxi.pdf: Airport Taxi Co 2026-05-07, 45.00 USD", 0.0),
        ("Lakeshore Inn: Room one night", 180.0)]  # fmt: skip
    with pymupdf.open(made) as doc:
        pages = [page.get_text() for page in doc]
    again = pipeline.process(made, page_text=pages, save=False, invoice_id=first.invoice_id)
    assert again.error is None and again.output["line_items"] == first.output["line_items"]
    assert sorted(i.code for i in again.report.issues) == sorted(i.code for i in first.report.issues)
    # The review screen's and the queue's checks say the same of it (``finalise_coding`` on the stored invoice).
    coding = InvoiceCoding.model_validate({k: v for k, v in first.output.items() if k != "gl_distribution"})
    _, report = finalise_coding(coding, store.reference_data(), settings, store=store,
                                exclude_invoice_id=first.invoice_id)  # fmt: skip
    said = {i.code: i.message for i in report.issues}
    assert said["CLAIM_RECEIPT_NOT_ADDED"].startswith(left_out) and "employee being repaid" in said["MISSING_VENDOR"]


def test_two_scanned_pages_joined_are_read_as_the_same_invoice_scanned_as_one_file(tmp_path, folder):
    """The pages of one invoice scanned one by one (a picture each) and joined, against the same two pictures a
    scanner saved as one PDF: the same header, lines and totals."""
    digital, subtotal, total = long_invoice(tmp_path, pages=2, per_page=4)
    one_file = pymupdf.open()
    scans = []
    with pymupdf.open(digital) as doc:
        for n, page in enumerate(doc, start=1):
            pix = page.get_pixmap(dpi=150)
            pix.set_dpi(150, 150)
            scans.append(folder / f"scan_{n}.png")
            pix.save(scans[-1])
            one_file.new_page(width=page.rect.width, height=page.rect.height).insert_image(page.rect, pixmap=pix)
    one_file.save(tmp_path / "both.pdf")
    one_file.close()
    settings = replace(Settings(), llm=replace(Settings().llm, provider="off"))
    read = []
    for name in ("joined", "one"):
        store = Store(tmp_path / name / "ap_coder.db")
        load_sample_setup(store)
        path = join(store, folder, scans, PAGES) if name == "joined" else tmp_path / "both.pdf"
        result = InvoicePipeline(settings, store.reference_data(), store=store).process(path)
        assert result.error is None and result.output["grand_total"] == total, result.error
        read.append((result.output, sorted(i.code for i in result.report.issues)))
        if name == "joined":
            assert [(f["name"], f["first_page"]) for f in store.joined_files(invoice_id=result.invoice_id)] == [
                ("scan_1.png", 1), ("scan_2.png", 2)]  # fmt: skip
    assert read[0] == read[1]
    assert read[0][0]["subtotal"] == subtotal and len(read[0][0]["line_items"]) == 8
