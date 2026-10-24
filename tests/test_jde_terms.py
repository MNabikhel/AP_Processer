"""Payment terms → the JD Edwards payment terms code (VLPTC, D-91): the terms' meaning is the key, so "Net 30",
"30 days" and "N30" are one row; a default code for terms not in the table; a check when terms have no code."""

from __future__ import annotations

import json

import pytest

from ap_coder.coding.terms import parse_terms, terms_key, terms_label
from ap_coder.core.store import Store
from ap_coder.erp import jde

from .test_jde import AN8, ON, _files, _invoice


@pytest.mark.parametrize(
    ("printed", "key"),
    [
        ("Net 30", "Net 30"),
        ("30 days", "Net 30"),
        ("N30", "Net 30"),
        ("net 30 days from invoice", "Net 30"),
        ("2/10 Net 30", "2/10 Net 30"),
        ("2% 10 days, net 30", "2/10 Net 30"),
        ("2/10 n45", "2/10 Net 45"),
        ("Net 30 EOM", "Net 30 EOM"),
        ("Due on receipt", "Due on receipt"),
        ("Payable dans les 30 jours", "Net 30"),
        ("Net 1 month", "Net 1 month"),
        ("Net 10 prox", "Net 10th prox"),
        ("As agreed", ""),
        ("", ""),
    ],
)
def test_terms_are_keyed_by_what_they_mean(printed, key):
    assert terms_key(printed) == key
    if key:  # the key reads back as the same terms: the settings table can be typed in any of the ways
        assert parse_terms(key) == parse_terms(printed)
        assert terms_label(parse_terms(key)) == key


def test_the_code_is_looked_up_by_meaning_then_the_default():
    settings = jde.JdeSettings(amount_mode=jde.AMOUNT_GROSS, terms_codes={"Net 30": "N30", "2/10 Net 30": "210"})
    assert jde.terms_code({"payment_terms": "30 days"}, settings) == ("N30", "Net 30", jde.TERMS_TABLE)
    assert jde.terms_code({"payment_terms": "2% 10, net 30"}, settings) == ("210", "2/10 Net 30", jde.TERMS_TABLE)
    assert jde.terms_code({"payment_terms": "Net 45"}, settings) == ("", "Net 45", "")
    settings.payment_terms_code = "Z"
    assert jde.terms_code({"payment_terms": "Net 45"}, settings) == ("Z", "Net 45", jde.TERMS_DEFAULT)
    assert jde.terms_code({"payment_terms": ""}, settings) == ("Z", "", jde.TERMS_DEFAULT)


def test_vlptc_is_filled_from_the_table():
    inv = _invoice(ON)  # "Net 30"
    head = _files([inv], jde.JdeSettings(amount_mode=jde.AMOUNT_GROSS, terms_codes={"Net 30": "N30"}))[jde.HEADER_FILE][
        0
    ]
    assert head["VLPTC"] == "N30"
    head = _files(
        [inv], jde.JdeSettings(amount_mode=jde.AMOUNT_GROSS, terms_codes={"Net 45": "N45"}, payment_terms_code="DFT")
    )[jde.HEADER_FILE][0]
    assert head["VLPTC"] == "DFT"
    assert (
        _files([inv], jde.JdeSettings(amount_mode=jde.AMOUNT_GROSS))[jde.HEADER_FILE][0]["VLPTC"] == ""
    )  # no table: E1 takes the supplier's


def _terms_findings(final, settings):
    return [f for f in jde.voucher_findings(final, settings, "10023") if f[1] == "JDE_TERMS_NO_CODE"]


def test_terms_without_a_code_are_a_note_once_the_table_is_in_use():
    """A note, not a warning: a blank VLPTC has E1 take the supplier's own terms, so it holds back no bulk approval
    (B-896)."""
    from ap_coder.coding.bulk import _needs_a_person

    final = _invoice(ON, payment_terms="2/10 Net 45")["final_output"]
    (finding,) = _terms_findings(final, jde.JdeSettings(amount_mode=jde.AMOUNT_GROSS, terms_codes={"Net 30": "N30"}))
    assert finding[0] == "info" and not _needs_a_person(finding[0], finding[1])
    assert finding[2] == ("Payment terms 2/10 Net 45 have no JD Edwards code: add it in Settings → JD Edwards E1 "
                          "(or ask the pilot tester)")  # fmt: skip
    assert not _terms_findings(
        final, jde.JdeSettings(amount_mode=jde.AMOUNT_GROSS, terms_codes={"Net 30": "N30"}, payment_terms_code="Z")
    )
    assert not _terms_findings(final, jde.JdeSettings(amount_mode=jde.AMOUNT_GROSS, terms_codes={"2/10 Net 45": "245"}))
    assert not _terms_findings(
        final, jde.JdeSettings(amount_mode=jde.AMOUNT_GROSS)
    )  # no table: E1 takes the supplier's own terms, as before
    unread = _invoice(ON, payment_terms="As agreed")["final_output"]
    assert not _terms_findings(
        unread, jde.JdeSettings(amount_mode=jde.AMOUNT_GROSS, terms_codes={"Net 30": "N30"})
    )  # TERMS_NOT_FOUND says it
    # not held back from a batch: a warning only
    table = jde.JdeSettings(amount_mode=jde.AMOUNT_GROSS, terms_codes={"Net 30": "N30"})
    assert jde.validate([_invoice(ON, payment_terms="2/10 Net 45")], table, AN8) == {}


