"""The Azure check: Azure AI Document Intelligence as an optional second opinion, off unless AP turns it on.

AP Coder reads every invoice on this computer and needs nothing from the internet. Turned on (Settings → Reading →
Azure check), a background thread sends some invoices to Azure Document Intelligence (``prebuilt-invoice``) and
compares the fields Azure reads with the values AP Coder's own readers settled on:

* **low-confidence**: every invoice AP Coder isn't sure of (a header field read but not *verified*, a field an
  invoice needs to go touchless not found, or a check that failed);
* **spot-check**: a share of the others, every field verified (5% by default), picked by the file's content
  (``spot_pick``: the same invoice is always picked or not, like the touchless audit sample), so readers that are
  right 99.9% of the time are still compared with an independent reader once in a while;
* never more pages a day than the daily cap (200 by default), so the cost is bounded; the rest wait for tomorrow.

The check never holds up *Process invoices*: invoices are put in line (``azure_checks``) and checked in the
background, after OvisOCR2 has read them when it is running. Azure agreeing is recorded ("Azure agrees on 11 of 11
fields"). A field Azure read differently is marked *Check* with the reason "Azure Document Intelligence read X", the
invoice gets a review issue and can't be approved without a person; an invoice picked for a spot-check isn't
approved without a person until Azure has agreed. Azure's reading is scored like the other readers' when AP approves
(reader "di" on Learning → Readers), so its own accuracy is measured over time.

Network: only the endpoint configured is contacted, over HTTPS, with its key. Every request the Azure client makes,
redirects and polling included, is checked against that one host (``only_host``) and refused otherwise.
``AP_ALLOW_INTERNET`` is neither needed nor changed. Turned off, without Azure's packages (the optional ``azure``
extra), or without an endpoint or key, nothing is queued or sent, and Settings says why. The key is kept in the .env
(``AZURE_DOCUMENT_INTELLIGENCE_KEY``, like the rest of the Azure settings), never shown back in full, never logged.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import importlib
import importlib.util
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
from urllib.parse import urlsplit

from .config import Settings
from .extraction import TEXT_EXTENSIONS

log = logging.getLogger(__name__)

LOW, SPOT = "low-confidence", "spot-check"
REASONS = {LOW: "Low confidence", SPOT: "Spot-check"}
ISSUE_CODE = "AZURE_DI_DISAGREES"
READER = "di"  # Azure's name among the readers (capture's sources, the Learning page's Readers tab)
NAME = "Azure Document Intelligence"

# Settings: on/off, model, spot-check share and daily cap in the database (Settings → Reading); endpoint and key in
# the .env with the other Azure settings.
ENABLED_KEY, MODEL_KEY, RATE_KEY, CAP_KEY = (
    "azure_check_enabled", "azure_check_model", "azure_check_spot_rate", "azure_check_daily_pages",
)  # fmt: skip
TEST_KEY = "azure_check_test"  # the last "Test connection", as JSON
ENDPOINT_ENV, KEY_ENV = "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", "AZURE_DOCUMENT_INTELLIGENCE_KEY"
DEFAULT_MODEL, DEFAULT_RATE, DEFAULT_CAP = "prebuilt-invoice", 0.05, 200
MAX_CAP = 100_000
PACKAGES = ("azure.ai.documentintelligence", "azure.core")
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._~-]{1,63}$")

# The background thread: how long it waits before looking again.
IDLE_SECONDS, OFF_SECONDS, CAP_SECONDS = 30.0, 120.0, 900.0
SAMPLE_INVOICE = Path(__file__).resolve().parent.parent / "samples" / "northwind_ON_HST_NW-2026-0912.pdf"
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

    @property
    def host(self) -> str:
        return endpoint_host(self.endpoint)

    @property
    def key_hint(self) -> str:
        """What the page shows of the key: never all of it."""
        if not self.api_key:
            return ""
        return f"saved, ending …{self.api_key[-4:]}" if len(self.api_key) >= 16 else "saved"


def endpoint_host(url: str) -> str:
    """The host of an HTTPS endpoint, lower case ("" for anything else: http, no host, user:password@)."""
    try:
        parts = urlsplit(str(url or "").strip())
    except ValueError:
        return ""
    if parts.scheme.lower() != "https" or not parts.hostname or "@" in parts.netloc:
        return ""
    return parts.hostname.lower()


def _env_value(name: str) -> str:
    from .config import _env

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
    return AzureCheckSettings(
        enabled=store.get_setting(ENABLED_KEY, "off") == "on",
        endpoint=_env_value(ENDPOINT_ENV) or (di.endpoint if di else "") or "",
        api_key=_env_value(KEY_ENV) or (di.api_key if di else "") or "",
        model=model if _MODEL_ID.match(model) else DEFAULT_MODEL,
        spot_rate=rate,
        daily_pages=cap,
    )


def env_path() -> Path:
    """The .env being used, or where a new one goes (the data folder)."""
    from . import paths

    return paths.env_file() or paths.private_dir() / ".env"


def save_settings(store: Any, *, enabled: bool, endpoint: str, api_key: str = "", forget_key: bool = False,
                  model: str = DEFAULT_MODEL, spot_percent: float = DEFAULT_RATE * 100,
                  daily_pages: int = DEFAULT_CAP, actor: str | None = None,
                  settings: Settings | None = None) -> list[str]:  # fmt: skip
    """Save the Azure check's settings; the names of those that changed (never their values). An empty ``api_key``
    keeps the one saved (``forget_key`` removes it). Raises ValueError, in plain words, for a value that can't be
    used: an endpoint that isn't https://, a model name that isn't one, a share outside 0-100%, a negative cap."""
    from .envfile import clean_url, write_env

    endpoint = clean_url(endpoint or "")
    if endpoint and not endpoint_host(endpoint):
        raise ValueError("The endpoint must be an https:// address, e.g. https://<name>.cognitiveservices.azure.com/")
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
              RATE_KEY: f"{float(spot_percent) / 100:g}", CAP_KEY: str(int(daily_pages))}  # fmt: skip
    names = {ENABLED_KEY: "enabled", MODEL_KEY: "model", RATE_KEY: "spot-check share", CAP_KEY: "daily cap"}
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
    if env:
        write_env(env_path(), env)
        os.environ.update(env)  # the dashboard reads settings from the environment
        changed += ["endpoint" if k == ENDPOINT_ENV else "key" for k in env]
    if changed:
        store.log_event("settings_changed", actor=actor, detail={"azure_check": changed})
    return changed


def packages_installed() -> bool:
    """Azure's Document Intelligence packages (the optional ``azure`` extra) are installed, found without importing
    them."""
    try:
        return all(importlib.util.find_spec(name) is not None for name in PACKAGES)
    except (ImportError, ValueError):
        return False


OFF, NO_PACKAGE, NO_ENDPOINT, NO_KEY, READY = "off", "no package", "no endpoint", "no key", "ready"


def status(cfg: AzureCheckSettings) -> tuple[str, str]:
    """(state, plain-English why): "ready", or why nothing is checked or tested. Off, nothing is sent at all, "Test
    connection" included: this computer contacts Azure only once AP has switched the check on."""
    if not cfg.enabled:
        return OFF, "Off — AP Coder works fully offline without it. Switch it on (and save) to test the connection."
    if not packages_installed():
        return NO_PACKAGE, (f"{NAME}'s packages aren't installed on this computer (the optional 'azure' extra): "
                            "nothing is sent. AP Coder works fully offline without them.")  # fmt: skip
    if not cfg.endpoint:
        return NO_ENDPOINT, "No endpoint yet: add the Document Intelligence endpoint from the Azure portal."
    if not cfg.host:
        return NO_ENDPOINT, "The endpoint isn't an https:// address: nothing is sent."
    if not cfg.api_key:
        return NO_KEY, "No key yet: add the Document Intelligence key from the Azure portal (Keys and Endpoint)."
    return READY, f"On: only {cfg.host} is contacted."


