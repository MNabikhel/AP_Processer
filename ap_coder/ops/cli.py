"""Command-line entry point: ``python -m ap_coder <command>``. ``build_parser`` lists the commands and their options,
``main`` runs one (its table of ``cmd_<command>`` functions); the company server's own commands are in
``service.py`` and ``server_update.py``, ``jde`` in ``jde_cmd.py``."""

from __future__ import annotations

import argparse
import contextlib
import dataclasses
import datetime as dt
import hashlib
import json
import logging
import os
import sys
import time
from collections.abc import Collection, Iterator
from pathlib import Path
from typing import Any

from .. import __version__
from ..ai.evaluation import evaluate
from ..ai.extraction import DocumentExtractor
from ..ai.labels import _prediction_files, export_labels
from ..ai.pipeline import (
    InvoicePipeline,
    PipelineResult,
    discover_inputs,
    invoice_files,
    other_files,
    output_stems,
    write_outputs,
)
from ..coding.tax import TaxSetup, load_tax_mapping
from ..core import ui
from ..core.config import Settings
from ..core.paths import PROJECT_DIR, env_path
from ..core.reference_data import ReferenceData, load_reference_data, load_table, parse_policy_notes
from ..core.schema import build_json_schema
from ..core.store import SAMPLE_DATA_DIR, Store, default_db_path, private_dir
from ..intake.mailbox import EMAIL_EXTENSIONS, emails_in, unpack, unpack_folder
from . import server_update, service
from .doctor import FAIL, Check, exit_code, format_checks, run_checks
from .share_report import build_share_report

_REFERENCE_FILES = {
    "coa": ("chart_of_accounts", (".csv", ".json")),
    "cost_centers": ("cost_centers", (".csv", ".json")),
    "tax_mapping": ("tax_gl_mapping", (".csv",)),
    "policy": ("coding_policy", (".md", ".txt")),
}


def reference_dir() -> tuple[Path, bool]:
    """Return (directory, is_sample). AP_REFERENCE_DIR > <data folder>/reference > bundled sample data."""
    env_dir = env_path("AP_REFERENCE_DIR")
    if env_dir:
        return env_dir, False
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


