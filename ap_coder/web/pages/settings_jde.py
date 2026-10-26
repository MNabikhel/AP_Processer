"""Settings → JD Edwards E1: company, batch, amount and account formats, the GL → BU.Object.Subsidiary mapping, the
province → tax area table, the payment terms → terms code table (D-91), PO-matched vouchers and supplier address
numbers. Saved with ``ap_coder.erp.jde.save``
(logged), checked with ``jde.settings_problems``. The **API connection** card (D-104: the AIS address, environment,
user, password kept in the .env, step 1 and step 2, Test connection, Sync now) is ``ap_coder.erp.jde_ais`` /
``jde_sync``; an Admin's, like the rest of the tab.

The editable tables (GL mapping, address numbers) are rows of inputs posted as lists (``gl_code``, ``bu``... one value
per row); a row without a GL code (or vendor) is dropped, which is how a row is removed without JavaScript.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, Response

from ap_coder.coding.tax import OUTSIDE_CANADA, PROVINCES, province_label
from ap_coder.coding.terms import terms_key
from ap_coder.core import ui
from ap_coder.core.memory import vendor_key
from ap_coder.core.store import Store
from ap_coder.erp import jde, jde_ais, jde_sync, jde_worksheet
from ap_coder.web import minimd
from ap_coder.web.core import done, get_store, jobs, posted_form, refuse, reviewer
from ap_coder.web.files import TOO_BIG, SheetError, read_capped, read_csv, read_excel

router = APIRouter()

URL = "/settings/jde"

CHECKLIST = """\
Confirm with your JDE team before the first live load:

- **Company numbers** (VLCO / VNCO / VLPKCO) and the business units the vouchers and lines go to.
- **Document type** (PV, or a custom one for imported vouchers) and the **P0400047 version** R04110ZA uses
  (its processing options decide defaults, tax recalculation and edits).
- **Tax areas and explanation codes** per province (the names here are placeholders): V, C, B, E and how the
  tax AAIs (PT / GT) post GST, HST, QST and PST.
- **Account mapping**: AP Coder GL code (+ cost center) → BU.Object.Subsidiary, and subledgers if used.
- **Batch numbering** (VLEDBT): the prefix and the user ID R04110ZA is run for.
- **Amounts**: implied decimals or a decimal point; the tax coded to G/L accounts, gross only or taxable / tax
  amounts. With the tax coded to G/L accounts: does R04110ZA, with code E and no tax area, post our G/L lines as
  sent (the tax lines to the tax accounts) and work out no tax of its own?
- **Line numbering** (VNEDLN): sequential, or matching the pay item line (VLEDLN).
- **Duplicate-invoice edit** in the processing options (H = hard error, I = warning): AP Coder checks
  supplier + invoice number too, but E1 should be the last word.
- **The PO match path**: whether PO-matched vouchers come in through the Z-tables at all, and the columns of
  the match file (marked *unconfirmed*).
