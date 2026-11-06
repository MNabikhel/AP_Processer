"""GL coding with no AI model: matching line descriptions, when a suggestion codes a line on its own, the
history read once per invoice, and the benchmark (``python -m ap_coder.bench coding``)."""

from __future__ import annotations

import json
import sqlite3

from ap_coder.ai import suggest
from ap_coder.ai.line_match import PREFIX, Index, keys, similarity, word_match
from ap_coder.ai.offline_coder import code_from_capture
from ap_coder.ai.suggest import suggest_gl
from ap_coder.capture.types import VERIFIED, CaptureResult, FieldResult, LineReading
from ap_coder.core.memory import aggregate, tokens, vendor_key
from ap_coder.core.reference_data import UNASSIGNED
from ap_coder.core.store import Store


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


def test_several_other_vendors_agreeing_outvote_one_that_differs(reference):
    """A new vendor's line: four other vendors coded it to repairs, one to materials. Four in five agree (weighted
    by how alike each vendor's closest line is): the line is coded; one against two is still a person's pick."""
    agree = [("Beta", "6220", "Hydraulic hose assembly 1/2 in."), ("Gamma", "6220", "HOSE ASSY HYDRAULIC"),
             ("Epsilon", "6220", "Hydraulic hose assembly"), ("Zeta", "6220", "Hydraulic hose assy 1/2")]  # fmt: skip
    feedback = _history(("Delta", "5000", "Hydraulic hose assembly"), *agree)
    top = suggest_gl("Hydraulic hose assy", "Newco", feedback, reference)[0]
    assert (top.gl_code, top.source, top.auto) == ("6220", "others", True)
    assert "4 other vendors" in top.reasons[0]
    close = _history(("Delta", "5000", "Hydraulic hose assembly"), *agree[:2])
    assert not suggest_gl("Hydraulic hose assy", "Newco", close, reference)[0].auto


def test_a_correction_against_several_approvals_is_shown_until_confirmed(reference):
    """AP changed a line coded five times to repairs: the next one is suggested, not coded, with the new account
    (a slip looks the same as a change of mind at first); once confirmed, it is coded. A change against four older
    approvals is coded at once."""

    def rows(*decisions: tuple[str, str]) -> list[dict]:
        out = _history(*[("Acme", gl, "Bearing 6205-2RS") for gl, _ in decisions])
        for row, (_, outcome) in zip(out, decisions, strict=True):
            row["outcome"] = outcome
        return out

    recoded = rows(("5000", "corrected"), *[("6220", "accepted")] * 5)
    top = suggest_gl("Bearing 6205-2RS", "Acme", recoded, reference)[0]
    assert (top.gl_code, top.auto) == ("5000", False)
    confirmed = rows(("5000", "accepted"), ("5000", "corrected"), *[("6220", "accepted")] * 5)
    assert suggest_gl("Bearing 6205-2RS", "Acme", confirmed, reference)[0].auto
    once = rows(("5000", "corrected"), *[("6220", "accepted")] * 4)
    assert suggest_gl("Bearing 6205-2RS", "Acme", once, reference)[0].auto


def _decisions(*rows: tuple[str, str, str, str]) -> list[dict]:
    """Feedback rows newest first: (vendor, account, description, outcome)."""
    out = _history(*[(v, gl, d) for v, gl, d, _ in rows])
    for row, (*_, outcome) in zip(out, rows, strict=True):
        row["outcome"] = outcome
    return out


def test_a_line_waiting_for_confirmation_is_not_coded_by_any_source(reference):
    """A slip to 6220 against six approvals of the bearing to 5000: neither the vendor's usual account (its other
    lines go to 6220) nor other vendors' lines may code it meanwhile, and the slip itself does not count toward the
    usual account (B-603)."""
    bearing = "Bearing 6205-2RS"
    others = ["Hydraulic hose assembly", "Pump rebuild kit", "Valve service call", "Conveyor belt repair"]
    rows = _decisions(("Acme", "6220", bearing, "corrected"), *[("Acme", "6220", d, "accepted") for d in others],
                      *[("Acme", "5000", bearing, "accepted")] * 6)  # fmt: skip
    found = suggest_gl(bearing, "Acme", rows, reference)
    assert not found[0].auto
    assert not any("5 different lines" in why for s in found for why in s.reasons)
    rows = _decisions(("Acme", "6220", bearing, "corrected"), *[("Acme", "5000", bearing, "accepted")] * 6,
                      *[(f"Other {i}", "6220", bearing + " sealed", "accepted") for i in range(3)])  # fmt: skip
    assert not suggest_gl(bearing, "Acme", rows, reference)[0].auto


