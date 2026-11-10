"""GL coding from last year's journal: how often an invoice line gets the right JD Edwards account string when all AP
Coder knows of a supplier is how the department coded it last year (``python -m ap_coder.bench coding --journal``).

The made-up company of ``jde_company`` (its chart, its business units, its suppliers and a year of vouchers) is
loaded as a department would load it: the chart of accounts and the business units, the supplier master with the
supplier numbers, and the Transaction Journal of the first ten months, whose remarks are in the department's own
words ("courier charges 4410 Alder"). The last two months are held out: each of their vouchers of a trade supplier
comes back as an invoice whose lines are worded as a supplier prints them (``jde_names.INVOICE_WORDING``: "Ground
shipment waybill 77812"), and is coded by the code the app runs with no AI model (``offline_coder.code_from_capture``).

Every line is scored against the account string AP posted it to, by the kind of supplier:

* *one account*: every line of the supplier went to one account string;
* *several, by what was bought*: several accounts, each ``object.subsidiary`` on one business unit, so the words of
  the line can tell them apart;
* *several, by who ordered*: one ``object.subsidiary`` on several business units (or a billback): nothing printed on
  the invoice says which department ordered. The right outcome there is the account in the first three with why;
* *never seen*: no voucher of the supplier in the months loaded.

For each: the lines coded with no one picking and how many of those are right, the first candidate right, the right
account among the first three, and the first candidate's ``object.subsidiary`` and business unit each on their own.
"""

from __future__ import annotations

import argparse
import calendar
import contextlib
import datetime as dt
import hashlib
import json
import random
import tempfile
import time
from collections import Counter
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from ap_coder.bench import jde_company, jde_exports
from ap_coder.bench.harness import _table
from ap_coder.bench.jde_names import INVOICE_WORDING
from ap_coder.core import chart
from ap_coder.core.reference_data import UNASSIGNED

HOLD_OUT_MONTHS = 2  # the journal's last months, kept back and scored
ONE, BOUGHT, ORDERED, NEW = "one account", "several, by what was bought", "several, by who ordered", "never seen"
KINDS = (ONE, BOUGHT, ORDERED, NEW)
BILLBACK = "2480"  # other clearing: a purchase billed back to a region or an affiliate, worded as the purchase is
JOURNAL, HISTORY = "journal", "history"  # how last year's coding is taught (``simulate``)
SIZES = {"quick": "tenth", "full": "year"}


def invoice_wording() -> dict[tuple[str, str], tuple[str, ...]]:
    """{(object, subsidiary): what a supplier prints for it}; a subsidiary "-" stands for all of the object's."""
    found = {}
    for line in INVOICE_WORDING.strip().splitlines():
        head, _, phrasings = line.partition("|")
        obj, subsidiary = head.split()
        found[(obj, subsidiary.strip("-"))] = tuple(p.strip() for p in phrasings.split(";") if p.strip())
    return found


_WORDING = invoice_wording()


def _phrasings(account: jde_company.Account) -> tuple[str, ...]:
    return _WORDING.get((account.object, account.subsidiary)) or _WORDING.get((account.object, ""), ())


def invoice_text(truth: jde_company.Truth, supplier: jde_company.Supplier, account: str, on: dt.date,
                 rng: random.Random) -> str:  # fmt: skip
    """What ``supplier`` prints on its invoice for a purchase AP coded to ``account``. A supplier words a kind of
    purchase its own two ways, from one invoice to the next. A billback is worded as the purchase it bills back (the
    supplier's first other account): the invoice does not say who pays in the end."""
    row = truth.account(account)
    if row.object == BILLBACK and len(row.subsidiary) == 6:
        others = [a for a in supplier.accounts if truth.account(a).object != BILLBACK]
        row = truth.account(others[0]) if others else row
    phrasings = _phrasings(row) or (row.description,)
    house = random.Random(f"wording:{supplier.number}:{row.object}.{row.subsidiary}")
    mine = house.sample(phrasings, min(2, len(phrasings)))
    text = rng.choice(mine)
    return text.format(n=rng.randrange(10000, 99999), qty=rng.choice((2, 3, 4, 6, 12, 24, 40)),
                       month=calendar.month_name[on.month], who=row.description.rpartition(" - ")[2])  # fmt: skip


