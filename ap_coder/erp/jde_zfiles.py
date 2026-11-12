"""The Z-files of a JD Edwards E1 voucher batch: the columns of F0411Z1 (pay items), F0911Z1 (G/L lines) and the PO
match file, the keys that tell a batch and a voucher from any other (``batch_number``, ``batch_site``,
``invoice_site``), one voucher's rows (``voucher_rows``) and the CSV files of a batch (``export_batch``). The field by
field mapping: ``docs/JDE_E1.md``.
"""

from __future__ import annotations

import csv
import datetime as dt
import hashlib
import io
from collections.abc import Iterable, Mapping
from decimal import Decimal
from typing import Any

from ..coding.line_tax import exempt_of, legacy_basis
from .jde_accounts import VendorLookup, ani
from .jde_formats import E1_YEARS, _date, _norm, _plain, _split, _text, _units, jde_amount, julian
from .jde_remarks import _pay_item_remark, line_remark
from .jde_settings import (
    ACCOUNT_ANI,
    AMOUNT_GL,
    AMOUNT_GROSS,
    IMPLIED,
    MATCH_HEADER,
    REMARKS_TYPED,
    GlMap,
    JdeSettings,
    gl_date_of,
)
from .jde_voucher import (
    EXEMPT_STATUSES,
    PO_WIDTH,
    PTC_WIDTH,
    _prepare,
    _Voucher,
    _vouchers,
    sent_due,
    terms_code,
)

HEADER_COLUMNS = [
    "VLEDUS", "VLEDBT", "VLEDTN", "VLEDLN", "VLEDSP", "VLEDTC", "VLEDTR",
    "VLDCT", "VLCO", "VLMCU", "VLAN8",
    "VLVINV", "VLDIVJ", "VLDGJ", "VLDSVJ",
    "VLAG", "VLATXA", "VLATXN", "VLSTAM", "VLTXA1", "VLEXR1",
    "VLCRRM", "VLCRCD", "VLCRR", "VLACR", "VLCTXA", "VLCTXN", "VLCTAM",
    "VLPTC", "VLDDJ", "VLPST",
    "VLPO", "VLPDCT", "VLPKCO", "VLRMK",
]  # fmt: skip
DIST_COLUMNS = [
    "VNEDUS", "VNEDBT", "VNEDTN", "VNEDLN", "VNEDSP", "VNEDTC", "VNEDTR",
    "VNCO", "VNDGJ", "VNDCT",
    "VNAM", "VNANI", "VNMCU", "VNOBJ", "VNSUB", "VNSBL", "VNSBLT",
    "VNLT", "VNAA", "VNCRCD", "VNACR", "VNEXA", "VNEXR", "VNTXA1", "VNEXR1",
]  # fmt: skip
# PO receipt-match lines: the layout is NOT a standard Oracle table; to be confirmed with the JDE team.
MATCH_COLUMNS = [
    "EDUS", "EDBT", "EDTN", "EDLN", "DOCO", "DCTO", "KCOO", "LNID", "LITM", "DSC1", "UORG", "PRRC", "AEXP", "CRCD",
]  # fmt: skip

HEADER_FILE, DIST_FILE, MATCH_FILE = "F0411Z1.csv", "F0911Z1.csv", "F0411Z1T.csv"
# The text columns filled from invoice text, at their Z-table widths.
TEXT_WIDTHS = {"VLVINV": 25, "VLRMK": 30, "VNEXA": 30, "VNEXR": 30, "DSC1": 30}


def _amount(cents: int, settings: JdeSettings, decimals: int) -> int | str:
    return jde_amount(Decimal(cents) / 100, settings.decimals == IMPLIED, decimals)


def _code(*parts: Any) -> str:
    """A short code of what makes a batch or an invoice that one and no other, in any data folder (D-182, R6)."""
    return hashlib.blake2b("|".join(str(p or "") for p in parts).encode(), digest_size=4).hexdigest().upper()


