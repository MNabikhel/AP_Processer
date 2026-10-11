"""Environment and data readiness checks (``python -m ap_coder doctor``).

The printed report is designed to be pasted into a support chat: it reports
whether settings are present but never their values (no keys, no endpoint
URLs), and summarises reference data by counts and column names only.
"""

from __future__ import annotations

import importlib.metadata
import sys
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from .config import Settings, _on_this_network
from .extraction import ExtractionResult, build_credential
from .inference import CodingError, InvoiceCoder, ModelProfile
from .local_llm import check_server, resolve_provider
from .offline import internet_allowed
from .reference_data import ReferenceData
from .schema import line_gl_codes, plan_code_enums
from .tax import TAX_TYPES

PASS, WARN, FAIL, SKIP = "PASS", "WARN", "FAIL", "SKIP"

# A tiny synthetic invoice used for the online Azure OpenAI dry run.
_DRY_RUN_INVOICE = """\
Doctor Test Supplies Ltd
INVOICE  No: DR-0001   Date: 2026-01-15   Currency: USD
<table><tr><th>#</th><th>Description</th><th>Qty</th><th>Unit</th><th>Amount</th></tr>
<tr><td>1</td><td>A4 copier paper, 5 reams</td><td>1</td><td>10.00</td><td>10.00</td></tr></table>
Subtotal 10.00  Tax 0.00  Total 10.00
"""


@dataclass
class Check:
    area: str
    status: str
    detail: str


def _version(pkg: str) -> str | None:
    try:
        return importlib.metadata.version(pkg)
    except importlib.metadata.PackageNotFoundError:
        return None


def _secrets(settings: Settings) -> list[str]:
    """Keys, endpoints, endpoint hostnames and resource names: none of these may appear in the report."""
    from urllib.parse import urlparse

    values: list[str] = []
    for endpoint in (settings.openai.endpoint, settings.document_intelligence.endpoint):
        if endpoint:
            values.append(endpoint.rstrip("/"))
            host = urlparse(endpoint if "//" in endpoint else f"https://{endpoint}").hostname or ""
            if host:
                values += [host, host.split(".")[0]]
    values += [k for k in (settings.openai.api_key, settings.document_intelligence.api_key) if k]
    if settings.llm.api_key and settings.llm.api_key != "lm-studio":
        values.append(settings.llm.api_key)
    # Longest first so a hostname is removed before its own resource-name prefix.
    return sorted({v for v in values if len(v) >= 4}, key=len, reverse=True)


def _scrub(message: str, settings: Settings) -> str:
    import re

    for secret in _secrets(settings):
        message = re.sub(re.escape(secret), "<redacted>", message, flags=re.IGNORECASE)
    message = " ".join(message.split())
    return message[:400]


# Packages every install needs, with what stops working without one (shown in the fix).
_PACKAGES = {
    "openai": "",
    "azure-ai-documentintelligence": "",
    "azure-identity": "",
    "pydantic": "",
    "openpyxl": "",
    "pymupdf": " (PDFs can't be read)",
    "streamlit": " (the dashboard can't start)",
    "pandas": " (the dashboard can't start)",
    "python-dotenv": " (settings can't be read)",
}
# Offline: packages come from the bundle's wheelhouse, never from the internet (pip install -e . needs PyPI).
_INSTALL_FIX = "run APProcessor.bat (Mac: APProcessor.command); it installs from the offline bundle"


def _python_check(version: tuple[int, ...]) -> tuple[str, str, str]:
    """3.11 to 3.13 is what the launcher installs and the docs name; 3.10 still runs (CI tests it) but warns."""
    shown = ".".join(str(n) for n in version[:3])
    if (3, 11) <= tuple(version[:2]) <= (3, 13):
        return "python", PASS, f"{shown} (3.11 to 3.13)"
    if tuple(version[:2]) < (3, 10):
        return "python", FAIL, f"{shown} (need 3.11 to 3.13)"
    return "python", WARN, f"{shown} (not tested: use 3.11 to 3.13)"


