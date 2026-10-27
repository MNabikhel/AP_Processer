"""The scripts/ the tests load as modules (the launcher, the updater, the installer...): scripts/ is no package."""

from __future__ import annotations

import importlib.util
import sys
from types import ModuleType

from .conftest import ROOT


def load_script(name: str, as_name: str) -> ModuleType:
    """A fresh copy of scripts/<name>.py, imported as ``as_name`` and kept in ``sys.modules`` under it (its
    dataclasses look themselves up there). Each call runs the script again: a test may change its globals freely."""
    spec = importlib.util.spec_from_file_location(as_name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module
