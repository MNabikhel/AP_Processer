"""Command-line entry point: ``python -m ap_coder <command>``."""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import logging
import os
import sys
import time
from pathlib import Path

from .config import Settings
from .doctor import exit_code, format_checks, run_checks
from .evaluation import evaluate
from .extraction import DocumentExtractor
from .labels import _prediction_files, export_labels
from .mailbox import unpack_folder
from .pipeline import (
    InvoicePipeline,
    PipelineResult,
    discover_inputs,
    invoice_files,
    output_stems,
    write_outputs,
)
from .reference_data import ReferenceData, load_reference_data, load_table, parse_policy_notes
from .schema import build_json_schema
from .share_report import build_share_report
from .store import Store, default_db_path, private_dir
from .tax import TaxSetup, load_tax_mapping

SAMPLE_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
# Everything enterprise-specific lives under ./private, which is git-ignored.

_REFERENCE_FILES = {
    "coa": ("chart_of_accounts", (".csv", ".json")),
    "cost_centers": ("cost_centers", (".csv", ".json")),
    "tax_mapping": ("tax_gl_mapping", (".csv",)),
    "policy": ("coding_policy", (".md", ".txt")),
}


def reference_dir() -> tuple[Path, bool]:
    """Return (directory, is_sample). AP_REFERENCE_DIR > <data folder>/reference > bundled sample data."""
    env_dir = os.getenv("AP_REFERENCE_DIR")
    if env_dir:
        return Path(env_dir), False
    private = private_dir() / "reference"
    if (private / "chart_of_accounts.csv").exists() or (private / "chart_of_accounts.json").exists():
        return private, False
    return SAMPLE_DATA_DIR, True


def _resolve_reference(args: argparse.Namespace) -> dict[str, Path | None]:
    base, _ = reference_dir()
    paths: dict[str, Path | None] = {}
    for key, (stem, exts) in _REFERENCE_FILES.items():
        explicit = getattr(args, key)
        if explicit is not None:
            paths[key] = Path(explicit) if explicit else None  # '' disables an optional file
            continue
        found = next((base / f"{stem}{ext}" for ext in exts if (base / f"{stem}{ext}").exists()), None)
        if found is None and key == "coa":
            found = base / f"{stem}.csv"  # let the loader report the missing file clearly
        paths[key] = found
    return paths


def reference_source(args: argparse.Namespace) -> str:
    """Where reference data will come from: explicit files > dashboard database > reference folder."""
    if getattr(args, "coa", None):
        return "files given on the command line"
    db = Path(getattr(args, "db", None) or default_db_path())
    if db.exists() and Store(db).has_reference():
        return "dashboard database"
    return (
        "BUNDLED SAMPLE DATA (import your GL accounts in the dashboard)" if reference_dir()[1] else "reference folder"
    )


def _load_reference(args: argparse.Namespace) -> ReferenceData:
    if reference_source(args) == "dashboard database":
        reference = Store(args.db).reference_data()
        # Explicit optional files still apply on top of the database ('' removes them).
        overrides: dict[str, object] = {}
        if args.cost_centers is not None:
            overrides["cost_centers"] = (
                load_table(args.cost_centers, "cost center", "cost_center") if args.cost_centers else None
            )
        if args.tax_mapping is not None:
            mapping = load_tax_mapping(args.tax_mapping) if args.tax_mapping else {}
            overrides["tax"] = TaxSetup(reference.tax.rates, mapping)
        if args.policy is not None:
            text = Path(args.policy).read_text(encoding="utf-8") if args.policy else ""
            overrides["notes"] = parse_policy_notes(text)
        reference = dataclasses.replace(reference, **overrides)
        return reference
    paths = _resolve_reference(args)
    return load_reference_data(
        paths["coa"], paths["cost_centers"], tax_mapping=paths["tax_mapping"], policy_notes=paths["policy"]
    )


def _add_reference_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group(
        "reference data",
        "Defaults to the dashboard database (if GL accounts were imported), else AP_REFERENCE_DIR, "
        "else <data folder>/reference, else the bundled sample data in ./data",
    )
    g.add_argument("--coa", help="GL accounts (.csv/.json); overrides the dashboard database")
    g.add_argument("--cost-centers", help="Cost center list (.csv/.json); '' to omit")
    g.add_argument("--tax-mapping", help="Tax GL mapping (tax_type,treatment,gl_code .csv); '' to omit")
    g.add_argument("--policy", help="Coding policy notes (.md/.txt); '' to omit")