def run_checks(
    settings: Settings,
    load_reference: Callable[[], ReferenceData],
    online: bool = False,
) -> list[Check]:
    checks: list[Check] = []

    def add(area: str, status: str, detail: str) -> None:
        checks.append(Check(area, status, detail))

    # --- Runtime -------------------------------------------------------------
    add(*_python_check(sys.version_info))
    for pkg in _PACKAGES:
        v = _version(pkg)
        add(f"package {pkg}", PASS if v else FAIL, v or f"not installed{_PACKAGES[pkg]}: {_INSTALL_FIX}")
    v = _version("pillow")
    status = PASS if v else (FAIL if settings.engine.vision else WARN)
    add("package pillow", status, v or "not installed (needed only for --vision)")

    # --- Offline readiness (OCR models, data folder, local-only dashboard) ----------------------------
    from .offline import offline_checks

    for area, status, detail in offline_checks():
        add(area, status, detail)

    # --- Configuration ----------------------------------------------------------
    di, oai, eng = settings.document_intelligence, settings.openai, settings.engine
    if di.endpoint and internet_allowed():
        add("DI endpoint", PASS, "set")
        add("DI auth", PASS, "API key" if di.api_key else "Entra ID (DefaultAzureCredential)")
        add("DI model", PASS, di.model_id)
    elif di.endpoint:  # the offline build: set in the .env, but never used
        add("DI endpoint", SKIP, "set, but not used: the internet isn't allowed, so invoices are read on this computer")
    else:  # optional: without it every invoice is read on this computer
        add("DI endpoint", SKIP, "not set: invoices are read on this computer (PDF text, local OCR for scans)")
    provider = resolve_provider(settings)
    add("AI model", PASS, _provider_detail(settings, provider))
    if provider != "azure":
        add("AOAI endpoint", SKIP, "not used (the offline build codes invoices on this computer)")
    else:
        add("AOAI endpoint", PASS, "set")
        add("AOAI auth", PASS, "API key" if oai.api_key else "Entra ID (DefaultAzureCredential)")
        api_ok = oai.api_version >= "2024-08-01"
        add("AOAI api version", PASS if api_ok else FAIL, f"{oai.api_version} (Structured Outputs needs >= 2024-08-01)")
        profile = ModelProfile.resolve(oai.model_name or oai.deployment, oai.supports_vision)
        add(
            "AOAI model",
            PASS if oai.model_name else WARN,
            f"deployment={oai.deployment}, model={oai.model_name or '(not set; inferred from deployment name)'}, "
            f"vision={profile.supports_vision}, reasoning={profile.reasoning}",
        )
        if eng.vision and not profile.supports_vision and provider == "azure":
            add("vision mode", WARN, "AP_VISION=true but the model is not vision-capable; images will be dropped")
    add(*_local_check(settings, provider))
    add(*_page_reader_check(settings))
    add(
        "engine",
        PASS,
        f"vision={eng.vision}, constrain_codes={eng.constrain_codes}, review_threshold={eng.review_threshold}",
    )

    # --- Reference data -----------------------------------------------------------
    reference: ReferenceData | None = None
    try:
        reference = load_reference()
    except Exception as exc:  # report and continue with the remaining checks
        add("reference data", FAIL, _scrub(str(exc), settings))
    if reference is not None:
        cc_codes = reference.cost_centers.codes if reference.cost_centers is not None else None
        enums = plan_code_enums(line_gl_codes(reference), cc_codes)
        tables = [
            ("GL accounts", reference.chart_of_accounts),
            ("cost centers", reference.cost_centers),
        ]
        for label, table in tables:
            if table is None:
                add(label, PASS, "not configured (optional)")
                continue
            columns = ", ".join(table.rows[0].keys())
            detail = f"{len(table.rows)} active rows; columns: {columns}"
            status = PASS
            if len(table.rows[0]) < 2 or not any(v for r in table.rows for k, v in r.items() if k != table.key_column):
                status, detail = WARN, detail + " (add descriptions: the model needs words to match)"
            if eng.constrain_codes:
                enforced = enums[0] if table is reference.chart_of_accounts else enums[1]
                if enforced is None:
                    status = WARN
                    detail += "; too many codes for schema enums -> free-text codes + local validation"
                else:
                    detail += "; enforced as schema enum"
            add(label, status, detail)

        known = set(reference.chart_of_accounts.codes)
        for tax_type in TAX_TYPES:
            t = reference.tax.treatment(tax_type)
            if not t.needs_gl:
                add(f"tax {tax_type}", PASS, f"{t.treatment} (added to each expense line's GL)")
            elif not t.gl_code:
                add(
                    f"tax {tax_type}", WARN, f"{t.treatment} but no GL account mapped yet (invoices with it will error)"
                )
            elif t.gl_code not in known:
                add(f"tax {tax_type}", FAIL, "mapped GL account is not in the GL accounts list")
            else:
                add(f"tax {tax_type}", PASS, f"{t.treatment} -> mapped GL account")
        add("policy notes", PASS if reference.notes else WARN, f"{len(reference.notes)} rule(s)")

        coder = InvoiceCoder(settings, reference, client=object())
        prompt_tokens = len(coder.system_prompt) // 4
        status = PASS if prompt_tokens < 30_000 else WARN
        add(
            "prompt size",
            status,
            f"~{prompt_tokens:,} tokens of instructions + reference data per invoice "
            "(cached after the first call in a batch)",
        )

    # --- Live connectivity ------------------------------------------------------------
    if not online:
        add("DI connectivity", SKIP, "run with --online to test")
        add("AOAI connectivity", SKIP, "run with --online to test")
        if provider == "local":
            add("local dry run", SKIP, "run with --online to ask the local model for two lines' accounts")
        return checks

    if di.endpoint:
        try:
            from azure.ai.documentintelligence import DocumentIntelligenceAdministrationClient

            admin = DocumentIntelligenceAdministrationClient(di.endpoint, build_credential(di))
            model = admin.get_model(di.model_id)
            api = getattr(model, "api_version", None)
            add("DI connectivity", PASS, f"{di.model_id} available" + (f" (api {api})" if api else ""))
        except Exception as exc:
            add("DI connectivity", FAIL, f"{type(exc).__name__}: {_scrub(str(exc), settings)}")
    else:
        add("DI connectivity", SKIP, "endpoint not set")

    if provider == "local":
        if reference is not None and check_server(settings.llm).active:
            add(*_local_dry_run(settings, reference))
        else:
            add("local dry run", SKIP, "no model loaded or reference data missing")
    elif oai.endpoint and reference is not None:
        add(*_aoai_dry_run(settings, reference))
    else:
        add("AOAI connectivity", SKIP, "endpoint or reference data missing")
    return checks


