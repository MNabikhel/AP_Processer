import functools
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ap_coder.core.config import Settings
from ap_coder.core.reference_data import load_reference_data


def _import_the_pages() -> None:
    """Every page module, imported once before any test (B-581). ``create_app`` imports them the first time it runs;
    inside a test whose ``me`` fixture had patched ``web.core.reviewer``, ``from ap_coder.web.core import reviewer``
    would copy the patched one into the page for the rest of the run."""
    import importlib

    from ap_coder.web.pages import MORE_MODULES, PAGES

    for name in {*(p.module for p in PAGES), *MORE_MODULES}:
        importlib.import_module(f"ap_coder.web.pages.{name}")


_import_the_pages()

ROOT = Path(__file__).resolve().parent.parent
_SLOW = {
    line.strip()
    for line in (Path(__file__).parent / "slow_tests.txt").read_text(encoding="utf-8").splitlines()
    if line.strip() and not line.startswith("#")
}


def pytest_addoption(parser):
    parser.addoption("--shard", default="", metavar="K/N", help="run only the K-th of N groups of test files (CI)")


def pytest_collection_modifyitems(config, items):
    """The slow tests (a browser, or over half a second: tests/slow_tests.txt) are marked ``slow``: the default run
    leaves them out (pyproject.toml), ``python -m pytest -m ""`` and CI run everything. ``--shard K/N`` keeps the
    K-th of N groups of whole test files (see :func:`_shard`)."""
    for item in items:
        base = item.nodeid.split("[", 1)[0]
        if base in _SLOW or "in_a_browser" in item.name:
            item.add_marker(pytest.mark.slow)
    if config.getoption("shard"):
        keep = _shard(items, config.getoption("shard"))
        config.hook.pytest_deselected(items=[item for item in items if item not in keep])
        items[:] = [item for item in items if item in keep]


def _shard(items, spec: str) -> set:
    """CI splits the slow Windows run over parallel jobs: whole files (their fixtures set up once) go to N groups of
    about equal time, the heaviest first into the lightest group. A file's time is the one measured in
    tests/test_times.json (a whole run on Linux; refresh it with ``--junitxml`` when the split grows uneven), else its
    slow tests at ``SLOW_SECONDS`` each plus the rest at ``FAST_SECONDS``. Rough, but every test lands in exactly one
    group."""
    k, n = (int(part) for part in spec.split("/"))
    if not 1 <= k <= n:
        raise pytest.UsageError(f"--shard {spec}: K must be 1 to N")
    weight: dict[str, float] = {}
    for item in items:
        path = item.nodeid.split("::", 1)[0]
        slow = item.get_closest_marker("slow") is not None
        weight[path] = weight.get(path, 0.0) + (SLOW_SECONDS if slow else FAST_SECONDS)
    weight.update({path: seconds for path, seconds in _TIMES.items() if path in weight})
    groups = [0.0] * n
    mine = set()
    for path in sorted(weight, key=lambda p: (-weight[p], p)):
        group = groups.index(min(groups))
        groups[group] += weight[path]
        if group == k - 1:
            mine.add(path)
    return {item for item in items if item.nodeid.split("::", 1)[0] in mine}


SLOW_SECONDS, FAST_SECONDS = 2.0, 0.05  # rough per-test times for _shard (tests/slow_tests.txt: over 0.5 s)
_TIMES_FILE = Path(__file__).parent / "test_times.json"
_TIMES = json.loads(_TIMES_FILE.read_text(encoding="utf-8")) if _TIMES_FILE.is_file() else {}


DATA = ROOT / "data"
SAMPLES = ROOT / "samples"
SAMPLE_STEM = "northwind_ON_HST_NW-2026-0912"


@pytest.fixture(autouse=True)
def isolated_private_dir(monkeypatch, tmp_path):
    """Never let a test touch the real private/ folder (database, invoices, outputs)."""
    monkeypatch.setenv("AP_PRIVATE_DIR", str(tmp_path / "private"))
    monkeypatch.setenv("AP_USER_SETTINGS", str(tmp_path / "user_settings.json"))  # not the real ~/.ap_coder
    monkeypatch.delenv("AP_ALLOW_INTERNET", raising=False)  # the offline default, even in an online CI job
    monkeypatch.delenv("AP_LAUNCH_LOCK_HELD", raising=False)


@pytest.fixture(autouse=True)
def fresh_reading_memory():
    """The tables found per file and the learning memory are remembered for the life of the process: each test
    starts with none."""
    from ap_coder.capture import layout, table_finder
    from ap_coder.core import store

    table_finder._found.clear()
    layout._pruned.clear()
    layout._SCAN_PNGS.clear()
    store._FEEDBACK.clear()
    yield
    table_finder._found.clear()
    store._FEEDBACK.clear()


@pytest.fixture(autouse=True)
def no_dashboard_page_reader(monkeypatch):
    """The dashboard's own page-reader thread runs for the life of the process: started by a test that opens the
    dashboard, it would run on in later tests and act on their fakes (a self-test counted twice). Tests that need a
    reader thread start their own BackgroundReader and stop it."""
    from ap_coder.ai import page_worker

    monkeypatch.setattr(page_worker, "start_background", lambda *args, **kwargs: None)
    from ap_coder.ai import azure_check  # the Azure check's thread likewise (it is off unless a test turns it on)

    monkeypatch.setattr(azure_check, "start_background", lambda *args, **kwargs: None)


