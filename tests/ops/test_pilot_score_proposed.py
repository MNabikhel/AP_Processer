"""``pilot-score --proposed``: what AP Coder proposed for invoices still in review against the vouchers keyed for
them. The made-up company's seven invoices (the ``chart_company`` fixture: read by the pipeline, nobody approved
them) with the vouchers AP would have keyed, put into a voucher list and a Transaction Journal made from the truth.
What AP Coder proposes for the seven is the pipeline's business: each verdict is checked against the invoice's own
proposal, and the tests that need a known proposal add an eighth invoice with one."""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import time
from dataclasses import replace
from decimal import Decimal
from types import SimpleNamespace

from ap_coder.bench import jde_company, jde_exports, jde_invoices
from ap_coder.core import chart
from ap_coder.core.reference_data import UNASSIGNED
from ap_coder.ops import pilot_score, proposed_score
from ap_coder.ops.proposed_score import AMOUNTS, LEFT, RIGHT, UNIT, WRONG

RATE = Decimal("1.37")  # the US-dollar voucher's
NEW_NUMBER = 990001  # the supplier number AP gave the supplier that was in no master
EIGHTH = "INV-21999"  # the invoice number of the invoice with a known proposal


def _voucher(truth, invoice: jde_invoices.Invoice, document: int, **changes) -> jde_company.Voucher:
    """The voucher AP keys for an invoice: its parts as the coding lines, one pay item with the recovered tax. A
    US-dollar one holds Canadian dollars at ``RATE`` and the invoice's own amounts as its foreign ones."""
    usd = invoice.currency != "CAD"
    rate = RATE if usd else Decimal(1)
    money = jde_company.money
    lines = tuple(jde_company.CodingLine(part.account, money(part.amount * rate), part.remark,
                                         part.amount if usd else None) for part in invoice.parts)  # fmt: skip
    tax = money(invoice.recovered * rate)
    gross = sum((line.amount for line in lines), Decimal(0)) + tax
    gl_date = invoice.date + dt.timedelta(days=3)
    due = invoice.date + dt.timedelta(days=0 if invoice.total < 0 else jde_invoices.TERMS.days)
    item = jde_company.PayItem("001", gross, gross - tax, tax, "V" if tax else "", invoice.tax_area if tax else "",
                               due, "P", invoice.total if usd else None)  # fmt: skip
    voucher = jde_company.Voucher(
        document=document, batch=8800, batch_date=gl_date, invoice_number=invoice.number, invoice_date=invoice.date,
        supplier=invoice.supplier.number if invoice.supplier else NEW_NUMBER, gl_date=gl_date,
        currency=invoice.currency, exchange_rate=RATE if usd else None, purchase_order="",
        business_unit=truth.company.main_business_unit, payable_object=truth.company.payable_object, lines=lines,
        pay_items=(item,), provincial_tax=Decimal(0), user="APCLERK", updated=dt.datetime(2025, 1, 14, 9, 0, 0),
    )  # fmt: skip
    return replace(voucher, **changes)


def _keyed(company, tmp, change=None) -> SimpleNamespace:
    """The voucher list and the journal of the seven invoices' vouchers, as files; ``change`` edits the vouchers
    ({name: voucher}) first. The books hold these vouchers alone, and the supplier AP set up for the new one."""
    vouchers = {name: _voucher(company.truth, invoice, 61000 + n)
                for n, (name, (invoice, _)) in enumerate(company.invoices.items())}  # fmt: skip
    if change:
        change(vouchers)
    new = jde_company.Supplier(NEW_NUMBER, jde_invoices.NEW_SUPPLIER.upper(), "trade", currency="USD")
    books = replace(company.truth, suppliers=[*company.truth.suppliers, new], vouchers=list(vouchers.values()))
    tmp.mkdir(parents=True, exist_ok=True)
    headers, journal = tmp / "headers.xlsx", tmp / "R09321.csv"
    headers.write_bytes(jde_exports.voucher_headers_xlsx(books))
    journal.write_bytes(jde_exports.journal_csv(books))
    return SimpleNamespace(headers=headers, journal=journal, vouchers=vouchers)


def _run(company, tmp, change=None):
    """(the report, its folder, {invoice number: the verdict of a voucher not wholly right})."""
    keyed = _keyed(company, tmp, change)
    report, folder = proposed_score.run(company.store, keyed.headers, keyed.journal, tmp / "out")
    verdicts = {m["invoice_number"]: m["what"] for m in report["misses"] if m["what"] in proposed_score.VERDICTS}
    return report, folder, verdicts


