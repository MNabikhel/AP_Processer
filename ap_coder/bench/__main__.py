"""python -m ap_coder.bench run --n 300 --seed 1 --scanned 0.3 [--out bench_out] [--workers 4]
python -m ap_coder.bench calibrate RUN_DIR [RUN_DIR ...]
python -m ap_coder.bench learn --suppliers 30 --invoices 40 [--scanned 0.0]
python -m ap_coder.bench speed --n 20 --scanned 0.5 [--history 30000]
python -m ap_coder.bench coding --vendors 30 --invoices 30 --seed 1 [--out bench_coding] [--realistic]"""

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
    sp = sub.add_parser("speed", help="seconds per invoice through the app's pipeline on this computer")
    sp.add_argument("--n", type=int, default=20, help="number of invoices (default 20)")
    sp.add_argument("--seed", type=int, default=1, help="random seed; the same seed gives the same invoices")
    sp.add_argument("--scanned", type=float, default=0.5, help="share of invoices made as scans (default 0.5)")
    sp.add_argument("--history", type=int, default=0, help="approved lines taught to the learning memory first")
    sp.add_argument("--out", default="bench_speed", help="output folder (default bench_speed)")
    cp = sub.add_parser("coding", help="GL coding with no AI model: lines coded from AP's approvals as they come in")
    cp.add_argument("--vendors", type=int, default=30, help="number of vendors (default 30)")
    cp.add_argument("--invoices", type=int, default=30, help="invoices per vendor (default 30)")
    cp.add_argument("--seed", type=int, default=1, help="random seed; the same seed gives the same company")
    cp.add_argument("--out", default="bench_coding", help="output folder (default bench_coding)")
    cp.add_argument(
        "--realistic",
        action="store_true",
        help="all of the four below: 2%% mistakes, history for 40%% of vendors, guesses, a split account",
    )
    cp.add_argument("--mistakes", type=float, default=None, help="share of lines AP approves with a wrong account")
    cp.add_argument("--history", type=float, default=None, help="share of vendors with ERP history imported first")
    cp.add_argument("--guesses", action="store_true", help="kept chat-model guesses on the lines left blank")
    cp.add_argument("--split", action="store_true", help="one item coded to two accounts by its quantity")
    tp = sub.add_parser("triage", help="email triage: made-up emails of every kind sorted as the dashboard does")
    tp.add_argument("--n", type=int, default=300, help="number of emails (default 300)")
    tp.add_argument("--seed", type=int, default=1, help="random seed; the same seed gives the same emails")
    tp.add_argument("--msg-share", type=float, default=0.4, help="share saved as Outlook .msg (default 0.4)")
    tp.add_argument("--out", default="bench_triage", help="output folder (default bench_triage)")
    a = p.parse_args(argv)

    if a.cmd == "triage":
        from ap_coder.bench.emails import run_triage

        o = run_triage(a.n, a.seed, a.out, a.msg_share)["summary"]["overall"]
        print(f"{o['emails']} emails: invoices lost {o['invoices_lost']}, kind right {100 * o['kind_right']:.1f}%, "
              f"routes right {100 * (o['routes_right'] or 0):.1f}%, bank changes flagged {o['fraud_flagged']}, "
              f"non-invoices into the queue {o['into_queue_wrongly']}")  # fmt: skip
        print(f"report: {Path(a.out) / 'report.md'}")
        return 0

    if a.cmd == "speed":
        from ap_coder.bench.speed import run_speed

        report = run_speed(a.n, a.seed, a.scanned, a.out, a.history)
        for kind, steps in report["seconds"].items():
            if steps["arrival"]["n"]:
                print(f"{kind}: {steps['arrival']['median']:.2f} s per invoice on arrival (median), "
                      f"{steps['page_reader']['median'] or 0:.2f} s in the page reader's pass")  # fmt: skip
        print(f"report: {Path(a.out) / 'speed.md'}")
        return 0

    if a.cmd == "coding":
        import dataclasses

        from ap_coder.bench.coding import REALISTIC, Realism, run_coding

        realism = REALISTIC if a.realistic else Realism()
        changes = {"mistakes": a.mistakes, "history": a.history, "guesses": a.guesses or None,
                   "split": a.split or None}  # fmt: skip
        realism = dataclasses.replace(realism, **{k: v for k, v in changes.items() if v is not None})
        if realism.history and not realism.history_noise and not a.realistic:
            realism = dataclasses.replace(realism, history_noise=REALISTIC.history_noise)
        report = run_coding(a.vendors, a.invoices, a.seed, a.out, realism)
        s = report["summary"]
        o, first = s["overall"], s["first_or_later"]

        def pc(v: float | None) -> str:
            return "-" if v is None else f"{100 * v:.1f}%"

        print(
            f"{a.vendors} vendors x {a.invoices} invoices, {o['lines']} lines: accuracy {pc(o['accuracy'])}, "
            f"precision {pc(o['precision'])} ({o['wrong']} wrong), coverage {pc(o['coverage'])}; first invoice "
            f"from a vendor {pc(first.get('first invoice', {}).get('accuracy'))}, later "
            f"{pc(first.get('later invoices', {}).get('accuracy'))}"
        )
        tc, ls = s.get("tax_codes") or {}, s.get("lessons") or {}
        print(f"tax codes: precision {pc(tc.get('precision'))} ({tc.get('wrong', 0)} wrong), coverage "
              f"{pc(tc.get('coverage'))}, {tc.get('learned', 0)} learned; line lessons shown on {ls.get('shown', 0)} "
              f"lines, {ls.get('wrong', 0)} for a wrong account")  # fmt: skip
        print(f"report: {Path(a.out) / 'report.md'}")
        return 0

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
