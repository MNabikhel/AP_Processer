"""Training data from AP's approvals: each approved invoice's pages and what AP approved, as one local ZIP.

Every invoice AP approves is a page with checked answers: what a vision model (a page reader such as
OvisOCR2, or Qwen 3.5) should read on it. ``export_training_set`` writes them in the layouts fine-tuning
tools start from:

* ``images/<invoice id>-p<page>.png``: the pages (up to five), as a page reader sees them;
* ``fields.jsonl``: one row per invoice: its images, the header fields, tax lines and line items AP
  approved, and which of AP Coder's readers read each approved value;
* ``chat.jsonl``: the same as chat fine-tuning rows (the pages and an instruction, then the approved JSON);
* ``README.txt``: what each file is and how it can be used.

Only invoices a person checked go in: never a demo invoice (made up), one approved without a person, or one
bulk-approved unchanged without anyone opening it (nobody checked its values on the page). The ZIP is
written where it is asked to be and goes nowhere else.
"""

from __future__ import annotations

import datetime as dt
import io
import json
import logging
import zipfile
from pathlib import Path
from typing import IO, Any

from .imaging import render_page_images
from .store import APPROVED, Store

log = logging.getLogger(__name__)

MAX_PAGES = 5
MAX_SIDE = 2048  # the long side of a page image at most, in pixels (what page readers read at)
INSTRUCTION = "Read this invoice's header fields, tax lines and line items as JSON."
# The header fields a page reader learns to read: printed on the invoice (the provinces AP Coder works out,
# and the bank account, are left out).
HEADER_FIELDS = (
    "vendor_name", "invoice_number", "invoice_date", "due_date", "po_number", "payment_terms", "currency",
    "gst_hst_registration_number", "qst_registration_number", "original_invoice_number", "subtotal", "tax_total",
    "grand_total",
)  # fmt: skip


def _since(since: str | dt.date | None) -> str:
    """'' or the date as YYYY-MM-DD (ValueError for anything else)."""
    if not since:
        return ""
    if isinstance(since, dt.date):
        return since.isoformat()[:10]
    return dt.date.fromisoformat(str(since).strip()[:10]).isoformat()


def training_invoices(store: Store, since: str | dt.date | None = None) -> dict[str, Any]:
    """{"invoices": the approved invoices a training set is made from (oldest first), "demo": demo invoices
    left out, "unreviewed": invoices approved without a person, left out, "bulk": invoices bulk-approved
    unchanged without anyone opening them (their latest approval says so), left out}. ``since``: only invoices
    approved on or after this date (YYYY-MM-DD)."""
    from .capture.workflow import AUTONOMOUS_REVIEWER

    start = _since(since)
    rows = store.invoice_columns(
        ("id", "file_name", "source_path", "reviewer", "reviewed_at", "second_reviewed_at", "final_output", "meta"),
        status=APPROVED,
    )
    # The latest approval of each invoice (events come newest first): a bulk approval took the AI's values as
    # they were, without anyone looking at the page.
    bulk_ids: set[int] = set()
    seen: set[int] = set()
    for e in store.events(actions=["approved"], limit=10_000_000):
        if e["invoice_id"] in seen:
            continue
        seen.add(e["invoice_id"])
        if (e["detail"] or {}).get("bulk"):
            bulk_ids.add(e["invoice_id"])
    picked: list[dict[str, Any]] = []
    demo = unreviewed = bulk = 0
    for r in rows:
        approved_at = max(r["reviewed_at"] or "", r["second_reviewed_at"] or "")  # approved once both have
        if not r["final_output"] or (start and approved_at < start):
            continue
        if (r["meta"] or {}).get("demo"):
            demo += 1
        elif r["reviewer"] == AUTONOMOUS_REVIEWER:
            unreviewed += 1
        elif r["id"] in bulk_ids:
            bulk += 1
        else:
            picked.append({**r, "approved_at": approved_at})
    return {"invoices": picked, "demo": demo, "unreviewed": unreviewed, "bulk": bulk}


