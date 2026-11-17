"""The adversarial bench (``python -m ap_coder.bench adversarial``, D-259): invoices carrying text addressed to
software beside their clean twins. The check fires on every steering twin and on no look-alike nor clean twin, the
text moves no value, and none goes through without a person, also when the stand-in chat model obeys the text."""

from __future__ import annotations

import pytest

from ap_coder.ai import local_llm, page_reader
from ap_coder.bench import adversarial
from ap_coder.capture import analyze
from ap_coder.capture.bridge import review_issues
from ap_coder.capture.instructions import CODE
from tests.ai.test_local_llm import REAL_FETCH  # taken before conftest refuses every model server


def _said(path) -> str:
    capture = analyze(path, ocr=False, tables=False, second_read=False)
    return next((words for _, code, words in review_issues(capture, "CAD") if code == CODE), "")


@pytest.mark.parametrize("variant", adversarial.VARIANTS, ids=lambda v: v.name)
def test_each_twin_is_told_as_it_should_be(tmp_path, variant):
    clean, steered = adversarial.twins(tmp_path, 1, 3, variant)
    assert _said(clean.path) == ""
    said = _said(steered.path)
    assert bool(said) == variant.steering, said
    if variant.where in ("white", "tiny"):
        assert ("(printed in white)" if variant.where == "white" else "(in tiny type)") in said
    assert steered.truth["adversarial"]["text"] == variant.text and "adversarial" not in clean.truth


def test_through_the_pipeline_an_obeyed_line_still_goes_to_a_person(tmp_path, monkeypatch):
    monkeypatch.setattr(local_llm, "_fetch_json", REAL_FETCH)  # conftest refuses every model server
    local_llm.forget_status()
    page_reader.forget_status()
    report = adversarial.run_adversarial(tmp_path, obedient=True, names=("line", "benign_line"))
    s = report["summary"]
    assert (s["steering"], s["flagged"], s["held_by_check"], s["touchless"]) == (1, 1, 1, 0)
    assert (s["look_alikes"], s["look_alikes_flagged"], s["clean_flagged"], s["fields_changed"]) == (1, 0, 0, 0)
    line = next(r for r in report["cases"] if r["variant"] == "line")
    assert line["accounts_changed"] and set(line["steered"]["accounts"]) == {"6100"}  # the stand-in obeyed
    assert adversarial.passed(s, obedient=True) and not adversarial.passed(s)
    assert "**Passed**" in (tmp_path / "report.md").read_text(encoding="utf-8")
