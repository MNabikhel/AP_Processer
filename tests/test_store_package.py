"""The store is a package of parts (D-69); everything the one module had is still there, on the same names."""

from pathlib import Path

import ap_coder.core.store as store

API = [line for line in (Path(__file__).parent / "fixtures" / "store_api.txt").read_text().splitlines()
       if line and not line.startswith("#")]  # fmt: skip


def test_every_name_and_method_of_the_old_module_is_still_there():
    missing = [n for n in API if not (hasattr(store.Store, n[6:]) if n.startswith("Store.") else hasattr(store, n))]
    assert not missing, missing


def test_each_part_is_small_enough_to_read_in_one_go():
    parts = Path(store.__file__).parent.glob("*.py")
    assert all(len(p.read_text(encoding="utf-8").splitlines()) < 1000 for p in parts)
