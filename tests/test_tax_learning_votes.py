"""Which approvals teach a line's tax code, and from which lines (D-80): the same place of supply, the same wording
for an exemption, never a generic description, and only approvals a person made of a code nobody had proposed for
sure (a kept learned code or guess is no vote)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ap_coder.coding.line_tax import GUESS_NOTE, LEARNED_NOTE, parse_code
from ap_coder.core.store import SCHEMA_VERSION, Store

from .test_tax_learning_safety import BOOTS, VENDOR, _learn, _lines, _row

# --- 6. Lines worded alike are not the same supply for tax (B-762) ---------------------------------------------------


def test_an_exemption_is_learned_only_from_the_same_wording():
    pharmacy = "Corner Pharmacy"
    rx = [_row(n, "Prescription drugs", "ZERO", "AB", pharmacy) for n in (1, 2)]
    assert _learn(_lines(("Prescription drugs", 80.0), ("Non-prescription drugs", 37.5)), rx, pharmacy, "AB") == [
        "ZERO", ""]  # fmt: skip
    hotel = [_row(n, "Hotel room", "GST~PST") for n in (1, 2)]
    assert _learn(_lines(("Hotel room service", 50.0)), hotel) == [""]
    milk = [_row(n, "Milk 2% 4L jug", "ZERO", "AB", "Fresh Foods Inc") for n in (1, 2)]
    assert _learn(_lines(("Chocolate milk 4L jug", 20.0), ("MILK 2% 4L JUG", 5.0)), milk, "Fresh Foods Inc",
                  "AB") == ["", "ZERO"]  # fmt: skip
    # Numbers and units are not words: "labour, 4 h" is the same supply as "labour 2 h".
    labour = [_row(1, "Electrician - labour", "GST~PST"), _row(2, "ELECTRICIAN - LABOUR 2 h", "GST~PST")]
    assert _learn(_lines(("Electrician - labour, 4 h", 400.0)), labour) == ["GST~PST"]


def test_a_taxed_code_matches_alike_wording_but_not_a_negation():
    taxed = [_row(n, "Prescription drugs", "GST", "AB", "Corner Pharmacy") for n in (1, 2)]
    assert _learn(_lines(("Non-prescription drugs", 37.5)), taxed, "Corner Pharmacy", "AB") == [""]
    hats = [_row(n, "Hard hat white", "GST+PST") for n in (1, 2)]
    assert _learn(_lines(("Hard hat white class E", 30.0)), hats) == ["GST+PST"]


def test_nothing_is_learned_from_a_generic_description():
    rows = [_row(n, "Misc", "GST~PST") for n in (1, 2)] + [_row(n, "Services", "EXEMPT", line=2) for n in (3, 4)]
    assert _learn(_lines(("Misc", 10.0), ("Services", 10.0), ("Misc items", 5.0)), rows) == ["", "", ""]


# --- 2. The place of supply: a BC exemption says nothing of a Manitoba supply (B-763) ---------------------------------


def test_a_code_is_learned_only_from_approvals_of_the_same_place_of_supply():
    boots = _lines(("Safety boots steel toe", 200.0))
    assert _learn(boots, BOOTS, province="BC") == ["GST~PST"]
    assert _learn(boots, BOOTS, province="MB") == [""]  # S1g, S1h: Manitoba's RST is another tax
    assert _learn(boots, BOOTS, province="") == [""]  # the place of supply unknown: nothing learned
    unknown = [{**r, "tax_province": ""} for r in BOOTS]  # a row that does not say where: no vote
    assert _learn(boots, unknown) == [""]


# --- 7. Only a person's own decisions are votes (B-764) ---------------------------------------------------------------


def test_votes_come_from_two_days_or_one_pick_and_a_contradiction_still_vetoes():
    hat = _lines(("Hard hat", 30.0))
    same_day = [_row(1, "Hard hat", "GST~PST", day=5), _row(2, "Hard hat", "GST~PST", day=5)]
    assert _learn(hat, same_day) == [""]  # two approvals minutes apart are not two looks
    assert _learn(hat, [_row(1, "Hard hat", "GST~PST", day=5), _row(2, "Hard hat", "GST~PST", day=6)]) == ["GST~PST"]
    picked = [_row(1, "Hard hat", "GST~PST", source="picked")]
    assert _learn(hat, picked) == ["GST~PST"]  # a person picked it on review: one is enough
    assert _learn(hat, [*picked, _row(2, "Hard hat", "GST+PST")]) == [""]  # one contradiction vetoes
    # The same invoice twice (two lines, or approved again) is one vote.
    assert _learn(hat, [_row(1, "Hard hat", "GST~PST"), _row(1, "Hard hat", "GST~PST", day=9, line=2)]) == [""]


def _approve(store: Store, path: Path, description: str, proposed: str, final: str, *, note: str = "",
             bulk: bool = False, ship_to: str = "BC") -> int:  # fmt: skip
    def line(code: str, reason: str) -> dict:
        parsed = parse_code(code)
        assert parsed is not None
        return {"line_number": 1, "description": description, "quantity": 1, "unit_price": 400.0, "amount": 400.0,
                "predicted_gl_code": "6220", "predicted_cost_center": "", "taxes_applied": parsed[0],
                "tax_exempt": parsed[1], "tax_status": parsed[2],
                "reasoning_justification": reason}  # fmt: skip

    def output(li: dict) -> dict:
        return {"vendor_name": VENDOR, "invoice_number": path.stem, "invoice_date": "2026-08-21", "currency": "CAD",
                "ship_to_province": ship_to, "supplier_province": "BC", "subtotal": 400.0,
                "tax_lines": [{"tax_type": "GST", "province": "", "rate": 0.05, "taxable_amount": 400.0,
                               "tax_amount": 20.0}],
                "tax_total": 20.0, "grand_total": 420.0, "line_items": [li]}  # fmt: skip

    invoice_id = store.add_invoice(path, output(line(proposed, note)), None)
    store.approve_invoice(invoice_id, json.loads(json.dumps(output(line(final, "")))), "Clerk", bulk=bulk)
    return invoice_id


def test_an_approval_records_where_and_whether_a_person_decided_the_code(tmp_path):
    store = Store(tmp_path / "s.db")
    _approve(store, tmp_path / "A.pdf", "Electrician - labour", "GST", "GST~PST")  # changed: picked
    _approve(store, tmp_path / "B.pdf", "Electrician - labour", "GST~PST", "GST~PST")  # as proposed for sure
    _approve(store, tmp_path / "C.pdf", "Electrician - labour", "GST~PST", "GST~PST", note=LEARNED_NOTE)
    _approve(store, tmp_path / "D.pdf", "LED panel", "GST", "GST", note=GUESS_NOTE)  # a guess left as it was
    _approve(store, tmp_path / "E.pdf", "LED panel", "GST+PST", "GST", bulk=True)  # nobody opened it
    _approve(store, tmp_path / "F.pdf", "LED panel", "GST", "GST", ship_to="MB")
    got = [
        (r["final_tax_code"], r["tax_source"], r["tax_province"])
        for r in sorted(store.feedback_rows(), key=lambda r: r["id"])
    ]
    assert got == [("GST~PST", "picked", "BC"), ("GST~PST", "approved", "BC"), ("", "kept", "BC"),
                   ("", "guess", "BC"), ("", "bulk", "BC"), ("GST", "approved", "MB")]  # fmt: skip


def test_two_bulk_approvals_of_a_guessed_code_teach_nothing(tmp_path):
    """The code review's repro: two bulk approvals of codes the solver only guessed reached the two votes, and the
    guess became "Learned from past approvals"."""
    store = Store(tmp_path / "s.db")
    for n in (1, 2):
        _approve(store, tmp_path / f"G{n}.pdf", "Copper wire spool", "GST", "GST", note=GUESS_NOTE, bulk=n == 1)
    assert _learn(_lines(("Copper wire spool", 100.0)), store.feedback_rows()) == [""]


# --- 3. Approvals made before the exemptions were recorded teach no exemption (B-765) ---------------------------------


def _drop_columns(path: Path, *columns: str, version: str) -> None:
    with sqlite3.connect(path) as conn:
        for column in columns:
            conn.execute(f"ALTER TABLE feedback DROP COLUMN {column}")
        conn.execute("UPDATE settings SET value = ? WHERE key = 'schema_version'", (version,))


def test_the_backfill_does_not_turn_legacy_approvals_into_learned_exemptions(tmp_path):
    path = tmp_path / "old.db"
    store = Store(path)
    for n in (1, 2):  # approved before tax_exempt existed: the PST not on the labour line was only inferred
        lines = [{"line_number": k, "description": d, "quantity": 1, "unit_price": a, "amount": a,
                  "predicted_gl_code": "6220", "predicted_cost_center": "", "taxes_applied": taxes}
                 for k, (d, a, taxes) in enumerate([("Electrician - labour", 400.0, ["GST"]),
                                                    ("LED panel", 100.0, ["GST", "PST"])], 1)]  # fmt: skip
        output = {"vendor_name": VENDOR, "invoice_number": f"L{n}", "invoice_date": "2026-08-21", "currency": "CAD",
                  "ship_to_province": "BC", "subtotal": 500.0, "tax_total": 32.0, "grand_total": 532.0,
                  "tax_lines": [{"tax_type": "GST", "province": "", "rate": 0.05, "taxable_amount": 500.0,
                                 "tax_amount": 25.0},
                                {"tax_type": "PST", "province": "BC", "rate": 0.07, "taxable_amount": 100.0,
                                 "tax_amount": 7.0}], "line_items": lines}  # fmt: skip
        invoice_id = store.add_invoice(tmp_path / f"L{n}.pdf", output, None)
        store.approve_invoice(invoice_id, json.loads(json.dumps(output)), "Clerk")
    _drop_columns(path, "final_tax_code", "tax_province", "tax_source", version="16")
    rows = sorted(Store(path).feedback_rows(), key=lambda r: r["id"])
    assert [(r["description"], r["final_tax_code"], r["tax_source"], r["tax_province"]) for r in rows] == [
        ("Electrician - labour", "GST~PST", "legacy", "BC"),
        ("LED panel", "GST+PST", "approved", "BC"),
    ] * 2
    assert _learn(_lines(("Electrician - labour", 400.0)), rows) == [""]
    # A legacy row still vetoes: it says AP once approved another code on such a line.
    assert _learn(_lines(("Electrician - labour", 400.0)), [*rows, _row(9, "Electrician - labour", "GST+PST",
                                                                         source="picked")]) == [""]  # fmt: skip


def test_a_database_at_17_without_the_new_columns_gets_them(tmp_path):
    path = tmp_path / "branch.db"
    store = Store(path)
    _approve(store, tmp_path / "A.pdf", "Electrician - labour", "GST", "GST~PST")
    _drop_columns(path, "tax_province", "tax_source", version="17")
    upgraded = Store(path)
    assert SCHEMA_VERSION == 17 and upgraded.get_setting("schema_version") == "17"
    assert [(r["final_tax_code"], r["tax_source"], r["tax_province"]) for r in upgraded.feedback_rows()] == [
        ("GST~PST", "picked", "BC")]  # fmt: skip
