"""Settings → Reading: how AP Coder reads every invoice, the same way each time, and how each reader is doing.
Ported from ``ap_coder/webapp/reading_settings.py``.

Nothing to choose here. Every invoice goes through the same readers (``ap_coder.reading``); the tab shows each one's
state, the page reader's (OvisOCR2 in LM Studio) self-test (run again from here, its progress polled from
``GET /settings/reading/self-test``), its queue and record, the models LM Studio has (with Load) and a checklist for a
new computer. The Azure Document Intelligence check is a card of its own (``partials/azure_check.html``, built in
``pages/azure.py``); the Azure connection itself shows only in a developer build that may use the internet
(``AP_ALLOW_INTERNET=1``).
"""

from __future__ import annotations

import datetime as dt
import functools
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Form, Request
from fastapi.responses import JSONResponse

from ap_coder import page_reader, page_worker, ui
from ap_coder.config import Settings
from ap_coder.envfile import clean_url, read_env
from ap_coder.offline import internet_allowed
from ap_coder.store import Store
from ap_coder.web.core import (
    done,
    get_settings,
    get_store,
    jobs,
    message,
    reading_status_or_none,
    reference_or_none,
    refuse,
)
from ap_coder.web.jobs import Job

router = APIRouter()

URL = "/settings/reading"

# The same steps for every invoice, nothing to configure: (in bold, the rest).
HOW_IT_READS: tuple[tuple[str, str], ...] = (
    ("The page's own words.", " A digital PDF's text layer is read as it is; a scan or a photo is read by OCR on this "
     "computer, with two engines."),
    ("OvisOCR2 reads every page too,", " digital PDFs included, as a second reader that works on its own. It runs in "
     "LM Studio on this computer, in the background, and is trusted only after its self-test."),
    ("The rule reader, the supplier's template and the business checks", " (totals, tax, GST/HST number, vendor "
     "master, purchase order, duplicates, bank account) compare what the readers found. A field they agree on is "
     "verified; one they read differently is marked Check."),
    ("A person decides", " whenever a reader or a check is unsure. No invoice is approved without a person before "
     "OvisOCR2 has read it."),
)  # fmt: skip
READER_PILL = {
    "ok": ("Working", "ok", "check_circle"),
    "warn": ("Needs a look", "warn", "warning"),
    "off": ("Not working", "err", "error"),
}
SELF_TEST_PILL = {
    "passed": ("Self-test passed", "ok", "verified"),
    "failed": ("Self-test failed: not trusted", "err", "error"),
    "running": ("Self-test running", "info", "hourglass_top"),
    "pending": ("Self-test not run yet", "gray", "pending"),
    "no model": ("No self-test: OvisOCR2 isn't in LM Studio", "gray", "block"),
}
TEST_POLL_SECONDS = 5  # the page looks up the self-test's progress this often while it runs
FIRST_WAIT = "about 10–20 minutes on a laptop without a graphics card"  # before this computer has read a page
MODEL_FILES = (("ATH-MaaS_OvisOCR2-Q8_0.gguf", "813 MB"), ("mmproj-ATH-MaaS_OvisOCR2-f16.gguf", "205 MB"))
DI_MODELS = {
    "prebuilt-layout": "prebuilt-layout · text and tables (recommended)",
    "prebuilt-invoice": "prebuilt-invoice · also invoice fields, used as cross-checks",
}
DOCTOR_JOB = "settings-doctor"


# --- What the tab shows ---------------------------------------------------------------------------------------------


def reader_rows(status: Any) -> list[dict[str, str]]:
    """The readers of ``reading_status``: name, state pill (label, tone, icon), plain-English detail."""
    rows = []
    for line in status.readers:
        label, tone, icon = READER_PILL.get(line.state, ("Unknown", "gray", "help"))
        rows.append({"name": line.name, "label": label, "tone": tone, "icon": icon, "detail": line.detail})
    return rows


