"""JD Edwards "Tax coded to G/L accounts" mode (D-93): F0911Z1 is AP Coder's own GL distribution, the recoverable tax
on its own lines to the tax accounts; the voucher goes with the gross only, explanation code E and no tax area."""

import json

import pytest

from ap_coder.coding.tax import TaxRateTable, TaxSetup, build_gl_distribution, load_tax_mapping
from ap_coder.core.schema import InvoiceCoding
from ap_coder.core.store import Store
from ap_coder.erp import jde

from .conftest import DATA


def _setup(meals=()):
    return TaxSetup(TaxRateTable.load(DATA / "canada_tax_rates.csv"), load_tax_mapping(DATA / "tax_gl_mapping.csv"),
                    meals_gl_codes=frozenset(meals))  # fmt: skip


def line(n, amt, gl="6010", taxes=(), exempt=None, status="", desc=None, cc="CC100"):
    return dict(line_number=n, description=desc or f"item {n}", quantity=1, unit_price=amt, amount=amt,
                predicted_gl_code=gl, predicted_cost_center=cc, taxes_applied=list(taxes),
                tax_exempt=list(exempt) if exempt is not None else [], tax_status=status)  # fmt: skip


def tl(t, prov, rate, base, amt):
    return dict(tax_type=t, province=prov, rate=rate, taxable_amount=base, tax_amount=amt)


def doc(lines, taxes, prov="ON", currency="CAD", **kw):
    sub = round(sum(li["amount"] for li in lines), 2)
    tax = round(sum(t["tax_amount"] for t in taxes), 2)
    d = dict(vendor_name="Acme Ltd", invoice_number="A-1", invoice_date="2026-05-01", currency=currency,
             supplier_province=prov, ship_to_province=prov, gst_hst_registration_number="123456782RT0001",
             subtotal=sub, tax_lines=taxes, tax_total=tax, grand_total=round(sub + tax, 2), confidence_score=0.99,
             line_items=lines)  # fmt: skip
    d.update(kw)
    return d


def coded(d, setup=None, inv_id=1):
    d = dict(d)
    d["gl_distribution"] = build_gl_distribution(InvoiceCoding.model_validate(d), setup or _setup())
    return {"id": inv_id, "final_output": d}


def GL(**kw):  # noqa: N802
    return jde.JdeSettings(amount_mode=jde.AMOUNT_GL, default_bu="CORP", **kw)


def rows(inv, settings):
    return jde.voucher_rows(inv, "10023", settings)


def total(dist, col="VNAA"):
    return sum(int(r[col] or 0) for r in dist)


# --- The default --------------------------------------------------------------------------------------------------


def test_new_setups_code_tax_to_gl_and_saved_ones_keep_their_mode(tmp_path):
    assert jde.JdeSettings().amount_mode == jde.AMOUNT_GL
    assert jde.JdeSettings.from_json(None).amount_mode == jde.AMOUNT_GL
    assert jde.load(Store(tmp_path / "a.db")).amount_mode == jde.AMOUNT_GL
    for mode in (jde.AMOUNT_GROSS, jde.AMOUNT_TAX, jde.AMOUNT_GL):
        assert jde.JdeSettings.from_json(jde.JdeSettings(amount_mode=mode).to_json()).amount_mode == mode
    # Settings saved before the mode existed (no amount_mode in them, or one no longer known): gross, as they were.
    assert jde.JdeSettings.from_json(json.dumps({"company": "00002"})).amount_mode == jde.AMOUNT_GROSS
    assert jde.JdeSettings.from_json(json.dumps({"amount_mode": "odd"})).amount_mode == jde.AMOUNT_GROSS
    assert "G/L" in jde.AMOUNT_MODES[jde.AMOUNT_GL]


