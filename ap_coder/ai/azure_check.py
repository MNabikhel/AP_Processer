"""Azure AI Document Intelligence as an optional reader, off unless an Admin turns it on (D-245).

AP Coder reads every invoice on this computer and needs nothing from the internet. Turned on (Settings → Reading →
Azure Document Intelligence) with the company's own resource, a background thread sends invoices to Azure Document
Intelligence (``prebuilt-invoice``), reads them again with Azure's answer among the readers (capture's merge rules:
local first, a disagreement to a person, agreeing surer, a value only Azure read at most likely) and compares the
fields:

* **low-confidence**: every invoice AP Coder isn't sure of (a header field read but not *verified*, a field an
  invoice needs to go touchless not found, or a check that failed);
* **spot-check**: a share of the others, every field verified (5% by default), picked by the file's content
  (``spot_pick``: the same invoice is always picked or not, like the touchless audit sample), so readers that are
  right 99.9% of the time are still compared with an independent reader once in a while;
* or **every invoice** (the mode ``ALL``), to compare the two readers;
* at most the first and last pages of an invoice (5 by default) and never more pages a day than the daily cap (200 by
  default), so the cost is bounded; the rest wait for tomorrow.

The check never holds up *Import invoices*: invoices are put in line (``azure_checks``) and checked in the
background, after OvisOCR2 has read them when it is running. Azure's answer is kept (``cache_path``) and read with
the invoice whenever it is read again (``kept_reading``). Azure agreeing is recorded ("Azure agrees on 11 of 11
fields"). A field Azure read differently is marked *Check*, the invoice gets a review issue and can't be approved
without a person; an invoice picked for a spot-check isn't approved without a person until Azure has agreed. Azure
not reached, the invoice stays as AP Coder's own readers read it and the queue pauses (``paused``). Azure's reading is
scored like the other readers' when AP approves (reader "di" on Learning → Readers), so its accuracy is measured.

Network: only the endpoint configured is contacted, over HTTPS, with its key, by AP Coder's own plain-HTTPS client
(``di_http``, API version 2024-11-30, D-245): every address, Azure's result address included, is checked against that
one host before a socket opens; no redirect is followed and no proxy is used. ``AP_ALLOW_INTERNET`` is neither needed
nor changed. Turned off, or without an endpoint or key, nothing is queued or sent, and Settings says why. The key is
kept in the .env (``AZURE_DOCUMENT_INTELLIGENCE_KEY``, like the rest of the Azure settings), never shown back in full,
never logged.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import logging
import os
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.config import Settings
from .di_http import API_VERSION, KEY, NOT_FOUND, REFUSED, TIMEOUT, UNREACHABLE, DiClient, DiError, endpoint_host
from .extraction import TEXT_EXTENSIONS

log = logging.getLogger(__name__)

LOW, SPOT, EVERY = "low-confidence", "spot-check", "every-invoice"
REASONS = {LOW: "Low confidence", SPOT: "Spot-check", EVERY: "Every invoice"}
# Which invoices go to Azure (Settings): those AP Coder is unsure of (and the spot-check share), or every one, to
# compare the two readers (D-245).
UNSURE, ALL = "unsure", "every"
MODES = {UNSURE: "Only those AP Coder is unsure of", ALL: "Every invoice, to compare the two readers"}
ISSUE_CODE = "AZURE_DI_DISAGREES"
NOT_COMPARED_CODE = "AZURE_DI_NOT_COMPARED"  # Azure read none of the fields AP Coder read: nothing was checked
CODES = (ISSUE_CODE, NOT_COMPARED_CODE)
READER = "di"  # Azure's name among the readers (capture's sources, the Learning page's Readers tab)
NAME = "Azure Document Intelligence"

# Settings: on/off, model, spot-check share and daily cap in the database (Settings → Reading); endpoint and key in
# the .env with the other Azure settings.
ENABLED_KEY, MODEL_KEY, RATE_KEY, CAP_KEY = (
    "azure_check_enabled", "azure_check_model", "azure_check_spot_rate", "azure_check_daily_pages",
)  # fmt: skip
MODE_KEY, PAGES_KEY = "azure_check_mode", "azure_check_invoice_pages"
TEST_KEY = "azure_check_test"  # the last "Test connection", as JSON
ENDPOINT_ENV, KEY_ENV = "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", "AZURE_DOCUMENT_INTELLIGENCE_KEY"
DEFAULT_MODEL, DEFAULT_RATE, DEFAULT_CAP, DEFAULT_PAGES = "prebuilt-invoice", 0.05, 200, 5
MAX_CAP, MAX_PAGES = 100_000, 2000
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{1,63}$")

# The background thread: how long it waits before looking again.
IDLE_SECONDS, OFF_SECONDS, CAP_SECONDS = 30.0, 120.0, 900.0
SAMPLE_INVOICE = Path(__file__).resolve().parent.parent.parent / "samples" / "northwind_ON_HST_NW-2026-0912.pdf"
WAITING_FOR_AZURE = f"picked for the {NAME} spot-check: approved once Azure agrees"


# --- Settings ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AzureCheckSettings:
    enabled: bool = False
    endpoint: str = ""
    api_key: str = field(default="", repr=False)  # never in a log line or an error
    model: str = DEFAULT_MODEL
    spot_rate: float = DEFAULT_RATE  # 0..1
    daily_pages: int = DEFAULT_CAP
    mode: str = UNSURE  # which invoices: UNSURE or ALL (``MODES``)
    invoice_pages: int = DEFAULT_PAGES  # at most this many pages of an invoice are sent (its first and its last)

    @property
    def host(self) -> str:
        return endpoint_host(self.endpoint)

    @property
    def key_hint(self) -> str:
        """What the page shows of the key: never all of it."""
        if not self.api_key:
            return ""
        return f"saved, ending …{self.api_key[-4:]}" if len(self.api_key) >= 16 else "saved"


# The endpoint: one of Azure Document Intelligence's hosts (``di_http.AZURE_HOSTS``, ``endpoint_host``): the check
# contacts nothing else, so invoices can't be sent to any https:// address typed in (B-978).
NOT_AZURE = ("The endpoint must be an Azure Document Intelligence https:// address, e.g. "
             "https://<name>.cognitiveservices.azure.com/ (Keys and Endpoint in the Azure portal).")  # fmt: skip


def _env_value(name: str) -> str:
    from ..core.config import _env

    return _env(name) or ""


def load_settings(store: Any, settings: Settings | None = None) -> AzureCheckSettings:
    """The Azure check's settings: the switch, model, spot-check share and cap from the database; the endpoint and key
    from the environment (the .env), else ``settings``."""
    di = settings.document_intelligence if settings is not None else None

    def number(key: str, default: float) -> float:
        try:
            return float(store.get_setting(key, "") or default)
        except (TypeError, ValueError):
            return default

    rate = min(1.0, max(0.0, number(RATE_KEY, DEFAULT_RATE)))
    cap = int(min(MAX_CAP, max(0, number(CAP_KEY, DEFAULT_CAP))))
    model = (store.get_setting(MODEL_KEY, "") or DEFAULT_MODEL).strip()
    mode = store.get_setting(MODE_KEY, "") or UNSURE
    return AzureCheckSettings(
        enabled=store.get_setting(ENABLED_KEY, "off") == "on",
        endpoint=_env_value(ENDPOINT_ENV) or (di.endpoint if di else "") or "",
        api_key=_env_value(KEY_ENV) or (di.api_key if di else "") or "",
        model=model if _MODEL_ID.match(model) else DEFAULT_MODEL,
        spot_rate=rate,
        daily_pages=cap,
        mode=mode if mode in MODES else UNSURE,
        invoice_pages=int(min(MAX_PAGES, max(1, number(PAGES_KEY, DEFAULT_PAGES)))),
    )


def env_path() -> Path:
    """The .env being used, or where a new one goes (the data folder)."""
    from ..core import paths

    return paths.env_target()


def save_settings(store: Any, *, enabled: bool, endpoint: str, api_key: str = "", forget_key: bool = False,
                  model: str = DEFAULT_MODEL, spot_percent: float = DEFAULT_RATE * 100,
                  daily_pages: int = DEFAULT_CAP, actor: str | None = None,
                  settings: Settings | None = None, mode: str = UNSURE,
                  invoice_pages: int = DEFAULT_PAGES) -> list[str]:  # fmt: skip
    """Save the Azure check's settings; the names of those that changed (never their values). An empty ``api_key``
    keeps the one saved (``forget_key`` removes it). Raises ValueError, in plain words, for a value that can't be
    used: an endpoint that isn't https://, a model name that isn't one, a share outside 0-100%, a negative cap."""
    if mode not in MODES:
        raise ValueError("Choose which invoices go to Azure: those AP Coder is unsure of, or every invoice.")
    if not 1 <= int(invoice_pages) <= MAX_PAGES:
        raise ValueError(f"The pages sent of an invoice are a number from 1 to {MAX_PAGES:,}.")
    from ..core.envfile import clean_url, one_line, write_env

    endpoint = clean_url(endpoint or "")
    if endpoint and not endpoint_host(endpoint):
        raise ValueError(NOT_AZURE)
    model = (model or DEFAULT_MODEL).strip()
    if not _MODEL_ID.match(model):
        raise ValueError("The model must be a Document Intelligence model id, e.g. prebuilt-invoice.")
    if not 0 <= float(spot_percent) <= 100:
        raise ValueError("The spot-check share is a percentage from 0 to 100.")
    if not 0 <= int(daily_pages) <= MAX_CAP:
        raise ValueError(f"The daily cap is a number of pages from 0 to {MAX_CAP:,}.")
    api_key = (api_key or "").strip()
    if any(c.isspace() for c in api_key):
        raise ValueError("The key has spaces in it: paste it again from the Azure portal (Keys and Endpoint).")

    before = load_settings(store, settings)
    changed: list[str] = []
    stored = {ENABLED_KEY: "on" if enabled else "off", MODEL_KEY: model,
              RATE_KEY: f"{float(spot_percent) / 100:g}", CAP_KEY: str(int(daily_pages)), MODE_KEY: mode,
              PAGES_KEY: str(int(invoice_pages))}  # fmt: skip
    names = {ENABLED_KEY: "enabled", MODEL_KEY: "model", RATE_KEY: "spot-check share", CAP_KEY: "daily cap",
             MODE_KEY: "which invoices", PAGES_KEY: "pages of an invoice"}  # fmt: skip
    for key, value in stored.items():
        if store.get_setting(key, "") != value:
            store.set_setting(key, value)
            changed.append(names[key])
    env: dict[str, str] = {}
    if endpoint != before.endpoint:
        env[ENDPOINT_ENV] = endpoint
    if forget_key and before.api_key:
        env[KEY_ENV] = ""
    elif api_key and api_key != before.api_key:
        env[KEY_ENV] = api_key
    env = {k: one_line(v) for k, v in env.items()}
    if env:
        write_env(env_path(), env)
        os.environ.update(env)  # the dashboard reads settings from the environment
        changed += ["endpoint" if k == ENDPOINT_ENV else "key" for k in env]
    if changed:
        _reached(store)  # a new key, endpoint or setting: Azure is asked again at once
        store.log_event("settings_changed", actor=actor, detail={"azure_check": changed})
    return changed


OFF, NO_ENDPOINT, NO_KEY, READY = "off", "no endpoint", "no key", "ready"


def status(cfg: AzureCheckSettings) -> tuple[str, str]:
    """(state, plain-English why): "ready", or why nothing is checked or tested. Off, nothing is sent at all, "Test
    connection" included: this computer contacts Azure only once AP has switched the check on."""
    if not cfg.enabled:
        return OFF, "Off — AP Coder works fully offline without it. Switch it on (and save) to test the connection."
    if not cfg.endpoint:
        return NO_ENDPOINT, "No endpoint yet: add the Document Intelligence endpoint from the Azure portal."
    if not cfg.host:
        return NO_ENDPOINT, "The endpoint isn't an Azure Document Intelligence https:// address: nothing is sent."
    if not cfg.api_key:
        return NO_KEY, "No key yet: add the Document Intelligence key from the Azure portal (Keys and Endpoint)."
    return READY, f"On: only {cfg.host} is contacted."


