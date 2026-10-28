"""Line items, wrong this time and right next time (D-120): each supplier's invoices read in order as the app reads
them (``offline_coder.read_invoice``, then coded from AP's approvals, ``code_from_capture``), AP correcting each line
to what the invoice prints (its description, numbers and GL account) and approving it, AP Coder learning from it
(``learn_from_approval``). Scans read a supplier's items the same wrong way each time (spaces lost, letters for
digits, the item code run in): what AP corrected once should read right on the next invoice that lists the item, and
the GL account AP gave it should follow.

    python -m ap_coder.bench lines --suppliers 20 --invoices 12 --scanned 1 [--no-memory]

``--no-memory``: what-if, the lines read as printed every time (AP Coder before D-120), for the before/after."""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import tempfile
import time
import zlib
from collections import defaultdict
from pathlib import Path
from typing import Any

from .coding import sample_reference
from .generator import make_stream_case, stream_scanned, supplier_id
from .harness import _map
from .learning import EPOCH, _app_clock

CLERK = "Bench clerk"
# Each item's GL account, as AP codes it (one of the sample chart's expense accounts, the same for every supplier).
ACCOUNTS = ("5000", "5100", "6000", "6010", "6020", "6030", "6040", "6100", "6110", "6200", "6300", "6400")

Job = tuple[str, int, int, int, float, bool]  # (out_dir, seed, supplier, invoices, scanned share, memory off)


def account_of(description: str) -> str:
    return ACCOUNTS[zlib.crc32(" ".join(description.split()).casefold().encode()) % len(ACCOUNTS)]


def _same(a: Any, b: Any) -> bool:
    return " ".join(str(a or "").split()) == " ".join(str(b or "").split())


def _money(a: Any, b: Any) -> bool:
    try:
        return a is not None and b is not None and abs(float(a) - float(b)) <= 0.011
    except (TypeError, ValueError):
        return False


def pair(proposed: list[dict[str, Any]], truth: list[dict[str, Any]]) -> list[tuple[dict[str, Any] | None, dict]]:
    """Each line the invoice prints with the proposed line AP corrects into it: by position when as many were read,
    else the closest amount not taken; None for a line AP adds."""
    if len(proposed) == len(truth):
        return list(zip(proposed, truth, strict=True))
    free = list(range(len(proposed)))
    out: list[tuple[dict[str, Any] | None, dict]] = []
    for t in truth:
        best = min(free, key=lambda i: abs(float(proposed[i].get("amount") or 0) - float(t["amount"])), default=None)
        if best is not None and _money(proposed[best].get("amount"), t["amount"]):
            free.remove(best)
            out.append((proposed[best], t))
        else:
            out.append((None, t))
    return out


def simulate_supplier(job: Job) -> dict[str, Any]:
    import ap_coder.ai.offline_coder as offline_coder

    real = offline_coder.apply_line_memory
    if job[5]:  # what-if: AP Coder before D-120, the lines shown as read
        offline_coder.apply_line_memory = lambda store, capture: 0
    try:
        return _simulate(job)
    finally:
        offline_coder.apply_line_memory = real


def _simulate(job: Job) -> dict[str, Any]:
    from ap_coder.ai.offline_coder import code_from_capture, read_invoice
    from ap_coder.capture import build_layout, workflow
    from ap_coder.capture.layout import ocr_cache
    from ap_coder.core.store import Store

    out_dir, seed, supplier, invoices, scanned_fraction, _memory_off = job
    sid = supplier_id(seed, supplier)
    cases_dir = Path(out_dir) / "cases" / sid
    reference = sample_reference()
    rows: list[dict[str, Any]] = []
    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="ap-lines-") as tmp, contextlib.ExitStack() as held:
        clock = held.enter_context(_app_clock(tmp))
        store = Store(Path(tmp) / "lines.db")
        held.enter_context(store.session())
        held.enter_context(ocr_cache(Path(tmp) / ".cache" / "ocr"))
        corrected_before: set[str] = set()  # items whose description AP had to correct on an earlier invoice
        coded_before: set[str] = set()  # items AP coded on an earlier invoice
        for i in range(invoices):
            case = make_stream_case(cases_dir, seed, supplier, i, stream_scanned(seed, supplier, i, scanned_fraction))
            clock[0] = (EPOCH + dt.timedelta(hours=i)).isoformat(timespec="seconds")
            truth_lines = list(case.truth.get("line_items") or [])
            layout = build_layout(case.path)
            capture = read_invoice(case.path, store, layout=layout)[0]
            name = case.truth["fields"]["vendor_name"]["value"]
            coding = code_from_capture(capture, reference, store.learning_rows(name), store).coding
            proposed = coding.model_dump(mode="json")
            # The vendor name and the rest of the header as printed: this measures the lines alone.
            header = {f: v["value"] for f, v in case.truth["fields"].items() if f in proposed and v.get("value")}
            final_lines, lines = [], []
            pairs = pair(proposed["line_items"], truth_lines)
            for n, (got, want) in enumerate(pairs, 1):
                item = " ".join(want["description"].split())
                gl = account_of(item)
                right_text = got is not None and _same(got.get("description"), item)
                right = right_text and _money(got.get("amount"), want["amount"])
                lines.append({"item": item, "read": got is not None, "shown": (got or {}).get("description", ""),
                              "right_text": right_text, "right": right,
                              "renamed": bool(got and "(read “" in str(got.get("reasoning_justification") or "")),
                              # named as another item of the supplier's: the error that must never happen
                              "other_item": bool(got) and not right_text and any(
                                  _same(got.get("description"), other) for other in coded_before if other != item),
                              "corrected_before": item in corrected_before, "coded_before": item in coded_before,
                              "gl_right": got is not None and got.get("predicted_gl_code") == gl})  # fmt: skip
                base = got or {"line_number": len(proposed["line_items"]) + n}
                final_lines.append({**base, "description": want["description"], "quantity": want.get("quantity"),
                                    "unit_price": want.get("unit_price"), "amount": want["amount"],
                                    "predicted_gl_code": gl})  # fmt: skip
            final = {**proposed, **header, "line_items": final_lines}
            invoice_id = store.add_invoice(case.path, proposed, None)
            store.save_capture(invoice_id, capture.to_dict())
            store.approve_invoice(invoice_id, final, CLERK)
            workflow.learn_from_approval(store, invoice_id, final, actor=CLERK)
            for line in lines:
                coded_before.add(line["item"])
                if not line["right_text"]:
                    corrected_before.add(line["item"])
            rows.append({"supplier": sid, "index": i + 1, "case": case.id, "scanned": case.scanned,
                         "layout": case.truth["layout"], "lines": lines})  # fmt: skip
    return {"supplier": sid, "rows": rows, "seconds": round(time.perf_counter() - t0, 2)}


