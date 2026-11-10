"""The hard cases of a department's day, on the made-up company (the ``chart_company`` fixture; the invoices are
``bench.jde_invoices``' ``hard_cases``, ``printed_cases`` and ``other_documents``): for each, what arrives coded, what
is left for a person, the EXACT reason the person sees (the line's, the finding's, the queue's one sentence) and
that nothing wrong is coded without a word.

No chat model and no page reader here (the fixture has neither): what is read is the rule reader's reading of a
digital PDF, what is coded is last year's journal. A case today's code gets wrong outside this file's own fixes is
``xfail(strict=True)`` with what is wrong and where: it fails the run the day it starts passing."""

import dataclasses
import datetime as dt
import logging

import pymupdf
import pytest

from ap_coder.ai.pipeline import InvoicePipeline
from ap_coder.bench import jde_exports, jde_invoices
from ap_coder.coding.tax import build_gl_distribution, meals_note
from ap_coder.core import chart
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import APPROVED, FAILED, REJECTED, REVIEW
from tests.pdfs import long_invoice
from tests.review_page import ReviewPage, queue_reasons
from tests.web.test_chart_company_walk import NEW_SUPPLIER, SEVERAL, TOO_FEW
from tests.webclient import post, web_settings

ON = dt.date.today() - dt.timedelta(days=1)  # the day the cases are dated: the fixture's books are last year's
NOT_INVOICE = ("this may not be an invoice: its title is \"{title}\". If it is not one, reject it (Reject, with the "
               "reason); if it is, check it as any other")  # fmt: skip
DUPLICATE = ("same vendor and invoice number as invoice #{first} (received first, {state}): possible duplicate "
             "payment. If it is the same bill, reject this one")  # fmt: skip
GONE = ("The supplier's usual account {account} is no longer in the chart (the chart has no account {code}): pick "
        "the account.")  # fmt: skip
WORKED_OUT = " Tax code worked out from the tax charged: check it."


class Arrived:
    """A file dropped in, as a person finds it: ``status``, the proposal's ``output`` and ``lines``, the findings
    (``found``: {code: its words}, ``severity``), and the queue's one sentence for it (``queue``)."""

    def __init__(self, store, client, result) -> None:
        self.result, self.id = result, result.invoice_id
        invoice = store.get_invoice(self.id)
        self.status = invoice["status"]
        self.output = invoice["ai_output"] or {}
        self.lines = self.output.get("line_items") or []
        issues = (invoice["validation"] or {}).get("issues") or []
        self.found = {i["code"]: i["message"] for i in reversed(issues)}
        self.severity = {i["code"]: i["severity"] for i in issues}
        self.queue = queue_reasons(client).get(self.id, "") if self.status == REVIEW else ""

    @property
    def accounts(self) -> list[str]:
        """Each line's account string as it arrived ("": left for a person)."""
        return ["" if li["predicted_gl_code"] == "UNASSIGNED"
                else chart.join_account(li["predicted_cost_center"], li["predicted_gl_code"])
                for li in self.lines]  # fmt: skip

    @property
    def reasons(self) -> list[str]:
        return [li["reasoning_justification"] for li in self.lines]

    @property
    def offered(self) -> list[list[str]]:
        return [[c["account"] for c in li.get("candidates") or []] for li in self.lines]


@pytest.fixture
def drop(chart_company, chart_company_client):
    """Read a file as the fixture's seven were (the pipeline, on the made-up company's database): its ``Arrived``."""

    def read(path) -> Arrived:
        store = chart_company.store
        result = InvoicePipeline(web_settings(), store.reference_data(), store=store).process(path)
        return Arrived(store, chart_company_client, result)

    return read


@pytest.fixture
def hard(chart_company, tmp_path):
    """{name: (the invoice with what AP should key, its PDF)} of ``jde_invoices.HARD``."""
    made = jde_invoices.hard_cases(chart_company.truth, ON)
    cases = jde_invoices.write(tmp_path / "hard", chart_company.truth, made)
    return {invoice.name: (invoice, case.path) for invoice, case in zip(made, cases, strict=True)}


@pytest.fixture
def printed(chart_company, tmp_path):
    """{name: its PDF} of ``jde_invoices.PRINTED``."""
    out = {}
    for name, data in jde_invoices.printed_cases(chart_company.truth, ON).items():
        out[name] = tmp_path / f"{name}.pdf"
        out[name].write_bytes(data)
    return out


