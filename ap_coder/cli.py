"""Command-line entry point: ``python -m ap_coder <command>``."""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import Settings
from .evaluation import evaluate
from .extraction import DocumentExtractor
from .pipeline import InvoicePipeline, discover_inputs, write_outputs
from .reference_data import load_reference_data
from .schema import build_json_schema

DEFAULT_DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _add_reference_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--coa", default=DEFAULT_DATA_DIR / "chart_of_accounts.csv", help="Chart of Accounts (.csv/.json)")
    p.add_argument(
        "--cost-centers", default=DEFAULT_DATA_DIR / "cost_centers.csv", help="Cost center list (.csv/.json)"
    )
    p.add_argument("--tax-codes", default=DEFAULT_DATA_DIR / "tax_codes.csv", help="Tax codes (.csv/.json); '' to omit")
    p.add_argument("--policy", default=DEFAULT_DATA_DIR / "coding_policy.md", help="Coding policy notes; '' to omit")


def _load_reference(args: argparse.Namespace):
    return load_reference_data(
        args.coa,
        args.cost_centers,
        tax_codes=args.tax_codes or None,
        policy_notes=args.policy or None,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ap_coder", description="Enterprise AP Invoice Coder Engine (PoC)")
    parser.add_argument("--env-file", default=None, help="Path to a .env file (default: ./.env if present)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("process", help="Extract + GL-code invoices (files or directories)")
    p.add_argument("inputs", nargs="+", help="PDF/TIFF/image invoices, or .md/.txt pre-extracted content")
    p.add_argument("-o", "--out", default="output", help="Output directory (default: ./output)")
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
    p.add_argument("--cache-dir", default=".cache/extraction", help="Extraction cache directory ('' to disable)")
    p.add_argument("--workers", type=int, default=1, help="Invoices processed in parallel")
    p.add_argument("--stdout", action="store_true", help="Also print each coded JSON to stdout")
    _add_reference_args(p)

    p = sub.add_parser("extract", help="Run Document Intelligence only and save Markdown + raw JSON")
    p.add_argument("inputs", nargs="+")
    p.add_argument("-o", "--out", default="output")
    p.add_argument("--extraction-model", choices=["prebuilt-layout", "prebuilt-invoice"])
    p.add_argument("--cache-dir", default=".cache/extraction")

    p = sub.add_parser("schema", help="Print the strict JSON Schema sent to Azure OpenAI")
    p.add_argument("--tax-rate-field", action="store_true")
    p.add_argument("--no-constrain-codes", action="store_true", help="Do not embed valid codes as enums")
    _add_reference_args(p)

    p = sub.add_parser("evaluate", help="Score predictions against ground-truth JSON files")
    p.add_argument("--predictions", required=True)
    p.add_argument("--ground-truth", required=True)
    p.add_argument("--target", type=float, default=0.9)
    p.add_argument("--show-mismatches", action="store_true")
    return parser


def cmd_process(args: argparse.Namespace, settings: Settings) -> int:
    settings = settings.with_overrides(
        di_model=args.extraction_model,
        deployment=args.deployment,
        model_name=args.model_name,
        vision=args.vision,
        include_tax_rate=args.tax_rate_field,
    )
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


def cmd_schema(args: argparse.Namespace) -> int:
    schema = build_json_schema(
        _load_reference(args),
        constrain_codes=not args.no_constrain_codes,
        include_tax_rate=args.tax_rate_field,
    )
    print(json.dumps(schema, indent=2))
    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    report = evaluate(args.predictions, args.ground_truth, target=args.target)
    data = report.to_dict()
    if not args.show_mismatches:
        data["mismatches"] = f"{len(report.mismatches)} (use --show-mismatches)"
    print(json.dumps(data, indent=2, default=str))
    return 0 if report.meets_target else 1


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
    if args.command == "process":
        return cmd_process(args, settings)
    if args.command == "extract":
        return cmd_extract(args, settings)
    if args.command == "schema":
        return cmd_schema(args)
    if args.command == "evaluate":
        return cmd_evaluate(args)
    return 2


if __name__ == "__main__":
    sys.exit(main())
