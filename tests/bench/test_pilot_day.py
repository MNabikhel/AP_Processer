"""``python -m ap_coder.bench pilotday`` (D-275): the pilot tester's day on the made-up construction group, here a few
invoices: the books and their files as the tester makes them, the day through the app, its scorecard and misses."""

from ap_coder.bench import jde_group, pilot_day


def test_the_groups_files_are_as_reportsnow_saves_them(tmp_path):
    books = jde_group.construction_books(1)
    assert {c for c, *_ in books.companies} >= set(jde_group.PILOT) and len(books.companies) == 7
    assert [cur for _c, _n, cur, ctr in books.companies if ctr == "US"] == ["USD", "USD"]
    path = tmp_path / "chart.txt"
    assert jde_group.write_chart_text(path, books) == len(books.accounts)
    raw = path.read_bytes()
    assert raw[:2] == b"\xff\xfe"  # UTF-16 with its byte order mark
    rows = raw.decode("utf-16").split("\r\n")
    assert rows[0].split("\t")[:2] == ["Company", "Business Unit"] and rows[1].split("\t")[1] == "     2101000"
    assert any('"' in row.split("\t")[4] for row in rows[1:-1]) and any("\tZ\t" in row for row in rows)
    closed = {u.code for u in books.units if u.status}
    assert closed and all(code[-4:] in ("2401", "2402") for code in closed)
    assert any(li.subledger for li in books.po_lines) and any(li.description == "FREIGHT" for li in books.po_lines)


def test_a_small_day_is_read_approved_keyed_and_scored(tmp_path):
    counts = {"po": 1, "office": 2, "undecided": 1, "us": 1, "outside_pilot": 1, "dup_erp": 1, "buyer_trap": 1}
    report = pilot_day.run(tmp_path, counts=counts, scanned=0)
    card, misses = report["scorecard"], report["misses"]
    assert report["config"]["invoices"] == 8 and not (tmp_path / "data").exists()
    assert card["all"]["company"] == [8, 8] and card["all"]["supplier"] == [8, 8], misses
    for category in ("undecided", "outside_pilot", "dup_erp", "us"):
        assert card[category]["findings"] == [1, 1], [m for m in misses if m["category"] == category]
    assert card["office"]["keying"] == [2, 2] and card["us"]["keying"] == [1, 1], misses
    assert card["buyer_trap"]["keying"] == [1, 1], misses  # (B-2720: the bill-to under the company's own name)
    shown = (tmp_path / "pilot_day.md").read_text(encoding="utf-8")
    assert "| all | 8 |" in shown and f"## Every miss ({len(misses)})" in shown