def test_a_credit_memo_is_sent_without_a_terms_code():
    """A terms code can carry a discount (2/10 Net 30): on a credit memo E1 would work one out on the credit. VLPTC is
    left blank on a credit memo, and nothing asks for its code (B-896)."""
    from .test_jde import CREDIT

    settings = jde.JdeSettings(
        amount_mode=jde.AMOUNT_GROSS, terms_codes={"Net 30": "N30", "2/10 Net 30": "210"}, payment_terms_code="DFT"
    )
    inv = _invoice(CREDIT, 3, payment_terms="2/10 Net 30")
    assert _files([inv], settings)[jde.HEADER_FILE][0]["VLPTC"] == ""
    assert not _terms_findings(
        inv["final_output"], jde.JdeSettings(amount_mode=jde.AMOUNT_GROSS, terms_codes={"Net 30": "N30"})
    )
    view = jde.voucher_view(inv["final_output"], settings, "10023")
    assert next(r for r in view["rows"] if r["column"] == "VLPTC")["value"] == (
        "Not sent (a credit): JD Edwards takes no terms code on a credit memo"
    )
    assert "payment terms 2/10 Net 30 -> none (a credit memo is sent without a terms code)" in jde.dry_run_text(
        jde.dry_run([inv], settings, AN8)
    )


def test_the_voucher_block_shows_the_code_beside_the_terms():
    final = _invoice(ON)["final_output"]

    def row(settings):
        view = jde.voucher_view(final, settings, "10023")
        return next(r for r in view["rows"] if r["column"] == "VLPTC")["value"], view

    value, view = row(jde.JdeSettings(amount_mode=jde.AMOUNT_GROSS, terms_codes={"Net 30": "N30"}))
    assert value == "N30 (Net 30)" and view["terms_code"] == "N30" and view["terms_sent"]
    value, _ = row(
        jde.JdeSettings(amount_mode=jde.AMOUNT_GROSS, terms_codes={"Net 45": "N45"}, payment_terms_code="DFT")
    )
    assert value == "DFT (the default for terms not in the table)"
    value, _ = row(jde.JdeSettings(amount_mode=jde.AMOUNT_GROSS, terms_codes={"Net 45": "N45"}))
    assert value == "Not sent: no code for Net 30, JD Edwards uses the supplier's own terms"
    value, view = row(jde.JdeSettings(amount_mode=jde.AMOUNT_GROSS))
    assert value == "Not sent: JD Edwards uses the supplier's own terms" and not view["terms_sent"]


def test_the_dry_run_shows_each_vouchers_terms_code():
    invoices = [_invoice(ON), _invoice(ON, inv_id=2, invoice_number="X-2", payment_terms="Net 60")]
    report = jde.dry_run(invoices, jde.JdeSettings(amount_mode=jde.AMOUNT_GROSS, terms_codes={"Net 30": "N30"}), AN8)
    text = jde.dry_run_text(report)
    assert "payment terms Net 30 -> N30" in text
    assert "payment terms Net 60 -> none (no code: JD Edwards uses the supplier's own terms)" in text
    assert "WARNING: payment terms Net 60 have no JD Edwards code" in text


def test_the_settings_table_round_trip(tmp_path):
    from .webclient import client_for, follow, post

    store = Store(tmp_path / "t.db")
    client = client_for(store)
    page = client.get("/settings/jde").text
    assert "Payment terms → JD Edwards code" in page
    assert "Examples: check each code against your JD Edwards payment terms" in page  # pre-filled, not in use yet
    assert 'value="N30"' in page and jde.load(store).terms_codes == {}
    form = {"terms": ["30 days", "2% 10 net 30", "N30", "", "As agreed"], "terms_code": ["n30", "210", "X", "Y", ""],
            "default_terms_code": "z"}  # fmt: skip
    answer = post(client, "/settings/jde/terms", form)
    assert answer.status_code == 400 and "Net 30" in answer.text  # the same meaning twice
    form["terms"][2], form["terms_code"][2] = "net 60", "n60"  # a row with no terms, or no code, is dropped
    follow(client, post(client, "/settings/jde/terms", form))
    saved = jde.load(store)
    assert saved.terms_codes == {"Net 30": "N30", "2/10 Net 30": "210", "Net 60": "N60"}
    assert saved.payment_terms_code == "Z"
    page = client.get("/settings/jde").text
    assert "Examples: check" not in page and 'value="2/10 Net 30"' in page and 'value="Z"' in page
    assert json.loads(store.get_setting(jde.SETTING_KEY))["terms_codes"]["Net 60"] == "N60"
    unknown = {"terms": ["sometime"], "terms_code": ["S"], "default_terms_code": ""}
    answer = post(client, "/settings/jde/terms", unknown)
    assert answer.status_code == 400 and "sometime" in answer.text


def test_the_review_screen_shows_the_code_beside_the_terms(tmp_path):
    from ap_coder.core.store import load_sample_setup

    from .webclient import add_clean, client_for

    store = Store(tmp_path / "t.db")
    load_sample_setup(store)
    store.set_setting(jde.SHOW_SETTING, "1")
    jde.save(store, jde.JdeSettings(amount_mode=jde.AMOUNT_GROSS, terms_codes={"Net 30": "N30"}))
    page = client_for(store).get(f"/review/{add_clean(store, ON)}").text
    assert "Sent to JD Edwards as code N30" in page  # under the Payment terms box
    block = page.split('aria-label="JD Edwards voucher"')[1]
    assert "N30 (Net 30)" in block and "Not sent" not in block.split("VLCRCD")[0]
