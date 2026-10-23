"""How long the heavy pages take on a big database: not a test, a stopwatch for before and after a change.

    python scripts/ci/perf_timing.py [--invoices 3000] [--repeat 3]

Builds a demo database of about 3,000 invoices in a temporary folder (each with a file of its own, a third approved),
then times ``/review``, ``/review/{id}``, a bulk approval of 100 invoices and 50 ``find_by_hash`` calls, and prints
a small table (the best of ``--repeat`` runs, and the SQLite connections each one opened).
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from ap_coder.core.store import REVIEW, Store, load_sample_setup  # noqa: E402
from tests.conftest import SAMPLE_STEM, SAMPLES  # noqa: E402
from tests.webclient import PAGE, client_for  # noqa: E402

_connects = 0
_real_connect = sqlite3.connect


def _counting(*args, **kwargs):
    global _connects
    _connects += 1
    return _real_connect(*args, **kwargs)


def build(folder: Path, n: int, bulk: int) -> tuple[Store, list[Path], list[int]]:
    """A database of ``n`` invoices like tests/test_scale.py's ``_add`` (each with a file of its own; a third
    approved), plus ``bulk`` clean ones of a known vendor for the bulk approval. (store, files, bulk ids)"""
    store = Store(folder / "private" / "ap_coder.db")
    load_sample_setup(store)
    gt = json.loads((SAMPLES / "ground_truth" / f"{SAMPLE_STEM}.json").read_text())
    pdf = (SAMPLES / f"{SAMPLE_STEM}.pdf").read_bytes()
    files_dir = folder / "files"
    files_dir.mkdir()
    files, ids = [], []
    with store.session():
        for i in range(n):
            path = files_dir / f"{i}.pdf"
            path.write_bytes(pdf + f"\n% {i}\n".encode())
            files.append(path)
            doc = {**gt, "invoice_number": f"N-{i}", "vendor_name": f"Vendor {i % 7}", "grand_total": 100.0 + i}
            issues = [{"severity": "warning", "code": "X", "message": "m"}] if i % 5 == 0 else []
            clean = {"requires_review": i % 3 == 0, "adjusted_confidence": 0.9, "issues": issues}
            ids.append(store.add_invoice(path, doc, clean, meta={"n": i}))
        for invoice_id in ids[: n // 3]:
            store.approve_invoice(invoice_id, store.get_invoice(invoice_id)["ai_output"], "jane")
        bulk_ids = []
        for i in range(bulk):
            path = files_dir / f"bulk-{i}.pdf"
            path.write_bytes(pdf + f"\n% bulk {i}\n".encode())
            doc = {**gt, "invoice_number": f"B-{i}", "vendor_name": f"Bulk Supplier {i}"}
            bulk_ids.append(store.add_invoice(path, doc, {"requires_review": False, "adjusted_confidence": 0.97}))
    # known to the ERP vendor master, so none is a new vendor (which always goes to a person)
    store.import_vendor_master([{"vendor_name": f"Bulk Supplier {i}", "erp_id": f"B{i}"} for i in range(bulk)])
    return store, files, bulk_ids


def timed(fn, repeat: int) -> tuple[float, int]:
    """(best seconds of ``repeat`` runs, connections opened by the last run)."""
    global _connects
    best = float("inf")
    for _ in range(repeat):
        _connects = 0
        start = time.perf_counter()
        fn()
        best = min(best, time.perf_counter() - start)
    return best, _connects


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--invoices", type=int, default=3000)
    parser.add_argument("--bulk", type=int, default=100)
    parser.add_argument("--repeat", type=int, default=3)
    args = parser.parse_args(argv)
    logging.disable(logging.INFO)
    with tempfile.TemporaryDirectory(prefix="ap-perf-") as tmp:
        started = time.perf_counter()
        store, files, bulk_ids = build(Path(tmp), args.invoices, args.bulk)
        built = time.perf_counter() - started
        client = client_for(store)
        one = store.list_invoices(REVIEW)[0]["id"]

        def page(url: str):
            def get():
                answer = client.get(url)
                assert answer.status_code == 200, (url, answer.status_code)

            return get

        def find():
            for path in files[:: max(1, len(files) // 50)][:50]:
                assert store.find_by_hash(path) is not None

        sqlite3.connect = _counting
        try:
            rows = [
                ("/review", *timed(page("/review"), args.repeat)),
                ("/review/{id}", *timed(page(f"/review/{one}"), args.repeat)),
                ("50 find_by_hash", *timed(find, args.repeat)),
            ]

            def bulk():
                answer = client.post("/review/bulk-approve", data={"ids": [str(i) for i in bulk_ids]}, headers=PAGE)
                assert answer.status_code == 200, answer.text

            rows.append((f"bulk approve {len(bulk_ids)}", *timed(bulk, 1)))
        finally:
            sqlite3.connect = _real_connect
        approved = [i for i in bulk_ids if (store.get_invoice(i) or {}).get("status") != REVIEW]
        total = len(store.invoice_columns(("id",)))
        print(f"database: {total} invoices (built in {built:.1f} s); bulk approval approved {len(approved)}")
        print(f"{'what':<22} {'seconds':>9} {'connections':>12}")
        for name, seconds, connections in rows:
            print(f"{name:<22} {seconds:>9.3f} {connections:>12}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
