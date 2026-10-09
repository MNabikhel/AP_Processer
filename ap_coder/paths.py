"""Where AP Coder keeps your data and settings.

Enterprise data (database, invoices, outputs) and the ``.env`` with Azure keys live in one
*data folder* that is separate from the code, so updating or re-downloading the code never
loses or duplicates them. The installer records that folder in a small per-user settings file
(``~/.ap_coder/settings.json``); everything else reads it from here.

Data folder, first match wins:

1. ``AP_PRIVATE_DIR`` environment variable
2. the folder recorded by the installer
3. ``private/`` inside the project, when running from a checkout
4. ``./private``
"""

from __future__ import annotations

import json
import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent


def user_settings_path() -> Path:
    return Path(os.environ.get("AP_USER_SETTINGS") or Path.home() / ".ap_coder" / "settings.json")


def read_user_settings() -> dict:
    try:
        return json.loads(user_settings_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def write_user_settings(**values: str) -> Path:
    path = user_settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    settings = {**read_user_settings(), **values}
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return path


def _env_path(name: str) -> Path | None:
    """A folder or file from an environment variable. Windows cmd's ``set NAME="C:\\My Folder"`` keeps the
    quotes in the value: they are not part of the path."""
    value = (os.environ.get(name) or "").strip().strip('"').strip()
    return Path(value) if value else None


def private_dir() -> Path:
    """The folder for enterprise data (see the module docstring for the lookup order)."""
    if folder := _env_path("AP_PRIVATE_DIR"):
        return folder
    recorded = read_user_settings().get("data_dir")
    if recorded:
        return Path(recorded)
    if (PROJECT_DIR / "pyproject.toml").exists() and (PROJECT_DIR / "ap_coder").is_dir():
        return PROJECT_DIR / "private"
    return Path("private")


def default_db_path() -> Path:
    return private_dir() / "ap_coder.db"


def env_file() -> Path | None:
    """The ``.env`` with Azure settings: ``AP_ENV_FILE``, else the data folder's, else the folder
    the command runs from, else the project folder's."""
    if chosen := _env_path("AP_ENV_FILE"):
        return chosen
    for candidate in (private_dir() / ".env", Path.cwd() / ".env", PROJECT_DIR / ".env"):
        if candidate.is_file():
            return candidate
    return None
