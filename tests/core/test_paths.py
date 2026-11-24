"""``core/paths.py``: the model's answers kept in the data folder (the page reader's readings, the chat model's
header readings and its accounts) are pruned by age and size, as each is written (D-171); this user's own notes folder
(``~/.ap_coder``) says what it is and is hidden on Windows, once."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from ap_coder.core import paths
from tests.conftest import SAMPLE_STEM, SAMPLES


def _answer(path: Path, days: float, size: int = 100) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x" * size, encoding="utf-8")
    when = time.time() - days * 86400
    os.utime(path, (when, when))
    return path


def test_the_answers_kept_go_after_30_days_and_past_their_size(tmp_path, monkeypatch):
    """Never pruned before: a few hundred thousand small files a year at the company server's volume, all invoice
    data. Now an answer written more than KEPT_DAYS ago goes (its folder too, once empty), then the oldest first past
    KEPT_MAX_BYTES; the page reader's timings and the answer just kept stay, and a half-written one a stopped process
    left goes after an hour."""
    folder = tmp_path / "page_reader"
    old, recent = _answer(folder / "aa" / "p1-k.json", 31), _answer(folder / "bb" / "p1-k.json", 2)
    timings, newest = _answer(folder / "timings.jsonl", 90), _answer(folder / "cc" / "p1-k.json", 40)
    left, writing = _answer(folder / "dd" / "p1-k.json.9-1.tmp", 0.1), _answer(folder / "ee" / "p2-k.json.9-1.tmp", 0)
    assert paths.prune_kept(folder, newest) == 2
    assert not old.exists() and not (folder / "aa").exists() and not left.exists()
    assert recent.exists() and timings.exists() and newest.exists() and writing.exists()
    _answer(folder / "ff" / "p1-k.json", 31)
    assert paths.prune_kept(folder) == 0  # within the hour after: not looked at again

    monkeypatch.setattr(paths, "KEPT_MAX_BYTES", 250)
    flat = tmp_path / "model_accounts"
    answers = [_answer(flat / f"{n}.json", 10 - n) for n in range(5)]  # 100 bytes each, the first the oldest
    assert paths.prune_kept(flat) == 3
    assert [a.exists() for a in answers] == [False, False, False, True, True]


def test_each_answer_kept_prunes_its_folder(tmp_path, monkeypatch, reference):
    """The page reader's reading of a page, the chat model's accounts for a question and its reading of a header:
    each one kept prunes its folder (at a process's first, then hourly)."""
    from ap_coder.ai import inference, page_reader
    from ap_coder.ai.inference import suggest_accounts
    from ap_coder.ai.pipeline import InvoicePipeline
    from tests.ai.test_local_llm import _PromptCoder

    cache = paths.private_dir() / ".cache"
    stale = [_answer(cache / name, 31) for name in ("page_reader/aa/p1-k.json", "model_accounts/a.json",
                                                    "model_reading/a-m-v1.json")]  # fmt: skip
    page_reader._cache_put("bb" * 32, 1, "ath-maas_ovisocr2", "a page", 12.0)
    assert suggest_accounts(_PromptCoder(reference), "Staples", [(1, "Paper", 10.0)], kept=cache / "model_accounts")
    monkeypatch.setattr(inference, "read_header", lambda coder, images: {"vendor_name": "Staples"})
    coder = SimpleNamespace(_prepare_local=lambda: "qwen3.5-9b", profile=SimpleNamespace(supports_vision=True))
    pdf = SAMPLES / f"{SAMPLE_STEM}.pdf"
    assert InvoicePipeline._model_reading(SimpleNamespace(coder=coder), pdf, "cc" * 32) == {"vendor_name": "Staples"}
    assert not any(path.exists() for path in stale)
    kept = [p for p in cache.rglob("*.json")]
    assert len(kept) == 3 and json.loads(
        (cache / "model_reading" / f"{'cc' * 32}-qwen3.5-9b-v1.json").read_text(encoding="utf-8")
    )


def test_a_reading_kept_as_another_process_prunes_its_folder_is_kept(monkeypatch):
    """B-1782: pruning removes the page reader's folders left empty (a folder a file read). One a pruner found empty
    and removed just after the page reader made it again, before its reading was written there, lost that reading:
    the page was read again. The page reader makes its folder again then."""
    from ap_coder.ai import page_reader

    real, removed = Path.mkdir, []

    def mkdir(self, *args, **kwargs):
        real(self, *args, **kwargs)
        if self.parent == page_reader.cache_dir() and not removed:
            removed.append(self)
            self.rmdir()  # another process pruning, which found it empty, removes it just then

    monkeypatch.setattr(Path, "mkdir", mkdir)
    page_reader._cache_put("dd" * 32, 1, "ath-maas_ovisocr2", "a page", 12.0)
    assert removed and (page_reader._cache_get("dd" * 32, 1, "ath-maas_ovisocr2") or {}).get("text") == "a page"


def test_two_processes_pruning_a_folder_at_once_leave_what_one_leaves(tmp_path, monkeypatch):
    """B-1779: each capture process, web worker and the background process prunes the folders it keeps things in; two
    at once each list the folder, and the second found the oldest files already gone, didn't count them as gone and
    went on removing newer ones (four at once left none of the OCR readings). A file already gone counts as gone: the
    OCR readings, the model's answers and the page viewer's pictures pruned by two processes at once leave what one
    leaves."""
    from ap_coder.capture import layout
    from ap_coder.web import document

    monkeypatch.setattr(layout, "OCR_CACHE_MAX_BYTES", 1000)
    monkeypatch.setattr(paths, "KEPT_MAX_BYTES", 1000)
    monkeypatch.setattr(document, "PAGES_KEPT_BYTES", 1000)

    def kept(folder: Path) -> None:
        paths._kept_pruned.pop(str(folder), None)  # (another process: it hasn't pruned the folder this hour)
        paths.prune_kept(folder)

    def pages(folder: Path) -> None:
        document._prune(folder, folder / "zz" / "newest.png")

    pruners = {"ocr": (layout._prune, "{n:02d}.json"), "kept": (kept, "{n:02d}/p1-k.json"),
               "pages": (pages, "{n:02d}/p{n}.png")}  # fmt: skip
    real = os.unlink
    for name, (prune, pattern) in pruners.items():
        left = []
        for at_once in (False, True):
            folder = tmp_path / name / str(at_once)
            for n in range(20):  # 2,000 bytes, written a second apart, the first the oldest
                _answer(folder / pattern.format(n=n), (20 - n) / 86400)

            def unlink(path, *args, prune=prune, folder=folder, **kwargs):
                monkeypatch.setattr(os, "unlink", real)
                prune(folder)  # another process pruning the same folder gets there first
                return real(path, *args, **kwargs)

            if at_once:
                monkeypatch.setattr(os, "unlink", unlink)
            prune(folder)
            monkeypatch.setattr(os, "unlink", real)
            left.append(sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_file()))
        assert left[0] and left[1] == left[0], name


