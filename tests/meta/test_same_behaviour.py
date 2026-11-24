"""``scripts/same_behaviour.py`` (D-124): its probes give the same output every run (else every comparison would
differ), and a difference is shown where it is."""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.scripts import load_script

ROOT = Path(__file__).resolve().parents[2]


def _tool():
    spec = importlib.util.spec_from_file_location("same_behaviour", ROOT / "scripts" / "same_behaviour.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_difference_is_shown_by_its_line_and_key_path():
    tool = _tool()
    before = b'{"a": 1, "b": {"c": [1, 2]}}\n{"x": 0}\n'
    after = b'{"a": 1, "b": {"c": [1, 3]}}\n{"x": 0}\n'
    assert tool.first_difference(before, after) == "line 1: /b/c[1]: '2' before, '3' after"
    assert tool.first_difference(b"1\n", b"1\n2\n") == "1 lines before, 2 after"
    assert set(tool.DEFAULT) <= set(tool.DEFAULT_N) == {p.stem for p in (ROOT / "scripts" / "probes").glob("*.py")}


def test_the_tax_probe_gives_the_same_output_each_run(tmp_path):
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONHASHSEED": "0"}
    runs = [subprocess.run([sys.executable, str(ROOT / "scripts" / "probes" / "tax.py"), "300", str(tmp_path / str(i))],
                           env=env, capture_output=True, check=True).stdout for i in range(2)]  # fmt: skip
    assert runs[0] == runs[1] and runs[0].count(b"\n") == 300


@pytest.mark.slow
@pytest.mark.parametrize(("probe", "n", "lines"), [("jde", 3, 150), ("pages", 2, 50)])
def test_the_jde_and_pages_probes_give_the_same_output_each_run(tmp_path, probe, n, lines):
    """D-331: two runs a second apart, side by side in work folders of their own (as ``same_behaviour.py`` runs the two
    trees), give the same bytes: the clock's times, the work folder and the review screen's token are left out."""
    env = {**os.environ, "PYTHONPATH": str(ROOT), "PYTHONHASHSEED": "0"}
    runs = []
    for i in range(2):
        runs.append(subprocess.Popen([sys.executable, str(ROOT / "scripts" / "probes" / f"{probe}.py"), str(n),
                                      str(tmp_path / str(i))], env=env, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE))  # fmt: skip
        time.sleep(1)
    (first, err), (second, _) = (run.communicate() for run in runs)
    assert all(run.returncode == 0 for run in runs), err[-3000:]
    assert first == second and first.count(b"\n") > lines


def _probe(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_probe", ROOT / "scripts" / "probes" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_jde_probe_leaves_out_only_the_times_the_clock_writes(capsys):
    """The clock's times (a PO's ``updated_at``) are left out; a date the books give, a keying sheet's cell among
    them, is printed as it is, so a wrong or swapped date says DIFFERENT."""
    import datetime as dt

    probe = _probe("jde")
    cells = {"Keying sheet": [["Invoice date", dt.datetime(2026, 10, 24)]]}
    probe.emit("x", po={"updated_at": "2026-11-21T03:29:33"}, sheet=cells, gl_date=dt.date(2026, 10, 20))
    said = capsys.readouterr().out
    assert '"updated_at": "(time)"' in said
    assert "2026-10-24" in said and "2026-10-20" in said


def _scratch_tree(to: Path) -> Path:
    import shutil

    for part in ("ap_coder", "data", "samples"):
        shutil.copytree(ROOT / part, to / part, ignore=shutil.ignore_patterns("__pycache__"))
    return to


@pytest.mark.slow
def test_the_jde_probe_says_different_for_a_keying_sheet_with_two_dates_swapped(tmp_path):
    """B-3010: the G/L date and the invoice date swapped on the keying sheet (what the clerk types) gave the same
    bytes, every date cell having been left out as a time of the clock."""
    tree = _scratch_tree(tmp_path / "tree")
    keying = tree / "ap_coder" / "erp" / "jde_keying.py"
    text = keying.read_text(encoding="utf-8")
    right = '[("Invoice date", LABEL), (v.invoice_date, DATE), ("G/L date", LABEL), (v.gl_date, DATE)]'
    assert text.count(right) == 1
    keying.write_text(text.replace(right, right.replace("v.invoice_date", "v.X").replace("v.gl_date", "v.invoice_date")
                                   .replace("v.X", "v.gl_date")), encoding="utf-8")  # fmt: skip
    runs = [subprocess.Popen([sys.executable, str(ROOT / "scripts" / "probes" / "jde.py"), "0", str(tmp_path / name)],
                             env={**os.environ, "PYTHONPATH": str(base), "PYTHONHASHSEED": "0"},
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for name, base in (("before", ROOT), ("after", tree))]  # fmt: skip
    (before, err), (after, _) = (run.communicate() for run in runs)
    assert all(run.returncode == 0 for run in runs), err[-3000:]
    assert before != after
    assert "/sheet/Keying sheet" in _tool().first_difference(before, after)


@pytest.mark.slow
def test_the_pages_probe_prints_its_own_day_whatever_the_day_it_runs(tmp_path):
    """B-3016: the pages printed the day the probe ran (the activity's range, "in the review queue since", a due
    date's days ago), so two runs a second apart on either side of midnight differed. The probe's clock now reads a
    fixed day; only its seconds go on. B-3041: this test took today's date anywhere in the output for the clock's, and
    failed on 24 November 2026, the due date of one of the made-up company's invoices. Two runs side by side on either
    side of midnight now (12 hours behind UTC and 14 ahead: never the same day) give the same bytes, the fixed day in
    them."""
    zones = ("XXX+12", "XXX-14")  # (POSIX time zones: the hours behind UTC)
    today = [subprocess.run([sys.executable, "-c", "import datetime; print(datetime.date.today())"],
                            env={**os.environ, "TZ": zone}, capture_output=True, check=True).stdout
             for zone in zones]  # fmt: skip
    if today[0] == today[1]:
        pytest.skip("this computer's clock does not follow TZ")
    probe = str(ROOT / "scripts" / "probes" / "pages.py")
    runs = [subprocess.Popen([sys.executable, probe, "1", str(tmp_path / str(i))],
                             env={**os.environ, "PYTHONPATH": str(ROOT), "PYTHONHASHSEED": "0", "TZ": zone},
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for i, zone in enumerate(zones)]  # fmt: skip
    (first, err), (second, _) = (run.communicate() for run in runs)
    assert all(run.returncode == 0 for run in runs), err[-3000:]
    assert first == second, _tool().first_difference(first, second)
    assert _probe("pages").DAY.isoformat().encode() in first


@pytest.mark.slow
def test_the_pages_probe_says_different_for_an_app_an_import_cycle_stops_from_starting(tmp_path):
    """B-3028: the probe imported every module of the app in its folders' order before making the app, to give
    them its clock, so a cycle that only that order resolves (a page importing a module that imports the page) gave
    the same bytes as the unchanged tree, though the app could not start. The app is made as it starts first."""
    tree = _scratch_tree(tmp_path / "tree")
    rules = tree / "ap_coder" / "web" / "pages" / "rules.py"
    future = "from __future__ import annotations\n"
    text = rules.read_text(encoding="utf-8")
    assert text.count(future) == 1
    rules.write_text(text.replace(future, future + "from ap_coder.coding import cycle_probe  # noqa: F401\n"),
                     encoding="utf-8")  # fmt: skip
    (tree / "ap_coder" / "coding" / "cycle_probe.py").write_text(
        "from ap_coder.web.pages.rules import router\n", encoding="utf-8"
    )
    runs = [subprocess.Popen([sys.executable, str(ROOT / "scripts" / "probes" / "pages.py"), "0", str(tmp_path / name)],
                             env={**os.environ, "PYTHONPATH": str(base), "PYTHONHASHSEED": "0"},
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            for name, base in (("before", ROOT), ("after", tree))]  # fmt: skip
    (before, err), (after, said) = (run.communicate() for run in runs)
    assert runs[0].returncode == 0, err[-3000:]
    assert runs[1].returncode != 0 or before != after  # same_behaviour.py: FAILED TO RUN, or DIFFERENT
    assert b"circular import" in said
    # a module the app's start never imports, broken: said in the output, so DIFFERENT too
    (tree / "ap_coder" / "coding" / "cycle_probe.py").unlink()
    rules.write_text(text, encoding="utf-8")
    (tree / "ap_coder" / "bench" / "broken_probe.py").write_text("import no_such_module_anywhere\n", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(tree), "PYTHONHASHSEED": "0"}
    out = subprocess.run([sys.executable, str(ROOT / "scripts" / "probes" / "pages.py"), "0", str(tmp_path / "broken")],
                         env=env, capture_output=True, check=True).stdout  # fmt: skip
    assert out.splitlines()[0] == (b'{"body": "ModuleNotFoundError: No module named \'no_such_module_anywhere\'", '
                                   b'"kind": "not imported", "path": "ap_coder.bench.broken_probe"}')  # fmt: skip


def test_the_pages_probe_leaves_out_what_differs_run_to_run(tmp_path):
    spec = importlib.util.spec_from_file_location("pages_probe", ROOT / "scripts" / "probes" / "pages.py")
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    work = tmp_path / "pages-after"
    page = (f'<div title="{work}/demo"></div><input type="hidden" name="screen" value="c31837d4"> read 2026-11-20 '
            f'23:10:05, at 11:10 PM <dt>Backups</dt><dd><code>{work}/backups</code>, 3 kept</dd> '
            '<img src="/review/1/page/1.png?v=18e9e46e809e5d9f28b7"> 4,800.00')  # fmt: skip
    assert probe.masked(page, work) == (
        '<div title="(work)/demo"></div><input type="hidden" name="screen" value="(screen)"> read (time), at (time) '
        '<dt>Backups</dt><dd>(backups)</dd> <img src="/review/1/page/1.png?v=(file)"> 4,800.00'
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=Pat Tester", "-c", "user.email=pat@example.invalid", *args], cwd=repo,
                   check=True, capture_output=True)  # fmt: skip


MODULE = '''"""A module."""


def one():
    """The first."""
    return 1


def two(x):
    return x + 2


class Kept:
    def same(self):
        return "same"
'''


def test_a_move_word_for_word_is_the_same_and_a_token_changed_on_the_way_is_said(tmp_path, capsys):
    """D-331: ``moved_same.py`` finds every function, class and method of the files changed since the ref once, with
    the same syntax tree, wherever it went; one token changed, a docstring reworded (unless ``--docstrings``) or a
    definition kept twice is said, by name and file."""
    moved = load_script("moved_same", "ap_moved_same")
    repo = moved.ROOT = tmp_path
    _git(repo, "init", "-q")
    (repo / "a.py").write_text(MODULE, encoding="utf-8")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-qm", "a")
    head, tail = MODULE.split("\n\n\ndef two")
    (repo / "a.py").write_text(head + "\n", encoding="utf-8")
    (repo / "b.py").write_text(f'"""B."""\n\n\ndef two{tail}', encoding="utf-8")  # not added to git yet
    assert moved.main(["HEAD"]) == 0
    assert capsys.readouterr().out.startswith("4 definitions in 1 files at HEAD: 4 found once and the same")

    (repo / "b.py").write_text(f'"""B."""\n\n\ndef two{tail.replace("x + 2", "x + 3")}', encoding="utf-8")
    assert moved.main(["HEAD"]) == 1
    assert "  changed: def two (a.py -> b.py)\n" in capsys.readouterr().out

    (repo / "b.py").write_text(f'"""B."""\n\n\ndef two{tail}', encoding="utf-8")
    (repo / "a.py").write_text(head.replace("The first.", "The first one.") + "\n", encoding="utf-8")
    assert moved.main(["HEAD"]) == 1
    assert "  changed: def one (a.py -> a.py)\n" in capsys.readouterr().out
    assert moved.main(["HEAD", "--docstrings"]) == 0
    capsys.readouterr()

    (repo / "c.py").write_text('"""C."""\n\n\ndef two(x):\n    return x + 2\n', encoding="utf-8")
    assert moved.main(["HEAD", "--docstrings"]) == 1
    assert "  twice: def two (c.py)\n" in capsys.readouterr().out
    assert moved.main(["HEAD", "--docstrings", "a.py", "b.py"]) == 0  # only these files
    capsys.readouterr()
    assert moved.main(["no-such-ref"]) == 2


def test_an_empty_module_is_compared_with_the_docstrings_left_out_too(tmp_path, capsys):
    """B-3017: ``--docstrings`` stopped with IndexError on an empty file (a split package's new ``__init__.py``):
    a module with nothing in it has no first statement to be its docstring."""
    moved = load_script("moved_same", "ap_moved_same")
    repo = moved.ROOT = tmp_path
    _git(repo, "init", "-q")
    (repo / "a.py").write_text(MODULE, encoding="utf-8")
    _git(repo, "add", "a.py")
    _git(repo, "commit", "-qm", "a")
    (repo / "pkg").mkdir()
    (repo / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "pkg" / "notes.py").write_text("# only a comment\n", encoding="utf-8")
    assert moved.main(["HEAD", "--docstrings"]) == 0
    assert "0 new" in capsys.readouterr().out


USERS = """class UsersMixin:
    def add_user(self, name):
        return name


    def start_session(self, user_id):
        return {"user": user_id}
"""
STORE = """from users import UsersMixin


class Store(UsersMixin):
    pass
"""


def test_methods_moved_word_for_word_into_a_new_class_are_the_same_and_one_changed_on_the_way_is_said(tmp_path, capsys):
    """B-3018: the store's usual split (methods moved word for word from one part's class into a new one, the Store
    taking it among its bases) was said gone, new and changed: a method was looked for in its own class only. A
    method is found by its name and code in any class; the new class that only holds what moved, and a class whose
    bases gained it, are said so and pass; a token changed in a moved method is said changed."""
    moved = load_script("moved_same", "ap_moved_same")
    repo = moved.ROOT = tmp_path
    _git(repo, "init", "-q")
    (repo / "users.py").write_text(USERS, encoding="utf-8")
    (repo / "store.py").write_text(STORE, encoding="utf-8")
    _git(repo, "add", "users.py", "store.py")
    _git(repo, "commit", "-qm", "a")
    kept, method = USERS.split("\n\n\n")
    (repo / "users.py").write_text(kept + "\n", encoding="utf-8")
    sessions = f'class SessionsMixin:\n    """The sessions."""\n\n{method}'
    (repo / "sessions.py").write_text(sessions, encoding="utf-8")
    (repo / "store.py").write_text(STORE.replace("(UsersMixin)", "(UsersMixin, SessionsMixin)"), encoding="utf-8")
    assert moved.main(["HEAD"]) == 0
    said = capsys.readouterr().out
    assert "0 changed, 0 gone, 0 twice, 0 new" in said
    assert "  moved: method start_session (users.py UsersMixin -> sessions.py SessionsMixin)\n" in said
    (repo / "sessions.py").write_text(sessions.replace('{"user": user_id}', '{"user": user_id + 1}'), encoding="utf-8")
    assert moved.main(["HEAD"]) == 1
    said = capsys.readouterr().out
    assert "  changed: method start_session (users.py UsersMixin -> sessions.py SessionsMixin)\n" in said


def test_with_no_probes_named_the_default_ones_run():
    tool = _tool()
    assert tool.parse([]).probes == list(tool.DEFAULT)
    assert tool.parse(["pipeline", "--n", "3"]).probes == ["pipeline"]
    with pytest.raises(SystemExit):
        tool.parse(["taxes"])


def test_the_code_compared_with_sees_the_models_this_checkout_has(tmp_path):
    """The optional models a checkout holds (``models/``, git-ignored: the table finder) change what is read; the
    checkout of the ref has none, so a comparison showed a difference no code made (B-1699)."""
    tool = _tool()
    root, before = tmp_path / "root", tmp_path / "before"
    (root / "models").mkdir(parents=True)
    (root / "models" / "finder.onnx").write_bytes(b"model")
    before.mkdir()
    tool.share_models(root, before)
    assert (before / "models" / "finder.onnx").read_bytes() == b"model"
    bare = tmp_path / "bare"
    bare.mkdir()
    tool.share_models(tmp_path / "none", bare)
    assert not (bare / "models").exists()


def test_the_migrate_probe_leaves_out_the_clock_time_a_database_is_brought_up_to_date(tmp_path, monkeypatch):
    """B-2306: bringing a database up to date writes when it began to keep its coding rules' use
    (``rule_use_since``, D-217): the clock's time, which the probe printed with the settings. The two trees run side
    by side a second apart, so ``migrate`` said DIFFERENT for a change that made none. What the clock writes is left
    out, as the id made anew each time is: two databases brought up to date at different times read the same."""
    from ap_coder.core.store import Store, migrations

    spec = importlib.util.spec_from_file_location("migrate_probe", ROOT / "scripts" / "probes" / "migrate.py")
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    read = []
    for n, at in enumerate(("2026-11-10T19:20:19", "2026-11-10T19:20:20")):
        monkeypatch.setattr(migrations, "_now", lambda at=at: at)
        Store(tmp_path / f"{n}.db")
        read.append(probe.contents(tmp_path / f"{n}.db"))
    assert dict(read[0]["tables"]["settings"])["rule_use_since"] == "(new each time)"
    assert read[0] == read[1]
