"""Settings: Azure connection (with a live test), review behaviour, data folder and backups, about."""

from __future__ import annotations

import datetime as dt
import os
import platform
import subprocess
from pathlib import Path

import streamlit as st

from ap_coder import __version__, paths, ui
from ap_coder.doctor import FAIL, PASS, WARN, run_checks
from ap_coder.envfile import clean_url, read_env, write_env
from ap_coder.safe import md
from ap_coder.store import Store
from ap_coder.webapp.common import (
    DB_PATH,
    INVOICE_DIR,
    card,
    esc,
    get_settings,
    get_store,
    notify,
    open_folder,
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
    env = read_env(env_path())
    effective = get_settings()  # values in use, including built-in defaults not written in the file
    env.setdefault("AZURE_OPENAI_DEPLOYMENT", effective.openai.deployment or "")
    env.setdefault("AZURE_OPENAI_API_VERSION", effective.openai.api_version or "")
    env.setdefault("AZURE_DOCUMENT_INTELLIGENCE_MODEL", effective.document_intelligence.model_id or "")
    with card("azure"):
        st.markdown("#### :material/cloud: Azure connection")
        st.caption(
            f"Saved in `{env_path()}`. Keys are only shown as their last 4 characters; leave a key field empty to "
            "keep the current key."
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
            notify(f"Saved {len(changed)} change(s)." if changed else "Nothing changed.", ":material/save:")
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
                ui.pill(f"{fails} problem(s)", "err", "error")
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
            min_value=0.50, max_value=0.99, step=0.01, value=float(settings.engine.review_threshold),
            format="%.2f",
            help="Invoices with any error always need attention. Higher = more invoices get a closer look.",
        )  # fmt: skip
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
        if st.form_submit_button("Save", type="primary", icon=":material/save:"):
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
            notify(f"Saved {len(changed)} change(s)." if changed else "Nothing changed.", ":material/save:")
            st.rerun()
    st.caption(
        "The confidence threshold applies to invoices processed from now on; invoices already in the queue keep "
        "their flag."
    )
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
        if st.form_submit_button("Save", type="primary", icon=":material/save:"):
            changed = []
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
        head.html(
            f"<div class='apc-muted'>Database, invoices, outputs, backups and Azure settings: "
            f"<code>{esc(data)}</code></div>"
        )
        if button.button("Open folder", icon=":material/folder_open:", key="open_data", width="stretch"):
            if not open_folder(data):
                st.info(f"Open this folder yourself: {data}")
        invoices = store.list_invoices()
        files = len(list(INVOICE_DIR.glob("*"))) if INVOICE_DIR.exists() else 0
        st.html(
            ui.tiles(
                [
                    ui.tile("Database", _size(DB_PATH), "database", "blue", "ap_coder.db"),
                    ui.tile("Invoices", len(invoices), "receipt_long", "green", f"{files} file(s) in invoices/"),
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
                if folder and not Path(folder).expanduser().is_dir():
                    st.error("That folder does not exist (or is not reachable from this computer).")
                else:
                    store.set_setting("backup_copy_dir", folder, actor=reviewer())
                    store.log_event("settings_changed", actor=reviewer(), detail={"keys": ["backup_copy_dir"]})
                    if folder:
                        store.backup_now()  # a first copy straight away (backup_now copies it)
                    notify("Backups will be copied there too." if folder else "Backups are kept on this computer only.",
                           ":material/backup:")  # fmt: skip
                    st.rerun()
        status = store.get_setting("backup_copy_status")
        if store.get_setting("backup_copy_dir") and status:
            ok = status.startswith("ok")
            st.caption(
                (":material/check_circle: Last copy " if ok else ":material/error: Last copy ")
                + md(status.split(" ", 1)[1] if " " in status else status)
            )
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
        chosen = st.selectbox("Backup", [b.name for b in backups], key="backup_choice")
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
        st.markdown(
            "**Updating:** close AP Coder and double-click `install.bat` again. With git it downloads the new "
            "version (without git, extract the new ZIP over the same folder first); your data, settings and "
            "shortcut are kept.\n\n"
            "**Help:** see `docs/GETTING_STARTED.md` in the code folder."
        )


def page_settings() -> None:
    store = get_store()
    show_toast()
    st.html(ui.page_header("Setup", "Settings", "Azure connection, review behaviour, your data and backups."))
    azure, review, data, about = st.tabs(
        [
            ":material/cloud: Azure",
            ":material/tune: Review",
            ":material/database: Data & backups",
            ":material/info: About",
        ]
    )
    with azure:
        azure_tab(store)
    with review:
        review_tab()
    with data:
        data_tab(store)
    with about:
        about_tab()
