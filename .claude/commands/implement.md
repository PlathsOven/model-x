---
description: Core feature implementation loop with built-in dependency gating and self-review
---

## /implement — Feature Implementation

### 1. Context Load
Read `CLAUDE.md` and `docs/architecture.md` to restore project structure, lane ownership, and the cycle lifecycle. If the task touches data that crosses the dashboard API boundary, also read `modelx/models.py` (dataclass source of truth) and `dashboard/frontend/src/types.ts` (TypeScript mirror).

### 2. Confirm Lane
Identify your assigned directory lane from the user's prompt (see Component Map in `docs/architecture.md`). If no lane is specified, ask before proceeding. You may ONLY create/modify files within this lane.

Lanes:
- `modelx/` — core engine (models, db, matching, scoring, phases, agents, runner)
- `dashboard/` — read-only web dashboard (FastAPI server + React/TS frontend)
- `tests/` — pytest tests colocated by subsystem
- Root scripts & configs — `run_live.py`, `settle.py`, `agents.yaml`, `contracts.yaml`

### 3. Plan
Output a step-by-step implementation plan. Include:
- Files to create or modify (full paths)
- Any new dependencies needed, with justification
- How the change connects to the cycle lifecycle (reference phase order from `docs/architecture.md`)

**If the plan touches >3 files, or crosses the dashboard API boundary, invoke `/preflight` first** and incorporate its risk findings into this plan.

**Do NOT write any code yet. Pause and wait for explicit approval.**

### 4. Execute
Implement the approved plan. Follow these rules:
- Only touch files within your confirmed lane
- Use plain dataclasses (not Pydantic) and plain dicts for wire shapes
- Use stdlib `sqlite3` for DB access — never add an ORM
- Use `httpx` for outbound HTTP (e.g. OpenRouter) — never `requests`
- Add all necessary imports at the top of each file
- Follow existing code style and naming conventions in the lane (see `docs/conventions.md`)

### 5. Dependency Gate
If you added any new package during execution:
- State what was added and why
- Confirm no existing package in the codebase already covers this need
- Pause for approval if any dependency is non-trivial (i.e., not a standard lib or already in `requirements.txt` / `dashboard/frontend/package.json`)

### 6. Self-Review
Before reporting completion, audit your own work:
- [ ] No files modified outside assigned lane
- [ ] No dead code or unused imports
- [ ] Error handling present for network/IO operations (OpenRouter, news/price fetches, DB)
- [ ] Consistent naming with existing codebase conventions
- [ ] Dataclass ↔ TypeScript dashboard shapes still aligned if boundary was touched
- [ ] Schema/DDL changes in `modelx/db.py` are applied to a fresh db AND `modelx.db.bak` regeneration is documented in the commit

Report any issues found. If clean, proceed to step 7.

### 7. Doc Sync
Delegate to `/doc-sync`. That workflow walks every context doc (`docs/architecture.md`, `docs/user-journey.md`, `README.md`, `docs/stack-status.md`, `docs/conventions.md`, `tasks/lessons.md`, `CLAUDE.md`) and updates only what changed this session. Skip this step only if your changes made **zero** user-visible, architectural, dependency, or status changes.

### 8. Verify
Run available verification commands appropriate to the lane:
- **modelx/**: `python3 -m compileall modelx/ -q` and `python3 -m pytest tests/ -x -q` (at minimum, tests relevant to the lane you touched)
- **dashboard/ (backend)**: `python3 -m compileall dashboard/ -q`; smoke-test with `curl localhost:8000/api/episode | jq '{loaded, status}'` if the server is running
- **dashboard/frontend/**: `cd dashboard/frontend && npx tsc --noEmit`, then `npm run build` if touching build-affecting files
- **root scripts**: `python3 -m compileall run_live.py settle.py -q`

Report results. Do not proceed to commit if any fails.

### 9. Commit
List the exact files you modified. Stage and commit surgically:

```bash
git add <file1> <file2> ...
git commit -m "<conventional commit message>"
```

**Never** use `git add .` or `git add -A`. Never push unless the user explicitly asks. Never bypass hooks with `--no-verify`. Wait for approval before executing the commit.