def _ratio(a: int, b: int) -> float | None:
    return round(a / b, 4) if b else None


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    lines = [li for r in rows for li in r["lines"]]
    again = [li for li in lines if li["corrected_before"]]
    known = [li for li in lines if li["coded_before"]]
    first = [li for li in lines if not li["coded_before"]]
    by_position: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_position["1" if r["index"] == 1 else "2-5" if r["index"] <= 5 else "6+"].extend(r["lines"])
    return {
        "invoices": len(rows),
        "lines": len(lines),
        "lines_right": _ratio(sum(li["right"] for li in lines), len(lines)),
        "descriptions_right": _ratio(sum(li["right_text"] for li in lines), len(lines)),
        "descriptions_corrected": sum(not li["right_text"] for li in lines),
        # Wrong this time, right next time: an item whose description AP corrected before, listed again.
        "corrected_before": len(again),
        "corrected_before_right": _ratio(sum(li["right_text"] for li in again), len(again)),
        "first_time_right": _ratio(sum(li["right_text"] for li in first), len(first)),
        # The GL account AP gave an item, on the next invoice that lists it.
        "known_items": len(known),
        "known_items_gl_right": _ratio(sum(li["gl_right"] for li in known), len(known)),
        "corrected_before_gl_right": _ratio(sum(li["gl_right"] for li in again), len(again)),
        "renamed": sum(li["renamed"] for li in lines),
        # Named as approved before, partly: the item code left out, the spacing of an item new to the memory still off.
        "renamed_partly": sum(li["renamed"] and not li["right_text"] and not li.get("other_item") for li in lines),
        "renamed_other_item": sum(li["renamed"] and bool(li.get("other_item")) for li in lines),
        "by_position": {
            k: {"lines": len(v), "descriptions_right": _ratio(sum(li["right_text"] for li in v), len(v))}
            for k, v in sorted(by_position.items())
        },  # fmt: skip
    }


def _pct(x: float | None) -> str:
    return "–" if x is None else f"{100 * x:.1f}%"


def report_md(s: dict[str, Any], params: dict[str, Any]) -> str:
    rows = [
        ("Lines read right (description and amount)", _pct(s["lines_right"])),
        ("Descriptions read right", _pct(s["descriptions_right"])),
        ("Descriptions AP had to correct", str(s["descriptions_corrected"])),
        ("Items AP corrected before, listed again", str(s["corrected_before"])),
        ("… read right this time (right next time)", _pct(s["corrected_before_right"])),
        ("Items listed the first time, read right", _pct(s["first_time_right"])),
        ("Items AP coded before: GL account right", _pct(s["known_items_gl_right"])),
        ("Items AP corrected before: GL account right", _pct(s["corrected_before_gl_right"])),
        ("Lines named as AP approved before", str(s["renamed"])),
        ("… still not exactly as printed (code left out, spacing still off)", str(s["renamed_partly"])),
        ("… named as another item", str(s["renamed_other_item"])),
    ]
    head = (f"# Line items, right next time\n\n{params['suppliers']} suppliers × {params['invoices']} invoices, "
            f"{_pct(params['scanned'])} scans, line memory {'off' if params['no_memory'] else 'on'}.\n\n")  # fmt: skip
    table = "| measure | value |\n|---|---|\n" + "\n".join(f"| {a} | {b} |" for a, b in rows)
    pos = "\n\n| invoice # | lines | descriptions right |\n|---|---|---|\n" + "\n".join(
        f"| {k} | {v['lines']} | {_pct(v['descriptions_right'])} |" for k, v in s["by_position"].items()
    )
    return head + table + pos + "\n"


def run_lines(suppliers: int, invoices: int, seed: int, scanned: float, out: str, workers: int = 1,
              no_memory: bool = False) -> dict[str, Any]:  # fmt: skip
    out_dir = Path(out)
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    jobs: list[Job] = [(str(out_dir), seed, s, invoices, scanned, no_memory) for s in range(1, suppliers + 1)]
    results = _map(simulate_supplier, jobs, workers, "suppliers")
    rows = [r for res in results for r in res["rows"]]
    params = {"suppliers": suppliers, "invoices": invoices, "seed": seed, "scanned": scanned, "no_memory": no_memory,
              "workers": workers, "seconds": round(time.perf_counter() - t0, 1)}  # fmt: skip
    summary = summarize(rows)
    (out_dir / "lines.json").write_text(json.dumps({"params": params, "summary": summary, "rows": rows}, indent=1))
    (out_dir / "lines.md").write_text(report_md(summary, params), encoding="utf-8")
    return {"params": params, "summary": summary}
