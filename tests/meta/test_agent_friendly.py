"""The repository stays easy for an agent to read: no line a file reader would cut off, decisions wrapped."""

from tests.conftest import ROOT


def test_no_doc_line_is_cut_off_when_read():
    """A line over 2,000 characters is truncated by the agent's file reader, so the doc gets read again another way."""
    long = [f"{p.name}:{n}" for p in [*ROOT.glob("docs/**/*.md"), ROOT / "README.md", ROOT / "CLAUDE.md"]
            for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1) if len(line) > 2000]  # fmt: skip
    assert not long, long


def test_decisions_are_wrapped():
    for path in [ROOT / "docs" / "decisions.md", *ROOT.glob("docs/archive/decisions-*.md")]:
        text = path.read_text(encoding="utf-8")
        assert max(len(line) for line in text.splitlines()) <= 120, path.name


def test_the_newest_bug_and_decision_numbers_are_found_in_the_short_files():
    """``grep -o "B-[0-9]*" docs/BUGS.md | tail -1`` (CLAUDE.md) gives the last number used, the archive's included."""
    import re

    def last(pattern, *paths):
        return max(int(n) for p in paths for n in re.findall(pattern, p.read_text(encoding="utf-8")))

    archive = ROOT / "docs" / "archive"
    bugs = re.findall(r"B-(\d+)", (ROOT / "docs" / "BUGS.md").read_text(encoding="utf-8"))
    assert int(bugs[-1]) == last(r"B-(\d+)", ROOT / "docs" / "BUGS.md", *archive.glob("bugs-*.md"))
    assert last(r"D-(\d+)", ROOT / "docs" / "decisions.md") == last(r"D-(\d+)", *archive.glob("decisions-*.md"),
                                                                     ROOT / "docs" / "decisions.md")  # fmt: skip