def usable(cfg: AzureCheckSettings) -> bool:
    return status(cfg)[0] == READY


# --- Which invoices -----------------------------------------------------------------------------------------------


def _capture(capture: Any) -> Any:
    from ..capture.types import CaptureResult

    if capture is None or isinstance(capture, CaptureResult):
        return capture
    return CaptureResult.from_dict(capture)


def low_confidence(capture: Any) -> bool:
    """AP Coder isn't sure of this invoice: a header field read but not verified, a field it needs to go touchless
    not found, or a check that failed."""
    from ..capture.supplier import REQUIRED_FIELDS
    from ..capture.types import MISSING, VERIFIED

    capture = _capture(capture)
    if capture is None or not capture.fields:
        return True
    if any(not c.get("ok", True) for c in capture.checks):
        return True
    for name in REQUIRED_FIELDS:
        fr = capture.fields.get(name)
        if fr is None or fr.status == MISSING or fr.value in (None, ""):
            return True
    return any(fr.status != VERIFIED for fr in capture.fields.values() if fr.value not in (None, ""))


def spot_pick(path: str | Path, rate: float) -> bool:
    """Whether this invoice is picked for a spot-check: decided by the file's content, so the same invoice is always
    picked or not (like ``capture.workflow.audit_pick``, salted so that it is not the same invoices as the audit)."""
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    try:
        data = Path(path).read_bytes()
    except OSError:
        data = str(path).encode()
    digest = hashlib.sha256(b"azure-check:" + data).hexdigest()
    return int(digest[:15], 16) / float(16**15) < rate