def self_test_pill(status: Any, lm_studio_running: bool = True) -> tuple[str, str, str]:
    label, tone, icon = SELF_TEST_PILL.get(status.self_test, (status.self_test or "Unknown", "gray", "help"))
    if status.self_test == "no model" and not lm_studio_running:
        label = "No self-test: LM Studio isn't running"  # whether OvisOCR2 is in it can't be known yet
    return label, tone, icon


def queue_text(status: Any) -> tuple[str, str]:
    """(in bold, the rest): the invoices waiting for OvisOCR2 and how long they take, or that nothing waits."""
    if not status.queue:
        return "", "Nothing waiting for OvisOCR2: it has read every invoice processed so far."
    eta = f" · {page_reader.duration(status.eta_minutes * 60)} to read them" if status.eta_minutes else ""
    return f"{ui.plural(status.queue, 'invoice')} waiting", f" for OvisOCR2{eta}."


def figures_text(status: Any) -> str:
    """On digital PDFs: how often OvisOCR2 read a figure as the PDF's own text has it ("" before it has read one)."""
    if status.figures_agree is None or not status.figures_count:
        return ""
    return (
        f"On digital PDFs, OvisOCR2 read {status.figures_agree:.0%} of figures the same as the PDF's own text "
        f"({status.figures_count:,} figures). The text layer is exact, so this is OvisOCR2's own accuracy."
    )


def test_detail(test: dict[str, Any]) -> str:
    right, total = test.get("fields_right", 0), test.get("fields_total", 0)
    minutes = (test.get("seconds") or 0) / 60
    when = (test.get("when") or "")[:16].replace("T", " ")
    return f"Read {right} of {total} fields right in {minutes:.1f} min · {when}"


def test_rows(test: dict[str, Any]) -> list[dict[str, Any]]:
    def text(value: object) -> str:
        return "" if value is None else str(value)

    return [
        {"field": text(r.get("field", "")), "expected": text(r.get("expected")), "read": text(r.get("read")),
         "match": bool(r.get("match"))}
        for r in test.get("rows") or []
    ]  # fmt: skip


@functools.cache
def test_page_count() -> int:
    """The pages of the test invoice the page reader reads (both pages of the sample: the totals are on the second)."""
    try:
        return max(1, page_reader._page_count(page_reader.TEST_SAMPLE.read_bytes(), True))
    except Exception:  # noqa: BLE001 - the sample missing or unreadable: the test itself says so
        return 2


def test_wait(settings: Settings, model: str) -> str:
    """How long the self-test should take on this computer ("about 3 minutes"), or what a laptop takes before this
    computer has read a page."""
    try:
        estimate = page_reader.page_seconds_estimate(settings, model=model)
    except Exception:  # noqa: BLE001 - no timings yet
        estimate = None
    if not estimate:
        return FIRST_WAIT
    return page_reader.duration(estimate * test_page_count())


test_wait.__test__ = False  # type: ignore[attr-defined]  # not a pytest test


def lm_studio_running(settings: Settings) -> bool:
    """LM Studio's server answers (cached briefly). False when it cannot be asked."""
    try:
        status = page_reader.reader_status(settings)
    except Exception:  # noqa: BLE001 - a status that can't be read is shown as not running
        return False
    return bool(status.reachable and status.lm_studio)


def chat_model(settings: Settings) -> tuple[bool, str]:
    """(ready, label) of the optional chat model that suggests GL accounts."""
    from ap_coder.local_llm import provider_status

    try:
        status = provider_status(settings)
    except Exception:  # noqa: BLE001 - shown as not loaded
        return False, ""
    return status.ready, status.label if status.ready else ""