def supplier_kind(supplier: jde_company.Supplier, seen: set[int]) -> str:
    """Which of ``KINDS`` a trade supplier is: by its accounts, and whether the months loaded have a voucher of it."""
    if supplier.number not in seen:
        return NEW
    if len(supplier.accounts) == 1:
        return ONE
    pairs = [chart.split_account(account) for account in supplier.accounts]
    codes = [code for _, code in pairs]
    billed_back = any(chart.split_code(code)[0] == BILLBACK for code in codes)
    return ORDERED if billed_back or len(set(codes)) < len(codes) else BOUGHT


def hold_out_from(truth: jde_company.Truth) -> dt.date:
    """The first G/L date held out: the start of the year's last ``HOLD_OUT_MONTHS`` months."""
    return dt.date(truth.company.year, 12 - HOLD_OUT_MONTHS + 1, 1)


def _retired(supplier: jde_company.Supplier) -> bool:
    """A supplier the master marks as no longer used: its invoices are another check's subject."""
    return bool(chart.skip_word(supplier.name)) or "*" in supplier.name


def load_books(store: Any, truth: jde_company.Truth, taught: list[jde_company.Voucher], how: str = JOURNAL) -> None:
    """The made-up company in ``store`` as a department loads it: the business units, the chart of accounts (the
    reserved subsidiaries and the payable objects never offered, the main unit as the home business unit), the
    supplier master with its numbers, and the journal of the ``taught`` vouchers. ``how``: "journal" (the Transaction
    Journal in its own tables) or "history" (its coding lines as "past coding", the remark as the description: what
    AP Coder could be taught before it read a journal)."""
    from ap_coder.erp import journal as jr

    company = truth.company
    units = chart.bu_rows({"Job Number": u.number, "Job Name": u.name, "Co": company.number, "BU Ty": u.kind,
                           "Job Sts": u.status} for u in truth.business_units)  # fmt: skip
    rows = chart.chart_rows({"Business Unit": a.business_unit, "Object Account": a.object, "Subsidiary": a.subsidiary,
                             "Account Description": (" " * a.level + a.description)[:30], "L D": a.level,
                             "P E": a.posting_edit} for a in truth.accounts)  # fmt: skip
    store.import_business_units(units.rows)
    store.import_chart(rows.rows)
    never = (*(f"*.{subsidiary}" for subsidiary in company.reserved_subsidiaries), company.payable_object,
             company.intercompany_object, company.gst_object, company.pst_object)  # fmt: skip
    store.save_chart_settings(replace(store.chart_settings(), never_offered=never,
                                      home_business_unit=company.main_business_unit))  # fmt: skip
    store.import_vendor_master([{"vendor_name": s.name, "erp_id": str(s.number), "status": "active"}
                                for s in truth.suppliers], by_erp_id=True)  # fmt: skip
    data = jde_exports.journal_csv(replace(truth, vouchers=taught))
    objects = jr.PayableObjects((company.payable_object,), (company.intercompany_object,))
    journal = jr.read_journal(data, payable_objects=objects, tax_objects=(company.gst_object,))
    if how == JOURNAL:
        store.import_journal(journal, source_name="journal.csv", source_hash=hashlib.sha256(data).hexdigest())
        return
    names = {str(s.number): s.name for s in truth.suppliers}
    store.import_history([
        {"vendor_name": names.get(v.supplier_number.strip().lstrip("0"), v.supplier_name), "description": line.remark,
         "gl_code": chart.code_of(line.object, line.subsidiary), "cost_center": line.business_unit,
         "amount": line.amount, "date": v.gl_date}
        for v in journal.vouchers if not v.affiliate and v.balanced
        for line in v.lines if line.kind == jr.CODING and line.remark
    ])  # fmt: skip


