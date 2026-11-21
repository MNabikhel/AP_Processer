"""Probe ``jde``: PO matching (``coding/po.py``), JD Edwards vouchers (``erp/jde*.py``) and bulk approval
(``coding/bulk.py``) on made-up books, each set of cases in a new database of its own, for
``scripts/same_behaviour.py``:

* the PO-matched invoices of ``bench.jde_po_vouchers`` (every Canadian tax case, each kind of match), under both its
  setups, with their PO numbers typed as suppliers print them, and billed 2.5% over (what ``po.TOTAL_TOLERANCE`` lets
  by); each one's ``po.match_invoice``, ``po_findings``, ``voucher_match`` and review checks, and the batch of them;
  then the set billed in a copy of the made-up company's queue (every other one approved, its voucher match kept,
  some rejected): the others matched again and each PO's page (``po_billing``), the approved ones' batch;
* the subcontractor's progress billings of the same set, under both setups, the tax on the whole or on the net, and
  with subcontract billings off;
* the made-up company of ``ops.demo.load_company`` (its JD Edwards lists, its setup and seven invoices read by the
  pipeline): each invoice's voucher on the review screen, and the batch;
* each batch under 16 settings (the amounts, the decimals, the account columns, the remarks), then N batches of the
  invoices above picked by a seed, each under settings picked by it: ``jde.validate``, ``dry_run``,
  ``jde_keying.vouchers`` and its sheet's cells, ``export_batch``'s files and ``build_zip``'s;
* the queue's bulk approval of the company's invoices (``queue_checks``, ``sort_for_bulk``, ``pay_from_stops``,
  ``bulk_approve``) and of the demo's, checked again after a change of settings and approved as the queue's job
  (``issues_today``, ``recheck_queue``, ``approve_in_job``).

One JSON line each. The same seed gives the same lines: each batch has a seed of its own, the invoices are dated
before a fixed day, and what the clock writes is never printed.

    PYTHONPATH=<tree> python scripts/probes/jde.py N WORK_DIR > out.jsonl"""

import dataclasses
import datetime as dt
import decimal
import enum
import io
import json
import os
import random
import re
import sys
import types
import zipfile
from dataclasses import replace
from pathlib import Path

ON = dt.date(2026, 11, 2)  # the batches' day; the company's invoices are dated in the days before
ROOT = Path(__file__).resolve().parents[2]
TYPED = ("PO {}", "PO-{}", "PO{}", "PO#{}", " #{} ", "PO# {}")  # how a PO is printed; the last over VLPO's 8
# What the clock writes: a stamp of the store's (a PO's ``updated_at``), by its name. Nothing else is left out: a date
# of the books (an invoice's, a G/L date, a keying sheet's cell) is printed as it is.
CLOCK = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?([+-]\d{2}:\d{2}|Z)?")


def _plain(value):
    """``value`` as JSON reads it: a dataclass by its fields, a set sorted; never an object's address."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {f.name: getattr(value, f.name) for f in dataclasses.fields(value)}
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=repr)
    if isinstance(value, (dt.date, decimal.Decimal)):
        return str(value)
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, types.MappingProxyType):
        return dict(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return f"<{type(value).__name__}>"


def _stamps_out(value):
    """``value`` (as JSON reads it) with each ``..._at`` stamp the clock wrote as "(time)"."""
    if isinstance(value, dict):
        return {k: "(time)" if k.endswith("_at") and isinstance(v, str) and CLOCK.fullmatch(v) else _stamps_out(v)
                for k, v in value.items()}  # fmt: skip
    if isinstance(value, list):
        return [_stamps_out(v) for v in value]
    return value


def emit(part: str, **said) -> None:
    plain = json.loads(json.dumps({"part": part, **said}, default=_plain))
    print(json.dumps(_stamps_out(plain), sort_keys=True))


def said(call, *args, **kwargs):
    """What ``call`` returns, or the error it raised (its type and words)."""
    try:
        return call(*args, **kwargs)
    except Exception as exc:  # a probe says what failed, as it says what worked
        return {"raised": type(exc).__name__, "said": str(exc)}


def zip_members(data: bytes) -> dict[str, str]:
    """A ZIP's files by name (their bytes as text): its own times are the clock's."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {name: zf.read(name).decode("utf-8", "replace") for name in zf.namelist()}


