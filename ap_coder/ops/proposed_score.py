"""What AP Coder proposed for real past invoices against the vouchers keyed for them
(``python -m ap_coder pilot-score --proposed --vouchers HEADERS.xlsx [--journal FILE]``).

Past invoices are dropped into AP Coder and read; nobody needs to approve them. Each invoice here (in review, parked
or approved) is scored on what AP Coder proposed before a person changed anything, against the voucher keyed for it:

1. invoice -> its row of JD Edwards' voucher list (``pilot_score.match``): supplier and invoice number, else supplier
   and gross amount with the closest invoice date;
2. that voucher -> its voucher in the Transaction Journal (the file given, else the journal loaded here): the same
   supplier number, the same G/L date, payable lines adding up to its gross. Two that fit: the one whose tax line is
   the invoice's recovered tax; still two (and not coded alike): listed as "could be either", not scored;
3. the coding BY ACCOUNT, not line by line (``pilot_score.by_account``): AP Coder's amounts added up per account
   string against the journal's coding lines added up per account. One verdict a voucher (``VERDICTS``);
4. then line by line, for the pilot's gate (``journal_score.gate``): each invoice line paired with a keyed coding line
   (``keyed_lines``), and whether AP Coder coded it alone (``LineItem.decided_by``), right, and offered the keyed
   account first or among its first three (``LineItem.candidates``). A line that cannot be paired is counted and
   why is said;
5. the header fields as ``pilot-score`` compares them, and the journal's tax line against the invoice's recovered
   tax.

A voucher scored that is in the journal loaded here is counted and said: AP Coder learns from that journal, so it may
have seen how the invoice was keyed before it proposed anything.

Writes ``proposed_score.md``, ``proposed_score_misses.csv`` (both sides of every voucher not wholly right) and
``proposed_score.json`` (the figures, no names) into ``pilot_score`` in the data folder.
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
from pathlib import Path
from typing import Any

from ..bench.harness import _table
from ..core import chart
from ..core.reference_data import UNASSIGNED
from ..core.safe import csv_row
from ..core.store.journal import supplier_key
from ..core.ui import plural
from ..erp import jde_settings
from ..erp import journal as jr
from . import pilot_score as ps
from .journal_score import account_of, gate, gate_table, the_journal
from .journal_score import share_of as _of

REPORT, MISSES, FIGURES = "proposed_score.md", "proposed_score_misses.csv", "proposed_score.json"
RIGHT, AMOUNTS, LEFT, UNIT, WRONG = (
    "every account and amount right", "accounts right, amounts differ", "a line left to a person, no account wrong",
    "object and subsidiary right, a business unit wrong", "an account wrong",
)  # fmt: skip
VERDICTS = (RIGHT, AMOUNTS, LEFT, UNIT, WRONG)
SAID = {RIGHT: "every account and amount it proposed is the one keyed",
        AMOUNTS: "the accounts are right and the amounts differ",
        LEFT: "it left a line or more to a person and put no wrong account in",
        UNIT: "the object and subsidiary are right and a business unit is not",
        WRONG: "an account is wrong"}  # fmt: skip
NO_VOUCHER, NO_JOURNAL, EITHER = (
    "no voucher of the voucher list", "its voucher is not in the journal", "could be either of two journal vouchers",
)  # fmt: skip
FOREIGN_CENTS = 0.05  # a foreign voucher's amounts, turned into dollars at the voucher's own rate, may round apart
# Why an invoice line is left out of the line figures: no keyed line of its own to compare it with (``keyed_lines``)
LINE_EITHER = "keyed lines of its amount are on different accounts"
LINE_NONE = "no keyed line has its amount (AP added lines together or split one when keying, or the amounts differ)"
NOT_ALONE = ("model", "person")  # what decided a line's account when it was not coded alone: a guess, a person
MISS_COLUMNS = ["ap_coder_id", "supplier", "supplier_number", "invoice_number", "jde_document", "what", "ap_coder",
                "keyed", "note"]  # fmt: skip


# --- One invoice against its voucher --------------------------------------------------------------------------------


def proposed_accounts(o: ps.Ours, rate: float = 1.0) -> tuple[dict[str, float], list[dict[str, Any]], float]:
    """(AP Coder's amounts added up per account string, the lines it left to a person, the tax it recovers), in the
    company's currency (``rate``: a foreign voucher's)."""
    coded, left, recovered = [], [], 0.0
    for line in o.lines:
        amount = round(line["amount"] * rate, 2)
        if line["recoverable_tax"]:
            recovered = round(recovered + amount, 2)
        elif not line["gl"] or line["gl"].upper() == UNASSIGNED:
            left.append({**line, "amount": amount})
        else:
            unit, obj, sub = line["account"] or (line["cc"], *chart.split_code(line["gl"]))
            coded.append((chart.join_account(unit, chart.code_of(obj, sub)), amount))
    return ps.by_account(coded), left, recovered


def keyed_accounts(voucher: jr.Voucher) -> dict[str, float]:
    """The journal voucher's coding lines added up per account string."""
    return ps.by_account((account_of(line), line.amount) for line in voucher.lines if line.kind == jr.CODING)


