# .claude: Claude Code's project settings

What Claude Code loads in this repository: the hook that keeps the codebase map honest, and the map-reviewer agent.
It holds no code of the app. Personal settings go in `settings.local.json` (keep it out of commits); agent worktrees in
`worktrees/` are git-ignored.

**Depends on:** `scripts`
**Used by:** none (Claude Code reads it; nothing in the app does)

## Interface
- `settings.json`: a PostToolUse hook on Edit/Write/MultiEdit that runs `python3 scripts/check_map.py --hook`: after
  an edit to a code file it reminds the agent of that folder's CLAUDE.md and its **Used by:** line. It never blocks.
- `agents/map-reviewer.md`: the reviewer to run before each commit (see its CLAUDE.md).

## Gotchas and decisions
- The hook only reminds; the checks that fail are `scripts/check_map.py` in CI (the "Codebase map" job) and before a
  commit (docs/decisions.md D-32).
- An edit inside an agent's worktree (`.claude/worktrees/<name>/...`) is reminded of that copy's own folder map.
- The hook uses `python3`; on Windows without it the reminder is simply not shown (nothing else breaks).

## Files
- `settings.json`: the project hook (committed, shared by everyone working here).
- `agents/`: project subagents (its own CLAUDE.md).