def setup_steps(status: Any, running: bool, chat: tuple[bool, str]) -> list[tuple[str, str, str]]:
    """(state, step, detail) for a new computer, in order: "ok" when done, "todo" when it is next, "opt" optional."""
    found = bool(status.page_reader_model)
    if found:
        have = f"found: {status.page_reader_model}"
    elif not running:
        have = "start LM Studio first to check"
    else:
        have = "copy its two files into LM Studio's models folder (below)"
    test = {
        "passed": ("ok", "passed: OvisOCR2 is trusted"),
        "running": ("todo", "running now, by itself"),
        "failed": ("bad", "failed: OvisOCR2 isn't trusted until it passes (Run the self-test again)"),
    }.get(status.self_test, ("todo", "runs by itself once OvisOCR2 is found"))
    ready, label = chat
    return [
        ("ok" if running else "todo", "LM Studio is running",
         "its server answers" if running else "start LM Studio, then its server (Developer tab, Start server)"),
        ("ok" if found else "todo", "OvisOCR2 is in LM Studio", have),
        (test[0], "Self-test passed (automatic)", test[1]),
        ("ok" if ready else "opt", "Chat model for GL suggestions (optional)",
         label or "without one, lines are coded from what AP approved before"),
    ]  # fmt: skip


def models_folder() -> Path:
    """Where LM Studio keeps its models (its default: .lmstudio/models in the user's folder)."""
    return Path.home() / ".lmstudio" / "models"


def model_rows(settings: Settings) -> list[dict[str, Any]] | None:
    """Every model LM Studio has, as the table shows it; None when LM Studio isn't answering."""
    try:
        rows = page_reader.lm_studio_models(settings)
    except Exception:  # noqa: BLE001 - shown as not answering
        return None
    if rows is None:
        return None
    return [
        {
            "model": r["model"],
            "loaded": (f"yes, {r['context']:,} tokens" if r.get("context") else "yes") if r.get("loaded") else "no",
            "is_loaded": bool(r.get("loaded")),
            "reads": "document reader" if r.get("document_reader") else ("can see" if r.get("vision") else "no"),
            "document_reader": bool(r.get("document_reader")),
            "used_for": r.get("used_for") or "—",
        }
        for r in rows
    ]


def testing_snapshot(store: Store) -> dict[str, Any]:
    """The self-test under way on this computer, for the page's polling: {state: running | idle, ...}."""
    testing = page_worker.test_under_way(store)
    if not testing:
        return {"state": "idle"}
    return {
        "state": "running",
        "model": str(testing.get("model") or ""),
        "started": str(testing.get("started") or "")[11:16],
        "done": int(testing.get("done") or 0),
        "pages": int(testing.get("pages") or 0),
    }


def page_reader_context(settings: Settings, store: Store, status: Any) -> dict[str, Any]:
    from ap_coder.local_llm import LM_STUDIO_STEPS

    running = lm_studio_running(settings)
    model = status.page_reader_model
    testing = testing_snapshot(store)
    test = page_worker.saved_test(store, model) if model else None
    same, seen = page_worker.figure_totals(store).get("scans", (0, 0))
    try:
        vlm = {row["reader"]: row for row in store.reader_scorecard()}.get("vlm")
    except Exception:  # noqa: BLE001 - the scorecard is a bonus line
        vlm = None
    return {
        "running": running,
        "model": model,
        "self_test": self_test_pill(status, running),
        "self_test_detail": status.self_test_detail,
        "testing": testing,
        "can_test": bool(model) and testing["state"] != "running" and status.self_test != "running",
        "wait": test_wait(settings, model) if model else FIRST_WAIT,
        "test": test,
        "test_detail": test_detail(test) if test else "",
        "test_rows": test_rows(test) if test else [],
        "copy_folder": str(models_folder() / "bartowski" / "ATH-MaaS_OvisOCR2-GGUF"),
        "model_files": MODEL_FILES,
        "lm_studio_steps": LM_STUDIO_STEPS,
        "queue": queue_text(status),
        "figures": figures_text(status),
        "scans": (
            f"On scans and photos, OvisOCR2 and OCR read {same:,} of {seen:,} figures the same ({same / seen:.0%}); "
            "where they differ on a field, the field is marked Check."
            if seen
            else ""
        ),
        "scorecard": (
            f"Against what AP approved, OvisOCR2 agreed on {vlm['agreed']} of {vlm['fields']} fields "
            f"({vlm['rate']:.1%}), on {ui.plural(vlm.get('invoices', 0), 'invoice')}. Learning & accuracy → Readers "
            "compares every reader."
            if vlm and vlm.get("fields")
            else ""
        ),
        "steps": setup_steps(status, running, chat_model(settings)),
    }


