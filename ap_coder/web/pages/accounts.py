"""GL accounts & tax: the codes the AI may use, how each sales tax posts, the coding policy and our bank accounts
(the account an invoice is paid from, D-66). The fixed coding rules have a page of their own (``pages/rules.py``,
D-217): ``?tab=rules`` goes there.

``GET /accounts?tab=gl|cc|tax|policy|bank``. GL accounts, cost centers and bank accounts are imported from a
spreadsheet (upload, then see it and pick the columns: ``?file=``; their specs, ``SPECS``: ``erp/import_specs``, the
column choices ``files.spec_sheet``), edited in place and downloaded as CSV (``GET /accounts/<table>.csv``). Before
any GL account exists, the sample setup can be loaded instead.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

import pandas as pd
from fastapi import File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from ap_coder.coding import banks
from ap_coder.coding.tax import DEFAULT_RATES_PATH, RATES_FILE, TAX_TYPES, TREATMENTS, TaxRateTable, rates_path
from ap_coder.core import ui
from ap_coder.core.reference_data import short_name
from ap_coder.core.safe import csv_cell
from ap_coder.core.store import Store, load_sample_setup
from ap_coder.erp.import_specs import BANK_ACCOUNTS, COST_CENTERS, GL_ACCOUNTS
from ap_coder.web.core import (
    STALE_PAGE,
    done,
    flash_message,
    get_store,
    one_connection,
    page_router,
    page_version,
    posted_form,
    refuse,
    render,
    reviewer,
    short_path,
    stale_page,
)
from ap_coder.web.files import (
    NO_ROWS,
    SheetError,
    download,
    drop_upload,
    first_rows,
    jde_refusal,
    pick_refusal,
    picked_columns,
    read_upload,
    receive_sheet,
    spec_sheet,
)
from ap_coder.web.pages.process import tax_types_mapped

router = page_router()  # each plain handler: one connection a request

TABLES = {"gl_accounts": ("gl", "GL account"), "cost_centers": ("cc", "cost center")}
SPECS = {"gl_accounts": GL_ACCOUNTS, "cost_centers": COST_CENTERS}  # what each list's file takes (D-234)
TABS = {
    "gl": "GL accounts",
    "cc": "Cost centers",
    "tax": "Sales tax accounts",  # (not the Sales tax page under More: M2)
    "policy": "Coding policy",
    "bank": "Bank accounts",
}
BLANK_ROWS = 3  # empty rows under a list, to add more
EXPENSE_TO_LINE = "expense_to_line"
WHERE = {
    "GST": "Federal · all provinces",
    "HST": "ON · NB · NL · NS · PE",
    "PST": "BC · SK · MB (RST)",
    "QST": "Quebec (TVQ)",
    "OTHER": "Outside Canada",
}

MEALS_LABEL = "Meals & entertainment (50% tax credit)"


def table_for(table: str) -> tuple[str, str]:
    if table not in TABLES:
        raise HTTPException(status_code=404, detail="No such list.")
    return TABLES[table]


def accounts_csv_bytes(rows: list[dict[str, Any]]) -> bytes:
    """The list as a CSV download (cells made safe for Excel, UTF-8 with a BOM)."""
    rows = [{**r, "meals": "yes" if r["meals"] else ""} if "meals" in r else r for r in rows]  # imported back (D-90)
    df = pd.DataFrame(rows, columns=["code", "description", "category"]) if not rows else pd.DataFrame(rows)
    return df.map(csv_cell).to_csv(index=False).encode("utf-8-sig")


def account_context(store: Store, table: str, file: str, sheet: str) -> dict[str, Any]:
    _tab, noun = table_for(table)
    rows = store.list_accounts(table)
    imp, imp_error = None, ""
    if file:
        try:
            upload, df, sheets = read_upload(file, sheet or None)
            imp = spec_sheet(file, upload, df, sheets, sheet, SPECS[table])
        except SheetError as exc:
            imp_error = str(exc)
    counts = Counter(r["category"] or "Uncategorised" for r in rows)
    return {
        "table": table,
        "noun": noun,
        "rows": rows,
        "version": page_version(rows),  # posted with the grid (B-655)
        "blank": BLANK_ROWS,
        "categories": counts.most_common(),
        "meals": table == "gl_accounts",  # the meals and entertainment column (D-90)
        "meals_label": MEALS_LABEL,
        "imp": imp,
        "imp_error": imp_error,
        "spec": SPECS[table],
        "chart": _from_chart(store),
    }


def _from_chart(store: Store) -> dict[str, int] | None:
    """The chart of accounts and business units loaded on JD Edwards files, which give the GL accounts and the cost
    centers (``chart.account_lists``): how many accounts and business units (None while no chart is loaded)."""
    rows = store.chart_accounts()
    if not rows:
        return None
    return {"accounts": sum(1 for row in rows if not row.is_header), "units": len(store.business_units()) or
            len({row.bu for row in rows})}  # fmt: skip


def tax_context(store: Store) -> dict[str, Any]:
    accounts = store.list_accounts("gl_accounts")
    treatments = store.tax_treatments()
    rates = TaxRateTable.load().in_force()
    return {
        "taxes": [
            {"type": t, "where": WHERE.get(t, ""), "treatment": treatments[t].treatment, "gl": treatments[t].gl_code}
            for t in TAX_TYPES
        ],
        "treatments": {k: v.split(" (")[0] for k, v in TREATMENTS.items()},
        "gl_options": [(a["code"], f"{a['code']} · {short_name(a['description'])}") for a in accounts],
        "expense_to_line": EXPENSE_TO_LINE,
        "rates": rates,
        "rates_source": "your copy in the data folder"
        if rates_path() != DEFAULT_RATES_PATH
        else "the rate table that comes with AP Coder (updated with it)",
        "rates_help": f"Rate table: {short_path(rates_path())}. To change a rate before an update brings it, copy "
        f"data/{RATES_FILE} into your data folder and edit the copy.",
    }


@router.get("/accounts", response_class=HTMLResponse)
def accounts_page(request: Request, tab: str = "gl", file: str = "", sheet: str = "") -> Response:
    if tab == "rules":  # an older link: the rules have their own page (D-217)
        return RedirectResponse("/rules", status_code=303)
    store = get_store(request)
    tab = tab if tab in TABS else "gl"
    gl = store.list_accounts("gl_accounts")
    cc = store.list_accounts("cost_centers")
    mapped = tax_types_mapped(store)
    policy = store.get_setting("policy_notes")
    policy_lines = [n for n in policy.splitlines() if n.strip() and not n.lstrip().startswith("#")]
    categories = len({a["category"] for a in gl if a["category"]})
    tiles = [
        ("GL accounts", len(gl), ui.plural(categories, "category", "categories")),
        ("Cost centers", len(cc) or "—", "in use" if cc else "optional"),
        ("Sales taxes mapped", f"{mapped}/{len(TAX_TYPES)}", "GST · HST · PST · QST · other"),
        (
            "Coding policy",
            len(policy_lines),
            f"plain-English lines · {ui.plural(len(store.coding_rules()), 'fixed rule')}",
        ),
    ]
    context: dict[str, Any] = {"tab": tab, "tabs": TABS, "tiles": tiles, "has_gl": bool(gl), "policy": policy}
    if tab in ("gl", "cc"):
        context["acc"] = account_context(store, "gl_accounts" if tab == "gl" else "cost_centers", file, sheet)
    elif tab == "tax":
        context.update(tax_context(store))
    elif tab == "bank":
        context["bank"] = bank_context(store, gl, file, sheet)
    return render(request, "accounts.html", "accounts", **context)


# --- Sample setup ----------------------------------------------------------------------------------------------


@router.post("/accounts/sample")
def use_sample(request: Request) -> JSONResponse:
    """The sample GL accounts, tax mapping and policy (to try AP Coder before importing yours), without cost
    centers: each line needs only a GL account (D-53)."""
    store = get_store(request)
    if store.list_accounts("gl_accounts"):
        return refuse("You already have GL accounts: the sample setup is only for an empty list.")
    load_sample_setup(store, cost_centers=False, actor=reviewer())
    return done(
        request,
        "/accounts",
        flash_message(
            "Sample setup loaded: GL accounts and tax mapping (no cost centers: each line needs only a GL account).",
            "ok",
            icon="science",
        ),  # fmt: skip
    )


# --- Our bank accounts (D-66) --------------------------------------------------------------------------------------
# The company's own accounts, the ones invoices are paid from ("Pay from" on the review screen). Only the last 4 digits
# of an account number are ever shown, here too: the grid's box is blank (typing replaces the number), and there is a
# template to download, not the list.

BANK_STAMP_SECRET = "bank_page_secret"  # (settings) the key of the grid's version stamp
BANK_TEMPLATE = (
    "code,name,currency,institution,transit,account_number,gl_account,default,active\r\n"
    "CAD-OP,Sample Bank CAD operating,CAD,000,00000,0000001234,,yes,yes\r\n"
)


def bank_shown(store: Store) -> list[dict[str, Any]]:
    """The bank accounts as the Bank accounts tab shows them (its ``page_version`` is posted with the grid)."""
    return store.bank_accounts()


def bank_version(store: Store, rows: list[dict[str, Any]]) -> str:
    """The Bank accounts grid's version stamp: an HMAC of the accounts with this database's own secret, so the page
    holds nothing a short account number could be found again from (B-687), and any change makes a new stamp."""
    import hashlib
    import hmac
    import json
    import secrets

    secret = store.get_setting(BANK_STAMP_SECRET)
    if not secret:
        secret = secrets.token_hex(32)
        store.set_setting(BANK_STAMP_SECRET, secret)
    text = json.dumps(rows, sort_keys=True, default=repr).encode()
    return hmac.new(secret.encode(), text, hashlib.sha256).hexdigest()[:16]


def bank_context(store: Store, gl: list[dict[str, Any]], file: str, sheet: str) -> dict[str, Any]:
    rows = bank_shown(store)
    imp, imp_error = None, ""
    if file:
        try:
            upload, df, sheets = read_upload(file, sheet or None)
            imp = spec_sheet(file, upload, df, sheets, sheet, BANK_ACCOUNTS)  # (only the code is needed: B-2402)
        except SheetError as exc:
            imp_error = str(exc)
    return {
        "rows": [{**r, "shown": banks.masked(r["account_number"]), "label": banks.label(r)} for r in rows],
        "version": bank_version(store, rows),  # posted with the grid (B-655), holding no account number (B-687)
        "blank": BLANK_ROWS,
        "gl_options": [(a["code"], f"{a['code']} · {short_name(a['description'])}") for a in gl],
        "imp": imp,
        "imp_error": imp_error,
        "spec": BANK_ACCOUNTS,
    }


def _gl_codes(store: Store) -> set[str] | None:
    """The GL accounts a bank account's cash GL must be one of (None while no chart is loaded)."""
    codes = {a["code"] for a in store.list_accounts("gl_accounts")}
    return codes or None