def usable(cfg: AzureCheckSettings) -> bool:
    return status(cfg)[0] == READY


# --- Which invoices -----------------------------------------------------------------------------------------------


def _capture(capture: Any) -> Any:
    from .capture.types import CaptureResult

    if capture is None or isinstance(capture, CaptureResult):
        return capture
    return CaptureResult.from_dict(capture)


def low_confidence(capture: Any) -> bool:
    """AP Coder isn't sure of this invoice: a header field read but not verified, a field it needs to go touchless
    not found, or a check that failed."""
    from .capture.supplier import REQUIRED_FIELDS
    from .capture.types import MISSING, VERIFIED

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
    """Why this invoice goes to the Azure check ("low-confidence", "spot-check"), or "" (it doesn't). A spot-check
    is not picked once today's pages reach the cap (it would hold the invoice back from touchless processing until
    tomorrow); a low-confidence invoice waits for its turn."""
    if not usable(cfg) or Path(path).suffix.lower() in TEXT_EXTENSIONS:
        return ""
    if low_confidence(capture):
        return LOW
    if not spot_pick(path, cfg.spot_rate):
        return ""
    pages = getattr(_capture(capture), "page_count", 0) or 1
    try:
        used = store.azure_pages_on()
    except Exception:  # noqa: BLE001 - not counted: no spot-check
        return ""
    return SPOT if used + pages <= cfg.daily_pages else ""