def _one(chart_company):
    """The supplier that always goes to one account (the fixture's one-line invoice's), and that account."""
    supplier = chart_company.invoices["one_line"][0].supplier
    return supplier, supplier.accounts[0]


def _sentence(words: str) -> str:
    """A finding's words as the queue says them."""
    return words[0].upper() + words[1:].rstrip(".")


def _left_line(reason: str, line: int = 1) -> str:
    """The queue's sentence for an invoice whose first line without an account is ``line``, left for ``reason``."""
    words = reason.rstrip(".")
    return f"Line {line}: {words[0].lower()}{words[1:]}"


# --- (a) (b) (c) long invoices ------------------------------------------------------------------------------------


def test_a_25_pages_with_the_totals_on_the_last_page(chart_company, drop, tmp_path):
    supplier, account = _one(chart_company)
    path, subtotal, total = long_invoice(tmp_path, 25, per_page=4, vendor=jde_invoices.printed_name(supplier.name),
                                         number="QC-25001", date=ON.isoformat())  # fmt: skip
    got = drop(path)
    assert (got.output["subtotal"], got.output["grand_total"]) == (subtotal, total)  # printed on page 25
    assert len(got.lines) == 100 and round(sum(li["amount"] for li in got.lines), 2) == subtotal
    assert got.accounts == [account] * 100  # every line of the supplier that always goes to one account
    assert "PAGES_NOT_READ" not in got.found and not [c for c, s in got.severity.items() if s != "info"]
    assert got.queue == ""  # Ready


def test_b_past_60_pages_the_pages_not_read_are_named(chart_company, drop, tmp_path):
    supplier, account = _one(chart_company)
    path, subtotal, total = long_invoice(tmp_path, 62, per_page=2, vendor=jde_invoices.printed_name(supplier.name),
                                         number="QC-62001", date=ON.isoformat())  # fmt: skip
    got = drop(path)
    said = (
        "pages 56 and 57 of its 62 pages were not read (of a file longer than 60 pages the first 55 and the last "
        "5 are read): nothing on them is in the lines or totals shown; check them yourself before approving"
    )
    assert (got.severity["PAGES_NOT_READ"], got.found["PAGES_NOT_READ"]) == ("warning", said)
    assert got.queue == _sentence(said)
    # The totals are the last page's, as printed; what pages 56 and 57 bill is not among the lines read, and is not
    # spread over them: it is a line of its own that says so.
    assert (got.output["subtotal"], got.output["grand_total"]) == (subtotal, total)
    unread = sum(1025 + (n * 7) % 90 * 100 for n in range(111, 115)) / 100  # lines 111 to 114, as ``long_invoice``
    assert len(got.lines) == 121
    assert (got.lines[-1]["description"], got.lines[-1]["amount"]) == ("Charges not in the line table", unread)
    assert got.accounts[:120] == [account] * 120


def test_c_a_60_line_invoice(chart_company, drop, hard):
    invoice, path = hard["sixty_lines"]
    got = drop(path)
    assert [(li["description"], li["amount"]) for li in got.lines] == [
        (line.description, float(line.amount)) for line in invoice.lines]  # fmt: skip
    assert len(got.lines) == 60 and got.output["grand_total"] == float(invoice.total)
    assert got.accounts == [part.account for part in invoice.parts]
    assert set(got.reasons) == {"8 of this supplier's 8 lines last year"}
    assert got.queue == "" and not [c for c, s in got.severity.items() if s != "info"]


# --- (d) (e) a credit note, a USD invoice -------------------------------------------------------------------------


def test_d_a_credit_note(chart_company, chart_company_client):
    invoice, invoice_id = chart_company.invoices["credit_note"]
    stored = chart_company.store.get_invoice(invoice_id)
    page = ReviewPage(chart_company_client, invoice_id)
    assert stored["ai_output"]["grand_total"] == float(invoice.total) < 0
    reason = TOO_FEW.format(counted="2 of this supplier's 2 lines", account=invoice.parts[0].account)
    assert [(row.account, row.reason) for row in page.lines] == [("", reason)]  # offered, never put in
    assert queue_reasons(chart_company_client)[invoice_id] == _left_line(reason)
    found = {i["code"]: i["message"] for i in stored["validation"]["issues"]}
    said = found["CREDIT_NOTE_ORIGINAL_UNKNOWN"]  # (the invoice it credits is the one its page names)
    assert said.startswith("credits invoice ") and said.endswith(
        ", which is not in AP Coder or the ERP register: check it was billed"
    )
    assert not page.approve()["ok"] and chart_company.store.get_invoice(invoice_id)["status"] == REVIEW