"""

# (field, label, max characters, help) of the voucher header and the "More fields" group.
HEADER_FIELDS = [
    ("company", "Company (VLCO)", 5, "Up to 5 digits; zero-padded."),
    ("user", "User ID (VLEDUS)", 10, "The user R04110ZA is run for."),
    ("batch_prefix", "Batch prefix (VLEDBT)", 9, "Batch number = prefix + AP Coder batch."),
    ("document_type", "Document type (VLDCT)", 2, "PV, or blank."),
    ("domestic_currency", "Domestic currency", 3, ""),
]
MORE_FIELDS = [
    ("pay_status", "Pay status (VLPST)", 1, ""),
    ("header_bu", "Header business unit (VLMCU)", 12, ""),
    ("credit_document_type", "Credit note document type", 2, "Blank: the document type above, with negative amounts."),
    ("po_document_type", "PO document type (VLPDCT)", 2, ""),
    ("po_company", "PO company (VLPKCO)", 5, "Blank: the company."),
    ("self_assessed_code", "Code for self-assessed PST", 2, ""),
    ("exempt_code", "Code when no tax is charged", 2, ""),
]
UPPER = {"document_type", "domestic_currency", "credit_document_type", "po_document_type", "self_assessed_code",
         "exempt_code"}  # fmt: skip
# (field, label, choices, help) of the radio groups.
CHOICES = [
    ("amount_mode", "Amounts", jde.AMOUNT_MODES,
     "Tax coded to G/L accounts: your tax accounts are in GL accounts & tax, each mapped below like any account; the "
     "tax areas are not used. Otherwise gross or tax amounts, never both: E1 rejects a voucher with both."),
    ("decimals", "Amount format", jde.DECIMAL_MODES, ""),
    ("account_mode", "Account", jde.ACCOUNT_MODES, ""),
    ("line_numbering", "Line numbering (VNEDLN) · unconfirmed", jde.LINE_NUMBERING,
     "Oracle's notes say VNEDLN must match VLEDLN; most loads number the lines 1, 2, 3 under pay item 1."),
]  # fmt: skip
TOGGLES = ["po_matched", "use_default_rule", "bu_from_cost_center", "send_due_date", "send_po_reference",
           "dist_tax_columns"]  # fmt: skip
GL_COLUMNS = [("gl_code", "GL code", 0), ("cost_center", "Cost center", 0), ("bu", "BU", 12), ("obj", "Object", 6),
              ("sub", "Subsidiary", 8), ("sbl", "Subledger", 8), ("sblt", "Subledger type", 1)]  # fmt: skip
TAX_CODES = ["V", "C", "B", "E", ""]


def jde_context(store: Store) -> dict[str, Any]:
    js = jde.load(store)
    names = {v["vendor_key"]: v["display_name"] for v in store.master_vendors()}
    order = [*PROVINCES, OUTSIDE_CANADA]
    return {
        "js": js,
        "header_fields": HEADER_FIELDS,
        "more_fields": MORE_FIELDS,
        "choices": CHOICES,
        "gl_dates": jde.GL_DATES,
        "currencies": ", ".join(js.currencies),
        "extras": ", ".join(f"{k}={v}" for k, v in js.po_extra_columns.items()),
        "bank_rows": bank_rows(store, js),
        "gl_columns": GL_COLUMNS,
        "gl_rows": [vars(m) for m in js.gl_map],
        "tax_rows": [
            {
                "province": p,
                "shown": "—" if p == OUTSIDE_CANADA else p,
                "name": province_label(p),
                "tax_area": js.tax_map[p].tax_area,
                "code": js.tax_map[p].code,
            }
            for p in order
            if p in js.tax_map
        ],
        "tax_codes": TAX_CODES,
        "an8_rows": [{"vendor": names.get(k, k), "an8": a} for k, a in js.an8_overrides.items()],
        # The payment terms table (D-91): the examples, pre-filled and not in use, until the pilot tester saves it.
        "terms_examples": not js.terms_codes,
        "terms_rows": [{"terms": k, "code": c} for k, c in (js.terms_codes or jde.EXAMPLE_TERMS_CODES).items()],
        "checklist": minimd.render(CHECKLIST),
        "worksheet_labels": {q.id: q.question for q in jde_worksheet.QUESTIONS},
        "api": api_context(store),
    }


def api_context(store: Store) -> dict[str, Any]:
    """What the API connection card shows (never the password)."""
    cfg = jde_ais.load(store)
    usable, note = jde_ais.status(cfg)
    synced = jde_sync.state(store)
    own, copy_path = jde_ais.mapping_paths()
    return {
        "cfg": cfg, "usable": usable, "note": note, "auth_modes": jde_ais.AUTH_MODES,
        "tested": jde_ais.last_test(store),
        "synced_at": str(synced.get("at") or "")[:16].replace("T", " "),
        "tried_at": str(synced.get("tried") or "")[:16].replace("T", " "),
        "report": jde_sync.report_lines(synced.get("report") or {}), "mapping": str(copy_path or own),
        "mapping_copy": copy_path is not None,
    }  # fmt: skip


def bank_rows(store: Store, js: jde.JdeSettings) -> list[dict[str, str]]:
    """Each bank account in use, with its G/L bank account (VLGLBA), then any code mapped that is no longer one."""
    from ap_coder.coding import banks

    mapped = {k.upper(): v for k, v in js.bank_glba.items()}
    rows = [{"code": a["code"], "label": banks.label(a), "glba": mapped.pop(a["code"].upper(), "")}
            for a in store.bank_accounts(active_only=True)]  # fmt: skip
    rows += [{"code": c, "label": f"{c} (not one of your bank accounts in use)", "glba": v} for c, v in mapped.items()]
    return rows


def copy(js: jde.JdeSettings) -> jde.JdeSettings:
    return jde.JdeSettings.from_json(js.to_json())


@router.post("/settings/jde")
async def save_general(request: Request) -> JSONResponse:
    store = get_store(request)
    form = await posted_form(request)

    def text(name: str) -> str:
        return str(form.get(name) or "").strip()

    new = copy(jde.load(store))
    for name, _, _, _ in HEADER_FIELDS + MORE_FIELDS:
        setattr(new, name, text(name).upper() if name in UPPER else text(name))
    new.currencies = [c.strip().upper() for c in text("currencies").replace(";", ",").split(",") if c.strip()]
    for name, label, choices, _ in CHOICES:
        value = text(name)
        if value not in choices:
            return refuse(f"Choose one of the options for {label}.")
        setattr(new, name, value)
    if text("gl_date") not in jde.GL_DATES:
        return refuse("Choose the G/L date.")
    new.gl_date = text("gl_date")
    for name in TOGGLES:
        setattr(new, name, str(form.get(name) or "") in ("1", "on", "true"))
    new.default_bu = text("default_bu")
    pairs = {}
    for part in text("extras").split(","):
        name, _, value = part.partition("=")
        if name.strip():
            pairs[name.strip().upper()] = value.strip()
    new.po_extra_columns = pairs
    if form.getlist("bank_code"):  # one box per bank account (B-684); a form without them keeps the mapping
        codes, values = form.getlist("bank_code"), form.getlist("bank_glba")
        new.bank_glba = {str(c).strip().upper(): str(values[i] if i < len(values) else "").strip()
                         for i, c in enumerate(codes)
                         if str(c).strip() and str(values[i] if i < len(values) else "").strip()}  # fmt: skip
    problems = jde.settings_problems(new)
    if problems:
        return refuse(" ".join(problems))
    changed = jde.save(store, new, actor=reviewer())
    return done(request, URL, "JD Edwards settings saved." if changed else "Nothing changed.")


@router.post("/settings/jde/gl-map")
async def save_gl_map(request: Request) -> JSONResponse:
    store = get_store(request)
    form = await posted_form(request)
    columns = {name: [str(v).strip() for v in form.getlist(name)] for name, _, _ in GL_COLUMNS}
    count = len(columns["gl_code"])
    if any(len(values) != count for values in columns.values()):
        return refuse("The mapping table arrived incomplete: reload the page and try again.")
    rows = [jde.GlMap(**{name: columns[name][i] for name in columns}) for i in range(count)]
    new = copy(jde.load(store))
    new.gl_map = [r for r in rows if r.gl_code]
    changed = jde.save(store, new, actor=reviewer())
    return done(request, URL + "#gl-map",
                f"{ui.plural(len(new.gl_map), 'mapping row')} saved." if changed else "Nothing changed.")  # fmt: skip


def read_sheet(name: str, content: bytes) -> list[dict[str, Any]]:
    """The rows of an uploaded CSV or Excel file (its first sheet), as text. Raises ValueError when it can't be
    read."""
    if not content:
        raise ValueError("the file is empty")
    try:  # a CSV as every page reads one: Windows-1252 and semicolon files too (B-1400)
        frame = read_excel(content, 0) if name.lower().endswith((".xlsx", ".xls")) else read_csv(content)
    except ValueError:
        raise
    except Exception as exc:  # noqa: BLE001 - empty, damaged or not a spreadsheet
        raise ValueError("it may be empty, damaged or another format") from exc
    frame.columns = [str(c) for c in frame.columns]
    return frame.fillna("").to_dict("records")


@router.post("/settings/jde/gl-map/import")
async def import_gl_map(
    request: Request,
    file: UploadFile = File(...),  # noqa: B008
    replace: str = Form(""),
) -> JSONResponse:
    store = get_store(request)
    name = file.filename or ""
    if not name.lower().endswith((".csv", ".xlsx", ".xls")):
        return refuse("Upload a CSV or Excel file (GL code, Cost center, BU, Object, Subsidiary).")
    content = await read_capped(file)  # never read whole, however large (B-1401)
    if content is None:
        return refuse(TOO_BIG)
    try:
        records = read_sheet(name, content)
    except SheetError as exc:  # already a whole sentence
        return refuse(str(exc))
    except ValueError as exc:
        return refuse(f"This file could not be read as a spreadsheet: {exc}.")
    rows = jde.gl_map_from_records(records)
    if not rows:
        return refuse("No row with a GL code was found: the file needs a GL code column.")
    new = copy(jde.load(store))
    kept = [] if replace in ("1", "on", "true") else new.gl_map
    keys = {(r.gl_code.upper(), r.cost_center.upper()) for r in rows}
    new.gl_map = [m for m in kept if (m.gl_code.upper(), m.cost_center.upper()) not in keys] + rows
    jde.save(store, new, actor=reviewer())
    return done(request, URL + "#gl-map", f"{ui.plural(len(rows), 'mapping row')} imported.")


@router.post("/settings/jde/tax-map")
async def save_tax_map(request: Request) -> JSONResponse:
    store = get_store(request)
    form = await posted_form(request)
    new = copy(jde.load(store))
    provinces = [str(p) for p in form.getlist("province")]
    areas = [str(a).strip().upper() for a in form.getlist("tax_area")]
    codes = [str(c).strip().upper() for c in form.getlist("code")]
    if not (len(provinces) == len(areas) == len(codes)):
        return refuse("The tax area table arrived incomplete: reload the page and try again.")
    for province, area, code in zip(provinces, areas, codes, strict=True):
        if province not in new.tax_map:
            continue
        if code not in TAX_CODES:
            return refuse(f"The code for {province} must be V, C, B, E or blank.")
        new.tax_map[province] = jde.TaxArea(area[:10], code)
    changed = jde.save(store, new, actor=reviewer())
    return done(request, URL + "#tax-map", "Tax areas saved." if changed else "Nothing changed.")


@router.post("/settings/jde/terms")
async def save_terms(request: Request) -> JSONResponse:
    """The payment terms table (D-91): each row's terms are kept by what they mean ("30 days" and "N30" are "Net 30"),
    so two rows meaning the same are refused, as are terms AP Coder can't read; a row without terms or a code is
    dropped. ``default_terms_code``: the code for terms not in the table (blank: E1 takes the supplier's own)."""
    store = get_store(request)
    form = await posted_form(request)
    terms = [str(t).strip() for t in form.getlist("terms")]
    codes = [str(c).strip().upper() for c in form.getlist("terms_code")]
    if len(terms) != len(codes):
        return refuse("The payment terms table arrived incomplete: reload the page and try again.")
    table: dict[str, str] = {}
    for printed, code in zip(terms, codes, strict=True):
        if not printed or not code:
            continue
        key = terms_key(printed)
        if not key:
            return refuse(f"“{printed}” are not payment terms AP Coder reads: type them as on an invoice, e.g. Net 30 "
                          "or 2/10 Net 30.")  # fmt: skip
        if key in table:
            return refuse(f"Two rows mean {key}: keep one.")
        if len(code) > jde.PTC_WIDTH:
            return refuse(f"The code for {key} must be {jde.PTC_WIDTH} characters or fewer (VLPTC).")
        table[key] = code
    default = str(form.get("default_terms_code") or "").strip().upper()
    if len(default) > jde.PTC_WIDTH:
        return refuse(f"The default code must be {jde.PTC_WIDTH} characters or fewer (VLPTC).")
    new = copy(jde.load(store))
    new.terms_codes, new.payment_terms_code = table, default
    changed = jde.save(store, new, actor=reviewer())
    return done(request, URL + "#terms",
                f"{ui.plural(len(table), 'payment terms row')} saved." if changed else "Nothing changed.")  # fmt: skip


@router.post("/settings/jde/an8")
async def save_an8(request: Request) -> JSONResponse:
    store = get_store(request)
    form = await posted_form(request)
    vendors = [str(v).strip() for v in form.getlist("vendor")]
    numbers = [str(a).strip() for a in form.getlist("an8")]
    if len(vendors) != len(numbers):
        return refuse("The address number table arrived incomplete: reload the page and try again.")
    new = copy(jde.load(store))
    new.an8_overrides = {vendor_key(v): a[:8] for v, a in zip(vendors, numbers, strict=True) if vendor_key(v) and a}
    changed = jde.save(store, new, actor=reviewer())
    return done(request, URL + "#an8", "Address numbers saved." if changed else "Nothing changed.")


# --- The worksheet for the JDE team -----------------------------------------------------------------------------


@router.get("/settings/jde/worksheet.xlsx")
def jde_worksheet_download(request: Request) -> Response:
    """Every question AP Coder needs the JDE team to answer, what it uses now, and a column for the answers."""
    store = get_store(request)
    js, lookup = jde.load(store), store.vendor_ids()
    data = jde_worksheet.build(
        js, store.list_accounts("gl_accounts"), store.vendor_summaries(),
        lambda name: jde.resolve_an8({"vendor_name": name}, lookup, js),
    )  # fmt: skip
    name = "AP Coder - questions for the JDE team.xlsx"
    return Response(data, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})  # fmt: skip