# --- Comparing --------------------------------------------------------------------------------------------------


def azure_values(raw: dict[str, Any] | None) -> dict[str, Any]:
    """{capture field: value} of what Azure read (``prebuilt-invoice``'s fields, as capture's DI reader reads them)."""
    from .capture import _di_readings

    if not raw:
        return {}
    return {name: readings[0].value for name, readings in _di_readings(raw).items() if readings}


def _same(name: str, ours: Any, theirs: Any) -> bool:
    from .capture.normalize import same_value
    from .memory import vendor_key

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
    from .capture.confidence import LABELS

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
    from .ui import plural

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
    from .capture.types import CHECK

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
        key = f"other:{row['azure']}"
        readers = [r.strip() for r in str(fr.sources.get(key) or "").split(",") if r.strip()]
        fr.sources[key] = ", ".join(sorted({*readers, READER}))
        fr.status = CHECK
        fr.confidence = min(fr.confidence, 0.5)
        reason = f"{NAME} read {_shown(row['azure'])}"
        if reason not in fr.reasons:
            fr.reasons.append(reason)
    return differ


def issue(rows: list[dict[str, Any]]) -> Any:
    """The review issue for fields Azure read differently (a ``validation.Issue``), or None when it agrees."""
    from .validation import WARNING, Issue

    differ = [r for r in rows if not r["agree"]]
    if not differ:
        return None
    what = "; ".join(f"{r['label']}: Azure {_shown(r['azure'])}, AP Coder {_shown(r['ours'])}" for r in differ)
    return Issue(WARNING, ISSUE_CODE, f"{NAME} read this differently ({what}): check it on the page.")


def reader_rows(rows: list[dict[str, Any]], final: dict[str, Any], layout_source: str = "") -> list[dict[str, Any]]:
    """Azure's reading against what AP approved (reader "di"), for ``Store.record_reader_outcomes``."""
    from .capture.supplier import header_values

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
                report.issues = [i for i in report.issues if i.code != ISSUE_CODE] + [found]
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
    """The touchless decision, held back while Azure hasn't checked an invoice picked for it."""
    if plan.hold and autonomy.get("auto"):
        return {**autonomy, "auto": False, "audit": False, "azure_hold": True, "reason": WAITING_FOR_AZURE}
    return autonomy


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
    """The Azure check can't run (off, packages missing, no endpoint or key) or a request was refused."""


def only_host(host: str) -> Callable[[Any], None]:
    """A request hook for the Azure client: every request it sends (the analysis, the polling, a redirect) must go
    to ``host`` over HTTPS, or it is refused before it leaves this computer."""

    def check(request: Any) -> None:
        http = getattr(request, "http_request", request)
        url = str(getattr(http, "url", "") or "")
        if endpoint_host(url) != host:
            raise AzureUnavailable(f"refused: the Azure check only contacts {host} (not {urlsplit(url).hostname})")

    return check