def _ranked(store: Any, reference: Any, vendor: str, text: str,
            feedback: list[dict[str, Any]]) -> list[tuple[str, str]]:  # fmt: skip
    """The (business unit, object.subsidiary) suggested for a line, the best first (the first three are scored); the
    business unit is "" when the suggestion names none."""
    from ap_coder.ai.suggest import suggest_gl

    found = suggest_gl(text, vendor, feedback, reference, limit=3)
    return [("" if s.cost_center in ("", UNASSIGNED) else s.cost_center, s.gl_code) for s in found]


def simulate(seed: int = 1, size: str = "year", how: str = JOURNAL) -> list[dict[str, Any]]:
    """Every line of every held-out invoice of a trade supplier, with what was proposed and the truth."""
    from ap_coder.ai.offline_coder import code_from_capture
    from ap_coder.bench.coding import _capture
    from ap_coder.core.store import Store

    truth = jde_company.build(seed, size)
    start = hold_out_from(truth)
    taught = [v for v in truth.vouchers if v.gl_date < start]
    seen = {v.supplier for v in taught if v.oddity != "unbalanced"}
    rows: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="ap-coding-journal-") as tmp, contextlib.ExitStack() as held:
        store = Store(Path(tmp) / "coding.db")
        load_books(store, truth, taught, how)
        held.enter_context(store.session())
        reference = store.reference_data()
        for voucher in truth.vouchers:
            supplier = truth.supplier(voucher.supplier)
            if voucher.gl_date < start or supplier.kind != "trade" or _retired(supplier) or voucher.oddity:
                continue
            rng = random.Random(f"ap-bench-journal:{seed}:{voucher.document}")
            vendor = supplier.name.title()
            lines = [SimpleNamespace(text=invoice_text(truth, supplier, line.account, voucher.gl_date, rng),
                                     quantity=1.0, unit_price=float(line.amount), amount=float(line.amount))
                     for line in voucher.lines]  # fmt: skip
            feedback = store.learning_rows(vendor)
            began = time.perf_counter()
            captured = _capture(vendor, voucher.invoice_number, voucher.invoice_date, lines)
            coding = code_from_capture(captured, reference, feedback, store, text="").coding
            took = (time.perf_counter() - began) / len(lines)
            kind = supplier_kind(supplier, seen)
            for line, posted, li in zip(lines, voucher.lines, coding.line_items, strict=True):
                unit, code = chart.split_account(posted.account)
                coded = li.predicted_gl_code != UNASSIGNED
                proposed_unit = "" if li.predicted_cost_center == UNASSIGNED else li.predicted_cost_center
                proposed = chart.join_account(proposed_unit, li.predicted_gl_code) if coded else ""
                ranked = _ranked(store, reference, vendor, line.text, feedback)
                top = [chart.join_account(*pair) for pair in ranked]
                first = ranked[0] if ranked else ("", "")
                rows.append({
                    "supplier": supplier.number, "vendor": vendor, "kind": kind, "document": voucher.document,
                    "text": line.text, "truth": posted.account, "coded": coded, "proposed": proposed,
                    "coded_right": coded and proposed == posted.account, "top": top,
                    "first_right": top[:1] == [posted.account], "in_top3": posted.account in top[:3],
                    "code_right": bool(top) and first[1] == code, "unit_right": bool(top) and first[0] == unit,
                    "why": li.reasoning_justification, "seconds": took,
                })  # fmt: skip
    return rows