def test_e_a_usd_invoice(chart_company, drop, hard):
    invoice, path = hard["usd"]
    got = drop(path)
    assert (got.output["currency"], got.output["grand_total"], got.output["tax_total"]) == ("USD", 1630.0, 0.0)
    reason = SEVERAL.format(lines=4, accounts=2, shares="50% / 50%")
    assert (got.accounts, got.reasons) == (["", ""], [reason, reason])
    assert all(set(offered[:2]) == set(invoice.supplier.accounts) for offered in got.offered)
    # (A US supplier's invoice is checked for Canadian tax like a domestic one: the audit's finding 16, for the user.)
    assert got.found["NO_TAX_CHARGED"] == (
        "no sales tax charged on a supply in Alberta; confirm it is exempt or zero-rated, or that the vendor is not "
        "registered for GST/HST (a small supplier, no GST/HST number printed: no GST/HST is due)"
    )
    assert got.queue == _left_line(reason) + " (and 1 more)"


# --- (f) (t) duplicates -------------------------------------------------------------------------------------------


def test_f_a_duplicate_of_an_approved_invoice(chart_company, chart_company_client, drop, tmp_path):
    invoice, first = chart_company.invoices["one_line"]
    assert ReviewPage(chart_company_client, first).approve()["ok"]
    assert chart_company.store.get_invoice(first)["status"] == APPROVED
    again = jde_invoices.write(tmp_path / "again", chart_company.truth,
                               [dataclasses.replace(invoice, name="sent_again")])[0]  # fmt: skip
    got = drop(again.path)
    today = dt.date.today()
    said = DUPLICATE.format(first=first, state=f"approved on {today:%b} {today.day}")
    assert (got.severity["DUPLICATE_INVOICE"], got.found["DUPLICATE_INVOICE"]) == ("error", said)
    assert got.queue == _sentence(said)
    assert not ReviewPage(chart_company_client, got.id).approve()["ok"]  # not in one click


def test_t_the_same_file_dropped_twice(chart_company, drop, tmp_path):
    first = chart_company.invoices["four_lines"][1]
    path = chart_company.folder / "invoices" / "four_lines.pdf"
    again = drop(path)
    said = DUPLICATE.format(first=first, state="waiting for review")
    assert again.id != first and (again.severity["DUPLICATE_INVOICE"], again.found["DUPLICATE_INVOICE"]) == (
        "error", said)  # fmt: skip
    assert again.queue.startswith(_sentence(said))
    renamed = tmp_path / "copy of four_lines.pdf"
    renamed.write_bytes(path.read_bytes())
    assert (
        drop(renamed)
        .found["DUPLICATE_INVOICE"]
        .startswith(
            f"same vendor and invoice number as invoice #{first} (received first, waiting for review), #{again.id} "
        )
    )


# --- (g) not an invoice -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "title"), [("statement", "Statement of account"),
                                             ("remittance_advice", "Remittance advice")])  # fmt: skip
def test_g_a_document_that_is_not_an_invoice(chart_company, chart_company_client, drop, tmp_path, name, title):
    path = tmp_path / f"{name}.pdf"
    path.write_bytes(jde_invoices.other_documents(chart_company.truth, ON)[name])
    got = drop(path)
    said = NOT_INVOICE.format(title=title)
    assert (got.severity["NOT_AN_INVOICE"], got.found["NOT_AN_INVOICE"]) == ("warning", said)
    assert got.queue.startswith(_sentence(said))  # said before anything else there is to fix
    assert got.status == REVIEW  # never rejected without a person
    answer = post(chart_company_client, f"/review/{got.id}/reject", {"reason": f"a {title.lower()}, not an invoice"})
    assert answer.status_code == 200 and chart_company.store.get_invoice(got.id)["status"] == REJECTED


# --- (h) to (m) the supplier --------------------------------------------------------------------------------------


def test_h_a_new_supplier(chart_company, chart_company_client):
    invoice, invoice_id = chart_company.invoices["usd_new_supplier"]
    page = ReviewPage(chart_company_client, invoice_id)
    assert [(row.account, row.reason) for row in page.lines] == [("", NEW_SUPPLIER)]
    said = ("this vendor is not in the vendor list imported from the ERP: pick it from the list, or add it as a new "
            "vendor")  # fmt: skip
    assert page.findings["VENDOR_NOT_IN_MASTER"] == _sentence(said)
    assert queue_reasons(chart_company_client)[invoice_id] == _sentence(said) + " (and 4 more)"
    assert not page.approve()["ok"]


