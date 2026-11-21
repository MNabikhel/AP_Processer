"""The function-level test map (``tests/test_map.py``): how it is made from what coverage recorded, read back, and
told when the code has moved on from it."""

from __future__ import annotations

import gzip
import json

from tests import test_map as tm
from tests.conftest import ROOT

TAXMATH, INVOICES = "ap_coder/coding/taxmath.py", "ap_coder/core/store/invoices.py"
A, B, C = "tests/coding/test_line_tax.py", "tests/core/test_store_session.py", "tests/web/test_web_invoice.py"


def _body_line(path: str, name: str) -> int:
    return tm.functions((ROOT / path).read_text(encoding="utf-8"))[name][0]


def test_each_function_gets_the_test_files_that_ran_one_of_its_lines(tmp_path):
    """Lines a context ran count for the function that holds them, by the test file the context names (ours, or
    pytest-cov's node ids); a line outside every function, a file outside the app and a context of no test count for
    none. The same map gives the same bytes."""
    taxes = _body_line(TAXMATH, "invoice_taxes")
    recorded = {
        TAXMATH: {1: [A], taxes: [A, f"{C}::test_x|run", ""]},
        INVOICES: {_body_line(INVOICES, "InvoicesMixin.final_approve"): [B]},
        "scripts/check.py": {10: ["tests/meta/test_check_script.py"]},
    }  # fmt: skip
    made = tm.build(recorded)
    assert made["tests"] == [A, B, C]
    assert made["map"] == {f"{TAXMATH}::invoice_taxes": [0, 2], f"{INVOICES}::InvoicesMixin.final_approve": [1]}
    assert tm.write(made, tmp_path / "a.json.gz") == tm.write(tm.build(recorded), tmp_path / "b.json.gz")
    assert (tmp_path / "a.json.gz").read_bytes() == (tmp_path / "b.json.gz").read_bytes()
    assert json.loads(gzip.decompress((tmp_path / "a.json.gz").read_bytes()))["format"] == tm.FORMAT


def test_the_test_files_of_some_functions_and_which_of_them_the_map_has(tmp_path):
    """``tests_for``: the files of the keys together, and for each key whether the map has it; a store method asked
    as ``Store.func`` is its part's class's. No map: no file, and no key in it."""
    made = tm.build({TAXMATH: {_body_line(TAXMATH, "invoice_taxes"): [A, "tests/coding/test_gone.py"]},
                     INVOICES: {_body_line(INVOICES, "InvoicesMixin.final_approve"): [B]}})  # fmt: skip
    tm.write(made, tmp_path / "map.json.gz")
    test_map = tm.load(tmp_path / "map.json.gz")
    never = "ap_coder/coding/po.py::template_csv"
    assert tm.tests_for([f"{TAXMATH}::invoice_taxes", never], test_map) == (
        [A],
        {f"{TAXMATH}::invoice_taxes": True, never: False},
    )  # fmt: skip  (a test file deleted since: left out)
    assert tm.tests_for([f"{INVOICES}::Store.final_approve"], test_map)[0] == [B]
    assert tm.load(tmp_path / "none.json.gz") is None
    assert tm.tests_for([never], tm.load(tmp_path / "none.json.gz")) == ([], {never: False})


def test_the_lines_of_a_change_name_the_functions_they_are_in():
    first, last = tm.functions((ROOT / TAXMATH).read_text(encoding="utf-8"))["invoice_taxes"]
    assert tm.keys_for_lines(TAXMATH, [first, last + 10_000]) == {f"{TAXMATH}::invoice_taxes"}
    assert tm.keys_for_lines(TAXMATH, [1]) == set()  # the module's docstring


def test_a_key_whose_function_is_gone_is_said_stale_and_the_maps_keys_are_the_codes_functions_or_stale(tmp_path):
    """A key of a function renamed, moved or deleted since the map was made, or of a file gone, is stale; the
    others are functions of the code. The map in the repository (once there is one) reads, and each of its keys is a
    function of the code or one ``stale`` names (a test file since deleted is left out of what ``tests_for`` gives)."""
    made = {"format": tm.FORMAT, "tests": [A],
            "map": {f"{TAXMATH}::invoice_taxes": [0], f"{TAXMATH}::invoice_taxes_gone": [0],
                    "ap_coder/gone.py::f": [0]}}  # fmt: skip
    tm.write(made, tmp_path / "map.json.gz")
    assert tm.stale(tm.load(tmp_path / "map.json.gz")) == [f"{TAXMATH}::invoice_taxes_gone", "ap_coder/gone.py::f"]
    kept = tm.load()
    if kept is None:
        assert not tm.MAP_FILE.exists()
        return
    gone = set(tm.stale(kept))
    assert all(0 <= i < len(kept.tests) for found in kept.keys.values() for i in found)
    for key in kept.keys:
        path, _, name = key.partition("::")
        assert key in gone or name in tm.functions((ROOT / path).read_text(encoding="utf-8")), key