def _provider_detail(settings: Settings, provider: str) -> str:
    if provider == "azure":
        return "Azure OpenAI (the internet is allowed: AP_ALLOW_INTERNET=1)"
    local = "local: read by the local readers, coded from AP's history and rules"
    if provider == "off":
        return f"{local} (the chat model is turned off for this run)"
    return f"{local}; the LM Studio chat model suggests accounts for new lines when it runs"


def _local_check(settings: Settings, provider: str) -> tuple[str, str, str]:
    """The local server's state: optional (a line nothing was learned for is left for AP without it), so never a
    failure. Its address is shown only when it is on this computer or network (keys never are)."""
    if provider == "azure":
        return "local model", SKIP, "not used (coding with Azure OpenAI)"
    if provider == "off":
        return "local model", SKIP, "the chat model is turned off for this run"
    status = check_server(settings.llm, use_cache=False)
    where = status.base_url if _on_this_network(urlparse(status.base_url).netloc) else "AP_LLM_BASE_URL"
    if status.active:
        sees = "yes" if status.vision else "no"
        override = {"on": " (forced on)", "off": " (turned off)"}.get(settings.llm.vision, "")
        return (
            "local model",
            PASS,
            f"{status.server} at {where}: {status.model}; can see pages: {sees}{override}; "
            f"{len(status.chat_models)} chat model(s) listed",
        )
    if status.reachable:
        return (
            "local model",
            WARN,
            f"{status.server_title} is running but no model is loaded",
        )
    detail = (f"not running at {where} (optional: accounts come from AP's history meanwhile; LM Studio: Developer "
              "tab -> Start server)")  # fmt: skip
    return "local model", WARN, detail


