"""GL coding with no AI model: matching line descriptions, when a suggestion codes a line on its own, the
history read once per invoice, and the benchmark (``python -m ap_coder.bench coding``)."""

from __future__ import annotations

import json

from ap_coder import suggest
from ap_coder.capture.types import VERIFIED, CaptureResult, FieldResult, LineReading
from ap_coder.line_match import PREFIX, Index, keys, similarity, word_match
from ap_coder.memory import aggregate, tokens, vendor_key
from ap_coder.offline_coder import code_from_capture
from ap_coder.reference_data import UNASSIGNED
from ap_coder.store import Store
from ap_coder.suggest import suggest_gl


def _history(*decisions: tuple[str, str, str]) -> list[dict]:
    """Feedback rows as ``Store.feedback_rows()`` gives them, newest first: (vendor, account, description)."""
    n = len(decisions)
    return [
        {"id": n - i, "vendor_key": vendor_key(v), "vendor_name": v, "description": d, "final_gl": gl, "final_cc": "",
         "outcome": "accepted", "created_at": "2026-09-01"}
        for i, (v, gl, d) in enumerate(decisions)
    ]  # fmt: skip


# --- matching line descriptions ---------------------------------------------------------------------------------


def test_keys_leave_out_case_accents_plurals_units_and_months():
    assert keys("Nitrile GLOVES, box of 100 - September") == {"nitrile", "glove"}
    assert keys("Câble Cat6 plénum, 1000 pi") == {"cable", "cat6", "plenum"}
    assert keys("FRAIS DE LIVRAISON") == keys("Frai de livraison") == {"frai", "livraison"}
    assert keys("Cons. service - août") == {"cons"}  # a short word is not made singular


def test_shortened_and_nearly_spelt_words_match():
    assert word_match("hydr", "hydraulic") == PREFIX
    assert 0 < word_match("brg", "bearing") < 1 and 0 < word_match("assy", "assembly") < 1  # without vowels
    assert word_match("gas", "glasses") == 0.0  # a real word is not an abbreviation of another
    assert word_match("hydraulique", "hydraulic") >= 0.6  # spelt nearly the same
    assert word_match("cat6", "cat5") == 0.0  # codes only match themselves
    assert similarity(keys("Nitrile gloves"), keys("GLOVES, NITRILE")) == 1.0  # word order
    assert similarity(keys("Diesel exhaust fluid 9.46 L"), keys("Dsl exhst fluid")) > 0.5
    assert similarity(keys("Energy charge"), keys("Energy charges")) == 1.0


def test_another_vendors_own_item_codes_are_no_difference():
    mine, theirs = keys("[KC31973] Shipping cartons"), keys("Shipping cartons #ZZ1234")
    assert similarity(mine, theirs) == 0.5  # the same vendor's two codes: two different items
    assert similarity(mine, theirs, own_codes=True) == 1.0
    shared = similarity(keys("Cat6 cable"), keys("Cat6 wire"), own_codes=True)
    assert shared > similarity(keys("Cat6 cable"), keys("Cat5 wire"), own_codes=True) == 0.0  # a code on both counts


def test_words_coded_to_several_accounts_count_for_less():
    index = Index([keys("Premium gloves"), keys("Premium paper")], ["5000", "6000"])
    assert index.weight["premium"] == 0.5 and index.weight["glove"] == 1.0
    found = dict(index.similar(keys("Premium gloves XL")))
    assert found[0] > found.get(1, 0.0)


# --- when a suggestion codes the line ---------------------------------------------------------------------------


def test_a_close_match_with_this_vendors_lines_codes_the_line(reference):
    feedback = _history(("Acme", "5000", "Nitrile gloves, box of 100"), ("Acme", "6000", "Copy paper 8.5x11"))
    top = suggest_gl("NIT. GLOVES (BLUE)", "ACME Ltd.", feedback, reference)[0]
    assert (top.gl_code, top.source, top.auto) == ("5000", "vendor", True)


def test_account_names_and_the_vendor_master_alone_only_suggest(reference):
    words = suggest_gl("Copy paper and toner cartridges", "Nobody", [], reference)[0]
    assert words.gl_code == "6000" and words.source == "words" and not words.auto
    master = suggest_gl("Miscellaneous charge", "Acme", [], reference, default_gl="6800")[0]
    assert master.gl_code == "6800" and not master.auto


