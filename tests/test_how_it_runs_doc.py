"""docs/HOW_IT_RUNS.md cites the code as ``file:function``: every one of those must still exist, so the map can't
quietly drift from the code it describes."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "HOW_IT_RUNS.md"
CITATION = re.compile(r"`((?:ap_coder|scripts)/[\w/]+\.py|APProcessor\.bat|APProcessor\.command):([\w.]+)`")


def defined(text: str, name: str, suffix: str) -> bool:
    last = name.split(".")[-1]
    if suffix == ".bat":
        return re.search(rf"(?m)^:{re.escape(last)}\b", text) is not None
    if suffix == ".command":
        return re.search(rf"(?m)^{re.escape(last)}\(\)", text) is not None
    word = re.escape(last)
    return re.search(rf"(?m)^\s*(def|class|async def)\s+{word}\b|^\s*{word}\s*[:=]", text) is not None


def test_every_file_and_function_the_map_cites_exists():
    cited = CITATION.findall(DOC.read_text(encoding="utf-8"))
    assert len(cited) > 40
    missing = []
    for file, name in cited:
        path = ROOT / file
        if not path.is_file() or not defined(path.read_text(encoding="utf-8"), name, path.suffix):
            missing.append(f"{file}:{name}")
    assert missing == []
