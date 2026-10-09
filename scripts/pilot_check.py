"""Pilot self-check: read and code the sample invoices on this computer, with no AI model and no Azure.

    .venv\\Scripts\\python.exe scripts\\pilot_check.py        (Windows)
    .venv/bin/python scripts/pilot_check.py                   (Mac, Linux)

Each sample in samples/ is read here (PDF text, or local OCR for a scan), checked and coded from the
bundled sample accounts, then compared with its answer in samples/ground_truth/. Nothing is saved to the
AP Coder database and nothing is sent anywhere. Exit code 0 when every sample reads right.
"""

from __future__ import annotations

import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from ap_coder.config import Settings  # noqa: E402
from ap_coder.pipeline import InvoicePipeline  # noqa: E402
from ap_coder.reference_data import load_reference_data  # noqa: E402
from ap_coder.store import Store  # noqa: E402

SAMPLES = ROOT / "samples"
DATA = ROOT / "data"
HEADER = ("vendor_name", "invoice_number", "invoice_date", "supplier_province", "ship_to_province")
AMOUNTS = ("subtotal", "tax_total", "grand_total")


def check(pdf: Path, pipe: InvoicePipeline) -> list[str]:
    truth = json.loads((SAMPLES / "ground_truth" / f"{pdf.stem}.json").read_text(encoding="utf-8"))
    result = pipe.process(pdf)
    if not result.ok:
        return [f"not read: {result.error}"]
    got = result.output
    wrong = [f"{f}: {got[f]!r}, expected {truth[f]!r}" for f in HEADER if got[f] != truth[f]]
    wrong += [f"{f}: {got[f]}, expected {truth[f]}" for f in AMOUNTS if abs(got[f] - truth[f]) > 0.011]
    lines = [round(li["amount"], 2) for li in got["line_items"]]
    if lines != [li["amount"] for li in truth["line_items"]]:
        wrong.append(f"line amounts {lines}")
    taxes = [(t["tax_type"], t["province"], t["rate"]) for t in got["tax_lines"]]
    if taxes != [(t["tax_type"], t["province"], t["rate"]) for t in truth["tax_lines"]]:
        wrong.append(f"taxes {taxes}")
    wrong += [f"error: {i.message}" for i in result.report.issues if i.severity == "error"]
    return wrong


def check_ocr(pdf: Path) -> list[str]:
    """Read one sample as if it were a scan (OCR on its page image), as a scanned invoice would be."""
    from ap_coder.capture import analyze
    from ap_coder.capture.layout import ocr_available

    if not ocr_available():
        return ["OCR is not installed: scanned invoices cannot be read (run the launcher again)"]
    truth = json.loads((SAMPLES / "ground_truth" / f"{pdf.stem}.json").read_text(encoding="utf-8"))
    fields = analyze(pdf, ocr=True).fields
    wrong = []
    if fields["invoice_number"].value != truth["invoice_number"]:
        wrong.append(f"invoice_number: {fields['invoice_number'].value!r}")
    if abs((fields["grand_total"].value or 0) - truth["grand_total"]) > 0.011:
        wrong.append(f"grand_total: {fields['grand_total'].value}")
    return wrong


def main() -> int:
    settings = Settings.from_env()
    settings = replace(
        settings,
        llm=replace(settings.llm, provider="off"),  # no AI model: the local reader and memory only
        document_intelligence=replace(settings.document_intelligence, endpoint=None),
    )
    reference = load_reference_data(
        DATA / "chart_of_accounts.csv",
        DATA / "cost_centers.csv",
        DATA / "tax_gl_mapping.csv",
        DATA / "coding_policy.md",
    )
    pdfs = sorted(SAMPLES.glob("*.pdf"))
    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        pipe = InvoicePipeline(settings, reference, store=Store(Path(tmp) / "check.db"))
        for pdf in pdfs:
            wrong = check(pdf, pipe)
            failed += bool(wrong)
            print(f"{'OK  ' if not wrong else 'FAIL'} {pdf.name}" + "".join(f"\n       {w}" for w in wrong))
    print(f"\n{len(pdfs) - failed} of {len(pdfs)} sample invoices read right on this computer (no AI model, no Azure).")
    scan = SAMPLES / "northwind_ON_HST_NW-2026-0912.pdf"
    ocr_wrong = check_ocr(scan) if scan.exists() else ["sample missing"]
    print(("OK  " if not ocr_wrong else "FAIL") + f" read as a scan with local OCR: {scan.name}"
          + "".join(f"\n       {w}" for w in ocr_wrong))  # fmt: skip
    return 1 if failed or ocr_wrong or not pdfs else 0


if __name__ == "__main__":
    sys.exit(main())
