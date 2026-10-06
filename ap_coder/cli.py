"""Command-line entry point: ``python -m ap_coder <command>``."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from .config import Settings
from .doctor import exit_code, format_checks, run_checks
from .evaluation import evaluate
from .extraction import DocumentExtractor
from .labels import export_labels
from .pipeline import InvoicePipeline, discover_inputs, write_outputs
from .reference_data import ReferenceData, load_reference_data
from .schema import build_json_schema
from .share_report import build_share_report

SAMPLE_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
# Everything enterprise-specific lives under ./private, which is git-ignored.
PRIVATE_DIR = Path("private")
DEFAULT_OUT = PRIVATE_DIR / "output"
DEFAULT_CACHE = PRIVATE_DIR / ".cache" / "extraction"

_REFERENCE_FILES = {
    "coa": ("chart_of_accounts", (".csv", ".json")),
    "cost_centers": ("cost_centers", (".csv", ".json")),
    "tax_codes": ("tax_codes", (".csv", ".json")),
    "policy": ("coding_policy", (".md", ".txt")),
}


def reference_dir() -> tuple[Path, bool]:
    """Return (directory, is_sample). AP_REFERENCE_DIR > ./private/reference > bundled sample data."""
    env_dir = os.getenv("AP_REFERENCE_DIR")
    if env_dir:
        return Path(env_dir), False
    private = PRIVATE_DIR / "reference"
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
        if found is None and key in {"coa", "cost_centers"}:
            found = base / f"{stem}.csv"  # let the loader report the missing file clearly
        paths[key] = found
    return paths


def _load_reference(args: argparse.Namespace) -> ReferenceData:
    paths = _resolve_reference(args)
    return load_reference_data(
        paths["coa"], paths["cost_centers"], tax_codes=paths["tax_codes"], policy_notes=paths["policy"]
    )


def _add_reference_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group(
        "reference data",
        "Defaults to AP_REFERENCE_DIR, else ./private/reference, else the bundled sample data in ./data",
    )
    g.add_argument("--coa", help="Chart of Accounts (.csv/.json)")
    g.add_argument("--cost-centers", help="Cost center list (.csv/.json)")
    g.add_argument("--tax-codes", help="Tax codes (.csv/.json); '' to omit")
    g.add_argument("--policy", help="Coding policy notes (.md/.txt); '' to omit")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ap_coder", description="Enterprise AP Invoice Coder Engine (PoC)")
    parser.add_argument("--env-file", default=None, help="Path to a .env file (default: ./.env if present)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("doctor", help="Check configuration, reference data and (with --online) Azure connectivity")
    p.add_argument("--online", action="store_true", help="Call both Azure services (costs about one invoice)")
    _add_reference_args(p)

    p = sub.add_parser("process", help="Extract + GL-code invoices (files or directories)")
    p.add_argument("inputs", nargs="+", help="PDF/TIFF/image invoices, or .md/.txt pre-extracted content")
    p.add_argument("-o", "--out", default=str(DEFAULT_OUT), help=f"Output directory (default: {DEFAULT_OUT})")
    p.add_argument(
        "--extraction-model", choices=["prebuilt-layout", "prebuilt-invoice"], help="Document Intelligence model"
    )
    p.add_argument("--deployment", help="Azure OpenAI deployment name")
    p.add_argument("--model-name", help="Underlying model name (gpt-4o, gpt-4o-mini, gpt-4.1, o4-mini, ...)")
    p.add_argument("--vision", action=argparse.BooleanOptionalAction, default=None, help="Attach page images")
    p.add_argument(
        "--tax-rate-field",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Add predicted_tax_rate to each line item",
    )
    p.add_argument("--cache-dir", default=str(DEFAULT_CACHE), help="Extraction cache directory ('' to disable)")
    p.add_argument("--workers", type=int, default=1, help="Invoices processed in parallel")
    p.add_argument("--stdout", action="store_true", help="Also print each coded JSON to stdout")
    _add_reference_args(p)

    p = sub.add_parser("extract", help="Run Document Intelligence only and save Markdown + raw JSON")
    p.add_argument("inputs", nargs="+")
    p.add_argument("-o", "--out", default=str(DEFAULT_OUT))
    p.add_argument("--extraction-model", choices=["prebuilt-layout", "prebuilt-invoice"])
    p.add_argument("--cache-dir", default=str(DEFAULT_CACHE))

    p = sub.add_parser("labels", help="Create an Excel workbook for the AP team to correct (ground truth)")
    p.add_argument("--predictions", default=str(DEFAULT_OUT), help=f"Output folder of `process` ({DEFAULT_OUT})")
    p.add_argument("-o", "--out", default=str(PRIVATE_DIR / "labels.xlsx"), help="Workbook path (.xlsx or .csv)")
    p.add_argument("--blind", action="store_true", help="Leave gl_code/cost_center empty to avoid anchoring bias")
    p.add_argument("--force", action="store_true", help="Overwrite an existing workbook (loses corrections in it)")
    _add_reference_args(p)

    p = sub.add_parser("evaluate", help="Score predictions against ground truth (JSON folder, .xlsx or .csv)")
    p.add_argument("--predictions", default=str(DEFAULT_OUT))
    p.add_argument("--ground-truth", default=str(PRIVATE_DIR / "labels.xlsx"))
    p.add_argument("--target", type=float, default=0.9)
    p.add_argument("--show-mismatches", action="store_true", help="Prints invoice data; do not share the output")

    p = sub.add_parser("share-report", help="Redacted summary that is safe to paste into a chat")
    p.add_argument("--predictions", default=str(DEFAULT_OUT))
    p.add_argument("--ground-truth", help="Labels workbook/CSV or JSON folder to include accuracy figures")
    p.add_argument("--include-codes", action="store_true", help="Include GL/cost-center confusion pairs")
    p.add_argument("-o", "--out", default=str(PRIVATE_DIR / "share_report.md"))

    p = sub.add_parser("schema", help="Print the strict JSON Schema sent to Azure OpenAI")
    p.add_argument("--tax-rate-field", action="store_true")
    p.add_argument("--no-constrain-codes", action="store_true", help="Do not embed valid codes as enums")
    _add_reference_args(p)
    return parser


def cmd_doctor(args: argparse.Namespace, settings: Settings) -> int:
    base, is_sample = reference_dir()
    checks = run_checks(settings, lambda: _load_reference(args), online=args.online)
    report = format_checks(checks)
    source = "BUNDLED SAMPLE DATA (put your files in private/reference/)" if is_sample else "your reference folder"
    print(report.replace("\n\n", f"\n\nreference data source: {source}\n\n", 1))
    return exit_code(checks)


def cmd_process(args: argparse.Namespace, settings: Settings) -> int:
    settings = settings.with_overrides(
        di_model=args.extraction_model,
        deployment=args.deployment,
        model_name=args.model_name,
        vision=args.vision,
        include_tax_rate=args.tax_rate_field,
    )
    if reference_dir()[1] and args.coa is None:
        print("NOTE: using the bundled SAMPLE chart of accounts (see private/reference/).", file=sys.stderr)
    reference = _load_reference(args)
    pipeline = InvoicePipeline(settings, reference, cache_dir=args.cache_dir or None)
    inputs = discover_inputs(args.inputs)
    if not inputs:
        print("No supported invoice files found.", file=sys.stderr)
        return 2

    results = pipeline.process_many(inputs, workers=args.workers)
    summaries = []
    for res in results:
        write_outputs(res, args.out)
        summaries.append(res.summary())
        if args.stdout and res.output:
            print(json.dumps(res.output, indent=2, ensure_ascii=False))

    Path(args.out).mkdir(parents=True, exist_ok=True)
    (Path(args.out) / "batch_summary.json").write_text(json.dumps(summaries, indent=2), encoding="utf-8")
    _print_summary(summaries)
    return 0 if all(r.ok for r in results) else 1


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
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for path in discover_inputs(args.inputs):
        result = extractor.extract(path)
        (out / f"{path.stem}.extraction.md").write_text(result.content, encoding="utf-8")
        if result.raw is not None:
            (out / f"{path.stem}.di.json").write_text(json.dumps(result.raw, indent=2), encoding="utf-8")
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
    text = build_share_report(args.predictions, args.ground_truth, args.include_codes, key_file=key_file)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    print(text)
    print(
        f"(Saved to {out}. Review it, then paste it into the chat. doc-NN -> file mapping stays local in {key_file}.)",
        file=sys.stderr,
    )
    return 0


def cmd_schema(args: argparse.Namespace) -> int:
    schema = build_json_schema(
        _load_reference(args),
        constrain_codes=not args.no_constrain_codes,
        include_tax_rate=args.tax_rate_field,
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
        "doctor": lambda: cmd_doctor(args, settings),
        "process": lambda: cmd_process(args, settings),
        "extract": lambda: cmd_extract(args, settings),
        "labels": lambda: cmd_labels(args),
        "evaluate": lambda: cmd_evaluate(args),
        "share-report": lambda: cmd_share_report(args),
        "schema": lambda: cmd_schema(args),
    }
    return commands[args.command]()


if __name__ == "__main__":
    sys.exit(main())
