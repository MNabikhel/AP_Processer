"""Meals and entertainment (D-90): only half the GST/HST (and QST) paid on them is claimable (ITA s.67.1, ETA s.236);
the other half is expensed with the line, in the posting, the JD Edwards voucher, the Excel/CSV export and the
sales-tax report."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from ap_coder.ai.pipeline import finalise_coding
from ap_coder.coding import taxreturn
from ap_coder.coding.tax import TaxRateTable, TaxSetup, build_gl_distribution, load_tax_mapping
from ap_coder.core.config import Settings
from ap_coder.core.reference_data import load_reference_data
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import Store, load_sample_setup
from ap_coder.erp import exports, jde

from .conftest import DATA, SAMPLES

ON, QC, BC = "northwind_ON_HST_NW-2026-0912", "montroyal_QC_TPS_TVQ_ACMR-2026-1187", "pacific_BC_GST_PST_PO-77120"
MEALS = "6320"
YEAR = (dt.date(2020, 1, 1), dt.date(2030, 12, 31))


def _gt(stem):
    return json.loads((SAMPLES / "ground_truth" / f"{stem}.json").read_text())


def _setup(meals=(MEALS,)):
    return TaxSetup(TaxRateTable.load(DATA / "canada_tax_rates.csv"), load_tax_mapping(DATA / "tax_gl_mapping.csv"),
                    meals_gl_codes=frozenset(meals))  # fmt: skip


def _on(lines, hst):
    """An Ontario invoice of ``lines`` [(amount, gl, description)], all charged HST, ``hst`` as printed."""
    gt = _gt(ON)
    item = gt["line_items"][0]
    gt["line_items"] = [
        {**item, "line_number": n, "quantity": 1, "unit_price": a, "amount": a, "predicted_gl_code": gl,
         "description": text}
        for n, (a, gl, text) in enumerate(lines, 1)
    ]  # fmt: skip
    subtotal = round(sum(a for a, _, _ in lines), 2)
    gt["tax_lines"] = [{"tax_type": "HST", "province": "ON", "rate": 0.13, "taxable_amount": subtotal,
                        "tax_amount": hst}]  # fmt: skip
    gt.update(subtotal=subtotal, tax_total=hst, grand_total=round(subtotal + hst, 2))
    return gt


def _dist(doc, setup=None):
    return build_gl_distribution(InvoiceCoding.model_validate(doc), setup or _setup())


def _split(dist):
    expense = [(e["gl_code"], e["non_recoverable_tax"], e["amount"]) for e in dist if e["kind"] == "expense"]
    tax = [(e["gl_code"], e["amount"]) for e in dist if e["kind"] == "tax"]
    return expense, tax


# --- The arithmetic ------------------------------------------------------------------------------------------------


def test_hst_13_on_a_meal_half_is_expensed_with_the_line():
    """HST 11.37 on a 87.43 dinner: 5.68 is claimable (half, never rounded up), 5.69 is expensed with the line."""
    doc = _on([(87.43, MEALS, "Client dinner")], 11.37)
    dist = _dist(doc)
    assert _split(dist) == ([(MEALS, 5.69, 93.12)], [("2310", 5.68)])
    assert dist[0]["meals_tax"] == 5.69 and dist[1]["meals_tax"] == 5.69
    assert round(sum(e["amount"] for e in dist), 2) == doc["grand_total"] == 98.80


@pytest.mark.parametrize(
    ("hst", "claimed", "expensed"),
    [(13.00, 6.50, 6.50), (0.01, 0.0, 0.01), (0.03, 0.01, 0.02), (-11.37, -5.68, -5.69)],
)
def test_the_claimable_half_is_rounded_down_to_the_cent(hst, claimed, expensed):
    """Cent rounding: the claim is half the tax, cut to the cent (a credit note's too: its reversal is no larger); the
    rest is expensed, so the two always add up to the tax."""
    amount = round(hst / 0.13, 2)
    doc = _on([(amount, MEALS, "Lunch meeting")], hst)
    expense, tax = _split(_dist(doc))
    assert expense[0][1] == expensed
    assert sum(a for _, a in tax) == pytest.approx(claimed)
    assert round(expensed + claimed, 2) == hst


def test_only_the_tax_on_the_meal_line_is_halved():
    """A 100.00 lunch and 100.00 of office supplies, HST 26.00: only the 13.00 on the lunch is halved."""
    dist = _dist(_on([(100.0, "6000", "Paper"), (100.0, MEALS, "Team lunch")], 26.0))
    assert _split(dist) == ([("6000", 0.0, 100.0), (MEALS, 6.50, 106.50)], [("2310", 19.50)])


def test_a_tip_line_carries_no_tax_to_halve():
    """Voluntary tips are not taxed: the HST printed is all on the meal, so half of all of it is expensed with the
    meal, none with the tip, even when the tip is coded elsewhere."""
    dist = _dist(_on([(100.0, MEALS, "Dinner for 4"), (15.0, "6900", "Tip")], 13.0))
    expense, tax = _split(dist)
    assert expense == [(MEALS, 6.50, 106.50), ("6900", 0.0, 15.0)] and tax == [("2310", 6.50)]


def test_gst_and_qst_on_a_quebec_client_cocktail():
    """The Montreal invoice's client cocktail (2,550.00, GST 5% and QST 9.975%): half of each is expensed with it,
    63.75 GST and 127.18 QST; the other lines keep their full credits and refunds."""
    doc = _gt(QC)
    dist = _dist(doc)
    meal = next(e for e in dist if e["kind"] == "expense" and e["gl_code"] == MEALS)
    assert meal["non_recoverable_tax"] == 190.93 and meal["amount"] == 2740.93
    assert {(e["gl_code"], e["amount"]) for e in dist if e["kind"] == "tax"} == {("2310", 874.65), ("2320", 1744.93)}
    assert all(e["non_recoverable_tax"] == 0 for e in dist if e["kind"] == "expense" and e["gl_code"] != MEALS)
    assert round(sum(e["amount"] for e in dist), 2) == doc["grand_total"]


def test_pst_is_not_halved_and_without_meals_nothing_changes():
    gt = _gt(BC)
    gt["line_items"][0]["predicted_gl_code"] = MEALS
    dist = _dist(gt)
    plain = _dist(gt, _setup(meals=()))
    assert [e for e in plain if e["kind"] == "expense"][0]["non_recoverable_tax"] == 62.30  # the PST alone
    gst = gt["tax_lines"][0]
    assert gst["tax_type"] == "GST"
    share = round(gst["tax_amount"] * gt["line_items"][0]["amount"] / gt["subtotal"], 2)
    assert dist[0]["non_recoverable_tax"] == round(62.30 + share - int(share * 50) / 100, 2)
    assert "meals_tax" not in json.dumps(plain)  # nothing added when no line is a meal
    assert round(sum(e["amount"] for e in dist), 2) == gt["grand_total"]


# --- The setting: a GL account attribute -----------------------------------------------------------------------------


def test_the_sample_meals_account_has_the_flag_and_the_import_reads_a_column(tmp_path):
    reference = load_reference_data(DATA / "chart_of_accounts.csv", None, DATA / "tax_gl_mapping.csv")
    assert reference.tax.meals_gl_codes == frozenset({MEALS})
    assert all("meals" not in row for row in reference.chart_of_accounts.rows)  # not a column for the AI

    store = Store(tmp_path / "t.db")
    load_sample_setup(store)
    assert store.reference_data().tax.meals_gl_codes == frozenset({MEALS})
    rows = [{"Account": "7100", "Name": "Client meals", "M&E": "yes"},
            {"Account": "7110", "Name": "Staff party", "M&E": "no"}]  # fmt: skip
    store.import_accounts("gl_accounts", rows, "Account", "Name", meals_column="M&E")
    flags = {a["code"]: a["meals"] for a in store.list_accounts("gl_accounts")}
    assert flags["7100"] is True and flags["7110"] is False and flags[MEALS] is True
    assert store.reference_data().tax.meals_gl_codes == frozenset({MEALS, "7100"})
    # The grid saves it: unticked, the account is no longer a meals one.
    store.save_accounts(
        "gl_accounts", [{**a, "meals": a["code"] == "7100"} for a in store.list_accounts("gl_accounts")]
    )
    assert store.reference_data().tax.meals_gl_codes == frozenset({"7100"})


def test_replacing_the_list_without_a_meals_column_keeps_the_meals_ticks(tmp_path):
    store = Store(tmp_path / "t.db")
    store.import_accounts("gl_accounts", [{"code": "6000", "description": "Office"},
                                          {"code": MEALS, "description": "Meals", "M&E": "yes"}],
                          "code", "description", meals_column="M&E")  # fmt: skip
    # The monthly chart from the ERP, "Replace the list" ticked: it has no meals column.
    store.import_accounts("gl_accounts", [{"code": "6000", "description": "Office"},
                                          {"code": MEALS, "description": "Meals & ent."},
                                          {"code": "6400", "description": "Rent"}],
                          "code", "description", replace_all=True)  # fmt: skip
    flags = {a["code"]: a["meals"] for a in store.list_accounts("gl_accounts")}
    assert flags == {"6000": False, MEALS: True, "6400": False}
    # A save whose rows don't say (a list kept from before the flag) keeps it too; a row that says is followed.
    store.save_accounts("gl_accounts", [{"code": "6000", "description": "Office", "category": ""},
                                        {"code": MEALS, "description": "Meals", "category": ""}])  # fmt: skip
    assert store.reference_data().tax.meals_gl_codes == frozenset({MEALS})
    store.save_accounts("gl_accounts", [{"code": MEALS, "description": "Meals", "category": "", "meals": False}])
    assert store.reference_data().tax.meals_gl_codes == frozenset()


def test_the_accounts_page_ticks_and_saves_the_meals_flag(tmp_path):
    from .webclient import client_for, follow, post

    store = Store(tmp_path / "t.db")
    load_sample_setup(store)
    client = client_for(store)
    page = client.get("/accounts?tab=gl").text
    assert "Meals &amp; entertainment (50% tax credit)" in page
    accounts = store.list_accounts("gl_accounts")
    meals_row = next(n for n, a in enumerate(accounts) if a["code"] == MEALS)
    form = {"version": page.split('name="version" value="')[1].split('"')[0],
            "code": [a["code"] for a in accounts], "description": [a["description"] for a in accounts],
            "category": [a["category"] for a in accounts], "meals": [str(meals_row), "0"]}  # fmt: skip
    follow(client, post(client, "/accounts/gl_accounts/save", form))
    assert {a["code"] for a in store.list_accounts("gl_accounts") if a["meals"]} == {MEALS, accounts[0]["code"]}


# --- Where the split matters ------------------------------------------------------------------------------------


def _jde_invoice(stem=QC, **changes):
    doc = {**_gt(stem), **changes}
    doc["gl_distribution"] = _dist(doc)
    return {"id": 1, "final_output": doc}


@pytest.mark.parametrize("numbering", [jde.SEQUENTIAL, jde.MATCH_HEADER])
def test_the_jde_voucher_balances_with_the_meals_half_in_the_expense_line(numbering):
    inv = _jde_invoice()
    settings = jde.JdeSettings(amount_mode=jde.AMOUNT_TAX, decimals=jde.POINT, line_numbering=numbering)
    assert jde.validate([inv], settings, lambda f: "10023") == {}
    headers, dists, _ = jde.voucher_rows(inv, "10023", settings)
    gross = sum(float(h["VLATXA"]) + float(h["VLATXN"]) + float(h["VLSTAM"]) for h in headers)
    tax = sum(float(h["VLSTAM"]) for h in headers)
    assert round(gross, 2) == 21578.51
    assert round(tax, 2) == round(874.65 + 1744.93, 2)  # what E1 posts as recoverable: the claimable part only
    assert round(sum(float(d["VNAA"]) for d in dists), 2) == round(21578.51 - tax, 2)
    meal = next(d for d in dists if d["VNANI"].endswith(f".{MEALS}"))
    assert meal["VNAA"] == "2740.93"


def test_in_gross_mode_a_meals_voucher_is_held_back():
    """B-866: with gross amounts only, E1 works out the tax itself and claims the full ITC while AP Coder expenses half
    of it: the GL and the ITC report disagree. It was a warning; it is now a blocker, before approval and at export."""
    inv = _jde_invoice()
    settings = jde.JdeSettings(an8_overrides={})
    findings = jde.voucher_findings(inv["final_output"], settings, "10023")
    meals = [f for f in findings if f[1] == "JDE_MEALS_GROSS"]
    assert meals and meals[0][0] == "error" and "Settings → JD Edwards E1" in meals[0][2]
    assert "tax amounts" in meals[0][2]
    assert 1 in jde.validate([inv], settings, lambda f: "10023")  # held back from the batch
    tax_mode = jde.JdeSettings(amount_mode=jde.AMOUNT_TAX)
    assert not [f for f in jde.voucher_findings(inv["final_output"], tax_mode, "10023") if f[1] == "JDE_MEALS_GROSS"]
    from ap_coder.ops.help import help_for

    assert "would not balance" not in help_for("JDE_MEALS_GROSS").meaning
    assert "full ITC" in help_for("JDE_MEALS_GROSS").meaning


def _dinner_and_paper(**changes):
    """An Ontario invoice: an 87.43 client dinner (meals) and 100.00 of paper, HST 24.37."""
    doc = _on([(87.43, MEALS, "Client dinner"), (100.0, "6000", "Paper")], 24.37)
    doc.update(changes)
    doc["gl_distribution"] = _dist(doc)
    return {"id": 1, "final_output": doc}


@pytest.mark.parametrize("mode", [jde.AMOUNT_TAX, jde.AMOUNT_GROSS])
def test_a_po_matched_meals_voucher_is_held_back(mode):
    """B-865: a PO-matched voucher has no G/L lines of ours (E1 distributes from the receipts), so the half of the HST
    on the dinner that is not claimable could not be expensed: VLSTAM went out at the full 24.37, with no finding."""
    inv = _dinner_and_paper(po_number="4500012")
    settings = jde.JdeSettings(an8_overrides={}, po_matched=True, amount_mode=mode)
    findings = [f for f in jde.voucher_findings(inv["final_output"], settings, "10023") if f[1] == "JDE_MEALS_PO"]
    assert findings and findings[0][0] == "error" and "PO" in findings[0][2]
    assert 1 in jde.validate([inv], settings, lambda f: "10023")
    plain = _dinner_and_paper(po_number="4500012")
    doc = {k: v for k, v in plain["final_output"].items() if k != "gl_distribution"}
    plain["final_output"]["gl_distribution"] = _dist(doc, _setup(meals=()))
    assert not [f for f in jde.voucher_findings(plain["final_output"], settings, "10023") if f[1] == "JDE_MEALS_PO"]


def test_one_pay_item_per_line_takes_the_tax_of_the_distribution():
    """B-867: with one pay item per line, the tax was split again by JD Edwards' own rounding: the dinner got 5.67 and
    the paper 13.01, while the GL distribution has 5.68 claimable on the dinner and 13.00 on the paper."""
    inv = _dinner_and_paper()
    settings = jde.JdeSettings(amount_mode=jde.AMOUNT_TAX, decimals=jde.POINT, line_numbering=jde.MATCH_HEADER)
    headers, dists, _ = jde.voucher_rows(inv, "10023", settings)
    assert [h["VLSTAM"] for h in headers] == ["5.68", "13.00"]
    assert [d["VNAA"] for d in dists] == ["93.12", "100.00"]
    gross = [round(float(h["VLATXA"]) + float(h["VLATXN"]) + float(h["VLSTAM"]), 2) for h in headers]
    assert gross == [98.80, 113.00]


def test_a_meal_sharing_the_tax_with_unmarked_lines_is_to_confirm():
    """B-868: a 100.00 catered lunch and 100.00 of groceries (zero-rated), HST 13.00, no line marked: the tax was
    spread over both, so only 3.25 was expensed instead of 6.50. A finding asks which lines carry the tax."""
    from ap_coder.coding.tax import check_taxes

    doc = _on([(100.0, MEALS, "Catered lunch"), (100.0, "6000", "Coffee beans")], 13.0)
    for li in doc["line_items"]:
        li["taxes_applied"] = []
    codes = {(f.code, f.line_number) for f in check_taxes(InvoiceCoding.model_validate(doc), _setup())}
    assert ("MEALS_TAX_LINES", 1) in codes
    doc["line_items"][1]["tax_status"] = "zero_rated"  # marked: the tax is all on the lunch
    assert not {
        c
        for c, _ in {(f.code, f.line_number) for f in check_taxes(InvoiceCoding.model_validate(doc), _setup())}
        if c == "MEALS_TAX_LINES"
    }
    expense, _ = _split(_dist(doc))
    assert expense[0][1] == 6.50


def test_a_tip_written_another_way_carries_no_tax():
    """B-869: "Server - thank you" is a tip too."""
    dist = _dist(_on([(100.0, MEALS, "Dinner for 4"), (15.0, "6900", "Server - thank you")], 13.0))
    assert _split(dist)[0] == [(MEALS, 6.50, 106.50), ("6900", 0.0, 15.0)]


def test_the_excel_and_csv_lines_carry_the_meals_half():
    inv = _jde_invoice()
    rows = exports.line_rows([inv])
    meal = next(r for r in rows if r["gl_code"] == MEALS)
    assert meal["non_recoverable_tax"] == 190.93 and meal["amount"] == 2740.93 and meal["net_amount"] == 2550.0
    taxes = {r["gl_code"]: r["amount"] for r in rows if r["kind"] == "Recoverable tax"}
    assert taxes == {"2310": 874.65, "2320": 1744.93}
    assert round(sum(r["amount"] for r in rows), 2) == 21578.51
    assert any("meals" in r["description"].lower() for r in rows if r["kind"] == "Recoverable tax")


def test_the_sales_tax_report_claims_half_and_says_why(tmp_path):
    store = Store(tmp_path / "t.db")
    load_sample_setup(store)
    output, _ = finalise_coding(InvoiceCoding.model_validate(_gt(QC)), store.reference_data(), Settings(), store=store)
    invoice_id = store.add_invoice(tmp_path / "qc.pdf", output, {})
    store.approve_invoice(invoice_id, output, "Jane")
    report = taxreturn.build(store, *YEAR)
    claims = {c.tax_type: c for c in report.claims}
    assert claims["GST"].amount == 874.65 and claims["QST"].amount == 1744.93
    assert claims["GST"].meals == 63.75 and claims["QST"].meals == 127.18
    assert taxreturn.MEALS in claims["GST"].issues and report.at_risk() == []  # a reason, not a risk
    assert report.meals_totals() == {"GST/HST": {"CAD": 63.75}, "QST": {"CAD": 127.18}}
    text = taxreturn.to_csv(report).decode("utf-8-sig")
    assert "Not claimed: meals & entertainment (50%)" in text and "63.75" in text and "874.65" in text
    from .webclient import client_for

    page = client_for(store).get("/sales-tax?start=2020-01-01&end=2030-12-31").text
    assert "meals and entertainment" in page and "63.75" in page


def test_the_review_screen_notes_a_meals_line(tmp_path):
    """The line coded to the meals account says only half its tax is claimable; the other lines say nothing."""
    from .webclient import add_clean, client_for

    store = Store(tmp_path / "t.db")
    load_sample_setup(store)
    invoice_id = add_clean(store, QC)
    page = client_for(store).get(f"/review/{invoice_id}").text
    notes = page.split("data-meals-note")[1:]
    assert len(notes) == len(_gt(QC)["line_items"]) + 1  # each line, and the template of a new one
    shown = [n for n in notes if " hidden" not in n.split(">")[0]]
    assert len(shown) == 1 and "Meals: 50% of the GST and QST is claimable" in shown[0]


def test_the_meals_totals_keep_each_currency_apart(tmp_path):
    """B-870: a USD invoice's meals tax was added to the CAD one."""
    store = Store(tmp_path / "t.db")
    load_sample_setup(store)
    for n, currency in enumerate(("CAD", "USD")):
        doc = {**_gt(QC), "currency": currency, "invoice_number": f"M-{n}"}
        output, _ = finalise_coding(InvoiceCoding.model_validate(doc), store.reference_data(), Settings(), store=store)
        store.approve_invoice(store.add_invoice(tmp_path / f"{n}.pdf", output, {}), output, "Jane")
    totals = taxreturn.build(store, *YEAR).meals_totals()
    assert totals == {"GST/HST": {"CAD": 63.75, "USD": 63.75}, "QST": {"CAD": 127.18, "USD": 127.18}}


def test_the_report_says_the_claim_is_already_net():
    assert "no add-back" in taxreturn.MEALS_WHY and "line 106" in taxreturn.MEALS_WHY


def test_the_line_note_points_to_the_other_account():
    from ap_coder.coding.tax import meals_note

    assert meals_note("ON") == ("Meals: 50% of the GST/HST is claimable. Billed to a client or a staff event? Use the "
                                "other account.")  # fmt: skip


def test_a_meals_invoice_whose_tax_lines_are_a_guess_never_goes_touchless():
    """MEALS_TAX_LINES: the half expensed rests on which lines carry the tax, a guess; a person confirms it (B-891)."""
    from ap_coder.capture.supplier import ALWAYS_A_PERSON_RULES, touchless_gates

    assert touchless_gates([{"code": "MEALS_TAX_LINES", "severity": "warning"}], grand_total=100)
    assert "meals" in dict(ALWAYS_A_PERSON_RULES)["Tax to look at"]
