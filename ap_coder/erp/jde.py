"""JD Edwards EnterpriseOne (E1) batch vouchers: F0411Z1 / F0911Z1 files for R04110ZA.

E1 loads vouchers from outside systems through two "Z" (interface) tables, which the Voucher Batch
Processor (R04110ZA, or R04110Z) turns into real vouchers (F0411 pay items and F0911 G/L lines):

* **F0411Z1**: one row per voucher pay item (supplier, invoice number, dates, gross / tax amounts,
  tax area and explanation code, currency, PO reference).
* **F0911Z1**: one row per G/L distribution line (account, amount, description).

AP Coder writes both as CSV files (plus, for PO-matched vouchers, an optional match file); the
customer's JDE team loads them into the Z-tables and runs R04110ZA (proof mode first). See
``docs/JDE_E1.md`` for the field-by-field mapping and the open questions.

How the distribution balances
-----------------------------
In E1 the tax explanation code decides which part of the tax the system posts by itself (through the
tax AAIs, e.g. PT / GT) and which part must already be in the G/L distribution:

* ``V`` (VAT: GST, HST, QST): all the tax is recoverable and posted by E1, so the distribution is the
  gross amount minus the tax. A tax the Tax setup keeps in the expense (the QST of a company that claims the GST
  only) is left off the pay item: it is in the distribution, and E1 posts the recovered tax alone (D-210).
* ``C`` (GST + seller-charged PST): the GST/HST part is posted by E1, the PST is an expense: the
  distribution is the gross minus the GST/HST, with the PST inside the expense lines.
* ``B`` (GST + self-assessed PST): the supplier charged GST only; E1 accrues the PST. The distribution
  is the gross minus the GST/HST.
* ``E`` (exempt) or blank: no tax handling, the distribution is the gross.

AP Coder's own GL distribution (``tax.build_gl_distribution``) has the same shape: recoverable taxes
are separate lines, non-recoverable taxes are added to the expense lines (or their own expense line).
The F0911Z1 lines are that distribution without the recoverable-tax lines, so ``validate`` checks
``sum(F0911Z1) == gross - recoverable tax for the code``. When the Tax setup and the JDE code
disagree (e.g. PST set up as recoverable in AP Coder but code C), the invoice is unbalanced and is
left out of the batch, instead of E1 posting the PST twice or not at all.

With the tax coded to G/L accounts (``AMOUNT_GL``, the default for a new setup, D-93) E1 posts no tax: the F0911Z1
lines are AP Coder's distribution unfiltered (each recoverable tax on its own line to its tax account), the voucher
goes with the gross, code E and no tax area, and ``sum(F0911Z1) == gross``.

Where things are
----------------
Every part works from one voucher worked out once (``jde_voucher._prepare``: its amounts in cents, tax area and
code, the distribution lines and why it can't go). One subject a module, each importing only modules above it in
this list (D-222):

* ``jde_formats.py``: E1's dates and amounts (``julian``, ``jde_amount``) and the small readers of a value;
* ``jde_settings.py``: the settings (``JdeSettings``, ``load``, ``save``, ``settings_problems``), whether JD Edwards
  is in use (``in_use``, ``show``) and a voucher's G/L date in the open period (``gl_date_of``);
* ``jde_prototype.py``: the settings of a department that types its own vouchers, in one step (``use_prototype``);
* ``jde_accounts.py``: supplier numbers and accounts (``resolve_an8``, ``account_for``) and the account mapping
  import (``gl_map_from_records``);
* ``jde_voucher.py``: one voucher (``_prepare``, ``terms_code``, ``sent_due``) and what holds it back;
* ``jde_remarks.py``: the remark of a G/L line and of the pay item (``line_remark``, ``voucher_remark``);
* ``jde_zfiles.py``: the Z-files' columns, a voucher's rows (``voucher_rows``) and the CSVs (``export_batch``);
* ``jde_review.py``: before approval, on the review screen (``voucher_findings``, ``voucher_view``), and the tax on
  the pay item;
* this file, a batch as a whole: the checks over it (``validate``, ``duplicate_problems``), its ZIP (``readme``,
  ``build_zip``) and the dry run (``dry_run``, ``dry_run_text``).

A new function goes in the module of its subject (a new subject: a new ``jde_<subject>.py``), and other code imports
it from there (``jde_settings.load``, not ``jde.load``): this file hands nothing on.

The keying sheet is ``jde_keying.py``. The AIS API is ``jde_ais.py`` (the client), ``jde_sync.py`` (reading from JDE)
and ``jde_post.py`` (posting).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import json
import zipfile
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

from ..coding.vendors import held_index, hold_reason, norm_invoice_number
from ..core.memory import vendor_key
from .jde_accounts import VendorLookup, resolve_an8
from .jde_formats import _negative, _norm
from .jde_settings import AMOUNT_GL, AMOUNT_GROSS, AMOUNT_TAX, IMPLIED, JdeSettings, settings_problems
from .jde_voucher import (
    TERMS_CREDIT,
    TERMS_DEFAULT,
    _accounts_once,
    _Voucher,
    _vouchers,
    accrues_use_tax,
    terms_code,
    terms_unmapped,
)
from .jde_zfiles import (
    DIST_FILE,
    HEADER_FILE,
    MATCH_FILE,
    bank_notes,
    batch_number,
    export_batch,
    voucher_rows,
)

UNCONFIRMED = (
    "the PO-matched extra columns (e.g. VLATFLG, VLPSTE) and the whole F0411Z1T match file",
    "VNEDLN numbering (sequential, or matching VLEDLN)",
    "VLEDLN / VNEDLN as written (1, 2, 3): E1 shows EDLN with display decimals, so a direct table load of 1 may "
    "read as 0.001 (1000 needed) where a table conversion would not",
    "the tax area names (ON-HST, QC-GSTQST, …) and explanation codes",
    "VLCRR (exchange rate): left blank when no rate is set, so E1 takes it from the exchange-rate table",
    "VLGLBA (G/L bank account), when set per Pay from account: the value E1 expects there (the bank account's short "
    "account ID) and whether R04110ZA keeps it or takes the AAI PB bank",
)


# --- Checks over a batch -----------------------------------------------------------------------------------------


def prior_records(store: Any, exclude_ids: Iterable[int] = (),
                  invoices: list[dict[str, Any]] | None = None) -> dict[str, list[dict[str, Any]]]:  # fmt: skip
    """What is already out (read-only): invoices in live export batches (not ``exclude_ids``) and the
    imported ERP invoice register; and the vendors on hold now ("held", ``validate``: B-2340). With ``invoices``:
    only what has one of their invoice numbers (as compared), all ``duplicate_problems`` reads of it for them, picked
    by SQLite (D-156): not the year's exports and the whole register read for a day's batch."""
    exclude = set(exclude_ids)
    if invoices is None:
        exported = [r for r in store.search_rows(exported=True) if r["id"] not in exclude]
        return {"exported": exported, "register": store.erp_register(), "held": store.held_vendors()}
    numbers = sorted({norm_invoice_number((inv.get("final_output") or {}).get("invoice_number")) for inv in invoices}
                     - {""})  # fmt: skip
    picked = store.search_rows(where="number_key IN (SELECT value FROM json_each(?))", params=(json.dumps(numbers),),
                               exported=True) if numbers else []  # fmt: skip
    exported = [r for r in picked if r["id"] not in exclude]
    return {"exported": exported, "register": store.erp_register(numbers), "held": store.held_vendors()}


def duplicate_problems(
    invoices: list[dict[str, Any]], settings: JdeSettings, vendor_lookup: VendorLookup,
    prior: Mapping[str, list[dict[str, Any]]] | None = None,
) -> dict[int, list[str]]:  # fmt: skip
    """Same supplier (address number or vendor name) and invoice number, within the batch or already
    exported / in the ERP. A credit note does not duplicate the invoice it reverses."""
    out: dict[int, list[str]] = {}
    prior = prior or {}

    def an8_of(row: dict[str, Any]) -> str:  # by name, else by GST/HST number, as for the invoices themselves
        gst = row.get("gst_hst_registration_number") or ""
        known = {"vendor_name": row.get("vendor_name") or "", "gst_hst_registration_number": gst}
        return resolve_an8(known, vendor_lookup, settings)

    def same(a: tuple[str, str], b: tuple[str, str]) -> bool:
        return (bool(a[0]) and a[0] == b[0]) or (bool(a[1]) and a[1] == b[1])

    def by_number(rows: Iterable[dict[str, Any]], total: str) -> dict[tuple[str, bool], list[dict[str, Any]]]:
        """``rows`` by (invoice number as compared, a credit), in their order: each number read once, not once an
        invoice of the batch (thousands of rows already out)."""
        found: dict[tuple[str, bool], list[dict[str, Any]]] = {}
        for r in rows:
            found.setdefault((norm_invoice_number(r.get("invoice_number")), _negative(r.get(total))), []).append(r)
        return found

    exported = by_number(prior.get("exported") or [], "grand_total")
    register = by_number(prior.get("register") or [], "total")
    earlier: list[tuple[tuple[str, str], str, bool, int]] = []
    for inv in invoices:
        final = inv.get("final_output") or {}
        number = norm_invoice_number(final.get("invoice_number"))
        if not number:
            continue
        who = (resolve_an8(final, vendor_lookup, settings), vendor_key(final.get("vendor_name") or ""))
        credit = _negative(final.get("grand_total"))
        msgs = []
        for other_who, other_number, other_credit, other_id in earlier:
            if other_number == number and other_credit == credit and same(who, other_who):
                msgs.append(f"same supplier and invoice number as AP Coder #{other_id} in this batch")
                break
        for r in exported.get((number, credit), []):
            if same(who, (an8_of(r), vendor_key(r.get("vendor_name") or ""))):
                msgs.append(f"already exported: AP Coder #{r['id']} in batch {r['export_batch']}")
                break
        for r in register.get((number, credit), []):
            key = r.get("vendor_key") or vendor_key(r.get("vendor_name") or "")
            an8 = str(r.get("an8") or "").strip()  # JD Edwards' voucher list's supplier number (B-2342)
            # as numbers: an override typed "02004" is JD Edwards' 2004 (B-2487); else (no GST/HST number) by name
            if (who[0].lstrip("0") == an8.lstrip("0")) if (who[0] and an8) else same(who, (an8_of(r), key)):
                when = f", dated {r['invoice_date']}" if r.get("invoice_date") else ""
                msgs.append(f"already in the ERP (invoice {r.get('invoice_number')}{when})")
                break
        if msgs:
            out[int(inv.get("id") or 0)] = msgs
        earlier.append((who, number, credit, int(inv.get("id") or 0)))
    return out


def validate(
    invoices: list[dict[str, Any]], settings: JdeSettings, vendor_lookup: VendorLookup = None,
    prior: Mapping[str, list[dict[str, Any]]] | None = None, *, prepared: list[tuple[str, _Voucher]] | None = None,
    on: dt.date | None = None,
) -> dict[int, list[str]]:  # fmt: skip
    """{invoice id: blocking problems} for the invoices that cannot go out (the others are fine). ``prepared``: the
    invoices' vouchers already prepared (``_vouchers``); ``on``: the batch's day (else today). A voucher whose vendor
    is on hold now (``prior``'s "held", here or in JD Edwards) is held too: approved before the hold, it is not typed
    or loaded (B-2340)."""
    out: dict[int, list[str]] = {}
    if prepared is None:
        prepared = _vouchers(invoices, settings, vendor_lookup, on)
    held = held_index((prior or {}).get("held") or [])
    for inv, (an8, v) in zip(invoices, prepared, strict=True):
        if v.problems:
            out[int(inv.get("id") or 0)] = _accounts_once(v)
        if why := hold_reason(held, inv.get("vendor_key"), an8):
            out.setdefault(int(inv.get("id") or 0), []).append(why)
    for inv_id, msgs in duplicate_problems(invoices, settings, vendor_lookup, prior).items():
        out.setdefault(inv_id, []).extend(msgs)
    return out


UNDO_TEXT = "in AP Coder, Exports → Past batches → Undo this batch, fix, and export again."


def readme(
    files: dict[str, bytes], settings: JdeSettings, batch: int | str, invoices: list[dict[str, Any]],
    left_out: Mapping[int, list[str]] | None = None, to_check: Mapping[int, list[str]] | None = None,
    no_undo: str = "", site: str = "",
) -> str:  # fmt: skip
    """README.txt of the ZIP: what is in it and how to load it. ``left_out``: invoices not in the file;
    ``to_check``: invoices in it that the current settings would refuse (a batch downloaded again); the invoices whose
    Pay from account has no VLGLBA mapped are listed too (``bank_notes``). ``no_undo``: why the batch can't be undone
    (vouchers JD Edwards has, or may have), said instead of the undo (B-1203). ``site``: the batch's code (VLEDBT)."""
    totals: dict[str, float] = {}
    for inv in invoices:
        final = inv.get("final_output") or {}
        cur = _norm(final.get("currency")) or settings.domestic_currency
        totals[cur] = round(totals.get(cur, 0.0) + float(final.get("grand_total") or 0), 2)

    def count(name: str) -> int:
        return len(list(csv.reader(io.StringIO(files[name].decode("utf-8"))))) - 1 if name in files else 0

    edbt = batch_number(settings, batch, site)
    money = ", ".join(f"{c} {t:,.2f}" for c, t in sorted(totals.items())) or "-"
    decimals = "implied decimals (5.50 is written 550)" if settings.decimals == IMPLIED else "with a decimal point"
    amounts = {
        AMOUNT_GROSS: "gross only (VLAG / VLACR)",
        AMOUNT_TAX: "taxable, non-taxable and tax",
        AMOUNT_GL: f"gross only (VLAG / VLACR), tax coded to G/L accounts: each tax on its own F0911Z1 line to the "
        f"tax account, code {settings.gl_tax_code} and no tax area, so E1 computes no tax",
    }[settings.amount_mode]
    lines = [
        f"AP Coder: JD Edwards E1 voucher batch {edbt}",
        "=" * 60,
        "",
        f"Vouchers: {len(invoices)}   Totals: {money}",
        f"User (EDUS): {settings.user.strip()}   Batch (EDBT): {edbt}   Company: {settings.company_code}",
        f"Amounts: {decimals}; {amounts}",
        "Dates: E1 Julian CYYDDD (2026-10-09 is 126282).",
        "",
        "Files",
        "-----",
        f"{HEADER_FILE}  {count(HEADER_FILE)} row(s): one per voucher pay item, for table F0411Z1.",
        f"{DIST_FILE}  {count(DIST_FILE)} row(s): one per G/L distribution line, for table F0911Z1.",
    ]
    if MATCH_FILE in files:
        lines.append(
            f"{MATCH_FILE}  {count(MATCH_FILE)} row(s): PO match lines (PO, line, item, quantity, unit price, amount)."
            " NOT a standard Oracle layout: confirm with your JDE team how PO-matched vouchers are loaded."
        )
    lines += [
        "",
        "How to load",
        "-----------",
        "1. Load each CSV into its Z-table (F0411Z1, F0911Z1) in the E1 environment you test in first, e.g. with",
        "   your database's import tool or a table conversion. Each header name is the table's column name.",
        "2. Run the Voucher Batch Processor R04110ZA (or R04110Z) in PROOF mode on this batch number "
        f"({edbt}) and user ({settings.user.strip()}).",
        "   Read the report and the Work Center (P0471/P0411Z1) errors; fix the mapping in AP Coder if needed.",
        "3. Run R04110ZA in FINAL mode. The Z-rows are marked processed (VLEDSP = 1) and the vouchers are created.",
        "4. Post the voucher batch (P0011 / R09801) as usual.",
        "",
        f"If the load fails: {no_undo or UNDO_TEXT}",
        "Each voucher's transaction number (EDTN) is APC + the AP Coder invoice number, a dash and a code of the",
        "invoice; the batch number ends in a code of the batch: two AP Coder data folders, or one put back from a",
        "backup, never give two vouchers the same keys.",
        "",
        "To confirm with your JDE team before the first live load: "
        + "; ".join(UNCONFIRMED)
        + (
            "; that R04110ZA with code E and no tax area posts the G/L lines as sent (the tax lines to the tax "
            "accounts) and works out no tax of its own (confirm with your JDE administrator)"
            if settings.tax_on_gl
            else ""
        )
        + ".",
    ]
    if left_out:
        lines += ["", "Not in this batch (fix them, then export again)", "-" * 47]
        for inv_id, problems in left_out.items():
            lines.append(f"AP Coder #{inv_id}: " + "; ".join(problems))
    notes = bank_notes(invoices, settings)
    if notes:
        title = "In this batch, paid from E1's default bank: check before loading"
        lines += ["", title, "-" * len(title)]
        lines += [n for said in notes.values() for n in said]
        lines.append("Map them in Settings -> JD Edwards E1 -> G/L bank account (VLGLBA) per Pay from account.")
    if to_check:
        title = "In this batch, but the current settings or vendor master would refuse them: check before loading"
        lines += ["", title, "-" * len(title)]
        for inv_id, problems in to_check.items():
            lines.append(f"AP Coder #{inv_id}: " + "; ".join(problems))
    return "\n".join(lines) + "\n"


def build_zip(
    invoices: list[dict[str, Any]], settings: JdeSettings, vendor_lookup: VendorLookup = None, *,
    batch: int | str = "", fx_rates: Mapping[str, float] | None = None, on: dt.date | None = None,
    left_out: Mapping[int, list[str]] | None = None, to_check: Mapping[int, list[str]] | None = None,
    left_invoices: list[dict[str, Any]] = (), no_undo: str = "", check_now: bool = False, site: str = "",
) -> tuple[bytes, str, str]:  # fmt: skip
    """(ZIP bytes, file name, mime type): the CSVs and a README.txt. ``left_invoices``: the invoices of ``left_out``,
    for their vendor, number and date in DRY_RUN.txt (B-1197); ``no_undo``: why it can't be undone (``readme``);
    ``check_now``: ``to_check`` is what ``validate`` now finds in ``invoices`` (each voucher prepared once for all);
    ``site``: the batch's code in VLEDBT (``batch_site``)."""
    vouchers = _vouchers(invoices, settings, vendor_lookup, on)
    if check_now:
        to_check = validate(invoices, settings, vendor_lookup, prepared=vouchers)
    files = export_batch(invoices, settings, vendor_lookup, batch=batch, fx_rates=fx_rates, on=on, prepared=vouchers,
                         site=site)  # fmt: skip
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
        zf.writestr("README.txt", readme(files, settings, batch, invoices, left_out, to_check, no_undo, site))
        report = dry_run(invoices, settings, vendor_lookup, batch=batch, fx_rates=fx_rates, on=on, prepared=vouchers,
                         site=site)  # fmt: skip
        for h in report["held"]:
            if h["id"] in (to_check or {}):  # the files hold it: it passed the checks when the batch was made
                h["reasons"] = ["it IS in this batch's files (checked when the batch was made): check it before "
                                "loading", *h["reasons"]]  # fmt: skip
        finals = {int(i.get("id") or 0): i.get("final_output") or {} for i in left_invoices}
        for inv_id, reasons in (left_out or {}).items():
            final = finals.get(inv_id, {})
            report["held"].append({"id": inv_id, "vendor": final.get("vendor_name") or "",
                                   "number": final.get("invoice_number") or "",
                                   "date": final.get("invoice_date") or "", "reasons": reasons})  # fmt: skip
        zf.writestr("DRY_RUN.txt", dry_run_text(report))
    name = f"ap_coder_jde_batch_{batch}.zip" if batch != "" else "ap_coder_jde.zip"
    return buf.getvalue(), name, "application/zip"


# --- Dry run ---------------------------------------------------------------------------------------------------


def _shown(value: Any, settings: JdeSettings, decimals: int) -> str:
    """An amount as a person reads it, from what the files hold (implied decimals or a decimal point)."""
    if value in ("", None):
        return ""
    try:
        number = Decimal(str(value))
    except ArithmeticError:
        return str(value)
    if settings.decimals == IMPLIED:
        number /= Decimal(10) ** decimals
    return f"{number:,.{decimals}f}"


def _gross_of(header: dict[str, Any], foreign: bool) -> Any:
    """A pay item's gross as the file holds it: VLAG / VLACR, or with taxable / non-taxable / tax amounts (no gross
    column) the three added up, from the currency's own VLC* columns for a foreign invoice."""
    gross_col = "VLACR" if foreign else "VLAG"
    parts = ("VLCTXA", "VLCTXN", "VLCTAM") if foreign else ("VLATXA", "VLATXN", "VLSTAM")
    if header.get(gross_col) not in ("", None):
        return header[gross_col]
    if all(header.get(p) in ("", None) for p in parts):
        return ""
    return sum(Decimal(str(header.get(p) or 0)) for p in parts)


def dry_run(
    invoices: list[dict[str, Any]],
    settings: JdeSettings,
    vendor_lookup: VendorLookup = None,
    *,
    prior: Mapping[str, list[dict[str, Any]]] | None = None,
    batch: int | str = "",
    fx_rates: Mapping[str, float] | None = None,
    on: dt.date | None = None,
    prepared: list[tuple[str, _Voucher]] | None = None,
    rows: Mapping[int, tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]] | None = None,
    site: str = "",
) -> dict[str, Any]:
    """Exactly what a batch of these invoices would load, row by row, and why any invoice is held back, without
    making a batch. {"blocking": [...], "vouchers": [...], "held": [...], "files": {file: rows}, "totals": {...}}.
    ``prepared``: the invoices' vouchers already prepared (``_vouchers``); ``rows``: {invoice: ``voucher_rows``}
    already made with the same batch, rates, date and ``site`` (the batch's code, ``batch_site``)."""
    blocking = settings_problems(settings)
    if prepared is None:
        prepared = _vouchers(invoices, settings, vendor_lookup, on)
    problems = validate(invoices, settings, vendor_lookup, prior, prepared=prepared)
    notes = bank_notes(invoices, settings)
    out: dict[str, Any] = {
        "batch": batch_number(settings, batch, site),
        "user": settings.user.strip(),
        "company": settings.company_code,
        "blocking": blocking,
        "vouchers": [],
        "held": [],
        "files": {HEADER_FILE: 0, DIST_FILE: 0, MATCH_FILE: 0},
        "totals": {},
    }
    for inv, (an8, v) in zip(invoices, prepared, strict=True):
        final = inv.get("final_output") or {}
        inv_id = int(inv.get("id") or 0)
        who = {
            "id": inv_id,
            "vendor": final.get("vendor_name") or "",
            "number": final.get("invoice_number") or "",
            "date": final.get("invoice_date") or "",
            "currency": _norm(final.get("currency")) or settings.domestic_currency,
            "total": final.get("grand_total"),
        }
        if inv_id in problems or blocking:
            out["held"].append(
                {**who, "reasons": problems.get(inv_id, []) + (["the JD Edwards settings above"] if blocking else [])}
            )
            continue
        made = (rows or {}).get(inv_id)
        headers, dists, matches = made or voucher_rows(inv, an8, settings, batch=batch, fx_rates=fx_rates, on=on,
                                                       prepared=v, site=site)  # fmt: skip
        aa_col = "VNACR" if v.foreign else "VNAA"
        dist_total = sum(Decimal(str(d.get(aa_col) or 0)) for d in dists)
        posted = Decimal(v.gross - sum(e["cents"] for e in v.entries)) / 100
        out["vouchers"].append(
            {
                **who,
                "an8": an8,
                "tax_area": v.tax_area,
                "tax_code": v.tax_code,
                "po_matched": v.po_matched,
                "pay_items": [
                    {
                        "line": h.get("VLEDLN"),
                        "gross": _shown(_gross_of(h, v.foreign), settings, v.decimals),
                        "tax": _shown(h.get("VLSTAM", h.get("VLCTAM", "")), settings, v.decimals),
                        "dates": f"invoice {h['VLDIVJ']}, G/L {h['VLDGJ']}"
                        + (f", due {h['VLDDJ']}" if h.get("VLDDJ") else ""),
                        "doc_type": h.get("VLDCT"),
                        "po": h.get("VLPO", ""),
                    }
                    for h in headers
                ],
                "lines": [
                    {
                        "line": d.get("VNEDLN"),
                        "account": d.get("VNANI")
                        or ".".join(x for x in (d.get("VNMCU"), d.get("VNOBJ"), d.get("VNSUB")) if x),
                        "amount": _shown(d.get(aa_col), settings, v.decimals),
                        "text": d.get("VNEXA", ""),
                    }
                    for d in dists
                ],
                "matches": len(matches),
                # The self-assessed PST is booked from the Self-assessment report by journal entry; a code that has
                # E1 accrue the use tax itself (B) would accrue it a second time (B-716).
                "warnings": (
                    [
                        f"code {v.tax_code} has JD Edwards accrue the PST (use tax): do not also book it from AP "
                        "Coder's Self-assessment report, or the PST is accrued twice"
                    ]
                    if accrues_use_tax(final, settings)
                    else []
                )
                + (
                    [f"payment terms {unmapped} have no JD Edwards code: add it in Settings -> JD Edwards E1"]
                    if (unmapped := terms_unmapped(final, settings))
                    else []
                ),
                "terms": _terms_shown(final, settings),
                "notes": notes.get(inv_id, []),
                "balance": (
                    f"distribution {_shown(dist_total, settings, v.decimals)} = gross {v.gross / 100:,.2f}, the tax "
                    f"on its own G/L lines (tax coded to G/L accounts, code {v.tax_code}: E1 posts no tax)"
                    if settings.tax_on_gl
                    else f"distribution {_shown(dist_total, settings, v.decimals)} = gross {v.gross / 100:,.2f} "
                    f"- tax E1 posts {posted:,.2f} (code {v.tax_code or 'none'})"
                )
                if dists
                else "PO-matched: E1 distributes from the receipts",
            }
        )
        out["files"][HEADER_FILE] += len(headers)
        out["files"][DIST_FILE] += len(dists)
        out["files"][MATCH_FILE] += len(matches)
        cur = who["currency"]
        out["totals"][cur] = round(out["totals"].get(cur, 0.0) + float(final.get("grand_total") or 0), 2)
    return out


