# .claude: Claude Code's project settings

What Claude Code loads in this repository: the hooks that keep the codebase map honest (the edit reminder and the
review gate), and the map-reviewer agent.
It holds no code of the app. Personal settings go in `settings.local.json` (keep it out of commits); agent worktrees in
`worktrees/` are git-ignored.

**Depends on:** `scripts`
**Used by:** none (Claude Code reads it; nothing in the app does)

## Interface
- `settings.json`: a PostToolUse hook on Edit/Write/MultiEdit that runs `python3 scripts/check_map.py --hook`: after
  an edit to a code file it reminds the agent of that folder's CLAUDE.md and its **Used by:** line. It never blocks.
- `settings.json`, the review gate (`scripts/map_review_gate.py`, D-35): SubagentStop of `map-reviewer` records the
  repository's changed files as reviewed, staged and on disk (`--reviewed`, in git's folder); PreToolUse on Bash stops
  a `git commit` (also after `cd X &&`, `git -C X`) while any changed non-Markdown file differs (`--commit`, exit 2).
- `agents/map-reviewer.md`: the reviewer to run before each commit (see its CLAUDE.md).

## Gotchas and decisions
- The edit hook only reminds; the checks that fail are `scripts/check_map.py` in CI (the "Codebase map" job) and the
  review gate before an agent's commit (D-32, D-35). The gate is a habit made automatic, not a lock: a person's
  commit in a terminal, a merge, cherry-pick or revert being finished, and anything it can't read pass.
- The gate reads the command, it doesn't run a shell: a commit behind `timeout`, a wrapper's option (`nice -n 10`),
  `bash -lc`, `eval`, `{ }`, `if ... then`, backticks or a git alias is not seen. Write `git commit` plainly.
- Run the map-reviewer in the foreground: what it records is the state when it finishes, so code edited while it
  runs in the background would count as reviewed.
- An edit inside an agent's worktree (`.claude/worktrees/<name>/...`) is reminded of that copy's own folder map.
- The hooks use `python3`; on Windows without it they don't run (no reminder, no gate; nothing else breaks).

## Files
- `settings.json`: the project hooks (the edit reminder and the review gate; committed, shared by everyone working here).
- `agents/`: project subagents (its own CLAUDE.md).
