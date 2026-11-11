"""GL coding from last year's journal: how often an invoice line gets the right JD Edwards account string when all AP
Coder knows of a supplier is how the department coded it last year (``python -m ap_coder.bench coding --journal``).

The made-up company of ``jde_company`` (its chart, its business units, its suppliers and a year of vouchers) is
loaded as a department would load it: the chart of accounts and the business units, the supplier master with the
supplier numbers, and the Transaction Journal of the first ten months, whose remarks are in the department's own
words ("courier charges 4410 Alder"). The last two months are held out: each of their vouchers of a trade supplier
comes back as an invoice whose lines are worded as a supplier prints them (``jde_names.INVOICE_WORDING``: "Ground
shipment waybill 77812"), and is coded by the code the app runs with no AI model (``offline_coder.code_from_capture``).

Every line is scored against the account string AP posted it to, by the kind of vendor (the suppliers that go by one
name are one vendor, as the vendor list keeps them: the made-up company has more of those than a real one):

* *one account*: every line went to one account string;
* *several, by what was bought*: several accounts, each ``object.subsidiary`` on one business unit, so the words of
  the line can tell them apart;
* *several, by who ordered*: one ``object.subsidiary`` on several business units (or a billback): nothing printed on
  the invoice says which department ordered. The right outcome there is the account in the first three with why;
* *never seen*: no voucher of the vendor in the months loaded.

For each: the lines coded with no one picking and how many of those are right, the first candidate right, the right
account among the first three, the right account kept with the line (coded, or among the accounts a line left keeps
for a person to pick, D-226), and the first candidate's ``object.subsidiary`` and business unit each on their own.
``--approve`` then has AP approve each held-out invoice with its true accounts as it comes, so the next one of the
vendor is coded with them: how fast the vendor's own approvals take over from last year's journal.
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
from ap_coder.bench.jde_names import invoice_wording
from ap_coder.core import chart
from ap_coder.core.memory import vendor_key
from ap_coder.core.reference_data import UNASSIGNED

HOLD_OUT_MONTHS = 2  # the journal's last months, kept back and scored
ONE, BOUGHT, ORDERED, NEW = "one account", "several, by what was bought", "several, by who ordered", "never seen"
KINDS = (ONE, BOUGHT, ORDERED, NEW)
BILLBACK = "2480"  # other clearing: a purchase billed back to a region or an affiliate, worded as the purchase is
JOURNAL, HISTORY = "journal", "history"  # how last year's coding is taught (``simulate``)
SIZES = {"quick": "tenth", "full": "year"}
REVIEWER = "AP clerk (benchmark)"
# The sure rule tried at other bars (``sure_grid``): the share one account has, and the lines the supplier has.
GRID_SHARES, GRID_LINES = (0.8, 0.9, 0.95, 1.0), (2, 3, 5)
SHARES = ("coded", "first_right", "in_top3", "kept", "code_right", "unit_right")  # a line's scores, as shares


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


def vendor_kind(accounts: list[str], seen: bool) -> str:
    """Which of ``KINDS`` a vendor is: by the accounts its suppliers are coded to, and whether the months loaded have
    a voucher of it."""
    if not seen:
        return NEW
    if len(accounts) == 1:
        return ONE
    codes = [chart.split_account(account)[1] for account in accounts]
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


def _vendors(truth: jde_company.Truth, seen: set[int]) -> dict[int, str]:
    """{supplier number: the kind of vendor it is}. The trade suppliers that go by one name (legal forms aside) are
    one vendor in the vendor list, so they are one here: their accounts together."""
    groups: dict[str, list[jde_company.Supplier]] = {}
    for supplier in truth.suppliers:
        if supplier.kind == "trade":
            groups.setdefault(vendor_key(supplier.name), []).append(supplier)
    kinds = {}
    for members in groups.values():
        accounts = list(dict.fromkeys(a for s in members for a in s.accounts))
        kind = vendor_kind(accounts, any(s.number in seen for s in members))
        kinds.update({s.number: kind for s in members})
    return kinds


def _ranked(store: Any, reference: Any, vendor: str, text: str, feedback: list[dict[str, Any]]) -> list[Any]:
    """The accounts offered for a line, the likeliest first, as the line's coding worked them out."""
    from ap_coder.ai.offline_coder import supplier_evidence
    from ap_coder.ai.suggest import candidates

    found = store.find_vendor(vendor)
    key = (found or {}).get("vendor_key") or vendor_key(vendor)
    prior, units = supplier_evidence(store, found or vendor, key, reference)
    return candidates(text, vendor, feedback, reference, 3, (found or {}).get("default_gl") or "", key, prior, units)