def secret_hint(value: str) -> str:
    return f"set (ending …{value[-4:]})" if len(value) > 8 else ("set" if value else "not set: signs in with az login")


def azure_context(settings: Settings, request: Request) -> dict[str, Any]:
    """The Azure connection (developer build only): values in use, keys only as a hint, the last test's result."""
    from ap_coder.web.pages.settings import env_path

    env = read_env(env_path())
    env.setdefault("AZURE_OPENAI_DEPLOYMENT", settings.openai.deployment or "")
    env.setdefault("AZURE_OPENAI_API_VERSION", settings.openai.api_version or "")
    env.setdefault("AZURE_DOCUMENT_INTELLIGENCE_MODEL", settings.document_intelligence.model_id or "")
    current = env.get("AZURE_DOCUMENT_INTELLIGENCE_MODEL") or "prebuilt-layout"
    models = dict(DI_MODELS) if current in DI_MODELS else {current: current, **DI_MODELS}
    return {
        "env": env,
        "env_file": str(env_path()),
        "di_models": models,
        "di_model": current,
        "di_key_hint": secret_hint(env.get("AZURE_DOCUMENT_INTELLIGENCE_KEY", "")),
        "aoai_key_hint": secret_hint(env.get("AZURE_OPENAI_API_KEY", "")),
        "doctor": jobs(request).get(DOCTOR_JOB).snapshot(),
    }


def reading_context(request: Request, *, fresh: bool = False) -> dict[str, Any]:
    """Everything the Reading tab shows. Never raises over LM Studio: a status that can't be worked out is said."""
    settings, store = get_settings(request), get_store(request)
    status = reading_status_or_none(settings, store, use_cache=not fresh)
    context: dict[str, Any] = {
        "how_it_reads": HOW_IT_READS,
        "status": status,
        "readers": reader_rows(status) if status is not None else [],
        "models": model_rows(settings),
        "poll_seconds": TEST_POLL_SECONDS,
        "developer": internet_allowed(),
    }
    if status is not None:
        context["reader"] = page_reader_context(settings, store, status)
    if context["developer"]:
        context["azure"] = azure_context(settings, request)
    return context


# --- Actions --------------------------------------------------------------------------------------------------------


@router.post("/settings/reading/check")
def check_again(request: Request) -> JSONResponse:
    """Ask LM Studio and the readers afresh (the page normally uses a short-lived cache)."""
    from ap_coder.local_llm import forget_status

    page_reader.forget_status()
    forget_status()
    reading_status_or_none(get_settings(request), get_store(request), use_cache=False)
    return done(request, URL, "Checked again just now.", tone="info")


@router.post("/settings/reading/self-test")
def run_self_test(request: Request) -> JSONResponse:
    """Run the page reader's self-test again, in the background (``page_worker.start_test``)."""
    settings, store = get_settings(request), get_store(request)
    status = reading_status_or_none(settings, store)
    model = getattr(status, "page_reader_model", "") if status is not None else ""
    if not model:
        return refuse("OvisOCR2 isn't in LM Studio, so there is nothing to test. Press Check again once it is.")
    wait = test_wait(settings, model)
    if not page_worker.start_test(settings, store, model):
        return message("A self-test is already running: one at a time.", "info")
    return done(request, URL, f"Self-test of {model} started in the background ({wait}).")


@router.get("/settings/reading/self-test")
def self_test_status(request: Request) -> JSONResponse:
    """How far the self-test under way got (the Reading tab polls this while it runs)."""
    return JSONResponse(testing_snapshot(get_store(request)), headers={"Cache-Control": "no-store"})


