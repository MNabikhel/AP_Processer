import copy
import json

from ap_coder.evaluation import evaluate


def _write(d, name, data):
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.json").write_text(json.dumps(data))


def test_evaluation_scores_fields(tmp_path, ground_truth):
    pred = copy.deepcopy(ground_truth)
    pred["vendor_name"] = "CONTOSO CLOUD SOLUTIONS LTD."  # normalised match
    pred["line_items"][0]["predicted_gl_code"] = "6020"
    pred["line_items"][1]["predicted_cost_center"] = "CC100"
    _write(tmp_path / "gt", "inv", ground_truth)
    _write(tmp_path / "pred", "inv", pred)
    _write(tmp_path / "gt", "missing", ground_truth)

    report = evaluate(tmp_path / "pred", tmp_path / "gt")
    assert report.documents == 2
    assert report.missing_predictions == ["missing"]
    assert report.field_accuracy["vendor_name"] == 0.5
    assert report.gl_accuracy == round(6 / 14, 4)
    assert report.cost_center_accuracy == round(6 / 14, 4)
    assert report.meets_target is False
    assert any(m["field"] == "predicted_gl_code" and m["line_number"] == 1 for m in report.mismatches)