def page_pngs(path: str | Path, max_pages: int = MAX_PAGES) -> list[bytes]:
    """The file's pages as PNG images: PDF pages at 150 dpi, a photo turned the right way up, the long side
    at most ``MAX_SIDE`` pixels. [] for a file without pages (a text file)."""
    from PIL import Image, ImageOps

    path = Path(path)
    out = []
    for page in render_page_images(path, max_pages):
        with Image.open(io.BytesIO(page.data)) as img:
            upright = img.getexif().get(0x0112, 1) in (0, 1)  # EXIF orientation: a phone photo may be turned
            if page.mime_type == "image/png" and upright and max(img.size) <= MAX_SIDE:
                out.append(page.data)
                continue
            fixed = ImageOps.exif_transpose(img)
            if fixed.mode not in ("RGB", "L"):
                fixed = fixed.convert("RGB")
            fixed.thumbnail((MAX_SIDE, MAX_SIDE))
            buf = io.BytesIO()
            fixed.save(buf, format="PNG")
            out.append(buf.getvalue())
    return out


def approved_values(final: dict[str, Any]) -> dict[str, Any]:
    """What AP approved, as a page reader should read it: the header fields, the tax lines (type, rate, amount)
    and the line items (description, quantity, unit price, amount)."""
    values: dict[str, Any] = {f: "" if final.get(f) is None else final.get(f) for f in HEADER_FIELDS}
    values["tax_lines"] = [
        {"tax_type": t.get("tax_type") or "", "rate": t.get("rate"), "tax_amount": t.get("tax_amount")}
        for t in final.get("tax_lines") or []
    ]
    values["line_items"] = [
        {"description": li.get("description") or "", "quantity": li.get("quantity"),
         "unit_price": li.get("unit_price"), "amount": li.get("amount")}
        for li in final.get("line_items") or []
    ]  # fmt: skip
    return values


