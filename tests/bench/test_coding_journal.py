"""``python -m ap_coder.bench coding --journal``: coding from last year's journal, the last two months held out."""

import json

from ap_coder.bench import coding_journal as cj
from ap_coder.bench import jde_company


def test_every_account_a_supplier_is_coded_to_has_invoice_wording_of_its_own():
    truth = jde_company.build(1, "small")
    wording = cj.invoice_wording()
    assert all(len(phrasings) >= 2 for phrasings in wording.values())
    remarks = {phrase for account in truth.accounts for phrase in account.phrases}
    assert not remarks & {p.lower() for phrasings in wording.values() for p in phrasings}  # the supplier's own words
    for supplier in truth.suppliers:
        for account in supplier.accounts:
            row = truth.account(account)
            assert cj._phrasings(row) or row.object == cj.BILLBACK, account


def test_the_kinds_of_vendor():
    assert cj.vendor_kind(["0021061010.7340.3307"], seen=True) == cj.ONE
    assert cj.vendor_kind(["0021061010.7340.3307", "0021061010.7340.3313"], seen=True) == cj.BOUGHT
    assert cj.vendor_kind(["0021061010.7340.3307", "0021061020.7340.3307"], seen=True) == cj.ORDERED
    assert cj.vendor_kind(["0021061010.7340.3307", "0021061010.2480.716500"], seen=True) == cj.ORDERED  # a billback
    assert cj.vendor_kind(["0021061010.7340.3307"], seen=False) == cj.NEW


def test_the_quick_run_scores_the_held_out_lines_by_kind_of_vendor(tmp_path):
    from ap_coder.bench.__main__ import main

    assert main(["coding", "--journal", "--quick", "--out", str(tmp_path)]) == 0
    report = json.loads((tmp_path / "report.json").read_text(encoding="utf-8"))
    summary = report["summary"]
    assert report["config"] == {"seed": 1, "size": "tenth", "how": "journal", "approve": False, "hold_out_months": 2}
    one, everything = summary["by_kind"][cj.ONE], summary["all"]
    assert everything["lines"] > 150 and sum(s["lines"] for s in summary["by_kind"].values()) == everything["lines"]
    # A vendor always coded to one account: the first candidate is that account, and a line coded alone is right.
    assert one["first_right"] == 1.0 and one["coded"] > 0 and one["coded_right"] == one["coded"]
    assert everything["coded_right"] == everything["coded"]  # nothing coded alone is wrong on this journal
    assert everything["in_top3"] > 0.6 and summary["by_kind"][cj.NEW]["coded"] == 0
    assert summary["ms_a_line_after"] < 50  # (0.9 ms measured)
    grid = {(g["share"], g["lines"]): g for g in summary["sure_grid"]}
    assert grid[(0.9, 3)]["wrong"] == 0 and grid[(0.8, 2)]["coded"] >= grid[(0.9, 3)]["coded"]
    assert "| one account |" in (tmp_path / "report.md").read_text(encoding="utf-8")
    assert main(["coding", "--quick", "--out", str(tmp_path / "x")]) == 0  # the vendors' own wording, as before
