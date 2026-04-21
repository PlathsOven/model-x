---
description: Walk all context docs after a work session to keep documentation current
---

## /doc-sync — Documentation Sync Protocol

After a work session that created or modified code, walk the seven checkpoints below. **Skip any checkpoint where nothing changed.** Present all proposed doc edits for human review before committing.

### 1. `docs/architecture.md`
Verify the component map and Key Files table still match reality. If a file was added, moved, or renamed, update the row. If a lane's purpose changed, update the lane description.

### 2. `docs/user-journey.md`
Update if any user-facing flow changed — new CLI command, new dashboard view, new error state, new scoring metric, new agent-facing prompt section.

### 3. `README.md` (operator's guide)
Update if prerequisites, env vars, run steps, CLI flags, or troubleshooting changed. `README.md` is the single operator's guide for `run_live.py`, `settle.py`, configs, and the dashboard launch flow.

### 4. `docs/stack-status.md`
Update PROD / MOCK / STUB / OFF for any component whose status transitioned this session. Add new rows for new components.

### 5. `docs/conventions.md`
Verify the listed patterns still match code reality. Flag any new pattern that appeared in this session — was it a deliberate choice (document it) or a drift (flag for cleanup)?

### 6. `tasks/lessons.md`
Add any new lesson learned from a correction or failed attempt this session. Prune entries that are no longer accurate (the code has changed).

### 7. `CLAUDE.md`
Add any rule that would have prevented a mistake made this session. Remove any rule that is now obsolete. **Hard cap: under 100 lines.** If adding a rule would push it over, absorb an existing rule first.

### 8. Present
Show the human every proposed edit grouped by file. **Do not commit until approved.** When committing, use a single `docs:` commit that lists all touched files explicitly.
