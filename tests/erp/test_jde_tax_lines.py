"""JD Edwards fixes of the fourth bug sweep: gross mode held back when E1 would work the tax out wrong (B-921), the
self-assessed code B only when every line owes the PST (B-922), VLATXA summed per tax type (B-923), a guessed due date
not sent (B-924)."""

from ap_coder.core.store._base import _due
from ap_coder.erp import jde
from tests.erp.test_jde_gl_mode import coded, doc, line, tl

GROSS, TAX = jde.JdeSettings(amount_mode=jde.AMOUNT_GROSS), jde.JdeSettings(amount_mode=jde.AMOUNT_TAX)


def held(inv, settings):
    return jde.validate([inv], settings, lambda f: "10023").get(1, [])


def codes(inv, settings):
    return {f[1] for f in jde.voucher_findings(inv["final_output"], settings, "10023")}


# --- B-921: gross mode -----------------------------------------------------------------------------------------


def test_gross_mode_holds_back_an_exempt_line_e1_would_tax():
    inv = coded(doc([line(1, 100.0, taxes=["HST"]), line(2, 50.0, exempt=["HST"], status="exempt")],
                    [tl("HST", "ON", 0.13, 100.0, 13.0)]))  # fmt: skip
    assert held(inv, GROSS) and "JDE_GROSS_TAX" in codes(inv, GROSS)
    assert not held(inv, TAX)  # the tax amounts are sent: fine


def test_gross_mode_holds_back_tax_charged_at_another_rate_than_the_area():
    inv = coded(doc([line(1, 100.0, taxes=["GST"])], [tl("GST", "", 0.05, 100.0, 5.0)]))  # GST only in Ontario
    problems = held(inv, GROSS)
    assert problems and "13" in problems[0] and "5.00" in problems[0]


def test_gross_mode_still_sends_what_e1_works_out_the_same():
    cases = [
        doc([line(1, 1000.0, taxes=["HST"])], [tl("HST", "ON", 0.13, 1000.0, 130.0)]),
        doc([line(1, 999.99, taxes=["HST"]), line(2, 0.01, taxes=["HST"])], [tl("HST", "ON", 0.13, 1000.0, 130.0)]),
        doc([line(1, 1000.0, taxes=["GST", "PST"])], [tl("GST", "", 0.05, 1000.0, 50.0),
                                                      tl("PST", "BC", 0.07, 1000.0, 70.0)], prov="BC"),
        doc([line(1, 1000.0, taxes=["GST"])], [tl("GST", "", 0.05, 1000.0, 50.0)], prov="BC"),  # code B: GST only
        doc([line(1, 1000.0, taxes=["GST", "QST"])], [tl("GST", "", 0.05, 1000.0, 50.0),
                                                      tl("QST", "QC", 0.09975, 1000.0, 99.75)], prov="QC",
            qst_registration_number="1234567890TQ0001"),
        doc([line(1, -100.0, taxes=["HST"])], [tl("HST", "ON", 0.13, -100.0, -13.0)]),
        doc([line(1, 100.0, status="exempt")], []),  # nothing charged: code E
    ]  # fmt: skip
    for d in cases:
        assert held(coded(d), GROSS) == [], d["line_items"]


# --- B-922: code B -------------------------------------------------------------------------------------------


def test_code_b_only_when_every_line_owes_the_pst():
    due = coded(doc([line(1, 1000.0, taxes=["GST"])], [tl("GST", "", 0.05, 1000.0, 50.0)], prov="BC"))
    assert jde.voucher_rows(due, "10023", TAX)[0][0]["VLEXR1"] == "B" and jde.accrues_use_tax(due["final_output"], TAX)

    exempt = coded(doc([line(1, 1000.0, taxes=["GST"], exempt=["PST"])], [tl("GST", "", 0.05, 1000.0, 50.0)],
                       prov="BC"))  # fmt: skip
    (header,), dist, _ = jde.voucher_rows(exempt, "10023", jde.JdeSettings(amount_mode=jde.AMOUNT_TAX, default_bu="HQ"))
    assert header["VLEXR1"] == "E" and not jde.accrues_use_tax(exempt["final_output"], TAX)
    assert sum(r["VNAA"] for r in dist) == 105000  # no tax for E1 to post: the GST is on its own G/L line
    assert held(exempt, jde.JdeSettings(amount_mode=jde.AMOUNT_TAX, default_bu="HQ")) == []

    mixed = coded(doc([line(1, 300.0, taxes=["GST"]), line(2, 700.0, taxes=["GST"], exempt=["PST"])],
                      [tl("GST", "", 0.05, 1000.0, 50.0)], prov="BC"))  # fmt: skip
    problems = held(mixed, TAX)
    assert problems and "PST" in problems[0] and "JDE_TAX_AREA" in codes(mixed, TAX)


# --- B-923: VLATXA ---------------------------------------------------------------------------------------------


def test_taxable_amount_is_the_sum_of_a_tax_printed_twice():
    inv = coded(doc([line(1, 1000.0, taxes=["HST"]), line(2, 200.0, taxes=["HST"], desc="Freight")],
                    [tl("HST", "ON", 0.13, 1000.0, 130.0), tl("HST", "ON", 0.13, 200.0, 26.0)]))  # fmt: skip
    (header,), _, _ = jde.voucher_rows(inv, "10023", TAX)
    assert (header["VLATXA"], header["VLATXN"], header["VLSTAM"]) == (120000, 0, 15600)


# --- B-924: VLDDJ ------------------------------------------------------------------------------------------------


def test_a_due_date_only_guessed_is_not_sent():
    d = doc([line(1, 1000.0, taxes=["HST"])], [tl("HST", "ON", 0.13, 1000.0, 130.0)], payment_terms="")
    inv = coded(d)
    inv["due_date"] = _due(inv["final_output"])  # the store's 30 days by default
    (header,), _, _ = jde.voucher_rows(inv, "10023", TAX)
    assert inv["due_date"] and header["VLDDJ"] == ""
    due_row = next(r for r in jde.voucher_view(inv["final_output"], TAX, "10023")["rows"] if r["column"] == "VLDDJ")
    assert due_row["value"].startswith("Not sent")

    net30 = coded({**d, "payment_terms": "Net 30"})
    net30["due_date"] = _due(net30["final_output"])
    assert jde.voucher_rows(net30, "10023", TAX)[0][0]["VLDDJ"] == jde.julian("2026-05-31")
    printed = coded({**d, "due_date": "2026-06-15"})
    assert jde.voucher_rows(printed, "10023", TAX)[0][0]["VLDDJ"] == jde.julian("2026-06-15")
    credit = coded(doc([line(1, -100.0, taxes=["HST"])], [tl("HST", "ON", 0.13, -100.0, -13.0)]))
    assert jde.voucher_rows(credit, "10023", TAX)[0][0]["VLDDJ"] == jde.julian("2026-05-01")  # the invoice date