def decide(store: Any, cfg: AzureCheckSettings, capture: Any, path: str | Path) -> str:
    """Why this invoice goes to the Azure check ("low-confidence", "spot-check", "every-invoice"), or "" (it doesn't).
    In "every invoice" mode one AP Coder is sure of goes too, held like a spot-check. A spot-check is not picked
    once today's pages reach the cap (it would hold the invoice back from touchless processing until tomorrow); a
    low-confidence invoice waits for its turn."""
    if not usable(cfg) or Path(path).suffix.lower() in TEXT_EXTENSIONS:
        return ""
    if low_confidence(capture):
        return LOW
    if cfg.mode != ALL and not spot_pick(path, cfg.spot_rate):
        return ""
    pages = sent_pages(getattr(_capture(capture), "page_count", 0) or 1, cfg)
    try:
        used = store.azure_pages_on()
    except Exception:  # noqa: BLE001 - not counted: no spot-check
        return ""
    return "" if used + pages > cfg.daily_pages else EVERY if cfg.mode == ALL else SPOT


def sent_pages(count: int, cfg: AzureCheckSettings) -> int:
    """How many of an invoice's ``count`` pages go to Azure (``pages_to_send``)."""
    return max(1, min(count, cfg.invoice_pages))


def pages_to_send(count: int, most: int) -> str:
    """Which pages Azure reads, as its ``pages`` parameter ("" for all): an invoice longer than ``most`` pages is
    read at its first pages and its last (where the totals are), as AP Coder's own readers read a long file."""
    if most <= 0 or count <= most:
        return ""
    if most == 1:
        return "1"
    return f"1-{most - 1},{count}" if most > 2 else f"1,{count}"


# --- Comparing --------------------------------------------------------------------------------------------------


def azure_values(raw: dict[str, Any] | None) -> dict[str, Any]:
    """{capture field: value} of what Azure read (``prebuilt-invoice``'s fields, as capture's DI reader reads them)."""
    from ..capture import _di_readings

    if not raw:
        return {}
    return {name: readings[0].value for name, readings in _di_readings(raw).items() if readings}


def _same(name: str, ours: Any, theirs: Any) -> bool:
    from ..capture.normalize import same_value
    from ..core.memory import vendor_key

    if same_value(name, ours, theirs):
        return True
    if name == "vendor_name":  # "Northwind Office Supply" and "NORTHWIND OFFICE SUPPLY INC." are the same vendor
        a, b = vendor_key(str(ours or "")), vendor_key(str(theirs or ""))
        return bool(a) and a == b
    return False


def compare(capture: Any, values: dict[str, Any]) -> list[dict[str, Any]]:
    """Azure's value against AP Coder's, for each field both read: [{field, label, ours, azure, agree}]. The value
    shown agrees when it is Azure's, or when a reader behind it read Azure's (the vendor master's name replacing
    the printed one, say)."""
    from ..capture.confidence import LABELS

    capture = _capture(capture)
    rows: list[dict[str, Any]] = []
    if capture is None:
        return rows
    for name, theirs in values.items():
        fr = capture.fields.get(name)
        if fr is None or fr.value in (None, "") or theirs in (None, ""):
            continue
        sources = fr.sources or {}
        raws = [raw for reader, raw in sources.items() if not reader.startswith("other:") and reader != READER]
        agree = _same(name, fr.value, theirs) or any(_same(name, raw, theirs) for raw in raws)
        rows.append({"field": name, "label": LABELS.get(name, name.replace("_", " ")), "ours": fr.value,
                     "azure": theirs, "agree": bool(agree)})  # fmt: skip
    return rows


def summary(rows: list[dict[str, Any]]) -> str:
    """In a line: "Azure agrees on 11 of 11 fields" / "Azure read 2 of 11 fields differently: Total, Invoice number"."""
    from ..core.ui import plural

    if not rows:
        return "Azure read none of the fields AP Coder read"
    differ = [r["label"] for r in rows if not r["agree"]]
    if not differ:
        return f"Azure agrees on {len(rows)} of {plural(len(rows), 'field')}"
    return f"Azure read {len(differ)} of {plural(len(rows), 'field')} differently: {', '.join(differ)}"