@router.post("/settings/reading/load-model")
def load_model(request: Request, model: str = Form("")) -> JSONResponse:
    """Have LM Studio load ``model`` (``page_reader.load_model``: OvisOCR2 with the context a page needs)."""
    model = model.strip()
    if not model:
        return refuse("Choose a model to load.")
    problem = page_reader.load_model(get_settings(request), model)
    if problem:
        return refuse(problem)
    page_reader.forget_status()
    return done(request, URL, f"{model} is loaded.")


# --- Developer build: the Azure connection ----------------------------------------------------------------------------


@router.post("/settings/reading/azure")
def save_azure(
    request: Request,
    di_endpoint: str = Form(""),
    aoai_endpoint: str = Form(""),
    di_key: str = Form(""),
    aoai_key: str = Form(""),
    di_model: str = Form("prebuilt-layout"),
    deployment: str = Form(""),
    model_name: str = Form(""),
    api_version: str = Form(""),
    clear_keys: str = Form(""),
) -> JSONResponse:
    from ap_coder.web.pages.settings import save_env, saved_line

    if not internet_allowed():
        return refuse("Cloud services are only in a developer build that may use the internet.", status=403)
    updates = {
        "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT": clean_url(di_endpoint),
        "AZURE_OPENAI_ENDPOINT": clean_url(aoai_endpoint),
        "AZURE_DOCUMENT_INTELLIGENCE_MODEL": di_model.strip() or "prebuilt-layout",
        "AZURE_OPENAI_DEPLOYMENT": deployment.strip(),
        "AZURE_OPENAI_MODEL_NAME": model_name.strip(),
        "AZURE_OPENAI_API_VERSION": api_version.strip(),
    }
    if clear_keys:
        updates |= {"AZURE_DOCUMENT_INTELLIGENCE_KEY": "", "AZURE_OPENAI_API_KEY": ""}
    else:
        if di_key.strip():
            updates["AZURE_DOCUMENT_INTELLIGENCE_KEY"] = di_key.strip()
        if aoai_key.strip():
            updates["AZURE_OPENAI_API_KEY"] = aoai_key.strip()
    changed = save_env(get_store(request), updates)
    return done(request, URL + "#azure", saved_line(changed))


@router.post("/settings/reading/azure-test")
def test_azure(request: Request) -> JSONResponse:
    """Send one tiny made-up invoice to each Azure service and check the rest (``doctor.run_checks``), as a job."""
    from ap_coder.doctor import FAIL, PASS, WARN, run_checks

    if not internet_allowed():
        return refuse("Cloud services are only in a developer build that may use the internet.", status=403)
    settings, store = get_settings(request), get_store(request)

    def reference() -> Any:
        found = reference_or_none(store)
        if found is None:
            raise ValueError("no GL accounts imported yet (GL accounts & tax page)")
        return found

    def work(job: Job) -> dict[str, Any]:
        job.step(0, 1, "Talking to Azure… (up to a minute)")
        checks = run_checks(settings, reference, online=True)
        order = {FAIL: 0, WARN: 1, PASS: 2}
        tone = {PASS: "ok", WARN: "warn", FAIL: "err"}
        return {
            "when": dt.datetime.now().strftime("%H:%M"),
            "fails": sum(c.status == FAIL for c in checks),
            "warns": sum(c.status == WARN for c in checks),
            "checks": [
                {"status": c.status, "tone": tone.get(c.status, "gray"), "area": c.area, "detail": c.detail}
                for c in sorted(checks, key=lambda c: order.get(c.status, 3))
            ],
        }

    if not jobs(request).get(DOCTOR_JOB).start(work, total=1):
        return message("The test is already running.", "info")
    return JSONResponse({"ok": True, "job": DOCTOR_JOB, "status": f"/jobs/{DOCTOR_JOB}"})