def _approve(store: Any, tmp: str, coding: Any, voucher: jde_company.Voucher) -> None:
    """AP approves the invoice with the accounts it was posted to: what the vendor's next invoice learns from."""
    output = coding.model_dump(mode="json")
    invoice_id = store.add_invoice(Path(tmp) / f"{voucher.document}.pdf", output, None)
    final = json.loads(json.dumps(output))
    for li, posted in zip(final["line_items"], voucher.lines, strict=True):
        li["predicted_cost_center"], li["predicted_gl_code"] = chart.split_account(posted.account)
    store.approve_invoice(invoice_id, final, REVIEWER)


def _scored(line: Any, posted: jde_company.CodingLine, li: Any, top: list[Any]) -> dict[str, Any]:
    """One line's score: what was proposed and offered against the account it was posted to."""
    unit, code = chart.split_account(posted.account)
    coded = li.predicted_gl_code != UNASSIGNED
    proposed_unit = "" if li.predicted_cost_center == UNASSIGNED else li.predicted_cost_center
    proposed = chart.join_account(proposed_unit, li.predicted_gl_code) if coded else ""
    accounts = [c.account for c in top]
    first = top[0] if top else None
    return {
        "text": line.text, "truth": posted.account, "coded": coded, "proposed": proposed,
        "coded_right": coded and proposed == posted.account, "top": accounts,
        "first_right": accounts[:1] == [posted.account], "in_top3": posted.account in accounts[:3],
        # coded right, or the account among those the line keeps for a person to pick (``LineItem.candidates``)
        "kept": (coded and proposed == posted.account) or posted.account in [c.get("account") for c in li.candidates],
        "code_right": first is not None and first.code == code,
        "unit_right": first is not None and first.business_unit == unit,
        "why": li.reasoning_justification, "source": (li.decided_by or {}).get("source", ""),
        # the share and the lines the supplier's first account has, when it is the first offered (``sure_grid``)
        "share": round(first.share, 4) if first is not None and first.source == "supplier" else 0.0,
        "of": first.of if first is not None and first.source == "supplier" else 0,
    }  # fmt: skip


def _remark(store: Any, reference: Any, vendor: str, coding: Any, li: Any,
            posted: jde_company.CodingLine) -> dict[str, Any]:  # fmt: skip
    """The remark suggested for a line once it has its true account, against the one the department keyed (D-213):
    whether it has the same shape (``supplier_prior.remark_shape``) and whether it is exactly it. {} for a line
    keyed with no remark."""
    from ap_coder.ai.supplier_prior import prior_for, remark_shape
    from ap_coder.coding.remark import suggest_remark

    if not posted.remark.strip():
        return {}
    prior = prior_for(store, store.find_vendor(vendor) or vendor, always=reference.pairs is not None)
    text, why = suggest_remark(li, coding, prior, posted.account, reference)
    return {"remark_keyed": posted.remark, "remark": text, "remark_why": why,
            "remark_shape": remark_shape(text) == remark_shape(posted.remark),
            "remark_exact": text == posted.remark.strip()}  # fmt: skip


