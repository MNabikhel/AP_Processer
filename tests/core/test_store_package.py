"""The store is a package of parts (D-69); everything the one module had is still there, on the same names."""

from pathlib import Path

import ap_coder.core.store as store

API = [line for line in (Path(__file__).parents[1] / "fixtures" / "store_api.txt").read_text().splitlines()
       if line and not line.startswith("#")]  # fmt: skip


def test_every_name_and_method_of_the_old_module_is_still_there():
    missing = [n for n in API if not (hasattr(store.Store, n[6:]) if n.startswith("Store.") else hasattr(store, n))]
    assert not missing, missing


def test_each_part_is_small_enough_to_read_in_one_go():
    parts = Path(store.__file__).parent.glob("*.py")
    assert all(len(p.read_text(encoding="utf-8").splitlines()) < 1000 for p in parts)


def test_no_two_parts_define_the_same_method():
    """Two parts that both define a name: the later one's silently replaces the other's on ``Store`` (two parts each
    wrote a ``_write_vouchers`` of their own; one part's callers got the other's)."""
    meant = {"_migrate", "_conn"}  # SummaryMixin wraps the core's on purpose
    owners: dict[str, list[str]] = {}
    for part in store.Store.__mro__:
        if part is store.Store or not part.__module__.startswith(store.__name__):
            continue
        for name, value in vars(part).items():
            if name.startswith("__") and name.endswith("__"):
                continue
            if callable(value) or isinstance(value, property | staticmethod | classmethod):
                owners.setdefault(name, []).append(part.__name__)
    twice = {name: parts for name, parts in owners.items() if len(parts) > 1 and name not in meant}
    assert not twice, twice