def batch_site(batch: Mapping[str, Any], invoices: Iterable[Mapping[str, Any]]) -> str:
    """The code of export batch ``batch`` (its row) in VLEDBT: when it was made, its number and its invoices (their
    ``invoice_site``), so another data folder's batch of that number, or the one a database put back from a backup
    hands that number to again, has another (D-182)."""
    return _code(batch.get("created_at"), batch.get("id"), *sorted(invoice_site(i) for i in invoices))


def invoice_site(invoice: Mapping[str, Any]) -> str:
    """The code of an invoice in VLEDTN: its file and when it was added (as a restore knows an invoice again, D-105):
    the same each time it is sent or downloaded, another for another invoice under the same number (D-182)."""
    return _code(invoice.get("file_sha256"), invoice.get("created_at"))


def batch_number(settings: JdeSettings, batch: int | str, site: str = "") -> str:
    """VLEDBT (15 characters): the prefix and AP Coder's batch number, then a dash and the batch's code
    (``batch_site``), as many of its 4 characters as there is room for (none with a long prefix and number)."""
    head = f"{settings.batch_prefix.strip()}{batch}"
    room = min(4, 15 - len(head) - 1)
    return f"{head}-{site[:room]}" if site and room >= 2 else head[:15]


def _keys(prefix: str, settings: JdeSettings, batch: int | str, invoice: Mapping[str, Any], inv_id: int,
          site: str = "") -> dict[str, Any]:  # fmt: skip
    return {
        f"{prefix}EDUS": settings.user.strip()[:10],
        f"{prefix}EDBT": batch_number(settings, batch, site),
        f"{prefix}EDTN": f"APC{inv_id}-{invoice_site(invoice)}"[:22],
        f"{prefix}EDSP": "0",
        f"{prefix}EDTC": "A",
        f"{prefix}EDTR": "V",
    }


def _rate(fx_rates: Mapping[str, float] | None, currency: str, domestic: str) -> float | None:
    """VLCRR: units of the domestic currency per unit of ``currency``. AP Coder's rates are CAD per unit (Settings →
    Review), so with books in another currency it is the rate across CAD (USD books, a CAD invoice: 1 / 1.37), and
    None (left blank: E1's exchange-rate table) without a rate for both."""
    rates = {"CAD": 1.0, **(fx_rates or {})}
    foreign, home = rates.get(_norm(currency)), rates.get(_norm(domestic))
    return foreign / home if foreign and home else None


def _doc_type(v: _Voucher, settings: JdeSettings) -> str:
    if v.gross < 0 and settings.credit_document_type.strip():
        return settings.credit_document_type.strip().upper()
    return settings.document_type.strip().upper()