def _shown(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:,.2f}"
    return str(value)


def apply(capture: Any, rows: list[dict[str, Any]]) -> list[str]:
    """Fold a comparison into a capture (changed in place): Azure's reading becomes one of each field's sources
    (scored as reader "di" when AP approves), and a field Azure read differently is marked Check with the reason
    "Azure Document Intelligence read X". Returns the fields read differently."""
    from ..capture.types import CHECK

    capture = _capture(capture)
    differ: list[str] = []
    if capture is None:
        return differ
    for row in rows:
        fr = capture.fields.get(row["field"])
        if fr is None:
            continue
        if row["agree"]:
            fr.sources[READER] = row["azure"]
            continue
        differ.append(row["field"])
        fr.status = CHECK
        fr.confidence = min(fr.confidence, 0.5)
        # Read with Azure among the readers (D-245): its reading is a rival already, and the reason said.
        if any(k.startswith("other:") and READER in str(v).split(", ") for k, v in fr.sources.items()):
            continue
        key = f"other:{row['azure']}"
        readers = [r.strip() for r in str(fr.sources.get(key) or "").split(",") if r.strip()]
        fr.sources[key] = ", ".join(sorted({*readers, READER}))
        reason = f"{NAME} read {_shown(row['azure'])}"
        if reason not in fr.reasons:
            fr.reasons.append(reason)
    return differ


def issue(rows: list[dict[str, Any]]) -> Any:
    """The review issue for fields Azure read differently (a ``validation.Issue``), or None when it agrees. Azure
    reading none of AP Coder's fields is not agreeing: nothing was checked, and that is an issue too."""
    from ..coding.validation import WARNING, Issue

    if not rows:
        return Issue(WARNING, "AZURE_DI_NOT_COMPARED", f"{NAME} read none of the fields AP Coder read, so nothing was "
                     "checked (is its model prebuilt-invoice?): check the invoice against the page.")  # fmt: skip
    differ = [r for r in rows if not r["agree"]]
    if not differ:
        return None
    what = "; ".join(f"{r['label']}: Azure {_shown(r['azure'])}, AP Coder {_shown(r['ours'])}" for r in differ)
    return Issue(WARNING, ISSUE_CODE, f"{NAME} read this differently ({what}): check it on the page.")


def reader_rows(rows: list[dict[str, Any]], final: dict[str, Any], layout_source: str = "") -> list[dict[str, Any]]:
    """Azure's reading against what AP approved (reader "di"), for ``Store.record_reader_outcomes``."""
    from ..capture.supplier import header_values

    approved = header_values(final or {})
    out = []
    for row in rows:
        value = approved.get(row["field"])
        if value in (None, ""):
            continue
        out.append({"reader": READER, "field": row["field"], "read_value": row["azure"], "final_value": value,
                    "correct": _same(row["field"], value, row["azure"]), "layout_source": layout_source})  # fmt: skip
    return out


# --- At processing time -----------------------------------------------------------------------------------------


@dataclass
class Plan:
    """What the Azure check means for an invoice being processed: ``reason`` it is put in line ("" when not), and
    ``hold``: it isn't approved without a person until Azure has checked it."""

    reason: str = ""
    hold: bool = False
    differ: list[str] = dataclasses.field(default_factory=list)


def prepare(store: Any, settings: Settings | None, invoice_id: int | None, capture: Any, report: Any,
            path: str | Path) -> Plan:  # fmt: skip
    """Before the touchless decision on an invoice being processed (``pipeline.InvoicePipeline.process``). An invoice
    Azure has already checked (read again by the page reader) gets that check folded in again: its fields Azure read
    differently are Check, with the review issue. Otherwise, with the check on: why it goes in line, and whether it
    waits for it. Never raises: the Azure check is an extra."""
    if store is None or capture is None:
        return Plan()
    try:
        check = store.azure_check(invoice_id) if invoice_id else None
        if check and check.get("status") == "done" and check.get("result"):
            values = (check["result"] or {}).get("azure") or {}
            rows = compare(capture, values)
            differ = apply(capture, rows)
            found = issue(rows)
            if found is not None and report is not None:
                report.issues = [i for i in report.issues if i.code not in CODES] + [found]
                report.requires_review = True
            return Plan(differ=differ)
        cfg = load_settings(store, settings)
        reason = decide(store, cfg, capture, path)
        if not reason:
            return Plan()
        waiting = check is None or check.get("status") in ("waiting", "checking", "skipped")
        return Plan(reason=reason, hold=waiting)
    except Exception as exc:  # noqa: BLE001 - never lose an invoice over the Azure check
        log.warning("Azure check not planned (%s)", type(exc).__name__)
        return Plan()


def hold(autonomy: dict[str, Any], plan: Plan) -> dict[str, Any]:
    """The touchless decision, held back while Azure hasn't checked an invoice picked for it (``verifier.held_for``:
    whether or not the AI reviewer is already holding it)."""
    from .verifier import held_for

    return held_for(autonomy, plan, "azure_hold", WAITING_FOR_AZURE)


def queue(store: Any, invoice_id: int | None, plan: Plan) -> bool:
    """Put the invoice in line for the Azure check when ``plan`` says so. Never raises."""
    if store is None or not invoice_id or not plan.reason:
        return False
    try:
        return bool(store.queue_azure_check(invoice_id, plan.reason))
    except Exception as exc:  # noqa: BLE001 - the queue is an extra: processing must not fail over it
        log.warning("invoice %s: not queued for the Azure check (%s)", invoice_id, type(exc).__name__)
        return False


# --- Reading with Azure -----------------------------------------------------------------------------------------


class AzureUnavailable(RuntimeError):
    """The Azure check can't run (off, no endpoint or key) or a request was refused. ``kind``: ``di_http``'s, when
    Azure was asked."""

    def __init__(self, message: str, kind: str = "") -> None:
        super().__init__(message)
        self.kind = kind


# --- When Azure can't be reached: the queue pauses, AP Coder carries on with its own readers -----------------------

