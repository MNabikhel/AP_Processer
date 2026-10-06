"""Redacted run summary that is safe to paste into a chat or ticket.

The report contains only aggregate statistics, issue codes, model names and
accuracy figures. It never includes vendor names, amounts, descriptions,
reasoning text, error messages or file names. Invoices appear as ``doc-01``,
``doc-02``, ...; the mapping back to real file names is written to a separate
local-only key file. GL / cost-center codes are omitted unless
``include_codes`` is set.
"""

from __future__ import annotations

import csv
import json
import statistics
from collections import Counter
from pathlib import Path

from .evaluation import evaluate

_CONF_BUCKETS = ((0.0, 0.5), (0.5, 0.7), (0.7, 0.85), (0.85, 0.95), (0.95, 1.01))

# Safe, content-free categories for pipeline failures (raw messages may contain invoice data).
_FAILURE_PATTERNS = (
    ("refused", "model_refusal"),
    ("truncated", "output_truncated"),
    ("content filter", "content_filter"),
    ("failed validation", "invalid_output_after_repair"),
    ("not set", "missing_configuration"),
    ("Unsupported file type", "unsupported_file_type"),
)


def _failure_category(error: str | None) -> str:
    if not error:
        return "unknown"
    exc_type = error.split(":", 1)[0].strip()
    for needle, category in _FAILURE_PATTERNS:
        if needle in error:
            return f"{exc_type}/{category}"
    return exc_type if exc_type.isidentifier() else "unknown"


def _stats(values: list[float]) -> str:
    if not values:
        return "n/a"
    return f"mean {statistics.fmean(values):.2f}, min {min(values):.2f}, max {max(values):.2f}"


def _bucket_line(values: list[float]) -> str:
    counts = []
    for lo, hi in _CONF_BUCKETS:
        n = sum(1 for v in values if lo <= v < hi)
        label = f"{lo:.2f}-{min(hi, 1.0):.2f}"
        counts.append(f"{label}: {n}")
    return " | ".join(counts)