def test_a_line_waiting_for_confirmation_offers_both_accounts_and_says_why(reference):
    """The review screen offers the new account and the one AP approved ten times, and says plainly what happened
    (B-606)."""
    from ap_coder.ai.offline_coder import not_sure

    rows = _decisions(("Acme", "5000", "Bearing 6205-2RS", "corrected"),
                      *[("Acme", "6220", "Bearing 6205-2RS", "accepted")] * 10)  # fmt: skip
    found = suggest_gl("Bearing 6205-2RS", "Acme", rows, reference)
    assert [s.gl_code for s in found[:2]] == ["5000", "6220"] and not found[0].auto
    said = "AP changed this line from 6220 (approved 10 times) to 5000 once: check which is right."
    assert not_sure(found[0]) == said


def test_a_similar_line_held_back_says_it_was_a_similar_line_ap_changed(reference):
    """A slip on the hose assembly holds back the merely similar crimp fitting too (the safe side), but the screen
    must not say AP changed the fitting: it names the similar line (B-607)."""
    from ap_coder.ai.offline_coder import not_sure

    rows = _decisions(("Acme", "6220", "Hydraulic hose assembly", "corrected"),
                      *[("Acme", "5000", "Hydraulic hose assembly", "accepted")] * 6)  # fmt: skip
    found = suggest_gl("Hydraulic hose crimp fitting", "Acme", rows, reference)
    assert not found[0].auto
    said = not_sure(found[0])
    assert "AP changed this line" not in said and "“Hydraulic hose assembly”" in said
    assert not any("for this line before" in why for s in found for why in s.reasons)


def test_other_vendors_evidence_is_quick_with_thousands_of_vendors(reference):
    """Each other vendor's closest line is found in one pass (B-604): 6,000 vendors coding the same words took
    most of a second per line."""
    import time

    rows = _history(*[(f"Vendor {i}", "5100" if i % 10 else "6800", "Freight charges") for i in range(6000)])
    suggest_gl("Freight charges", "Newco", rows, reference)  # the index, built once
    started = time.perf_counter()
    top = suggest_gl("Freight charges", "Newco", rows, reference)[0]
    assert time.perf_counter() - started < 0.3
    assert (top.gl_code, top.auto) == ("5100", True)


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
    # B-463: never put in the line, with or without the chat model: kept as a hint for the model's answer.
    weak = code_from_capture(_capture("Acme", "Copy paper"), reference, feedback, store)
    assert weak.coding.line_items[0].predicted_gl_code == UNASSIGNED and weak.hints == {1: "6000"}


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


def _held(memory) -> tuple:
    """All a memory holds, in its order: its patterns, its index's lists and dicts, each vendor's usual accounts."""
    index = memory.index
    return (
        [(p.vendor_key, p.description, p.keys, p.gl_code, p.cost_center, p.rank, p.latest) for p in memory.patterns],
        index.entries, index.accounts, list(index.by_word.items()), list(index.weight.items()),
        list(index._by_letter.items()), {v: list(c.items()) for v, c in memory.usual.items()}, memory.newest,
    )  # fmt: skip