def test_other_vendors_code_a_line_only_when_they_agree(reference):
    feedback = _history(("Beta", "6220", "Hydraulic hose assembly 1/2 in."), ("Gamma", "6220", "HOSE ASSY HYDRAULIC"))
    top = suggest_gl("Hydraulic hose assy", "Newco", feedback, reference)[0]
    assert (top.gl_code, top.source, top.auto) == ("6220", "others", True)
    split = _history(("Delta", "5000", "Hydraulic hose assembly")) + feedback
    found = suggest_gl("Hydraulic hose assy", "Newco", split, reference)
    assert not found[0].auto and {s.gl_code for s in found} >= {"5000", "6220"}  # both shown, a person picks


def test_the_latest_decision_wins_and_a_recode_elsewhere_holds_old_codes_back(reference):
    feedback = _history(
        ("Acme", "5000", "Bearing 6205-2RS"),  # newest: AP now codes spare parts to materials
        ("Acme", "6220", "Bearing 6205-2RS"),
        ("Beta", "6220", "Bearings 6205-2RS"),
        ("Acme", "6220", "Bearing 6205-2RS"),
    )
    acme = suggest_gl("Bearing 6205-2RS", "Acme", feedback, reference)[0]
    assert (acme.gl_code, acme.auto) == ("5000", True)
    beta = suggest_gl("Bearings 6205-2RS", "Beta", feedback, reference)
    assert beta[0].gl_code == "6220" and not beta[0].auto  # its own, but AP moved such lines away since
    assert "5000" in {s.gl_code for s in beta}


def test_a_vendors_usual_account_codes_its_new_lines(reference):
    lines = ["Basic monthly charge", "Delivery charge", "Energy charge", "Regulatory charges"]
    feedback = _history(*[("Rideau Hydro", "6210", d) for d in lines])
    top = suggest_gl("Global adjustment", "Rideau Hydro", feedback, reference)[0]
    assert (top.gl_code, top.source, top.auto) == ("6210", "usual", True)
    few = suggest_gl("Global adjustment", "Rideau Hydro", feedback[:2], reference)[0]
    assert few.gl_code == "6210" and not few.auto  # two lines are not enough to code with it


def _capture(vendor: str, *descriptions: str) -> CaptureResult:
    fields = {"vendor_name": FieldResult("vendor_name", vendor, 0.99, VERIFIED),
              "invoice_number": FieldResult("invoice_number", "INV-1", 0.99, VERIFIED),
              "invoice_date": FieldResult("invoice_date", "2026-09-01", 0.99, VERIFIED)}  # fmt: skip
    return CaptureResult(fields=fields, line_items=[LineReading(d, 1.0, 10.0, 10.0) for d in descriptions])


def test_the_offline_coder_leaves_weak_matches_for_ap_with_a_suggestion(tmp_path, reference):
    from ap_coder.web.invoice_view import suggestions

    store = Store(tmp_path / "ap.db")
    feedback = _history(("Acme", "5000", "Nitrile gloves"))
    coding = code_from_capture(_capture("Acme", "Nitrile gloves", "Copy paper"), reference, feedback, store).coding
    gloves, paper = coding.line_items
    assert gloves.predicted_gl_code == "5000"
    assert paper.predicted_gl_code == UNASSIGNED and paper.reasoning_justification.startswith("Not sure enough")
    assert [o["gl"] for o in suggestions(store, reference, coding)[0]["options"]][:1] == ["6000"]  # on the screen
    # With the local chat model, a weak match is kept (the model only gets lines with no suggestion at all).
    weak = code_from_capture(_capture("Acme", "Copy paper"), reference, feedback, store, assign_weak=True).coding
    assert weak.line_items[0].predicted_gl_code == "6000"


# --- the history is read once per invoice -----------------------------------------------------------------------