def bundled_reference(args: argparse.Namespace) -> bool:
    """Whether the reference data is the sample bundled in data/ (nothing imported yet); False when that can't be
    told (a damaged database: the doctor says so itself)."""
    try:
        return reference_source(args).startswith("BUNDLED")
    except Exception:  # noqa: BLE001
        return False


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
    db_path = env_path("AP_DB_PATH") or db_path  # the database the dashboard uses, as it chooses it
    parser = argparse.ArgumentParser(
        prog="ap_coder", description="AP Coder: offline AP invoice coding for Canadian invoices"
    )
    parser.add_argument(
        "--env-file", default=None, help="Path to a .env file (default: the data folder's, else ./.env)"
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    parser.add_argument("--db", default=str(db_path), help=f"Dashboard database (default: {db_path})")
    sub = parser.add_subparsers(dest="command", required=True)

    for name, text in (("serve", "Open the review dashboard in your browser (runs on this computer)"),
                       ("dashboard", "The same as serve")):  # fmt: skip
        p = sub.add_parser(name, help=text)
        p.add_argument("--port", type=int, default=None, help="Default: 8501, or the next free port")
        p.add_argument(
            "--host", "--address", dest="host", default=None,
            help="Address to listen on. Default 127.0.0.1 (this computer only), or AP_DASHBOARD_ADDRESS; "
            "0.0.0.0 lets others on the network open it",
        )  # fmt: skip
        p.add_argument("--no-browser", action="store_true", help="Don't open the browser (it prints the address)")
        p.add_argument(
            "--server",
            action="store_true",
            help="Run as the company's server (AP_SERVER_MODE=1): HTTPS, every network, no browser",
        )
        p.add_argument("--certfile", default=None, help="HTTPS certificate, a PEM file (or AP_TLS_CERT)")
        p.add_argument("--keyfile", default=None, help="The certificate's private key, a PEM file (or AP_TLS_KEY)")
        p.add_argument(
            "--proxy",
            default=None,
            metavar="IP",
            help="The reverse proxy's address: its X-Forwarded-For is believed (from no one else)",
        )
        p.add_argument("--insecure-http", action="store_true",
                       help="Server mode on plain HTTP, for a first test on a trusted network only")  # fmt: skip
        p.add_argument("--workers", type=int, default=None, metavar="N",
                       help="Server mode: N web worker processes and one for the background work (default 1, or "
                       "AP_SERVER_WORKERS; the service: server.json's workers)")  # fmt: skip
        p.add_argument("--background-work", action="store_true", help=argparse.SUPPRESS)  # (the server's own)

    p = sub.add_parser("doctor", help="Check configuration, reference data and (with --online) the models")
    p.add_argument(
        "--online",
        action="store_true",
        help="Also ask the local model in LM Studio for two lines' accounts (the dry run); Azure only with "
        "AP_ALLOW_INTERNET=1 (costs about one invoice)",
    )
    _add_reference_args(p)
    _add_server_ops(sub)

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
    p.add_argument("folder", nargs="?", default=None,
                   help="Default: the dashboard's folder (invoices, next to the database)")  # fmt: skip
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

    p = sub.add_parser(
        "pilot-score", help="Shadow pilot: compare what AP Coder approved with what AP posted in JD Edwards"
    )
    p.add_argument("--vouchers", required=True, help="The F0411 voucher export from JD Edwards (CSV or Excel)")
    p.add_argument("--distribution", default=None, help="The F0911 G/L distribution export (optional: the coding)")
    p.add_argument("-o", "--out", default=None, help="Folder for the report (default: pilot_score in the data folder)")
    p.add_argument("--implied-decimals", action="store_true", help="Amounts without a decimal point (550 = 5.50)")
    p.add_argument("--from", dest="start", default="", help="Only invoices dated on or after this day (YYYY-MM-DD)")
    p.add_argument("--to", dest="end", default="", help="Only invoices dated on or before this day (YYYY-MM-DD)")
    p.add_argument(
        "--all", dest="every_voucher", action="store_true",
        help="Keep every voucher of the export (default: those dated like the invoices approved here)",
    )  # fmt: skip

    p = sub.add_parser("demo", help="Load the sample invoices into the review queue (no Azure needed)")
    p.add_argument("--remove", action="store_true", help="Remove the demo invoices and their lessons instead")

    from ..core.store.users import ROLES

    p = sub.add_parser("users", help="Server mode: the people who log in (add, list, reset, deactivate, activate, "
                       "role)")  # fmt: skip
    users = p.add_subparsers(dest="users_command", required=True)
    u = users.add_parser("add", help="Add a person (the password is asked for, twice)")
    u.add_argument("username")
    u.add_argument("--role", choices=ROLES, default="clerk", help="Default: clerk")
    u.add_argument("--admin", action="store_true", help="The same as --role admin (the first Admin)")
    u.add_argument("--name", default="", help="The name on their approvals (default: the username)")
    users.add_parser("list", help="Everyone who can log in, with their role")
    u = users.add_parser("reset", help="A new temporary password (asked for, twice); they choose their own at login")
    u.add_argument("username")
    u = users.add_parser("deactivate", help="Turn an account off: it can no longer log in")
    u.add_argument("username")
    u = users.add_parser("activate", help="Turn an account that was turned off on again: it can log in again")
    u.add_argument("username")
    u = users.add_parser("role", help="Change a person's role")
    u.add_argument("username")
    u.add_argument("role", choices=ROLES)

    from . import jde_cmd

    jde_cmd.add_parser(sub)  # the JD Edwards API connection (D-104)

    p = sub.add_parser("hub", help="The finance hub feed (Microsoft Fabric): send what changed (docs/FINANCE_HUB.md)")
    hub = p.add_subparsers(dest="hub_command", required=True)
    h = hub.add_parser("export", help="Write the tables' changes to the finance hub's folder or OneLake")
    h.add_argument("--full", action="store_true", help="Every row again (a full rebuild), not only what changed")
    h.add_argument("--dry-run", action="store_true", help="Say what would be sent; write and send nothing")
    hub.add_parser("status", help="Whether the feed is on, where it goes and how the last run went")

    p = sub.add_parser("schema", help="Developers: print the coding schema a cloud build sends to Azure OpenAI")
    p.add_argument("--no-constrain-codes", action="store_true", help="Do not embed valid codes as enums")
    _add_reference_args(p)
    return parser


def _add_server_ops(sub: Any) -> None:
    """The company server's own commands (D-100): ``service`` and ``server-update`` (docs/SERVER.md)."""
    service.add_parser(sub)
    server_update.add_parser(sub)


def cmd_doctor(args: argparse.Namespace, settings: Settings) -> int:
    checks = run_checks(settings, lambda: _load_reference(args), online=args.online, bundled=bundled_reference(args))
    try:
        source = reference_source(args)
    except Exception as exc:  # noqa: BLE001 - a damaged database file: the doctor reports it, it never crashes
        db = Path(getattr(args, "db", None) or default_db_path())
        checks.append(Check("database", FAIL, f"{db} cannot be opened ({exc}): it is damaged or not an AP Coder "
                            "database. Put back a copy from the backups folder next to it."))  # fmt: skip
        source = "none (the database cannot be opened)"
    else:
        from .doctor import store_checks
        from .service import is_server_here

        db = Path(getattr(args, "db", None) or default_db_path())
        server = is_server_here(private_dir())
        if db.exists() or server:  # a laptop not set up yet has no database: nothing to look at
            checks.extend(store_checks(Store(db), server))
    report = format_checks(checks)
    print(report.replace("\n\n", f"\n\nreference data source: {source}\n\n", 1))
    return exit_code(checks)


def cmd_serve(args: argparse.Namespace) -> int:
    """Run the dashboard in this process (ap_coder/web/server.py) until Ctrl+C or the launcher stops it; with
    ``--server``, as the company's server (HTTPS, D-99) until its service is stopped."""
    if args.env_file:  # the settings the dashboard reads again for every page
        os.environ["AP_ENV_FILE"] = str(Path(args.env_file).resolve())
    from ..core.store import Store
    from ..web import server
    from .offline import DEFAULT_PORT, SERVER_ENV, free_port, is_loopback, server_mode

    if args.server:
        os.environ[SERVER_ENV] = "1"  # (the pages ask server_mode())
    if args.background_work:  # the company server's background process, started by its serve (D-111)
        return server.run_background(Store(Path(args.db)))

    certfile = args.certfile or os.environ.get(server.CERT_ENV) or None
    keyfile = args.keyfile or os.environ.get(server.KEY_ENV) or None
    refusal = server.tls_refusal(server_mode(), certfile, keyfile, args.insecure_http)
    if refusal:
        print(refusal, file=sys.stderr)
        return 2
    host = args.host or server.default_host()
    # The launcher's own choice: 8501, else the next free one. Never on the company server: staff were given its
    # address with its port, so a port in use stops it, in plain words (D-102).
    port = args.port or (DEFAULT_PORT if server_mode() else free_port(address=host))
    url = server.browser_url(host, port, bool(certfile))
    taken = server.port_refusal(host, port, bool(certfile)) if server_mode() else ""
    if taken:  # said before "AP Coder server: ..." could be
        print(taken, file=sys.stderr)
        return 1
    if server_mode():
        print(f"AP Coder server: {url} on this computer; staff open it by the server's name (Ctrl+C here to stop)",
              file=sys.stderr)  # fmt: skip
    else:
        opens = "" if args.no_browser else "it opens in your browser; "  # (the launcher opens it, and says so itself)
        print(f"Dashboard: {url}  ({opens}Ctrl+C here to stop)", file=sys.stderr)
        if not is_loopback(host if host not in ("", "localhost") else "127.0.0.1"):
            print(f"Listening on {host}: other computers on the network can open the dashboard. Colleagues give their "
                  "name first, with no password: anyone who can reach this address can see the invoices. For logins, "
                  "run it as the company server (server mode).", file=sys.stderr)  # fmt: skip
    network = {"certfile": certfile, "keyfile": keyfile, "proxy": args.proxy, "insecure_http": args.insecure_http}
    try:
        workers = args.workers or int((os.environ.get(server.WORKERS_ENV) or "1").strip())
    except ValueError:
        workers = 1  # a typo never stops the server: one worker, as before
    if server_mode() and workers > 1:
        network["workers"] = workers
    extra = network if server_mode() or any(network.values()) else {}  # (the laptop's call is as before)
    return server.serve(host, port, open_browser=not args.no_browser, store=Store(Path(args.db)), **extra)


def _inputs(paths: list[str], store: Store | None = None) -> tuple[list[Path], bool, int] | None:
    """The invoice files named on the command line (folders expanded, the invoices taken out of saved emails, as
    the dashboard and ``watch`` do), each invoice once: a file named twice, or two identical copies, is one invoice;
    whether an email could not be read (said on screen: the command then exits 1); and how many emails were sorted
    (each in the Activity log of ``store``, as ``watch`` logs them). None, said in one line, when one of them is not
    there (a typo)."""
    from ..intake.inbox import log_sorted

    try:
        found = discover_inputs(paths)
    except FileNotFoundError as exc:
        print(f"Not found: {exc}. Give an invoice file or a folder of them.", file=sys.stderr)
        return None
    others = [p.name for raw in paths if Path(raw).is_dir() for p in other_files(Path(raw), EMAIL_EXTENSIONS)]
    if others:  # never passed over in silence (B-268)
        print(f"Left out, not a kind AP Coder reads (save as PDF or JPEG): {', '.join(others)}", file=sys.stderr)
    files = [p for p in found if p.suffix.lower() not in EMAIL_EXTENSIONS]
    emails = [p for p in found if p.suffix.lower() in EMAIL_EXTENSIONS]
    emails += [e for raw in paths if Path(raw).is_dir() for e in emails_in(Path(raw))]
    unread, sorted_emails = False, 0
    for path in dict.fromkeys(emails):
        try:
            mail = unpack(path)  # attachments saved next to the email; the email moves to emails/
        except OSError as exc:
            print(f"{path.name}: not read ({exc})", file=sys.stderr)
            unread = True
            continue
        if mail.error:
            print(f"{mail.email}: {mail.error}; moved to emails/could not read", file=sys.stderr)
            unread = True
        else:
            print(f"{mail.email}: {_sorted_line(mail)}", file=sys.stderr)
            files += mail.saved
            sorted_emails += 1
            if store is not None:
                log_sorted(store, mail)
    unique: dict[str, Path] = {}
    for path in files:
        try:
            key = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            key = str(path.resolve())  # unreadable now: processing it says so
        unique.setdefault(key, path)
    if len(unique) < len(files):
        print(f"{len(files) - len(unique)} file(s) left out: the same invoice as another file given.", file=sys.stderr)
    return list(unique.values()), unread, sorted_emails


NO_GL_ACCOUNTS = (
    "Finish the setup first: import your GL accounts in the dashboard (GL accounts & tax). Invoices "
    "coded before that could not be checked or approved."
)


def cmd_process(args: argparse.Namespace, settings: Settings) -> int:
    settings = settings.with_overrides(
        di_model=args.extraction_model,
        deployment=args.deployment,
        model_name=args.model_name,
        vision=args.vision,
    )
    store = None if args.no_db else Store(args.db)
    given = _inputs(args.inputs, store)
    if given is None:
        return 2
    inputs, unread, sorted_emails = given
    if not inputs:
        if sorted_emails:  # only emails, each sorted (said above): nothing wrong, nothing else to read
            print(f"No invoices to read: {sorted_emails} email(s) sorted.", file=sys.stderr)
            return 1 if unread else 0
        print("No supported invoice files found.", file=sys.stderr)
        return 2
    if store is not None and not store.list_accounts("gl_accounts"):  # as the dashboard (B-1212)
        print(NO_GL_ACCOUNTS, file=sys.stderr)
        return 2
    source = reference_source(args)
    if source.startswith("BUNDLED"):
        print("NOTE: using the bundled SAMPLE GL accounts (import yours in the dashboard).", file=sys.stderr)
    reference = _load_reference(args)
    pipeline = InvoicePipeline(settings, reference, cache_dir=args.cache_dir or None, store=store)
    started, clock, skipped = dt.datetime.now().isoformat(timespec="seconds"), time.monotonic(), 0
    if store is not None and not args.force:
        done = [p for p in inputs if store.find_by_hash(p) is not None]
        skipped = len(done)
        if done:
            print(
                f"Skipping {len(done)} file(s) already in the review queue or approved "
                "(use --force to process them again).",
                file=sys.stderr,
            )
            inputs = [p for p in inputs if p not in done]
        if not inputs:
            return 1 if unread else 0

    stems = output_stems(inputs)

    def save(i: int, res: PipelineResult) -> None:  # as each invoice finishes, so Ctrl+C loses nothing done
        write_outputs(res, args.out, stem=stems[i])
        if args.stdout and res.output:
            print(json.dumps(res.output, indent=2, ensure_ascii=False))

    results = pipeline.process_many(inputs, workers=args.workers, on_result=save)
    summaries = [res.summary() for res in results]
    if store is not None:  # the run in the audit trail (the finance hub's workflow_runs, D-103)
        store.record_import_run(
            "process", files=len(results), ok=sum(1 for r in results if r.ok),
            failed=sum(1 for r in results if not r.ok), skipped=skipped, started_at=started,
            seconds=time.monotonic() - clock, errors=[f"{r.source.name}: {r.error}" for r in results if not r.ok],
            actor=login_name(),
        )  # fmt: skip

    Path(args.out).mkdir(parents=True, exist_ok=True)
    (Path(args.out) / "batch_summary.json").write_text(json.dumps(summaries, indent=2), encoding="utf-8")
    _print_summary(summaries)
    return 0 if all(r.ok for r in results) and not unread else 1


SETTLE_SECONDS = 5  # a file changed more recently than this may still be copying in


def new_files(folder: Path, store: Store, now: float | None = None, fresh: Collection[Path] = ()) -> list[Path]:
    """Invoice files in ``folder`` that AP Coder has not seen (failed attempts count as seen: no retry loop).
    Two identical files dropped together are one invoice: only the first is returned. ``fresh`` files were just
    written by AP Coder itself (taken out of emails): they are not "still copying"."""
    now = time.time() if now is None else now
    if not folder.is_dir():
        return []
    found = []
    seen: set[str] = set()  # the contents already in this check (the store knows them only once processed)
    for p in invoice_files(folder):
        try:  # a file being copied, locked by a scanner or removed meanwhile waits for the next check
            if p not in fresh and abs(now - p.stat().st_mtime) < SETTLE_SECONDS:  # (far ahead of the clock: settled)
                continue
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            if digest not in seen and store.find_by_hash(p, include_failed=True) is None:
                found.append(p)
            seen.add(digest)
        except OSError:
            continue
    return found


def still_copying(folder: Path, now: float, fresh: Collection[Path] = ()) -> int:
    """How many invoice files and saved emails in ``folder`` changed in the last ``SETTLE_SECONDS``: left for the next
    check, as ``new_files`` and ``unpack_folder`` leave them."""
    if not folder.is_dir():
        return 0
    count = 0
    for p in [*invoice_files(folder), *emails_in(folder)]:
        with contextlib.suppress(OSError):
            count += p not in fresh and abs(now - p.stat().st_mtime) < SETTLE_SECONDS
    return count


def _sorted_line(mail: Any) -> str:
    """What triage made of an email, in one line: its kind, where its attachments went, why it needs a person."""
    t = mail.triage
    parts = [t.label if t else "email", f"{len(mail.saved)} invoice(s) to process"]
    if mail.statements:
        parts.append(f"{len(mail.statements)} statement(s) to statements/")
    if mail.held:
        parts.append(f"{len(mail.held)} attachment(s) set aside in emails/needs a person")
    if t and t.needs_person:
        parts.append("NEEDS A PERSON: " + "; ".join(t.reasons[:2]))
    return ", ".join(parts)


def watch_once(args: argparse.Namespace, settings: Settings, store: Store, folder: Path) -> tuple[int, int]:
    """One check of the watched folder: (invoices processed, of which failed); an email that could not be read
    counts as one of each."""
    from ..intake.inbox import log_sorted

    done = failed = 0
    taken_out: list[Path] = []  # invoices just written from emails: read in this check, not the next one
    for mail in unpack_folder(folder):  # saved emails (.eml, .msg), sorted by triage; a bad one is filed away
        if mail.error:
            print(f"{time.strftime('%H:%M:%S')} {mail.email}: {mail.error}; moved to emails/could not read",
                  file=sys.stderr)  # fmt: skip
            done, failed = done + 1, failed + 1
            continue
        print(f"{time.strftime('%H:%M:%S')} {mail.email}: {_sorted_line(mail)}", file=sys.stderr)
        log_sorted(store, mail)
        taken_out += mail.saved
    now = time.time()
    files = new_files(folder, store, now=now, fresh=set(taken_out))
    copying = still_copying(folder, now, fresh=set(taken_out))
    if copying:  # waits for the next check, said (B-1302)
        later = "run it again in a few seconds" if args.once else "the next check picks them up"
        print(f"{time.strftime('%H:%M:%S')} {ui.plural(copying, 'file')} still being copied, skipped ({later})",
              file=sys.stderr)  # fmt: skip
    if not files:
        return done, failed
    if not store.list_accounts("gl_accounts"):  # as the dashboard (B-1212): read once they are imported
        print(f"{time.strftime('%H:%M:%S')} {len(files)} file(s) not read yet: {NO_GL_ACCOUNTS}", file=sys.stderr)
        return done + len(files), failed + len(files)
    reference = _load_reference(args)  # picks up GL accounts edited in the dashboard meanwhile
    pipeline = InvoicePipeline(settings, reference, cache_dir=args.cache_dir or None, store=store)
    started, clock, read, errors = dt.datetime.now().isoformat(timespec="seconds"), time.monotonic(), 0, []
    for path in files:
        try:
            result = pipeline.process(path)
        except OSError as exc:  # unreadable now: try again at the next check
            print(f"{time.strftime('%H:%M:%S')} {path.name}: skipped for now ({exc})", file=sys.stderr)
            continue
        stamp = time.strftime("%H:%M:%S")
        done += 1
        read += 1
        if not result.ok:
            errors.append(f"{path.name}: {result.error}")
        if result.ok:
            flag = "needs attention" if result.report and result.report.requires_review else "ready"
            print(f"{stamp} {path.name}: {result.output.get('vendor_name', '?')} ({flag})", file=sys.stderr)
        else:
            failed += 1
            print(f"{stamp} {path.name}: FAILED {result.error}", file=sys.stderr)
    if read:  # the check in the audit trail (the finance hub's workflow_runs, D-103)
        store.record_import_run("watch", files=read, ok=read - len(errors), failed=len(errors), started_at=started,
                                seconds=time.monotonic() - clock, errors=errors, actor=login_name())  # fmt: skip
    return done, failed


WORKER_FILE = ".ap_coder_worker-{pid}.json"  # in the data folder while watch or read-pages runs


@contextlib.contextmanager
def running_note(command: str) -> Iterator[None]:
    """While ``watch`` or ``read-pages`` runs, ``<data folder>/.ap_coder_worker-<pid>.json`` says which AP Coder it is
    (pid, command, folder, version, started), as the server's file does: a newer version's launcher stops an older
    one before it updates (scripts/running.py), so old code never reads invoices into the same database. A note
    that can't be written never stops the command."""
    path: Path | None = private_dir() / WORKER_FILE.format(pid=os.getpid())
    note = {"pid": os.getpid(), "command": command, "folder": str(PROJECT_DIR), "version": __version__,
            "started": dt.datetime.now().isoformat(timespec="seconds")}  # fmt: skip
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")  # whole or not at all: the launcher may read it meanwhile
        tmp.write_text(json.dumps(note, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        path = None
    try:
        yield
    finally:
        if path is not None:
            with contextlib.suppress(OSError):
                path.unlink()


def cmd_watch(args: argparse.Namespace, settings: Settings) -> int:
    with running_note("watch"):
        return _watch(args, settings)


def _watch(args: argparse.Namespace, settings: Settings) -> int:
    # Like the dashboard: without Azure the invoices are read on this computer, and coded from AP's approvals
    # (plus LM Studio when its server answers), so there is nothing to refuse here.
    store = Store(args.db)
    folder = Path(args.folder) if args.folder else store.path.parent / "invoices"  # as web.core.invoice_dir
    folder.mkdir(parents=True, exist_ok=True)
    if args.once:
        print(f"Checking {folder} once. New invoices go to the review queue.", file=sys.stderr)
    else:
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
                    return 1 if done and failed == done else 0  # all failed (invoices, emails): tell Task Scheduler
            time.sleep(max(args.every, 5))
    except KeyboardInterrupt:
        print("Stopped.", file=sys.stderr)
        return 0


def cmd_read_pages(args: argparse.Namespace, settings: Settings) -> int:
    """The page reader's queue, read once (Windows Task Scheduler overnight, or by hand). Ctrl+C stops it: the
    invoice being read goes back in line, its pages read so far kept."""
    store = Store(args.db)
    try:
        with running_note("read-pages"):
            return _read_pages(args, settings, store)
    except KeyboardInterrupt:
        print("Stopped: the invoice being read is back in line (pages already read are kept).", file=sys.stderr)
        return 130


def _read_pages(args: argparse.Namespace, settings: Settings, store: Store) -> int:
    from ..ai.page_worker import ReadOutcome, read_one, ready, release_interrupted, run_queue, self_test_if_due

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
    return 1 if any(outcome.status == "failed" for outcome in done) else 0  # said above: Task Scheduler sees it too


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
    from ..ai import page_reader
    from ..ai.page_worker import run_test

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
        f"\n{'file':<40} {'status':<7} {'lines':>5} {'total':>12} {'read':>5} {'code':>5} {'review':<6} err/warn",
        file=sys.stderr,
    )
    for r in rows:
        if r["status"] != "ok":
            print(f"{r['file'][:40]:<40} FAILED  {r.get('error', '')}", file=sys.stderr)
            continue
        print(
            f"{r['file'][:40]:<40} {'ok':<7} {r['line_items']:>5} {r['grand_total']:>12,.2f} "
            f"{r.get('reading_confidence', r['model_confidence']):>5.2f} "
            f"{r.get('coding_confidence', r['adjusted_confidence']):>5.2f} "
            f"{'YES' if r['requires_review'] else 'no':<6} {r['errors']}/{r['warnings']}",
            file=sys.stderr,
        )


def cmd_extract(args: argparse.Namespace, settings: Settings) -> int:
    settings = settings.with_overrides(di_model=args.extraction_model)
    extractor = DocumentExtractor(settings.document_intelligence, cache_dir=args.cache_dir or None)
    given = _inputs(args.inputs)
    if given is None:
        return 2
    paths, unread, sorted_emails = given
    if not paths:
        if sorted_emails:
            print(f"No invoices to read: {sorted_emails} email(s) sorted.", file=sys.stderr)
            return 1 if unread else 0
        print("No supported invoice files found.", file=sys.stderr)
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
    return 1 if unread else 0


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
    if not Path(args.ground_truth).exists():
        print(f"No labels file at {args.ground_truth}: make one with `python -m ap_coder labels` and have AP "
              "correct it, or give the path of yours with --ground-truth.", file=sys.stderr)  # fmt: skip
        return 2
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


_LEFT_OUT = (
    ("missing_files", "file no longer on this computer"), ("no_pages", "no page to show"), ("demo", "demo invoice"),
    ("unreviewed", "approved without a person"), ("bulk", "bulk-approved unchanged, nobody opened them"),
)  # fmt: skip


def _say_left_out(counts: dict) -> None:
    for key, text in _LEFT_OUT:
        if counts.get(key):
            print(f"  left out: {counts[key]} ({text})", file=sys.stderr)


def cmd_export_training(args: argparse.Namespace) -> int:
    from ..ai.training_export import export_training_set, training_invoices

    store = Store(args.db)
    try:
        candidates = training_invoices(store, args.since)
    except ValueError:
        print(f"--since takes a date as YYYY-MM-DD, not {args.since!r}.", file=sys.stderr)
        return 2
    if not candidates["invoices"]:
        since = f" since {args.since}" if args.since else ""
        if any(candidates.get(key) for key, _ in _LEFT_OUT):
            print(f"Nothing to export{since}: no approved invoice was checked by a person.", file=sys.stderr)
            _say_left_out(candidates)
        else:
            print(f"No approved invoices to export yet{since}.", file=sys.stderr)
        return 1
    default = Path(args.db).resolve().parent / "training" / f"ap-coder-training-{dt.date.today().isoformat()}.zip"
    out = Path(args.out) if args.out else default
    counts = export_training_set(store, out, since=args.since)
    print(f"Wrote {counts['invoices']} invoice(s), {counts['pages']} page image(s) to {out}", file=sys.stderr)
    _say_left_out(counts)
    print("It stays on this computer: it holds your suppliers' invoices; README.txt inside says how to use it.",
          file=sys.stderr)  # fmt: skip
    return 0 if counts["invoices"] else 1


def cmd_pilot_score(args: argparse.Namespace) -> int:
    from .pilot_score import ExportError, run

    try:
        report, folder = run(Store(args.db), args.vouchers, args.distribution, args.out,
                             implied_decimals=args.implied_decimals, start=args.start, end=args.end,
                             every_voucher=args.every_voucher)  # fmt: skip
    except ExportError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    inv, coding = report["invoices"], report["coding"]
    fields = sum(h["n"] for h in report["header"].values())
    right = sum(h["right"] for h in report["header"].values())
    print(f"{inv['matched']} of {inv['ap_coder']} invoices approved in AP Coder matched a JD Edwards voucher "
          f"({inv['jd_edwards_only']} posted only in JD Edwards).")  # fmt: skip
    print(f"Header fields right: {right} of {fields}. Lines coded as posted: {coding['gl_right']} of "
          f"{coding['compared']} (as AP Coder proposed them: {coding['proposed_right']}).")  # fmt: skip
    print(f"{len(report['differences'])} differences. Report: {folder / 'pilot_score.md'}")
    print(f"Differences, one per row: {folder / 'pilot_score_differences.csv'}")
    return 0


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


def _new_password(username: str, name: str = "") -> str | None:
    """A password typed twice at the console (never shown, never an argument: it would stay in the history)."""
    import getpass

    from ..core.store.users import password_problem

    first = getpass.getpass(f"Password for {username}: ")
    problem = password_problem(first, username, name)
    if problem:
        print(problem, file=sys.stderr)
        return None
    if getpass.getpass("The same password again: ") != first:
        print("The two passwords are not the same: nothing was changed.", file=sys.stderr)
        return None
    return first


def cmd_users(args: argparse.Namespace) -> int:
    """The accounts of server mode, from the server's console (D-97): the first Admin is added here."""
    from ..core.store.users import ADMIN, ROLE_LABELS, USERNAME, fold_username

    store = Store(args.db)
    actor = "the server console"
    action = args.users_command
    if action == "list":
        found = store.list_users()
        if not found:
            print("No accounts yet. Add the first Admin: python -m ap_coder users add <username> --role admin")
        for u in found:
            state = "turned off" if not u["active"] else ("temporary password" if u["must_change"] else "active")
            print(f"{u['username']:<24} {ROLE_LABELS[u['role']]:<10} {u['display_name']:<30} {state}")
        return 0
    username = fold_username(args.username)
    if action == "add":
        if not USERNAME.fullmatch(username) or store.find_user(username) is not None:
            taken = f"There is already an account {username}." if store.find_user(username) else ""
            print(taken or "A username is 1 to 64 letters, digits, dots, dashes, underscores or @ (e.g. ann.lee).",
                  file=sys.stderr)  # fmt: skip
            return 1
        role = ADMIN if args.admin else args.role
        password = _new_password(username, args.name)
        if password is None:
            return 1
        try:
            store.add_user(username, password, role=role, display_name=args.name, actor=actor)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(f"Added {username} ({ROLE_LABELS[role]}). They choose their own password at their first login.")
        return 0
    user = store.find_user(username)
    if user is None:
        print(f"There is no account {username} (python -m ap_coder users list shows them).", file=sys.stderr)
        return 1
    try:
        if action == "reset":
            password = _new_password(username, user["display_name"])
            if password is None:
                return 1
            store.reset_password(user["id"], actor=actor, password=password)
            print(f"Password of {username} reset: they are logged out everywhere and choose their own at login.")
        elif action == "deactivate":
            store.deactivate_user(user["id"], actor=actor)
            print(f"{username} can no longer log in.")
        elif action == "activate":
            store.activate_user(user["id"], actor=actor)
            print(f"{username} can log in again, with the same password as before.")
        else:
            store.set_role(user["id"], args.role, actor=actor)
            print(f"{username} is now {ROLE_LABELS[args.role]}.")
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    return 0


def _jde(args: argparse.Namespace) -> int:
    from . import jde_cmd

    return jde_cmd.main(args)


def cmd_hub(args: argparse.Namespace) -> int:
    """``hub export [--full] [--dry-run]`` and ``hub status`` (D-103)."""
    from ..erp.hub.run import HubConfig, export, last_run

    store = Store(args.db)
    config = HubConfig.load(store)
    if args.hub_command == "status":
        where = config.folder if config.mode == "folder" else config.onelake_url
        when = f"every {config.every_hours} hours" if config.every_hours else "only when run by hand"
        print(f"Finance hub feed: {'on' if config.enabled else 'off'}; {config.mode}: {where or '(not set)'}; {when}")
        last = last_run(store)
        print(f"Last run: {last['at']} by {last.get('by')}: {last.get('summary')}" if last else "Last run: never")
        return 0
    if not config.enabled and not args.dry_run:
        print("The finance hub feed is off: an Admin turns it on in Settings → Finance hub (a dry run works "
              "anyway: hub export --dry-run).", file=sys.stderr)  # fmt: skip
        return 1
    result = export(store, config, full=args.full, dry_run=args.dry_run, actor=login_name())
    print(result.summary(), file=sys.stdout if result.ok else sys.stderr)
    return 0 if result.ok else 1


def login_name() -> str:
    """The computer login running the command (who ran it, in the audit trail)."""
    import getpass

    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001 - no login name available
        return ""


def cmd_schema(args: argparse.Namespace) -> int:
    schema = build_json_schema(
        _load_reference(args),
        constrain_codes=not args.no_constrain_codes,
    )
    print(json.dumps(schema, indent=2))
    return 0


def configure_logging(verbose: bool, stream: Any = None) -> None:
    """What the window shows: warnings and errors (everything with ``-v``). The level is set on the window's own
    handler too, not only on the root logger: importing the OCR engine (rapidocr) sets the root logger to INFO, and
    the pilot tester's window then filled with technical lines (B-240)."""
    level = logging.INFO if verbose else logging.WARNING
    root = logging.getLogger()
    before = list(root.handlers)
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)s %(name)s: %(message)s", stream=stream)
    for handler in root.handlers:
        if handler not in before:
            handler.setLevel(level)
    # Azure SDK HTTP logging is extremely noisy at INFO.
    logging.getLogger("azure").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):  # a Windows console or log file (cp1252) can't show every character of a
        sys.stdout.reconfigure(errors="replace")  # file name or vendor: "?", never a crash (stderr escapes them)
    configure_logging(args.verbose)

    # An administrator's commands on the company server read no .env: the service's account can write the data
    # folder's (D-109). They need none of its settings.
    admin = args.command == "server-update" or (args.command == "service" and args.action != "run")
    settings = Settings() if admin else Settings.from_env(args.env_file)
    commands = {
        "serve": lambda: cmd_serve(args),
        "dashboard": lambda: cmd_serve(args),
        "doctor": lambda: cmd_doctor(args, settings),
        "service": lambda: service.main(args),  # the company server (D-100)
        "server-update": lambda: server_update.main(args),
        "process": lambda: cmd_process(args, settings),
        "watch": lambda: cmd_watch(args, settings),
        "read-pages": lambda: cmd_read_pages(args, settings),
        "extract": lambda: cmd_extract(args, settings),
        "labels": lambda: cmd_labels(args),
        "evaluate": lambda: cmd_evaluate(args),
        "share-report": lambda: cmd_share_report(args),
        "export-training": lambda: cmd_export_training(args),
        "pilot-score": lambda: cmd_pilot_score(args),
        "demo": lambda: cmd_demo(args, settings),
        "schema": lambda: cmd_schema(args),
        "users": lambda: cmd_users(args),
        "jde": lambda: _jde(args),
        "hub": lambda: cmd_hub(args),
    }
    try:
        return commands[args.command]()
    except PermissionError as exc:  # Windows locks a file open in Excel (or the folder is read-only)
        where = exc.filename or "a file"
        print(f"{where} could not be opened or written: it is open in another program (Excel?) or the folder is "
              "read-only. Close it, then run the command again.", file=sys.stderr)  # fmt: skip
        return 1


if __name__ == "__main__":
    sys.exit(main())