def test_old_settings_rebuild_old_batches_unchanged(tmp_path):
    store = Store(tmp_path / "a.db")
    old = jde.JdeSettings(amount_mode=jde.AMOUNT_GROSS)
    jde.save(store, old)
    inv = coded(doc([line(1, 1000.0, taxes=["HST"])], [tl("HST", "ON", 0.13, 1000.0, 130.0)]))
    assert jde.load(store).amount_mode == jde.AMOUNT_GROSS
    assert jde.export_batch([inv], jde.load(store), {"acme": "10023"}, batch=3) == jde.export_batch(
        [inv], old, {"acme": "10023"}, batch=3
    )


# --- Balancing, province by province ---------------------------------------------------------------------------

PROVINCES = {
    "ON HST": ("ON", [line(1, 1000.0, taxes=["HST"])], [tl("HST", "ON", 0.13, 1000.0, 130.0)],
               [("CC100.6010", 100000), ("CORP.2310", 13000)]),
    "QC GST+QST": ("QC", [line(1, 1000.0, taxes=["GST", "QST"])],
                   [tl("GST", "", 0.05, 1000.0, 50.0), tl("QST", "QC", 0.09975, 1000.0, 99.75)],
                   [("CC100.6010", 100000), ("CORP.2310", 5000), ("CORP.2320", 9975)]),
    "BC GST+PST": ("BC", [line(1, 1000.0, taxes=["GST", "PST"])],
                   [tl("GST", "", 0.05, 1000.0, 50.0), tl("PST", "BC", 0.07, 1000.0, 70.0)],
                   [("CC100.6010", 107000), ("CORP.2310", 5000)]),
    "SK GST+PST": ("SK", [line(1, 500.0, taxes=["GST", "PST"]), line(2, 300.0, taxes=["GST", "PST"], gl="6020")],
                   [tl("GST", "", 0.05, 800.0, 40.0), tl("PST", "SK", 0.06, 800.0, 48.0)],
                   [("CC100.6010", 53000), ("CC100.6020", 31800), ("CORP.2310", 4000)]),
    "MB GST+RST": ("MB", [line(1, 200.0, taxes=["GST", "PST"])],
                   [tl("GST", "", 0.05, 200.0, 10.0), tl("PST", "MB", 0.07, 200.0, 14.0)],
                   [("CC100.6010", 21400), ("CORP.2310", 1000)]),
    "AB GST": ("AB", [line(1, 400.0, taxes=["GST"])], [tl("GST", "", 0.05, 400.0, 20.0)],
               [("CC100.6010", 40000), ("CORP.2310", 2000)]),
}  # fmt: skip


@pytest.mark.parametrize("name", list(PROVINCES))
def test_each_province_balances_to_the_gross_with_the_tax_on_its_own_gl_lines(name):
    prov, lines, taxes, expected = PROVINCES[name]
    inv = coded(doc(lines, taxes, prov=prov))
    settings = GL()
    assert jde.validate([inv], settings, lambda f: "10023") == {}
    assert jde.voucher_findings(inv["final_output"], settings, "10023") == []
    (header,), dist, matches = rows(inv, settings)
    gross = round(inv["final_output"]["grand_total"] * 100)
    assert header["VLAG"] == gross and header["VLTXA1"] == "" and header["VLEXR1"] == "E"
    assert all(header.get(c) in (None, "") for c in ("VLATXA", "VLATXN", "VLSTAM"))
    assert [(r["VNANI"], r["VNAA"]) for r in dist] == expected
    assert total(dist) == gross and not matches
    # F0911Z1 is the stored distribution, line for line (nothing dropped, nothing left for E1's AAIs)
    assert [round(e["amount"] * 100) for e in inv["final_output"]["gl_distribution"]] == [r["VNAA"] for r in dist]
    assert not jde.accrues_use_tax(inv["final_output"], settings)


def test_the_tax_area_checks_are_not_asked():
    # No province of supply read: a tax area could not be picked, but none is needed.
    inv = coded(doc([line(1, 100.0, taxes=["HST"])], [tl("HST", "ON", 0.13, 100.0, 13.0)], prov="",
                    ship_to_province="", supplier_province=""))  # fmt: skip
    assert jde.validate([inv], GL(), lambda f: "10023") == {}
    assert jde.validate([inv], GL(tax_map={}), lambda f: "10023") == {}