DOWN_KEY = "azure_check_down"  # the outage, as JSON: since, failures, until, error, permanent
FAILURES_TO_PAUSE, PAUSE_MINUTES = 3, 15
NOT_REACHED = f"not read by {NAME}: not reached; read by AP Coder's own readers"
OUTAGE_KINDS = (UNREACHABLE, TIMEOUT, KEY, NOT_FOUND, REFUSED)


def outage(store: Any) -> dict[str, Any] | None:
    """The outage under way ({since, failures, until, error, permanent}), or None while Azure answers."""
    saved = store.get_json(DOWN_KEY)
    return saved if isinstance(saved, dict) and saved.get("since") else None


def _not_reached(store: Any, error: str, permanent: bool) -> None:
    """One more failure in a row: after ``FAILURES_TO_PAUSE`` the queue pauses ``PAUSE_MINUTES``; a refused key (or
    address) pauses it until the settings change."""
    now = dt.datetime.now()
    down = outage(store) or {"since": now.isoformat(timespec="seconds"), "failures": 0}
    down["failures"] = int(down.get("failures") or 0) + 1
    down.update(error=error, permanent=permanent, last=now.isoformat(timespec="seconds"))
    if permanent or down["failures"] >= FAILURES_TO_PAUSE:
        down["until"] = (now + dt.timedelta(minutes=PAUSE_MINUTES)).isoformat(timespec="seconds")
    store.set_setting(DOWN_KEY, json.dumps(down))


def _reached(store: Any) -> None:
    if store.get_setting(DOWN_KEY, ""):
        store.set_setting(DOWN_KEY, "")


def paused(store: Any, now: dt.datetime | None = None) -> str:
    """Why the queue waits (nothing is sent), in plain words; "" when it doesn't."""
    down = outage(store)
    if down is None:
        return ""
    since = str(down.get("since") or "")[11:16]
    if down.get("permanent"):
        return f"{NAME} refused AP Coder's request since {since} ({down.get('error')}): the key or the endpoint " \
               "needs changing in Settings → Reading."  # fmt: skip
    until = str(down.get("until") or "")
    if until and (now or dt.datetime.now()).isoformat(timespec="seconds") < until:
        return f"{NAME}: not reached since {since}; tried again at {until[11:16]}."
    return ""


def outage_line(store: Any) -> str:
    """The outage in a line for the Reading status and the card ("" while Azure answers)."""
    down = outage(store)
    if down is None:
        return ""
    since = str(down.get("since") or "")[11:16]
    if down.get("permanent"):
        return f"{NAME}: the key or the endpoint was refused at {since}: invoices are read on this computer."
    return f"{NAME}: not reached since {since} — invoices are read on this computer."


def make_client(cfg: AzureCheckSettings) -> DiClient:
    """The Document Intelligence client (``di_http.DiClient``) for the endpoint configured, its key, nothing else."""
    state, why = status(cfg)
    if state != READY:
        raise AzureUnavailable(why)
    return DiClient(cfg.endpoint, cfg.api_key)


def read(cfg: AzureCheckSettings, path: str | Path, client: Any = None, pages: str = "") -> dict[str, Any]:
    """Azure's reading of the file (its ``analyzeResult``), ``pages`` of it ("1-4,9"; all when empty)."""
    client = client if client is not None else make_client(cfg)
    try:
        return client.analyze(Path(path).read_bytes(), pages=pages) or {}
    except DiError as exc:
        raise AzureUnavailable(str(exc), exc.kind) from None


def scrub(text: str, cfg: AzureCheckSettings) -> str:
    """``text`` with the key taken out, should an error ever quote it."""
    text = str(text or "")
    if cfg.api_key and len(cfg.api_key) >= 4:
        text = text.replace(cfg.api_key, "••••")
    return text[:500]


# --- Azure's reading kept, and read with the invoice --------------------------------------------------------------


def cache_path(digest: str) -> Path:
    """Where Azure's reading of a file is kept (the data folder's ``.cache/di``, by the file's SHA-256, the model and
    the API version): an invoice read again uses it, and Azure is never asked twice for one file. Like every cache,
    never in a backup."""
    from ..core.paths import private_dir
    from .di_http import API_VERSION, MODEL

    return private_dir() / ".cache" / "di" / f"{digest}.{MODEL}.{API_VERSION}.json"


def keep_reading(digest: str, raw: dict[str, Any]) -> None:
    """Keep Azure's reading of the file ``digest``. Never raises: without it the invoice is read without Azure."""
    path = cache_path(digest)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f"{path.name}.{os.getpid()}-{threading.get_ident()}.tmp")
        temporary.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        log.warning("Azure's reading not kept (%s)", type(exc).__name__)


def kept_reading(store: Any, path: str | Path, digest: str | None = None) -> dict[str, Any] | None:
    """Azure's reading kept for this file, while the check is switched on (off, nothing of it is used: AP Coder reads
    as it does without it). None when there is none."""
    if store is None:
        return None
    try:
        if store.get_setting(ENABLED_KEY, "off") != "on":
            return None
        from ..core.paths import file_sha256

        digest = digest or file_sha256(path)
        raw = json.loads(cache_path(digest).read_text(encoding="utf-8")) if digest else None
    except (OSError, ValueError):
        return None
    except Exception as exc:  # noqa: BLE001 - an extra reader: never stops the invoice
        log.warning("Azure's kept reading not used (%s)", type(exc).__name__)
        return None
    return raw if isinstance(raw, dict) else None


# --- The queue, in the background -------------------------------------------------------------------------------


@dataclass
class CheckOutcome:
    invoice_id: int
    status: str  # done | failed | skipped | postponed
    message: str = ""
    differ: list[str] = dataclasses.field(default_factory=list)
    approved: bool = False
    read_again: str = ""  # how the invoice was read again with Azure's reading: "now", "page reader", "" (not)


def _page_reader_running(settings: Settings | None, store: Any) -> bool:
    """OvisOCR2 is running and will read the invoices waiting for it: they are checked once it has."""
    if settings is None:
        return False
    try:
        from .page_worker import ready

        return bool(ready(settings, store)[0])
    except Exception:  # noqa: BLE001 - not known: check now
        return False


