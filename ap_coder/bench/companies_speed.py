"""How fast AP Coder is for a group of many JD Edwards companies (D-267): ``python -m ap_coder.bench companies``.

The made-up group (``jde_group``) at ``Scale`` x ``--share`` (default 0.1 of 20 companies, 3,000,000 chart rows, 20,000
suppliers, 1,000,000 ledger rows, 3,000,000 past-coding lines and 100,000 open PO lines) is loaded into a new database
through each card's loader, each load timed; then, for invoices of suppliers billing one or two companies and quoting
PO numbers several companies share, the checks an invoice's check runs are timed one by one (which company, the
suggestions, the PO match, the duplicate check) and the whole check (``finalise_coding``), median and slowest of
``--invoices``. The master workbook's survey and Load all are timed at ``--workbook-share`` (its sheets must fit an
upload). Everything is written to ``companies_speed.json`` and ``companies_speed.md`` in ``--out``; the loads'
seconds are also given extrapolated to the full size (linear: each loader writes its rows once)."""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import jde_group
from .jde_group import Scale


def _timed(work: Callable[[], Any]) -> tuple[float, Any]:
    started = time.perf_counter()
    out = work()
    return time.perf_counter() - started, out


def _supplier_rows(scale: Scale) -> list[dict[str, Any]]:
    return [{"vendor_name": r["vendor_name"], "erp_id": r["erp_id"], "gst": r["gst_number"], "qst": "", "pst": "",
             "terms": None, "status": "active", "status_given": False, "default_gl": None, "hold": None,
             "province": ""} for r in jde_group.supplier_rows(scale)]  # fmt: skip


def load_all(store: Any, scale: Scale) -> dict[str, float]:
    """Each report loaded into ``store`` through its loader: {report: seconds}."""
    from ..coding import companies
    from ..core.headers import match_columns
    from ..ops import journal_cmd

    seconds: dict[str, float] = {}
    rows = jde_group.companies_rows(jde_group.group(scale.companies))
    records = [dict(zip(rows[0], row, strict=True)) for row in rows[1:]]
    seconds["companies"], _ = _timed(lambda: store.set_setting(companies.COMPANIES_SETTING, json.dumps(
        companies.company_table(records, match_columns(rows[0], companies.COMPANY_ALIASES))[0])))  # fmt: skip
    from ..ops import chart_cmd

    units = jde_group.unit_rows(scale)
    seconds["business units"], _ = _timed(lambda: store.import_business_units(units))
    path = Path(store.path).parent / "chart.csv"
    jde_group.write_chart_csv(path, scale)  # (report 1 as one CSV, streamed from disk: D-267)
    seconds["chart (CSV, streamed)"], _ = _timed(lambda: chart_cmd.import_files(store, path))
    seconds["reference data (the chart's offered accounts)"], _ = _timed(store.reference_data)
    suppliers = _supplier_rows(scale)
    seconds["suppliers"], _ = _timed(lambda: store.import_vendor_master(suppliers))
    ledger = list(jde_group.ledger_rows(scale))
    seconds["ledger (vouchers entered)"], _ = _timed(lambda: store.import_erp_vouchers(ledger))
    data = jde_group.journal_csv(scale)
    seconds["past coding (G/L lines)"], said = _timed(lambda: journal_cmd.load(store, data, "gl_lines.csv"))
    if said[0] != journal_cmd.DONE:
        raise RuntimeError(f"the past coding was not loaded: {said[1][-1]}")
    pos = jde_group.po_rows(scale)
    seconds["open PO lines"], _ = _timed(lambda: store.import_purchase_orders(pos))
    return seconds