def verdict(coded: dict[str, float], left: list[Any], keyed: dict[str, float], cents: float = ps.CENT) -> str:
    """Which of ``VERDICTS`` a voucher is: AP Coder's accounts and amounts against the ones keyed."""
    codes = {chart.split_account(account)[1] for account in keyed}
    if any(chart.split_account(account)[1] not in codes for account in coded):
        return WRONG
    if any(account not in keyed for account in coded):
        return UNIT
    if left:
        return LEFT
    if any(account not in coded for account in keyed):  # keyed over more accounts: wrong unless only a unit's split
        ours = {chart.split_account(account)[1] for account in coded}
        return AMOUNTS if codes <= ours else WRONG
    return RIGHT if all(abs(coded[account] - keyed[account]) <= cents for account in keyed) else AMOUNTS


def _sides(amounts: dict[str, float], left: list[dict[str, Any]] = ()) -> str:
    said = [f"{account} {amount:.2f}" for account, amount in amounts.items()]
    said += [f"(left to a person: {line['description']} {line['amount']:.2f})" for line in left]
    return "; ".join(said)


def journal_voucher(p: ps.Posted, free: list[jr.Voucher], recovered: float | None) -> tuple[jr.Voucher | None, str]:
    """(the journal's voucher of a voucher of the list, taken out of ``free``; else why there is none). ``recovered``:
    the invoice's recovered tax in the company's currency, which decides between two."""
    number = supplier_key(p.an8)
    fits = [v for v in free if number and supplier_key(v.supplier_number) == number
            and (not p.gl_date or v.gl_date == p.gl_date) and ps._close(v.gross, p.domestic_gross)]  # fmt: skip
    if len(fits) > 1 and recovered is not None:
        fits = [v for v in fits if ps._close(v.tax, recovered)] or fits
    if len(fits) > 1 and any(keyed_accounts(v) != keyed_accounts(fits[0]) for v in fits[1:]):
        return None, EITHER
    if not fits:
        return None, NO_JOURNAL
    free[:] = [v for v in free if v is not fits[0]]
    return fits[0], ""


def _string(account: tuple[str, str, str]) -> str:
    unit, obj, sub = account
    return chart.join_account(unit, chart.code_of(obj, sub))


def keyed_lines(o: ps.Ours, voucher: jr.Voucher, rate: float = 1.0,
                cents: float = ps.CENT) -> list[tuple[dict[str, Any], str, str]]:  # fmt: skip
    """(each line of the invoice, the account string keyed for it, else "" and why there is none: ``LINE_EITHER``,
    ``LINE_NONE``). A line is paired with a keyed coding line of its amount (``rate``: a foreign voucher's, as
    ``proposed_accounts``) when the keyed lines of that amount are all on one account; then the lines left with the
    keyed lines left, when those are all on one account (lines AP keyed as one). The tax lines take part, not
    returned."""
    ours = [line for line in o.lines if not line["recoverable_tax"]]
    free = [(account_of(line), line.amount) for line in voucher.lines if line.kind == jr.CODING]

    def fits(line: dict[str, Any]) -> list[tuple[str, float]]:
        return [k for k in free if abs(k[1] - round(line["amount"] * rate, 2)) <= cents]

    found: dict[int, tuple[str, str]] = {}
    for n, line in enumerate(ours):
        same = fits(line)
        if same and len({account for account, _ in same}) == 1:
            free.remove(same[0])
            found[n] = (same[0][0], "")
    rest = {account for account, _ in free}
    for n, line in enumerate(ours):
        if n not in found:
            either = len({account for account, _ in fits(line)}) > 1
            found[n] = (next(iter(rest)), "") if len(rest) == 1 else ("", LINE_EITHER if either else LINE_NONE)
    return [(line, *found[n]) for n, line in enumerate(ours) if line["kind"] == "expense"]