def check_one(settings: Settings | None, store: Any, *, client: Any = None) -> CheckOutcome | None:
    """Check the next invoice in line. None when the check is off or not usable, or nothing waits."""
    if store.restoring():  # AP is restoring a backup: nothing is taken from a database being replaced
        return None
    cfg = load_settings(store, settings)
    if not usable(cfg) or paused(store):
        return None
    row = store.next_azure_check(after_page_reader=_page_reader_running(settings, store))
    if not row:
        return None
    iid = int(row["invoice_id"])
    try:
        return _check(cfg, store, row, client, settings)
    except AzureUnavailable as exc:
        if exc.kind not in OUTAGE_KINDS:
            message = scrub(f"{type(exc).__name__}: {exc}", cfg)
            log.warning("Azure check: invoice %s failed (%s)", iid, message)
            store.finish_azure_check(iid, "failed", error=message)
            return CheckOutcome(iid, "failed", message)
        # Azure not reached, or refusing: the invoice stays as AP Coder's own readers read it, back in line.
        _not_reached(store, scrub(str(exc), cfg), permanent=exc.kind in (KEY, NOT_FOUND, REFUSED))
        log.warning("Azure check: %s not reached (%s)", cfg.host, exc.kind)
        store.finish_azure_check(iid, "waiting", error=NOT_REACHED)
        return CheckOutcome(iid, "postponed", NOT_REACHED)
    except Exception as exc:  # noqa: BLE001 - one invoice must never stop the queue
        message = scrub(f"{type(exc).__name__}: {exc}", cfg)
        log.warning("Azure check: invoice %s failed (%s)", iid, message)
        store.finish_azure_check(iid, "failed", error=message)
        return CheckOutcome(iid, "failed", message)


def _check(cfg: AzureCheckSettings, store: Any, row: dict[str, Any], client: Any,
           settings: Settings | None = None) -> CheckOutcome:  # fmt: skip
    iid, reason = int(row["invoice_id"]), str(row["reason"])
    inv = store.get_invoice(iid)
    identity = store.row_identity(iid)  # Azure's reading is folded into this invoice only (a restore may come between)
    path = Path((inv or {}).get("source_path") or "")
    if inv is None or not path.is_file():
        return _skip(store, iid, "the invoice or its file is gone")
    if path.suffix.lower() in TEXT_EXTENSIONS:
        return _skip(store, iid, "a text file: there is no page to send")
    capture = _capture(store.get_capture(iid))
    if capture is None:
        return _skip(store, iid, "AP Coder has no reading of it to compare with")
    if reason == LOW and not low_confidence(capture):  # every field verified since (OvisOCR2 read it)
        if cfg.mode != ALL and not spot_pick(path, cfg.spot_rate):
            return _skip(store, iid, "every field was verified once OvisOCR2 had read it; not picked for a spot-check")
        reason = EVERY if cfg.mode == ALL else SPOT
    count = capture.page_count or 1
    pages = sent_pages(count, cfg)
    if pages > cfg.daily_pages:
        return _skip(store, iid, f"{pages} pages: more than the daily cap of {cfg.daily_pages}")
    if store.azure_pages_on() + pages > cfg.daily_pages:
        message = f"today's cap of {cfg.daily_pages} pages is reached: checked tomorrow"
        store.finish_azure_check(iid, "waiting", error=message)
        return CheckOutcome(iid, "postponed", message)

    raw = read(cfg, path, client, pages=pages_to_send(count, cfg.invoice_pages))
    _reached(store)
    digest = inv.get("file_sha256") or ""
    if digest:
        keep_reading(digest, raw)  # read again later (the page reader, a person's Read again), Azure isn't asked
    if store.restoring() or store.row_identity(iid) != identity:
        # A backup was restored while Azure read it: nothing is written, the queue stays as the backup has it.
        return CheckOutcome(iid, "skipped", "not applied: a backup was restored while Azure read it")
    values = azure_values(raw)
    rows = compare(capture, values)
    billed = len(raw.get("pages") or []) or pages
    agreed = sum(1 for r in rows if r["agree"])
    now = dt.datetime.now().isoformat(timespec="seconds")
    result = {"model": cfg.model, "reason": reason, "azure": values, "rows": rows, "summary": summary(rows),
              "at": now}  # fmt: skip
    store.finish_azure_check(iid, "done", result=result, pages=billed, compared=len(rows), agreed=agreed,
                             reason=reason)  # fmt: skip
    outcome = CheckOutcome(iid, "done", result["summary"])
    outcome.differ, outcome.approved = _fold_in(store, inv, capture, rows, reason, now, expect=identity)
    if not outcome.approved and digest and rows:
        outcome.read_again = _read_again(settings, store, iid, identity)
    store.log_event("azure_checked", iid, actor="AP Coder", detail={
        "reason": reason, "compared": len(rows), "agreed": agreed, "differ": outcome.differ, "pages": billed,
    })  # fmt: skip
    return outcome


def _read_again(settings: Settings | None, store: Any, iid: int, identity: Any) -> str:
    """The invoice read again with Azure's reading as one more reader (D-245), while it waits untouched in the review
    queue: where it agrees, the fields are surer; a field the readers on this computer missed is filled in; where it
    differs, a person checks. Read by the page reader already: it goes back in the page reader's line (its pages are
    kept, so nothing is read twice). Otherwise it is read again now. Not while another check holds it (the AI
    reviewer decides then, on the proposal it has). Never raises."""
    from ..core.store import REVIEW
    from .verifier import held

    inv = store.get_invoice(iid) or {}
    worked_on = inv.get("final_output") or inv.get("reviewer") or inv.get("edits")
    if inv.get("status") != REVIEW or worked_on or not settings or held((inv.get("meta") or {}).get("capture") or {}):
        return ""
    try:
        if (inv.get("meta") or {}).get("page_reader"):
            return "page reader" if store.queue_page_read(iid, "azure-check") else ""
        return "now" if _process_again(settings, store, iid, identity) else ""
    except Exception as exc:  # noqa: BLE001 - the comparison is folded in already: the reading again is an extra
        log.warning("invoice %s: not read again with Azure's reading (%s)", iid, type(exc).__name__)
        return ""


