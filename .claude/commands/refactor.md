---
description: Architecture review and refactoring pass — canonicalize patterns, delete bloat, decompose, rename for legibility
---

## /refactor — Vibe-Code Rescue & Refactor

A structured workflow for taming codebases built primarily through AI-assisted "vibe coding." Vibe-coded repos accumulate a specific class of entropy — not bugs, but **pattern drift**: the same intent expressed N different ways because each section was generated in a separate conversation. This workflow detects that drift, converges on canonical patterns, and locks them in.

---

### 0. Philosophy

**Target reader:** a single operator who needs to understand every line after being away for a week. Every function must be legible to this person in under 30 seconds.

**Core principle:** Vibe-coded repos don't need more abstraction — they need *convergence* and *subtraction*. The goal is fewer patterns, fewer layers, fewer lines. When two approaches exist for the same thing, pick the simpler one and kill the other everywhere. When one approach exists for a thing that doesn't need doing, delete it entirely. **Every pass of /refactor must remove more LOC than it adds.**

**Refactoring priorities (strict order):**
1. **Canonicalize** — one pattern per intent, enforced everywhere
2. **Minimize** — delete / inline / collapse anything that doesn't earn its keep (unused exports, thin wrappers, single-callsite helpers, speculative parameters, duplicated logic, pass-through indirection)
3. **Decompose** — god files/functions → single-purpose modules (but only when splitting *reduces* complexity; don't decompose for its own sake)
4. **Name** — domain vocabulary in every identifier

---

### 1. Context Load

Read these files and internalize the constraints before touching anything:
- `CLAUDE.md` — market structure, scoring definitions, surgical commit discipline
- `docs/architecture.md` — component map, cycle lifecycle, Key Files table, boundaries
- `docs/conventions.md` — patterns used vs. avoided, file organization, commit format
- `docs/stack-status.md` — which components are PROD / MOCK / STUB / OFF

Hard constraints to hold in memory throughout:
- All changes must stay within your approved lane (`modelx/`, `dashboard/`, `tests/`, root scripts)
- Surgical commits only — never `git add .`
- The dashboard is read-only with respect to `modelx.db`

---

### 2. Scope

Ask the user:
- **Full codebase** — default
- **Single lane** (e.g., `modelx/`, `modelx/agents/`, `dashboard/frontend/`)
- **Specific files**

---

### 3. Inventory — Map Before You Cut

Before auditing code quality, build a structural map. This catches the macro-level rot that vibe coding produces.

#### 3.0 File Census

For the scope, list every file with:
- **Purpose** (one phrase)
- **Imports from** (which other project files it depends on)
- **Imported by** (which other project files depend on it)
- **Status**: ACTIVE (called in production flow) / ORPHAN (not imported anywhere) / DUPLICATE (>60% overlap with another file)

Output the census as a table. Flag:
- [ ] **Orphaned files** — files that nothing imports. Candidates for deletion.
- [ ] **Duplicate files** — files with substantially overlapping logic (common in vibe-coded repos where a feature was re-prompted from scratch instead of edited). Candidates for merge.
- [ ] **Circular dependencies** — A imports B imports A. Must be broken.
- [ ] **Wrong-layer files** — files that live in one directory but belong in another based on what they actually do (e.g., a "utility" that's really an agent prompt).

#### 3.1 Pattern Divergence Scan

The defining pathology of vibe code. Scan for multiple implementations of the same intent:

- [ ] **Multiple HTTP client patterns** — some files use `httpx`, others use `requests`, others use a custom wrapper. Pick `httpx` everywhere.
- [ ] **Multiple DB access patterns** — some files use a connection-per-call, others cache on a module global, others pass a connection explicitly. Converge on one.
- [ ] **Multiple dataclass serialization paths** — dataclass → dict done differently in each handler. Establish one helper.
- [ ] **Multiple error handling styles** — some files use try/except with print, some with logging, some swallow errors silently. Establish one pattern per layer.
- [ ] **Multiple ways to define the same type** — a TS interface in `types.ts`, a duplicate in a component file, a third in an API file. Single source of truth.
- [ ] **Inconsistent async patterns** — mixing `async/await`, `asyncio.run_in_executor`, and blocking calls inside async functions.
- [ ] **Franken-paradigms** — class-based agent wrappers next to function-based helpers, OOP services next to plain functions. Pick one per layer.

For each divergence found, propose the **canonical pattern** (the simplest one consistent with the stated stack — stdlib-first, dataclasses, httpx) and list every file that needs to converge.

---

### 4. Deep Audit — Code Quality

Now audit every file against these pillars. **Be ruthlessly critical.** Vibe-coded repos are full of "works but wrong" code — functional on the happy path, rotten underneath.

#### 4A. Dead Weight & Bloat

Every pass of /refactor must reduce LOC and indirection. Hunt aggressively for anything that can be deleted, inlined, or collapsed without changing behaviour. For each finding record `path:line | problem | action (delete / inline / collapse) | impact | effort | risk`.

- [ ] **Unused exports** — public functions/classes with zero importers across the project. Confirm via repo-wide grep of the symbol name before deleting.
- [ ] **Trivial wrappers** — functions whose entire body is `return otherFn(args)` or a single delegating call with no transformation. Inline the caller and delete the wrapper.
- [ ] **Single-callsite helpers** — functions called from exactly one place that inline in ≤10 lines. Prefer inline unless the abstraction barrier is load-bearing (>1 caller likely, crosses a public boundary, or is domain-named in a way that documents intent).
- [ ] **Abandoned experiments** — half-built features, commented-out blocks, functions never reached from any entry point. If it's not in the active call graph, delete it.
- [ ] **Hallucinated APIs** — calls to functions, methods, or library APIs that don't actually exist (LLMs hallucinate these). Grep for every import and verify the target exists.
- [ ] **Dead branches** — `if/else` paths that cannot trigger given the current call graph; legacy option handlers for options never set; fallback code for cases that can't occur.
- [ ] **Speculative parameters** — function params always passed the same value (or always omitted as a default) at every call site. Inspect every call; if never varied, remove the parameter.
- [ ] **Vestigial parameters** — accepted but never used inside the body. Remove from signature and all call sites.
- [ ] **Duplicate logic** — same computation / conditional / formatting expressed in 2+ places. Extract to one helper (or collapse to the existing one) and update callers.
- [ ] **Re-export / pass-through indirection** — modules whose only job is re-exports; classes that wrap a handful of static methods for no gain. Inline the consumer's import and delete the shim.
- [ ] **Over-typed dicts** — `dict[str, Any]` / `Record<string, unknown>` where a narrower typed shape exists or could easily be written. Tighten to a dataclass or narrower TS interface.
- [ ] **Stale TODO/FIXME** — no owner and no issue ticket → resolve or delete.
- [ ] **Copy-paste artifacts** — variable names that reference a different context (e.g., `user_list` in a file about agents) because the code was copy-pasted from another file.
- [ ] **Debug leftovers** — stray `print("here")`, `breakpoint()`, `console.log` statements in non-debug code paths.
- [ ] **Placeholder data** — "replace with real entries" scaffolding that never got replaced.

#### 4B. Modularity & File Hygiene

- [ ] **God files** (>300 lines doing multiple things) — split by responsibility
- [ ] **God functions** (>40 lines) — decompose into named sub-steps
- [ ] **One concept per file** — a matching module, an agent class, a db helper — each in its own file
- [ ] **Clean dependency direction** — `modelx/` is upstream of `dashboard/`; `dashboard/` must never be imported from `modelx/`
- [ ] **Shared types in one place** — Python: one `models.py` (`modelx/models.py`). TypeScript: one `types.ts` per feature area. No redefinitions.
- [ ] **Config separate from logic** — no hardcoded paths, intervals, thresholds, or magic numbers in logic files. Extract to a `config` or named constant.

#### 4C. Naming & Readability

Write for the operator who just came back from a week off.

- [ ] **Domain vocabulary in identifiers** — `mm_mark`, `residual_book`, `markout_5`, `position_limit` — NOT `x`, `tmp`, `val`, `data`, `result`
- [ ] **Verb-phrase function names** — `advance_phase()`, `match_mm_phase()`, `score_mm()`, `carry_mark_forward()` — NOT `process()`, `handle()`, `run()`, `doStuff()`
- [ ] **Flat control flow** — max 2 indentation levels inside a function. If deeper, extract a helper with a descriptive name.
- [ ] **No nested ternaries** — use `if/elif/else`
- [ ] **Type annotations** — Python: type hints on public function signatures. TypeScript: explicit return types on all exports. No `any`.
- [ ] **Named constants** — every magic number gets a `SCREAMING_SNAKE_CASE` name: `DEFAULT_POSITION_LIMIT`, `POLL_INTERVAL_MS`

#### 4D. TypeScript / React (dashboard frontend)

- [ ] **No prop drilling >2 levels** — lift state to a provider or use a context
- [ ] **No inline styles** — Tailwind utility classes only
- [ ] **Pure components** — side effects in hooks, not in render
- [ ] **Zero `any` types** — strict typing everywhere
- [ ] **View-local UI state stays local** — filters, sort order, selected cycle — use `useState`, not a shared store
- [ ] **`dataVersion` discipline** — data-fetch `useEffect` deps include `dataVersion`; UI state does not
- [ ] **No duplicate component variants** — if two components are >70% identical, parameterize one and delete the other

#### 4E. Python (engine + dashboard backend)

- [ ] **Plain dataclasses** at module boundaries — no Pydantic, no raw-dict contracts that should be typed
- [ ] **`async def`** for all IO-bound operations (HTTP, concurrent agent fan-out)
- [ ] **No module-level mutable globals** — except for intentional singletons (dashboard `AppState`) with clear init/rebuild
- [ ] **stdlib `sqlite3` only** — no ORM, no `sqlalchemy`, no query builder
- [ ] **httpx for HTTP** — never `requests`
- [ ] **No bare `except:`** — catch specific exceptions, log them, re-raise or return typed errors
- [ ] **YAML parsing** via a safe loader (`yaml.safe_load` or the built-in fallback) — never `yaml.load(...)` with the default loader

---

### 5. Report

Output a structured report grouped by audit section (3.0 → 4E):

```
[SECTION] <path>:<line>
  Problem:  <one line>
  Current:  <code snippet>
  Fix:      <refactored snippet or approach>
  Impact:   high | medium | low
  Effort:   trivial (<5 min) | moderate (5–30 min) | significant (>30 min)
```

End with:
- **Counts** per section
- **Canonical patterns elected** (from §3.1) — list each pattern choice with rationale
- **Top 10 highest-impact changes** ranked by Impact × (1 / Effort)
- **Execution order** — dependency-graph-aware: shared types first, then db, then engine, then runner, then dashboard server, then frontend
- **Projected LOC delta** — sum of `bytes saved` from §4A findings minus new code added by canonicalization / decomposition. Must be negative; if it isn't, the pass isn't net-lean and §4A needs another sweep before execution begins.
- **Things that LOOK like bloat but aren't** — ≤5 items where surface-level bloat has hidden value (real abstraction barriers, external API contracts, load-bearing indirection). Record these so the next /refactor pass doesn't re-flag them.

**STOP. Do NOT refactor yet. Wait for explicit approval of the report, canonical pattern choices, and execution order.**

---

### 6. Refactor Execution

After approval, work in this strict order:

#### Phase 0 — Logic Audit
Before cutting any code, invoke `/logic-audit` on the highest-impact area identified in §5. A structural simplification at the root often eliminates 50% of the downstream refactor work. If the audit reveals the current shape is already near-minimal, proceed to Phase 1. If it reveals a deeper redesign, pause and present the alternative to the human before continuing the refactor.

#### Phase 1 — Delete & Inline (§4A)
Delete / inline / collapse every §4A finding the report approved. This reduces surface for every subsequent phase. The LOC delta after this phase should be sharply negative; if it isn't, revisit §4A before moving on.

#### Phase 2 — Canonicalize Patterns (§3.1)
For each pattern divergence, apply the elected canonical pattern everywhere. This is the highest-leverage phase — it makes the codebase feel like one person wrote it.

#### Phase 3 — Decompose & Modularize (§4B)
Split god files, extract shared types, enforce dependency direction. Move misplaced files to correct directories.

#### Phase 4 — Rename & Annotate (§4C)
Apply domain naming, add type hints, extract magic numbers to named constants.

**Per-file procedure within each phase:**
1. Read the full file
2. Apply edits (batch all changes to one file in one pass)
3. If a function signature changed, grep for all call sites and update them atomically in the same phase
4. If a file was moved/renamed, update all imports in the same edit batch
5. After editing, re-read the file to confirm it's clean

**Execution rules:**
- If a refactor touches >5 files, pause after each file and verify syntax before continuing
- Preserve comments and docstrings unless provably wrong or stale
- If you discover a new issue mid-refactor that wasn't in the report, note it but do NOT fix it — it goes in the next `/refactor` cycle

---

### 7. Regression Check

After each phase (not just at the end):

```bash
python3 -m compileall modelx/ dashboard/ -q
python3 -m pytest tests/ -x -q
cd dashboard/frontend && npx tsc --noEmit
```

Fix any errors before moving to the next phase. If a fix would be non-trivial, revert the breaking edit and flag it for the user.

---

### 8. Doc Sync

Delegate to `/doc-sync`. Refactors frequently touch:
- `docs/architecture.md` — file locations, Key Files table, cycle lifecycle
- `docs/conventions.md` — newly canonicalized patterns (add), deprecated patterns (remove)
- `docs/stack-status.md` — component status transitions
- `tasks/lessons.md` — add any lesson surfaced by the audit
- `CLAUDE.md` — only if a load-bearing rule changed

Skip categories where nothing changed.

---

### 9. Commit

One surgical commit per phase, using native git only:

```bash
git add <files...>
git commit -m "refactor(phase1): delete & inline — orphans, thin wrappers, speculative params"

git add <files...>
git commit -m "refactor(phase2): canonicalize patterns — <list elected patterns>"

git add <files...>
git commit -m "refactor(phase3): decompose god files, extract shared types"

git add <files...>
git commit -m "refactor(phase4): rename to domain vocabulary, add type hints"
```

Never `git add .`. Never `--no-verify`. Never push unless explicitly asked. Wait for approval before executing each commit.
