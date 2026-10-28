"""Demo mode: the whole product with the bundled sample invoices, without Azure.

``load_demo`` puts the synthetic sample invoices (``samples/``) into the review queue exactly as if
Azure had read and coded them, through the real validation, tax checks and GL distribution. The
"AI" suggestions are the ground truth with a handful of realistic mistakes (a monitor coded as
office supplies, an annual support plan expensed instead of prepaid, ...), so a reviewer can try
correcting and approving, and watch the learning work. Two invoices are already approved, so the
Learning page has history to show.

Everything it adds is marked ``meta.demo`` and can be removed with ``remove_demo`` without touching
real invoices. If no purchase orders are loaded, the sample POs that go with the invoices are loaded too
(and removed with the demo); so are the sample GL accounts, cost centers, tax setup and policy on a database
without GL accounts (put back as they were when the demo is removed, unless the user changed them meanwhile).
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..ai import verifier
from ..ai.pipeline import finalise_coding
from ..capture.workflow import capture_invoice
from ..core.config import Settings
from ..core.paths import PROJECT_DIR
from ..core.schema import InvoiceCoding
from ..core.store import Store, load_sample_purchase_orders, load_sample_setup, load_sample_vendor_master

SAMPLES_DIR = PROJECT_DIR / "samples"
DEMO_POS_SETTING = "demo_purchase_orders"  # PO keys the demo loaded, removed with it
DEMO_VENDORS_SETTING = "demo_vendor_master"  # vendor keys of the sample vendor master the demo loaded
DEMO_SETUP_SETTING = "demo_reference_setup"  # the reference data before and after the demo loaded the sample setup
DEMO_REVIEWER = "Demo reviewer"


@dataclass
class Mistake:
    line_number: int
    field: str
    value: str
    reasoning: str


@dataclass
class DemoInvoice:
    stem: str
    confidence: float
    mistakes: list[Mistake] = field(default_factory=list)
    approved: bool = False  # already reviewed "last week", so the Learning page has history


DEMO_INVOICES = [
    DemoInvoice("northwind_ON_HST_NW-2026-0912", 0.96, approved=True),
    DemoInvoice(
        "chinook_AB_GST_CCO-26-10418", 0.88,
        [Mistake(4, "predicted_gl_code", "6000", "Printing and binding looks like an office supplies purchase.")],
        approved=True,
    ),
    DemoInvoice(
        "pacific_BC_GST_PST_PO-77120", 0.81,
        [Mistake(2, "predicted_gl_code", "6000", "Bought from an office supply vendor alongside toner and paper.")],
    ),
    DemoInvoice("laurentides_QC_TPS_TVQ_SIL-4471", 0.95),
    DemoInvoice(
        "redriver_MB_GST_RST_RRO-55821", 0.79,
        [Mistake(1, "predicted_gl_code", "6900", "Furniture item; no specific expense account matched.")],
    ),
    DemoInvoice(
        "montroyal_QC_TPS_TVQ_ACMR-2026-1187", 0.86,
        [Mistake(6, "predicted_gl_code", "6400", "Catering at the trade show, part of the marketing event.")],
    ),
    DemoInvoice(
        "cascade_US_SalesTax_INV-30981", 0.84,
        [Mistake(2, "predicted_gl_code", "6020", "Support for a software platform, treated as a subscription.")],
    ),
    DemoInvoice(
        "harbourview_NS_HST_HPS-2026-0347", 0.9,
        [
            Mistake(3, "predicted_gl_code", "6110", "Contract review reads like consulting work."),
            Mistake(4, "predicted_gl_code", "UNASSIGNED", "Disbursements could be several accounts; not sure."),
        ],
    ),
    DemoInvoice("prairie_SK_GST_PST_PNS-104882", 0.93),
    DemoInvoice("northwind_ON_HST_CN-2026-0047", 0.94),
]  # fmt: skip


def demo_available() -> bool:
    return (SAMPLES_DIR / "ground_truth").is_dir()


def _ground_truth(stem: str) -> dict[str, Any]:
    return json.loads((SAMPLES_DIR / "ground_truth" / f"{stem}.json").read_text(encoding="utf-8"))


def _without_cost_centers(truth: dict[str, Any]) -> dict[str, Any]:
    """The ground truth without its cost centers, for a database with no cost center list (the pilot's, D-53): a
    tester sees none, so none may be kept, exported or learned (B-528)."""
    truth = copy.deepcopy(truth)
    for li in truth.get("line_items", []):
        li["predicted_cost_center"] = ""
    for row in truth.get("gl_distribution") or []:
        if isinstance(row, dict) and "cost_center" in row:
            row["cost_center"] = ""
    return truth


def _ai_output(demo: DemoInvoice, truth: dict[str, Any]) -> dict[str, Any]:
    ai = copy.deepcopy(truth)
    ai["confidence_score"] = demo.confidence
    for m in demo.mistakes:
        line = next(li for li in ai["line_items"] if li["line_number"] == m.line_number)
        line[m.field] = m.value
        line["reasoning_justification"] = m.reasoning
    return ai


def _demo_meta(pdf: Path, truth: dict[str, Any]) -> dict[str, Any]:
    """Processing metadata like a real run (typical token use for an invoice this size), so the Insights
    page has realistic cost and timing figures in the demo."""
    pages = 2 if "montroyal" in pdf.stem else 1
    lines = len(truth.get("line_items", []))
    return {
        "demo": True, "source": str(pdf), "status": "ok",
        "extraction": {"model_id": "demo", "page_count": pages, "table_count": 1},
        "inference": {"model": "demo", "attempts": 1, "usage": {
            "prompt_tokens": 3600 + 500 * pages, "cached_prompt_tokens": 2304,
            "completion_tokens": 450 + 160 * lines, "total_tokens": 4050 + 500 * pages + 160 * lines,
        }},
        "timings_seconds": {"extraction": 3.1 + 1.4 * pages, "inference": 4.0 + 0.6 * lines},
    }  # fmt: skip


def is_demo(invoice: dict[str, Any]) -> bool:
    return bool((invoice.get("meta") or {}).get("demo"))


def _setup(store: Store) -> dict[str, Any]:
    """The reference data the sample setup fills in, as the demo compares and puts it back."""
    return {
        "gl_accounts": store.list_accounts("gl_accounts"),
        "cost_centers": store.list_accounts("cost_centers"),
        "tax": {t: [x.treatment, x.gl_code] for t, x in store.tax_treatments().items()},
        "policy_notes": store.get_setting("policy_notes"),
    }


def _put_back_setup(store: Store) -> None:
    """Each part of the sample setup the demo loaded (GL accounts, cost centers, tax setup, policy) as it was before,
    when it is still as the demo left it: a part the user changed meanwhile is theirs now and stays."""
    saved = store.get_json(DEMO_SETUP_SETTING, {})
    before, after = saved.get("before"), saved.get("after")
    if not before or not after:
        return
    now = _setup(store)
    for table in ("gl_accounts", "cost_centers"):
        if now[table] == after[table]:
            store.save_accounts(table, before[table])
    if now["tax"] == after["tax"]:
        for tax_type, (treatment, gl_code) in before["tax"].items():
            store.set_tax_treatment(tax_type, treatment, gl_code)
    if now["policy_notes"] == after["policy_notes"]:
        store.set_setting("policy_notes", before["policy_notes"])
    store.set_setting(DEMO_SETUP_SETTING, "")


def load_demo(store: Store, settings: Settings | None = None) -> dict[str, int]:
    """Add the demo invoices (once). Returns how many were added to the queue and pre-approved."""
    if not demo_available():
        raise FileNotFoundError(f"sample invoices not found in {SAMPLES_DIR}")
    settings = settings or Settings()
    if not store.list_accounts("gl_accounts"):
        before = _setup(store)
        load_sample_setup(store, cost_centers=False)  # as "Use the sample GL accounts": GL accounts only (D-53)
        store.set_setting(DEMO_SETUP_SETTING, json.dumps({"before": before, "after": _setup(store)}))
    real_invoices = len(store.list_invoices()) - store.demo_count()
    if not store.has_vendor_master() and not real_invoices:
        # A sample vendor master (Cascade left out on purpose). Only on a database without real invoices (they
        # would all be "not in the vendor master"), and only adding vendors: the user's own vendor records stay.
        store.set_setting(DEMO_VENDORS_SETTING, json.dumps(load_sample_vendor_master(store, only_new=True)))
    if not store.has_purchase_orders():  # sample POs, so the PO matching has something to show
        store.set_setting(DEMO_POS_SETTING, json.dumps(load_sample_purchase_orders(store)))
    reference = store.reference_data()
    existing = {Path(i["source_path"]).name for i in store.list_invoices_full() if is_demo(i)}
    added = approved = 0
    for demo in DEMO_INVOICES:
        pdf = SAMPLES_DIR / f"{demo.stem}.pdf"
        if pdf.name in existing or not pdf.exists():
            continue
        truth = _ground_truth(demo.stem)
        if reference.cost_centers is None:
            truth = _without_cost_centers(truth)
        ai = _ai_output(demo, truth)
        coding = InvoiceCoding.model_validate({k: v for k, v in ai.items() if k != "gl_distribution"})
        output, report = finalise_coding(coding, reference, settings, store=store)
        md_path = SAMPLES_DIR / f"{demo.stem}.md"
        invoice_id = store.add_invoice(
            pdf,
            output,
            report.to_dict(),
            extraction_md=md_path.read_text(encoding="utf-8") if md_path.exists() else "",
            meta=_demo_meta(pdf, truth),
        )
        capture, _, _ = capture_invoice(pdf, output, store=store)
        if capture is not None:
            store.save_capture(invoice_id, capture.to_dict())
        added += 1
        if demo.approved:
            final_coding = InvoiceCoding.model_validate(truth)
            final, _ = finalise_coding(final_coding, reference, settings, store=store, exclude_invoice_id=invoice_id)
            store.approve_invoice(invoice_id, final, DEMO_REVIEWER)
            _record_supplier_outcomes(store, invoice_id, output, final)
            approved += 1
        elif verifier.load_settings(store).on:  # the AI reviewer checks the demo invoices left to review too
            store.queue_verification(invoice_id, "demo")
    return {"added": added, "approved": approved, "to_review": added - approved}


def _record_supplier_outcomes(store: Store, invoice_id: int, proposed: dict[str, Any], final: dict[str, Any]) -> None:
    """The header fields of a pre-approved demo invoice count towards its supplier's accuracy (Learning page)."""
    from ..capture.supplier import outcome_rows

    name = final.get("vendor_name") or ""
    key = store.supplier_key_for(name, final.get("gst_hst_registration_number"))
    store.record_outcomes(key, invoice_id, outcome_rows(proposed, final), display_name=name)


def remove_demo(store: Store) -> int:
    """Delete the demo invoices and everything learned from them; real invoices are untouched."""
    ids = [i["id"] for i in store.list_invoices_full() if is_demo(i)]
    batches = {r["export_batch"] for r in store.invoice_columns(("id", "export_batch"), ids=ids) if r["export_batch"]}
    suppliers = store.supplier_keys_for_invoices(ids)
    for invoice_id in ids:
        store.delete_invoice(invoice_id, forget_lessons=True)
    store.delete_empty_batches(batches)  # export batches that held only demo invoices
    store.prune_supplier_profiles(suppliers)  # supplier profiles that only the demo invoices made
    vendor_keys = store.get_json(DEMO_VENDORS_SETTING, [])
    if vendor_keys:
        store.delete_vendors(vendor_keys)
        store.set_setting(DEMO_VENDORS_SETTING, "")
    po_keys = store.get_json(DEMO_POS_SETTING, [])
    if po_keys:
        store.delete_purchase_orders(po_keys)
        store.set_setting(DEMO_POS_SETTING, "")
    _put_back_setup(store)
    return len(ids)
