"""The ``developer`` agent (D-106) and the ``reviewer`` agent (D-338): coding work and reviews here run with only the
developer tools, so each step carries a small context. A tool added to a list costs every step of every agent; this
keeps each list a deliberate choice."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PROFILES = {
    "developer": ROOT / ".claude" / "agents" / "developer.md",
    "reviewer": ROOT / ".claude" / "agents" / "reviewer.md",
}
TOOLS = {"Bash", "Read", "Edit", "Write", "WebFetch", "WebSearch"}


def _front_matter(profile: Path) -> dict[str, str]:
    text = profile.read_text(encoding="utf-8")
    assert text.startswith("---\n"), "the agent file starts with its front matter"
    head = text.split("---\n", 2)[1]
    return dict(line.split(":", 1) for line in head.splitlines() if ":" in line)


@pytest.mark.parametrize("name", PROFILES)
def test_each_agent_carries_only_the_developer_tools(name):
    meta = {k.strip(): v.strip() for k, v in _front_matter(PROFILES[name]).items()}
    assert meta["name"] == name
    assert {t.strip() for t in meta["tools"].split(",")} == TOOLS


def test_every_agent_profile_is_held_here():
    """A profile added to .claude/agents/ has its tools chosen here too."""
    assert sorted((ROOT / ".claude" / "agents").glob("*.md")) == sorted(PROFILES.values())


@pytest.mark.parametrize("name", PROFILES)
def test_the_scripts_a_profile_names_are_there(name):
    """Its rules send the agent to scripts by path (``scripts/check.py``, ``scripts/moved_same.py``): a script renamed
    or gone would leave the agent a rule it can't follow."""
    named = set(re.findall(r"scripts/[\w/]+\.\w+", PROFILES[name].read_text(encoding="utf-8")))
    assert named and not [s for s in sorted(named) if not (ROOT / s).is_file()]