def line_row(line: dict[str, Any], keyed: str, why: str) -> dict[str, Any]:
    """One invoice line as ``journal_score.gate`` counts it: coded alone (what decided its account, as proposed, is
    neither a guess nor a person, nor nothing) and right; the keyed account first or among the first three of the
    accounts offered (its own account, then its ``candidates``)."""
    coded = bool(line["gl"]) and line["gl"].upper() != UNASSIGNED
    mine = _string(line["account"] or (line["cc"], *chart.split_code(line["gl"]))) if coded else ""
    alone = coded and line["decided"] not in ("", *NOT_ALONE)
    offered = list(dict.fromkeys([*([mine] if mine else []), *(_string(c) for c in line["candidates"])]))
    return {"coded": alone, "left_out": why, "coded_right": alone and not why and mine == keyed,
            "first_right": not why and offered[:1] == [keyed], "in_top3": not why and keyed in offered[:3]}  # fmt: skip


# --- The score ------------------------------------------------------------------------------------------------------


def score(ours: list[ps.Ours], posted: list[ps.Posted], journal: jr.Journal, domestic: str = "CAD",
          notes: list[str] | None = None) -> dict[str, Any]:  # fmt: skip
    """The figures, the misses and the invoices not scored (see the module's docstring)."""
    pairs, ours_only, _ = ps.match(ours, posted)
    free = list(journal.vouchers)
    header = {name: {"n": 0, "right": 0} for name in ps.HEADER_FIELDS if name != "tax_total"}
    tax = {"n": 0, "right": 0}
    lines = {"coded": 0, "right": 0, "unit_wrong": 0, "wrong": 0, "left": 0}
    rows: list[dict[str, Any]] = []  # a row for each invoice line, for the gate (``line_row``)
    vouchers = dict.fromkeys(VERDICTS, 0)
    suppliers: dict[str, dict[str, Any]] = {}
    units: dict[str, dict[str, Any]] = {}
    misses: list[dict[str, Any]] = []
    matched: list[jr.Voucher] = []
    unmatched = [{"id": o.id, "supplier": o.vendor_name, "invoice_number": o.invoice_number, "total": o.grand_total,
                  "why": NO_VOUCHER} for o in ours_only]  # fmt: skip

    def miss(o: ps.Ours, p: ps.Posted, what: str, mine: Any, keyed: Any, note: str = "") -> None:
        misses.append({"ap_coder_id": o.id, "supplier": o.vendor_name or p.vendor_name, "supplier_number": p.an8,
                       "invoice_number": o.invoice_number, "jde_document": p.document, "what": what,
                       "ap_coder": ps._shown(mine), "keyed": ps._shown(keyed), "note": note})  # fmt: skip

    for o, p, how in pairs:
        for name in header:
            theirs = ps._posted_value(p, name)
            if theirs in (None, "") or (name == "due_date" and not o.due_date):
                continue  # not in the list, or no due date to send (JD Edwards works it out from the terms)
            header[name]["n"] += 1
            if ps.header_right(name, ps._ours_value(o, name), theirs):
                header[name]["right"] += 1
            else:
                miss(o, p, ps.HEADER_FIELDS[name], ps._ours_value(o, name), theirs,
                     "matched by supplier and amount" if how != "supplier and invoice number" else "")  # fmt: skip
        foreign = bool(p.currency) and p.currency != domestic.upper() and p.gross and p.domestic_gross
        rate = p.domestic_gross / p.gross if foreign else 1.0
        coded, left, recovered = proposed_accounts(o, rate)
        voucher, why = journal_voucher(p, free, recovered)
        if voucher is None:
            unmatched.append({"id": o.id, "supplier": o.vendor_name, "invoice_number": o.invoice_number,
                              "total": o.grand_total, "why": why})  # fmt: skip
            continue
        matched.append(voucher)
        keyed = keyed_accounts(voucher)
        found = verdict(coded, left, keyed, FOREIGN_CENTS if foreign else ps.CENT)
        vouchers[found] += 1
        if found != RIGHT:
            rate_said = f"in {p.currency}, compared in {domestic.upper()} at the voucher's rate" if foreign else ""
            miss(o, p, found, _sides(coded, left), _sides(keyed), rate_said)
        tax["n"] += 1
        if ps._close(recovered, voucher.tax):
            tax["right"] += 1
        else:
            miss(o, p, "Recovered tax (the journal's tax line)", recovered, voucher.tax)
        rows += [line_row(*one) for one in keyed_lines(o, voucher, rate, FOREIGN_CENTS if foreign else ps.CENT)]
        codes = {chart.split_account(account)[1] for account in keyed}
        for line in o.lines:
            if line["recoverable_tax"]:
                continue
            if not line["gl"] or line["gl"].upper() == UNASSIGNED:
                lines["left"] += 1
                continue
            unit, obj, sub = line["account"] or (line["cc"], *chart.split_code(line["gl"]))
            code = chart.code_of(obj, sub)
            lines["coded"] += 1
            where = "right" if chart.join_account(unit, code) in keyed else "unit_wrong" if code in codes else "wrong"
            lines[where] += 1
        s = suppliers.setdefault(p.an8 or o.vkey, {"number": p.an8, "name": o.vendor_name or p.vendor_name,
                                                   "vouchers": 0, "right": 0})  # fmt: skip
        s["vouchers"] += 1
        s["right"] += found == RIGHT
        for account in keyed:
            u = units.setdefault(chart.split_account(account)[0], {"accounts": 0, "right": 0})
            u["accounts"] += 1
            u["right"] += account in coded
    scored = sum(vouchers.values())
    return {
        "invoices": {
            "ap_coder": len(ours), "in_review": sum(1 for o in ours if o.status != "approved"),
            "in_voucher_list": len(pairs),
            "matched_by_amount": sum(1 for *_, how in pairs if how != "supplier and invoice number"),
            "scored": scored, "not_scored": len(unmatched),
        },
        "gate": gate(rows), "vouchers": vouchers, "lines": lines, "header": header, "tax": tax,
        "by_supplier": sorted(suppliers.values(), key=lambda s: (s["right"] - s["vouchers"], -s["vouchers"])),
        "by_unit": [{"unit": unit, **u} for unit, u in sorted(units.items(), key=lambda t: (-t[1]["accounts"], t[0]))],
        "misses": misses, "unmatched": unmatched, "notes": list(notes or []), "matched_vouchers": matched,
    }  # fmt: skip