def test_the_settings_file_is_replaced_even_while_another_process_has_it_open(tmp_path, monkeypatch):
    """B-2509: Windows refuses to rename over a file another process has open (the dashboard reading settings.json, an
    antivirus): write_user_settings's bare os.replace failed with PermissionError, as server.json's did (B-1962). The
    rename is tried again for a moment on Windows; elsewhere such a rename never fails: no wait."""
    monkeypatch.setenv("AP_USER_SETTINGS", str(tmp_path / "settings.json"))
    paths.write_user_settings(reviewer="Before")
    real, tries, waited = os.replace, [], []

    def busy(src, dst):
        tries.append(dst)
        if len(tries) == 1:
            raise PermissionError(13, "The process cannot access the file: another process uses it", str(dst))
        return real(src, dst)

    monkeypatch.setattr(paths, "REPLACE_WAITS", (0.05, 0.1, 0.2, 0.4, 0.8))  # (Windows' own)
    monkeypatch.setattr(paths.time, "sleep", waited.append)
    monkeypatch.setattr(os, "replace", busy)
    paths.write_user_settings(reviewer="After")
    assert paths.read_user_settings()["reviewer"] == "After" and len(tries) == 2 and waited == [0.05]
    monkeypatch.setattr(paths, "REPLACE_WAITS", ())  # (elsewhere)
    tries.clear()
    with pytest.raises(PermissionError):
        paths.write_user_settings(reviewer="Never")
    assert paths.read_user_settings()["reviewer"] == "After" and len(tries) == 1 and waited == [0.05]


# --- This user's notes folder (~/.ap_coder): it says what it is, and is hidden on Windows, once ---------------------