def test_a_vendors_older_lessons_extend_the_shared_index_to_what_building_it_whole_gives(
    tmp_path, reference, monkeypatch
):
    """D-159: past the memory's cap, a vendor's ``learning_rows`` are the shared memory's rows then its older lessons
    (``WithPast``). The shared rows' index is built once; each vendor's older lessons extend it (``_with_past``,
    ``Index.extended``) to exactly what building it of all the rows gives: its lines with more decisions (a recode
    that reverses them is unconfirmed then), its lines only there, new words, its usual account. Many vendors in turn,
    fewer kept than asked: each extended again as it comes back, the same each time."""
    import random

    from ap_coder.core import memory as memory_module
    from ap_coder.core.store import _base as store_base
    from ap_coder.core.store import learning

    monkeypatch.setattr(learning, "FEEDBACK_READ", 80)
    monkeypatch.setattr(suggest, "_MEMORY", suggest.RowsCache())
    monkeypatch.setattr(suggest, "_PAST", {})
    monkeypatch.setattr(learning, "PAST_KEPT", 4)
    monkeypatch.setattr(suggest, "PAST_KEPT", 4)
    store = Store(tmp_path / "ap.db")
    rng = random.Random(159)
    items = ["Nitrile gloves", "Copy paper", "Toner cartridge", "Freight charge", "Dell Latitude laptop",
             "Hydraulic hose", "Brg assy", "Monthly cloud hosting", "Safety boots", "Cable cat6",
             "Hydraulique raccord", "Diesel exhaust fluid"]  # fmt: skip
    accounts = ["5000", "5100", "6000", "6010", "6030", "6230"]
    vendors = [f"Vendor {n} Inc." for n in range(14)]
    history = [{"vendor_name": rng.choice(vendors), "description": f"{rng.choice(items)} {rng.choice(['', 'XL'])}",
                "gl_code": rng.choice(accounts), "amount": 1.0, "date": f"2025-{n % 12 + 1:02d}-{n % 27 + 1:02d}"}
               for n in range(420)]  # fmt: skip
    # older, past the cap: reversed below
    history += [{"vendor_name": "Vendor 0 Inc.", "description": "Hydraulic hose", "gl_code": "5000", "amount": 1.0,
                 "date": f"2024-01-{n + 1:02d}"} for n in range(6)]  # fmt: skip
    store.import_history(history)
    with sqlite3.connect(store.path) as conn:  # the reviewers' decisions, ranked before the history
        for n in range(70):
            v = rng.choice(vendors)
            conn.execute("INSERT INTO feedback (vendor_key, vendor_name, description, final_gl, suggested_gl, outcome, "
                         "created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (vendor_key(v), v, rng.choice(items), rng.choice(accounts), rng.choice(accounts),
                          rng.choice(["accepted", "corrected", "coded"]), f"2026-01-{n % 28 + 1:02d}"))  # fmt: skip
        conn.execute("INSERT INTO feedback (vendor_key, vendor_name, description, final_gl, suggested_gl, outcome, "
                     "created_at) VALUES (?, 'Vendor 0 Inc.', 'Hydraulic hose', '6230', '5000', 'corrected', "
                     "'2026-02-01')", (vendor_key("Vendor 0 Inc."),))  # fmt: skip
    tax_gls = reference.tax.tax_gl_codes()
    valid = frozenset(c for c in reference.chart_of_accounts.codes if c not in tax_gls and c != UNASSIGNED)
    whole, extend = suggest._build_memory, suggest._extended
    built, extended = [], []
    monkeypatch.setattr(suggest, "_build_memory", lambda rows, ok: built.append(len(rows)) or whole(rows, ok))
    monkeypatch.setattr(suggest, "_extended", lambda *a: extended.append(1) or extend(*a))
    for vendor in vendors * 2:
        rows = store.learning_rows(vendor)
        assert isinstance(rows, memory_module.WithPast) and rows.past, vendor
        memory = suggest._memory(rows, valid)
        assert _held(memory) == _held(whole(list(rows), valid)), vendor
        assert suggest._memory(store.learning_rows(vendor), valid) is memory  # kept
        for item in items:
            asked = (f"{item} XL", vendor, rows, reference)
            assert suggest_gl(*asked) == suggest_gl(f"{item} XL", vendor, list(rows), reference), (vendor, item)
    assert built.count(80) == 1  # the shared rows' index, once
    assert len(extended) == 28 and len(suggest._PAST) == 4  # each vendor's, again as it comes back
    assert [key[1] for key in store_base._PAST_MEMORY] == [vendor_key(v) for v in vendors[-4:]]  # the store's too
    monkeypatch.setattr(learning, "PAST_ROWS_KEPT", 1)
    monkeypatch.setattr(suggest, "PAST_ROWS_KEPT", 1)
    rows = store.learning_rows(vendors[3])
    suggest._memory(rows, valid)
    assert list(suggest._PAST) == [rows.key] and list(store_base._PAST_MEMORY) == [rows.key]  # the latest always
    hose = next(p for p in suggest._memory(store.learning_rows("Vendor 0 Inc."), valid).patterns
                if p.description == "Hydraulic hose")  # fmt: skip
    assert hose.gl_code == "6230" and hose.unconfirmed  # the reversal of the decisions only the past lessons hold