def test_i_a_supplier_with_several_accounts(chart_company, chart_company_client):
    invoice, invoice_id = chart_company.invoices["four_lines"]
    stored = chart_company.store.get_invoice(invoice_id)["ai_output"]["line_items"]
    reason = SEVERAL.format(lines=3, accounts=3, shares="33% / 33% / 33%")
    assert [(li["predicted_gl_code"], li["reasoning_justification"]) for li in stored] == [("UNASSIGNED", reason)] * 4
    for li in stored:  # its accounts of last year are offered first, none put in
        assert {c["account"] for c in li["candidates"][:3]} == set(invoice.supplier.accounts[:3])
    assert queue_reasons(chart_company_client)[invoice_id] == _left_line(reason)


def test_j_a_supplier_marked_do_not_use(chart_company, chart_company_client, drop, hard):
    invoice, path = hard["do_not_use"]
    assert "DO NOT USE" in invoice.supplier.name and "DO NOT USE" not in invoice.supplier_name.upper()
    got = drop(path)
    said = "this vendor is on hold in the vendor list: marked DO NOT USE in the supplier master"
    assert (got.severity["VENDOR_ON_HOLD"], got.found["VENDOR_ON_HOLD"]) == ("error", said)
    assert got.queue.startswith(_sentence(said))
    assert got.accounts == [""] and got.reasons == [SEVERAL.format(lines=8, accounts=3, shares="62% / 25% / 12%")]
    assert not ReviewPage(chart_company_client, got.id).approve()["ok"]


def test_k_a_supplier_on_payment_hold(drop, hard):
    invoice, path = hard["on_hold"]
    got = drop(path)
    assert (got.severity["VENDOR_PAYMENTS_HELD"], got.found["VENDOR_PAYMENTS_HELD"]) == ("warning", (
        f"JD Edwards holds this supplier's payments (hold payment code {invoice.supplier.hold_payment}: payments "
        "held, new vouchers still allowed): the voucher can be entered, and is paid once the hold is lifted in JD "
        "Edwards"))  # fmt: skip
    reason = SEVERAL.format(lines=2, accounts=2, shares="50% / 50%")
    assert (got.accounts, got.reasons) == ([""], [reason])
    assert got.queue == _left_line(reason) + " (and 1 more)"


def test_l_two_suppliers_of_one_name(chart_company, drop, hard):
    """The supplier master as exported again, with a second supplier under the first one's name: the invoice names
    neither number, so a person picks."""
    from ap_coder.coding.vendors import master_columns, master_rows
    from ap_coder.web.files import read_sheet

    store, truth = chart_company.store, chart_company.truth
    supplier, _ = _one(chart_company)
    twin = dataclasses.replace(supplier, number=991177)
    sheet, _ = read_sheet(jde_exports.SUPPLIERS_FILE, jde_exports.suppliers_xlsx(
        dataclasses.replace(truth, suppliers=[*truth.suppliers, twin])))  # fmt: skip
    store.import_vendor_master(master_rows(sheet.to_dict("records"), master_columns(list(sheet.columns))), actor="AP")
    got = drop(hard["odd_line"][1])
    printed = jde_invoices.printed_name(supplier.name)
    said = (f"2 suppliers in the vendor list read “{printed}”: pick one from the list, each has its own supplier "
            f"number: {supplier.number} {supplier.name} (4 vouchers in last year's journal, the most); 991177 "
            f"{supplier.name}")  # fmt: skip
    assert (got.severity["VENDOR_NOT_IN_MASTER"], got.found["VENDOR_NOT_IN_MASTER"]) == ("error", said)
    assert got.queue.startswith(_sentence(said))


def test_m_an_account_that_left_the_chart_since_last_year(chart_company, drop, hard):
    """This year's chart, imported again without the account the supplier's lines all went to last year: the lines
    are left, the reason says the account is gone, and it is not among the accounts offered."""
    from ap_coder.ops import chart_cmd

    store, truth = chart_company.store, chart_company.truth
    supplier, gone = _one(chart_company)
    chart_cmd.import_files(store, jde_exports.chart_xlsx(
        dataclasses.replace(truth, accounts=[a for a in truth.accounts if a.string != gone])), actor="AP")  # fmt: skip
    got = drop(hard["odd_line"][1])
    reason = GONE.format(account=gone, code=chart.split_account(gone)[1])
    assert (got.accounts, got.reasons) == (["", ""], [reason, reason])
    assert not any(gone in offered for offered in got.offered)
    assert got.queue == _left_line(reason)


