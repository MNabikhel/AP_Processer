"""Azure Document Intelligence beside AP Coder's own readers, on the benchmark's made-up invoices (D-245).

``python -m ap_coder.bench run --di-responses DIR`` reads every case twice, the same way: once as AP Coder reads it
on its own (the run's report, as always), once with Azure's recorded answer for the case among the readers. It writes
``readers.json`` and ``readers.md``: for each field, how often AP Coder alone, Azure alone and the two together were
right, how often the two agreed, and how often the value kept was right when they agreed and when they didn't.

The answers are Azure's ``analyzeResult`` (API 2024-11-30), one file a case: ``<case id>.<generator version>.<the
truth's hash>.json`` (or ``.json.gz``), keyed by the invoice's truth rather than the PDF's bytes, so a PyMuPDF update
that writes the same invoice differently doesn't orphan them; a file for a case whose truth has changed is named in
a warning and not used.

``python -m ap_coder.bench record-di --cases DIR --out DIR`` records them: a developer's command, only with
``AP_ALLOW_INTERNET=1`` and an endpoint and key in the environment, and only for the benchmark's made-up invoices
(never a company's).
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

from ap_coder.bench.generator import GENERATOR_VERSION, Case
from ap_coder.bench.scoring import values_match
from ap_coder.capture.types import FIELDS, VERIFIED, CaptureResult


def response_key(case: Case) -> str:
    """The name of a case's answer file (without its suffix): its id, the generator version and its truth's hash."""
    digest = hashlib.sha256(json.dumps(case.truth, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return f"{case.id}.{GENERATOR_VERSION}.{digest[:12]}"


def _read(path: Path) -> dict[str, Any]:
    data = gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path.read_bytes()
    raw = json.loads(data.decode("utf-8"))
    return raw if isinstance(raw, dict) else {}


def load_responses(folder: str | Path, cases: list[Case]) -> tuple[dict[str, Path], list[str]]:
    """({case id: its answer file}, warnings): the answers that match each case's truth, and the cases whose answer
    was recorded for another truth or generator version (not used)."""
    folder = Path(folder)
    found: dict[str, Path] = {}
    warnings: list[str] = []
    for case in cases:
        key = response_key(case)
        for suffix in (".json", ".json.gz"):
            if (folder / f"{key}{suffix}").is_file():
                found[case.id] = folder / f"{key}{suffix}"
                break
        else:
            stale = sorted(p.name for p in folder.glob(f"{case.id}.*.json*"))
            if stale:
                warnings.append(f"{case.id}: the answer {stale[0]} was recorded for another version of this invoice "
                                "(record it again): not used")  # fmt: skip
    return found, warnings


def read_with_di(job: tuple[str, str | None, str]) -> dict[str, Any]:
    """Read one case with its recorded answer among the readers (a process pool's job: path, received, answer)."""
    import time

    from ap_coder.capture import analyze

    path, received, answer = job
    t0 = time.perf_counter()
    try:
        raw = _read(Path(answer))
        day = dt.date.fromisoformat(received) if received else None
        result = analyze(path, today=day, di_raw=raw)
        return {"result": result.to_dict(), "error": None, "seconds": time.perf_counter() - t0, "azure": _azure(raw)}
    except Exception as e:  # noqa: BLE001 - a reader crash is a benchmark result
        return {"result": None, "error": f"{type(e).__name__}: {e}", "seconds": time.perf_counter() - t0, "azure": {}}


def _azure(raw: dict[str, Any]) -> dict[str, Any]:
    """Azure's own value of each field, as capture maps it."""
    from ap_coder.capture import _di_readings

    return {name: found[0].value for name, found in _di_readings(raw).items() if found}


def _fields(out: dict[str, Any] | None) -> dict[str, Any]:
    result = CaptureResult.from_dict(out["result"]) if out and out.get("result") else None
    return result.fields if result else {}


def compare(cases: list[Case], alone: list[dict[str, Any]], with_di: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Per field and overall: AP Coder alone, Azure alone, the two together (each: right / printed), how often they
    agreed, the value kept right when they agreed and when they didn't, and verified (precision, coverage) with
    Azure. ``with_di``: {case id: its reading with Azure}; only those cases are compared."""
    rows: dict[str, dict[str, int]] = {}
    for case, out in zip(cases, alone, strict=True):
        both = with_di.get(case.id)
        if both is None:
            continue
        own, merged, azure = _fields(out), _fields(both), both.get("azure") or {}
        for f, entry in (case.truth.get("fields") or {}).items():
            if f not in FIELDS:
                continue
            row = rows.setdefault(f, dict.fromkeys(
                ("printed", "local", "azure", "both", "agree", "agree_right", "differ", "differ_right",
                 "verified", "verified_right"), 0))  # fmt: skip
            truth = entry.get("value")
            mine = own[f].value if f in own else None
            theirs, kept = azure.get(f), merged[f] if f in merged else None
            row["printed"] += 1
            row["local"] += values_match(f, truth, mine)
            row["azure"] += values_match(f, truth, theirs)
            right = kept is not None and values_match(f, truth, kept.value)
            row["both"] += right
            if mine not in (None, "") and theirs not in (None, ""):
                same = values_match(f, mine, theirs)
                row["agree" if same else "differ"] += 1
                row["agree_right" if same else "differ_right"] += right
            if kept is not None and kept.status == VERIFIED:
                row["verified"] += 1
                row["verified_right"] += right
    total = {k: sum(r[k] for r in rows.values()) for k in next(iter(rows.values()), {})}
    return {"cases": len(with_di), "fields": {f: _rates(r) for f, r in sorted(rows.items())},
            "overall": _rates(total) if total else {}}  # fmt: skip


def _rates(row: dict[str, int]) -> dict[str, Any]:
    def share(a: int, b: int) -> float | None:
        return round(a / b, 4) if b else None

    n = row["printed"]
    return {**row, "local_accuracy": share(row["local"], n), "azure_accuracy": share(row["azure"], n),
            "both_accuracy": share(row["both"], n), "agreement": share(row["agree"], row["agree"] + row["differ"]),
            "right_when_agree": share(row["agree_right"], row["agree"]),
            "right_when_differ": share(row["differ_right"], row["differ"]),
            "verified_precision": share(row["verified_right"], row["verified"]),
            "verified_coverage": share(row["verified"], n)}  # fmt: skip


def render(report: dict[str, Any], warnings: list[str]) -> str:
    def pct(v: Any) -> str:
        return "-" if v is None else f"{100 * v:.1f}%"

    lines = ["# AP Coder and Azure Document Intelligence, field by field", "",
             f"{report['cases']} invoices read twice: by AP Coder alone, and with Azure's recorded answer among the "
             "readers (local first: a disagreement goes to a person; agreeing raises the confidence; a value only "
             "Azure read is at most likely).", "",
             "| Field | Printed | AP Coder | Azure | Together | Agree | Right when agree | Right when differ "
             "| Verified precision | Verified coverage |", "|---|---|---|---|---|---|---|---|---|---|"]  # fmt: skip
    for name, r in [*report["fields"].items(), ("all fields", report["overall"])]:
        if not r:
            continue
        lines.append(f"| {name} | {r['printed']} | {pct(r['local_accuracy'])} | {pct(r['azure_accuracy'])} | "
                     f"{pct(r['both_accuracy'])} | {pct(r['agreement'])} | {pct(r['right_when_agree'])} | "
                     f"{pct(r['right_when_differ'])} | {pct(r['verified_precision'])} | "
                     f"{pct(r['verified_coverage'])} |")  # fmt: skip
    if warnings:
        lines += ["", "## Answers not used", "", *[f"- {w}" for w in warnings]]
    return "\n".join(lines) + "\n"


def write(out: Path, cases: list[Case], alone: list[dict[str, Any]], with_di: dict[str, dict[str, Any]],
          warnings: list[str]) -> dict[str, Any]:  # fmt: skip
    """readers.json and readers.md in ``out``; the report."""
    report = {**compare(cases, alone, with_di), "warnings": warnings}
    (out / "readers.json").write_text(json.dumps(report, indent=1) + "\n", encoding="utf-8")
    (out / "readers.md").write_text(render(report, warnings), encoding="utf-8")
    overall = report["overall"]
    if overall:
        print(f"with Azure: {report['cases']} invoices, AP Coder alone {overall['local_accuracy']}, Azure alone "
              f"{overall['azure_accuracy']}, together {overall['both_accuracy']}: {out / 'readers.md'}",
              file=sys.stderr)  # fmt: skip
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    return report


# --- Recording the answers (developers only, made-up invoices only) ----------------------------------------------


def record(cases: list[Case], out: str | Path, client: Any) -> list[Path]:
    """Azure's answer for each case not recorded yet, gzipped, in ``out``."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for case in cases:
        path = out / f"{response_key(case)}.json.gz"
        if path.is_file():
            continue
        raw = client.analyze(case.path.read_bytes())
        path.write_bytes(gzip.compress(json.dumps(raw, ensure_ascii=False).encode("utf-8")))
        written.append(path)
    return written


def record_command(a: argparse.Namespace) -> int:
    from ap_coder.ai.di_http import DiClient
    from ap_coder.bench.generator import load_cases
    from ap_coder.ops.offline import internet_allowed

    if not internet_allowed():
        print("record-di asks Azure: a developer's command, only with AP_ALLOW_INTERNET=1.", file=sys.stderr)
        return 2
    endpoint, key = os.environ.get("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", ""), os.environ.get(
        "AZURE_DOCUMENT_INTELLIGENCE_KEY", "")  # fmt: skip
    if not endpoint or not key:
        print("Set AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT and AZURE_DOCUMENT_INTELLIGENCE_KEY.", file=sys.stderr)
        return 2
    cases = load_cases(Path(a.cases))  # the benchmark's made-up invoices only
    written = record(cases, a.out, DiClient(endpoint, key))
    print(f"{len(written)} answers recorded in {a.out} ({len(cases) - len(written)} there already)")
    return 0


def add_parser(sub: Any) -> None:
    """``record-di``, and ``run``'s ``--di-responses``, under ``python -m ap_coder.bench``."""
    sub.choices["run"].add_argument("--di-responses", default=None, help="a folder of Azure Document Intelligence's "
                                    "recorded answers: each case read with and without it (readers.md)")  # fmt: skip
    p = sub.add_parser("record-di", help="record Azure Document Intelligence's answers for the benchmark's made-up "
                       "invoices (developers, AP_ALLOW_INTERNET=1)")  # fmt: skip
    p.add_argument("--cases", required=True, help="the cases folder of an earlier run (bench_out/cases)")
    p.add_argument("--out", required=True, help="where the answers go (one .json.gz a case)")
    p.set_defaults(run=record_command)
