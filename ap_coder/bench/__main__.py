"""python -m ap_coder.bench run --n 300 --seed 1 --scanned 0.3 [--out bench_out] [--workers 4]
python -m ap_coder.bench calibrate RUN_DIR [RUN_DIR ...]
python -m ap_coder.bench learn --suppliers 30 --invoices 40 [--scanned 0.0]"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ap_coder.bench.harness import READERS, default_workers, prepare_cases, run


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m ap_coder.bench", description="Invoice capture accuracy benchmark")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--n", type=int, default=300, help="number of invoices (default 300)")
        sp.add_argument("--seed", type=int, default=1, help="random seed; the same seed gives the same invoices")
        sp.add_argument("--scanned", type=float, default=0.3, help="share of invoices made as scans (default 0.3)")
        sp.add_argument("--out", default="bench_out", help="output folder (default bench_out)")
        sp.add_argument("--workers", type=int, default=default_workers(), help="processes (OCR is ~2 s a page)")
        sp.add_argument("--regen", action="store_true", help="regenerate the invoices even if they match")

    r = sub.add_parser("run", help="generate invoices, read them, write report.json/report.md/failures.jsonl")
    common(r)
    r.add_argument(
        "--reader",
        choices=READERS,
        default="auto",
        help="capture: ap_coder.capture.analyze; stub: the harness's naive reader; auto: capture if available",
    )
    g = sub.add_parser("generate", help="only write the invoices and their truth")
    common(g)
    c = sub.add_parser("calibrate", help="fit capture's confidence table from finished runs (their evidence.jsonl)")
    c.add_argument("runs", nargs="+", help="output folders of earlier `run` commands")
    c.add_argument("--dest", default=None, help="where to write the table (default: ap_coder/capture/calibration.json)")
    lp = sub.add_parser("learn", help="supplier learning: each supplier's invoices read in order as AP approves them")
    lp.add_argument("--suppliers", type=int, default=20, help="number of suppliers (default 20)")
    lp.add_argument("--invoices", type=int, default=40, help="invoices per supplier (default 40)")
    lp.add_argument("--seed", type=int, default=1, help="random seed; the same seed gives the same suppliers")
    lp.add_argument("--scanned", type=float, default=0.0, help="share of invoices that arrive as scans (default 0)")
    lp.add_argument("--out", default="bench_learn", help="output folder (default bench_learn)")
    lp.add_argument("--workers", type=int, default=default_workers(), help="processes (one supplier each)")
    lp.add_argument(
        "--no-vendor-master", action="store_true", help="suppliers are not in the vendor master (no name/GST check)"
    )
    lp.add_argument(
        "--ignore-check",
        action="append",
        default=[],
        metavar="CODE",
        help="what-if: a failed check with this code (e.g. LINES_ADD_UP) does not hold an invoice back",
    )
    a = p.parse_args(argv)

    if a.cmd == "calibrate":
        from ap_coder.bench.harness import fit_calibration

        table = fit_calibration(a.runs, a.dest)
        ev = table["evidence"]
        print(f"{len(ev)} evidence patterns from {sum(v[0] for v in ev.values())} values")
        return 0

    if a.cmd == "learn":
        from ap_coder.bench.learning import run_learning

        report = run_learning(
            a.suppliers,
            a.invoices,
            a.seed,
            a.scanned,
            a.out,
            a.workers,
            vendor_master=not a.no_vendor_master,
            ignore_checks=tuple(a.ignore_check),
        )
        au, b = report["summary"]["autonomy"], report["summary"]["buckets"]
        first, last = next(iter(b.values())), list(b.values())[-1]
        print(
            f"{report['summary']['suppliers']} suppliers x {a.invoices} invoices: field accuracy "
            f"{100 * (first['accuracy'] or 0):.1f}% on the first invoice, {100 * (last['accuracy'] or 0):.1f}% "
            f"from #{list(b)[-1]}; {au['suppliers_ready']} suppliers reached autonomy "
            f"(median before #{au['ready_at_median']}); "
            f"after that {au['touchless']}/{au['invoices_after_ready']} touchless with {au['touchless_errors']} errors"
        )
        print(f"report: {Path(a.out) / 'report.md'}")
        return 0
    if a.cmd == "generate":
        cases = prepare_cases(Path(a.out) / "cases", a.n, a.seed, a.scanned, a.workers, a.regen)
        print(f"{len(cases)} invoices in {Path(a.out) / 'cases'}")
        return 0
    report = run(a.n, a.seed, a.scanned, a.out, a.workers, a.reader, a.regen)
    s = report["summary"]
    o, inv = s["overall"], s["invoices"]

    def pct(v: float | None) -> str:
        return "-" if v is None else f"{100 * v:.1f}%"

    print(
        f"reader {report['config']['reader']}: {report['cases']} invoices, field accuracy {pct(o['accuracy'])}, "
        f"verified precision {pct(o['verified_precision'])} (coverage {pct(o['verified_coverage'])}), "
        f"fully correct invoices {pct(inv['fully_correct'])}, no silent error {pct(inv.get('no_silent_error'))}, "
        f"ECE {s['calibration']['ece']}"
    )
    print(f"report: {Path(a.out) / 'report.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