def make_client(cfg: AzureCheckSettings) -> Any:
    """The Document Intelligence client for the endpoint configured, its key, and nothing else."""
    state, why = status(cfg)
    if state != READY:
        raise AzureUnavailable(why)
    module = importlib.import_module("azure.ai.documentintelligence")
    credentials = importlib.import_module("azure.core.credentials")
    return module.DocumentIntelligenceClient(
        endpoint=cfg.endpoint.rstrip("/"), credential=credentials.AzureKeyCredential(cfg.api_key),
        raw_request_hook=only_host(cfg.host), retry_total=2, logging_enable=False,
    )  # fmt: skip


def read(cfg: AzureCheckSettings, path: str | Path, client: Any = None) -> dict[str, Any]:
    """Azure's reading of the file (``AnalyzeResult.as_dict()``), with the Document Intelligence code AP Coder
    already has (``extraction.DocumentExtractor``)."""
    from .config import DocumentIntelligenceSettings
    from .extraction import DocumentExtractor

    di = DocumentIntelligenceSettings(endpoint=cfg.endpoint, api_key=cfg.api_key, model_id=cfg.model)
    extractor = DocumentExtractor(di, client=client if client is not None else make_client(cfg))
    return extractor.extract(Path(path)).raw or {}


def scrub(text: str, cfg: AzureCheckSettings) -> str:
    """``text`` with the key taken out, should an error ever quote it."""
    text = str(text or "")
    if cfg.api_key and len(cfg.api_key) >= 4:
        text = text.replace(cfg.api_key, "••••")
    return text[:500]


# --- The queue, in the background -------------------------------------------------------------------------------


@dataclass
class CheckOutcome:
    invoice_id: int
    status: str  # done | failed | skipped | postponed
    message: str = ""
    differ: list[str] = dataclasses.field(default_factory=list)
    approved: bool = False


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
    cfg = load_settings(store, settings)
    if not usable(cfg):
        return None
    row = store.next_azure_check(after_page_reader=_page_reader_running(settings, store))
    if not row:
        return None
    iid = int(row["invoice_id"])
    try:
        return _check(cfg, store, row, client)
    except Exception as exc:  # noqa: BLE001 - one invoice must never stop the queue
        message = scrub(f"{type(exc).__name__}: {exc}", cfg)
        log.warning("Azure check: invoice %s failed (%s)", iid, message)
        store.finish_azure_check(iid, "failed", error=message)
        return CheckOutcome(iid, "failed", message)


def _check(cfg: AzureCheckSettings, store: Any, row: dict[str, Any], client: Any) -> CheckOutcome:
    iid, reason = int(row["invoice_id"]), str(row["reason"])
    inv = store.get_invoice(iid)
    path = Path((inv or {}).get("source_path") or "")
    if inv is None or not path.is_file():
        return _skip(store, iid, "the invoice or its file is gone")
    if path.suffix.lower() in TEXT_EXTENSIONS:
        return _skip(store, iid, "a text file: there is no page to send")
    capture = _capture(store.get_capture(iid))
    if capture is None:
        return _skip(store, iid, "AP Coder has no reading of it to compare with")
    if reason == LOW and not low_confidence(capture):  # every field verified since (OvisOCR2 read it)
        if not spot_pick(path, cfg.spot_rate):
            return _skip(store, iid, "every field was verified once OvisOCR2 had read it; not picked for a spot-check")
        reason = SPOT
    pages = capture.page_count or 1
    if pages > cfg.daily_pages:
        return _skip(store, iid, f"{pages} pages: more than the daily cap of {cfg.daily_pages}")
    if store.azure_pages_on() + pages > cfg.daily_pages:
        message = f"today's cap of {cfg.daily_pages} pages is reached: checked tomorrow"
        store.finish_azure_check(iid, "waiting", error=message)
        return CheckOutcome(iid, "postponed", message)

    raw = read(cfg, path, client)
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
    outcome.differ, outcome.approved = _fold_in(store, inv, capture, rows, reason, now)
    store.log_event("azure_checked", iid, actor="AP Coder", detail={
        "reason": reason, "compared": len(rows), "agreed": agreed, "differ": outcome.differ, "pages": billed,
    })  # fmt: skip
    return outcome