def readers_by_field(capture: dict[str, Any] | None, final: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """{field: {"agreed": readers that read the approved value, "disagreed": readers that read another,
    "status": the status AP saw}} from the invoice's stored capture."""
    from .capture.workflow import reader_outcome_rows

    out: dict[str, dict[str, Any]] = {}
    for r in reader_outcome_rows(capture, final):
        entry = out.setdefault(r["field"], {"agreed": [], "disagreed": [], "status": ""})
        if r["reader"] == "fused":
            entry["status"] = r.get("status") or ""
        else:
            entry["agreed" if r["correct"] else "disagreed"].append(r["reader"])
    for entry in out.values():
        entry["agreed"].sort()
        entry["disagreed"].sort()
    return out


def chat_row(images: list[str], values: dict[str, Any]) -> dict[str, Any]:
    """One chat fine-tuning example: the pages and the instruction, then the approved values as JSON."""
    return {
        "messages": [
            {"role": "user",
             "content": [*({"type": "image", "path": p} for p in images), {"type": "text", "text": INSTRUCTION}]},
            {"role": "assistant", "content": json.dumps(values, ensure_ascii=False)},
        ]
    }  # fmt: skip


def export_training_set(
    store: Store, dest: str | Path | IO[bytes], *, since: str | dt.date | None = None, max_pages: int = MAX_PAGES
) -> dict[str, int]:
    """Write the training ZIP to ``dest`` (a path, or a binary file such as ``io.BytesIO``). Returns counts:
    invoices and pages written; invoices left out because their file is no longer on this computer
    (``missing_files``) or has no page to show (``no_pages``: a text file, a damaged file); and approved
    invoices that never go in (``demo``, ``unreviewed``: approved without a person, ``bulk``: bulk-approved
    unchanged without anyone opening them)."""
    picked = training_invoices(store, since)
    counts = {"invoices": 0, "pages": 0, "missing_files": 0, "no_pages": 0, "demo": picked["demo"],
              "unreviewed": picked["unreviewed"], "bulk": picked["bulk"]}  # fmt: skip
    if isinstance(dest, (str, Path)):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
    fields_rows: list[str] = []
    chat_rows: list[str] = []
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for inv in picked["invoices"]:
            path = Path(inv["source_path"] or "")
            if not inv["source_path"] or not path.is_file():
                counts["missing_files"] += 1
                continue
            try:
                pages = page_pngs(path, max_pages)
            except Exception as exc:  # a damaged file: left out, the others still go in
                log.warning("invoice %s: pages not rendered (%s)", inv["id"], exc)
                pages = []
            if not pages:
                counts["no_pages"] += 1
                continue
            images = []
            for n, png in enumerate(pages, 1):
                images.append(f"images/{inv['id']}-p{n}.png")
                zf.writestr(images[-1], png, compress_type=zipfile.ZIP_STORED)  # a PNG is compressed already
            final = inv["final_output"]
            values = approved_values(final)
            capture = store.get_capture(inv["id"])
            fields_rows.append(json.dumps({
                "invoice_id": inv["id"], "file": inv["file_name"], "approved_at": inv["approved_at"],
                "layout": (capture or {}).get("layout_source") or "", "images": images,
                "fields": {f: values[f] for f in HEADER_FIELDS}, "tax_lines": values["tax_lines"],
                "line_items": values["line_items"], "readers": readers_by_field(capture, final),
            }, ensure_ascii=False))  # fmt: skip
            chat_rows.append(json.dumps(chat_row(images, values), ensure_ascii=False))
            counts["invoices"] += 1
            counts["pages"] += len(images)
        zf.writestr("fields.jsonl", "".join(row + "\n" for row in fields_rows))
        zf.writestr("chat.jsonl", "".join(row + "\n" for row in chat_rows))
        zf.writestr("README.txt", readme(counts, _since(since)))
    return counts


_LEFT_OUT = (
    ("missing_files", "their file is no longer on the computer"),
    ("no_pages", "their file has no page to show (a text file, or a damaged file)"),
    ("demo", "they are demo invoices (made up)"),
    ("unreviewed", "they were approved without a person (nobody checked their values)"),
    ("bulk", "they were bulk-approved unchanged, without anyone opening them (nobody checked them on the page)"),
)


def readme(counts: dict[str, int], since: str = "") -> str:
    """README.txt of the training ZIP."""
    made = (
        f"Made by AP Coder on {dt.date.today().isoformat()} from {counts.get('invoices', 0)} invoice(s) AP approved"
        f"{f' since {since}' if since else ''} ({counts.get('pages', 0)} page image(s))."
    )
    left_out = [f"  - {counts[key]} because {why}" for key, why in _LEFT_OUT if counts.get(key)]
    if left_out:
        made += "\nApproved invoices left out:\n" + "\n".join(left_out)
    return f"""AP Coder training data
======================

{made}
Only invoices a person checked are in it: never demo invoices, invoices AP Coder approved on its own,
or invoices bulk-approved unchanged without anyone opening them.

What is in this ZIP
-------------------
images/<invoice>-p<page>.png
    Each invoice's pages as images, up to {MAX_PAGES} pages: PDF pages at 150 dpi, photos turned the right
    way up, the long side at most {MAX_SIDE} pixels. <invoice> is AP Coder's number for the invoice.

fields.jsonl
    One line (a JSON object) per invoice: its images, the header fields AP approved (supplier, invoice
    number, dates, PO, terms, currency, GST/HST and QST numbers, subtotal, tax, total), the tax lines and
    the line items, and, per field, which of AP Coder's readers read the approved value ("agreed") and
    which read something else ("disagreed"), with the status AP saw (verified / likely / check).
    Readers: rules = OCR or the PDF's text read by AP Coder's rules, ocr / ocr2 = a second OCR read,
    vlm = the page reader, template = the supplier's learned layout, ai = the AI model,
    di = Azure Document Intelligence.

chat.jsonl
    The same invoices as chat fine-tuning examples, one per line: the user turn holds the page images
    ({{"type": "image", "path": "images/..."}}) and the instruction "{INSTRUCTION}"; the assistant turn
    is the JSON AP approved (header fields, tax lines, line items).

README.txt
    This file.

Where it goes
-------------
AP Coder made this file on your computer and sends it nowhere: it leaves the computer only if someone
copies it. It holds your suppliers' real invoices and amounts, so keep and share it as you would the
invoices themselves. The page images show anything printed on the invoices, including bank account
and remittance details when a supplier prints them: share it only with people who may see those. The
GL coding is not in it.

What it is for
--------------
Fine-tuning a vision model that reads invoice pages, such as OvisOCR2 (the page reader AP Coder
recommends) or Qwen 3.5, so it learns your suppliers' layouts and the mistakes to avoid. Fine-tuning
tools for vision models (LLaMA-Factory, ms-swift, Unsloth and others) take chat rows like these with a
small conversion: each {{"type": "image", "path": ...}} becomes the image file it names, in the tool's own
layout. Keep a few invoices aside, never trained on, to measure the tuned model on pages it never saw,
and use the tuned model only once it reads them at least as well as the one it replaces.
"""