def test_once_the_lessons_change_the_vendors_memories_extended_from_the_older_ones_are_let_go(tmp_path, monkeypatch):
    """Each memory extended from the shared rows keeps all of their memory (its patterns, its index): 61.6 MB at
    50,000 lessons. A vendor asked after each approval (new shared rows) kept one whole memory of each generation of
    the lessons, up to ``PAST_KEPT`` (681 MB for 32), until that same vendor was asked again. A memory extended from
    the current shared rows lets go of those extended from others."""
    from ap_coder.core.memory import WithPast
    from ap_coder.core.store import learning

    monkeypatch.setattr(learning, "FEEDBACK_READ", 20)
    monkeypatch.setattr(suggest, "_MEMORY", suggest.RowsCache())
    monkeypatch.setattr(suggest, "_PAST", {})
    store = Store(tmp_path / "ap.db")
    vendors = ["Acme Supply", "Beta Ltd", "Gamma Inc."]
    store.import_history([{"vendor_name": vendors[n % 3], "description": f"Part {n}", "gl_code": "6000", "amount": 1.0,
                           "date": f"2025-01-{n % 28 + 1:02d}"} for n in range(60)])  # fmt: skip
    valid = frozenset({"6000", "6100"})

    def asked(vendor: str) -> None:
        rows = store.learning_rows(vendor)
        assert isinstance(rows, WithPast) and rows.past and rows.shared is store.feedback_rows(), vendor
        suggest._memory(rows, valid)

    asked(vendors[0])
    asked(vendors[1])
    assert len(suggest._PAST) == 2
    with sqlite3.connect(store.path) as conn:  # an approval: new shared rows
        conn.execute("INSERT INTO feedback (vendor_key, vendor_name, description, final_gl, outcome, created_at) "
                     "VALUES (?, 'Gamma Inc.', 'Freight', '6100', 'accepted', '2026-01-05')",
                     (vendor_key(vendors[2]),))  # fmt: skip
    asked(vendors[2])
    shared = store.feedback_rows()
    assert [key[1] for key in suggest._PAST] == [vendor_key(vendors[2])]
    asked(vendors[0])  # extended again from the new ones
    assert len(suggest._PAST) == 2 and all(kept[0] is shared for kept in suggest._PAST.values())
    # Within their bytes too (D-171): each extended memory copies the shared index's lists and dicts it changes (0.35
    # MB a vendor on the 100k seed, more with a real vocabulary); past PAST_BYTES_KEPT the oldest go, the latest kept.
    own = [kept[4] for kept in suggest._PAST.values()]
    assert all(size > 0 for size in own)
    monkeypatch.setattr(suggest, "PAST_BYTES_KEPT", own[-1])
    asked(vendors[1])
    assert [key[1] for key in suggest._PAST] == [vendor_key(vendors[1])]


def test_aggregate_and_tokens_cache_without_sharing_mutable_results():
    rows = _history(("Acme", "5000", "Nitrile gloves"), ("Acme", "5000", "nitrile gloves"))
    assert aggregate(rows) is aggregate(rows) and aggregate(rows)[0].decisions == 2
    assert aggregate(list(rows)) is aggregate(rows)  # the same rows in a new list
    changed = _history(("Acme", "6000", "Nitrile gloves"), ("Acme", "5000", "nitrile gloves"))
    assert {p.gl_code for p in aggregate(changed)} == {"5000", "6000"}  # same ids, other rows: worked out again
    words = tokens("Nitrile gloves")
    words.add("changed")
    assert tokens("Nitrile gloves") == {"nitrile", "gloves"}