# --- The report -----------------------------------------------------------------------------------------------------


def paragraph(report: dict[str, Any]) -> str:
    """The report's first paragraph: what a finance analyst reads out."""
    inv, found = report["invoices"], report["vouchers"]
    scored = inv["scored"]
    said = (
        f"Of {plural(inv['ap_coder'], 'invoice')} read by AP Coder here ({inv['in_review']:,} of them not yet "
        f"approved), {scored:,} {'was' if scored == 1 else 'were'} found in the voucher list and in the journal."
    )
    if scored:
        parts = [f"on {found[v]:,} {SAID[v]}" for v in VERDICTS if found[v]]
        said += f" Of those {scored:,}, as AP Coder proposed them before anyone changed anything: {'; '.join(parts)}."
    if inv["not_scored"]:
        said += (f" {plural(inv['not_scored'], 'invoice')} could not be scored (no voucher, or two that fit): "
                 "listed in the report.")  # fmt: skip
    if inv.get("learned_from"):
        said += (f" Careful: the vouchers of {inv['learned_from']:,} of them are in the journal AP Coder learns from "
                 "here. If that journal was loaded before these invoices were read, it had already seen how they "
                 "were keyed; for a fair test, score invoices keyed after the journal's last voucher.")  # fmt: skip
    return said


