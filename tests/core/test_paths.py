"""``core/paths.py``: the model's answers kept in the data folder (the page reader's readings, the chat model's
header readings and its accounts) are pruned by age and size, as each is written (D-171)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from types import SimpleNamespace

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