def _process_again(settings: Settings, store: Any, iid: int, identity: Any) -> bool:
    """Process the invoice again (its kept Azure reading among the readers) into its proposal, approved as touchless
    processing would have (as ``page_worker.chat_again`` does). Whether the proposal was replaced."""
    from ..capture.workflow import AUTONOMOUS_REVIEWER, has_draft
    from .page_worker import chat_trust, reference_data
    from .pipeline import InvoicePipeline
    from .pipeline import _meta as pipeline_meta

    inv = store.get_invoice(iid) or {}
    result = InvoicePipeline(settings, reference_data(store), store=store).process(
        Path(inv.get("source_path") or ""), save=False, invoice_id=iid)  # fmt: skip
    if result.error or result.output is None or result.report is None:
        return False
    if store.restoring() or store.row_identity(iid) != identity:
        return False
    capture = result.capture.to_dict() if result.capture is not None else None
    updated = store.replace_proposal(iid, result.output, result.report.to_dict(), capture, pipeline_meta(result),
                                     expect=identity)  # fmt: skip
    untrusted = chat_trust(store, result.header_model) if updated and result.autonomy.get("auto") else ""
    if updated and result.autonomy.get("auto") and not untrusted and not has_draft(store, iid):
        try:
            store.approve_invoice(iid, result.output, AUTONOMOUS_REVIEWER, login="ap-coder")
        except (KeyError, ValueError) as exc:  # approved or rejected meanwhile
            log.info("invoice %s: not approved after Azure's reading (%s)", iid, exc)
    return updated


def _skip(store: Any, iid: int, why: str) -> CheckOutcome:
    store.finish_azure_check(iid, "skipped", error=why)
    return CheckOutcome(iid, "skipped", why)


def _fold_in(store: Any, inv: dict[str, Any], capture: Any, rows: list[dict[str, Any]], reason: str,
             now: str, *, expect: tuple[Any, ...] | None = None) -> tuple[list[str], bool]:  # fmt: skip
    """Fold the check into the invoice: still open, its capture, review issue and notes (and, held only for this
    check and Azure agreeing on every field it compared, it is approved as touchless processing would have; Azure
    comparing no field keeps the hold, with a review issue); approved already, Azure's reading
    is scored against what AP approved. ``expect``: the invoice's ``Store.row_identity`` when the check started (a
    restore may have put another invoice under its number). Returns (fields read differently, approved now)."""
    from ..capture.workflow import AUTONOMOUS_REVIEWER, still_touchless
    from ..core.store import APPROVED, PARKED, PENDING, REVIEW
    from .page_worker import header_untrusted

    iid = int(inv["id"])
    differ = apply(capture, rows)
    found = issue(rows)
    meta = dict(inv.get("meta") or {})
    note = {"reason": reason, "summary": summary(rows), "differ": differ, "at": now}
    status_ = inv.get("status")
    approved = False
    if status_ in (REVIEW, PARKED):
        notes = dict(meta.get("capture") or {})
        held = bool(notes.get("azure_hold"))
        untouched = status_ == REVIEW and not (inv.get("final_output") or inv.get("reviewer") or inv.get("edits"))
        # The chat model that read its header took part: Azure agreeing approves nothing it isn't trusted for (D-180).
        untrusted = header_untrusted(store, meta) if held and rows and not differ else ""
        if held and rows:  # nothing compared is not nothing differing: then the hold stays
            notes = {k: v for k, v in notes.items() if k != "azure_hold"}
            notes["reason"] = (f"{NAME} read {len(differ)} field(s) differently" if differ else
                               untrusted and f"not approved without a person: {untrusted}")  # fmt: skip
        elif held:
            notes["reason"] = summary(rows)
        folded = store.update_after_check(iid, capture.to_dict(), dataclasses.asdict(found) if found else None,
                                          CODES, {"azure_check": note, "capture": notes}, expect=expect)  # fmt: skip
        # Held at the touchless decision, maybe days ago: the switch, the supplier's state and the limits are
        # checked again, as they stand now, and the invoice is read again: the AI reviewer may still hold it, or a
        # person may have approved it with corrections and reopened it while Azure read it.
        output = inv.get("ai_output")
        if (folded and held and rows and untouched and not differ and not untrusted and output
                and still_touchless(store, capture, output, invoice_id=iid)):  # fmt: skip
            try:
                store.approve_invoice(iid, output, AUTONOMOUS_REVIEWER, login="ap-coder")
                approved = True
            except (KeyError, ValueError) as exc:  # approved, rejected or changed meanwhile
                log.info("invoice %s: not approved after the Azure check (%s)", iid, exc)
    elif status_ in (APPROVED, PENDING):
        store.save_capture(iid, capture.to_dict())
        final = inv.get("final_output") or inv.get("ai_output") or {}
        store.record_reader_outcomes(iid, reader_rows(rows, final, capture.layout_source), reader=READER)
        if differ:
            store.log_event("azure_check_disagrees", iid, actor="AP Coder", detail={"fields": differ})
    return differ, approved


class AzureChecker(threading.Thread):
    """Checks invoices in line, one at a time, while the dashboard runs. Settings are read again each round, so
    turning the check on or off in Settings applies without a restart; off, it only looks again now and then."""

    def __init__(self, settings_factory: Callable[[], Settings], store_factory: Callable[[], Any]) -> None:
        super().__init__(name="ap-coder-azure-check", daemon=True)
        self._settings_factory = settings_factory
        self._store_factory = store_factory
        self._store: Any = None  # the store, built at the first round (a Store() runs the schema and its upgrade)
        self._halt = threading.Event()

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        while not self._halt.is_set():
            pause = IDLE_SECONDS
            try:
                store = self._store = self._store or self._store_factory()  # built once, then reused
                settings = self._settings_factory()
                if not usable(load_settings(store, settings)):
                    pause = OFF_SECONDS
                else:
                    outcome = check_one(settings, store)
                    if outcome is not None:
                        log.info("Azure check: invoice %s %s", outcome.invoice_id, outcome.status)
                        pause = CAP_SECONDS if outcome.status == "postponed" else 0.0
            except Exception:  # noqa: BLE001 - never let the thread die: the next round tries again
                log.warning("Azure check round failed", exc_info=False)
                pause = OFF_SECONDS
            self._halt.wait(pause)