def test_self_assessed_pst_goes_to_the_self_assessment_report():
    # BC supply, GST only charged: PST due. Not accrued by E1 (no code B): AP Coder's report has it.
    inv = coded(doc([line(1, 1000.0, taxes=["GST"])], [tl("GST", "", 0.05, 1000.0, 50.0)], prov="BC"))
    (header,), dist, _ = rows(inv, GL())
    assert header["VLEXR1"] == "E" and header["VLTXA1"] == ""
    assert not jde.accrues_use_tax(inv["final_output"], GL())
    assert jde.accrues_use_tax(inv["final_output"], jde.JdeSettings(amount_mode=jde.AMOUNT_TAX))
    assert total(dist) == 105000


def test_meals_post_the_claimable_half_on_the_tax_line_and_no_meals_hold():
    s = _setup(meals=["6320"])
    inv = coded(doc([line(1, 200.0, gl="6320", taxes=["GST", "QST"]), line(2, 100.0, taxes=["GST", "QST"])],
                    [tl("GST", "", 0.05, 300.0, 15.0), tl("QST", "QC", 0.09975, 300.0, 29.93)], prov="QC",
                    qst_registration_number="1234567890TQ0001"), s)  # fmt: skip
    settings = GL()
    assert jde.validate([inv], settings, lambda f: "10023") == {}
    codes = {f[1] for f in jde.voucher_findings(inv["final_output"], settings, "10023")}
    assert "JDE_MEALS_GROSS" not in codes
    (header,), dist, _ = rows(inv, settings)
    stored = inv["final_output"]["gl_distribution"]
    assert [r["VNAA"] for r in dist] == [round(e["amount"] * 100) for e in stored]
    gst = next(e for e in stored if e["kind"] == "tax" and e["description"].startswith("GST"))
    assert next(r for r in dist if r["VNANI"] == "CORP.2310")["VNAA"] == round(gst["amount"] * 100)  # the claim
    assert total(dist) == header["VLAG"] == 34493
    # On a PO-matched voucher E1 distributes from the receipts: still held back (JDE_MEALS_PO)
    again = {k: x for k, x in inv["final_output"].items() if k != "gl_distribution"}
    po = coded({**again, "po_number": "PO1"}, s)
    assert "JDE_MEALS_PO" in {f[1] for f in jde.voucher_findings(po["final_output"], GL(po_matched=True), "10023")}


def test_credit_memo_balances():
    inv = coded(doc([line(1, -100.0, taxes=["HST"]), line(2, -33.33, taxes=["HST"])],
                    [tl("HST", "ON", 0.13, -133.33, -17.33)]))  # fmt: skip
    assert jde.validate([inv], GL(), lambda f: "10023") == {}
    (header,), dist, _ = rows(inv, GL())
    assert header["VLAG"] == -15066 == total(dist)
    assert [r["VNAA"] for r in dist] == [-10000, -3333, -1733]


def test_usd_goes_in_the_foreign_columns():
    inv = coded(doc([line(1, 1000.0, taxes=["HST"])], [tl("HST", "ON", 0.13, 1000.0, 130.0)], currency="USD"))
    (header,), dist, _ = rows(inv, GL())
    assert header["VLACR"] == 113000 and header["VLCRCD"] == "USD" and not header.get("VLAG")
    assert total(dist, "VNACR") == 113000 and all(r["VNAA"] == "" for r in dist)