def build_parser() -> argparse.ArgumentParser:
    private = private_dir()
    db_path, out_dir, cache_dir = private / "ap_coder.db", private / "output", private / ".cache" / "extraction"
    db_path = Path(os.environ.get("AP_DB_PATH") or db_path)  # the database the dashboard uses, as it chooses it
    parser = argparse.ArgumentParser(prog="ap_coder", description="Enterprise AP Invoice Coder Engine (PoC)")
    parser.add_argument(
        "--env-file", default=None, help="Path to a .env file (default: the data folder's, else ./.env)"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--db", default=str(db_path), help=f"Dashboard database (default: {db_path})")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("dashboard", help="Open the review dashboard in your browser (runs locally)")
    p.add_argument("--port", type=int, default=None, help="Default: 8501, or the next free port")
    p.add_argument(
        "--address", default=None,
        help="Address to listen on. Default 127.0.0.1 (this computer only), or AP_DASHBOARD_ADDRESS; "
        "0.0.0.0 lets others on the network open it",
    )  # fmt: skip

    p = sub.add_parser("doctor", help="Check configuration, reference data and (with --online) Azure connectivity")
    p.add_argument("--online", action="store_true", help="Call both Azure services (costs about one invoice)")
    _add_reference_args(p)

    p = sub.add_parser("process", help="Extract + GL-code invoices (files or directories)")
    p.add_argument("inputs", nargs="+", help="PDF/TIFF/image invoices, or .md/.txt pre-extracted content")
    p.add_argument("-o", "--out", default=str(out_dir), help=f"Output directory (default: {out_dir})")
    p.add_argument(
        "--extraction-model", choices=["prebuilt-layout", "prebuilt-invoice"], help="Document Intelligence model"
    )
    p.add_argument("--deployment", help="Azure OpenAI deployment name")
    p.add_argument("--model-name", help="Underlying model name (gpt-4o, gpt-4o-mini, gpt-4.1, o4-mini, ...)")
    p.add_argument("--vision", action=argparse.BooleanOptionalAction, default=None, help="Attach page images")
    p.add_argument("--no-db", action="store_true", help="Do not use the learning memory or add to the review queue")
    p.add_argument("--cache-dir", default=str(cache_dir), help="Extraction cache directory ('' to disable)")
    p.add_argument("--workers", type=int, default=1, help="Invoices processed in parallel")
    p.add_argument("--force", action="store_true", help="Process files even if they were processed before")
    p.add_argument("--stdout", action="store_true", help="Also print each coded JSON to stdout")
    _add_reference_args(p)

    p = sub.add_parser("watch", help="Keep processing new files dropped in the invoices folder (Ctrl+C stops)")
    p.add_argument("folder", nargs="?", default=str(private / "invoices"), help="Default: the dashboard's folder")
    p.add_argument("--every", type=int, default=60, help="Seconds between checks (default 60)")
    p.add_argument("--once", action="store_true", help="Check once and stop (e.g. from Windows Task Scheduler)")
    p.add_argument("--cache-dir", default=str(cache_dir), help="Extraction cache directory ('' to disable)")
    _add_reference_args(p)

    p = sub.add_parser("read-pages", help="Read the invoices waiting for the page reader (OvisOCR2), then stop")
    p.add_argument("--minutes", type=float, default=60.0, help="Stop after this long (default 60; a page under way "
                   "is finished)")  # fmt: skip
    p.add_argument("--invoice", type=int, default=None, help="Read only this invoice (it is queued first)")
    p.add_argument("--test", action="store_true", help="Run the page reader's self-test again now, instead of "
                   "reading (10 to 20 minutes on a laptop; it runs on its own before the first invoice)")  # fmt: skip
    p.add_argument("--cache-dir", default=str(cache_dir), help="Extraction cache directory ('' to disable)")

    p = sub.add_parser("extract", help="Run Document Intelligence only and save Markdown + raw JSON")
    p.add_argument("inputs", nargs="+")
    p.add_argument("-o", "--out", default=str(out_dir))
    p.add_argument("--extraction-model", choices=["prebuilt-layout", "prebuilt-invoice"])
    p.add_argument("--cache-dir", default=str(cache_dir))

    p = sub.add_parser("labels", help="Create an Excel workbook for the AP team to correct (ground truth)")
    p.add_argument("--predictions", default=str(out_dir), help=f"Output folder of `process` ({out_dir})")
    p.add_argument("-o", "--out", default=str(private / "labels.xlsx"), help="Workbook path (.xlsx or .csv)")
    p.add_argument("--blind", action="store_true", help="Leave gl_code/cost_center empty to avoid anchoring bias")
    p.add_argument("--force", action="store_true", help="Overwrite an existing workbook (loses corrections in it)")
    _add_reference_args(p)

    p = sub.add_parser("evaluate", help="Score predictions against ground truth (JSON folder, .xlsx or .csv)")
    p.add_argument("--predictions", default=str(out_dir))
    p.add_argument("--ground-truth", default=str(private / "labels.xlsx"))
    p.add_argument("--target", type=float, default=0.9)
    p.add_argument("--show-mismatches", action="store_true", help="Prints invoice data; do not share the output")

    p = sub.add_parser("share-report", help="Redacted summary that is safe to paste into a chat")
    p.add_argument("--predictions", default=str(out_dir))
    p.add_argument("--ground-truth", help="Labels workbook/CSV or JSON folder to include accuracy figures")
    p.add_argument("--include-codes", action="store_true", help="Include GL/cost-center confusion pairs")
    p.add_argument("-o", "--out", default=str(private / "share_report.md"))

    p = sub.add_parser(
        "export-training", help="Approved invoices (pages + approved values) as a ZIP to fine-tune a vision model"
    )
    p.add_argument(
        "-o", "--out", default=None,
        help="ZIP to write (default: training/ap-coder-training-<today>.zip in the database's folder)",
    )  # fmt: skip
    p.add_argument("--since", default=None, help="Only invoices approved on or after this date (YYYY-MM-DD)")

    p = sub.add_parser("demo", help="Load the sample invoices into the review queue (no Azure needed)")
    p.add_argument("--remove", action="store_true", help="Remove the demo invoices and their lessons instead")

    p = sub.add_parser("schema", help="Print the strict JSON Schema sent to Azure OpenAI")
    p.add_argument("--no-constrain-codes", action="store_true", help="Do not embed valid codes as enums")
    _add_reference_args(p)
    return parser


def cmd_doctor(args: argparse.Namespace, settings: Settings) -> int:
    checks = run_checks(settings, lambda: _load_reference(args), online=args.online)
    report = format_checks(checks)
    if hasattr(sys.stdout, "reconfigure"):  # a Windows console (cp1252) can't print every character a note may hold
        sys.stdout.reconfigure(errors="replace")
    print(report.replace("\n\n", f"\n\nreference data source: {reference_source(args)}\n\n", 1))
    return exit_code(checks)


def cmd_dashboard(args: argparse.Namespace) -> int:
    import subprocess

    app = Path(__file__).resolve().parent / "dashboard.py"
    env = {**os.environ, "AP_DB_PATH": str(Path(args.db).resolve())}
    if args.env_file:
        env["AP_ENV_FILE"] = str(Path(args.env_file).resolve())
    from .offline import dashboard_address, is_loopback, streamlit_flags

    port = args.port or free_port(8501)
    address = args.address or dashboard_address()
    # Local only by default (127.0.0.1), no usage statistics, fonts and icons served from the package.
    cmd = [
        sys.executable, "-m", "streamlit", "run", str(app), *streamlit_flags(port, address),
        "--theme.base", str(Path(__file__).resolve().parent / "assets" / "theme.toml"),
    ]  # fmt: skip
    host = address if address not in ("0.0.0.0", "::") else "localhost"
    print(f"Dashboard: http://{host}:{port}  (it opens in your browser; Ctrl+C here to stop)", file=sys.stderr)
    if not is_loopback(address):
        print(f"Listening on {address}: other computers on the network can open the dashboard.", file=sys.stderr)
    try:
        return subprocess.call(cmd, env=env)
    except KeyboardInterrupt:
        return 0


def free_port(start: int) -> int:
    """The first port from ``start`` that nothing on this computer is listening on."""
    import socket

    for port in range(start, start + 50):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start


def _inputs(paths: list[str]) -> list[Path] | None:
    """The invoice files named on the command line (folders expanded); None, said in one line, when one of
    them is not there (a typo)."""
    try:
        return discover_inputs(paths)
    except FileNotFoundError as exc:
        print(f"Not found: {exc}. Give an invoice file or a folder of them.", file=sys.stderr)
        return None


def cmd_process(args: argparse.Namespace, settings: Settings) -> int:
    settings = settings.with_overrides(
        di_model=args.extraction_model,
        deployment=args.deployment,
        model_name=args.model_name,
        vision=args.vision,
    )
    inputs = _inputs(args.inputs)
    if inputs is None:
        return 2
    if not inputs:
        print("No supported invoice files found.", file=sys.stderr)
        return 2
    source = reference_source(args)
    if source.startswith("BUNDLED"):
        print("NOTE: using the bundled SAMPLE GL accounts (import yours in the dashboard).", file=sys.stderr)
    reference = _load_reference(args)
    store = None if args.no_db else Store(args.db)
    pipeline = InvoicePipeline(settings, reference, cache_dir=args.cache_dir or None, store=store)
    if store is not None and not args.force:
        done = [p for p in inputs if store.find_by_hash(p) is not None]
        if done:
            print(
                f"Skipping {len(done)} file(s) already in the review queue or approved "
                "(use --force to process them again).",
                file=sys.stderr,
            )
            inputs = [p for p in inputs if p not in done]
        if not inputs:
            return 0

    stems = output_stems(inputs)

    def save(i: int, res: PipelineResult) -> None:  # as each invoice finishes, so Ctrl+C loses nothing done
        write_outputs(res, args.out, stem=stems[i])
        if args.stdout and res.output:
            print(json.dumps(res.output, indent=2, ensure_ascii=False))

    results = pipeline.process_many(inputs, workers=args.workers, on_result=save)
    summaries = [res.summary() for res in results]

    Path(args.out).mkdir(parents=True, exist_ok=True)
    (Path(args.out) / "batch_summary.json").write_text(json.dumps(summaries, indent=2), encoding="utf-8")
    _print_summary(summaries)
    return 0 if all(r.ok for r in results) else 1


SETTLE_SECONDS = 5  # a file changed more recently than this may still be copying in


def new_files(folder: Path, store: Store, now: float | None = None) -> list[Path]:
    """Invoice files in ``folder`` that AP Coder has not seen (failed attempts count as seen: no retry loop).
    Two identical files dropped together are one invoice: only the first is returned."""
    now = time.time() if now is None else now
    if not folder.is_dir():
        return []
    found = []
    seen: set[str] = set()  # the contents already in this check (the store knows them only once processed)
    for p in invoice_files(folder):
        try:  # a file being copied, locked by a scanner or removed meanwhile waits for the next check
            if now - p.stat().st_mtime < SETTLE_SECONDS:
                continue
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            if digest not in seen and store.find_by_hash(p, include_failed=True) is None:
                found.append(p)
            seen.add(digest)
        except OSError:
            continue
    return found


def watch_once(args: argparse.Namespace, settings: Settings, store: Store, folder: Path) -> tuple[int, int]:
    """One check of the watched folder: (invoices processed, of which failed)."""
    for mail in unpack_folder(folder):  # invoices attached to saved emails (.eml); a bad one is filed away
        if mail.error:
            print(f"{time.strftime('%H:%M:%S')} {mail.email}: {mail.error}; moved to emails/could not read",
                  file=sys.stderr)  # fmt: skip
            continue
        print(f"{time.strftime('%H:%M:%S')} {mail.email}: {len(mail.saved)} attachment(s) to process",
              file=sys.stderr)  # fmt: skip
    files = new_files(folder, store)
    if not files:
        return 0, 0
    reference = _load_reference(args)  # picks up GL accounts edited in the dashboard meanwhile
    pipeline = InvoicePipeline(settings, reference, cache_dir=args.cache_dir or None, store=store)
    done = failed = 0
    for path in files:
        try:
            result = pipeline.process(path)
        except OSError as exc:  # unreadable now: try again at the next check
            print(f"{time.strftime('%H:%M:%S')} {path.name}: skipped for now ({exc})", file=sys.stderr)
            continue
        stamp = time.strftime("%H:%M:%S")
        done += 1
        if result.ok:
            flag = "needs attention" if result.report and result.report.requires_review else "ready"
            print(f"{stamp} {path.name}: {result.output.get('vendor_name', '?')} ({flag})", file=sys.stderr)
        else:
            failed += 1
            print(f"{stamp} {path.name}: FAILED {result.error}", file=sys.stderr)
    return done, failed


def cmd_watch(args: argparse.Namespace, settings: Settings) -> int:
    # Like the dashboard: without Azure the invoices are read on this computer, and coded from AP's approvals
    # (plus LM Studio when its server answers), so there is nothing to refuse here.
    store = Store(args.db)
    folder = Path(args.folder)
    folder.mkdir(parents=True, exist_ok=True)
    print(f"Watching {folder} every {args.every}s. New invoices go to the review queue. Ctrl+C to stop.",
          file=sys.stderr)  # fmt: skip
    try:
        while True:
            try:
                done, failed = watch_once(args, settings, store, folder)
            except Exception as exc:  # noqa: BLE001 - e.g. the GL accounts CSV open in Excel: the next check retries
                again = "run it again" if args.once else "trying again at the next check"
                print(f"{time.strftime('%H:%M:%S')} this check stopped: {type(exc).__name__}: {exc} ({again})",
                      file=sys.stderr)  # fmt: skip
                if args.once:
                    return 1
            else:
                if args.once:
                    return 1 if done and failed == done else 0  # every invoice failed: say so to Task Scheduler
            time.sleep(max(args.every, 5))
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        return 0


def cmd_read_pages(args: argparse.Namespace, settings: Settings) -> int:
    """The page reader's queue, read once (Windows Task Scheduler overnight, or by hand). Ctrl+C stops it: the
    invoice being read goes back in line, its pages read so far kept."""
    store = Store(args.db)
    try:
        return _read_pages(args, settings, store)
    except KeyboardInterrupt:
        print("Stopped: the invoice being read is back in line (pages already read are kept).", file=sys.stderr)
        return 130


def _read_pages(args: argparse.Namespace, settings: Settings, store: Store) -> int:
    from .page_worker import ReadOutcome, read_one, ready, release_interrupted, run_queue, self_test_if_due

    cache = Path(args.cache_dir) if args.cache_dir else None
    if getattr(args, "test", False):
        return _test_page_reader(settings, store)

    def say(outcome: ReadOutcome) -> None:
        stamp = time.strftime("%H:%M:%S")
        note = f": {outcome.message}" if outcome.message else ""
        print(f"{stamp} invoice {outcome.invoice_id}: {outcome.status} ({outcome.pages} page(s), "
              f"{outcome.seconds / 60:.1f} min){note}", file=sys.stderr)  # fmt: skip

    try:  # OvisOCR2 with no self-test that passed: tested first, on its own
        tested = self_test_if_due(settings, store, on_page=_test_progress)
    except KeyboardInterrupt:
        print("Stopped: the self-test wasn't finished, so nothing was kept.", file=sys.stderr)
        return 130
    if tested is not None:
        _say_test(tested)
    model, why = ready(settings, store)
    if not model:
        print(f"Nothing read: {why}.", file=sys.stderr)
        return 1
    if args.invoice is not None:
        release_interrupted(store)
        store.queue_page_read(args.invoice, "asked", requested_by="command line")
        outcome = read_one(settings, store, cache_dir=cache, invoice_id=args.invoice)
        if outcome is None:
            print("Nothing read: the invoice isn't there.", file=sys.stderr)
            return 1
        say(outcome)
        return 0 if outcome.status == "done" else 1
    waiting = store.page_reads_waiting()
    print(f"{waiting} invoice(s) waiting for the page reader.", file=sys.stderr)
    done = run_queue(settings, store, minutes=args.minutes, cache_dir=cache, on_result=say)
    print(f"Read {len(done)}; {store.page_reads_waiting()} still waiting.", file=sys.stderr)
    return 0


def _test_progress(number: int, total: int) -> None:
    if number == 1:
        print("Self-test of the page reader: reading a test invoice whose answers are known (10 to 20 minutes on a "
              "laptop without a graphics card); Ctrl+C stops it.", file=sys.stderr)  # fmt: skip
    print(f"{time.strftime('%H:%M:%S')} reading page {number} of {total} of the test invoice…", file=sys.stderr)


def _say_test(record: dict) -> None:
    verdict = "passed: it reads the queue" if record.get("ok") else "failed: it reads nothing until it passes"
    print(f"{record.get('model', '')} {verdict} ({record.get('fields_right', 0)} of {record.get('fields_total', 0)} "
          f"fields right in {(record.get('seconds') or 0) / 60:.1f} min). {record.get('problem') or ''}".rstrip(),
          file=sys.stderr)  # fmt: skip


def _test_page_reader(settings: Settings, store: Store) -> int:
    """``read-pages --test``: the page reader's self-test, run again now (it runs on its own the first time)."""
    from . import page_reader
    from .page_worker import run_test

    status = page_reader.reader_status(settings)
    if not status.usable:
        print(f"Nothing tested: {status.note or 'OvisOCR2 is not running in LM Studio'}.", file=sys.stderr)
        return 1
    estimate = page_reader.page_seconds_estimate(settings, model=status.model)
    wait = page_reader.duration(estimate * 2) if estimate else "about 10–20 minutes on a laptop without a graphics card"
    print(f"Testing {status.model} on the test invoice ({wait}); Ctrl+C stops it.", file=sys.stderr)
    try:
        record = run_test(settings, store, status.model,
                          on_page=lambda n, total: print(f"{time.strftime('%H:%M:%S')} reading page {n} of {total}…",
                                                         file=sys.stderr))  # fmt: skip
    except KeyboardInterrupt:
        print("Stopped: the test wasn't finished, so nothing was kept.", file=sys.stderr)
        return 130
    if record is None:
        print("Nothing tested: a test of the page reader is already under way (in the dashboard?).", file=sys.stderr)
        return 1
    _say_test(record)
    return 0 if record.get("ok") else 1


def _print_summary(rows: list[dict]) -> None:
    print(
        f"\n{'file':<40} {'status':<7} {'lines':>5} {'total':>12} {'conf':>5} {'adj':>5} {'review':<6} err/warn",
        file=sys.stderr,
    )
    for r in rows:
        if r["status"] != "ok":
            print(f"{r['file'][:40]:<40} FAILED  {r.get('error', '')}", file=sys.stderr)
            continue
        print(
            f"{r['file'][:40]:<40} {'ok':<7} {r['line_items']:>5} {r['grand_total']:>12,.2f} "
            f"{r['model_confidence']:>5.2f} {r['adjusted_confidence']:>5.2f} "
            f"{'YES' if r['requires_review'] else 'no':<6} {r['errors']}/{r['warnings']}",
            file=sys.stderr,
        )


def cmd_extract(args: argparse.Namespace, settings: Settings) -> int:
    settings = settings.with_overrides(di_model=args.extraction_model)
    extractor = DocumentExtractor(settings.document_intelligence, cache_dir=args.cache_dir or None)
    paths = _inputs(args.inputs)
    if paths is None:
        return 2
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for path, stem in zip(paths, output_stems(paths), strict=True):
        try:
            result = extractor.extract(path)
        except RuntimeError as exc:  # e.g. no Azure endpoint set: one line, not a traceback
            print(f"{path.name}: not extracted: {exc}", file=sys.stderr)
            return 1
        (out / f"{stem}.extraction.md").write_text(result.content, encoding="utf-8")
        if result.raw is not None:
            (out / f"{stem}.di.json").write_text(json.dumps(result.raw, indent=2), encoding="utf-8")
        print(f"{path.name}: {result.page_count} page(s), {result.table_count} table(s) -> {out}", file=sys.stderr)
    return 0


def cmd_labels(args: argparse.Namespace) -> int:
    if Path(args.out).exists() and not args.force:
        print(
            f"{args.out} already exists and may contain your team's corrections. "
            "Use -o to write a new file, or --force to overwrite it.",
            file=sys.stderr,
        )
        return 2
    if not any(_prediction_files(Path(args.predictions))):  # an empty workbook would only block the next run
        print(f"Nothing to label: no processed invoices in {args.predictions} (run `process` first).",
              file=sys.stderr)  # fmt: skip
        return 1
    reference = _load_reference(args) if args.out.lower().endswith(".xlsx") else None
    count = export_labels(args.predictions, args.out, reference=reference, blind=args.blind)
    print(f"Wrote {count} invoice(s) to {args.out}. Ask the AP team to correct it and mark rows reviewed=Y.")
    return 0 if count else 1


def cmd_evaluate(args: argparse.Namespace) -> int:
    report = evaluate(args.predictions, args.ground_truth, target=args.target)
    data = report.to_dict()
    if not args.show_mismatches:
        data["mismatches"] = f"{len(report.mismatches)} (use --show-mismatches)"
    print(json.dumps(data, indent=2, default=str))
    return 0 if report.meets_target else 1


def cmd_share_report(args: argparse.Namespace) -> int:
    out = Path(args.out)
    key_file = out.with_name(out.stem + "_key.csv")
    try:
        text = build_share_report(
            args.predictions, args.ground_truth, args.include_codes, key_file=key_file, db_path=args.db
        )
    except FileNotFoundError as exc:  # nothing processed yet (or a ground-truth path that is not there)
        print(f"No report written: {exc}.", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(text)
    print(
        f"(Saved to {out}. Review it, then paste it into the chat. doc-NN -> file mapping stays local in {key_file}.)",
        file=sys.stderr,
    )
    return 0


def cmd_export_training(args: argparse.Namespace) -> int:
    import datetime as dt

    from .training_export import export_training_set, training_invoices

    store = Store(args.db)
    try:
        candidates = training_invoices(store, args.since)
    except ValueError:
        print(f"--since takes a date as YYYY-MM-DD, not {args.since!r}.", file=sys.stderr)
        return 2
    if not candidates["invoices"]:
        print("No approved invoices to export yet" + (f" since {args.since}" if args.since else "") + ".",
              file=sys.stderr)  # fmt: skip
        return 1
    default = Path(args.db).resolve().parent / "training" / f"ap-coder-training-{dt.date.today().isoformat()}.zip"
    out = Path(args.out) if args.out else default
    counts = export_training_set(store, out, since=args.since)
    print(f"Wrote {counts['invoices']} invoice(s), {counts['pages']} page image(s) to {out}", file=sys.stderr)
    for key, text in (("missing_files", "file no longer on this computer"), ("no_pages", "no page to show"),
                      ("demo", "demo invoice"), ("unreviewed", "approved without a person")):  # fmt: skip
        if counts[key]:
            print(f"  left out: {counts[key]} ({text})", file=sys.stderr)
    print("It stays on this computer: it holds your suppliers' invoices; README.txt inside says how to use it.",
          file=sys.stderr)  # fmt: skip
    return 0 if counts["invoices"] else 1


def cmd_demo(args: argparse.Namespace, settings: Settings) -> int:
    from .demo import load_demo, remove_demo

    store = Store(args.db)
    if args.remove:
        print(f"Removed {remove_demo(store)} demo invoice(s) and what was learned from them.", file=sys.stderr)
        return 0
    result = load_demo(store, settings)
    print(
        f"Demo loaded: {result['to_review']} invoice(s) to review, {result['approved']} already approved. "
        "Start the dashboard to try it.",
        file=sys.stderr,
    )
    return 0


def cmd_schema(args: argparse.Namespace) -> int:
    schema = build_json_schema(
        _load_reference(args),
        constrain_codes=not args.no_constrain_codes,
    )
    print(json.dumps(schema, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # Azure SDK HTTP logging is extremely noisy at INFO.
    logging.getLogger("azure").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    settings = Settings.from_env(args.env_file)
    commands = {
        "dashboard": lambda: cmd_dashboard(args),
        "doctor": lambda: cmd_doctor(args, settings),
        "process": lambda: cmd_process(args, settings),
        "watch": lambda: cmd_watch(args, settings),
        "read-pages": lambda: cmd_read_pages(args, settings),
        "extract": lambda: cmd_extract(args, settings),
        "labels": lambda: cmd_labels(args),
        "evaluate": lambda: cmd_evaluate(args),
        "share-report": lambda: cmd_share_report(args),
        "export-training": lambda: cmd_export_training(args),
        "demo": lambda: cmd_demo(args, settings),
        "schema": lambda: cmd_schema(args),
    }
    return commands[args.command]()


if __name__ == "__main__":
    sys.exit(main())