def _terms_shown(final: dict[str, Any], settings: JdeSettings) -> str:
    """The dry run's line about the voucher's payment terms code (VLPTC), "" when there is nothing to say."""
    code, key, source = terms_code(final, settings)
    if not code and not key:
        return ""
    printed = key or "none printed"
    if source == TERMS_CREDIT:
        return f"payment terms {printed} -> none (a credit memo is sent without a terms code)"
    if source == TERMS_DEFAULT:
        return f"payment terms {printed} -> {code} (the default code)"
    if code:
        return f"payment terms {printed} -> {code}"
    return f"payment terms {printed} -> none (no code: JD Edwards uses the supplier's own terms)"


def dry_run_text(report: dict[str, Any]) -> str:
    """The dry run as plain text: for the JDE team, and kept in each batch's ZIP as DRY_RUN.txt."""
    lines = [
        f"AP Coder: JD Edwards E1 dry run of batch {report['batch']} "
        f"(user {report['user']}, company {report['company']})",
        "=" * 78,
        "",
    ]
    if report["blocking"]:
        lines += ["NOTHING CAN LOAD until these JD Edwards settings are fixed (Settings -> JD Edwards E1):"]
        lines += [f"  - {p}" for p in report["blocking"]] + [""]
    money = ", ".join(f"{c} {t:,.2f}" for c, t in sorted(report["totals"].items())) or "nothing to load"
    lines += [
        f"Will load: {len(report['vouchers'])} voucher(s), {money}. Held back: {len(report['held'])}.",
        "Rows: " + ", ".join(f"{f} {n}" for f, n in report["files"].items() if n or f != MATCH_FILE),
        "",
    ]
    for v in report["vouchers"]:
        lines.append(
            f"WILL LOAD  AP Coder #{v['id']}  {v['vendor']}  invoice {v['number']}  {v['date']}  "
            f"{v['currency']} {float(v['total'] or 0):,.2f}"
        )
        lines.append(f"  supplier AN8 {v['an8']}; tax area {v['tax_area'] or '-'}, code {v['tax_code'] or '-'}")
        for p in v["pay_items"]:
            lines.append(
                f"  F0411Z1 pay item {p['line']}: {p['doc_type']} gross {p['gross']}"
                + (f", tax {p['tax']}" if p["tax"] else "")
                + f"; {p['dates']}"
                + (f"; PO {p['po']}" if p["po"] else "")
            )
        for d in v["lines"]:
            lines.append(f"  F0911Z1 line {d['line']}: {d['account']}  {d['amount']}  {d['text']}")
        if v.get("terms"):
            lines.append(f"  {v['terms']}")
        if v["matches"]:
            lines.append(f"  F0411Z1T: {v['matches']} PO match line(s)")
        lines += [f"  WARNING: {w}" for w in v.get("warnings") or []]
        lines.append(f"  {v['balance']}")
        lines += [f"  NOTE: {n}" for n in v.get("notes") or []]
        lines.append("")
    for h in report["held"]:
        lines.append(f"HELD BACK  AP Coder #{h['id']}  {h['vendor']}  invoice {h['number']}  {h['date']}")
        lines += [f"  - {r}" for r in h["reasons"]] + [""]
    return "\n".join(lines) + "\n"