def _invoices(scale: Scale, n: int) -> list[dict[str, Any]]:
    """``n`` invoices, each of the supplier of an order whose number another company uses too (every tenth order's
    number is used again: ``jde_group.po_rows``), quoting that number."""
    suppliers: dict[str, list[int]] = {}
    for row in jde_group.po_rows(scale):
        suppliers.setdefault(row["po_number"], []).append(int(row["supplier_number"]) - 10000)
    shared = [number for number, of in suppliers.items() if len(of) > 5] or list(suppliers)
    out = []
    for k in range(n):
        number = shared[k % len(shared)]
        supplier = suppliers[number][0]
        place = jde_group._PLACES[supplier % len(jde_group._PLACES)]
        out.append({
            "vendor_name": f"Supplier {supplier:05d} {place} Ltd", "invoice_number": f"INV-{k:07d}",
            "invoice_date": "2026-03-02", "po_number": number, "currency": "CAD", "subtotal": 100.0,
            "tax_lines": [{"tax_type": "GST", "province": "AB", "rate": 0.05, "taxable_amount": 100.0,
                           "tax_amount": 5.0}],
            "tax_total": 5.0, "grand_total": 105.0, "confidence_score": 0.9, "gst_hst_registration_number": "",
            "line_items": [{"line_number": 1, "description": "Material 1 for job", "quantity": 10, "unit_price": 10.0,
                            "amount": 100.0, "predicted_gl_code": "", "taxes_applied": ["GST"]}],
        })  # fmt: skip
    return out


def per_invoice(store: Any, scale: Scale, n: int) -> dict[str, dict[str, float]]:
    """The checks of ``n`` invoices, each timed: {check: {"median", "slowest"}} in milliseconds."""
    from ..ai.offline_coder import supplier_evidence
    from ..ai.pipeline import finalise_coding
    from ..coding import companies
    from ..coding.po import match_invoice
    from ..core.config import Settings
    from ..core.schema import InvoiceCoding

    reference = store.reference_data()
    settings = Settings()
    took: dict[str, list[float]] = {"which company": [], "suggestions": [], "PO match": [], "duplicate check": [],
                                    "a line's account check": [], "an account search": [],
                                    "whole check": []}  # fmt: skip
    pairs = reference.pairs
    units = jde_group.unit_rows(scale)
    docs = _invoices(scale, n + 1)
    for at, doc in enumerate(docs):
        coding = InvoiceCoding.model_validate(doc)
        text = f"{doc['vendor_name']}\nBill to: Maple Ridge Constructors Ltd.\n"
        key = store.vendor_key_for(coding.vendor_name, "")
        steps = {
            "which company": lambda c=coding, t=text: companies.decide(store, c, t),
            "suggestions": lambda c=coding, k=key: supplier_evidence(store, c.vendor_name, k, reference, "00220"),
            "PO match": lambda c=coding: match_invoice(c.model_copy(update={"company": "00220"}), store),
            "duplicate check": lambda c=coding: store.in_erp(c.vendor_name, c.invoice_number, c.grand_total),
            "a line's account check": lambda a=at: pairs.status(
                units[(a * 7919) % len(units)].code, jde_group.OBJECTS[a % len(jde_group.OBJECTS)]
            ),
            "an account search": lambda a=at: pairs.search(
                f"cost {jde_group.OBJECTS[a % len(jde_group.OBJECTS)]}", pairs.units_of(units[a % len(units)].company)
            ),
            "whole check": lambda c=coding: finalise_coding(c.model_copy(), reference, settings, store=store),
        }
        for name, step in steps.items():
            seconds, _ = _timed(step)
            if at:  # (the first invoice warms the caches each check keeps)
                took[name].append(seconds * 1000)
    return {name: {"median": round(statistics.median(ms), 2), "slowest": round(max(ms), 2)}
            for name, ms in took.items() if ms}  # fmt: skip