def sheet_cells(data: bytes) -> dict[str, list[list]]:
    """A workbook's cells, sheet by sheet: its own properties hold the clock's time."""
    import openpyxl

    wb = openpyxl.load_workbook(io.BytesIO(data))
    return {ws.title: [[c.value for c in row] for row in ws.iter_rows()] for ws in wb.worksheets}


def matrix(base):
    """16 settings of ``base``: the amounts (gross or by G/L line), the decimals, the account columns and the
    remarks; the line numbering and the G/L date taken along."""
    from ap_coder.erp import jde_settings as js

    out = []
    for amounts in (js.AMOUNT_GROSS, js.AMOUNT_GL):
        for decimals in (js.IMPLIED, js.POINT):
            for accounts in (js.ACCOUNT_ANI, js.ACCOUNT_COLUMNS):
                for remarks in (js.REMARKS_REFERENCE, js.REMARKS_TYPED):
                    k = len(out)
                    out.append(replace(base, amount_mode=amounts, decimals=decimals, account_mode=accounts,
                                       remarks=remarks, line_numbering=js.MATCH_HEADER if k % 3 else js.SEQUENTIAL,
                                       gl_date=js.GL_DATE_EXPORT if k % 5 == 0 else js.GL_DATE_INVOICE))  # fmt: skip
    return out


def batch(part: str, store, invoices: list[dict], settings, lookup, number: int) -> None:
    """Everything a batch of ``invoices`` gives under ``settings``: its checks, its dry run, its keying sheet, its
    files and its ZIP."""
    from ap_coder.erp import jde, jde_keying, jde_zfiles

    matches = said(jde_keying.po_matches, store, invoices, settings.charge_account) if settings.po_matched else None
    matches = matches if isinstance(matches, dict) else None
    left_out = said(jde.validate, invoices, settings, lookup, on=ON)
    kept = [inv for inv in invoices if not isinstance(left_out, dict) or inv["id"] not in left_out]
    keyed = said(jde_keying.vouchers, invoices, settings, lookup, on=ON, po_matches=matches)
    sheet = said(jde_keying.build, invoices, settings, lookup, batch=number, on=ON, po_matches=matches)
    files = said(jde_zfiles.export_batch, kept, settings, lookup, batch=number, on=ON, po_matches=matches)
    dry = said(jde.dry_run, invoices, settings, lookup, batch=number, on=ON, po_matches=matches)
    made = said(jde.build_zip, kept, settings, lookup, batch=number, on=ON, check_now=True, po_matches=matches,
                left_out=left_out if isinstance(left_out, dict) else None,
                left_invoices=[inv for inv in invoices if inv not in kept])  # fmt: skip
    emit(part, batch=number, ids=[inv["id"] for inv in invoices], settings=settings, matches=matches,
         validate=left_out, vouchers=keyed, dry_run=dry, files=files,
         sheet=sheet_cells(sheet[0]) if isinstance(sheet, tuple) else sheet,
         zip=zip_members(made[0]) if isinstance(made, tuple) else made)  # fmt: skip


def by_name(numbers: dict[str, str]):
    """The supplier numbers of a set's suppliers, by the name an invoice gives (a vendor lookup)."""
    return lambda final: numbers.get(final.get("vendor_name") or "", "")


def load_pos(store, files: dict[str, bytes]) -> None:
    """The set's PO files loaded as the Purchase orders page reads them, every column by its own name."""
    from ap_coder.coding import po
    from ap_coder.erp.import_specs import PURCHASE_ORDERS, check
    from ap_coder.web.files import read_sheet

    for name, data in files.items():
        sheet, _ = read_sheet(name, data)
        rows, _ = po.rows_from_records(sheet.to_dict("records"), check(PURCHASE_ORDERS, list(sheet.columns)).found)
        store.import_purchase_orders(rows)


def copy_of(db: Path, to: Path) -> Path:
    import sqlite3

    to.parent.mkdir(parents=True)
    source, copy = sqlite3.connect(db), sqlite3.connect(to)
    source.backup(copy)
    source.close()
    copy.close()
    return to


