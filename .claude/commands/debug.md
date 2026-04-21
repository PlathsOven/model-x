---
description: Structured bug isolation across engine, runner, and dashboard layers
---

## /debug — Bug Isolation & Fix

### 1. Context Load
Read `CLAUDE.md` and `docs/architecture.md` to understand the cycle lifecycle and the lane structure (`modelx/` core vs. `dashboard/` read-only surface). Skim `tasks/lessons.md` for any entry that touches the area of the bug.

### 2. Reproduce
Gather details about the bug:
- What is the expected behavior?
- What is the actual behavior?
- Which layer does it appear in? (`modelx/` engine, `run_live.py` supervisor, `settle.py`, `dashboard/server.py`, `dashboard/frontend/`)
- Is there a recent commit or change that correlates with the onset?
- Can you reproduce with `python3 -m pytest tests/<file>::<test> -x` or a minimal `run_live.py --contract <yaml>` invocation?

### 3. Isolate
Narrow down the root cause. Work through these layers in order:
1. **Input / config** — is `agents.yaml` or `contracts.yaml` actually what you expect? Is the SQLite db state consistent?
2. **Core engine** (`modelx/`) — models, matching, scoring, phase advancement
3. **Runner** (`run_live.py`, `modelx/supervisor.py`, `modelx/market_runner.py`) — tick loop, concurrency, persistence
4. **Dashboard transport** (`dashboard/server.py` ↔ `dashboard/frontend/src/api.ts`) — response shape, polling, mtime reload
5. **Dashboard UI** (`dashboard/frontend/src/components/`) — render logic, state hooks

Check schema drift at the dashboard boundary: `modelx/models.py` dataclasses → `dashboard/server.py` serialization → `dashboard/frontend/src/types.ts`. When the three diverge, the Python side is upstream.

### 4. Fix
Apply the **minimal** fix that addresses the root cause:
- Prefer upstream fixes over downstream workarounds
- Fix the root cause, not the symptom — one bug = one fix in one place
- If your diff has a primary fix and a secondary fix, the secondary is probably the real one; reconsider the primary
- Add descriptive error messages or logging if the failure mode was silent (prefer `print(...)` or `logging.warning` consistent with the surrounding file — the codebase is print-based, not structured-log based)
- Do not refactor unrelated code while fixing

**Escalation rule:** if 2 fix attempts have already failed, **STOP and invoke `/logic-audit`** before attempting a third. Two failed fixes usually means the bug is structural and a surface-level patch will not hold.

### 5. Verify
- Confirm the fix resolves the reported behavior (run the repro from step 2)
- Run the appropriate subset of: `python3 -m compileall modelx/ dashboard/ -q`, `python3 -m pytest tests/ -x -q`, `cd dashboard/frontend && npx tsc --noEmit`
- Check that no regressions were introduced in adjacent functionality
- If the bug was caused by dashboard schema drift, confirm the dataclass/server serialization and `types.ts` are now aligned

### 6. Commit
List the exact files you modified. Stage and commit surgically:

```bash
git add <file1> <file2> ...
git commit -m "fix: <description>"
```

After the fix lands, add a one-line lesson to `tasks/lessons.md` describing what class of bug this was and how to avoid it next time.