def workbook(scale: Scale) -> dict[str, float]:
    """The master workbook of the group at ``scale`` (companies, chart, business units, suppliers, PO lines): its
    survey and each sheet's load as Load all loads it, {step: seconds}."""
    import io

    from openpyxl import Workbook

    from ..core.store import Store
    from ..web.pages import jde_workbook

    book = Workbook(write_only=True)
    sheets = {
        "Companies": jde_group.companies_rows(jde_group.group(scale.companies)),
        "BUs": [["Job Number", "Job Name", "Co", "BU Ty", "Job Sts"]],
        "Accounts": [["Business Unit", "Object Account", "Subsidiary", "Account Description", "L D", "P E"]],
        "Suppliers": [["Address Number", "Alpha Name", "Tax ID", "Hold Payment Code"]],
        "Open POs": [["Order Number", "Or Ty", "Order Co", "Line Number", "Supplier", "Line Description",
                      "Quantity Ordered", "Unit Cost", "Extended Price", "Business Unit", "Object Account"]],
    }  # fmt: skip
    chart, units = jde_group.chart_rows(scale)
    sheets["BUs"] += [[u.code, u.name, u.company, u.type, ""] for u in units]
    sheets["Accounts"] += [[r.bu, r.object, r.subsidiary, r.description, str(r.lod), r.posting_edit] for r in chart]
    sheets["Suppliers"] += [
        [r["erp_id"], r["vendor_name"], r["gst_number"], ""] for r in jde_group.supplier_rows(scale)
    ]
    sheets["Open POs"] += [[r["po_number"], r["order_type"], r["order_company"], f"{r['line_number']}.000",
                            r["supplier_number"], r["description"], r["quantity"], r["unit_price"], r["amount"],
                            r["cost_center"], r["gl_code"]] for r in jde_group.po_rows(scale)]  # fmt: skip
    for title, rows in sheets.items():
        sheet = book.create_sheet(title)
        for row in rows:
            sheet.append(row)
    out = io.BytesIO()
    book.save(out)
    content = out.getvalue()
    seconds: dict[str, float] = {"workbook MB": round(len(content) / 1e6, 1)}
    seconds["survey"], surveyed = _timed(lambda: jde_workbook.survey(content, "group.xlsx"))
    with tempfile.TemporaryDirectory() as folder:
        store = Store(Path(folder) / "ap_coder.db")
        for key in jde_workbook.ORDER:
            for sheet in [s for s in surveyed["sheets"] if s["key"] == key]:
                if key == "purchase_orders":
                    frame = jde_workbook._frame(content, "group.xlsx", sheet)
                    from ..web.pages import jde_files

                    seconds[f"load {sheet['title']}"], _ = _timed(lambda f=frame: jde_files.load_orders(store, [f]))
                else:
                    seconds[f"load {sheet['title']}"], _ = _timed(
                        lambda k=key, s=sheet: jde_workbook._load_sheet(store, k, content, "group.xlsx", s)
                    )
        store.forget_reference()  # (its chart's connections closed: Windows removes no folder with the file open)
    return seconds


def _peak_mb_of_children() -> float | None:
    """The largest resident memory of a child process so far, in MB (None where the platform does not say)."""
    try:
        import resource
    except ImportError:  # (Windows)
        return None
    peak = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return round(peak / (1024 * 1024 if sys.platform == "darwin" else 1024), 1)


def _median_us(work: Callable[[int], Any], times: int) -> float:
    took = []
    for k in range(times):
        started = time.perf_counter()
        work(k)
        took.append(time.perf_counter() - started)
    return round(statistics.median(took) * 1e6, 1)


