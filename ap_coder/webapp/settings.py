"""Settings: AI model (LM Studio or Azure), Azure connection (with a live test), review behaviour, data folder and
backups, about."""

from __future__ import annotations

import datetime as dt
import os
import platform
import subprocess
from pathlib import Path

import streamlit as st

from ap_coder import __version__, paths, ui
from ap_coder.config import normalise_base_url
from ap_coder.doctor import FAIL, PASS, WARN, run_checks
from ap_coder.envfile import clean_url, read_env, write_env
from ap_coder.local_llm import LM_STUDIO_STEPS, check_server, forget_status, resolve_provider
from ap_coder.safe import md
from ap_coder.store import Store
from ap_coder.webapp.common import (
    DB_PATH,
    INVOICE_DIR,
    PUBLIC_DEMO,
    card,
    esc,
    get_settings,
    get_store,
    not_in_public_demo,
    notify,
    open_folder,
    page_head,
    reference_or_none,
    reviewer,
    show_toast,
)

DI_MODELS = {
    "prebuilt-layout": "prebuilt-layout · text and tables (recommended)",
    "prebuilt-invoice": "prebuilt-invoice · also invoice fields, used as cross-checks",
}
STATUS_TONE = {PASS: "ok", WARN: "warn", FAIL: "err"}


def env_path() -> Path:
    """The .env being used, or where a new one goes (the data folder)."""
    return paths.env_file() or paths.private_dir() / ".env"


def save_settings(updates: dict[str, str]) -> list[str]:
    """Write changed values to the .env and apply them to this running dashboard. Returns changed keys."""
    current = read_env(env_path())
    changed = {k: v for k, v in updates.items() if v != current.get(k, "")}
    if changed:
        detail: dict[str, object] = {"keys": sorted(changed)}
        if "AP_REVIEWER" in changed:  # a name change is recorded under the old name, with both names
            detail["reviewer"] = {"from": reviewer(), "to": changed["AP_REVIEWER"]}
        get_store().log_event("settings_changed", actor=reviewer(), detail=detail)
        write_env(env_path(), changed)
        os.environ.update(changed)  # the dashboard reads settings from the environment
    return sorted(changed)


def _secret_hint(value: str) -> str:
    return f"set (ending …{value[-4:]})" if len(value) > 8 else ("set" if value else "not set: signs in with az login")


