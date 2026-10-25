"""The ``developer`` agent (D-106): coding work here runs with only the developer tools, so each step carries a
small context. A tool added to its list costs every step of every agent; this keeps the list a deliberate choice."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / ".claude" / "agents" / "developer.md"
TOOLS = {"Bash", "Read", "Edit", "Write", "WebFetch", "WebSearch"}


def _front_matter() -> dict[str, str]:
    text = PROFILE.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "the agent file starts with its front matter"
    head = text.split("---\n", 2)[1]
    return dict(line.split(":", 1) for line in head.splitlines() if ":" in line)


def test_the_developer_agent_carries_only_the_developer_tools():
    meta = {k.strip(): v.strip() for k, v in _front_matter().items()}
    assert meta["name"] == "developer"
    assert {t.strip() for t in meta["tools"].split(",")} == TOOLS


def test_claude_md_points_new_agents_to_it():
    assert "--agent developer" in (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