def _proposal(company, name: str) -> tuple[set[str], int]:
    """(the account strings AP Coder put on an invoice, how many lines it left), from the invoice itself."""
    lines = company.store.get_invoice(company.invoices[name][1])["ai_output"]["line_items"]
    coded = [li for li in lines if li["predicted_gl_code"] != UNASSIGNED]
    accounts = {chart.join_account(li["predicted_cost_center"], li["predicted_gl_code"]) for li in coded}
    return accounts, len(lines) - len(coded)


def _eighth(company, third: str = "") -> tuple[jde_invoices.Invoice, int]:
    """An eighth invoice in review, like ``four_lines`` (four lines, three accounts), which AP Coder proposed with
    the account of every line as AP keys it (``third``: its third line on this account instead): (the invoice, its
    id)."""
    invoice, like = company.invoices["four_lines"]
    output = json.loads(json.dumps(company.store.get_invoice(like)["ai_output"]))
    output.pop("gl_distribution", None)
    output["invoice_number"] = EIGHTH
    accounts = [part.account for part in invoice.parts]
    accounts[2] = third or accounts[2]
    for li, account in zip(output["line_items"], accounts, strict=True):
        li["predicted_cost_center"], li["predicted_gl_code"] = chart.split_account(account)
    return replace(invoice, number=EIGHTH), company.store.add_invoice(company.folder / "eighth.pdf", output, None)


def _with_eighth(company, relined=None):
    """A change of the vouchers that adds the eighth invoice's voucher, a day after ``four_lines``'s; ``relined``
    turns its coding lines into the ones keyed."""
    invoice = replace(company.invoices["four_lines"][0], number=EIGHTH)

    def change(vouchers):
        v = _voucher(company.truth, invoice, 61900)
        v = replace(v, gl_date=v.gl_date + dt.timedelta(days=1))
        vouchers["eighth"] = replace(v, lines=tuple(relined(list(v.lines)))) if relined else v

    return change


def test_the_seven_invoices_in_review_are_scored_against_their_vouchers(chart_company, tmp_path):
    began = time.perf_counter()
    report, folder, verdicts = _run(chart_company, tmp_path)
    print(f"\npilot-score --proposed on the seven invoices: {time.perf_counter() - began:.1f} s\n"
          f"{proposed_score.paragraph(report)}")  # fmt: skip
    inv = report["invoices"]
    assert (inv["ap_coder"], inv["in_review"], inv["in_voucher_list"], inv["scored"], inv["not_scored"]) == (
        7, 7, 7, 7, 0)  # fmt: skip
    assert inv["matched_by_amount"] == 0 and sum(report["vouchers"].values()) == 7
    # each voucher's verdict is what the invoice's own proposal says of it
    for name, (invoice, _) in chart_company.invoices.items():
        accounts, left = _proposal(chart_company, name)
        keyed = {part.account for part in invoice.parts}
        if not accounts <= keyed:
            assert verdicts[invoice.number] in (UNIT, WRONG), (name, verdicts)
        elif left:
            assert verdicts[invoice.number] == LEFT, (name, verdicts)
        elif accounts == keyed and len(invoice.parts) == len(invoice.lines):  # (no line AP splits)
            assert invoice.number not in verdicts, (name, verdicts)
    assert report["vouchers"][RIGHT] == 7 - len(verdicts)
    assert report["lines"]["coded"] + report["lines"]["left"] == sum(len(i.lines) for i, _ in
                                                                     chart_company.invoices.values())  # fmt: skip
    # the header fields the voucher list has; the US-dollar voucher in its own currency, not in Canadian dollars
    for name in ("invoice_number", "invoice_date", "grand_total", "currency", "due_date"):
        assert report["header"][name] == {"n": 7, "right": 7}, name
    # the tax JD Edwards posts itself (the journal's GST line) is the invoice's recovered tax
    assert report["tax"] == {"n": 7, "right": 7}

    text = (folder / proposed_score.REPORT).read_text(encoding="utf-8")
    said = proposed_score.paragraph(report)
    assert said in text and said.startswith("Of 7 invoices read by AP Coder here (7 of them not yet approved), 7 were "
                                            "found in the voucher list and in the journal.")  # fmt: skip
    for heading in ("## The coding, voucher by voucher", "## Header fields and tax", "## By supplier",
                    "## By business unit", "## The misses", "## Not scored"):  # fmt: skip
        assert heading in text
    rows = list(csv.DictReader(io.StringIO((folder / proposed_score.MISSES).read_text("utf-8-sig"))))
    assert len(rows) == len(report["misses"]) and all(list(row) == proposed_score.MISS_COLUMNS for row in rows)
    figures = json.loads((folder / proposed_score.FIGURES).read_text(encoding="utf-8"))
    assert figures["vouchers"] == report["vouchers"] and "misses" not in figures
    assert chart_company.store.list_invoices("approved") == []  # nobody had to approve anything


