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
    assert report["config"]["invoices"] == 8 and report["data_left"] == [], report["data_left"]  # (B-2725)
    assert not (tmp_path / "data").exists()
    assert card["all"]["company"] == [8, 8] and card["all"]["supplier"] == [8, 8], misses
    for category in ("undecided", "outside_pilot", "dup_erp", "us"):
        assert card[category]["findings"] == [1, 1], [m for m in misses if m["category"] == category]
    assert card["office"]["keying"] == [2, 2] and card["us"]["keying"] == [1, 1], misses
    # (the US supplier's state, county and city sales tax, a row each: read as one tax, kept in the expense, B-2870)
    assert card["us"]["tax"] == [1, 1] and set(report["invoices"]["us_01"]["taxes"]) == {"OTHER"}, misses
    assert card["buyer_trap"]["keying"] == [1, 1], misses  # (B-2720: the bill-to under the company's own name)
    shown = (tmp_path / "pilot_day.md").read_text(encoding="utf-8")
    assert "| all | 8 |" in shown and f"## Every miss ({len(misses)})" in shown


def test_the_commands_help_is_printed(capsys):
    """B-2723: `python -m ap_coder.bench pilotday --help` stopped on "TypeError: %o format": the --quick help said
    "15% of each category", and argparse reads a lone % as a format code."""
    import argparse

    import pytest

    parser = argparse.ArgumentParser(prog="bench")
    pilot_day.add_parser(parser.add_subparsers(dest="command"))
    with pytest.raises(SystemExit) as done:
        parser.parse_args(["pilotday", "--help"])
    assert done.value.code == 0 and "of each category" in capsys.readouterr().out


def test_a_data_folder_with_a_read_only_file_is_removed_and_what_cannot_be_is_said(tmp_path, monkeypatch):
    """B-2725: CI's Windows jobs (Python 3.11, 3.12) found the pilot day's data folder still there after a run:
    ``shutil.rmtree(..., ignore_errors=True)`` leaves a file Windows will not remove (read-only before Python 3.13, or
    held open a moment longer) and says nothing. The day's database is closed first; a read-only file is made
    writable; what cannot be removed is tried again, then said in the report."""
    import os
    import stat

    folder = tmp_path / "data"
    (folder / "inner").mkdir(parents=True)
    kept = folder / "inner" / "kept.txt"
    kept.write_text("x", encoding="utf-8")
    os.chmod(kept, stat.S_IREAD)
    assert pilot_day.remove_folder(folder) == [] and not folder.exists()
    (folder / "inner").mkdir(parents=True)
    (folder / "inner" / "held.txt").write_text("x", encoding="utf-8")

    def refused(path, *args, **kwargs):  # as Windows does while another program holds a file open
        (kwargs.get("onexc") or kwargs.get("onerror"))(os.unlink, str(folder / "inner" / "held.txt"), None)

    monkeypatch.setattr(pilot_day.os, "unlink", lambda *a, **k: (_ for _ in ()).throw(PermissionError("in use")))
    monkeypatch.setattr(pilot_day.shutil, "rmtree", refused)
    left = pilot_day.remove_folder(folder, wait=0.3)
    assert len(left) == 1 and left[0].endswith("held.txt: in use") and folder.exists()
