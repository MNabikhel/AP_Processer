"""Run a reader over generated invoices and report its accuracy.

`run(...)` generates (or reuses) the cases, reads each one with `ap_coder.capture.analyze` (or, until
that exists, a trivial stub reader so the harness itself can be tested), scores every field and
writes `report.json`, `report.md` and `failures.jsonl` into the output folder.
"""

from __future__ import annotations

import json
import multiprocessing
import os
import re
import shutil
import sys
import time
from collections import defaultdict
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from ap_coder.bench.generator import GENERATOR_VERSION, Case, load_cases, make_case, plan
from ap_coder.bench.scoring import line_item_metrics, score_case, summarize
from ap_coder.capture.types import FIELDS, LIKELY, MISSING, Box, CaptureResult, FieldResult

READERS = ("auto", "capture", "stub")


# --- readers ----------------------------------------------------------------------------------


def capture_available() -> bool:
    try:
        from ap_coder.capture import analyze  # noqa: F401
    except ImportError:
        return False
    return True


def resolve_reader(name: str) -> str:
    if name not in READERS:
        raise ValueError(f"unknown reader {name!r}; choose from {', '.join(READERS)}")
    if name == "stub":
        return "stub"
    if capture_available():
        return "capture"
    if name == "capture":
        raise ImportError("ap_coder.capture.analyze is not available yet")
    return "stub"


_AMOUNT = re.compile(r"^\(?-?\$?\d{1,3}(?:,\d{3})*\.\d{2}\)?$")
_NUMBER_LABEL = re.compile(r"(invoice|inv\.?|facture|bill)\s*(no\.?|#|number|n°)?:?$", re.I)


def stub_analyze(path: str | Path) -> CaptureResult:
    """A deliberately naive reader: PDF text layer only; the word after an 'Invoice No' label, and
    the largest amount on the last page as the total. It exists to exercise the harness."""
    import pymupdf

    fields: dict[str, FieldResult] = {}
    path = Path(path)
    if path.suffix.lower() != ".pdf":
        return CaptureResult(fields={}, layout_source="none", page_count=1)
    doc = pymupdf.open(path)
    best: tuple[float, Box, str] | None = None
    for pno, page in enumerate(doc, 1):
        w, h = page.rect.width, page.rect.height
        words = page.get_text("words")
        for i, wd in enumerate(words):
            text = wd[4]
            box = Box(pno, wd[0] / w, wd[1] / h, wd[2] / w, wd[3] / h)
            if "invoice_number" not in fields and i + 1 < len(words):
                prev = " ".join(x[4] for x in words[max(0, i - 2) : i + 1])
                nxt = words[i + 1]
                if _NUMBER_LABEL.search(prev) and abs((nxt[1] + nxt[3]) - (wd[1] + wd[3])) < 4 and nxt[0] > wd[2]:
                    nbox = Box(pno, nxt[0] / w, nxt[1] / h, nxt[2] / w, nxt[3] / h)
                    fields["invoice_number"] = FieldResult(
                        "invoice_number",
                        nxt[4].strip("#:"),
                        0.4,
                        LIKELY,
                        [nbox],
                        {"stub": nxt[4]},
                        ["stub: word after the label"],
                    )
            if pno == doc.page_count and _AMOUNT.match(text):
                neg = text.startswith(("-", "("))
                val = float(text.strip("()-$").replace(",", "")) * (-1 if neg else 1)
                if best is None or abs(val) > abs(best[0]):
                    best = (val, box, text)
    if best:
        fields["grand_total"] = FieldResult(
            "grand_total", best[0], 0.5, LIKELY, [best[1]], {"stub": best[2]}, ["stub: largest amount"]
        )
    for f in FIELDS:
        fields.setdefault(f, FieldResult(f, None, 0.0, MISSING))
    return CaptureResult(fields=fields, layout_source="text", page_count=doc.page_count)


def read_one(job: tuple[str, str]) -> dict[str, Any]:
    """Read one file; never raises (an error is recorded against the case)."""
    path, reader = job
    t0 = time.perf_counter()
    try:
        if reader == "capture":
            from ap_coder.capture import analyze

            result = analyze(path)
        else:
            result = stub_analyze(path)
        return {"result": result.to_dict(), "error": None, "seconds": time.perf_counter() - t0}
    except Exception as e:  # noqa: BLE001 - a reader crash is a benchmark result, not a harness failure
        return {"result": None, "error": f"{type(e).__name__}: {e}", "seconds": time.perf_counter() - t0}