@router.post("/accounts/bank/upload")
async def bank_upload(request: Request, file: UploadFile = File(...)) -> JSONResponse:  # noqa: B008
    token, problem = await receive_sheet(file)
    if problem:
        return refuse(problem)
    return done(request, f"/accounts?tab=bank&file={token}#import")


@router.post("/accounts/bank/import")
async def bank_import(request: Request) -> JSONResponse:
    form = await posted_form(request)
    return await run_in_threadpool(_bank_import, request, form)


@one_connection
def _bank_import(request: Request, form: Any) -> JSONResponse:
    store = get_store(request)
    token, sheet = str(form.get("file") or ""), str(form.get("sheet") or "")
    try:
        _upload, df, _sheets = read_upload(token, sheet or None)
    except SheetError as exc:
        return refuse(str(exc))
    if refusal := jde_refusal(first_rows(df)):  # (JD Edwards' own reports: on JD Edwards files only, B-2404)
        return refuse(refusal)
    if df.empty:
        return refuse(NO_ROWS)
    picked = picked_columns(form, banks.ALIASES, list(df.columns), {})
    if refusal := pick_refusal(BANK_ACCOUNTS, picked):
        return refuse(refusal)
    records = [{f: rec.get(c) for f, c in picked.items()} for rec in df.to_dict("records")]
    keep = {r["code"]: r for r in store.bank_accounts()}
    for r in records:  # a column not picked keeps what the account has
        old = keep.get(str(r.get("code") or "").strip())
        if old:
            for f in banks.COLUMNS:
                if f not in picked:
                    r[f] = old[f]
    rows, problems = banks.clean_rows(records, _gl_codes(store))
    if problems:
        return refuse("Nothing was imported: " + " ".join(problems[:6]) + (" …" if len(problems) > 6 else ""))
    if not rows:
        return refuse("No row has a code in the column picked: nothing was imported.")
    result = store.save_bank_accounts(rows, actor=reviewer(), imported=True)
    drop_upload(token)
    return done(request, "/accounts?tab=bank",
                f"Imported: {result['added']} added, {result['updated']} updated.")  # fmt: skip