# --- an approval updates the memory, it isn't built again (D-171) -------------------------------------------------


def _lessons(rng, n: int, first_id: int, accounts: list[str]) -> list[dict]:
    """``n`` random lessons, newest first, of a few vendors and lines that come back (some with no words that count,
    some coded to an account the chart doesn't have, corrections among them; a word met once in a while, so words
    come and go): as ``Store.feedback_rows`` gives."""
    from ap_coder.core.memory import ACCEPTED, BULK, CODED, CORRECTED

    items = [
        "Nitrile gloves",
        "Copy paper",
        "Toner cartridge",
        "Freight charge",
        "Dell Latitude laptop",
        "Brg assy",
        "Hydraulic hose",
        "Safety boots",
        "Cable cat6",
        "the item",
        "Monthly cloud hosting",
        "Diesel fluid",
    ]
    vendors = ["Acme Supply", "Beta Ltd", "Gamma Inc.", "Delta"]
    rare = [f"{a}{b}" for a in ("zor", "kal", "vex", "mir", "tup") for b in ("ba", "di", "fo", "gu", "ke", "li")]
    out = []
    for n_id in range(first_id + n - 1, first_id - 1, -1):
        v = rng.choice(vendors)
        gl = rng.choice([*accounts, "9999"])  # (9999: not in the chart)
        out.append(
            {
                "id": n_id,
                "vendor_key": vendor_key(v),
                "vendor_name": v,
                "description": f"{rng.choice(items)}{rng.choice(['', '', ' XL', ' 2', f' {rng.choice(rare)}'])}",
                "final_gl": gl,
                "final_cc": rng.choice(["", "CC10"]),
                "suggested_gl": rng.choice([gl, gl, "", "6000"]),
                "outcome": rng.choice([ACCEPTED, ACCEPTED, CORRECTED, CODED, BULK]),
                "created_at": f"2026-{n_id:06d}",
            }
        )
    return out  # fmt: skip


def _as_built(memory) -> tuple:
    """``_held`` with each rank as its place in the rows (an updated memory's are less its ``offset``), and each line's
    decisions."""
    o = memory.offset
    index = memory.index
    return (
        [(p.vendor_key, p.description, p.keys, p.gl_code, p.cost_center, p.rank + o, p.latest)
         for p in memory.patterns],
        index.entries, index.accounts, list(index.by_word.items()), dict(index.weight), list(index._by_letter.items()),
        {v: list(c.items()) for v, c in memory.usual.items()}, {v: n + o for v, n in memory.newest.items()},
        {line: [(rank + o, id(r)) for rank, r in seq] for line, seq in memory.lines.items()},
        {line: (p.description, p.rank + o) for line, p in memory.pattern_of.items()},
    )  # fmt: skip


def test_an_approval_updates_the_suggestions_memory_to_exactly_what_building_it_gives(reference):
    """After each approval every process built the GL suggestions' index again from the whole memory: 0.2 s at 50,000
    lessons (more with a varied vocabulary), in every web worker, capture process and the background process. Its
    lessons now go in front of the memory kept and only their lines (and those of the oldest lessons dropped past
    the cap) are worked out again: the same patterns, ranks in the same order, the same index (its words in the
    order a build meets them, so lines are found in the same order) and the same suggestions, approval after
    approval."""
    import random

    tax_gls = reference.tax.tax_gl_codes()
    valid = frozenset(c for c in reference.chart_of_accounts.codes if c not in tax_gls and c != UNASSIGNED)
    accounts = sorted(valid)[:6]
    rng, cap = random.Random(171), 150
    rows = _lessons(rng, 120, 1, accounts)
    memory, next_id = suggest._build_memory(rows, valid), 121
    for approval in range(80):
        added = _lessons(rng, rng.choice([1, 1, 2, 3, 5, 12]), next_id, accounts)
        next_id += len(added)
        new_rows = (added + rows)[:cap]  # past the cap, the oldest dropped
        updated = suggest._updated_memory(rows, memory, new_rows, valid)
        built = suggest._build_memory(new_rows, valid)
        assert updated is not None and _as_built(updated) == _as_built(built), approval
        for item in ("Nitrile gloves XL", "Copy paper 2", "Toner", "Freight", "laptop", "cloud hosting"):
            for v in ("Acme Supply", "Delta"):
                key = vendor_key(v)
                asked = (item, keys(item), key, updated, reference, valid, 3, "")
                assert suggest._suggested(*asked) == suggest._suggested(item, keys(item), key, built, reference,
                                                                         valid, 3, "")  # fmt: skip
        rows, memory = new_rows, updated
    assert memory.offset == next_id - 121