DIRECTORY, ARCHIVE, HIDDEN, NOT_INDEXED = 0x10, 0x20, 0x2, 0x2000  # Windows' file attributes
NOT_FOUND = 0xFFFFFFFF  # GetFileAttributesW's INVALID_FILE_ATTRIBUTES


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A home folder of the test's own, with no AP_USER_SETTINGS (the person's own record, not a demo's or a test's),
    and its ``.ap_coder`` as a start leaves it: the record written."""
    folder = tmp_path / "home"
    monkeypatch.setenv("HOME", str(folder))
    monkeypatch.setenv("USERPROFILE", str(folder))  # (Windows' home)
    monkeypatch.delenv("AP_USER_SETTINGS", raising=False)
    paths.write_user_settings(data_dir=str(folder / "APCoder"))
    assert paths.user_settings_path() == folder / ".ap_coder" / "settings.json"
    return folder


@pytest.fixture
def hidden(monkeypatch):
    """``hide_folder`` as Windows answers it (hidden now): every folder it was asked to hide."""
    asked: list[Path] = []
    monkeypatch.setattr(paths, "hide_folder", lambda folder: asked.append(folder) or True)
    return asked


def test_the_notes_folder_says_what_it_is_once_and_is_hidden_then(home, hidden):
    """The owner asked why their user folder shows two folders, APCoder and .ap_coder. .ap_coder is AP Coder's own
    notes, which every version, older ones too, reads where it is (the record of installs: which AP Coder is the
    current one), so it stays there: it says what it is in a README.txt and is hidden in File Explorer, once. Its
    text names no folder (nothing to go stale, nothing that can't be written); a second start writes nothing."""
    notes = home / ".ap_coder"
    assert paths.label_notes_folder() is True and hidden == [notes]
    readme = notes / "README.txt"
    text = readme.read_text(encoding="utf-8")
    assert text == paths.NOTES_TEXT and "Don't delete, move or rename this folder" in text
    assert "Settings, Data & backups" in text and str(home) not in text and "APCoder" not in text
    os.utime(readme, (1_000_000_000, 1_000_000_000))
    assert paths.label_notes_folder() is False and hidden == [notes]
    assert readme.stat().st_mtime == 1_000_000_000 and readme.read_text(encoding="utf-8") == text
    assert sorted(p.name for p in notes.iterdir()) == ["README.txt", "settings.json"]  # nothing else written


def test_a_notes_folder_with_its_readme_is_never_hidden_again(home, hidden):
    """Someone who showed the folder again in File Explorer keeps it shown: its README is there, so it is not hidden
    again, also after going back to an older AP Coder (which never reads it) and forward again. A README of their own
    words is kept as it is."""
    readme = home / ".ap_coder" / "README.txt"
    readme.write_text("my own words", encoding="utf-8")
    assert paths.label_notes_folder() is False and hidden == []
    assert readme.read_text(encoding="utf-8") == "my own words"


def test_nothing_is_labelled_for_a_demo_a_test_or_a_scratch_record(tmp_path, monkeypatch, hidden):
    """AP_USER_SETTINGS set (scripts/demo.bat, the tests, the first-day walk, CI's scratch homes): that record is not
    the person's own folder. Nothing is written beside it, nothing hidden."""
    folder = tmp_path / "home"
    monkeypatch.setenv("HOME", str(folder))
    monkeypatch.setenv("USERPROFILE", str(folder))
    record = folder / ".ap_coder" / "demo_settings.json"
    monkeypatch.setenv("AP_USER_SETTINGS", str(record))
    paths.write_user_settings(reviewer="Demo")
    assert paths.label_notes_folder() is False and hidden == []
    assert sorted(p.name for p in record.parent.iterdir()) == ["demo_settings.json"]


def test_a_notes_folder_that_cant_be_written_is_left_as_it_is_and_never_raises(home, hidden, monkeypatch):
    """Labelling is a courtesy: a README Windows refuses to write, or a notes folder not there, is no reason to stop
    a start. Nothing is written or made, nothing hidden."""
    notes = home / ".ap_coder"

    def refused(self, *args, **kwargs):
        raise PermissionError(13, "Access is denied", str(self))

    with monkeypatch.context() as m:
        m.setattr(Path, "write_text", refused)
        assert paths.label_notes_folder() is False and hidden == []
    assert not (notes / "README.txt").exists()
    for item in notes.iterdir():
        item.unlink()
    notes.rmdir()
    assert paths.label_notes_folder() is False and hidden == [] and not notes.exists()


@pytest.fixture
def kernel32(monkeypatch):
    """Windows as ``hide_folder`` sees it, its kernel32 a stand-in: each folder's attributes (``attributes``, by
    name), every call made (``calls``) and the libraries AP Coder opened for itself (``opened``)."""
    import ctypes

    def get(name):
        windows.calls.append(("get", name))
        return windows.attributes.get(name, NOT_FOUND)

    def put(name, value):
        windows.calls.append(("set", name, value))
        windows.attributes[name] = (windows.attributes[name] & DIRECTORY) | value  # (a folder stays one)
        return 1

    def library(name, **options):
        windows.opened.append(name)
        return windows

    windows = SimpleNamespace(GetFileAttributesW=get, SetFileAttributesW=put, attributes={}, calls=[], opened=[])
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", library, raising=False)
    return windows


def test_on_windows_the_folder_is_hidden_its_other_attributes_kept_once(kernel32, tmp_path):
    """Only the folder is hidden (a hidden file can't be written whole any more, and every version writes its files
    whole), with the attributes it had and Windows lets be set; one already hidden is left alone and gives False.
    Through a kernel32 handle of AP Coder's own with the calls' types (ops/awake.py: the shared one's are every
    library's)."""
    from ctypes import wintypes

    folder = tmp_path / ".ap_coder"
    name = str(folder)
    kernel32.attributes[name] = DIRECTORY | ARCHIVE | NOT_INDEXED
    assert paths.hide_folder(folder) is True
    assert kernel32.calls == [("get", name), ("set", name, ARCHIVE | NOT_INDEXED | HIDDEN)]
    assert kernel32.attributes[name] == DIRECTORY | ARCHIVE | NOT_INDEXED | HIDDEN
    assert paths.hide_folder(folder) is False and kernel32.calls[2:] == [("get", name)]  # hidden already: nothing set
    assert set(kernel32.opened) == {"kernel32"}
    get, put = kernel32.GetFileAttributesW, kernel32.SetFileAttributesW
    assert get.argtypes == [wintypes.LPCWSTR] and get.restype is wintypes.DWORD
    assert put.argtypes == [wintypes.LPCWSTR, wintypes.DWORD] and put.restype is wintypes.BOOL


def test_a_folder_windows_wont_hide_is_simply_shown(kernel32, tmp_path, monkeypatch):
    """Not found, refused, or no kernel32 to ask: False, nothing raised; off Windows nothing is asked at all."""
    import ctypes

    folder = tmp_path / ".ap_coder"
    assert paths.hide_folder(folder) is False and kernel32.calls == [("get", str(folder))]  # not found: nothing set
    kernel32.attributes[str(folder)] = DIRECTORY
    kernel32.SetFileAttributesW = lambda name, value: 0  # refused (an antivirus, a policy)
    assert paths.hide_folder(folder) is False

    def missing(name, **options):
        raise OSError("no kernel32 here")

    monkeypatch.setattr(ctypes, "WinDLL", missing)
    assert paths.hide_folder(folder) is False
    monkeypatch.setattr(sys, "platform", "linux")
    kernel32.opened.clear()
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **options: kernel32.opened.append(name), raising=False)
    assert paths.hide_folder(folder) is False and kernel32.opened == []