_background: AzureChecker | None = None
_background_lock = threading.Lock()


def start_background(settings_factory: Callable[[], Settings], store_factory: Callable[[], Any]) -> AzureChecker:
    """The dashboard's Azure-check thread, started once per process (it does nothing while the check is off)."""
    global _background
    with _background_lock:
        if _background is None or not _background.is_alive():
            _background = AzureChecker(settings_factory, store_factory)
            _background.start()
        return _background


# --- Test connection and the Settings card ----------------------------------------------------------------------


def connection_test(store: Any, settings: Settings | None = None, *, client: Any = None,
                    sample: Path = SAMPLE_INVOICE) -> dict[str, Any]:  # fmt: skip
    """Read the made-up sample invoice that comes with AP Coder with Azure (one page; nothing of yours is sent) and
    compare its fields with AP Coder's own reading of it. The result is kept (``TEST_KEY``) and returned:
    {ok, when, summary, rows, pages, seconds, sample, host} or {ok: False, when, error}."""
    cfg = load_settings(store, settings)
    when = dt.datetime.now().isoformat(timespec="seconds")
    record: dict[str, Any]
    try:
        state, why = status(cfg)
        if state != READY:
            raise AzureUnavailable(why)
        t0 = time.perf_counter()
        client = client if client is not None else make_client(cfg)
        raw = read(cfg, sample, client)
        from ..capture import analyze

        ours = analyze(sample, today=dt.date.today())
        rows = compare(ours, azure_values(raw))
        _reached(store)
        record = {"ok": True, "when": when, "summary": summary(rows), "rows": rows, "host": cfg.host,
                  "pages": len(raw.get("pages") or []), "seconds": round(time.perf_counter() - t0, 1),
                  "sample": sample.name, **region_note(str(getattr(client, "region", "") or ""))}  # fmt: skip
    except Exception as exc:  # noqa: BLE001 - shown on the card, in plain words
        problem = str(exc) if isinstance(exc, AzureUnavailable) else f"{type(exc).__name__}: {exc}"
        record = {"ok": False, "when": when, "error": scrub(problem, cfg), "host": cfg.host}
    store.set_setting(TEST_KEY, json.dumps(record, default=str))
    return record


REGION = "Canada Central"


def region_note(region: str) -> dict[str, Any]:
    """What the test says of the resource's region: {region, region_ok, region_note}. Azure's answer names it only
    sometimes (``x-ms-region``): not named, IT confirms it in the Azure portal."""
    if not region:
        return {"region": "", "region_ok": None, "region_note": f"Azure's answer didn't name its region: confirm in "
                f"the Azure portal that the resource is in {REGION}."}  # fmt: skip
    ok = "".join(region.split()).lower() == "".join(REGION.split()).lower()
    note = f"Region: {region}." if ok else f"Region: {region}, not {REGION}: ask IT to confirm it is your company's."
    return {"region": region, "region_ok": ok, "region_note": note}


def last_test(store: Any) -> dict[str, Any] | None:
    saved = store.get_json(TEST_KEY)
    return saved if isinstance(saved, dict) else None


def overview(store: Any, settings: Settings | None = None, days: int = 30) -> dict[str, Any]:
    """Everything the Settings card shows: the settings (the key only as a hint), the state and why, the checks of
    the last ``days`` days (checked, agreed, the disagreements), the queue and today's pages, the last test."""
    cfg = load_settings(store, settings)
    state, note = status(cfg)
    since = (dt.datetime.now() - dt.timedelta(days=days)).isoformat(timespec="seconds")
    checks = store.azure_checks_since(since)
    agreed = sum(1 for c in checks if c["compared"] and c["agreed"] == c["compared"])
    disagreements = []
    for c in checks:
        rows = [r for r in ((c.get("result") or {}).get("rows") or []) if not r.get("agree")]
        if rows:
            disagreements.append({"invoice_id": c["invoice_id"], "vendor": c.get("vendor_name") or "",
                                  "number": c.get("invoice_number") or "", "status": c.get("invoice_status") or "",
                                  "reason": REASONS.get(c.get("reason") or "", c.get("reason") or ""),
                                  "when": c.get("compared_at") or "",
                                  "fields": [(r.get("label"), _shown(r.get("ours")), _shown(r.get("azure")))
                                             for r in rows]})  # fmt: skip
    counts = store.azure_check_counts()
    return {
        "enabled": cfg.enabled, "endpoint": cfg.endpoint, "host": cfg.host, "key_hint": cfg.key_hint,
        "model": DEFAULT_MODEL, "api_version": API_VERSION, "spot_percent": f"{cfg.spot_rate * 100:g}",
        "daily_pages": cfg.daily_pages, "mode": cfg.mode, "modes": MODES, "invoice_pages": cfg.invoice_pages,
        "state": state, "note": note, "ready": state == READY,
        "can_test": status(cfg)[0] == READY, "days": days, "checked": len(checks),
        "agreed": agreed, "agreed_rate": agreed / len(checks) if checks else None,
        "fields_compared": sum(int(c["compared"] or 0) for c in checks),
        "fields_agreed": sum(int(c["agreed"] or 0) for c in checks),
        "disagreements": disagreements[:50], "waiting": counts["waiting"] + counts["checking"],
        "failed": counts["failed"], "pages_today": store.azure_pages_on(), "test": last_test(store),
        "outage": outage_line(store), "paused": paused(store),
    }  # fmt: skip