def test_an_approval_updates_the_examples_patterns_to_exactly_what_working_them_out_gives():
    """The patterns of the whole memory (``memory.aggregate``: the chat model's examples, the history check), worked
    out again from every lesson after each approval in every process: updated now, the same patterns in the same
    order."""
    import random

    from ap_coder.core import memory as memory_module

    def held(found):
        return [(p.vendor_key, p.vendor_name, p.description, p.gl_code, p.cost_center, p.approvals, p.corrections,
                 p.last_seen, p.superseded) for p in found.patterns]  # fmt: skip

    rng, cap = random.Random(1710), 150
    rows = _lessons(rng, 120, 1, ["5000", "6000", "6100"])
    kept, next_id = memory_module._aggregated(rows), 121
    for approval in range(80):
        added = _lessons(rng, rng.choice([1, 1, 2, 3, 5, 12]), next_id, ["5000", "6000", "6100"])
        next_id += len(added)
        new_rows = (added + rows)[:cap]
        updated = memory_module._updated_aggregate(rows, kept, new_rows)
        built = memory_module._aggregated(new_rows)
        assert updated is not None and held(updated) == held(built), approval
        assert {line: [id(r) for r in rs] for line, rs in updated.lines.items()} == {
            line: [id(r) for r in rs] for line, rs in built.lines.items()}  # fmt: skip
        assert held(memory_module._Aggregated(list(updated.groups.values()))) == held(
            memory_module._Aggregated([built.groups[k] for k in updated.groups]))  # fmt: skip
        rows, kept = new_rows, updated


def test_the_memory_kept_is_updated_after_an_approval_and_built_again_after_any_other_change(
    tmp_path, reference, monkeypatch
):
    """Through the store: an approval's lessons update the index and the patterns kept (none built); lessons
    forgotten, the memory read again, or more than ``UPDATE_MOST`` lessons at once build them again."""
    from ap_coder.core import memory as memory_module

    built, real = [], suggest._build_memory
    monkeypatch.setattr(suggest, "_build_memory", lambda rows, ok: built.append(len(rows)) or real(rows, ok))
    monkeypatch.setattr(suggest, "_MEMORY", suggest.RowsCache())
    monkeypatch.setattr(memory_module, "_AGGREGATED", memory_module.RowsCache())
    aggregated, whole = [], memory_module._aggregated
    monkeypatch.setattr(memory_module, "_aggregated", lambda rows: aggregated.append(len(rows)) or whole(rows))
    store = Store(tmp_path / "ap.db")
    store.import_history([{"vendor_name": "Acme", "description": f"Part {n}", "gl_code": "6000", "amount": 1.0,
                           "date": f"2025-01-{n % 28 + 1:02d}"} for n in range(30)])  # fmt: skip

    def approve(description: str, gl: str) -> None:
        with sqlite3.connect(store.path) as conn:
            conn.execute("INSERT INTO feedback (vendor_key, vendor_name, description, final_gl, outcome, created_at) "
                         "VALUES ('acme', 'Acme', ?, ?, 'corrected', '2026-01-01')", (description, gl))  # fmt: skip

    assert suggest_gl("Part 3", "Acme", store.feedback_rows(), reference)[0].gl_code == "6000"
    assert aggregate(store.feedback_rows()) and (built, aggregated) == ([30], [30])
    approve("Part 3", "6100")
    approve("Part 3", "6100")  # (confirmed)
    assert suggest_gl("Part 3", "Acme", store.feedback_rows(), reference)[0].gl_code == "6100"
    assert [p.gl_code for p in aggregate(store.feedback_rows()) if p.description == "Part 3"] == ["6100", "6000"]
    assert (built, aggregated) == ([30], [30])  # updated, not built
    with sqlite3.connect(store.path) as conn:
        conn.execute("DELETE FROM feedback WHERE id = 5")  # a lesson forgotten
    suggest_gl("Part 3", "Acme", store.feedback_rows(), reference)
    aggregate(store.feedback_rows())
    assert (built, aggregated) == ([30, 31], [30, 31])
    rows = store.feedback_rows()
    assert memory_module.added_in_front(rows, [{"id": 0}] * (memory_module.UPDATE_MOST + 1) + rows) is None


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


