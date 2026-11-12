"""``core/paths.py``: the model's answers kept in the data folder (the page reader's readings, the chat model's
header readings and its accounts) are pruned by age and size, as each is written (D-171)."""

from __future__ import annotations

import json
import os
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
    assert len(kept) == 3 and json.loads((cache / "model_reading" / f"{'cc' * 32}-qwen3.5-9b-v1.json").read_text())


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
    """B-2573: Windows refuses to rename over a file another process has open (the dashboard reading settings.json, an
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
