"""Probe ``read``: N generated invoices (digital, scans and photos) read as the app reads them, alone and with the
vendor master's record, then two suppliers' streams read in order with AP approving each (their templates, aliases,
lessons and line memory at work); each reading's fields, checks and lines, for ``scripts/same_behaviour.py``.

    PYTHONPATH=<tree> python scripts/probes/read.py N WORK_DIR > out.jsonl"""

import json
import os
import shutil
import sys
from pathlib import Path

work = Path(sys.argv[2]).resolve()
os.environ["AP_PRIVATE_DIR"] = str(work / "private")
os.environ["AP_USER_SETTINGS"] = str(work / "user_settings.json")

from ap_coder.ai.offline_coder import read_invoice  # noqa: E402
from ap_coder.bench.generator import generate, make_stream_case  # noqa: E402
from ap_coder.bench.learning import approved_values  # noqa: E402
from ap_coder.capture import analyze  # noqa: E402
from ap_coder.capture.workflow import learn_from_approval  # noqa: E402
from ap_coder.core.store import Store  # noqa: E402


def shown(capture) -> str:
    out = capture.to_dict()
    return json.dumps(out, sort_keys=True, default=str).replace(str(work), "<work>")


def main() -> None:
    n = int(sys.argv[1])
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    for case in generate(work / "gen", n, seed=13, scanned_fraction=0.35):
        fields = case.truth["fields"]
        vendor = {
            "name": (fields.get("vendor_name") or {}).get("value") or "",
            "gst_number": (fields.get("gst_hst_registration_number") or {}).get("value") or "",
            "erp_id": "V1",
        }
        print(shown(analyze(case.path)))
        print(shown(analyze(case.path, vendor=vendor)))
    store = Store(work / "private" / "ap_coder.db")
    with store.session():
        for supplier in (3, 9):
            for i in range(max(4, n // 4)):
                case = make_stream_case(work / "stream", 1, supplier, i)
                capture = read_invoice(case.path, store)[0]
                print(shown(capture))
                proposed = {f: fr.value for f, fr in capture.fields.items() if fr.value not in (None, "")}
                invoice_id = store.add_invoice(case.path, {**proposed, "line_items": []}, None)
                store.save_capture(invoice_id, capture.to_dict())
                final = {**approved_values(case.truth, capture), "line_items": []}
                store.approve_invoice(invoice_id, final, "Ann")
                learn_from_approval(store, invoice_id, final, actor="Ann")


if __name__ == "__main__":
    main()