@pytest.mark.skipif(sys.platform != "win32", reason="Windows' own attributes")
def test_on_windows_itself_the_hidden_folder_is_still_written_as_every_version_writes_it(tmp_path, monkeypatch):
    """The folder is hidden, its other attributes kept, and hidden once; every write a version makes in it still
    works: the record written whole and replaced, a log made and appended to, a log replaced by another."""
    import ctypes
    import stat
    from ctypes import wintypes

    folder = tmp_path / ".ap_coder"
    folder.mkdir()
    put = ctypes.WinDLL("kernel32", use_last_error=True).SetFileAttributesW
    put.argtypes, put.restype = [wintypes.LPCWSTR, wintypes.DWORD], wintypes.BOOL
    assert put(str(folder), stat.FILE_ATTRIBUTE_NOT_CONTENT_INDEXED)
    assert paths.hide_folder(folder) is True
    found = os.stat(folder).st_file_attributes
    assert found & stat.FILE_ATTRIBUTE_HIDDEN and found & stat.FILE_ATTRIBUTE_NOT_CONTENT_INDEXED
    assert found & stat.FILE_ATTRIBUTE_DIRECTORY and paths.hide_folder(folder) is False
    monkeypatch.setenv("AP_USER_SETTINGS", str(folder / "settings.json"))
    paths.write_user_settings(reviewer="Ann")
    paths.write_user_settings(reviewer="Bea")  # (written whole again, over the one there)
    assert paths.read_user_settings()["reviewer"] == "Bea"
    logs = folder / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    for said in ("a start\n", "another\n"):
        with open(logs / "launch.log", "a", encoding="utf-8") as log:
            log.write(said)
    os.replace(logs / "launch.log", logs / "launch.previous.log")
    assert (logs / "launch.previous.log").read_text(encoding="utf-8") == "a start\nanother\n"
    assert not os.stat(folder / "settings.json").st_file_attributes & stat.FILE_ATTRIBUTE_HIDDEN
