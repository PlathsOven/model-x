---
description: Pre-change context load + risk assessment before touching any non-trivial area of the code
---

## /preflight — Pre-Change Checklist

Run before any change that touches more than one file, crosses the dashboard API boundary, or edits an area you have not read in this session. Preflight catches problems cheaply — at read-time instead of commit-time.

**When to run:**
- Before `/implement` on any task touching >3 files
- Before `/debug` on a bug with unclear origin
- Before any change crossing the dashboard API boundary (`modelx/models.py` dataclasses ↔ `dashboard/server.py` serialization ↔ `dashboard/frontend/src/types.ts`)
- Before any change that modifies the SQLite schema in `modelx/db.py`
- When editing a file >300 lines

---

### 1. Load Schemas at the Boundary

If the change will touch data that crosses the dashboard API boundary:
- Read `modelx/models.py` (dataclasses — source of truth for engine state)
- Read the relevant handler(s) in `dashboard/server.py` (Python-side JSON serialization)
- Read `dashboard/frontend/src/types.ts` (TypeScript mirror — must match serialized shape)

If the three diverge for any field you intend to touch, stop and align them **before** touching feature code. The dataclass is upstream.

If the change will touch the SQLite schema:
- Read `modelx/db.py` (DDL + read/write helpers)
- Decide explicitly whether existing `modelx.db` files will be migrated, wiped, or left untouched — the project treats `modelx.db` as disposable per-run state, not as a long-lived store.

### 2. Load Architecture & Conventions

- `docs/architecture.md` — confirm the component map still matches reality; identify the lane you will work in
- `docs/conventions.md` — confirm the patterns you intend to use are the established ones; flag any deviation

If the file you are about to edit is not in the Key Files table, that is a finding — either add it to the table as part of this task, or the table is stale.

### 3. Map the Blast Radius

For each file you intend to modify, grep for its importers:
- Python: search for `from modelx.<module>` and `import <name>` across `modelx/`, `dashboard/`, `tests/`, and root scripts
- TypeScript: search for `from '.*<module>'` under `dashboard/frontend/src/`

List every file that depends on what you're about to change. A change that looks local but is imported by 15 files is not local.

### 4. Check the Lessons Log

Read `tasks/lessons.md` and search for any entry that mentions:
- The file you are about to touch
- The pattern you intend to use
- The error mode your change could produce

If there's a relevant lesson, honor it. Lessons exist because the mistake already happened once.

### 5. List Files to Modify

Output an exhaustive list of files you will create, modify, or delete. Include:
- Full path
- One-line reason per file
- Predicted line-count delta (rough)
- Whether the file is inside your confirmed lane

If any file is outside the lane implied by the task, justify it explicitly or move that change into a separate task.

### 6. Identify Risks

For this specific change, enumerate:
- **Dashboard schema drift risk** — could this leave dataclass, server serialization, and `types.ts` out of sync?
- **SQLite schema risk** — does this change DDL? If so, will existing `modelx.db` files break? What is the migration story?
- **Resume-from-DB risk** — `run_live.py` rebuilds state mid-cycle from stored fills. Does this change the persisted shape in a way that breaks resumption?
- **Async/concurrency risk** — does this touch the supervisor's `asyncio.gather` fan-out or the per-market `MarketRunner`?
- **OpenRouter risk** — does this change how agent prompts are built or how responses are parsed? A parse regression silently skips that agent's cycle.
- **Dashboard auto-reload risk** — does this touch the mtime-driven rebuild in `dashboard/server.py`?

Flag each applicable risk with a one-line mitigation.

### 7. Present the Plan

Output a structured plan:

```
## Preflight: <task summary>

### Files to modify
- <path> — <reason> — (~<±lines>) — lane: <lane>

### Importers / blast radius
<file → list of importers>

### Schemas touched
<modelx/models.py | dashboard/server.py | types.ts | modelx/db.py DDL | none>

### Lessons applied
<lesson reference or "none applicable">

### Risks
- <risk> → <mitigation>

### Plan
1. <step>
2. <step>
```

**STOP. Wait for explicit approval** before touching any file. Preflight is a read-only phase.
