"""``python -m ap_coder.bench lines``: line items read, corrected by AP and right next time (D-120)."""

from ap_coder.bench.lines import account_of, pair, run_lines


def test_digital_invoices_read_every_line_and_each_item_keeps_the_account_ap_gave_it(tmp_path):
    s = run_lines(2, 3, seed=1, scanned=0.0, out=str(tmp_path))["summary"]
    assert s["lines"] > 10 and s["descriptions_right"] == 1.0 and s["descriptions_corrected"] == 0
    assert s["known_items"] and s["known_items_gl_right"] == 1.0  # the coding memory knows each item by its words
    assert s["renamed_other_item"] == 0
    assert (tmp_path / "lines.md").read_text().startswith("# Line items, right next time")


def test_each_printed_line_pairs_with_the_line_read_for_it():
    read = [{"description": "a", "amount": 10.0}, {"description": "b", "amount": 5.0}]
    truth = [
        {"description": "B", "amount": 5.0},
        {"description": "C", "amount": 7.0},
        {"description": "A", "amount": 10},
    ]
    assert [(g or {}).get("description") for g, _ in pair(read, truth)] == ["b", None, "a"]
    assert account_of("Bearing 6205-2RS") == account_of(" Bearing  6205-2RS ")
