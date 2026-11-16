"""The benchmark's comparison of Azure Document Intelligence with AP Coder's own readers (ap_coder/bench/di_compare.py,
D-245), with answers made here from the made-up invoices' truth (nothing is asked of Azure)."""

import gzip
import json

from ap_coder.bench import di_compare
from ap_coder.bench.generator import load_cases
from ap_coder.bench.harness import run
from ap_coder.capture import DI_FIELDS
from ap_coder.capture.types import AMOUNT_FIELDS, DATE_FIELDS

TAX_RATES = {"gst_amount": "5%", "hst_amount": "13%", "pst_amount": "7%", "qst_amount": "9.975%"}


def _answer(truth, **wrong):
    """Azure's answer with the truth's values (``wrong``: what Azure reads otherwise), in the 2024-11-30 shape."""
    values = {f: e.get("value") for f, e in (truth.get("fields") or {}).items()} | wrong
    fields = {}
    for di_name, name in DI_FIELDS.items():
        value = values.get(name)
        if value in (None, ""):
            continue
        if name in AMOUNT_FIELDS:
            fields[di_name] = {"type": "currency", "valueCurrency": {"amount": float(value), "currencyCode": "CAD"},
                               "confidence": 0.95}  # fmt: skip
        elif name in DATE_FIELDS:
            fields[di_name] = {"type": "date", "valueDate": str(value), "confidence": 0.95}
        else:
            fields[di_name] = {"type": "string", "valueString": str(value), "confidence": 0.95}
    details = [{"type": "object", "valueObject": {"Amount": {"valueCurrency": {"amount": float(values[f])}},
                                                  "Rate": {"valueString": rate}}}
               for f, rate in TAX_RATES.items() if values.get(f) not in (None, "")]  # fmt: skip
    fields["TaxDetails"] = {"type": "array", "valueArray": details}
    return {"apiVersion": "2024-11-30", "modelId": "prebuilt-invoice", "pages": [{"pageNumber": 1}],
            "documents": [{"fields": fields}]}  # fmt: skip


def test_each_case_is_read_with_and_without_azure_and_compared_field_by_field(tmp_path):
    out, answers = tmp_path / "run", tmp_path / "answers"
    alone = run(n=3, seed=7, scanned=0.0, out=out, workers=1, reader="capture")
    cases = load_cases(out / "cases")
    answers.mkdir()
    first, second, third = cases
    (answers / f"{di_compare.response_key(first)}.json").write_text(json.dumps(_answer(first.truth)))
    wrong = _answer(second.truth, invoice_number="WRONG-1")
    (answers / f"{di_compare.response_key(second)}.json.gz").write_bytes(gzip.compress(json.dumps(wrong).encode()))
    (answers / f"{third.id}.old-version.0123456789ab.json").write_text("{}")  # recorded for another invoice

    both = run(n=3, seed=7, scanned=0.0, out=out, workers=1, reader="capture", di_responses=answers)
    assert {**both, "timing": None} == {**alone, "timing": None}  # the report is AP Coder's alone, as always
    readers = json.loads((out / "readers.json").read_text())
    assert readers["cases"] == 2
    assert len(readers["warnings"]) == 1 and third.id in readers["warnings"][0]
    number = readers["fields"]["invoice_number"]
    assert number["printed"] == 2 and number["azure_accuracy"] == 0.5 and number["differ"] == 1
    assert number["right_when_differ"] == 1.0  # AP Coder's own reading kept: local first
    assert readers["overall"]["both_accuracy"] >= readers["overall"]["local_accuracy"]
    text = (out / "readers.md").read_text()
    assert "| invoice_number | 2 |" in text and "Answers not used" in text


def test_the_command_line_takes_the_answers_and_record_di_asks_azure_only_for_developers(tmp_path, monkeypatch,
                                                                                         capsys):  # fmt: skip
    from ap_coder.bench.__main__ import main

    monkeypatch.delenv("AP_ALLOW_INTERNET", raising=False)
    assert main(["record-di", "--cases", str(tmp_path), "--out", str(tmp_path / "di")]) == 2
    assert "AP_ALLOW_INTERNET=1" in capsys.readouterr().err and not (tmp_path / "di").exists()