def _make_case_job(job: tuple[str, int, int, str, bool]) -> str:
    out, seed, index, arch, scanned = job
    return str(make_case(out, seed, index, arch, scanned).truth_path)


def _map(fn: Callable[[Any], Any], jobs: list[Any], workers: int, label: str) -> list[Any]:
    """Ordered map, in a process pool when workers > 1; prints progress to stderr."""
    out: list[Any] = []
    step = max(1, len(jobs) // 10)

    def tick(i: int) -> None:
        if (i + 1) % step == 0 or i + 1 == len(jobs):
            print(f"  {label}: {i + 1}/{len(jobs)}", file=sys.stderr, flush=True)

    if workers <= 1 or len(jobs) <= 1:
        for i, j in enumerate(jobs):
            out.append(fn(j))
            tick(i)
        return out
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(workers) as pool:
        it: Iterable[Any] = pool.imap(fn, jobs, chunksize=1)
        for i, r in enumerate(it):
            out.append(r)
            tick(i)
    return out


# --- cases ------------------------------------------------------------------------------------


def prepare_cases(cases_dir: Path, n: int, seed: int, scanned: float, workers: int, regen: bool) -> list[Case]:
    manifest = {"n": n, "seed": seed, "scanned": scanned, "generator_version": GENERATOR_VERSION}
    mpath = cases_dir / "manifest.json"
    if not regen and mpath.is_file():
        try:
            if json.loads(mpath.read_text(encoding="utf-8")) == manifest:
                cases = load_cases(cases_dir)
                if len(cases) == n and all(c.path.is_file() for c in cases):
                    return cases
        except (OSError, ValueError, KeyError):
            pass
    if cases_dir.exists():
        shutil.rmtree(cases_dir)
    cases_dir.mkdir(parents=True)
    jobs = [(str(cases_dir), seed, i, arch, sc) for i, arch, sc in plan(n, seed, scanned)]
    _map(_make_case_job, jobs, workers, "generate")
    mpath.write_text(json.dumps(manifest) + "\n", encoding="utf-8")
    return load_cases(cases_dir)


# --- the run ----------------------------------------------------------------------------------


def evaluate(cases: list[Case], outputs: list[dict[str, Any]]) -> dict[str, Any]:
    """Score every case; returns the report (without timing) plus failure rows."""
    per_case: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    failures: list[dict[str, Any]] = []
    evidence_rows: list[dict[str, Any]] = []
    errors = []
    li_want = li_match = li_got = 0
    for case, out in zip(cases, outputs, strict=True):
        result = CaptureResult.from_dict(out["result"]) if out.get("result") else None
        if out.get("error"):
            errors.append({"case": case.id, "error": out["error"]})
        recs = score_case(case.truth, result)
        per_case.append((case.truth, recs))
        evidence_rows.extend(
            {
                "case": case.id,
                "scanned": case.scanned,
                "field": r["field"],
                "evidence": r["evidence"],
                "correct": r["correct"],
            }
            for r in recs
            if r["scored"] and r["reported"] and r["evidence"]
        )
        w, m, g = line_item_metrics(case.truth, result)
        li_want, li_match, li_got = li_want + w, li_match + m, li_got + g
        for r in recs:
            kind = None
            if r["present"] and not r["correct"]:
                kind = "missing" if not r["reported"] else "wrong"
            elif r["spurious"]:
                kind = "spurious"
            elif r["present"] and r["correct"] and not r["box_hit"]:
                kind = "box"
            if kind:
                entry = case.truth["fields"].get(r["field"]) or {}
                failures.append(
                    {
                        "case": case.id,
                        "file": case.truth.get("file"),
                        "layout": case.layout,
                        "scanned": case.scanned,
                        "field": r["field"],
                        "kind": kind,
                        "truth": r["truth"],
                        "raw": entry.get("raw"),
                        "label": entry.get("label"),
                        "got": r["got"],
                        "status": r["status"],
                        "confidence": round(r["confidence"], 4),
                        "reasons": r["reasons"],
                    }
                )
    digital = [pc for pc in per_case if not pc[0].get("scanned")]
    scanned = [pc for pc in per_case if pc[0].get("scanned")]
    by_arch: dict[str, list] = defaultdict(list)
    for pc in per_case:
        by_arch[pc[0].get("layout", "")].append(pc)
    archetypes = {}
    for arch in sorted(by_arch):
        s = summarize(by_arch[arch])
        archetypes[arch] = {
            "cases": len(by_arch[arch]),
            **{k: s["overall"][k] for k in ("n", "accuracy", "verified_precision", "verified_coverage", "spurious")},
            "fully_correct": s["invoices"]["fully_correct"],
        }
    report = {
        "summary": summarize(per_case),
        "digital": summarize(digital) if digital else None,
        "scanned": summarize(scanned) if scanned else None,
        "archetypes": archetypes,
        "line_items": {
            "truth": li_want,
            "matched": li_match,
            "returned": li_got,
            "recall": round(li_match / li_want, 4) if li_want else None,
            "precision": round(li_match / li_got, 4) if li_got else None,
        },
        "errors": errors,
        "cases": len(cases),
        "scanned_cases": len(scanned),
    }
    return {"report": report, "failures": failures, "evidence": evidence_rows}


def run(
    n: int = 300,
    seed: int = 1,
    scanned: float = 0.3,
    out: str | Path = "bench_out",
    workers: int = 1,
    reader: str = "auto",
    regen: bool = False,
) -> dict[str, Any]:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    reader_name = resolve_reader(reader)
    t0 = time.perf_counter()
    cases = prepare_cases(out / "cases", n, seed, scanned, workers, regen)
    t_gen = time.perf_counter() - t0
    outputs = _map(read_one, [(str(c.path), reader_name) for c in cases], workers, f"read ({reader_name})")
    t_read = time.perf_counter() - t0 - t_gen
    scored = evaluate(cases, outputs)
    report = {
        "config": {
            "n": n,
            "seed": seed,
            "scanned": scanned,
            "reader": reader_name,
            "workers": workers,
            "generator_version": GENERATOR_VERSION,
        },
        **scored["report"],
        "timing": {
            "generate_s": round(t_gen, 2),
            "read_s": round(t_read, 2),
            "read_per_case_s": round(sum(o["seconds"] for o in outputs) / max(1, len(outputs)), 3),
        },
    }
    (out / "report.json").write_text(json.dumps(report, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    (out / "report.md").write_text(render_markdown(report), encoding="utf-8")
    with (out / "failures.jsonl").open("w", encoding="utf-8") as fh:
        for row in scored["failures"]:
            fh.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    with (out / "evidence.jsonl").open("w", encoding="utf-8") as fh:
        for row in scored["evidence"]:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    return report


def fit_calibration(runs: list[str | Path], dest: str | Path | None = None) -> dict[str, Any]:
    """Count, per evidence pattern, how often the value was right across benchmark runs, and write
    the table capture uses for its confidence (``ap_coder/capture/calibration.json``)."""
    from ap_coder.capture.confidence import CALIBRATION_FILE

    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    configs = []
    for run_dir in runs:
        run_dir = Path(run_dir)
        for line in (run_dir / "evidence.jsonl").read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            c = counts[row["evidence"]]
            c[0] += 1
            c[1] += bool(row["correct"])
        try:
            configs.append(json.loads((run_dir / "report.json").read_text(encoding="utf-8"))["config"])
        except (OSError, ValueError, KeyError):
            pass
    table = {
        "about": "Per evidence pattern: [values reported, values right] on the benchmark runs below. "
        "Written by `python -m ap_coder.bench calibrate`; read by ap_coder.capture.confidence.",
        "runs": configs,
        "evidence": {k: counts[k] for k in sorted(counts)},
    }
    path = Path(dest) if dest else CALIBRATION_FILE
    path.write_text(json.dumps(table, indent=1) + "\n", encoding="utf-8")
    return table


# --- markdown ---------------------------------------------------------------------------------


def _pct(v: float | None) -> str:
    return "-" if v is None else f"{100 * v:.1f}%"


def _table(head: list[str], rows: list[list[Any]]) -> str:
    lines = ["| " + " | ".join(head) + " |", "|" + "|".join("---:" if i else "---" for i in range(len(head))) + "|"]
    lines += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(lines) + "\n"


def _field_rows(summary: dict[str, Any]) -> list[list[Any]]:
    rows = []
    for f, m in list(summary["fields"].items()) + [("**overall**", summary["overall"])]:
        st = m["statuses"]
        rows.append(
            [
                f,
                m["n"],
                _pct(m["accuracy"]),
                _pct(m["box_hit"]),
                _pct(m["verified_precision"]),
                _pct(m["verified_coverage"]),
                _pct(m["vl_precision"]),
                _pct(m["vl_coverage"]),
                m["spurious"],
                f"{st['verified']}/{st['likely']}/{st['check']}/{st['missing']}",
            ]
        )
    return rows


FIELD_HEAD = [
    "field",
    "n",
    "accuracy",
    "box hit",
    "verified prec.",
    "verified cov.",
    "v+l prec.",
    "v+l cov.",
    "spurious",
    "V/L/C/M",
]


def render_markdown(report: dict[str, Any]) -> str:
    cfg = report["config"]
    s = report["summary"]
    inv = s["invoices"]
    out = [
        "# Capture benchmark\n",
        f"Reader **{cfg['reader']}**, {report['cases']} invoices ({report['scanned_cases']} scanned), "
        f"seed {cfg['seed']}, "
        f"generator v{cfg['generator_version']}.\n",
        "Accuracy is the top value against the truth, over the fields printed on the invoice. *Verified precision* "
        "counts every verified answer, including values reported for fields the invoice does not print "
        "(spurious). Coverage is the share of printed fields given that status. Box hit: of the correct values, "
        "how many point at a place where the value is printed.\n",
        "## Invoices\n",
        _table(
            ["invoices", "fully correct", "touchless", "touchless with an error", "touchless precision"],
            [
                [
                    inv["n"],
                    _pct(inv["fully_correct"]),
                    _pct(inv["touchless"]),
                    inv["touchless_wrong"],
                    _pct(inv["touchless_precision"]),
                ]
            ],
        ),
        "## Fields\n",
        _table(FIELD_HEAD, _field_rows(s)),
        "## Calibration\n",
        f"Expected calibration error (ECE): **{s['calibration']['ece']}** over "
        f"{s['calibration']['n']} reported values.\n",
        _table(
            ["confidence", "n", "mean confidence", "accuracy"],
            [
                [
                    f"{b['lo']:.1f}-{b['hi']:.1f}",
                    b["n"],
                    "-" if b["conf"] is None else f"{b['conf']:.3f}",
                    _pct(b["acc"]),
                ]
                for b in s["calibration"]["bins"]
            ],
        ),
        "## Digital vs scanned\n",
    ]
    d, sc = report.get("digital"), report.get("scanned")
    rows = []
    for f in list(FIELDS) + ["overall"]:
        md = (d["overall"] if f == "overall" else d["fields"][f]) if d else None
        ms = (sc["overall"] if f == "overall" else sc["fields"][f]) if sc else None
        rows.append(
            [
                f,
                md["n"] if md else 0,
                _pct(md["accuracy"]) if md else "-",
                _pct(md["verified_precision"]) if md else "-",
                ms["n"] if ms else 0,
                _pct(ms["accuracy"]) if ms else "-",
                _pct(ms["verified_precision"]) if ms else "-",
            ]
        )
    out.append(
        _table(
            [
                "field",
                "digital n",
                "digital acc.",
                "digital verified prec.",
                "scanned n",
                "scanned acc.",
                "scanned verified prec.",
            ],
            rows,
        )
    )
    for name, part in (("Digital", d), ("Scanned", sc)):
        if part:
            pi = part["invoices"]
            out.append(
                f"{name}: {pi['n']} invoices, fully correct {_pct(pi['fully_correct'])}, touchless "
                f"{_pct(pi['touchless'])} ({pi['touchless_wrong']} with an error), ECE "
                f"{part['calibration']['ece']}.\n"
            )
    out.append("\n## By layout\n")
    out.append(
        _table(
            [
                "layout",
                "invoices",
                "fields",
                "accuracy",
                "verified prec.",
                "verified cov.",
                "spurious",
                "fully correct",
            ],
            [
                [
                    a,
                    m["cases"],
                    m["n"],
                    _pct(m["accuracy"]),
                    _pct(m["verified_precision"]),
                    _pct(m["verified_coverage"]),
                    m["spurious"],
                    _pct(m["fully_correct"]),
                ]
                for a, m in report["archetypes"].items()
            ],
        )
    )
    li = report["line_items"]
    out.append("\n## Line items\n")
    out.append(
        f"{li['matched']} of {li['truth']} printed lines found (recall {_pct(li['recall'])}); "
        f"{li['returned']} returned (precision {_pct(li['precision'])}).\n"
    )
    if report["errors"]:
        out.append(
            f"\n## Reader errors\n\n{len(report['errors'])} cases raised an error, e.g. "
            f"`{report['errors'][0]['case']}`: {report['errors'][0]['error']}\n"
        )
    t = report.get("timing")
    if t:
        out.append(
            f"\n## Timing\n\nGenerate {t['generate_s']} s, read {t['read_s']} s "
            f"({t['read_per_case_s']} s per invoice, {cfg['workers']} worker(s)).\n"
        )
    return "\n".join(out)


def default_workers() -> int:
    return max(1, min(8, (os.cpu_count() or 2) - 1))