def test_a_voucher_is_compared_by_account_whatever_its_lines(chart_company, tmp_path):
    """The eighth invoice: four lines on three accounts (a, a, b, c), each proposed on the account AP keys."""
    truth = chart_company.truth
    _eighth(chart_company)
    a, _, b, c = (part.account for part in chart_company.invoices["four_lines"][0].parts)
    other_code = next(
        x.string
        for x in truth.suggestable()
        if chart.split_account(x.string)[1] not in {chart.split_account(y)[1] for y in (a, b, c)}
    )

    def as_one(lines):  # AP keyed the two lines of account a as one
        return [replace(lines[0], amount=lines[0].amount + lines[1].amount), *lines[2:]]

    report, _, verdicts = _run(chart_company, tmp_path / "keyed", _with_eighth(chart_company))
    assert EIGHTH not in verdicts and report["invoices"]["scored"] == 8
    before = report["vouchers"][RIGHT]
    report, _, verdicts = _run(chart_company, tmp_path / "one", _with_eighth(chart_company, as_one))
    assert EIGHTH not in verdicts and report["vouchers"][RIGHT] == before

    def moved(lines):  # the same accounts, 10.00 keyed on another of them
        return [replace(lines[0], amount=lines[0].amount - 10), lines[1],
                replace(lines[2], amount=lines[2].amount + 10), lines[3]]  # fmt: skip

    report, folder, verdicts = _run(chart_company, tmp_path / "moved", _with_eighth(chart_company, moved))
    assert verdicts[EIGHTH] == AMOUNTS and report["vouchers"][RIGHT] == before - 1
    miss = next(m for m in report["misses"] if m["invoice_number"] == EIGHTH)
    assert miss["ap_coder"] == f"{a} 811.00; {b} 420.00; {c} 96.25"  # both sides, account by account
    assert miss["keyed"] == f"{a} 801.00; {b} 430.00; {c} 96.25"
    rows = list(csv.DictReader(io.StringIO((folder / proposed_score.MISSES).read_text("utf-8-sig"))))
    assert any(row["what"] == AMOUNTS and row["keyed"] == miss["keyed"] for row in rows)

    def keyed_to(account):
        return lambda lines: [*lines[:2], replace(lines[2], account=account), lines[3]]

    report, _, verdicts = _run(chart_company, tmp_path / "code", _with_eighth(chart_company, keyed_to(other_code)))
    assert verdicts[EIGHTH] == WRONG and report["vouchers"][WRONG] >= 1 and report["lines"]["wrong"] >= 1
    assert f"on {report['vouchers'][WRONG]} an account is wrong" in proposed_score.paragraph(report)


def test_the_object_and_subsidiary_right_on_another_business_unit_is_counted_apart(chart_company, tmp_path):
    here, _, there = (part.account for part in chart_company.invoices["split_line"][0].parts)
    assert chart.split_account(here)[1] == chart.split_account(there)[1] and here != there
    _eighth(chart_company, third=here)  # proposed on one business unit, keyed on another
    keyed = _with_eighth(chart_company, lambda lines: [*lines[:2], replace(lines[2], account=there), lines[3]])
    report, _, verdicts = _run(chart_company, tmp_path, keyed)
    assert verdicts[EIGHTH] == UNIT and report["vouchers"][UNIT] == 1 and report["vouchers"][WRONG] == 0
    assert report["lines"]["unit_wrong"] == 1 and report["lines"]["wrong"] == 0
    assert "on 1 the object and subsidiary are right and a business unit is not" in proposed_score.paragraph(report)


def test_the_verdicts():
    x, y, z = "0021061010.7340.2210", "0021061012.7340.2210", "0021061010.7450.4513"
    assert proposed_score.verdict({x: 300.0}, [], {x: 300.0}) == RIGHT
    assert proposed_score.verdict({x: 300.0}, [], {x: 180.0, y: 120.0}) == AMOUNTS  # AP split it over two units
    assert proposed_score.verdict({x: 300.0}, [], {x: 299.0}) == AMOUNTS
    assert proposed_score.verdict({y: 300.0}, [], {x: 300.0}) == UNIT
    assert proposed_score.verdict({x: 200.0}, [{"amount": 100.0}], {x: 300.0}) == LEFT
    assert proposed_score.verdict({}, [{"amount": 300.0}], {x: 300.0}) == LEFT
    assert proposed_score.verdict({x: 300.0}, [], {x: 200.0, z: 100.0}) == WRONG  # an account AP Coder has no line on
    assert proposed_score.verdict({z: 300.0}, [{"amount": 1.0}], {x: 300.0}) == WRONG


