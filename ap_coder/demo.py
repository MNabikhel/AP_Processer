"""Demo mode: the whole product with the bundled sample invoices, without Azure.

``load_demo`` puts the synthetic sample invoices (``samples/``) into the review queue exactly as if
Azure had read and coded them, through the real validation, tax checks and GL distribution. The
"AI" suggestions are the ground truth with a handful of realistic mistakes (a monitor coded as
office supplies, an annual support plan expensed instead of prepaid, ...), so a reviewer can try
correcting and approving, and watch the learning work. Two invoices are already approved, so the
Learning page has history to show.

Everything it adds is marked ``meta.demo`` and can be removed with ``remove_demo`` without touching
real invoices.
"""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import Settings
from .paths import PROJECT_DIR
from .pipeline import finalise_coding
from .schema import InvoiceCoding
from .store import Store, load_sample_setup

SAMPLES_DIR = PROJECT_DIR / "samples"
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
        [Mistake(3, "predicted_cost_center", "CC200", "Professional fees are charged to Finance.")],
    ),
    DemoInvoice("prairie_SK_GST_PST_PNS-104882", 0.93),
    DemoInvoice("northwind_ON_HST_CN-2026-0047", 0.94),
]  # fmt: skip


def demo_available() -> bool:
    return (SAMPLES_DIR / "ground_truth").is_dir()


def _ground_truth(stem: str) -> dict[str, Any]:
    return json.loads((SAMPLES_DIR / "ground_truth" / f"{stem}.json").read_text(encoding="utf-8"))


def _ai_output(demo: DemoInvoice, truth: dict[str, Any]) -> dict[str, Any]:
    ai = copy.deepcopy(truth)
    ai["confidence_score"] = demo.confidence
    for m in demo.mistakes:
        line = next(li for li in ai["line_items"] if li["line_number"] == m.line_number)
        line[m.field] = m.value
        line["reasoning_justification"] = m.reasoning
    return ai


def is_demo(invoice: dict[str, Any]) -> bool:
    return bool((invoice.get("meta") or {}).get("demo"))


def load_demo(store: Store, settings: Settings | None = None) -> dict[str, int]:
    """Add the demo invoices (once). Returns how many were added to the queue and pre-approved."""
    if not demo_available():
        raise FileNotFoundError(f"sample invoices not found in {SAMPLES_DIR}")
    settings = settings or Settings()
    if not store.list_accounts("gl_accounts"):
        load_sample_setup(store)
    reference = store.reference_data()
    existing = {Path(i["source_path"]).name for i in store.list_invoices_full() if is_demo(i)}
    added = approved = 0
    for demo in DEMO_INVOICES:
        pdf = SAMPLES_DIR / f"{demo.stem}.pdf"
        if pdf.name in existing or not pdf.exists():
            continue
        truth = _ground_truth(demo.stem)
        ai = _ai_output(demo, truth)
        coding = InvoiceCoding.model_validate({k: v for k, v in ai.items() if k != "gl_distribution"})
        output, report = finalise_coding(coding, reference, settings, store=store)
        md_path = SAMPLES_DIR / f"{demo.stem}.md"
        invoice_id = store.add_invoice(
            pdf,
            output,
            report.to_dict(),
            extraction_md=md_path.read_text(encoding="utf-8") if md_path.exists() else "",
            meta={"demo": True, "source": str(pdf), "status": "ok", "extraction": {"model_id": "demo"}},
        )
        added += 1
        if demo.approved:
            final_coding = InvoiceCoding.model_validate(truth)
            final, _ = finalise_coding(final_coding, reference, settings, store=store, exclude_invoice_id=invoice_id)
            store.approve_invoice(invoice_id, final, DEMO_REVIEWER)
            approved += 1
    return {"added": added, "approved": approved, "to_review": added - approved}


def remove_demo(store: Store) -> int:
    """Delete the demo invoices and everything learned from them; real invoices are untouched."""
    ids = [i["id"] for i in store.list_invoices_full() if is_demo(i)]
    for invoice_id in ids:
        store.delete_invoice(invoice_id, forget_lessons=True)
    return len(ids)