def test_the_history_index_is_built_once_for_the_same_rows(monkeypatch, reference):
    built = []
    real = suggest._build_memory
    monkeypatch.setattr(suggest, "_build_memory", lambda fb, valid: built.append(1) or real(fb, valid))
    monkeypatch.setattr(suggest, "_MEMORY", suggest.RowsCache())
    feedback = _history(("Acme", "5000", "Nitrile gloves"))
    for description in ("Nitrile gloves", "Nitrile glove XL", "Copy paper"):
        suggest_gl(description, "Acme", feedback, reference)
    suggest_gl("Nitrile gloves", "Acme", list(feedback), reference)  # the same rows in a new list
    suggest_gl("Nitrile gloves", "Acme", json.loads(json.dumps(feedback)), reference)  # the same rows read again
    assert len(built) == 1
    other = _history(("Acme", "6230", "Nitrile gloves"))  # other rows with the same ids: another database
    assert suggest_gl("Nitrile gloves", "Acme", other, reference)[0].gl_code == "6230" and len(built) == 2
    more = _history(("Acme", "6230", "Disinfectant 4 L"), ("Acme", "5000", "Nitrile gloves"))
    assert suggest_gl("Disinfectant", "Acme", more, reference)[0].gl_code == "6230" and len(built) == 3


def test_a_store_learns_from_each_approval_with_the_index_kept_in_between(tmp_path, reference):
    store = Store(tmp_path / "ap.db")
    rows = [
        {"vendor_name": "Acme", "description": "Nitrile gloves", "gl_code": "5000", "amount": 1, "date": "2026-01-01"}
    ]
    store.import_history(rows)
    assert suggest_gl("Disinfectant 4 L", "Acme", store.feedback_rows(), reference) == []
    store.import_history([{**rows[0], "description": "Disinfectant 4 L", "gl_code": "6230"}])  # one more lesson
    assert suggest_gl("Disinfectant", "Acme", store.feedback_rows(), reference)[0].gl_code == "6230"


def test_aggregate_and_tokens_cache_without_sharing_mutable_results():
    rows = _history(("Acme", "5000", "Nitrile gloves"), ("Acme", "5000", "nitrile gloves"))
    assert aggregate(rows) is aggregate(rows) and aggregate(rows)[0].decisions == 2
    assert aggregate(list(rows)) is aggregate(rows)  # the same rows in a new list
    changed = _history(("Acme", "6000", "Nitrile gloves"), ("Acme", "5000", "nitrile gloves"))
    assert {p.gl_code for p in aggregate(changed)} == {"5000", "6000"}  # same ids, other rows: worked out again
    words = tokens("Nitrile gloves")
    words.add("changed")
    assert tokens("Nitrile gloves") == {"nitrile", "gloves"}


# --- the benchmark ----------------------------------------------------------------------------------------------


def test_every_catalog_item_has_a_real_expense_account(reference):
    from ap_coder.bench import content as C
    from ap_coder.bench.coding import ITEM_GL, RECODED, UTILITY_GL, UTILITY_ITEMS

    expense = set(reference.chart_of_accounts.codes) - reference.tax.tax_gl_codes()
    assert {en for en, *_ in C.GOODS} == set(ITEM_GL)
    assert {en for en, *_ in C.UTILITY_LINES} == {it for items in UTILITY_ITEMS.values() for it in items}
    assert set(ITEM_GL.values()) | set(UTILITY_GL.values()) | {gl for _, gl in RECODED.values()} <= expense


def test_the_coding_benchmark_runs_and_is_deterministic(tmp_path, capsys):
    from ap_coder.bench.__main__ import main
    from ap_coder.bench.coding import simulate

    out = tmp_path / "coding"
    assert main(["coding", "--vendors", "4", "--invoices", "3", "--seed", "5", "--out", str(out)]) == 0
    assert "4 vendors x 3 invoices" in capsys.readouterr().out
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    s = report["summary"]
    assert s["overall"]["lines"] > 0 and list(s["first_or_later"]) == ["first invoice", "later invoices"]
    assert set(s["by_seen"]) <= {"vendor, same words", "vendor, other words", "other vendors only", "never"}
    assert s["overall"]["precision"] is None or s["overall"]["precision"] >= 0.9
    rows = [json.loads(x) for x in (out / "lines.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(rows) == s["overall"]["lines"] and all(r["position"] > 1 for r in rows if r["seen"].startswith("vendor"))
    md = (out / "report.md").read_text(encoding="utf-8")
    assert "## First invoice from a vendor" in md and "## Has AP coded this item before?" in md
    assert simulate(4, 3, 5) == rows  # the same seed gives the same company, invoices and proposals