def _skip(store: Any, iid: int, why: str) -> CheckOutcome:
    store.finish_azure_check(iid, "skipped", error=why)
    return CheckOutcome(iid, "skipped", why)


def _fold_in(store: Any, inv: dict[str, Any], capture: Any, rows: list[dict[str, Any]], reason: str,
             now: str) -> tuple[list[str], bool]:  # fmt: skip
    """Fold the check into the invoice: still open, its capture, review issue and notes (and, held only for this
    check and Azure agreeing, it is approved as touchless processing would have); approved already, Azure's reading
    is scored against what AP approved. Returns (fields read differently, approved now)."""
    from .capture.workflow import AUTONOMOUS_REVIEWER, still_touchless
    from .store import APPROVED, PARKED, PENDING, REVIEW

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
        if held:
            notes = {k: v for k, v in notes.items() if k != "azure_hold"}
            notes["reason"] = "" if not differ else f"{NAME} read {len(differ)} field(s) differently"
        store.update_after_azure_check(iid, capture.to_dict(), dataclasses.asdict(found) if found else None,
                                       ISSUE_CODE, {"azure_check": note, "capture": notes})  # fmt: skip
        # Held at the touchless decision, maybe days ago: the switch, the supplier's state and the limits are
        # checked again, as they stand now.
        output = inv.get("ai_output")
        if held and untouched and not differ and output and still_touchless(store, capture, output):
            try:
                store.approve_invoice(iid, inv["ai_output"], AUTONOMOUS_REVIEWER, login="ap-coder")
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
        self._halt = threading.Event()
        self.last: CheckOutcome | None = None

    def stop(self) -> None:
        self._halt.set()

    def run(self) -> None:
        while not self._halt.is_set():
            pause = IDLE_SECONDS
            try:
                store = self._store_factory()
                settings = self._settings_factory()
                if not usable(load_settings(store, settings)):
                    pause = OFF_SECONDS
                else:
                    outcome = check_one(settings, store)
                    if outcome is not None:
                        self.last = outcome
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
        raw = read(cfg, sample, client)
        from .capture import analyze

        ours = analyze(sample, today=dt.date.today())
        rows = compare(ours, azure_values(raw))
        record = {"ok": True, "when": when, "summary": summary(rows), "rows": rows, "host": cfg.host,
                  "pages": len(raw.get("pages") or []), "seconds": round(time.perf_counter() - t0, 1),
                  "sample": sample.name}  # fmt: skip
    except Exception as exc:  # noqa: BLE001 - shown on the card, in plain words
        problem = str(exc) if isinstance(exc, AzureUnavailable) else f"{type(exc).__name__}: {exc}"
        record = {"ok": False, "when": when, "error": scrub(problem, cfg), "host": cfg.host}
    store.set_setting(TEST_KEY, json.dumps(record, default=str))
    return record


def last_test(store: Any) -> dict[str, Any] | None:
    try:
        saved = json.loads(store.get_setting(TEST_KEY, "") or "null")
    except (TypeError, ValueError):
        return None
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
        "model": cfg.model, "spot_percent": f"{cfg.spot_rate * 100:g}", "daily_pages": cfg.daily_pages,
        "state": state, "note": note, "ready": state == READY, "packages": packages_installed(),
        "can_test": status(cfg)[0] == READY, "days": days, "checked": len(checks),
        "agreed": agreed, "agreed_rate": agreed / len(checks) if checks else None,
        "fields_compared": sum(int(c["compared"] or 0) for c in checks),
        "fields_agreed": sum(int(c["agreed"] or 0) for c in checks),
        "disagreements": disagreements[:50], "waiting": counts["waiting"] + counts["checking"],
        "failed": counts["failed"], "pages_today": store.azure_pages_on(), "test": last_test(store),
    }  # fmt: skip