@router.post("/settings/jde/worksheet")
async def jde_worksheet_import(request: Request, file: UploadFile = File(...)) -> JSONResponse:  # noqa: B008
    """Apply the JDE team's answers; say what changed and what could not be used."""
    store = get_store(request)
    content = await file.read(10_000_001)
    if len(content) > 10_000_000:
        return refuse("This file is larger than 10 MB: it is not AP Coder's worksheet.")
    result = jde_worksheet.read(content, jde.load(store))
    if not result.changes and result.problems:
        return refuse(" ".join(result.problems[:5]))
    if result.changes:
        jde.save(store, result.settings, reviewer())
    from ap_coder.web.core import flash_message

    head = (f"{ui.plural(len(result.changes), 'answer')} applied from the JDE team's worksheet." if result.changes
            else "Nothing new in the worksheet.")  # fmt: skip
    messages = [flash_message(head, "ok", result.changes[:40])]
    if result.problems:
        messages.append(flash_message("Not used (fix them in the worksheet and import it again):", "warn",
                                      result.problems[:40]))  # fmt: skip
    return done(request, URL, *messages)


# --- The API connection (D-104) ---------------------------------------------------------------------------------

API_URL = URL + "#api"
SYNC_JOB = "jde-sync"


@router.post("/settings/jde/api")
async def save_api(request: Request) -> JSONResponse:
    """Save the AIS connection; the password only when one is typed (it goes to the .env, never the database)."""
    store = get_store(request)
    form = await posted_form(request)

    def text(name: str) -> str:
        return str(form.get(name) or "").strip()

    def number(name: str, default: float) -> float:
        try:
            return float(text(name) or default)
        except ValueError:
            raise ValueError(f"The {name.replace('_', ' ')} must be a number.") from None

    try:
        timeout = number("timeout", jde_ais.DEFAULT_TIMEOUT)
        if not 1 <= timeout <= 300:
            raise ValueError("The timeout is a number of seconds from 1 to 300.")
        changed = jde_ais.save(
            store, base_url=text("base_url"), environment=text("environment").upper(), role=text("role"),
            username=text("username"), password=str(form.get("password") or ""),
            forget_password=bool(form.get("forget_password")), device_name=text("device_name"),
            auth_mode=text("auth_mode") or jde_ais.TOKEN, ca_file=text("ca_file"), timeout=timeout,
            step1=bool(form.get("step1")), step2=bool(form.get("step2")), sync_hour=int(number("sync_hour", 2)),
            company=text("company"), po_order_type=text("po_order_type").upper(),
            voucher_days=int(number("voucher_days", 730)), shared_network=bool(form.get("shared_network")),
            actor=reviewer(),
        )  # fmt: skip
    except ValueError as exc:
        return refuse(str(exc))
    jde_sync.forget_cache()
    _, note = jde_ais.status(jde_ais.load(store))
    return done(request, API_URL, f"API connection saved ({', '.join(changed)}). {note}" if changed
                else "Nothing changed.")  # fmt: skip