def simulate(seed: int = 1, size: str = "year", how: str = JOURNAL, approve: bool = False) -> list[dict[str, Any]]:
    """Every line of every held-out invoice of a trade supplier, with what was proposed and the truth. ``approve``:
    AP approves each invoice with its true accounts before the next one is read."""
    from ap_coder.ai.offline_coder import code_from_capture
    from ap_coder.bench.coding import _capture
    from ap_coder.core.store import Store

    truth = jde_company.build(seed, size)
    start = hold_out_from(truth)
    taught = [v for v in truth.vouchers if v.gl_date < start]
    kinds = _vendors(truth, {v.supplier for v in taught if v.oddity != "unbalanced"})
    met: set[str] = set()
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
            for line, posted, li in zip(lines, voucher.lines, coding.line_items, strict=True):
                top = _ranked(store, reference, vendor, line.text, feedback)
                rows.append({"supplier": supplier.number, "vendor": vendor, "kind": kinds[supplier.number],
                             "document": voucher.document, **_scored(line, posted, li, top), "seconds": took,
                             "first_of_vendor": vendor not in met,
                             **_remark(store, reference, vendor, coding, li, posted)})  # fmt: skip
            met.add(vendor)
            if approve:
                _approve(store, tmp, coding, voucher)
    return rows


def line_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(rows)
    count = Counter()
    for r in rows:
        count.update(k for k in (*SHARES, "coded_right") if r[k])
    share = {k: (count[k] / n if n else 0.0) for k in SHARES}
    return {"lines": n, "suppliers": len({r["supplier"] for r in rows}), "coded": count["coded"],
            "coded_right": count["coded_right"], "coded_share": share["coded"],
            "coded_right_share": count["coded_right"] / count["coded"] if count["coded"] else 0.0,
            "first_right": share["first_right"], "in_top3": share["in_top3"], "kept": share["kept"],
            "code_right": share["code_right"],
            "unit_right": share["unit_right"]}  # fmt: skip


