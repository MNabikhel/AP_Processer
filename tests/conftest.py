import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ap_coder.config import Settings
from ap_coder.reference_data import load_reference_data

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SAMPLES = ROOT / "samples"
SAMPLE_STEM = "northwind_ON_HST_NW-2026-0912"


@pytest.fixture(autouse=True)
def isolated_private_dir(monkeypatch, tmp_path):
    """Never let a test touch the real private/ folder (database, invoices, outputs)."""
    monkeypatch.setenv("AP_PRIVATE_DIR", str(tmp_path / "private"))


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
