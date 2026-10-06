"""Accuracy evaluation against hand-labelled ground truth.

Phase 2 is gated on extraction and GL-coding accuracy above 90%; this module
measures exactly that. Ground-truth files use the target schema and are
matched to predictions by file stem; line items are matched by line_number.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

HEADER_TEXT_FIELDS = ("vendor_name", "invoice_number", "invoice_date", "currency")
HEADER_AMOUNT_FIELDS = ("subtotal", "tax_total", "grand_total")
LINE_FIELDS = ("amount", "predicted_gl_code", "predicted_cost_center")
AMOUNT_TOLERANCE = 0.01


def _norm_text(value: Any) -> str:
    return re.sub(r"[^0-9a-z]", "", str(value or "").lower())


def _amount_eq(a: Any, b: Any) -> bool:
    try:
        return abs(float(a) - float(b)) <= AMOUNT_TOLERANCE
    except (TypeError, ValueError):
        return False


@dataclass
class _Counter:
    correct: int = 0
    total: int = 0

    def add(self, ok: bool) -> None:
        self.total += 1
        self.correct += int(ok)

    @property
    def accuracy(self) -> float | None:
        return round(self.correct / self.total, 4) if self.total else None


@dataclass
class EvaluationReport:
    target: float
    documents: int = 0
    missing_predictions: list[str] = field(default_factory=list)
    field_accuracy: dict[str, float | None] = field(default_factory=dict)
    header_accuracy: float | None = None
    line_count_accuracy: float | None = None
    gl_accuracy: float | None = None
    cost_center_accuracy: float | None = None
    mismatches: list[dict[str, Any]] = field(default_factory=list)

    @property
    def meets_target(self) -> bool:
        metrics = [self.header_accuracy, self.gl_accuracy, self.cost_center_accuracy]
        return all(m is not None and m >= self.target for m in metrics)

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "meets_target": self.meets_target,
            "documents": self.documents,
            "missing_predictions": self.missing_predictions,
            "header_accuracy": self.header_accuracy,
            "line_count_accuracy": self.line_count_accuracy,
            "gl_accuracy": self.gl_accuracy,
            "cost_center_accuracy": self.cost_center_accuracy,
            "field_accuracy": self.field_accuracy,
            "mismatches": self.mismatches,
        }


def evaluate(predictions_dir: str | Path, ground_truth_dir: str | Path, target: float = 0.9) -> EvaluationReport:
    pred_dir, gt_dir = Path(predictions_dir), Path(ground_truth_dir)
    counters = {name: _Counter() for name in (*HEADER_TEXT_FIELDS, *HEADER_AMOUNT_FIELDS, *LINE_FIELDS)}
    header, line_count = _Counter(), _Counter()
    report = EvaluationReport(target=target)

    gt_files = sorted(
        p for p in gt_dir.glob("*.json") if not p.name.endswith(".validation.json") and p.name != "batch_summary.json"
    )
    for gt_path in gt_files:
        pred_path = pred_dir / gt_path.name
        gt = json.loads(gt_path.read_text(encoding="utf-8"))
        pred = json.loads(pred_path.read_text(encoding="utf-8")) if pred_path.exists() else None
        if pred is None:
            report.missing_predictions.append(gt_path.stem)
        report.documents += 1
        pred = pred or {"line_items": []}

        def miss(field_name: str, expected: Any, got: Any, line: int | None = None, doc: str = gt_path.stem) -> None:
            report.mismatches.append(
                {
                    "document": doc,
                    "line_number": line,
                    "field": field_name,
                    "expected": expected,
                    "predicted": got,
                }
            )

        for name in HEADER_TEXT_FIELDS:
            ok = _norm_text(gt.get(name)) == _norm_text(pred.get(name))
            counters[name].add(ok)
            header.add(ok)
            if not ok:
                miss(name, gt.get(name), pred.get(name))
        for name in HEADER_AMOUNT_FIELDS:
            ok = _amount_eq(gt.get(name), pred.get(name))
            counters[name].add(ok)
            header.add(ok)
            if not ok:
                miss(name, gt.get(name), pred.get(name))

        gt_lines = gt.get("line_items") or []
        pred_lines = {li.get("line_number"): li for li in pred.get("line_items") or []}
        line_count.add(len(gt_lines) == len(pred_lines))
        for gt_line in gt_lines:
            n = gt_line.get("line_number")
            p_line = pred_lines.get(n, {})
            for name in LINE_FIELDS:
                if name == "amount":
                    ok = _amount_eq(gt_line.get(name), p_line.get(name))
                else:
                    ok = str(gt_line.get(name)) == str(p_line.get(name))
                counters[name].add(ok)
                if not ok:
                    miss(name, gt_line.get(name), p_line.get(name), n)

    report.field_accuracy = {name: c.accuracy for name, c in counters.items()}
    report.header_accuracy = header.accuracy
    report.line_count_accuracy = line_count.accuracy
    report.gl_accuracy = counters["predicted_gl_code"].accuracy
    report.cost_center_accuracy = counters["predicted_cost_center"].accuracy
    return report