def sure_grid(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What coding a line alone would do at other bars than ``supplier_prior.SURE_SHARE`` and ``SURE_LINES``: for
    each share and number of lines, the lines the supplier's first account would code, and how many wrongly."""
    grid = []
    for share in GRID_SHARES:
        for lines in GRID_LINES:
            would = [r for r in rows if r["of"] >= lines and r["share"] >= share]
            grid.append({"share": share, "lines": lines, "coded": len(would),
                         "wrong": sum(1 for r in would if not r["first_right"])})  # fmt: skip
    return grid


def _median_ms(seconds: list[float]) -> float:
    return round(1000 * sorted(seconds)[len(seconds) // 2], 2) if seconds else 0.0


def remark_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Of the lines keyed with a remark: the share suggested a remark of the same shape, and the very remark."""
    keyed = [r for r in rows if r.get("remark_keyed")]
    n = len(keyed)
    return {"lines": n, "same_shape": sum(1 for r in keyed if r["remark_shape"]) / n if n else 0.0,
            "exact": sum(1 for r in keyed if r["remark_exact"]) / n if n else 0.0}  # fmt: skip


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_kind = {kind: line_stats([r for r in rows if r["kind"] == kind]) for kind in KINDS}
    return {"all": line_stats(rows), "by_kind": by_kind, "sure_grid": sure_grid(rows),
            "remarks": remark_stats(rows),
            "ms_a_line": _median_ms([r["seconds"] for r in rows]),
            "ms_a_line_first": _median_ms([r["seconds"] for r in rows if r["first_of_vendor"]]),
            "ms_a_line_after": _median_ms([r["seconds"] for r in rows if not r["first_of_vendor"]]),
            "ms_a_line_most": round(1000 * max((r["seconds"] for r in rows), default=0.0), 2)}  # fmt: skip


def _pct(x: float) -> str:
    return f"{100 * x:.0f}%"


def table(report: dict[str, Any]) -> str:
    """The scores by kind of vendor, as a markdown table."""
    head = ["Held-out lines", "lines (suppliers)", "coded alone", "of those right", "first candidate right",
            "in the first 3", "kept with the line", "object.subsidiary right", "business unit right"]  # fmt: skip
    body = []
    for name, s in [*report["summary"]["by_kind"].items(), ("all", report["summary"]["all"])]:
        body.append([name, f"{s['lines']} ({s['suppliers']})", _pct(s["coded_share"]),
                     f"{_pct(s['coded_right_share'])} ({s['coded_right']}/{s['coded']})" if s["coded"] else "-",
                     _pct(s["first_right"]), _pct(s["in_top3"]), _pct(s["kept"]), _pct(s["code_right"]),
                     _pct(s["unit_right"])])  # fmt: skip
    return _table(head, body)


def grid_table(report: dict[str, Any]) -> str:
    """The sure rule at other bars: lines coded alone and, of those, wrong."""
    grid = {(g["share"], g["lines"]): g for g in report["summary"]["sure_grid"]}
    head = ["one account's share", *(f"{n} lines or more" for n in GRID_LINES)]
    body = [[f"{_pct(share)} or more", *(f"{grid[(share, n)]['coded']} coded, {grid[(share, n)]['wrong']} wrong"
                                         for n in GRID_LINES)] for share in GRID_SHARES]  # fmt: skip
    return _table(head, body)


def render_markdown(report: dict[str, Any]) -> str:
    cfg, s = report["config"], report["summary"]
    taught = ("the Transaction Journal, loaded as a journal" if cfg["how"] == JOURNAL else
              "the journal's coding lines taught as past coding (the remark as the description)")  # fmt: skip
    approving = (" AP approves each held-out invoice with its true accounts before the next one is read."
                 if cfg.get("approve") else "")  # fmt: skip
    return "\n".join([
        "# GL coding from last year's journal\n",
        f"The made-up company, seed {cfg['seed']}, size {cfg['size']}: {taught}; the last {HOLD_OUT_MONTHS} months "
        "held out and coded from invoices in the suppliers' own wording, no AI model. The first candidate is right "
        f"when both its object.subsidiary and its business unit are.{approving}\n",
        table(report),
        "\n## The sure rule at other bars\n",
        "A line is coded with no one picking when one account has a share of the supplier's lines, of a number of "
        "lines at least (``supplier_prior.SURE_SHARE``, ``SURE_LINES``). Counted on the lines whose first candidate "
        "is the supplier's own:\n",
        grid_table(report),
        f"\nTime to code a line (median): {s['ms_a_line_first']} ms on a vendor's first invoice of the run, "
        f"{s['ms_a_line_after']} ms after; the slowest line {s['ms_a_line_most']} ms.",
        f"\nThe remark suggested for a line with its true account (``coding/remark.py``), on the "
        f"{s['remarks']['lines']} held-out lines keyed with one: the same shape as the department's "
        f"{_pct(s['remarks']['same_shape'])}, exactly its remark {_pct(s['remarks']['exact'])} (the made-up "
        "department picks the office and the wording at random, so the exact figure is a floor).",
    ])  # fmt: skip


def run_journal(seed: int = 1, size: str = "year", out: str | Path = "bench_coding", how: str = JOURNAL,
                approve: bool = False) -> dict[str, Any]:  # fmt: skip
    """Run the hold-out and write ``report.json``, ``report.md`` and ``lines.jsonl`` in ``out``."""
    rows = simulate(seed, size, how, approve)
    report = {"config": {"seed": seed, "size": size, "how": how, "approve": approve,
                         "hold_out_months": HOLD_OUT_MONTHS},
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
    coding.add_argument(
        "--approve",
        action="store_true",
        help="with --journal: AP approves each held-out invoice with its true accounts as it comes",
    )


def run_command(a: Any) -> int:
    size = SIZES["quick" if a.quick else "full"]
    out = "bench_coding_journal" if a.out == "bench_coding" else a.out
    report = run_journal(a.seed, size, out, a.taught, a.approve)
    s = report["summary"]
    print(table(report))
    print(f"median {s['ms_a_line_first']} ms a line on a vendor's first invoice, {s['ms_a_line_after']} ms after; "
          f"report: {Path(out) / 'report.md'}")  # fmt: skip
    return 0