@router.post("/accounts/bank/save")
async def bank_save(request: Request) -> JSONResponse:
    """The edited grid: each row by its code (a new row's code typed in), the account number kept when its box is
    left blank, the default (one per currency) and whether it is in use."""
    form = await posted_form(request)
    return await run_in_threadpool(_bank_save, request, form)


@one_connection
def _bank_save(request: Request, form: Any) -> JSONResponse:
    store = get_store(request)
    posted = form.get("version")  # an account imported meanwhile would be overwritten (B-655)
    if posted is not None and str(posted) != bank_version(store, bank_shown(store)):
        return refuse(STALE_PAGE, 409)
    stored = {r["code"]: r for r in store.bank_accounts()}
    cols = {f: form.getlist(f) for f in ("code", "name", "currency", "institution", "transit", "account_number",
                                         "gl_account")}  # fmt: skip
    defaults, active = {str(v) for v in form.getlist("is_default")}, {str(v) for v in form.getlist("active")}
    records = []
    for n in range(len(cols["code"])):
        row = {f: str(values[n] if n < len(values) else "").strip() for f, values in cols.items()}
        old = stored.get(row["code"])
        if old and not row["account_number"]:
            row["account_number"] = old["account_number"]  # left blank: unchanged (it is never shown in full)
        row["is_default"] = "yes" if str(n) in defaults else "no"
        row["active"] = "yes" if (str(n) in active or not old) else "no"
        records.append(row)
    rows, problems = banks.clean_rows(records, _gl_codes(store))
    if problems:
        return refuse(" ".join(problems[:6]))
    result = store.save_bank_accounts(rows, actor=reviewer())
    changed = result["added"] + result["updated"]
    return done(request, "/accounts?tab=bank",
                f"Saved: {ui.plural(changed, 'bank account')} changed." if changed else "Nothing changed.")  # fmt: skip