@router.post("/settings/jde/api/test")
def test_api(request: Request) -> JSONResponse:
    """Test connection: AIS's version, a sign-in and a logout. Only once a step is on: off, nothing is sent."""
    store = get_store(request)
    if not jde_ais.load(store).on:
        return refuse(str(jde_ais.AisOff()) + " Switch step 1 or step 2 on (and save) to test the connection.")
    found = jde_ais.connection_test(store, actor=reviewer())
    if found.get("ok"):
        return done(request, API_URL, found["summary"])
    return refuse(found.get("error") or "The connection test failed.")


@router.post("/settings/jde/api/sync")
def sync_api(request: Request) -> JSONResponse:
    """Sync now: suppliers, GL accounts, business units, open POs and vouchers from JD Edwards (step 1), in the
    background; the page reloads with the report."""
    store = get_store(request)
    if not jde_ais.load(store).step1:
        return refuse("Step 1 (reading from JD Edwards) is off: switch it on and save first.")
    actor = reviewer()

    def work(job: Any) -> dict[str, Any]:
        job.step(0, 1, "Reading from JD Edwards…")
        report = jde_sync.sync(store, actor=actor)
        tone = "ok" if report["ok"] else "warn"
        return {
            "messages": [
                {"text": "Synced from JD Edwards." if report["ok"] else "The sync did not finish.", "tone": tone},
                *({"text": line, "tone": "info"} for line in jde_sync.report_lines(report)),
            ]
        }

    if not jobs(request).get(SYNC_JOB).start(work, total=1, note="Syncing from JD Edwards"):
        return refuse("A sync is running already.", status=409)
    return JSONResponse({"ok": True, "job": SYNC_JOB, "status": f"/jobs/{SYNC_JOB}"})
