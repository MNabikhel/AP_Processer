"""Puts tests/offline_guard.py's network guard in every Python process started with this folder on PYTHONPATH (the
offline proof's servers and pip runs): it reports to the file named in AP_OFFLINE_GUARD_LOG."""

import importlib.util
import os
import sys
from pathlib import Path

if os.environ.get("AP_OFFLINE_GUARD_LOG"):
    _path = Path(__file__).resolve().parent.parent / "offline_guard.py"
    _spec = importlib.util.spec_from_file_location("ap_offline_guard", _path)
    _guard = importlib.util.module_from_spec(_spec)
    sys.modules["ap_offline_guard"] = _guard
    _spec.loader.exec_module(_guard)
    _guard.install()