@router.post("/accounts/bank/remove")
def bank_remove(request: Request, remove: str = Form("")) -> JSONResponse:
    """Remove one bank account (the grid's Remove button posts its code)."""
    if not get_store(request).remove_bank_account(remove.strip(), actor=reviewer()):
        return refuse("That bank account is not in the list any more.")
    return done(request, "/accounts?tab=bank", f"Bank account {remove.strip()} removed.")


@router.get("/accounts/bank/template.csv")
def bank_template() -> Response:
    """The columns a bank account file takes, with one made-up row (never the accounts themselves: only the last 4
    digits of an account number are ever shown)."""
    return download(BANK_TEMPLATE.encode("utf-8-sig"), "bank_accounts_template.csv", "text/csv")


# --- GL accounts and cost centers ----------------------------------------------------------------------------------


@router.post("/accounts/{table}/upload")
async def accounts_upload(request: Request, table: str, file: UploadFile = File(...)) -> JSONResponse:  # noqa: B008
    tab, _noun = table_for(table)
    token, problem = await receive_sheet(file)
    if problem:
        return refuse(problem)
    return done(request, f"/accounts?tab={tab}&file={token}#import")


@router.post("/accounts/{table}/import")
async def accounts_import(request: Request, table: str) -> JSONResponse:
    form = await posted_form(request)
    return await run_in_threadpool(_accounts_import, request, table, form)