def render_markdown(report: dict[str, Any]) -> str:
    inv, lines, tax = report["invoices"], report["lines"], report["tax"]
    out = [
        "# What AP Coder proposed against what was keyed", "", paragraph(report), "",
        *gate_table(report["gate"]),
        f"Made {report.get('made', '')} from {report.get('source', 'the voucher list and the journal')}.", "",
        "## The invoices", "",
        f"- Invoices here, in review or approved: {inv['ap_coder']:,}. Found in the voucher list: "
        f"{inv['in_voucher_list']:,}"
        + (f" ({inv['matched_by_amount']:,} by supplier and amount: their invoice numbers differ)"
           if inv["matched_by_amount"] else "") + ".",
        f"- Scored, their voucher found in the journal too: {inv['scored']:,}. Not scored: {inv['not_scored']:,}.",
        "", "## The coding, voucher by voucher", "",
        "Compared by account: AP Coder's amounts added up per account string (business unit, object, subsidiary) "
        "against the journal's coding lines added up per account. The tax JD Edwards posts itself is not coding.", "",
        _table(["", "vouchers"], [[v, f"{report['vouchers'][v]:,}"] for v in VERDICTS]),
        f"Lines AP Coder put an account on: {lines['coded']:,}; on an account keyed for the voucher: "
        f"{_of(lines['right'], lines['coded'])}; object and subsidiary keyed, on another business unit: "
        f"{lines['unit_wrong']:,}; on an account not keyed: {lines['wrong']:,}. Lines it left to a person: "
        f"{lines['left']:,}.", "",
        "## Header fields and tax", "",
        _table(["field", "right"], [
            *([ps.HEADER_FIELDS[name], _of(h["right"], h["n"])] for name, h in report["header"].items()),
            ["Recovered tax, against the journal's tax line", _of(tax["right"], tax["n"])],
        ]),
        "A field the voucher list does not have is not counted.", "",
    ]  # fmt: skip
    if report["by_supplier"]:
        out += ["## By supplier", "",
                _table(["supplier number", "name", "vouchers", "every account and amount right"],
                       [[s["number"], s["name"], f"{s['vouchers']:,}", f"{s['right']:,}"]
                        for s in report["by_supplier"]]),
                "## By business unit", "",
                _table(["business unit keyed", "accounts keyed", "proposed by AP Coder"],
                       [[u["unit"], f"{u['accounts']:,}", _of(u["right"], u["accounts"])]
                        for u in report["by_unit"]])]  # fmt: skip
    out += [f"## The misses ({len(report['misses'])})", ""]
    if report["misses"]:
        out += [_table(["AP Coder #", "supplier", "invoice", "what", "AP Coder", "keyed", "note"],
                       [[m["ap_coder_id"], m["supplier"], m["invoice_number"], m["what"], m["ap_coder"], m["keyed"],
                         m["note"]] for m in report["misses"]])]  # fmt: skip
    else:
        out += ["None: every invoice scored is as it was keyed.", ""]
    out += ["## Not scored", ""]
    if report["unmatched"]:
        out += [_table(["AP Coder #", "supplier", "invoice", "total", "why"],
                       [[u["id"], u["supplier"], u["invoice_number"], ps._shown(u["total"]), u["why"]]
                        for u in report["unmatched"]])]  # fmt: skip
    else:
        out += ["Every invoice has its voucher.", ""]
    if report["notes"]:
        out += ["## Notes on the voucher list", "", *[f"- {note}" for note in report["notes"]]]
    return "\n".join(out).rstrip("\n") + "\n"


def misses_csv(report: dict[str, Any]) -> bytes:
    """Both sides of every miss, then the invoices not scored."""
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(MISS_COLUMNS)
    for m in report["misses"]:
        writer.writerow(csv_row([m[c] for c in MISS_COLUMNS]))
    for u in report["unmatched"]:
        writer.writerow(csv_row([u["id"], u["supplier"], "", u["invoice_number"], "", "not scored",
                                 ps._shown(u["total"]), "", u["why"]]))  # fmt: skip
    return buf.getvalue().encode("utf-8-sig")


def figures(report: dict[str, Any]) -> dict[str, Any]:
    """The figures with no supplier's name or amount in them."""
    keys = ("made", "invoices", "gate", "vouchers", "lines", "header", "tax")
    return {key: report[key] for key in keys if key in report}


def run(store: Any, vouchers: str | Path, journal: str | Path | None = None, out: str | Path | None = None, *,
        implied_decimals: bool = False) -> tuple[dict[str, Any], Path]:  # fmt: skip
    """Score the invoices in ``store`` as proposed against the voucher list and the journal (the file, else the one
    loaded here), and write the three files; returns (the report, the folder written to)."""
    settings = jde_settings.load(store)
    read, source = the_journal(store, journal)
    posted, notes = ps.posted_vouchers(ps.read_table(vouchers)[0], implied_decimals=implied_decimals,
                                       domestic=settings.domestic_currency)  # fmt: skip
    report = score(ps.our_invoices(store, settings, proposed=True), posted, read, settings.domestic_currency, notes)
    # the vouchers scored that the journal loaded here holds: what AP Coder may have learned from before it proposed
    report["invoices"]["learned_from"] = store.journal_already(jr.journal_of(report.pop("matched_vouchers")))
    report["made"] = dt.datetime.now().isoformat(timespec="minutes").replace("T", " ")
    report["source"] = f"{Path(vouchers).name} and {source}"
    folder = Path(out) if out else Path(store.path).parent / ps.FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    (folder / REPORT).write_text(render_markdown(report), encoding="utf-8")
    (folder / MISSES).write_bytes(misses_csv(report))
    (folder / FIGURES).write_text(json.dumps(figures(report), indent=1) + "\n", encoding="utf-8")
    return report, folder
