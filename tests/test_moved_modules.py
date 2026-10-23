"""The old flat module paths (``ap_coder.store``...) still import, as the moved module itself (D-33, D-67)."""

import importlib
import sys
from pathlib import Path
from unittest import mock

import pytest

import ap_coder


@pytest.mark.parametrize("name", sorted(ap_coder._MOVED))
def test_every_old_path_is_the_moved_module(name):
    real = importlib.import_module(f"ap_coder.{ap_coder._MOVED[name]}.{name}")
    assert importlib.import_module(f"ap_coder.{name}") is real
    assert getattr(ap_coder, name) is real


def test_patching_through_an_old_path_reaches_the_real_module():
    from ap_coder.core import store

    with mock.patch("ap_coder.store.load_sample_setup") as fake:
        assert store.load_sample_setup is fake


def test_a_stale_flat_file_left_by_an_older_install_is_never_imported(monkeypatch):
    stale = Path(ap_coder.__file__).parent / "zz_stale_moved.py"
    stale.write_text("raise RuntimeError('the stale copy ran')\n", encoding="utf-8")
    try:
        monkeypatch.setitem(ap_coder._MOVED, "zz_stale_moved", "core")
        monkeypatch.setitem(sys.modules, "ap_coder.core.zz_stale_moved", sys.modules["ap_coder.core.audit"])
        sys.modules.pop("ap_coder.zz_stale_moved", None)
        assert importlib.import_module("ap_coder.zz_stale_moved") is sys.modules["ap_coder.core.audit"]
    finally:
        stale.unlink()
        sys.modules.pop("ap_coder.zz_stale_moved", None)
        if hasattr(ap_coder, "zz_stale_moved"):
            delattr(ap_coder, "zz_stale_moved")


def test_the_moved_module_keeps_its_own_spec_and_still_reloads(monkeypatch):
    """B-724: an old path imported first must not rename the real module (its relative imports, and reload, follow
    its spec)."""
    import warnings

    from ap_coder.coding import terms

    sys.modules.pop("ap_coder.terms", None)
    importlib.import_module("ap_coder.terms")
    assert terms.__spec__.name == "ap_coder.coding.terms" and terms.__spec__.parent == "ap_coder.coding"
    assert terms.__name__ == "ap_coder.coding.terms"
    monkeypatch.delattr(terms, "payment_findings")
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        importlib.reload(terms)
    assert callable(terms.payment_findings)


def test_an_unknown_name_is_still_missing():
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("ap_coder.no_such_module")