@one_connection
def _accounts_import(request: Request, table: str, form: Any) -> JSONResponse:
    tab, _noun = table_for(table)
    store = get_store(request)
    token, sheet = str(form.get("file") or ""), str(form.get("sheet") or "")
    try:
        _upload, df, _sheets = read_upload(token, sheet or None)
    except SheetError as exc:
        return refuse(str(exc))
    if refusal := jde_refusal(first_rows(df)):  # (JD Edwards' own reports: on JD Edwards files only, B-2404)
        return refuse(refusal)
    if df.empty:
        return refuse(NO_ROWS)
    picked = picked_columns(form, {c.field: c.label for c in SPECS[table].columns}, list(df.columns), {})
    if refusal := pick_refusal(SPECS[table], picked):
        return refuse(refusal)
    try:
        result = store.import_accounts(
            table,
            df.to_dict("records"),
            picked["code"],
            picked.get("description"),
            picked.get("category"),
            replace_all=bool(form.get("replace")),
            actor=reviewer(),
            meals_column=picked.get("meals") or None,
        )
    except ValueError as exc:  # a replace with no code in any row: nothing was changed
        return refuse(str(exc))
    drop_upload(token)
    return done(
        request,
        f"/accounts?tab={tab}",
        f"Imported: {result['added']} added, {result['updated']} updated, {result['skipped']} skipped "
        "(blank or repeated codes).",
    )


@router.post("/accounts/{table}/save")
async def accounts_save(request: Request, table: str) -> JSONResponse:
    """The edited list replaces the stored one: ticked rows are deleted, blank codes left out."""
    form = await posted_form(request)
    return await run_in_threadpool(_accounts_save, request, table, form)


@one_connection
def _accounts_save(request: Request, table: str, form: Any) -> JSONResponse:
    tab, noun = table_for(table)
    store = get_store(request)
    stale = stale_page(form, store.list_accounts(table))  # an account imported meanwhile would be deleted (B-655)
    if stale is not None:
        return stale
    codes, descriptions, categories = form.getlist("code"), form.getlist("description"), form.getlist("category")
    deleted = {str(d) for d in form.getlist("delete")}
    meals = {str(m) for m in form.getlist("meals")}  # the rows ticked "Meals & entertainment" (D-90)
    keep, removed = [], 0
    for n, code in enumerate(codes):
        code = str(code).strip()
        if str(n) in deleted:
            removed += bool(code)
            continue
        if not code:
            continue
        keep.append(
            {
                "code": code,
                "description": str(descriptions[n] if n < len(descriptions) else "").strip(),
                "category": str(categories[n] if n < len(categories) else "").strip(),
                **({"meals": str(n) in meals} if table == "gl_accounts" else {}),
            }
        )
    seen = Counter(r["code"] for r in keep)
    dupes = sorted(c for c, k in seen.items() if k > 1)
    if dupes:
        return refuse(f"These codes appear more than once: {', '.join(dupes)}")
    store.save_accounts(table, keep, actor=reviewer())
    nouns = ui.plural(len(keep), noun)
    return done(request, f"/accounts?tab={tab}", f"Saved {nouns}" + (f", removed {removed}." if removed else "."))


@router.get("/accounts/{table}.csv")
def accounts_csv(request: Request, table: str) -> Response:
    table_for(table)
    return download(accounts_csv_bytes(get_store(request).list_accounts(table)), f"{table}.csv", "text/csv")


# --- Sales tax, policy ---------------------------------------------------------------------------------------------


@router.post("/accounts/tax")
async def save_tax(request: Request) -> JSONResponse:
    """How each sales tax posts: its treatment, and its GL account unless it is added to the expense lines."""
    form = await posted_form(request)
    return await run_in_threadpool(_save_tax, request, form)


@one_connection
def _save_tax(request: Request, form: Any) -> JSONResponse:
    store = get_store(request)
    codes = {a["code"] for a in store.list_accounts("gl_accounts")}
    chosen = {}
    for tax_type in TAX_TYPES:
        treatment = str(form.get(f"treatment_{tax_type}") or "")
        if treatment not in TREATMENTS:
            return refuse(f"Choose how {tax_type} posts.")
        gl = "" if treatment == EXPENSE_TO_LINE else str(form.get(f"gl_{tax_type}") or "")
        if gl and gl not in codes:
            return refuse(f"GL account {gl} is not in your GL list.")
        chosen[tax_type] = (treatment, gl)
    for tax_type, (treatment, gl) in chosen.items():
        store.set_tax_treatment(tax_type, treatment, gl, actor=reviewer())
    return done(request, "/accounts?tab=tax", "Tax setup saved.")


@router.post("/accounts/policy")
def save_policy(request: Request, notes: str = Form("")) -> JSONResponse:
    get_store(request).set_setting("policy_notes", notes, actor=reviewer())
    return done(request, "/accounts?tab=policy", "Coding policy saved.")
