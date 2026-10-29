"""Probe ``pipeline``: generated invoices (digital, scans, photos), two suppliers' streams, the repo's samples, a
broken PDF and an empty file through the offline pipeline into a new database (touchless on), then each saved one
read again as the page reader does; what each result and the database hold, for ``scripts/same_behaviour.py``. What
differs from run to run is left out: timings, timestamps, the checks' stamp (it has the rate file's path and time)
and the hash of a photo made a PDF (the PDF has the time it was made).

    PYTHONPATH=<tree> python scripts/probes/pipeline.py N WORK_DIR > out.json"""

import json
import os
import shutil
import sqlite3
import sys
from dataclasses import replace
from pathlib import Path

work = Path(sys.argv[2]).resolve()
os.environ["AP_PRIVATE_DIR"] = str(work / "private")
os.environ["AP_USER_SETTINGS"] = str(work / "user_settings.json")
os.environ["AP_ENV_FILE"] = str(work / "test.env")

import ap_coder.core.store._base as base  # noqa: E402
import ap_coder.core.store.invoices as inv_mod  # noqa: E402
from ap_coder.ai.pipeline import InvoicePipeline  # noqa: E402
from ap_coder.bench.generator import generate, make_stream_case  # noqa: E402
from ap_coder.core.config import Settings  # noqa: E402
from ap_coder.core.reference_data import load_reference_data  # noqa: E402
from ap_coder.core.store import Store  # noqa: E402

TICK = [0]


def fixed_now() -> str:
    TICK[0] += 1
    return f"2026-10-29T{TICK[0] // 3600 % 24:02d}:{TICK[0] // 60 % 60:02d}:{TICK[0] % 60:02d}"


base._now = fixed_now
inv_mod._now = fixed_now
ROOT = Path(__file__).resolve().parents[2]  # the inputs: this checkout's samples and data, for both trees
DATA = ROOT / "data"


VOLATILE = {
    "timings",
    "seconds",
    "created_at",
    "updated_at",
    "at",
    "reviewed_at",
    "since",
    "timings_seconds",
    "checks_stamp",
    "file_sha256",
}


def clean(obj):
    """Drop what differs from run to run (``VOLATILE``), in JSON stored as text too; the work folder as <work>."""
    if isinstance(obj, dict):
        return {k: clean(v) for k, v in obj.items() if k not in VOLATILE}
    if isinstance(obj, list):
        return [clean(v) for v in obj]
    if isinstance(obj, str):
        if obj.startswith(("{", "[")) and any(k in obj for k in VOLATILE):
            try:
                return clean(json.loads(obj))
            except ValueError:
                pass
        return obj.replace(str(work), "<work>")
    return obj


def main() -> None:
    n = int(sys.argv[1])
    if work.exists():
        shutil.rmtree(work)
    (work / "in").mkdir(parents=True)
    files = [c.path for c in generate(work / "gen", n, seed=11, scanned_fraction=0.3)]
    files += [make_stream_case(work / "stream", 1, s, i).path for s in (2, 5) for i in range(24)]  # templates, autonomy
    for p in sorted((ROOT / "samples").glob("*")):
        if p.suffix.lower() in (".pdf", ".md", ".txt", ".png", ".jpg"):
            files.append(shutil.copy(p, work / "in" / p.name))
    (work / "in" / "broken.pdf").write_bytes(b"%PDF-1.4 not really")
    (work / "in" / "empty.txt").write_text("")
    files += [work / "in" / "broken.pdf", work / "in" / "empty.txt"]
    store = Store(work / "private" / "ap_coder.db")
    reference = load_reference_data(
        DATA / "chart_of_accounts.csv",
        DATA / "cost_centers.csv",
        DATA / "tax_gl_mapping.csv",
        DATA / "coding_policy.md",
    )
    store.set_touchless(True, "Mia")
    s = Settings()
    pipeline = InvoicePipeline(replace(s, llm=replace(s.llm, provider="off")), reference, store=store)
    out = []
    for i, f in enumerate(files):
        r = pipeline.process(Path(f))
        out.append(
            clean(
                {
                    "i": i,
                    "file": Path(f).name,
                    "error": r.error,
                    "output": r.output,
                    "issues": [(x.severity, x.code, x.message) for x in (r.report.issues if r.report else [])],
                    "autonomy": r.autonomy,
                    "capture": r.capture.to_dict() if r.capture else None,
                    "invoice_id": r.invoice_id,
                    "rules": r.rules_applied,
                    "history": r.history_examples,
                }
            )
        )
        if r.invoice_id and r.output and not (r.autonomy or {}).get("auto") and (i % 3 == 0 or "-v00" in Path(f).name):
            store.approve_invoice(r.invoice_id, r.output, "Ann")  # teach the next ones
    # As the page reader does: each saved invoice read again with its pages' text, not saved again.
    for listed in store.list_invoices():
        row = store.get_invoice(listed["id"])
        if row.get("status") != "review" or not row.get("source_path"):
            continue
        text = [(row.get("extraction_md") or "x")[:400]]
        r = pipeline.process(Path(row["source_path"]), page_text=text, save=False, invoice_id=row["id"])
        out.append(
            clean(
                {
                    "again": row["id"],
                    "error": r.error,
                    "output": r.output,
                    "autonomy": r.autonomy,
                    "issues": [(x.severity, x.code) for x in (r.report.issues if r.report else [])],
                }
            )
        )
    conn = sqlite3.connect(work / "private" / "ap_coder.db")
    conn.row_factory = sqlite3.Row
    for table in ("invoices", "feedback", "page_queue"):
        try:
            rows = [dict(x) for x in conn.execute(f"SELECT * FROM {table} ORDER BY rowid")]
        except sqlite3.OperationalError as exc:
            rows = [str(exc)]
        out.append(clean({"table": table, "rows": rows}))
    print(json.dumps(out, sort_keys=True, default=str))


if __name__ == "__main__":
    main()