def test_match_header_is_one_pay_item_per_gl_line_with_no_tax_share():
    inv = coded(doc([line(1, 100.0, taxes=["HST"]), line(2, 50.0, exempt=["HST"], status="exempt")],
                    [tl("HST", "ON", 0.13, 100.0, 13.0)]))  # fmt: skip
    settings = GL(line_numbering=jde.MATCH_HEADER, dist_tax_columns=True)
    assert jde.validate([inv], settings, lambda f: "10023") == {}
    headers, dist, _ = rows(inv, settings)
    assert [(h["VLEDLN"], h["VLAG"], h["VLEXR1"], h["VLTXA1"]) for h in headers] == [
        (1, 10000, "E", ""), (2, 5000, "E", ""), (3, 1300, "E", "")]  # fmt: skip
    assert [(r["VNEDLN"], r["VNAA"], r["VNEXR1"], r["VNTXA1"]) for r in dist] == [
        (1, 10000, "E", ""), (2, 5000, "E", ""), (3, 1300, "E", "")]  # fmt: skip
    assert sum(h["VLAG"] for h in headers) == 16300


def test_po_matched_with_tax_is_held_back_and_without_tax_goes():
    taxed = coded(doc([line(1, 100.0, taxes=["HST"])], [tl("HST", "ON", 0.13, 100.0, 13.0)], po_number="PO1"))
    settings = GL(po_matched=True)
    problems = jde.validate([taxed], settings, lambda f: "10023")
    assert problems and "receipts" in problems[1][0]
    assert "JDE_GL_PO" in {f[1] for f in jde.voucher_findings(taxed["final_output"], settings, "10023")}
    untaxed = coded(doc([line(1, 100.0, status="exempt")], [], po_number="PO2"))
    assert jde.validate([untaxed], settings, lambda f: "10023") == {}
    (header,), dist, matches = rows(untaxed, settings)
    assert header["VLAG"] == 10000 and header["VLEXR1"] == "E" and not dist and len(matches) == 1


def test_a_tax_account_with_no_jde_account_is_said_clearly():
    inv = coded(doc([line(1, 1000.0, taxes=["HST"])], [tl("HST", "ON", 0.13, 1000.0, 130.0)]))
    settings = jde.JdeSettings(amount_mode=jde.AMOUNT_GL)  # no default BU: the tax line (no cost center) has no BU
    problems = jde.validate([inv], settings, lambda f: "10023")
    assert problems and "2310" in problems[1][0]
    (finding,) = [f for f in jde.voucher_findings(inv["final_output"], settings, "10023") if f[1] == "JDE_NO_ACCOUNT"]
    assert "2310" in finding[2] and "HST" in finding[2] and "tax" in finding[2]
    # mapped in the account mapping: it goes
    mapped = jde.JdeSettings(amount_mode=jde.AMOUNT_GL, gl_map=[jde.GlMap("2310", "", "HQ", "2310")])
    assert jde.validate([inv], mapped, lambda f: "10023") == {}
    assert [r["VNANI"] for r in rows(inv, mapped)[1]] == ["CC100.6010", "HQ.2310"]


def test_a_tax_line_with_no_gl_account_is_held_back():
    inv = coded(doc([line(1, 1000.0, taxes=["HST"])], [tl("HST", "ON", 0.13, 1000.0, 130.0)]))
    for e in inv["final_output"]["gl_distribution"]:
        if e["kind"] == "tax":
            e["gl_code"] = ""
    problems = jde.validate([inv], GL(), lambda f: "10023")
    assert problems and "GL accounts & tax" in problems[1][0]


def test_views_say_the_mode():
    inv = coded(doc([line(1, 1000.0, taxes=["HST"])], [tl("HST", "ON", 0.13, 1000.0, 130.0)]))
    view = jde.voucher_view(inv["final_output"], GL(), "10023")
    tax_row = next(r for r in view["rows"] if r["column"] == "VLTXA1 / VLEXR1")
    assert "G/L" in tax_row["value"]
    report = jde.dry_run([inv], GL(), lambda f: "10023")
    assert "G/L" in report["vouchers"][0]["balance"]
    files = jde.export_batch([inv], GL(), lambda f: "10023")
    text = jde.readme(files, GL(), 1, [inv])
    assert "G/L accounts" in text and "code E" in text