def full_chart(rows: int, companies: int = 20) -> dict[str, Any]:
    """The group's whole chart unfiltered (D-272: every company's accounts, about half not posted to, some of a code
    not decided) as ReportsNow's tab-delimited text: loaded by ``python -m ap_coder chart import --chart`` in a process
    of its own (its time and peak memory), then refreshed in this one, staged and swapped apart, and asked what the
    pages and the checks ask of it. {step: value}."""
    from ..coding import companies as companies_mod
    from ..core import chart_file
    from ..core.store import Store
    from ..ops import chart_cmd
    from ..web.pages import settings_jde

    out: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "f0901_all_companies.txt"
        out["write the file, s"], written = _timed(lambda: jde_group.write_full_chart(path, rows, companies))
        out["rows"], out["file MB"] = written, round(path.stat().st_size / 1e6, 1)
        db = Path(folder) / "ap_coder.db"
        store = Store(db)
        table = {code: {"name": name, "currency": cur, "country": country}
                 for code, name, cur, country in jde_group.group(companies)}  # fmt: skip
        store.set_setting(companies_mod.COMPANIES_SETTING, json.dumps(table))
        out["business units"] = len(units := jde_group.full_unit_rows(rows, companies))
        out["load the business units, s"], _ = _timed(lambda: store.import_business_units(units))
        env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2])}
        command = [sys.executable, "-m", "ap_coder", "--db", str(db), "chart", "import", "--chart", str(path)]
        out["load (the command line, a process of its own), s"], done = _timed(
            lambda: subprocess.run(command, capture_output=True, text=True, env=env, check=False))  # fmt: skip
        if done.returncode:
            raise RuntimeError(f"the chart was not loaded: {done.stderr[-800:]}")
        out["load, peak memory of its process, MB"] = _peak_mb_of_children()
        out["what the load said"] = [line for line in done.stdout.splitlines() if "rows read..." not in line][:14]
        # A refresh of the same file in this process: read and staged (no write lock), then swapped in.
        with store.session():
            read, given = chart_file.stream_chart_text(path)
            out["refresh: read and staged, s"], staged = _timed(lambda: store.stage_chart([(path.name, given)]))
            out["refresh: the import's summary, s"], said = _timed(
                lambda: chart_cmd.read_lines([(path.name, read)], staged, store.chart_settings()))  # fmt: skip
            out["refresh: swapped in (one transaction), s"], _ = _timed(lambda: store.swap_chart(staged, summary=said))
        # A refresh that changes something: a company's descriptions, and a quarter of another's accounts gone.
        changed = Path(folder) / "f0901_changed.txt"
        codes = [c[0] for c in jde_group.group(companies)]
        with open(path, encoding="utf-8") as source, open(changed, "w", encoding="utf-8", newline="") as target:
            for n, line in enumerate(source):
                if not (line.startswith(f"{codes[1]}\t") and n % 4 == 0):
                    target.write(line.replace("material", "materials") if line.startswith(f"{codes[0]}\t") else line)
        with store.session():
            given = chart_file.stream_chart_text(changed)[1]
            out["refresh with changes: read and staged, s"], staged = _timed(
                lambda: store.stage_chart([(changed.name, given)]))  # fmt: skip
            out["refresh with changes: swapped in (one transaction), s"], result = _timed(
                lambda: store.swap_chart(staged))  # fmt: skip
        out["refresh with changes: changed, removed"] = (result["updated"], result["removed"])
        out["the chart's reference data (offered codes and units), s"], reference = _timed(store.reference_data)
        pairs = reference.pairs
        out["what the chart holds (the JD Edwards files card), s"], _ = _timed(lambda: chart_cmd.tally_lines(store))
        out["the Accounts AP posts to card, s"], _ = _timed(lambda: settings_jde.posting_context(store))
        by_unit = [u.code for u in units]
        objects = jde_group.OBJECTS
        out["a line's account check, µs (median)"] = _median_us(
            lambda k: pairs.status(by_unit[(k * 7919) % len(by_unit)], objects[k % len(objects)]), 2000
        )
        out["an account search, ms (median)"] = round(_median_us(
            lambda k: pairs.search(f"cost {objects[k % len(objects)]}"), 30) / 1000, 1)  # fmt: skip
        one = pairs.units_of(units[0].company)
        out["an account search within one company, ms (median)"] = round(_median_us(
            lambda k: pairs.search(f"{objects[k % len(objects)]} labour", one), 30) / 1000, 1)  # fmt: skip
        out["a search for words found nowhere, ms"] = round(
            _median_us(lambda k: pairs.search("qqqq zzzz"), 5) / 1000, 1
        )
        out["database MB"] = round(db.stat().st_size / 1e6, 1)
        pairs.close()  # (Windows removes no folder with the database open: WinError 32, B-2653)
        store.forget_reference()
    return out


