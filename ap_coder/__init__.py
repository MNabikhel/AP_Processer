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


# The old flat module paths (``ap_coder.store``...), from before the modules moved into folders (D-33), still import:
# each is the one real module under its old name too, so old imports, outside scripts and monkeypatching through an
# old path all reach it. A table here rather than a file per old path (D-67); first on ``sys.meta_path``, so a stale
# flat file an older install left behind is never imported in its place.
_MOVED = {
    name: package
    for package, names in {
        "core": "audit config csvio envfile memory paths reference_data safe schema store ui",
        "ai": "azure_check evaluation extraction figures history imaging inference labels lessons line_match "
        "local_llm offline_coder page_reader page_worker pipeline prompts reading suggest training_export verifier",
        "coding": "bulk po review rules tax taxmath taxreturn terms validation vendor_mail vendors",
        "erp": "accruals controls directory dupaudit export_layout exports insights jde jde_worksheet recurring "
        "registers search spend stamp statements",
        "intake": "inbox mailbox msgfile triage",
        "ops": "cli demo doctor help offline share_report",
    }.items()
    for name in names.split()
}


class _MovedModules:
    """Finds ``ap_coder.<old name>`` and loads it as the moved module itself (no second copy)."""

    @staticmethod
    def find_spec(fullname, path=None, target=None):
        package, _, name = fullname.partition(".")
        if package != __name__ or name not in _MOVED:
            return None
        import importlib.util

        return importlib.util.spec_from_loader(fullname, _MovedModules)

    @staticmethod
    def create_module(spec):
        import importlib

        name = spec.name.partition(".")[2]
        module = importlib.import_module(f"{__name__}.{_MOVED[name]}.{name}")
        spec.loader_state = module.__spec__  # the import system sets the old name's spec on it next (B-724)
        return module

    @staticmethod
    def exec_module(module):
        # The moved module ran when it was imported: only its own spec goes back, so its relative imports and
        # importlib.reload still follow its real name.
        module.__spec__ = module.__spec__.loader_state


def _install_moved_modules() -> None:
    import sys

    if not any(getattr(f, "__name__", "") == "_MovedModules" for f in sys.meta_path):
        sys.meta_path.insert(0, _MovedModules)


_install_moved_modules()