# --- (n) to (q) tax -----------------------------------------------------------------------------------------------


def test_n_gst_and_a_provincial_tax_kept_in_the_expense(chart_company, chart_company_client, drop, hard):
    invoice, path = hard["bc_pst"]
    got = drop(path)
    assert [(t["tax_type"], t["tax_amount"]) for t in got.output["tax_lines"]] == [
        (tax.code, float(tax.amount)) for tax in invoice.taxes] == [("GST", 27.5), ("PST", 38.5)]  # fmt: skip
    assert [row.tax for row in ReviewPage(chart_company_client, got.id).lines] == ["GST + PST"] * 2
    assert [li["amount"] for li in got.lines] == [400.0, 150.0]  # as printed, before tax
    # As posted: the PST in each line's expense (what AP keys), the GST alone on the tax account.
    coding = InvoiceCoding.model_validate({k: v for k, v in got.output.items() if k != "gl_distribution"})
    posted = build_gl_distribution(coding, chart_company.store.reference_data().tax)
    assert [e["amount"] for e in posted if e["kind"] == "expense"] == [float(part.amount) for part in invoice.parts]
    assert [e["amount"] for e in posted if e["kind"] == "tax"] == [float(invoice.recovered)] == [27.5]


TWO_AREAS = "JD Edwards: GST 5% and HST"


def test_o_lines_for_two_provinces_are_held(chart_company_client, drop, printed):
    got = drop(printed["two_provinces"])
    assert got.severity["JDE_TAX_AREA"] == "error"
    held = got.found["JDE_TAX_AREA"]
    assert (
        held.startswith(TWO_AREAS)
        and (
            "are both charged (lines supplied in two provinces): a pay item has one tax area, so this voucher is two "
            "pay items, to key by hand with the G/L lines as coded. Pay item 001: "
        )
        in held
    )
    assert got.queue.startswith(TWO_AREAS)
    assert not ReviewPage(chart_company_client, got.id).approve()["ok"]  # no "Approve anyway" for it


@pytest.mark.xfail(strict=True, reason="the two pay items are spelled out with the wrong figures: each tax line's "
                   "taxable amount is the whole subtotal (1,700.00), though the page prints \"GST 5% on 1,200.00\" "
                   "and \"HST 13% on 500.00\", so the HST's rate reads 3.824%, its lines 1, 2, 3 and its tax area "
                   "Alberta's, and \"HST is not levied in Alberta\" is raised. Where: the tax lines' taxable amounts "
                   "(the reading and coding/tax.py) and erp/jde.py's _two_tax_areas; not this file's.")  # fmt: skip
def test_o_the_two_pay_items_are_spelled_out_as_printed(drop, printed):
    got = drop(printed["two_provinces"])
    held = got.found["JDE_TAX_AREA"]
    assert "taxable amount 1,200.00, tax amount 60.00 (GST 5%: lines 1, 2)" in held
    assert "taxable amount 500.00, tax amount 65.00 (HST 13%: line 3)" in held
    assert "TAX_TYPE_NOT_LEVIED" not in got.found


def test_p_some_lines_exempt(drop, printed):
    got = drop(printed["exempt_line"])
    assert [(t["tax_type"], t["tax_amount"]) for t in got.output["tax_lines"]] == [("GST", 75.0)]
    assert [li["taxes_applied"] for li in got.lines] == [["GST"], ["GST"], []]  # the third: no tax, as printed
    account = "0021061010.7620.6207"
    reason = TOO_FEW.format(counted="2 of this supplier's 2 lines", account=account)
    assert got.accounts == ["", "", ""]
    assert got.reasons == [reason, reason, reason + WORKED_OUT]  # the exempt line says its tax code is to check
    assert got.queue.startswith(_left_line(reason))


@pytest.mark.xfail(strict=True, reason="two findings that are not so: \"GST rate 4.286% differs from the official "
                   "5%\" and, on the exempt line, \"the GST charged adds up only with this line\". The page prints "
                   "\"GST 5% on 1,500.00\"; the tax line's taxable amount is taken as the whole subtotal (1,750.00). "
                   "Where: the tax lines' taxable amounts (the reading and coding/tax.py, coding/line_tax.py); not "
                   "this file's.")  # fmt: skip