def tax_setup(qst_in_line: bool):
    from ap_coder.coding.tax import TaxRateTable, TaxSetup, TaxTreatment, load_tax_mapping

    mapping = load_tax_mapping(ROOT / "data" / "tax_gl_mapping.csv")
    if qst_in_line:
        mapping["QST"] = TaxTreatment("QST", "expense_to_line")
    return TaxSetup(TaxRateTable.load(ROOT / "data" / "canada_tax_rates.csv"), mapping)


def each_invoice(part: str, store, invoices: list[dict], settings, numbers: dict[str, str], **about) -> None:
    """Each invoice's PO match, its findings, its voucher match, and the voucher checks of its review screen."""
    from ap_coder.coding import po
    from ap_coder.core.schema import InvoiceCoding
    from ap_coder.erp import jde_review

    for inv in invoices:
        final = inv["final_output"]
        coding = InvoiceCoding.model_validate({k: v for k, v in final.items() if k != "gl_distribution"})
        match = said(po.match_invoice, coding, store, inv["id"])
        number = str(final.get("po_number") or "").strip()
        matched = said(po.voucher_match, match, number, settings.charge_account) if number else None
        vm = matched if isinstance(matched, po.VoucherMatch) else None
        an8 = numbers.get(final.get("vendor_name") or "", "")
        emit(part, id=inv["id"], **about, match=match, findings=said(po.po_findings, coding, store, inv["id"]),
             voucher_match=matched, kept=said(po.kept_match, vm) if vm else None,
             voucher=said(jde_review.voucher_findings, final, settings, an8),
             match_checks=said(jde_review.match_findings, final, settings, an8, vm) if vm else None,
             view=said(jde_review.voucher_view, final, settings, an8, on=ON, match=vm))  # fmt: skip


def billed(store, invoices: list[dict], settings, js, numbers: dict[str, str], setup: str) -> list[dict]:
    """The set added to the queue of the made-up company (``store``, its chart and suppliers, the set's POs
    loaded), every other one approved as the review screen approves it (its voucher match kept with it) and every
    fifth rejected: each one left in the queue matched again, what the others billed counted, then each PO's billing.
    The approved ones, as approved, for the batches."""
    from ap_coder.ai.pipeline import finalise_coding
    from ap_coder.core.schema import InvoiceCoding

    approved, left = [], []
    for k, inv in enumerate(invoices):
        coding = InvoiceCoding.model_validate({k: v for k, v in inv["final_output"].items() if k != "gl_distribution"})
        invoice_id = store.add_invoice(store.path.parent / f"{inv['id']}.pdf", coding.model_dump(), {})
        if k % 2 == 0:
            output, _ = finalise_coding(coding, store.reference_data(), settings, store=store,
                                        exclude_invoice_id=invoice_id)  # fmt: skip
            store.approve_invoice(invoice_id, output, "Ann")
            approved.append({"id": invoice_id, "final_output": output})
        elif k % 5 == 0:
            store.reject_invoice(invoice_id, "Ann", "a duplicate")
        else:
            left.append({"id": invoice_id, "final_output": inv["final_output"]})
    each_invoice("po case billed", store, left, js, numbers, setup=setup)
    from ap_coder.web.pages.purchase_orders import po_detail

    for order in store.purchase_orders():
        emit("po billing", setup=setup, po=order, detail=said(po_detail, store, order))
    return approved


def a_little_over(final: dict) -> dict:
    """``final`` billed 2.5% over: each line's price and amount, and the subtotal."""
    lines = [{**li, "unit_price": round((li.get("unit_price") or 0) * 1.025, 2),
              "amount": round((li.get("amount") or 0) * 1.025, 2)} for li in final["line_items"]]  # fmt: skip
    return {**final, "line_items": lines, "subtotal": round(sum(li["amount"] for li in lines), 2)}