def azure_tab(store: Store) -> None:
    if PUBLIC_DEMO:
        with card("azure"):
            st.markdown("#### :material/cloud: Azure connection")
            not_in_public_demo("Connecting Azure")
        return
    env = read_env(env_path())
    effective = get_settings()  # values in use, including built-in defaults not written in the file
    env.setdefault("AZURE_OPENAI_DEPLOYMENT", effective.openai.deployment or "")
    env.setdefault("AZURE_OPENAI_API_VERSION", effective.openai.api_version or "")
    env.setdefault("AZURE_DOCUMENT_INTELLIGENCE_MODEL", effective.document_intelligence.model_id or "")
    with card("azure"):
        st.markdown("#### :material/cloud: Azure connection")
        st.caption(
            "Saved on this computer. Keys show only their last 4 characters: leave a key field empty to keep it.",
            help=f"Settings file: {env_path()}",
        )
        with st.form("azure_form", border=False):
            c1, c2 = st.columns(2)
            di_endpoint = c1.text_input(
                "Document Intelligence endpoint", env.get("AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT", ""),
                placeholder="https://<name>.cognitiveservices.azure.com/",
            )  # fmt: skip
            aoai_endpoint = c2.text_input(
                "Azure OpenAI endpoint", env.get("AZURE_OPENAI_ENDPOINT", ""),
                placeholder="https://<name>.openai.azure.com/",
            )  # fmt: skip
            di_key = c1.text_input(
                "Document Intelligence key", type="password",
                help=_secret_hint(env.get("AZURE_DOCUMENT_INTELLIGENCE_KEY", "")),
                placeholder=_secret_hint(env.get("AZURE_DOCUMENT_INTELLIGENCE_KEY", "")),
            )  # fmt: skip
            aoai_key = c2.text_input(
                "Azure OpenAI key", type="password",
                help=_secret_hint(env.get("AZURE_OPENAI_API_KEY", "")),
                placeholder=_secret_hint(env.get("AZURE_OPENAI_API_KEY", "")),
            )  # fmt: skip
            current_model = env.get("AZURE_DOCUMENT_INTELLIGENCE_MODEL", "prebuilt-layout")
            models = list(DI_MODELS) if current_model in DI_MODELS else [current_model, *DI_MODELS]
            di_model = c1.selectbox(
                "Document Intelligence model", models, index=models.index(current_model),
                format_func=lambda m: DI_MODELS.get(m, m),
            )  # fmt: skip
            deployment = c2.text_input("Azure OpenAI deployment name", env.get("AZURE_OPENAI_DEPLOYMENT", ""))
            c3, c4 = st.columns(2)
            model_name = c3.text_input(
                "Model behind the deployment", env.get("AZURE_OPENAI_MODEL_NAME", ""),
                placeholder="e.g. gpt-4o or gpt-4o-mini", help="Used to know whether the model can read images.",
            )  # fmt: skip
            api_version = c4.text_input(
                "API version", env.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
                help="Structured Outputs needs 2024-08-01-preview or later.",
            )  # fmt: skip
            clear_keys = st.checkbox("Sign in with az login instead of keys (removes both keys)")
            saved = st.form_submit_button("Save Azure settings", type="primary", icon=":material/save:")
        if saved:
            updates = {
                "AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT": clean_url(di_endpoint),
                "AZURE_OPENAI_ENDPOINT": clean_url(aoai_endpoint),
                "AZURE_DOCUMENT_INTELLIGENCE_MODEL": di_model,
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
            changed = save_settings(updates)
            notify(f"Saved {ui.plural(len(changed), 'change')}." if changed else "Nothing changed.", ":material/save:")
            st.rerun()

    with card("azure_test"):
        head, button = st.columns([3, 1], vertical_alignment="center")
        head.markdown("#### :material/network_check: Test the connection")
        head.caption(
            "Sends one tiny made-up invoice to each service (a fraction of a cent) and checks everything else "
            "AP Coder needs. Nothing from your invoices is sent."
        )
        if button.button("Run the test", icon=":material/play_arrow:", key="run_doctor", width="stretch"):
            with st.spinner("Talking to Azure… (up to a minute)"):
                checks = run_checks(get_settings(), lambda: _reference(store), online=True)
            st.session_state["doctor_result"] = (dt.datetime.now().strftime("%H:%M"), checks)
        result = st.session_state.get("doctor_result")
        if result:
            when, checks = result
            fails = sum(c.status == FAIL for c in checks)
            warns = sum(c.status == WARN for c in checks)
            summary = (
                ui.pill(f"{ui.plural(fails, 'problem')}", "err", "error")
                if fails
                else ui.pill("Everything works", "ok", "check_circle")
            )
            if warns:
                summary += " " + ui.pill(f"{warns} to look at", "warn", "warning")
            st.html(f"<div style='margin-bottom:.5rem'>{summary} <span class='apc-muted'>tested at {when}</span></div>")
            order = {FAIL: 0, WARN: 1, PASS: 2}
            rows = [
                [ui.pill(c.status, STATUS_TONE.get(c.status, "gray")), esc(c.area), esc(c.detail)]
                for c in sorted(checks, key=lambda c: order.get(c.status, 3))
            ]
            st.html(ui.table(["", "Check", "Detail"], rows, wrap=[2]))


PROVIDERS = {
    "auto": "Automatic: Azure OpenAI if it is set up, else the model in LM Studio",
    "local": "Local model on this computer (LM Studio or Ollama)",
    "azure": "Azure OpenAI",
    "off": "Off: no AI coding",
}
VISION_CHOICES = {"auto": "Automatic: when the model can see pages", "on": "Yes", "off": "No, text only"}


def _model_status_html(settings) -> str:
    """One line: which AI codes invoices, and whether it is answering."""
    provider = resolve_provider(settings)
    if provider == "azure":
        return (
            ui.pill("Using Azure OpenAI", "ok", "cloud")
            + f" <span class='apc-muted'>{esc(settings.openai.deployment)}</span>"
        )
    if settings.llm.provider == "off":
        return ui.pill("Off", "gray", "block") + " <span class='apc-muted'>Invoices are not coded by AI.</span>"
    status = check_server(settings.llm)
    if status.active:
        sees = "can see pages: yes" if status.vision else "can see pages: no"
        return (
            ui.pill(f"Connected to {status.server}", "ok", "check_circle")
            + f" <span class='apc-muted'>{esc(status.model)} · {esc(sees)}</span>"
        )
    if status.reachable:
        return ui.pill("No model loaded", "warn", "warning") + (
            f" <span class='apc-muted'>{esc(status.server_title)} is running: load a model in it.</span>"
        )
    return ui.pill("Not running", "err", "error") + f" <span class='apc-muted'>{esc(status.base_url)}</span>"


def ai_model_tab() -> None:
    if PUBLIC_DEMO:
        with card("ai_model"):
            st.markdown("#### :material/smart_toy: AI model")
            not_in_public_demo("Connecting an AI model")
        return
    settings = get_settings()
    llm = settings.llm
    with card("ai_model"):
        head, button = st.columns([3, 1], vertical_alignment="center")
        head.markdown("#### :material/smart_toy: AI model")
        head.caption(
            "The model that codes each invoice. A model in LM Studio runs on this computer: nothing is sent out."
        )
        if button.button("Test connection", icon=":material/network_check:", key="test_llm", width="stretch"):
            forget_status()
            check_server(llm, use_cache=False)
            st.session_state["llm_tested"] = dt.datetime.now().strftime("%H:%M")
        status_line = _model_status_html(settings)
        tested = st.session_state.get("llm_tested")
        if tested:
            status_line += f" <span class='apc-muted'>· tested at {esc(tested)}</span>"
        st.html(f"<div style='margin:.25rem 0 .5rem'>{status_line}</div>")
        status = check_server(llm)
        if resolve_provider(settings) != "azure" and llm.provider != "off" and not status.active:
            steps = "\n".join(f"{n}. {step}" for n, step in enumerate(LM_STUDIO_STEPS, start=1))
            with st.container(key="note_lmstudio"):
                st.markdown(f"**To use a model on this computer**\n\n{steps}\n\nThen press **Test connection**.")

    with card("ai_model_settings"), st.form("ai_model_form", border=False):
        st.markdown("#### :material/tune: Which model")
        providers = list(PROVIDERS)
        provider = st.selectbox(
            "Use", providers, index=providers.index(llm.provider), format_func=PROVIDERS.get, key="llm_provider"
        )
        c1, c2 = st.columns(2)
        base_url = c1.text_input(
            "Server address", llm.base_url, key="llm_base_url",
            help="LM Studio shows it in the Developer tab. Ollama: http://127.0.0.1:11434/v1",
        )  # fmt: skip
        models = ["", *status.chat_models]
        if llm.model and llm.model not in models:
            models.append(llm.model)
        model = c2.selectbox(
            "Model", models, index=models.index(llm.model), key="llm_model",
            format_func=lambda m: m or "Automatic: the model loaded in LM Studio",
            help="The list comes from the server. Press Test connection to refresh it.",
        )  # fmt: skip
        vision_modes = list(VISION_CHOICES)
        vision = st.selectbox(
            "Show the model the page images", vision_modes, index=vision_modes.index(llm.vision),
            format_func=VISION_CHOICES.get, key="llm_vision",
            help="Helps with scans when the model can see (a vision model shows an eye icon in LM Studio). Slower.",
        )  # fmt: skip
        if st.form_submit_button("Save AI model settings", type="primary", icon=":material/save:"):
            # Only what differs from the values in effect: a default is not written to the .env as a "change".
            updates = {}
            if provider != llm.provider:
                updates["AP_LLM_PROVIDER"] = provider
            if normalise_base_url(base_url) != llm.base_url:
                updates["AP_LLM_BASE_URL"] = normalise_base_url(base_url)
            if model != llm.model:
                updates["AP_LLM_MODEL"] = model
            if vision != llm.vision:
                updates["AP_LLM_VISION"] = vision
            changed = save_settings(updates)
            forget_status()
            notify(f"Saved {ui.plural(len(changed), 'change')}." if changed else "Nothing changed.", ":material/save:")
            st.rerun()


def _reference(store: Store):
    reference = reference_or_none(store)
    if reference is None:
        raise ValueError("no GL accounts imported yet (GL accounts & tax page)")
    return reference


def review_tab() -> None:
    env = read_env(env_path())
    settings = get_settings()
    with card("review_settings"), st.form("review_form", border=False):
        st.markdown("#### :material/tune: Review and AI behaviour")
        reviewer_name = st.text_input(
            "Your name",
            paths.read_user_settings().get("reviewer") or env.get("AP_REVIEWER") or os.environ.get("AP_REVIEWER", ""),
            placeholder="shown on the invoices you approve",
            help="Saved for your Windows account: two people sharing one AP Coder each keep their own name.",
        )
        threshold = st.slider(
            "Send to *Needs attention* when the AI's confidence is below",
            min_value=50, max_value=99, step=1, value=round(float(settings.engine.review_threshold) * 100),
            format="%d%%",
            help="Invoices with any error always need attention. Higher = more invoices get a closer look. Applies "
            "to invoices processed from now on; invoices already in the queue keep their flag.",
        ) / 100  # fmt: skip
        vision = st.toggle(
            "Also send page images to the AI (vision models only, e.g. gpt-4o)",
            value=settings.engine.vision,
            help="Helps with scans and unusual layouts; costs more tokens per invoice.",
        )
        constrain = st.toggle(
            "Only allow codes from my GL account list (recommended)",
            value=settings.engine.constrain_codes,
            help="Off only for very large charts of accounts; codes are still checked after the AI answers.",
        )
        if PUBLIC_DEMO:
            st.caption(":material/science: Fixed in the public demo: these apply to invoices read with Azure.")
        if st.form_submit_button("Save review settings", type="primary", icon=":material/save:", disabled=PUBLIC_DEMO):
            name = reviewer_name.strip()
            renamed = bool(name) and name != reviewer()
            if renamed:  # logged under the old name, with both names
                rename = {"from": reviewer(), "to": name}
                get_store().log_event(
                    "settings_changed", actor=reviewer(), detail={"keys": ["reviewer"], "reviewer": rename}
                )
                paths.write_user_settings(reviewer=name)
            # Only what differs from the values in effect: a default is not written to the .env as a "change".
            updates = {}
            if round(threshold, 2) != round(float(settings.engine.review_threshold), 2):
                updates["AP_REVIEW_THRESHOLD"] = f"{threshold:.2f}"
            if vision != settings.engine.vision:
                updates["AP_VISION"] = "true" if vision else "false"
            if constrain != settings.engine.constrain_codes:
                updates["AP_CONSTRAIN_CODES"] = "true" if constrain else "false"
            changed = save_settings(updates)
            if renamed:
                st.session_state.pop("reviewer", None)
                changed.append("reviewer")
            notify(f"Saved {ui.plural(len(changed), 'change')}." if changed else "Nothing changed.", ":material/save:")
            st.rerun()
    store = get_store()
    with card("payment_settings"), st.form("payment_form", border=False):
        st.markdown("#### :material/event_available: Approval and payment")
        limit = st.number_input(
            "Second approval for invoices over (0 = never)",
            min_value=0.0, step=1000.0, value=store.approval_limit(), format="%.2f",
            help="Above this amount, an approved invoice waits for a second, different approver before export.",
        )  # fmt: skip
        days = st.number_input(
            "Days to pay when an invoice prints no due date and no terms",
            min_value=0, max_value=180, step=1, value=store.default_terms_days(),
            help="Used to show when an invoice is due, to sort the queue by due date and in exports.",
        )  # fmt: skip
        fx = st.text_input(
            "Exchange rates to CAD (optional)", store.get_setting("fx_rates"), placeholder="e.g. USD=1.37, EUR=1.50",
            help="CAD per unit of each foreign currency you are billed in. Used for estimates in CAD (Spend, Sales "
            "tax); invoices and exports keep their own currency.",
        )  # fmt: skip
        if st.form_submit_button("Save approval settings", type="primary", icon=":material/save:"):
            changed = []
            if fx.strip() != store.get_setting("fx_rates"):
                store.set_setting("fx_rates", fx.strip(), actor=reviewer())
                changed.append("fx_rates")
            if int(days) != store.default_terms_days():
                store.set_setting("default_terms_days", str(int(days)), actor=reviewer())
                changed.append("default_terms_days")
            if float(limit) != store.approval_limit():
                store.set_setting("approval_limit", f"{float(limit):.2f}", actor=reviewer())
                changed.append("approval_limit")
            if changed:
                store.log_event("settings_changed", actor=reviewer(), detail={"keys": changed})
                notify("Saved.", ":material/save:")
            else:
                notify("Nothing changed.", ":material/save:")
            st.rerun()


def _size(path: Path) -> str:
    size = path.stat().st_size if path.exists() else 0
    return f"{size / 1024 / 1024:.1f} MB" if size >= 1024 * 1024 else f"{size / 1024:.0f} KB"


def data_tab(store: Store) -> None:
    data = DB_PATH.parent
    with card("data_folder"):
        head, button = st.columns([3, 1], vertical_alignment="center")
        head.markdown("#### :material/folder_managed: Data folder")
        head.caption(
            "Everything AP Coder keeps (database, invoices, exports, backups and settings) is in one folder on "
            "this computer."
        )
        if button.button(
            "Open folder",
            icon=":material/folder_open:",
            key="open_data",
            width="stretch",
            disabled=PUBLIC_DEMO,
            help=str(data),
        ):
            if not open_folder(data):
                st.info(f"Open this folder yourself: {data}")
        invoices = store.list_invoices()
        files = len(list(INVOICE_DIR.glob("*"))) if INVOICE_DIR.exists() else 0
        st.html(
            ui.tiles(
                [
                    ui.tile("Database", _size(DB_PATH), "database", "blue", "ap_coder.db"),
                    ui.tile(
                        "Invoices", len(invoices), "receipt_long", "green", f"{ui.plural(files, 'file')} in invoices/"
                    ),
                    ui.tile("Lessons", len(store.feedback_rows()), "psychology", "violet", "reviewer decisions"),
                    ui.tile("Backups", len(store.list_backups()), "backup", "amber", "kept in backups/"),
                ]
            )
        )

    with card("backups"):
        head, button = st.columns([3, 1], vertical_alignment="center")
        head.markdown("#### :material/backup: Backups")
        head.caption(
            "A copy of the database is made automatically once a day when AP Coder starts (the newest 14 are "
            "kept). Make one yourself before big changes, e.g. importing a new chart of accounts."
        )
        if button.button("Back up now", icon=":material/backup:", key="backup_now", width="stretch"):
            made = store.backup_now("manual", actor=reviewer())
            notify(f"Backed up to {made.name}.", ":material/backup:")
            st.rerun()
        if PUBLIC_DEMO:  # a folder on the web server is no place for a backup copy
            st.caption(":material/science: Copying backups to a OneDrive or network folder: not in the public demo.")
        else:
            with st.form("backup_copy_form", border=False):
                c1, c2 = st.columns([4, 1], vertical_alignment="bottom")
                folder = c1.text_input(
                    "Also copy each backup to (optional)", store.get_setting("backup_copy_dir"),
                    placeholder=r"e.g. C:\Users\you\OneDrive - Company\AP Coder backups",
                    help="A OneDrive, SharePoint-synced or network folder, so a lost or broken computer does not lose "
                    "the database. Only the backup copies go there; the database itself stays on this computer.",
                )  # fmt: skip
                if c2.form_submit_button("Save", icon=":material/save:", width="stretch"):
                    folder = folder.strip().strip('"')
                    if folder == store.get_setting("backup_copy_dir"):
                        notify("Nothing changed.", ":material/save:")
                        st.rerun()
                    elif folder and not Path(folder).expanduser().is_dir():
                        st.error("That folder does not exist (or is not reachable from this computer).")
                    elif folder and Path(folder).expanduser().resolve() == store.backup_dir().resolve():
                        st.error("That is the local backups folder: choose a folder on OneDrive or a network drive.")
                    else:
                        store.set_setting("backup_copy_dir", folder, actor=reviewer())
                        store.log_event("settings_changed", actor=reviewer(), detail={"keys": ["backup_copy_dir"]})
                        if folder:
                            store.backup_now()  # a first copy straight away (backup_now copies it)
                        notify(
                            "Backups will be copied there too."
                            if folder
                            else "Backups are kept on this computer only.",
                            ":material/backup:",
                        )
                        st.rerun()
        status = store.get_setting("backup_copy_status")
        if store.get_setting("backup_copy_dir") and status:
            state, _, rest = status.partition(" ")
            when, _, what = rest.partition(": ")
            when = when.replace("T", " ")
            if state == "ok":
                st.caption(f":material/check_circle: Last copied {md(when)}: {md(what)}")
            else:
                st.caption(f":material/error: Copying failed {md(when)}: {md(what)}. The local backups are fine.")
        backups = store.list_backups()
        if not backups:
            st.caption("No backups yet.")
            return
        rows = [
            [
                esc(b.name),
                esc(dt.datetime.fromtimestamp(b.stat().st_mtime).strftime("%Y-%m-%d %H:%M")),
                esc(_size(b)),
            ]
            for b in backups[:10]
        ]
        st.html(ui.table(["Backup", "Made", "Size"], rows, right=[2]))
        chosen = st.selectbox("Download or restore a backup", [b.name for b in backups], key="backup_choice")
        path = store.backup_dir() / chosen
        c1, c2 = st.columns(2)
        c1.download_button(
            "Download this backup", path.read_bytes(), file_name=chosen, mime="application/octet-stream",
            icon=":material/download:", width="stretch",
        )  # fmt: skip
        with c2.popover("Restore this backup…", icon=":material/settings_backup_restore:", width="stretch"):
            st.markdown(
                f"Replace the current database with **{esc(chosen)}**? Everything done since that backup "
                "(processed invoices, approvals, lessons, GL changes) is replaced. The current database is "
                "backed up first, so this can be undone."
            )
            confirm = st.text_input("Type RESTORE to confirm", key="restore_confirm")
            if st.button("Restore", type="primary", disabled=confirm.strip().upper() != "RESTORE", key="restore"):
                safety = store.restore_from(path)
                st.session_state.pop("open_invoice", None)
                notify(f"Restored {chosen}. The previous database was saved as {safety.name}.", ":material/restore:")
                st.rerun()


def _git_version() -> str:
    if not (paths.PROJECT_DIR / ".git").exists():
        return ""
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%h · %cd", "--date=format:%Y-%m-%d %H:%M"],
            cwd=paths.PROJECT_DIR, capture_output=True, text=True, timeout=5,
        )  # fmt: skip
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def about_tab() -> None:
    import streamlit as streamlit_module

    with card("about"):
        st.markdown("#### :material/info: About this installation")
        rows = [
            ["Version", esc(__version__ + (f" · {_git_version()}" if _git_version() else ""))],
            ["Code folder", f"<code>{esc(paths.PROJECT_DIR)}</code>"],
            ["Data folder", f"<code>{esc(DB_PATH.parent)}</code>"],
            ["Settings file", f"<code>{esc(env_path())}</code>"],
            ["Python", esc(platform.python_version())],
            ["Streamlit", esc(streamlit_module.__version__)],
            ["Computer", esc(f"{platform.system()} {platform.release()}")],
        ]
        st.html(ui.table(["", ""], rows, wrap=[1]))
        with st.container(key="note_update"):
            st.markdown(
                "**Updating:** close AP Coder and double-click `install.bat` again. With git it downloads the new "
                "version (without git, extract the new ZIP over the same folder first); your data, settings and "
                "shortcut are kept.\n\n"
                "**Help:** see `docs/GETTING_STARTED.md` in the code folder."
            )


def page_settings() -> None:
    store = get_store()
    show_toast()
    page_head("settings", "Settings", "AI model, review behaviour, JD Edwards E1, your data and backups.")
    ai_model, azure, review, erp, data, about = st.tabs(
        [
            ":material/smart_toy: AI model",
            ":material/cloud: Azure",
            ":material/tune: Review",
            ":material/account_tree: JD Edwards E1",
            ":material/database: Data & backups",
            ":material/info: About",
        ]
    )
    with ai_model:
        ai_model_tab()
    with azure:
        azure_tab(store)
    with review:
        review_tab()
    with erp:
        from ap_coder.webapp.jde_settings import jde_tab

        jde_tab(store)
    with data:
        data_tab(store)
    with about:
        about_tab()