def build_share_report(
    output_dir: str | Path,
    ground_truth: str | Path | None = None,
    include_codes: bool = False,
    key_file: str | Path | None = None,
) -> str:
    output_dir = Path(output_dir)
    metas = [json.loads(p.read_text(encoding="utf-8")) for p in sorted(output_dir.glob("*.validation.json"))]
    if not metas:
        raise FileNotFoundError(f"No *.validation.json files in {output_dir}; run `process` first")

    aliases = {Path(m["source"]).stem: f"doc-{i:02d}" for i, m in enumerate(metas, start=1)}
    if key_file:
        Path(key_file).parent.mkdir(parents=True, exist_ok=True)
        with Path(key_file).open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(["alias", "file"])
            for m in metas:
                writer.writerow([aliases[Path(m["source"]).stem], Path(m["source"]).name])

    ok = [m for m in metas if m.get("status") == "ok"]
    failed = [m for m in metas if m.get("status") != "ok"]
    lines = ["## AP Coder run summary (redacted)", ""]
    lines.append(f"- invoices: {len(metas)} (ok {len(ok)}, failed {len(failed)})")
    if failed:
        cats = Counter(_failure_category(m.get("error")) for m in failed)
        lines.append("- failures: " + ", ".join(f"{k} x{v}" for k, v in cats.most_common()))

    ext = [m["extraction"] for m in metas if m.get("extraction")]
    if ext:
        lines.append("- extraction models: " + ", ".join(sorted({e["model_id"] for e in ext})))
        lines.append(f"- pages per invoice: {_stats([e['page_count'] for e in ext])}")
        lines.append(f"- tables per invoice: {_stats([e['table_count'] for e in ext])}")
        ocr = [e["mean_word_confidence"] for e in ext if e.get("mean_word_confidence") is not None]
        lines.append(f"- mean OCR word confidence: {_stats(ocr)}")

    inf = [m["inference"] for m in metas if m.get("inference")]
    if inf:
        lines.append("- LLM models: " + ", ".join(sorted({i["model"] for i in inf})))
        prompt = [i["usage"].get("prompt_tokens", 0) for i in inf]
        completion = [i["usage"].get("completion_tokens", 0) for i in inf]
        cached = sum(i["usage"].get("cached_prompt_tokens", 0) for i in inf)
        lines.append(f"- prompt tokens/invoice: {_stats(prompt)}; cached share {cached / max(sum(prompt), 1):.0%}")
        lines.append(f"- completion tokens/invoice: {_stats(completion)}")
        lines.append(f"- repair retries needed: {sum(1 for i in inf if i.get('attempts', 1) > 1)}")
        lines.append(f"- invoices sent with page images: {sum(1 for i in inf if i.get('images_attached'))}")

    for stage in ("extraction", "inference"):
        secs = [m["timings_seconds"][stage] for m in metas if stage in (m.get("timings_seconds") or {})]
        if secs:
            lines.append(f"- {stage} seconds: {_stats(secs)}")

    vals = [m["validation"] for m in metas if m.get("validation")]
    if vals:
        review = sum(1 for v in vals if v["requires_review"])
        lines += [
            "",
            "### Validation",
            f"- requires_review: {review}/{len(vals)} ({review / len(vals):.0%})",
            f"- model confidence buckets: {_bucket_line([v['model_confidence'] for v in vals])}",
            f"- adjusted confidence buckets: {_bucket_line([v['adjusted_confidence'] for v in vals])}",
        ]
        occurrences: Counter[str] = Counter()
        invoices_hit: Counter[str] = Counter()
        for v in vals:
            codes = [i["code"] for i in v["issues"]]
            occurrences.update(codes)
            invoices_hit.update(set(codes))
        if occurrences:
            lines.append("- issue codes (invoices affected / occurrences):")
            for code, n in occurrences.most_common():
                lines.append(f"  - {code}: {invoices_hit[code]} / {n}")
        else:
            lines.append("- issue codes: none")

        lines += ["", "### Per invoice", "| doc | pages | lines | model conf | adj conf | review | issue codes |"]
        lines.append("|---|---|---|---|---|---|---|")
        for m in metas:
            alias = aliases[Path(m["source"]).stem]
            v = m.get("validation")
            pages = (m.get("extraction") or {}).get("page_count", "")
            if not v:
                lines.append(f"| {alias} | {pages} | | | | FAILED: {_failure_category(m.get('error'))} | |")
                continue
            line_count = v.get("checks", {}).get("line_count", "")
            issues = ", ".join(
                f"{i['code']}" + (f"@L{i['line_number']}" if i.get("line_number") else "") for i in v["issues"]
            )
            lines.append(
                f"| {alias} | {pages} | {line_count} | {v['model_confidence']:.2f} | {v['adjusted_confidence']:.2f} "
                f"| {'YES' if v['requires_review'] else 'no'} | {issues or '-'} |"
            )

    if ground_truth:
        lines += ["", *_accuracy_section(output_dir, ground_truth, include_codes, aliases)]

    lines += ["", f"_codes included: {'yes' if include_codes else 'no'}_"]
    return "\n".join(lines) + "\n"


def _accuracy_section(
    output_dir: Path, ground_truth: str | Path, include_codes: bool, aliases: dict[str, str]
) -> list[str]:
    report = evaluate(output_dir, ground_truth)

    def pct(v: float | None) -> str:
        return "n/a" if v is None else f"{v:.1%}"

    out = [
        "### Accuracy vs ground truth",
        f"- documents scored: {report.documents} (unreviewed skipped: {len(report.unreviewed_documents)}, "
        f"missing predictions: {len(report.missing_predictions)})",
        f"- header accuracy: {pct(report.header_accuracy)}",
        f"- GL code accuracy: {pct(report.gl_accuracy)}",
        f"- cost center accuracy: {pct(report.cost_center_accuracy)}",
        f"- line count exact: {pct(report.line_count_accuracy)}",
        f"- meets {report.target:.0%} target: {'YES' if report.meets_target else 'no'}",
        "- per field: " + ", ".join(f"{k} {pct(v)}" for k, v in report.field_accuracy.items()),
    ]
    by_field = Counter(m["field"] for m in report.mismatches)
    if by_field:
        out.append("- mismatches by field: " + ", ".join(f"{k} x{v}" for k, v in by_field.most_common()))
    if include_codes:
        for field_name, label in (("predicted_gl_code", "GL"), ("predicted_cost_center", "Cost center")):
            pairs = Counter(
                (str(m["expected"]), str(m["predicted"])) for m in report.mismatches if m["field"] == field_name
            )
            if pairs:
                out.append(f"- {label} confusions (expected -> predicted x count):")
                out += [f"  - {e} -> {p} x{n}" for (e, p), n in pairs.most_common(25)]
        where = Counter(
            aliases.get(m["document"], "doc-??")
            for m in report.mismatches
            if m["field"] in {"predicted_gl_code", "predicted_cost_center"}
        )
        if where:
            out.append("- coding mismatches per doc: " + ", ".join(f"{d} x{n}" for d, n in sorted(where.items())))
    return out