def voucher_rows(
    invoice: dict[str, Any], vendor_an8: str, settings: JdeSettings, *, batch: int | str = "",
    fx_rates: Mapping[str, float] | None = None, on: dt.date | None = None, prepared: _Voucher | None = None,
    site: str = "",
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:  # fmt: skip
    """(F0411Z1 rows, F0911Z1 rows, match rows) of one approved invoice, as {column: value}. Check it with
    ``validate`` first: an invoice with problems gives incomplete rows. ``prepared``: its voucher already prepared
    for ``vendor_an8`` (``_vouchers``), not prepared again. ``site``: the batch's code (``batch_site``) in VLEDBT."""
    from .exports import entry_lines

    v = prepared if prepared is not None else _prepare(invoice, settings, str(vendor_an8 or "").strip(), on)
    final = v.final
    keys = _keys("VL", settings, batch, invoice, v.inv_id, site)
    invoice_date = _date(final.get("invoice_date")) or (on or dt.date.today())
    gl_date = gl_date_of(final, settings, on)[0]  # in the open period (D-182)
    due = sent_due(final, invoice)  # a credit note: its invoice date; a guessed one is not sent (B-924)
    doc_type = _doc_type(v, settings)
    po = str(final.get("po_number") or "").strip()
    # VLPO, VLPDCT and VLPKCO go together (B-457); a PO longer than VLPO is not sent, only kept in the remark (D-87).
    send_po = bool(po) and (v.po_matched or (settings.send_po_reference and len(po) <= PO_WIDTH))
    typed = settings.remarks == REMARKS_TYPED  # VNEXR: the line's remark, not the invoice number (D-210)
    rate = _rate(fx_rates, v.currency, settings.domestic_currency) if v.foreign else None
    common = {
        **keys, "VLDCT": doc_type, "VLCO": settings.company_code, "VLMCU": settings.header_bu.strip(),
        "VLAN8": str(vendor_an8 or "").strip(), "VLVINV": str(final.get("invoice_number") or "").strip()[:25],
        "VLDIVJ": julian(invoice_date), "VLDGJ": julian(gl_date), "VLDSVJ": julian(invoice_date),
        "VLTXA1": v.tax_area, "VLEXR1": v.tax_code, "VLCRRM": "F" if v.foreign else "D", "VLCRCD": v.currency,
        "VLCRR": _plain(rate, 7) if rate else "", "VLPTC": terms_code(final, settings)[0][:PTC_WIDTH],
        # A due date E1 cannot hold is left blank, not an error: a batch downloaded again is not checked again (B-21),
        # and ``validate`` lists it for the README's "check before loading".
        "VLDDJ": julian(due) if settings.send_due_date and due is not None and due.year in E1_YEARS else "",
        "VLPST": settings.pay_status.strip(),
        "VLPO": po[:PO_WIDTH] if send_po else "",
        "VLPDCT": settings.po_document_type.strip() if send_po else "",
        "VLPKCO": (settings.po_company.strip().zfill(5) if settings.po_company.strip().isdigit()
                   else settings.po_company.strip() or settings.company_code) if send_po else "",
        "VLRMK": _pay_item_remark(v, settings),
    }  # fmt: skip
    if settings.bank_glba:  # the G/L bank account of the Pay from account (D-66); blank: E1's AAI PB
        common["VLGLBA"] = glba(final, settings)

    def amounts(gross: int, stam: int, atxa: int, code: str | None = None) -> dict[str, Any]:
        out: dict[str, Any] = {}
        prefix = ("VLACR", "VLCTXA", "VLCTXN", "VLCTAM") if v.foreign else ("VLAG", "VLATXA", "VLATXN", "VLSTAM")
        if settings.amount_mode in (AMOUNT_GROSS, AMOUNT_GL):  # with the tax on G/L lines, E1 takes no tax amount
            out[prefix[0]] = _amount(gross, settings, v.decimals)
        elif (v.tax_code if code is None else code) in ("E", "", exempt) or not stam:
            out.update({prefix[1]: _amount(0, settings, v.decimals), prefix[2]: _amount(gross, settings, v.decimals),
                        prefix[3]: _amount(0, settings, v.decimals)})  # fmt: skip
        else:
            out.update({prefix[1]: _amount(atxa, settings, v.decimals),
                        prefix[2]: _amount(gross - atxa - stam, settings, v.decimals),
                        prefix[3]: _amount(stam, settings, v.decimals)})  # fmt: skip
        return out

    extras = dict(settings.po_extra_columns) if settings.po_matched else {}
    exempt = settings.exempt_code.strip().upper()
    # Each distribution line's own explanation code (D-64): a line marked exempt or zero-rated, on an invoice whose
    # code handles tax, is the exempt code when nothing of the tax is put on it; the others keep the invoice's.
    items = final.get("line_items") or []
    kinds = sorted({t["tax_type"] for t in v.charged})
    basis = legacy_basis(items, final.get("tax_lines") or [])
    stated = [li is not None and not (li.get("taxes_applied") or [])
              and (li.get("tax_status") in EXEMPT_STATUSES
                   or (bool(kinds) and not li.get("tax_status") and set(kinds) <= set(exempt_of(li, basis))))
              for li in entry_lines(v.entries, items)]  # fmt: skip
    line_codes = [exempt if no_tax and v.tax_code not in ("", exempt) else v.tax_code for no_tax in stated]
    if v.po_matched:
        header = {**common, "VLEDLN": 1, **amounts(v.gross, v.stam, v.atxa), **extras}
        matches = []
        for li in final.get("line_items") or []:
            matches.append({
                "EDUS": keys["VLEDUS"], "EDBT": keys["VLEDBT"], "EDTN": keys["VLEDTN"], "EDLN": 1,
                "DOCO": po[:8], "DCTO": settings.po_document_type.strip(), "KCOO": common["VLPKCO"],
                "LNID": li.get("line_number") or "", "LITM": "", "DSC1": _text(li.get("description"), 30),
                "UORG": _plain(li.get("quantity")), "PRRC": _plain(li.get("unit_price"), 4),
                "AEXP": _amount(_units(li.get("amount")), settings, v.decimals), "CRCD": v.currency,
            })  # fmt: skip
        return [header], [], matches

    def dist_row(n: int, e: dict[str, Any]) -> dict[str, Any]:
        account: GlMap | None = e["account"]
        acct = account or GlMap(e.get("gl_code") or "")
        amount = _amount(e["cents"], settings, v.decimals)
        row = {
            **_keys("VN", settings, batch, invoice, v.inv_id, site), "VNEDLN": n, "VNCO": settings.company_code,
            "VNDGJ": julian(gl_date), "VNDCT": doc_type, "VNLT": "AA",
            "VNAA": "" if v.foreign else amount, "VNCRCD": v.currency, "VNACR": amount if v.foreign else "",
            "VNEXA": line_remark(e),
            "VNEXR": line_remark(e) if typed else str(final.get("invoice_number") or "").strip()[:30],
            "VNSBL": acct.sbl, "VNSBLT": acct.sblt,
            "VNTXA1": v.tax_area if settings.dist_tax_columns else "",
            "VNEXR1": line_codes[n - 1] if settings.dist_tax_columns else "",
        }  # fmt: skip
        if settings.account_mode == ACCOUNT_ANI:
            row.update({"VNAM": "2", "VNANI": ani(acct) if account else ""})
        else:
            row.update({"VNMCU": acct.bu, "VNOBJ": acct.obj, "VNSUB": acct.sub})
        return row

    if settings.line_numbering == MATCH_HEADER and len(v.entries) > 1 and settings.tax_on_gl:
        # One pay item per G/L line, the line's own amount: the tax lines are lines like the others (D-93).
        headers = [{**common, "VLEDLN": n, "VLEXR1": v.tax_code, **amounts(e["cents"], 0, 0), **extras}
                   for n, e in enumerate(v.entries, 1)]  # fmt: skip
        return headers, [dist_row(n, e) for n, e in enumerate(v.entries, 1)], []
    if settings.line_numbering == MATCH_HEADER and len(v.entries) > 1:
        # One pay item per distribution line: each gets its share of each recoverable tax and of the taxable amount,
        # split over the lines that carry that tax (an exempt line gets none). Its tax is that share plus the
        # non-recoverable tax already in its line (e.g. PST charged on some lines only); a tax the Tax setup keeps
        # in the expense under code V is not the pay item's (D-210): its tax is that share alone.
        unit = 10 ** (2 - v.decimals)
        taxes = _entry_taxes(v.entries, final.get("line_items") or [])
        net = [_units(e.get("net_amount")) if e.get("kind") == "expense" else 0 for e in v.entries]
        weights = net if any(net) else [e["cents"] for e in v.entries]

        def carrying(tax_type: str) -> list[int]:
            w = [n if tax_type in t else 0 for n, t in zip(net, taxes, strict=True)]
            return w if any(w) else weights  # no line marked with it: by net amount

        shares = [0] * len(v.entries)
        for t in v.charged:
            if t["tax_type"] in v.recoverable_types:
                part = _split(_units(t.get("tax_amount"), v.decimals), carrying(t["tax_type"]), unit)
                shares = [a + b for a, b in zip(shares, part, strict=True)]
        bases: dict[str, int] = {}  # each tax's taxable amount, its lines added up, as VLATXA is (B-923, B-2487)
        for t in v.charged:
            if t["tax_type"] not in v.expensed:
                bases[t["tax_type"]] = bases.get(t["tax_type"], 0) + _units(t.get("taxable_amount"), v.decimals)
        widest = max(bases, key=lambda tax_type: abs(bases[tax_type]), default=None)
        base = carrying(widest) if widest else weights  # the tax whose taxable amount is VLATXA
        atxas = _split(v.atxa, base, unit)
        # Half the tax on a meals line is in its expense amount (D-90): not E1's to post, so neither in its share
        # of the recoverable tax nor in its tax.
        meals = [_units(e.get("meals_tax"), v.decimals) if v.meals else 0 for e in v.entries]
        shares = [s - m for s, m in zip(shares, meals, strict=True)]
        own = _claimed_shares(v) if v.meals else None
        if own is not None:  # the distribution's own shares, not split again with other cents (B-867)
            shares = own
        stams = [s if v.expensed else s + _units(e.get("non_recoverable_tax"), v.decimals) - m
                 for e, s, m in zip(v.entries, shares, meals, strict=True)]  # fmt: skip
        stams[max(range(len(base)), key=lambda i: abs(base[i]))] += v.stam - sum(stams)
        grosses = [e["cents"] + s for e, s in zip(v.entries, shares, strict=True)]
        # An exempt line's pay item says so (its own VLEXR1) only when no tax is put on it.
        line_codes = [c if c != exempt or not s else v.tax_code for c, s in zip(line_codes, stams, strict=True)]
        headers = [
            {**common, "VLEDLN": n, "VLEXR1": line_codes[n - 1], **amounts(g, s, a, line_codes[n - 1]), **extras}
            for n, (g, s, a) in enumerate(zip(grosses, stams, atxas, strict=True), 1)
        ]
        return headers, [dist_row(n, e) for n, e in enumerate(v.entries, 1)], []
    header = {**common, "VLEDLN": 1, **amounts(v.gross, v.stam, v.atxa), **extras}
    return [header], [dist_row(n, e) for n, e in enumerate(v.entries, 1)], []


def _claimed_shares(v: _Voucher) -> list[int] | None:
    """Each distribution line's claimable share of the recoverable taxes E1 posts, as the GL distribution has them
    (``tax.tax_shares``, half of a meals line's share: D-90), in the currency's units; None when the invoice's lines
    can't be read back or don't match the distribution lines (then the pay items split the tax themselves)."""
    from ..coding.tax import half_down, is_meals, tax_shares
    from ..core.schema import InvoiceCoding
    from .exports import entry_lines

    try:
        coding = InvoiceCoding.model_validate({k: x for k, x in v.final.items() if k != "gl_distribution"})
    except ValueError:
        return None
    items = v.final.get("line_items") or []
    behind = entry_lines(v.entries, items)
    position = {id(li): i for i, li in enumerate(items)}
    lines = [position.get(id(li)) if li is not None else None for li in behind]
    if len(coding.line_items) != len(items):
        return None
    meals_codes = {str(e.get("gl_code") or "").strip() for e in v.entries if e.get("meals_tax")}
    places = v.decimals
    per_line = [0] * len(items)
    for tl in coding.tax_lines:
        if tl.tax_type not in v.recoverable_types or abs(tl.tax_amount) < 0.005:
            continue
        for idx, share in tax_shares(coding.line_items, tl, places, coding.tax_lines):
            meal = is_meals(coding.line_items[idx].predicted_gl_code, meals_codes)
            per_line[idx] += _units(half_down(share, places) if meal else share, v.decimals)
    shares = [per_line[i] if i is not None else 0 for i in lines]
    return shares if sum(shares) == v.recoverable else None


def _entry_taxes(entries: list[dict[str, Any]], items: list[dict[str, Any]]) -> list[set[str]]:
    """The taxes the invoice line behind each distribution line is marked with (none for a tax line)."""
    from .exports import entry_lines

    return [set((li or {}).get("taxes_applied") or []) for li in entry_lines(entries, items)]


def glba(final: dict[str, Any], settings: JdeSettings) -> str:
    """VLGLBA of an invoice: the value mapped to its Pay from account, the code's case aside ("" when none)."""
    mapped = {k.upper(): v for k, v in settings.bank_glba.items()}
    return mapped.get(str(final.get("pay_from_account") or "").strip().upper(), "")


def bank_notes(invoices: list[dict[str, Any]], settings: JdeSettings) -> dict[int, list[str]]:
    """{invoice id: [note]} for the invoices whose Pay from account has no G/L bank account mapped, while the mapping
    is in use (B-684): their VLGLBA is blank and E1 pays them from its default bank (AAI PB), which may not be the
    account AP chose. A note to check before loading, never a reason to hold an invoice back."""
    if not settings.bank_glba:
        return {}
    out: dict[int, list[str]] = {}
    for inv in invoices:
        final = inv.get("final_output") or {}
        if glba(final, settings):
            continue
        code = str(final.get("pay_from_account") or "").strip()
        inv_id = int(inv.get("id") or 0)
        out[inv_id] = [  # whole sentences (B-689)
            f"AP Coder #{inv_id} is paid from {code}, which has no G/L bank account (VLGLBA) mapped: E1 pays it from "
            "its default bank (AAI PB), which may not be that account."
            if code
            else f"AP Coder #{inv_id} has no Pay from account: E1 pays it from its default bank (AAI PB)."
        ]
    return out


def header_columns(settings: JdeSettings) -> list[str]:
    extra = [c for c in settings.po_extra_columns if c not in HEADER_COLUMNS] if settings.po_matched else []
    bank = ["VLGLBA"] if settings.bank_glba and "VLGLBA" not in extra else []
    return HEADER_COLUMNS + bank + extra


def _csv(columns: list[str], rows: list[dict[str, Any]]) -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(columns)
    for r in rows:
        values = ["" if r.get(c) is None else r.get(c) for c in columns]
        # The text as it is: a database loader reads the Z-files, not Excel, so no spreadsheet quoting (an
        # apostrophe before "+A-100" would be loaded with it and JDE's supplier + invoice number check would miss).
        writer.writerow([v[: TEXT_WIDTHS[c]] if c in TEXT_WIDTHS and isinstance(v, str) else v
                         for c, v in zip(columns, values, strict=True)])  # fmt: skip
    return buf.getvalue().encode("utf-8")  # no BOM: database loaders read the first header as-is


def export_batch(
    invoices: list[dict[str, Any]], settings: JdeSettings, vendor_lookup: VendorLookup = None, *,
    batch: int | str = "", fx_rates: Mapping[str, float] | None = None, on: dt.date | None = None,
    prepared: list[tuple[str, _Voucher]] | None = None, site: str = "",
) -> dict[str, bytes]:  # fmt: skip
    """{"F0411Z1.csv": …, "F0911Z1.csv": …[, "F0411Z1T.csv": …]} for these invoices (validated first)."""
    headers: list[dict[str, Any]] = []
    dists: list[dict[str, Any]] = []
    matches: list[dict[str, Any]] = []
    if prepared is None:
        prepared = _vouchers(invoices, settings, vendor_lookup, on)
    for inv, (an8, v) in zip(invoices, prepared, strict=True):
        h, d, m = voucher_rows(inv, an8, settings, batch=batch, fx_rates=fx_rates, on=on, prepared=v, site=site)
        headers += h
        dists += d
        matches += m
    files = {HEADER_FILE: _csv(header_columns(settings), headers), DIST_FILE: _csv(DIST_COLUMNS, dists)}
    if settings.po_matched and matches:
        files[MATCH_FILE] = _csv(MATCH_COLUMNS, matches)
    return files