@pytest.fixture(autouse=True)
def no_local_model(monkeypatch):
    """Never ask a real LM Studio on this computer: every test sees no local model unless it starts a fake one
    (tests/test_local_llm.py restores the real fetch for that)."""
    from ap_coder.ai import local_llm

    def refused(url, api_key, timeout):
        raise ConnectionRefusedError(f"no local model in tests ({url})")

    monkeypatch.setattr(local_llm, "_fetch_json", refused)
    local_llm.forget_status()
    local_llm._mode_by_model.clear()
    yield
    local_llm.forget_status()


@pytest.fixture
def offline_guard():
    """This computer with no network: any connection or name lookup but 127.0.0.1 / ::1 / localhost is refused and
    recorded in ``offline_guard.attempts`` (tests/offline_guard.py; ``subprocess_env`` puts it in a subprocess)."""
    from . import offline_guard as guard

    guard.attempts.clear()
    guard.install()
    try:
        yield guard
    finally:
        guard.uninstall()
        guard.attempts.clear()


@pytest.fixture
def reference():
    return load_reference_data(
        DATA / "chart_of_accounts.csv",
        DATA / "cost_centers.csv",
        DATA / "tax_gl_mapping.csv",
        DATA / "coding_policy.md",
    )


@pytest.fixture
def settings():
    return Settings()


@pytest.fixture
def ground_truth():
    return json.loads((SAMPLES / "ground_truth" / f"{SAMPLE_STEM}.json").read_text())


@pytest.fixture
def sample_markdown_path():
    return SAMPLES / f"{SAMPLE_STEM}.md"


def make_completion(content, finish_reason="stop", refusal=None, model="gpt-4o-2024-11-20"):
    return SimpleNamespace(
        model=model,
        choices=[
            SimpleNamespace(
                finish_reason=finish_reason,
                message=SimpleNamespace(content=content, refusal=refusal),
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=1000,
            completion_tokens=200,
            total_tokens=1200,
            prompt_tokens_details=SimpleNamespace(cached_tokens=512),
        ),
    )


class FakeOpenAI:
    """Records chat.completions.create calls and replays queued responses."""

    def __init__(self, *responses):
        self.calls = []
        self._responses = list(responses)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responses.pop(0)


class FakeDIClient:
    def __init__(self, raw):
        self.raw = raw
        self.calls = []

    def begin_analyze_document(self, model_id, body, **kwargs):
        self.calls.append({"model_id": model_id, "body": body, **kwargs})
        raw = self.raw
        return SimpleNamespace(result=lambda: SimpleNamespace(as_dict=lambda: raw))


class _FewVariables:
    """A connection that refuses a statement with more than ``limit`` bound variables, as an older SQLite build does
    (Python 3.10 has no ``Connection.setlimit`` to lower the real limit)."""

    def __init__(self, conn, limit):
        self._conn, self._limit = conn, limit

    def _check(self, sql, params):
        import sqlite3

        count = len(params) if isinstance(params, (list, tuple)) else len(params or {})
        if sql.count("?") > self._limit or count > self._limit:
            raise sqlite3.OperationalError("too many SQL variables")

    def execute(self, sql, params=()):
        self._check(sql, params)
        return self._conn.execute(sql, params)

    def executemany(self, sql, seq):
        rows = list(seq)
        for params in rows:
            self._check(sql, params)
        return self._conn.executemany(sql, rows)

    def __enter__(self):
        self._conn.__enter__()
        return self

    def __exit__(self, *exc):
        return self._conn.__exit__(*exc)

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def __setattr__(self, name, value):
        if name in ("_conn", "_limit"):
            object.__setattr__(self, name, value)
        else:
            setattr(self._conn, name, value)


@pytest.fixture
def few_sql_variables(monkeypatch):
    """Every Store connection takes at most 999 bound variables (an older SQLite build's limit)."""
    import sqlite3

    from ap_coder.core.store import Store

    opened = Store._open

    def low_limit(self):
        conn = opened(self)
        if hasattr(conn, "setlimit"):  # Python 3.11+: the real limit
            conn.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 999)
            return conn
        return _FewVariables(conn, 999)

    monkeypatch.setattr(Store, "_open", low_limit)


BROWSER_ROOTS = ["/opt/pw-browsers"]  # a Chromium installed outside Playwright's own folder (this cloud image)


@functools.cache
def chromium_path() -> str | None:
    """A Chromium for the browser tests, found once per run (they call this inside ``with sync_playwright()``, where
    a second Playwright can't start: B-127): one under PLAYWRIGHT_BROWSERS_PATH or /opt/pw-browsers, else the one
    Playwright installed (``python -m playwright install chromium``). None when there is none: the browser tests
    skip, unless ``AP_REQUIRE_BROWSER=1`` (CI's browser job), where they fail so they can't pass by not running."""
    import importlib.util
    import os

    found = None
    if importlib.util.find_spec("playwright") is not None:
        for root in (os.environ.get("PLAYWRIGHT_BROWSERS_PATH"), *BROWSER_ROOTS):
            for pattern in ("chromium-*/chrome-linux*/chrome", "chromium/chrome-linux*/chrome"):
                hits = sorted(Path(root).glob(pattern)) if root else []
                if hits and not found:
                    found = str(hits[-1])
        if not found:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                path = p.chromium.executable_path
            found = path if path and Path(path).is_file() else None
    if found is None and os.environ.get("AP_REQUIRE_BROWSER") == "1":
        pytest.fail("AP_REQUIRE_BROWSER=1 but no Chromium for Playwright was found")
    return found
