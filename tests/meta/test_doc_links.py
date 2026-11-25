"""The docs' links lead somewhere: each relative link (or picture) in docs/*.md and README.md names a file or folder
that is there, and its ``#anchor`` a heading of that page as GitHub names it. A file moved or a heading reworded
breaks no link silently. The logs (BUGS.md, decisions.md) and docs/archive/ are history, left as they were written."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import unquote

from tests.conftest import ROOT

LOGS = {"BUGS.md", "decisions.md"}
LINK = re.compile(r"\]\(<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\)")  # [text](target) and ![picture](target)
FENCE = re.compile(r"(?ms)^ {0,3}(```|~~~).*?^ {0,3}\1[^\n]*$")
CODE = re.compile(r"(`+)(?:(?!\n[ \t]*\n).)*?\1", re.S)  # (within a paragraph)


def docs() -> list[Path]:
    return [p for p in sorted((ROOT / "docs").glob("*.md")) if p.name not in LOGS] + [ROOT / "README.md"]


def prose(text: str) -> str:
    """``text`` without its code (fenced blocks and inline spans, each kept as blank lines so line numbers hold)."""

    def blank(m: re.Match[str]) -> str:
        return re.sub(r"[^\n]", " ", m.group(0))

    return CODE.sub(blank, FENCE.sub(blank, text))


def anchors(path: Path) -> set[str]:
    """The ``#anchor`` of each heading of ``path`` as GitHub makes it: the heading's text without its markup, lower
    case, every character but a letter, a digit, ``_``, ``-`` and a space left out, the spaces as ``-``; a second
    heading of the same name gets ``-1``, a third ``-2``."""
    found: set[str] = set()
    for line in FENCE.sub("", path.read_text(encoding="utf-8")).splitlines():
        heading = re.match(r" {0,3}#{1,6}\s+(.*?)\s*#*\s*$", line)
        if not heading:
            continue
        text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", heading.group(1))  # a link: its text
        text = re.sub(r"<[^>]+>|[`*]", "", text).strip().lower()
        slug = re.sub(r"[^\w\- ]", "", text).replace(" ", "-")
        n, unique = 0, slug
        while unique in found:
            n += 1
            unique = f"{slug}-{n}"
        found.add(unique)
    return found


def broken_links(doc: Path) -> list[str]:
    """The relative links of ``doc`` that lead nowhere, each as ``doc:line target (why)``."""
    out = []
    for n, line in enumerate(prose(doc.read_text(encoding="utf-8")).splitlines(), 1):
        for target in LINK.findall(line):
            if re.match(r"[a-zA-Z][\w+.-]*:|//", target):  # https:, mailto:... (not this repository's)
                continue
            file, _, anchor = target.partition("#")
            path = (doc.parent / unquote(file)).resolve() if file else doc
            if not path.exists():
                out.append(f"{doc.name}:{n} {target} (no such file)")
            elif anchor and path.suffix == ".md" and unquote(anchor).lower() not in anchors(path):
                out.append(f"{doc.name}:{n} {target} (no such heading)")
    return out


def test_every_relative_link_of_the_docs_leads_somewhere():
    found = [link for doc in docs() for link in broken_links(doc)]
    assert not found, found


def test_a_link_to_a_missing_file_or_heading_is_found(tmp_path):
    (tmp_path / "GUIDE.md").write_text("# The guide\n\n## Step one: `serve` it\n\n## Step one: `serve` it\n",
                                       encoding="utf-8")  # fmt: skip
    doc = tmp_path / "README.md"
    doc.write_text("[ok](GUIDE.md#step-one-serve-it) [second](GUIDE.md#step-one-serve-it-1) [web](https://x.example)\n"
                   "[gone](OLD.md) [bad](GUIDE.md#step-two) ![picture](shots/a.png) [top](#the-readme)\n"
                   "`[in code](NOWHERE.md)`\n\n```\n[in a block](NOWHERE.md)\n```\n", encoding="utf-8")  # fmt: skip
    assert broken_links(doc) == [
        "README.md:2 OLD.md (no such file)",
        "README.md:2 GUIDE.md#step-two (no such heading)",
        "README.md:2 shots/a.png (no such file)",
        "README.md:2 #the-readme (no such heading)",
    ]
