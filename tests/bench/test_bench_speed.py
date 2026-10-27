"""``python -m ap_coder.bench speed``: seconds per invoice through the app's pipeline, in a data folder of its own."""

import json
import os

from ap_coder.bench.__main__ import main


def test_the_speed_benchmark_times_each_invoice_twice_in_its_own_data_folder(tmp_path, capsys):
    before = os.environ.get("AP_PRIVATE_DIR")
    assert main(["speed", "--n", "3", "--scanned", "0", "--seed", "5", "--history", "200", "--out", str(tmp_path)]) == 0
    report = json.loads((tmp_path / "speed.json").read_text(encoding="utf-8"))
    digital = report["seconds"]["digital"]
    assert digital["arrival"]["n"] == 3 and digital["page_reader"]["n"] == 3 and not report["errors"]
    assert report["config"]["history"] == 200 and report["computer"]["cores"]
    assert (tmp_path / "data" / "ap_coder.db").is_file()  # its own database, not AP's
    assert os.environ.get("AP_PRIVATE_DIR") == before  # the data folder put back
    assert "| digital | arrival | 3 |" in (tmp_path / "speed.md").read_text(encoding="utf-8")
    assert "s per invoice on arrival" in capsys.readouterr().out
