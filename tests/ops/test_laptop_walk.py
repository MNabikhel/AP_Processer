"""The fixes from an end-to-end walk of AP Coder on a laptop (B-1180 on): the demo with JD Edwards on, a rejected
invoice reopened, a duplicate pair after a reject, the vendors that make work, the email import counts, who to ask about
JD Edwards, the CSV exports, the approval limit, the review screen, the new vendor form and the setup report."""

from __future__ import annotations

import re

import pytest

from ap_coder.core.store import REVIEW
from ap_coder.erp import jde
from tests.browser import wait_until
from tests.web.test_web_review_signoff import _jde_on, browser, demo_db, me, store

__all__ = ["browser", "demo_db", "me", "store"]  # the fixtures, shared


def _plain(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


# --- L1. The demo with JD Edwards on -------------------------------------------------------------------------------


def test_the_demo_vendors_have_jd_edwards_address_numbers(client, store):
    """B-1180: ticking "Show JD Edwards settings" blocked every demo invoice: "supplier number V10104 is not an address
    number". The demo vendor master's numbers are JD Edwards address numbers; About says the checks come with it."""
    _jde_on(store)
    settings = jde.load(store)
    for inv in store.list_invoices(REVIEW):
        output = store.get_invoice(inv["id"])["ai_output"]
        codes = [f[1] for f in jde.voucher_findings(output, settings, jde.resolve_an8(output, store.vendor_ids(),
                                                                                       settings))]  # fmt: skip
        assert "JDE_BAD_AN8" not in codes, inv["vendor_name"]
    about = _plain(client.get("/settings/about").text)
    assert "adds the JD Edwards checks to every invoice" in about


# --- L2. A rejected invoice reopened -------------------------------------------------------------------------------


def test_a_rejected_invoice_reopened_speaks_of_the_rejection_and_its_reason(client, store):
    """B-1181: reopening a rejected invoice said "Reopened by X / no reason given. The approved coding is kept below":
    it was never approved, and the rejection's reason was not shown."""
    from ap_coder.core.audit import describe

    invoice_id = store.list_invoices(REVIEW)[0]["id"]
    store.reject_invoice(invoice_id, "Sam Reviewer", "Duplicate of NW-2026-0912")
    store.reopen(invoice_id, "Jordan Lee", only_rejected=True)
    page = _plain(client.get(f"/review/{invoice_id}").text)
    assert "Rejected by Sam Reviewer, reopened by Jordan Lee" in page
    assert "Duplicate of NW-2026-0912" in page and "no reason given" not in page
    assert "approved coding is kept" not in page
    reopened = next(e for e in store.events(invoice_id) if e["action"] == "reopened")
    assert describe(reopened) == "rejection undone, back to the review queue · rejected for: Duplicate of NW-2026-0912"


# --- L3. A duplicate pair after a reject or a delete ---------------------------------------------------------------


@pytest.mark.parametrize("how", ["reject", "delete"])
def test_rejecting_one_of_a_duplicate_pair_checks_the_other_again(store, tmp_path, how):
    """B-1182: rejecting one invoice of a duplicate pair left the other "Needs attention" (its DUPLICATE_INVOICE
    stored) and out of bulk approval until it was opened."""
    from ap_coder.coding.bulk import clean_candidates

    first = clean_candidates(store)[0]
    inv = store.get_invoice(first["id"])
    copy = store.add_invoice(tmp_path / "copy.pdf", inv["ai_output"], {**inv["validation"], "requires_review": True})
    for this, other in ((first["id"], copy), (copy, first["id"])):
        said = f"same vendor and invoice number as invoice #{other}: possible duplicate payment"
        flag = {"severity": "error", "code": "DUPLICATE_INVOICE", "line_number": None, "message": said}
        assert store.update_after_check(this, None, flag, ("DUPLICATE_INVOICE",))
    assert first["id"] not in [c["id"] for c in clean_candidates(store)]

    if how == "reject":
        store.reject_invoice(copy, "Sam Reviewer", "Sent twice")
    else:
        store.delete_invoice(copy, actor="Sam Reviewer")
    left = store.get_invoice(first["id"])
    assert "DUPLICATE_INVOICE" not in [i["code"] for i in left["validation"]["issues"]]
    assert not left["requires_review"] and first["id"] in [c["id"] for c in clean_candidates(store)]


# --- L4. Vendors that make work ------------------------------------------------------------------------------------


def test_vendors_that_make_work_leave_out_rejected_invoices_and_ap_coders_own_misreads(tmp_path):
    """B-1183: "Vendors that make work" counted rejected invoices, and findings the reviewer cleared by correcting what
    AP Coder read (its own misread, not the vendor's doing). An approved invoice counts what was still open when it was
    approved."""
    from ap_coder.core.store import Store
    from ap_coder.erp.insights import vendor_workload
    from tests.webclient import gt

    store = Store(tmp_path / "w.db")
    doc = gt()
    totals = {"issues": [{"severity": "error", "code": "SUBTOTAL_MISMATCH", "message": "x"}]}
    misread = store.add_invoice(tmp_path / "a.pdf", doc, totals)
    store.approve_invoice(misread, doc, "Jane")  # the subtotal corrected: nothing open when approved
    real = store.add_invoice(tmp_path / "b.pdf", {**doc, "invoice_number": "X-2"}, totals)
    store.approve_invoice(real, {**doc, "invoice_number": "X-2"}, "Jane",
                          open_issues=[{"code": "SUBTOTAL_MISMATCH", "severity": "error", "line": None}])  # fmt: skip
    rejected = store.add_invoice(tmp_path / "c.pdf", {**doc, "invoice_number": "X-3"},
                                 {"issues": [{"severity": "error", "code": "GST_HST_NUMBER_MISSING"}]})  # fmt: skip
    store.reject_invoice(rejected, "Jane", "Not ours")
    (row,) = vendor_workload(store)
    assert (row["invoices"], row["with_problems"], row["top_codes"]) == (2, 1, ["SUBTOTAL_MISMATCH"])


# --- L5. An email import's counts ----------------------------------------------------------------------------------


def test_an_email_whose_attachment_is_already_in_ap_coder_says_so_and_the_counts_are_true(tmp_path):
    """B-1184: an email whose attachment was already in AP Coder said "1 invoice to the review queue" though it was
    skipped, and "Processed 1 of 1" was shown for the three files uploaded."""
    from email.message import EmailMessage

    from ap_coder.core.store import Store, load_sample_setup
    from tests.conftest import SAMPLES
    from tests.web.test_web_process import PAGE, finished
    from tests.webclient import client_for

    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    client = client_for(store)
    first, other = (SAMPLES / f"{s}.pdf" for s in ("harbourview_NS_HST_HPS-2026-0347", "northwind_ON_HST_NW-2026-0912"))
    client.post("/process/upload", files=[("files", (first.name, first.read_bytes(), "application/pdf"))],
                headers=PAGE)  # fmt: skip
    finished(client.app, client)
    mail = EmailMessage()
    mail["Subject"], mail["From"] = "Invoice HPS-2026-0347", "ar@vendor.example"
    mail.set_content("Attached.")
    mail.add_attachment(first.read_bytes(), maintype="application", subtype="pdf", filename=first.name)
    files = [("files", ("again.eml", mail.as_bytes(), "message/rfc822")),
             ("files", (first.name, first.read_bytes(), "application/pdf")),
             ("files", (other.name, other.read_bytes(), "application/pdf"))]  # fmt: skip
    client.post("/process/upload", files=files, headers=PAGE)
    snap = finished(client.app, client)
    assert snap["result"]["label"] == "Processed 1 of 3: 2 already in AP Coder"
    (line,) = [entry["text"] for entry in snap["lines"] if entry["text"].startswith("again.eml")]
    assert "to the review queue" not in line and "1 attachment already in AP Coder" in line, line


# --- L6. Who to ask about the JD Edwards settings ------------------------------------------------------------------


def test_the_jd_edwards_setup_findings_say_where_to_set_it_or_whom_to_ask():
    """B-1185: "Ask the pilot tester…" in JD Edwards findings a clerk can fix in Settings → JD Edwards E1 (and on the
    server, where the pilot tester is no one): they say where to set it, or to ask whoever sets up AP Coder."""
    from ap_coder.ops.help import CHECKS
    from tests.erp.test_jde_gl_mode import coded, doc, line, tl

    told = "Settings → JD Edwards E1, or ask whoever sets up AP Coder"
    exempt = coded(doc([line(1, 100.0, taxes=["HST"]), line(2, 50.0, exempt=["HST"], status="exempt")],
                       [tl("HST", "ON", 0.13, 100.0, 13.0)]))["final_output"]  # fmt: skip
    settings = jde.JdeSettings(company="x", amount_mode=jde.AMOUNT_GROSS)
    found = {code: text for _, code, text, _ in jde.voucher_findings(exempt, settings, "V1")}
    for code in ("JDE_SETTINGS", "JDE_BAD_AN8", "JDE_GROSS_TAX"):
        assert told in found[code] and "pilot tester" not in found[code], found[code]
    for code in ("JDE_SETTINGS", "JDE_BAD_AN8", "JDE_MEALS_GROSS"):
        assert told in CHECKS[code].action and "pilot tester" not in CHECKS[code].action, CHECKS[code].action


# --- L7. The CSV exports -------------------------------------------------------------------------------------------


def test_the_csv_exports_show_plain_statuses_and_amounts_with_two_decimals(client, store):
    """B-1186: the directory CSV showed status codes ("approved") and the CSV exports amounts as 4350.0; the statuses
    are plain words, the amounts have two decimals, and Excel's amounts stay numbers."""
    import csv
    import io

    from openpyxl import load_workbook

    from ap_coder.core.store import APPROVED
    from ap_coder.erp import exports

    query = "col=vendor&col=total&col=status&f_status=approved"
    rows = list(csv.reader(io.StringIO(client.get(f"/search/export.csv?{query}").content.decode("utf-8-sig"))))
    assert rows[1][2] == "Approved" and re.fullmatch(r"-?\d+\.\d\d", rows[1][1]), rows[1]
    sheet = load_workbook(io.BytesIO(client.get(f"/search/export.xlsx?{query}").content)).active
    assert isinstance(sheet["B2"].value, float) and sheet["C2"].value == "Approved"
    invoices = [store.get_invoice(i["id"]) for i in store.list_invoices(APPROVED)]
    lines = list(csv.DictReader(io.StringIO(exports.build("csv", invoices)[0].decode("utf-8-sig"))))
    assert lines and all(re.fullmatch(r"-?\d+\.\d\d", r["Amount"]) for r in lines), [r["Amount"] for r in lines]
    assert "4350.00" in [r["Amount"] for r in lines]


# --- L8. A raised approval limit -----------------------------------------------------------------------------------


def test_raising_the_approval_limit_says_the_invoices_already_in_keep_the_strictest(client, store):
    """B-1187: raising the approval limit said only "Saved the second-approval limit", though the invoices already in
    keep the strictest limit since they arrived (B-970): the message says so; lowering it applies at once."""
    from tests.web.test_web_invoice import PAGE

    def saved(value: str) -> str:
        answer = client.post("/settings/review", data={"approval_limit": value}, headers=PAGE).json()
        return _plain(client.get(answer["redirect"]).text)

    assert "keep the strictest limit" not in saved("5000")
    assert "Invoices already in AP Coder keep the strictest limit in force since they arrived" in saved("15000")
    assert "keep the strictest limit" not in saved("1000")


# --- L9. The review screen: a split, the header's count, the address after an action ------------------------------


def test_a_split_puts_the_new_parts_under_the_line_split(reference):
    """B-1188: the new parts of a split line went to the end of the grid, away from the line they came from; they
    are under it, and keep their own numbers (the AI's lines keep theirs, D-71), with no LINE_NUMBERING warning."""
    import json

    import pandas as pd

    from ap_coder.ai.pipeline import finalise_coding
    from ap_coder.coding.review import coding_from_inputs, split_line
    from ap_coder.core.config import Settings
    from tests.conftest import SAMPLE_STEM, SAMPLES

    gt = json.loads((SAMPLES / "ground_truth" / f"{SAMPLE_STEM}.json").read_text())
    out = split_line(pd.DataFrame(gt["line_items"]), 2, [("6110", "", 60), ("6110", "", 40)])
    assert out["line_number"].tolist() == [1, 2, 6, 3, 4, 5]
    assert out["description"].tolist()[1:3] == [f"{gt['line_items'][1]['description']} ({p}%)" for p in (60, 40)]
    header = {k: gt[k] for k in gt if k not in ("line_items", "tax_lines", "confidence_score")}
    coding, _ = coding_from_inputs(header, out, pd.DataFrame(gt["tax_lines"]), gt)
    _, report = finalise_coding(coding, reference, Settings())
    assert "LINE_NUMBERING" not in {i.code for i in report.issues}


def test_the_headers_to_fix_count_is_the_blocking_items_listed(client, store):
    """B-1189: the header said "1 to fix" over the two items the checks listed as keeping Approve closed (a GL account
    to pick is a warning, not counted); it counts what the checks list."""
    from ap_coder.core.reference_data import UNASSIGNED
    from tests.web.test_web_invoice import check, clean_invoice, form_of

    invoice_id = clean_invoice(client, store)

    def blank(rows):
        rows[0]["predicted_gl_code"] = rows[1]["predicted_gl_code"] = UNASSIGNED
        return rows

    answer = check(client, invoice_id, form_of(store, invoice_id, blank, invoice_number=""))
    listed = sum(int(n) for n in re.findall(r"(?:Before you can approve|Must be fixed) \((\d+)\)",
                                            answer["sections"]["checks"]))  # fmt: skip
    said = re.search(r"(\d+) to fix", answer["sections"]["summary"])
    assert listed >= 2 and said and int(said.group(1)) == listed, (said and said.group(0), listed)


# --- Shared on the network, and the server's log ----------------------------------------------------------------


def test_shared_on_the_network_the_dashboard_says_colleagues_give_their_name(monkeypatch, capsys):
    """B-1191: shared on the office network, ``serve`` said "There is no login", though each colleague gives their
    name first (web/identity.py) and the company server has logins."""
    from ap_coder.ops import offline
    from tests.offline.test_offline import _serve_call

    monkeypatch.setenv(offline.ADDRESS_ENV, "0.0.0.0")
    _serve_call(monkeypatch)
    said = capsys.readouterr().err
    assert "There is no login" not in said and "give their name" in said and "server mode" in said


def test_on_the_server_report_a_problem_shows_the_services_log(tmp_path):
    """B-1192: on the company server Report a problem named the laptop's logs only; it names the service's log,
    ``<data folder>\\logs\\service.log``."""
    from ap_coder.core.store import Store, load_sample_setup
    from ap_coder.ops import service
    from ap_coder.web.core import data_dir, short_path
    from tests.web.test_server_role_pages import _server

    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    _, clients = _server(store)
    page = clients["admin"].get("/help/report").text
    assert short_path(service.log_path(data_dir(store))) in page and "launch.log" not in page


# --- L10. The Add new vendor form ----------------------------------------------------------------------------------


def test_in_a_browser_the_new_vendor_form_shows_no_error_before_anything_is_typed(browser, store):
    """B-1193: on an invoice whose vendor is flagged (not in the vendor list), the Add new vendor form's boxes had the
    flagged field's red border before anything was typed; only a refused add marks the name, until it is typed in."""
    from tests.web.test_web_review_signoff import _review

    page = _review(browser, store, "Cascade")
    page.locator("[data-vendor-add] summary").click()
    name = page.locator('[data-vendor-add] [data-new="name"]')
    border = "(el) => getComputedStyle(el).borderTopColor"
    plain = page.locator("[form=inv][name=invoice_number]").evaluate(border)
    boxes = [page.locator(f'[data-vendor-add] [data-new="{k}"]') for k in ("name", "gst")]
    assert [box.evaluate(border) for box in boxes] == [plain] * 2
    name.fill("")
    page.locator("[data-new-go]").click()
    wait_until(page, lambda: name.get_attribute("aria-invalid") == "true")
    assert name.get_attribute("aria-invalid") == "true" and name.evaluate(border) != plain
    name.type("Cascade")
    assert name.get_attribute("aria-invalid") is None


# --- L11. The doctor and the setup report --------------------------------------------------------------------------


def test_the_doctor_says_when_the_gl_accounts_are_the_bundled_sample(tmp_path, monkeypatch, capsys):
    """B-1194: with no GL accounts imported, the doctor said "GL accounts: 26 active rows" (and the same of the cost
    centers) from the bundled sample in data/: it says they are not imported yet, the bundled sample shown."""
    from ap_coder.ops import cli

    monkeypatch.setenv("AP_PRIVATE_DIR", str(tmp_path))
    monkeypatch.delenv("AP_REFERENCE_DIR", raising=False)
    cli.main(["--db", str(tmp_path / "ap_coder.db"), "doctor"])
    lines = {line.split("]", 1)[1].split("  ", 1)[0].strip(): line for line in capsys.readouterr().out.splitlines()
             if line.startswith("[")}  # fmt: skip
    for table in ("GL accounts", "cost centers"):
        assert "not imported yet (bundled sample shown)" in lines[table], lines[table]


def test_the_setup_report_has_the_lm_studio_and_page_reader_lines(client, store, monkeypatch):
    """B-1195: the dashboard's setup report (Report a problem) left out the LM Studio and page reader lines the doctor
    shows, the two a problem with reading most often comes from."""
    from ap_coder.ops import doctor
    from ap_coder.web.pages import help as help_page

    monkeypatch.setattr(doctor, "_local_check", lambda settings, provider: ("local model", "WARN", "not running"))
    monkeypatch.setattr(doctor, "_page_reader_check", lambda settings: ("page reader", "WARN", "not running"))
    report = help_page._make_setup_report(store)
    assert "local model" in report and "page reader" in report


def test_on_the_server_the_doctor_cant_see_https_given_on_the_command_line(tmp_path):
    """B-1196: a server started with ``serve --server --certfile … --keyfile …`` was reported "[FAIL] server HTTPS"
    by the doctor, which reads only server.json and the environment: it says what it read, and that it can't see a
    running server's command line."""
    from ap_coder.ops import service

    data = tmp_path / "APCoderData"
    data.mkdir()
    service.write_config(data, allowed_hosts=["apcoder.company.local"])
    checks = {area: (status, detail) for area, status, detail in service.doctor_checks(data, env={}, windows=False)}
    status, detail = checks["server HTTPS"]
    assert status == "WARN" and "server.json" in detail and "command line" in detail, checks["server HTTPS"]
