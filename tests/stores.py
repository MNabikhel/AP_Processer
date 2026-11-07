"""The test files' databases, written once: a file whose tests all start from one of these imports it (``from
tests.stores import demo_store  # noqa: F401``) and its tests' ``store`` is that one (pytest takes a fixture imported
into a test file as the file's own; tests/conftest.py's ``client`` then serves it too). Where the dashboard keeps its
database, ``tmp_path / "private" / "ap_coder.db``, as tests/conftest.py's plain ``store``."""

from __future__ import annotations

import pytest

from ap_coder.core.store import Store, load_sample_setup
from tests.conftest import copy_database


@pytest.fixture(name="store")
def sample_setup_store(tmp_path) -> Store:
    """The sample setup (GL accounts, cost centers, tax treatments: ``load_sample_setup``), no invoices."""
    store = Store(tmp_path / "private" / "ap_coder.db")
    load_sample_setup(store)
    return store


@pytest.fixture(name="store")
def demo_store(demo_db, tmp_path) -> Store:
    """A copy of the demo's database (made once a run: ``demo_db``, which a test file may make the cost centers'
    demo)."""
    return copy_database(demo_db, tmp_path / "private" / "ap_coder.db")


@pytest.fixture(name="store")
def cost_centers_demo_store(cost_centers_demo_template, tmp_path) -> Store:
    """A copy of the database with the sample cost centers, then the demo (made once a run)."""
    return copy_database(cost_centers_demo_template, tmp_path / "private" / "ap_coder.db")