def _page_reader_check(settings: Settings) -> tuple[str, str, str]:
    """The page reader (OvisOCR2, reading every page of every invoice as one more reader): found in LM Studio, and its
    self-test passed. Without it invoices are still read and wait for a person, so it is never a failure."""
    try:
        from .page_reader import reader_status
        from .page_worker import self_test_detail

        status = reader_status(settings, use_cache=False)
        if not status.usable:
            return "page reader", WARN, status.note or "OvisOCR2 isn't running in LM Studio"
        state, detail = self_test_detail(status.model)
    except Exception as exc:  # optional: a problem here never stops the doctor
        return "page reader", WARN, f"not checked ({type(exc).__name__})"
    found = f"{status.model} found in LM Studio ({status.state})"
    if state == "passed":
        return "page reader", PASS, f"{found}; self-test passed: it reads every page of every invoice"
    return "page reader", WARN, f"{found}; self-test {state}: {detail}"


def _aoai_dry_run(settings: Settings, reference: ReferenceData, provider: str | None = None) -> tuple[str, str, str]:
    """Send one synthetic invoice with the real prompt and schema (about one invoice's cost on Azure; free locally)."""
    area = "local dry run" if provider == "local" else "AOAI dry run"
    try:
        coder = InvoiceCoder(settings, reference, provider=provider)
        result = coder.code(
            ExtractionResult(source="doctor-dry-run.md", model_id="synthetic", content=_DRY_RUN_INVOICE)
        )
    except CodingError as exc:
        return area, WARN, f"reached the model but: {_scrub(str(exc), settings)}"
    except Exception as exc:
        hint = _aoai_hint(exc)
        return area, FAIL, f"{type(exc).__name__}: {_scrub(str(exc), settings)}{hint}"
    usage = result.usage
    accepted = "strict schema accepted" if provider != "local" else f"valid coding in {result.attempts} call(s)"
    return (
        area,
        PASS,
        f"{accepted} by {result.model}; prompt {usage.get('prompt_tokens', '?')} tokens, "
        f"completion {usage.get('completion_tokens', '?')} tokens",
    )


def _aoai_hint(exc: Exception) -> str:
    status = getattr(exc, "status_code", None)
    hints = {
        401: " -> check AZURE_OPENAI_API_KEY / your Entra ID role (Cognitive Services OpenAI User)",
        403: " -> your identity lacks access, or a firewall/private endpoint blocks this network",
        404: " -> AZURE_OPENAI_DEPLOYMENT name is wrong or AZURE_OPENAI_API_VERSION is not supported",
        400: " -> the model version may not support Structured Outputs (gpt-4o needs 2024-08-06 or later)",
        429: " -> rate limited; quota is too low for testing or another job is running",
    }
    return hints.get(status, "") if isinstance(status, int) else ""


def format_checks(checks: list[Check]) -> str:
    lines = ["## AP Coder doctor report (no secrets or endpoint URLs included)", ""]
    width = max(len(c.area) for c in checks)
    for c in checks:
        lines.append(f"[{c.status}] {c.area.ljust(width)}  {c.detail}")
    fails = sum(c.status == FAIL for c in checks)
    warns = sum(c.status == WARN for c in checks)
    lines += ["", f"{fails} failure(s), {warns} warning(s)"]
    return "\n".join(lines)


def exit_code(checks: list[Check]) -> int:
    return 1 if any(c.status == FAIL for c in checks) else 0


def _local_dry_run(settings: Settings, reference: Any) -> tuple[str, str, str]:
    """What the pipeline asks a local model for: the accounts of lines nothing else could code (the invoice
    itself is read by the local reader). Two made-up lines, so it answers in seconds even on a laptop CPU."""
    import time

    from .inference import suggest_accounts

    lines = [(1, "Printer paper, letter size, 10 cases", 420.0), (2, "Courier delivery, same day", 35.0)]
    started = time.perf_counter()
    try:
        picks = suggest_accounts(InvoiceCoder(settings, reference), "Sample Office Supply Ltd.", lines)
    except Exception as exc:  # noqa: BLE001 - reported, not raised
        return "local dry run", WARN, f"reached the model but: {_scrub(str(exc), settings)}"
    took = time.perf_counter() - started
    if not picks:
        return "local dry run", WARN, f"the model answered in {took:.0f}s but gave no account from the chart"
    chosen = ", ".join(f"line {n} -> {gl}" for n, (gl, _, _) in sorted(picks.items()))
    return "local dry run", PASS, f"accounts from the chart in {took:.0f}s: {chosen}"
