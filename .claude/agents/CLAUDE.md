# .claude/agents: project subagents

Agents Claude Code can run in this repository. Each is a Markdown file with a YAML header (name, description, tools)
and its instructions. They read and report; none edits code.

**Depends on:** none
**Used by:** none (run by Claude Code on request, or as the root CLAUDE.md asks)

## Interface
- `map-reviewer`: give it a change (the working tree, or a base to diff against). It runs `scripts/check_map.py
  --changed`, compares the diff with each touched folder's CLAUDE.md, checks every folder in their **Used by:** lines
  for callers that break, checks docs/decisions.md and the standing rules, and reports BLOCKING / UPDATE / OK.

## Gotchas and decisions
- Run it before every commit that changes code (the root CLAUDE.md's rule); its findings are fixed before pushing.
- It has a fresh context on purpose: it trusts the code, not the map, and reports where they differ.

## Files
- `map-reviewer.md`: the reviewer of a change against the codebase map.
