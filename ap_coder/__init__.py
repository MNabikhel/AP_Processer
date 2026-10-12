"""AP Coder: reads vendor invoices on this computer (PDF text, local OCR) and codes them to GL accounts, with
Canadian sales tax and a review dashboard. Offline by default.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

# Libraries that report to their maker over the internet unless told not to before they start. ONNX Runtime (the OCR
# and table-finder engine) uploads usage events to Microsoft (mobile.events.data.microsoft.com) from its own native
# threads on Linux, Macs and Windows. Set here, first, so every process of AP Coder and every one it starts (the
# self-check, the server, its page workers) inherits it: an offline build sends nothing, whatever was set before.
os.environ["ORT_DISABLE_TELEMETRY"] = "1"


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