def run(share: float, invoices: int, workbook_share: float, out: str, full_rows: int = 0) -> dict[str, Any]:
    from ..core.store import Store

    full = Scale()
    scale = full.scaled(share)
    report: dict[str, Any] = {"scale": vars(scale), "share": share, "full": vars(full)}
    with tempfile.TemporaryDirectory() as folder:
        store = Store(Path(folder) / "ap_coder.db")
        loads = load_all(store, scale)
        report["loads"] = {name: round(s, 2) for name, s in loads.items()}
        report["loads_at_full_size"] = {name: round(s / share, 1) if name != "companies" else round(s, 2)
                                        for name, s in loads.items()}  # fmt: skip
        report["per_invoice_ms"] = per_invoice(store, scale, invoices)
        store.forget_reference()  # (its chart's connections closed: Windows removes no folder with the file open)
    report["workbook"] = {name: round(s, 2) for name, s in workbook(full.scaled(workbook_share)).items()}
    report["workbook_share"] = workbook_share
    if full_rows:
        report["full_chart"] = full_chart(full_rows, full.companies)
    folder_out = Path(out)
    folder_out.mkdir(parents=True, exist_ok=True)
    (folder_out / "companies_speed.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (folder_out / "companies_speed.md").write_text(_markdown(report), encoding="utf-8")
    return report


def _markdown(report: dict[str, Any]) -> str:
    s = report["scale"]
    lines = ["# Many companies: speed (D-267)", "",
             f"{s['companies']} companies, {s['chart_rows']:,} chart rows, {s['suppliers']:,} suppliers, "
             f"{s['ledger_rows']:,} ledger rows, {s['journal_lines']:,} past-coding lines, {s['po_lines']:,} open PO "
             f"lines ({report['share']:g} of the full size).", "", "| Load | seconds | at the full size (s) |",
             "|---|---:|---:|"]  # fmt: skip
    lines += [f"| {name} | {sec} | {report['loads_at_full_size'][name]} |" for name, sec in report["loads"].items()]
    lines += ["", "| Each invoice | median (ms) | slowest (ms) |", "|---|---:|---:|"]
    lines += [f"| {name} | {t['median']} | {t['slowest']} |" for name, t in report["per_invoice_ms"].items()]
    lines += ["", f"Master workbook at {report['workbook_share']:g} of the full size:", ""]
    lines += [f"- {name}: {value}" for name, value in report["workbook"].items()]
    if report.get("full_chart"):
        lines += ["", "The whole chart unfiltered, as tab-delimited text (D-272):", ""]
        lines += [f"- {name}: {value}" for name, value in report["full_chart"].items() if name != "what the load said"]
        lines += [
            "",
            "What its load said:",
            "",
            *(f"    {line}" for line in report["full_chart"]["what the load said"]),
        ]
    return "\n".join(lines) + "\n"


def add_parser(sub: Any) -> argparse.ArgumentParser:
    """``companies``' command line, under ``python -m ap_coder.bench``."""
    p = sub.add_parser("companies", help="many JD Edwards companies at once: each load and each invoice's checks, "
                       "timed (D-267)")  # fmt: skip
    p.add_argument(
        "--share",
        type=float,
        default=0.1,
        help="of the full size (20 companies, 3,000,000 chart rows, "
        "20,000 suppliers, 1,000,000 ledger rows, 3,000,000 past-coding lines, 100,000 PO lines)",
    )
    p.add_argument("--invoices", type=int, default=30, help="invoices whose checks are timed (default 30)")
    p.add_argument("--workbook-share", type=float, default=0.05, help="the master workbook's size (default 0.05)")
    p.add_argument("--out", default="bench_companies", help="output folder (default bench_companies)")
    p.add_argument("--full-chart", type=int, default=0, metavar="ROWS",
                   help="also the whole chart unfiltered as tab-delimited text, of ROWS rows (3500000: the pilot "
                        "tester's), loaded by the command line and asked (D-272)")  # fmt: skip
    p.set_defaults(run=main)
    return p


def main(a: argparse.Namespace) -> int:
    report = run(a.share, a.invoices, a.workbook_share, a.out, a.full_chart)
    print(_markdown(report))
    return 0
