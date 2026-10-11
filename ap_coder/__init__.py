"""Enterprise AP Invoice Coder Engine (PoC).

Chains Azure AI Document Intelligence (layout/table extraction) with Azure OpenAI
(Structured Outputs) to turn raw vendor invoices into GL-coded JSON.
"""

from __future__ import annotations

import re
from pathlib import Path


def _version() -> str:
    """The version in pyproject.toml, the one place it is written: read from the pyproject.toml next to this folder
    (AP Coder run from its code folder, as the launcher does: an older installed copy's metadata may be stale), else
    the installed package's metadata (``pip install``), else "0+unknown"."""
    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    try:
        text = pyproject.read_text(encoding="utf-8")
    except OSError:
        text = ""
    section = re.search(r"(?ms)^\[project\][ \t]*$(.*?)(?=^\[|\Z)", text)  # up to the next [table] header
    found = re.search(r'(?m)^version\s*=\s*"([^"]+)"', section.group(1)) if section else None
    if found:
        return found.group(1)
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("ap-coder")
    except (ImportError, PackageNotFoundError):
        return "0+unknown"


__version__ = _version()
