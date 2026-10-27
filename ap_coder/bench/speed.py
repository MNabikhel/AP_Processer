"""How long AP Coder takes per invoice on this computer, through the app's own pipeline.

``python -m ap_coder.bench speed --n 20 --scanned 0.5`` makes random invoices (digital PDFs and scans, as ``run`` does)
and reads each one the way the dashboard does, with no AI model: on arrival (read, coded, checked, saved to the review
queue), then again as the page reader's pass does once OvisOCR2 has read its pages (the page reader's own time is not
included: it runs in LM Studio, in the background). A fresh data folder and database each run, in ``OUT/data``, so a
second run times the same work again. ``--history N`` first teaches the learning memory N approved lines, as an import
of the ERP's history would: coding must stay quick as AP Coder learns.

The report (``OUT/speed.json`` and ``speed.md``) gives each kind of invoice's seconds per invoice: median, mean, the
slowest tenth, for the arrival and for the page reader's pass, with the computer's cores and Python.
"""

from __future__ import annotations

import json
import os
import platform
import random
import shutil
import statistics
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from .coding import DATA, sample_reference
from .harness import default_workers, prepare_cases


def _teach_history(store: Any, n: int, seed: int) -> None:
    """``n`` approved lines in the learning memory, as an import of the ERP's history makes them."""
    import csv

    from ap_coder.bench.content import GOODS, UTILITY_LINES

    with (DATA / "chart_of_accounts.csv").open(encoding="utf-8") as f:
        gls = [row["gl_code"] for row in csv.DictReader(f)][:30]
    rng = random.Random(seed)
    names = [g[0] for g in GOODS] + [u[0] for u in UTILITY_LINES]
    rows = [
        {"vendor_name": f"Supplier {rng.randint(1, 400)}", "description": f"{rng.choice(names)} {rng.randint(1, 999)}",
         "gl_code": rng.choice(gls), "cost_center": "", "amount": round(rng.uniform(5, 900), 2),
         "date": f"2025-{rng.randint(1, 12):02d}-{rng.randint(1, 28):02d}"}
        for _ in range(n)
    ]  # fmt: skip
    store.import_history(rows)


def _stats(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"n": 0, "median": None, "mean": None, "p90": None}
    ordered = sorted(values)
    return {
        "n": len(values),
        "median": round(statistics.median(ordered), 3),
        "mean": round(statistics.fmean(ordered), 3),
        "p90": round(ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))], 3),
    }


def run_speed(n: int, seed: int, scanned: float, out: str | Path, history: int = 0) -> dict[str, Any]:
    out = Path(out)
    cases = prepare_cases(out / "cases", n, seed, scanned, default_workers(), False)
    data = out / "data"
    shutil.rmtree(data, ignore_errors=True)
    (data / "invoices").mkdir(parents=True)
    old = os.environ.get("AP_PRIVATE_DIR")
    os.environ["AP_PRIVATE_DIR"] = str(data)  # the OCR cache, the page reader's cache: this run's own
    try:
        from ap_coder.ai.pipeline import InvoicePipeline
        from ap_coder.core.config import Settings
        from ap_coder.core.store import Store

        store = Store(data / "ap_coder.db")
        if history:
            _teach_history(store, history, seed)
        settings = Settings()
        pipeline = InvoicePipeline(replace(settings, llm=replace(settings.llm, provider="off")), sample_reference(),
                                   store=store)  # fmt: skip
        times: dict[str, dict[str, list[float]]] = {"digital": {"arrival": [], "page_reader": []},
                                                    "scanned": {"arrival": [], "page_reader": []}}  # fmt: skip
        errors = []
        for i, case in enumerate(cases, 1):
            kind = "scanned" if case.scanned else "digital"
            path = data / "invoices" / case.path.name
            shutil.copy(case.path, path)
            start = time.perf_counter()
            first = pipeline.process(path)
            times[kind]["arrival"].append(time.perf_counter() - start)
            if first.error:
                errors.append(f"{case.id}: {first.error}")
                continue
            pages = first.capture.page_count if first.capture is not None else 1
            start = time.perf_counter()
            again = pipeline.process(first.source, page_text=[""] * max(1, pages), invoice_id=first.invoice_id)
            times[kind]["page_reader"].append(time.perf_counter() - start)
            if again.error:
                errors.append(f"{case.id} (page reader's pass): {again.error}")
            if i % 10 == 0 or i == len(cases):
                print(f"  speed: {i}/{len(cases)}", file=sys.stderr, flush=True)
    finally:
        if old is None:
            os.environ.pop("AP_PRIVATE_DIR", None)
        else:
            os.environ["AP_PRIVATE_DIR"] = old
    report = {
        "config": {"n": n, "seed": seed, "scanned": scanned, "history": history},
        "computer": {
            "cores": os.cpu_count(),
            "python": platform.python_version(),
            "system": platform.system(),
            "machine": platform.machine(),
        },  # fmt: skip
        "seconds": {kind: {step: _stats(v) for step, v in steps.items()} for kind, steps in times.items()},
        "errors": errors,
    }
    (out / "speed.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    (out / "speed.md").write_text(_markdown(report), encoding="utf-8")
    return report


def _markdown(report: dict[str, Any]) -> str:
    c, pc = report["config"], report["computer"]
    lines = [
        "# Speed benchmark",
        "",
        f"{c['n']} invoices (seed {c['seed']}, {round(100 * c['scanned'])}% scans), "
        f"{c['history']} lines in the learning memory; {pc['cores']} cores, Python {pc['python']}, {pc['system']}.",
        "",
        "Seconds per invoice through the app's pipeline with no AI model: on arrival, and again in the page "
        "reader's pass (OvisOCR2's own reading not included).",
        "",
        "| invoices | step | n | median | mean | slowest 10% |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for kind, steps in report["seconds"].items():
        for step, s in steps.items():
            if s["n"]:
                lines.append(f"| {kind} | {step.replace('_', ' ')} | {s['n']} | {s['median']:.2f} | {s['mean']:.2f} | "
                             f"{s['p90']:.2f} |")  # fmt: skip
    if report["errors"]:
        lines += ["", "Errors:", *[f"- {e}" for e in report["errors"]]]
    return "\n".join(lines) + "\n"