def test_p_an_exempt_line_raises_no_finding_that_is_not_so(drop, printed):
    got = drop(printed["exempt_line"])
    assert "TAX_RATE_NONSTANDARD" not in got.found and "EXEMPT_LINE_TAXED" not in got.found


def test_q_a_meals_line(chart_company, chart_company_client, drop, hard):
    """A lunch on a courier's invoice, coded by a person to the meals account (marked as one, as Settings does):
    half its GST is claimed and the other half stays in its expense, as the screen's note on the line says
    (``meals_note``)."""
    store = chart_company.store
    meals = next(a for a in chart_company.truth.suggestable() if a.description == "Meals - Travel")
    code = chart.split_account(meals.string)[1]
    store.save_accounts("gl_accounts", [{**row, "meals": row["code"] == code}
                                        for row in store.list_accounts("gl_accounts")])  # fmt: skip
    page = ReviewPage(chart_company_client, drop(hard["odd_line"][1]).id)
    page.set_account(2, meals.string)
    assert page.check()["allow"] and page.lines[1].account == meals.string
    assert meals_note("AB") == ("Meals: 50% of the GST/HST is claimable. Billed to a client or a staff event? Use "
                                "the other account.")  # fmt: skip
    assert meals_note("AB") in page.text
    assert page.approve()["ok"]
    posted = store.get_invoice(page.id)["final_output"]["gl_distribution"]
    # 64.20 and 186.40 with 3.21 and 9.32 of GST: half the lunch's 9.32 (4.66) is not claimed, and is its expense.
    assert [e["amount"] for e in posted if e["kind"] == "expense"] == [64.2, 191.06]
    assert [e["amount"] for e in posted if e["kind"] == "tax"] == [7.87]


@pytest.mark.xfail(strict=True, reason="coded without a word: \"Working lunch for the project kickoff, 12 people\" "
                   "on the courier's invoice arrives on the courier account (\"8 of this supplier's 8 lines last "
                   "year\") and the invoice is Ready. A supplier whose lines all went to one account last year has "
                   "every line coded there whatever its words. Where: ai/supplier_prior.py and ai/suggest.py; not "
                   "this file's.")  # fmt: skip
def test_q_a_lunch_on_a_couriers_invoice_is_not_coded_as_courier(chart_company, drop, hard):
    _, account = _one(chart_company)
    got = drop(hard["odd_line"][1])
    assert got.accounts[0] == account and got.accounts[1] != account
    assert got.queue != ""


# --- (r) line tables ----------------------------------------------------------------------------------------------
# Only that each line's amount is the one before tax, or that a finding says the lines do not add up: the reader's
# rules for a tax column and a "#" column are tested in tests/capture (the reader's own tests cover the rest).


@pytest.mark.parametrize(("name", "amounts"), [("tax_column", [100.0, 200.0]), ("hash_column", [100.0])])
def test_r_a_line_tables_amounts_are_the_ones_before_tax(chart_company, drop, printed, name, amounts):
    got = drop(printed[name])
    raised = {"SUBTOTAL_MISMATCH", "TOTAL_MISMATCH", "LINE_ITEMS_AGREE", "CAPTURE_TOTALS"} & set(got.found)
    assert [li["amount"] for li in got.lines] == amounts or raised
    assert got.output["grand_total"] == round(sum(amounts) * 1.05, 2) or raised


# --- (s) files that cannot be read --------------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "said"), [
    ("locked.pdf", "locked.pdf is protected by a password, so it can't be read. Ask the vendor for a copy without "
                   "one."),
    ("empty.pdf", "empty.pdf can't be opened as a PDF: the file is damaged or incomplete. Ask the vendor for a new "
                  "copy."),
])  # fmt: skip
def test_s_a_file_that_cannot_be_read_says_one_plain_line(chart_company, drop, tmp_path, caplog, name, said):
    path = tmp_path / name
    if name == "empty.pdf":
        path.write_bytes(b"")
    else:
        with pymupdf.open(chart_company.folder / "invoices" / "one_line.pdf") as doc:
            doc.save(path, encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")
    with caplog.at_level(logging.INFO):  # (the error as it was raised is kept at debug level, for a developer)
        got = drop(path)
    assert (got.result.ok, got.result.error, got.status) == (False, said, FAILED)
    assert chart_company.store.get_invoice(got.id)["error"] == said
    assert not [r for r in caplog.records if r.exc_info or "Traceback" in r.getMessage()]  # no traceback
    assert said in [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