def test_the_realistic_coding_benchmark_adds_what_the_default_leaves_out(tmp_path):
    """``--realistic``: ERP history before the first invoice, reviewer mistakes, kept chat-model guesses and a split
    account; the default run is not changed by it (its numbers stay comparable)."""
    from ap_coder.bench.__main__ import main
    from ap_coder.bench.coding import REALISTIC, SPLIT, simulate, true_gl

    assert true_gl("Monthly maintenance contract", "supply", quantity=12) == "6220"  # split only when asked
    assert (
        true_gl("Monthly maintenance contract", "supply", quantity=12, split=True)
        == SPLIT["Monthly maintenance contract"][1]
    )
    out = tmp_path / "real"
    assert main(["coding", "--vendors", "8", "--invoices", "4", "--seed", "3", "--realistic", "--out", str(out)]) == 0
    report = json.loads((out / "report.json").read_text(encoding="utf-8"))
    assert report["config"]["realism"]["mistakes"] == REALISTIC.mistakes
    rows = [json.loads(x) for x in (out / "lines.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows == simulate(8, 4, 3, REALISTIC)
    assert any(r["position"] == 1 and r["seen"] != "never" for r in rows)  # history imported first
    assert any(r["in_line"] != r["proposed"] for r in rows)  # the chat model's guesses on the blank lines
    assert any(r["approved"] != r["truth"] for r in rows)  # a mistake, or a wrong guess kept
    assert "split" in rows[0] and "history_warnings" in report["summary"]
    plain = simulate(8, 4, 3)
    assert "approved" not in plain[0] and len(plain) == len(rows)


def test_the_coding_benchmark_scores_line_tax_codes_and_lessons(tmp_path):
    """Each line has a true tax code (BC: goods GST and PST, services GST only, utilities their own), the invoice
    charges its taxes on those lines, and the report scores the codes proposed for sure (precision and coverage) and
    the line lessons shown."""
    from ap_coder.bench.coding import invoice_taxes, run_coding, true_tax

    assert true_tax("Electrician - labour", "supply") == "GST~PST"
    assert true_tax("Copy paper 8.5x11, case of 10", "supply") == "GST+PST"
    assert true_tax("Basic monthly charge", "water") == "EXEMPT"

    class L:
        def __init__(self, amount):
            self.amount = amount

    assert invoice_taxes([L(100.0), L(50.0)], ["GST+PST", "GST~PST"]) == {"gst_amount": 7.5, "pst_amount": 7.0}
    report = run_coding(5, 4, 2, tmp_path / "tax")
    tc = report["summary"]["tax_codes"]
    assert tc["lines"] == report["summary"]["overall"]["lines"] and 0 < tc["sure"] <= tc["lines"]
    assert tc["precision"] is not None and tc["coverage"] is not None
    assert set(report["summary"]["lessons"]) == {"lines", "shown", "wrong", "noise", "screen", "screen_wrong"}
    assert "## Line tax codes" in (tmp_path / "tax" / "report.md").read_text(encoding="utf-8")