def po_cases(work: Path, truth, app_settings, books: Path) -> list[tuple]:
    """The PO-matched set under each setup, as typed and as suppliers print the PO, then billed in a queue:
    (database, invoices, settings, lookup) of each, for the batches."""
    from ap_coder.bench import jde_po_vouchers as pv
    from ap_coder.coding.tax import build_gl_distribution
    from ap_coder.core.schema import InvoiceCoding
    from ap_coder.core.store import Store

    made = pv.cases(truth)
    numbers = {case.coding()["vendor_name"]: str(case.supplier.number) for case in made}
    sources = []
    for setup in pv.SETUPS:
        store = Store(work / f"po-{setup}" / "ap_coder.db")
        load_pos(store, pv.po_files(made))
        settings = (pv.pilot_settings if setup == pv.PILOT else pv.area_settings)(None)
        tax = tax_setup(qst_in_line=setup == pv.PILOT)
        for typed in (False, True):
            invoices = []
            for n, case in enumerate(made, 1):
                final = {**case.coding(), **({"po_number": TYPED[n % len(TYPED)].format(case.po)} if typed else {})}
                final["gl_distribution"] = build_gl_distribution(InvoiceCoding.model_validate(final), tax)
                invoices.append({"id": n, "final_output": final})
            each_invoice("po case", store, invoices, settings, numbers, setup=setup, typed=typed)
            sources.append((f"po {setup}{' typed' if typed else ''}", store, invoices, settings, by_name(numbers)))
        over = [{"id": n, "final_output": a_little_over(case.coding())} for n, case in enumerate(made, 1)]
        each_invoice("po case over", store, over, settings, numbers, setup=setup)  # (what the PO's tolerance lets by)
        queue = Store(copy_of(books, work / f"po-{setup}-billed" / "ap_coder.db"))
        load_pos(queue, pv.po_files(made))
        approved = billed(queue, sources[-2][2], app_settings, settings, numbers, setup)
        sources.append((f"po {setup} approved", queue, approved, settings, by_name(numbers)))
    return sources


def progress_cases(work: Path, truth) -> list[tuple]:
    from ap_coder.bench import jde_exports
    from ap_coder.bench import jde_po_vouchers as pv
    from ap_coder.coding.tax import build_gl_distribution
    from ap_coder.core.schema import InvoiceCoding
    from ap_coder.core.store import Store

    made = pv.progress_cases(truth)
    store = Store(work / "progress" / "ap_coder.db")
    lines = list({(pl.order, pl.line): pl for case in made for pl in case.po_lines}.values())
    load_pos(store, {pv.SUBCONTRACT_FILE: jde_exports.po_lines_report_xlsx(lines)})
    numbers = {case.coding()["vendor_name"]: str(case.supplier.number) for case in made}
    invoices = []
    for n, case in enumerate(made, 1):
        final = {**case.coding(), "progress_billing": case.progress()}
        final["gl_distribution"] = build_gl_distribution(InvoiceCoding.model_validate(final), tax_setup(False))
        invoices.append({"id": n, "final_output": final})
    sources = [("progress off", store, invoices, pv.pilot_settings(None), by_name(numbers))]
    for setup in pv.SETUPS:
        for tax_on in (pv.WHOLE, pv.NET):
            settings = pv.subcontract_settings(None, tax_on, setup)
            each_invoice("progress case", store, invoices, settings, numbers, setup=setup, tax_on=tax_on)
            sources.append((f"progress {setup} {tax_on}", store, invoices, settings, by_name(numbers)))
    return sources


def company(work: Path, settings) -> tuple:
    """The made-up company's database and its seven invoices as read (their coding as the queue proposes it)."""
    from ap_coder.core.store import Store
    from ap_coder.erp import jde_settings
    from ap_coder.ops.demo import load_company

    store = Store(work / "company" / "ap_coder.db")
    made = load_company(store, settings, on=ON)
    ids = sorted(inv_id for _, inv_id in made["invoices"].values())
    rows = store.get_invoices(ids)
    invoices = [{"id": inv["id"], "final_output": inv["ai_output"]} for inv in rows]
    lookup = store.vendor_ids()
    numbers = {inv["ai_output"].get("vendor_name") or "": lookup.get(inv["vendor_key"] or "", "") for inv in rows}
    js = jde_settings.load(store)
    each_invoice("company invoice", store, invoices, js, numbers)
    return made["truth"], ("company", store, invoices, js, lookup)