def test_an_invoice_with_no_voucher_or_two_that_fit_is_listed_not_scored(chart_company, tmp_path):
    one = chart_company.invoices["one_line"][0]
    quebec = chart_company.invoices["quebec"][0]

    def none_and_two(vouchers):
        del vouchers["quebec"]  # never keyed
        twin = vouchers["one_line"]  # the same supplier, day and gross twice, coded two ways
        other = next(a.string for a in chart_company.truth.suggestable() if a.string != twin.lines[0].account)
        vouchers["twin"] = replace(twin, document=61999, invoice_number="60418",
                                   lines=(replace(twin.lines[0], account=other),))  # fmt: skip

    report, folder, _ = _run(chart_company, tmp_path, none_and_two)
    why = {u["invoice_number"]: u["why"] for u in report["unmatched"]}
    assert why == {quebec.number: proposed_score.NO_VOUCHER, one.number: proposed_score.EITHER}
    assert report["invoices"]["scored"] == 5 and report["invoices"]["not_scored"] == 2
    assert "2 invoices could not be scored" in proposed_score.paragraph(report)
    text = (folder / proposed_score.REPORT).read_text(encoding="utf-8")
    assert proposed_score.EITHER in text and proposed_score.NO_VOUCHER in text
    assert "not scored" in (folder / proposed_score.MISSES).read_text("utf-8-sig")


def test_the_tax_line_decides_between_two_vouchers_that_fit(chart_company, tmp_path):
    def twin_without_tax(vouchers):
        twin = vouchers["one_line"]
        gross = twin.pay_items[0].gross
        untaxed = replace(twin.pay_items[0], tax=Decimal(0), taxable=gross, tax_code="", tax_area="")
        other = next(a.string for a in chart_company.truth.suggestable() if a.string != twin.lines[0].account)
        vouchers["twin"] = replace(twin, document=61999, invoice_number="60418", pay_items=(untaxed,),
                                   lines=(replace(twin.lines[0], account=other, amount=gross),))  # fmt: skip

    assert chart_company.invoices["one_line"][0].recovered > 0
    report, _, _ = _run(chart_company, tmp_path, twin_without_tax)
    assert report["unmatched"] == [] and report["tax"] == {"n": 7, "right": 7}


def test_an_approved_invoice_is_scored_on_what_was_proposed_before_a_person_changed_it(chart_company, tmp_path):
    invoice, invoice_id = _eighth(chart_company)
    store = chart_company.store
    final = json.loads(json.dumps(store.get_invoice(invoice_id)["ai_output"]))
    other = next(a for a in chart_company.truth.suggestable() if a.string != invoice.parts[0].account)
    final["line_items"][0]["predicted_cost_center"], final["line_items"][0]["predicted_gl_code"] = (
        chart.split_account(other.string))  # fmt: skip
    store.approve_invoice(invoice_id, final, "Pat Tester")
    assert store.get_invoice(invoice_id)["status"] == "approved"
    report, _, verdicts = _run(chart_company, tmp_path, _with_eighth(chart_company))
    assert EIGHTH not in verdicts  # as proposed it was right: the person's change is not what is scored
    assert (report["invoices"]["ap_coder"], report["invoices"]["in_review"]) == (8, 7)


def test_the_command_scores_with_the_journal_loaded_here_and_says_the_paragraph(chart_company, tmp_path, capsys):
    from ap_coder.ops import cli, journal_cmd

    keyed = _keyed(chart_company, tmp_path)
    done, _ = journal_cmd.load(chart_company.store, keyed.journal.read_bytes(), keyed.journal.name)
    assert done == journal_cmd.DONE
    db = str(chart_company.store.path)
    assert cli.main(["--db", db, "pilot-score", "--proposed", "--vouchers", str(keyed.headers)]) == 0
    said = capsys.readouterr().out
    folder = chart_company.store.path.parent / pilot_score.FOLDER
    assert "Of 7 invoices read by AP Coder here" in said and str(folder / proposed_score.REPORT) in said
    assert json.loads((folder / proposed_score.FIGURES).read_text(encoding="utf-8"))["invoices"]["scored"] == 7
    assert cli.main(["--db", db, "pilot-score", "--proposed"]) == 2
    assert "--vouchers" in capsys.readouterr().err
