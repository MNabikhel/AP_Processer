"""The repository stays easy for an agent to read: no line a file reader would cut off, decisions wrapped."""

from tests.conftest import ROOT


def test_no_doc_line_is_cut_off_when_read():
    """A line over 2,000 characters is truncated by the agent's file reader, so the doc gets read again another way."""
    long = [f"{p.name}:{n}" for p in [*ROOT.glob("docs/*.md"), ROOT / "README.md", ROOT / "CLAUDE.md"]
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if len(line) > 2000]  # fmt: skip
    assert not long, long


def test_decisions_are_wrapped():
    text = (ROOT / "docs" / "decisions.md").read_text(encoding="utf-8")
    assert max(len(line) for line in text.splitlines()) <= 120