def bulk(name: str, store, settings, in_a_job: bool = False) -> None:
    """The queue's bulk approval: what it reads of each invoice, which are clean and why the others are not, what
    stops a Pay from account, then the clean ones approved (``in_a_job``: as the queue's job does, after a change of
    the policy notes, so each invoice's checks are done again first, finding what they found), and every invoice's
    status after."""
    from ap_coder.coding import bulk as bulk_mod
    from ap_coder.core.store import REVIEW

    if in_a_job:
        store.set_setting("policy_notes", "Checked again by the jde probe.")
    queue = store.list_invoices(REVIEW)
    ids = [inv["id"] for inv in queue]
    again = said(bulk_mod.issues_today, store, ids, store.reference_data(), settings)
    checks, earlier = bulk_mod.queue_checks(store, ids, settings, recheck=True)
    rechecked = said(bulk_mod.recheck_queue, store, settings)
    clean, why = bulk_mod.sort_for_bulk(store, queue, checks)
    stops = bulk_mod.pay_from_stops(store, queue, checks)
    facts = {inv["id"]: {k: v for k, v in checks.get(inv["id"], {}).items() if not k.endswith("_stamp")}
             for inv in queue}  # fmt: skip
    picked = [c["id"] for c in clean]
    if in_a_job:
        asked = [[i, list(store.row_identity(i) or []), stamp] for i, stamp in store.bulk_stamps(picked).items()]
        store.start_bulk_job("probe", {"name": "Ann", "login": ""}, asked, bulk_mod.JOB_FORMAT)
        job = store.claim_bulk_job("probe", 60.0, bulk_mod.JOB_FORMAT)
        ended = said(bulk_mod.approve_in_job, store, settings, job, "probe")
        done = store.bulk_job(job["id"]) or {}
        approved = {"ended": ended, "approved": done.get("approved"), "skipped": done.get("skipped")}
    else:
        approved = said(bulk_mod.bulk_approve, store, store.reference_data(), settings, picked, "Ann")
    statuses = {inv["id"]: inv["status"] for inv in store.list_invoices()}
    emit("bulk", db=name, again=again, earlier=earlier, rechecked=rechecked, facts=facts, clean=picked, why=why,
         stops=stops, approved=approved, statuses=statuses)  # fmt: skip


def main() -> None:
    n, work = int(sys.argv[1]), Path(sys.argv[2]).resolve()
    work.mkdir(parents=True, exist_ok=True)
    os.environ["AP_PRIVATE_DIR"] = str(work / "private")
    os.environ["AP_USER_SETTINGS"] = str(work / "user.json")
    os.environ["HOME"] = os.environ["USERPROFILE"] = str(work / "home")
    from ap_coder.core.config import Settings
    from ap_coder.core.store import Store
    from ap_coder.erp.jde_settings import CHARGES_GL, CHARGES_HOLD
    from ap_coder.ops.demo import load_demo

    settings = Settings()
    settings = replace(settings, llm=replace(settings.llm, provider="off"))  # no chat model is asked
    truth, ours = company(work, settings)
    sources = [ours, *po_cases(work, truth, settings, ours[1].path), *progress_cases(work, truth)]
    number = 0
    for name, store, invoices, js, lookup in sources:
        number += 1
        batch(f"batch {name}", store, invoices, js, lookup, number)
    for name, store, invoices, js, lookup in [s for s in sources if s[0] in ("company", "po pilot", "po areas")]:
        for k, each in enumerate(matrix(js)):
            number += 1
            batch(f"matrix {name} {k}", store, invoices, each, lookup, number)
    for k in range(n):
        rng = random.Random(f"jde batch {k}")
        name, store, invoices, js, lookup = rng.choice(sources)
        picked = sorted(rng.sample(invoices, rng.randint(1, min(5, len(invoices)))), key=lambda inv: inv["id"])
        charges = rng.choice((CHARGES_HOLD, CHARGES_GL))
        each = replace(rng.choice(matrix(js)), send_due_date=rng.random() < 0.7, po_charges=charges,
                       send_po_reference=rng.random() < 0.8)  # fmt: skip
        number += 1
        batch(f"seeded {name}", store, picked, each, lookup, number)
    bulk("company", ours[1], settings)
    demo = Store(work / "demo" / "ap_coder.db")
    load_demo(demo, settings)
    bulk("demo", demo, settings, in_a_job=True)


if __name__ == "__main__":
    main()