def line_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    count = Counter()
    for r in rows:
        count.update(k for k in ("coded", "coded_right", "first_right", "in_top3", "code_right", "unit_right") if r[k])
    share = {k: (count[k] / n if n else 0.0) for k in ("coded", "first_right", "in_top3", "code_right", "unit_right")}
    return {"lines": n, "suppliers": len({r["supplier"] for r in rows}), "coded": count["coded"],
            "coded_right": count["coded_right"], "coded_share": share["coded"],
            "coded_right_share": count["coded_right"] / count["coded"] if count["coded"] else 0.0,
            "first_right": share["first_right"], "in_top3": share["in_top3"], "code_right": share["code_right"],
            "unit_right": share["unit_right"]}  # fmt: skip


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_kind = {kind: line_stats([r for r in rows if r["kind"] == kind]) for kind in KINDS}
    seconds = sorted(r["seconds"] for r in rows)
    return {"all": line_stats(rows), "by_kind": by_kind,
            "ms_a_line": round(1000 * seconds[len(seconds) // 2], 2) if seconds else 0.0}  # fmt: skip


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def table(report: dict[str, Any]) -> str:
    """The scores by kind of supplier, as a markdown table."""
    head = ["Held-out lines", "lines (suppliers)", "coded alone", "of those right", "first candidate right",
            "in the first 3", "object.subsidiary right", "business unit right"]  # fmt: skip
    body = []
    for name, s in [*report["summary"]["by_kind"].items(), ("all", report["summary"]["all"])]:
        body.append([name, f"{s['lines']} ({s['suppliers']})", _pct(s["coded_share"]),
                     f"{_pct(s['coded_right_share'])} ({s['coded_right']}/{s['coded']})" if s["coded"] else "-",
                     _pct(s["first_right"]), _pct(s["in_top3"]), _pct(s["code_right"]),
                     _pct(s["unit_right"])])  # fmt: skip
    return _table(head, body)


def render_markdown(report: dict[str, Any]) -> str:
    cfg = report["config"]
    taught = ("the Transaction Journal, loaded as a journal" if cfg["how"] == JOURNAL else
              "the journal's coding lines taught as past coding (the remark as the description)")  # fmt: skip
    return "\n".join([
        "# GL coding from last year's journal\n",
        f"The made-up company, seed {cfg['seed']}, size {cfg['size']}: {taught}; the last {HOLD_OUT_MONTHS} months "
        "held out and coded from invoices in the suppliers' own wording, no AI model. The first candidate is right "
        "when both its object.subsidiary and its business unit are.\n",
        table(report),
        f"\nMedian time to code a line: {report['summary']['ms_a_line']} ms.",
    ])  # fmt: skip


def run_journal(
    seed: int = 1, size: str = "year", out: str | Path = "bench_coding", how: str = JOURNAL
) -> dict[str, Any]:
    """Run the hold-out and write ``report.json``, ``report.md`` and ``lines.jsonl`` in ``out``."""
    rows = simulate(seed, size, how)
    report = {"config": {"seed": seed, "size": size, "how": how, "hold_out_months": HOLD_OUT_MONTHS},
              "summary": summarize(rows)}  # fmt: skip
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out / "report.md").write_text(render_markdown(report) + "\n", encoding="utf-8")
    with (out / "lines.jsonl").open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return report


class _Journal(argparse.Action):
    """``--journal``: the coding bench runs this realism (``run_command``) instead of the vendors' own wording."""

    def __call__(self, parser: Any, namespace: Any, values: Any, option_string: str | None = None) -> None:
        namespace.journal, namespace.run = True, run_command


def add_parser(sub: Any) -> None:
    """The options of ``python -m ap_coder.bench coding --journal`` (on the coding bench's own parser)."""
    coding = sub.choices["coding"]
    coding.add_argument("--journal", action=_Journal, nargs=0, default=False,
                        help="history is last year's journal in the department's words, invoices in the suppliers': "
                             "the last two months held out (--quick: a quarter of a year)")  # fmt: skip
    coding.add_argument("--taught", choices=(JOURNAL, HISTORY), default=JOURNAL,
                        help="with --journal: the journal loaded as a journal (default), or its lines taught as past "
                             "coding with the remark as the description (what could be done before)")  # fmt: skip


def run_command(a: Any) -> int:
    size = SIZES["quick" if a.quick else "full"]
    out = "bench_coding_journal" if a.out == "bench_coding" else a.out
    report = run_journal(a.seed, size, out, a.taught)
    print(table(report))
    print(f"median {report['summary']['ms_a_line']} ms a line; report: {Path(out) / 'report.md'}")
    return 0
