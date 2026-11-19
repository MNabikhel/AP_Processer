"""``python -m ap_coder.bench companies --full-chart ROWS`` (D-272): the group's whole chart unfiltered, written as
ReportsNow's tab-delimited text, loaded by the command line and asked what the pages and the checks ask, here small."""

from ap_coder.bench import companies_speed, jde_group


def test_the_whole_chart_is_written_loaded_by_the_command_line_and_timed(tmp_path):
    path = tmp_path / "chart.txt"
    assert jde_group.write_full_chart(path, 2160, companies=4) == 2160
    head = path.read_text(encoding="utf-8").splitlines()[:3]
    assert head[0].split("\t")[:2] == ["Company", "Business Unit"] and head[1].split("\t")[1] == "     2100001"
    found = companies_speed.full_chart(2160, companies=4)
    said = found["what the load said"]
    assert any(line.startswith("Read 2,160 rows of the chart: 2,160 accepted") for line in said), said
    assert any("Z " in line and "not yet decided" in line for line in said)
    assert found["refresh with changes: changed, removed"][0] > 0 and found["a line's account check, µs (median)"] > 0
