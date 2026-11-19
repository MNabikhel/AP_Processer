"""How fast AP Coder is for a group of many JD Edwards companies (D-267): ``python -m ap_coder.bench companies``.

The made-up group (``jde_group``) at ``Scale`` x ``--share`` (default 0.1 of 20 companies, 50,000 chart rows, 20,000
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
import statistics
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
    chart, units = jde_group.chart_rows(scale)
    seconds["business units"], _ = _timed(lambda: store.import_business_units(units))
    seconds["chart"], _ = _timed(lambda: store.import_chart(chart))
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
                                    "whole check": []}  # fmt: skip
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
    return seconds


def run(share: float, invoices: int, workbook_share: float, out: str) -> dict[str, Any]:
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
    report["workbook"] = {name: round(s, 2) for name, s in workbook(full.scaled(workbook_share)).items()}
    report["workbook_share"] = workbook_share
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
    return "\n".join(lines) + "\n"


def add_parser(sub: Any) -> argparse.ArgumentParser:
    """``companies``' command line, under ``python -m ap_coder.bench``."""
    p = sub.add_parser("companies", help="many JD Edwards companies at once: each load and each invoice's checks, "
                       "timed (D-267)")  # fmt: skip
    p.add_argument(
        "--share",
        type=float,
        default=0.1,
        help="of the full size (20 companies, 50,000 chart rows, "
        "20,000 suppliers, 1,000,000 ledger rows, 3,000,000 past-coding lines, 100,000 PO lines)",
    )
    p.add_argument("--invoices", type=int, default=30, help="invoices whose checks are timed (default 30)")
    p.add_argument("--workbook-share", type=float, default=0.05, help="the master workbook's size (default 0.05)")
    p.add_argument("--out", default="bench_companies", help="output folder (default bench_companies)")
    p.set_defaults(run=main)
    return p


def main(a: argparse.Namespace) -> int:
    report = run(a.share, a.invoices, a.workbook_share, a.out)
    print(_markdown(report))
    return 0
