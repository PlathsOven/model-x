# Agentic Coding Setup Checklist

**What to put in your codebase so an LLM agent can work effectively.**
Distilled from the Agentic Coding Playbook and adapted for ModelX.

---

## 1. Agent Instructions File

**File:** `CLAUDE.md` (root of repo)

The single file loaded into every agent session. Keep it **under 100 lines**. Contains:

- **Build & test commands** — exact CLI for compile, test, typecheck
- **Code style rules** — import style, error handling pattern, formatting conventions
- **Architecture summary** — lane names + dependency direction (one sentence), with file path pointers to deeper docs
- **Workflow rules** — commit message format, what to preserve on compaction
- **Known gotchas** — project-specific traps the agent would hit without being told (OpenRouter parse failures, resume-from-DB constraints, dashboard read-only rule)
- **Debugging directive** — "fix root cause, not symptoms" with examples

**Exclude:** exhaustive API docs, anything the agent already does correctly unprompted. Every line must prevent a specific mistake.

**Format:** Bullet points. When prohibiting something, always state the alternative. Link to deeper docs by file path, never inline them.

---

## 2. Context Documents

Structured docs the agent reads on demand.

### `docs/architecture.md`
- System overview (one paragraph)
- Component map (lane → responsibility, one line each)
- Data flow (the cycle lifecycle from ingest to settlement)
- Key design decisions (decision + rationale)
- Boundaries & contracts (dataclass source of truth, dashboard API mirror, SQLite schema)

### `docs/conventions.md`
- File organization rules (one concept per file, 300-line target)
- Patterns used (dataclasses, stdlib sqlite3, httpx, asyncio.gather)
- Patterns avoided (Pydantic, ORMs, `requests`, sync IO in async, writes from dashboard)
- Commit message format + build/test commands

### `docs/decisions.md` (append-only log)
- Each entry: date, context, decision, rationale, consequences
- Never edited, only appended

### `docs/user-journey.md`
- Personas (operator, agent author, human participant, dashboard viewer)
- Core flows (run, settle, inspect, add agent, play as human)
- Invariants (`Ctrl-C` safety, parse-error tolerance, dashboard read-only, API key never leaks)
- Edge cases

### `docs/product.md`
- The "why" — what ModelX measures, two roles, cycle shape, scoring intuition

### `docs/stack-status.md`
- Component registry with `PROD` / `MOCK` / `STUB` / `OFF` per file
- Connection map diagram
- MOCK → PROD upgrade path

### `docs/using-agents.md`
- Operator playbook for using Claude Code on this repo

---

## 3. Task Tracking Documents

Files the agent reads **and writes** to maintain state across sessions.

### `tasks/todo.md`
- In-progress items with sub-task checkboxes
- Completed this session
- Blocked items with reason

### `tasks/lessons.md`
- Date-stamped entries of mistakes and corrections
- Agent updates this after every mistake — the self-improvement loop
- Prune entries when the code they reference is refactored away

### `tasks/progress.md` (when mid-task)
- Handoff note: goal, approach, steps done, current status, blockers
- Written by agent at session end so a fresh session can pick up cold

---

## 4. Directory & File Structure

Structural rules that make agents dramatically more effective:

- **300-line target per file** — beyond this, agents lose track and make conflicting edits
- **Flat-ish structure, one concept per file** — agents navigate by filename
- **Single-package project** — engine in `modelx/`, dashboard in `dashboard/`, tests colocated by subsystem
- **Dataclasses in one place** — `modelx/models.py` is the single source of truth for engine shapes
- **Colocated tests** — `tests/test_<subsystem>.py` next to the subsystem

### Recommended Layout (ModelX)

```
model-x/
├── CLAUDE.md                         # Agent instructions (< 100 lines)
├── README.md                         # Operator's guide
├── docs/
│   ├── architecture.md               # System map
│   ├── conventions.md                # Coding patterns
│   ├── decisions.md                  # Decision log (append-only)
│   ├── product.md                    # Product theory
│   ├── user-journey.md               # Personas + flows
│   ├── stack-status.md               # PROD/MOCK/STUB/OFF registry
│   ├── using-agents.md               # Operator playbook for AI coding
│   └── agentic-setup-checklist.md    # This file
├── tasks/
│   ├── todo.md                       # Active work
│   ├── lessons.md                    # Mistake log
│   └── progress.md                   # Mid-task handoff notes
├── modelx/
│   ├── models.py                     # Dataclasses (source of truth)
│   ├── db.py                         # SQLite persistence
│   ├── matching.py                   # Matching rules
│   ├── scoring.py                    # Metrics
│   ├── phase.py                      # Phase orchestration
│   ├── market_runner.py              # Per-market loop
│   ├── supervisor.py                 # Global tick
│   ├── config.py                     # YAML parsing + configs
│   ├── news.py                       # News + price ingest
│   └── agents/                       # Agent implementations
├── dashboard/
│   ├── server.py                     # FastAPI read-only backend
│   └── frontend/                     # React + TS + Vite + Tailwind
├── tests/
│   └── test_*.py                     # Colocated by subsystem
├── run_live.py                       # Live runner
├── settle.py                         # Settlement CLI
├── agents.yaml                       # Participant roster
├── contracts.yaml                    # Market definitions
└── .claude/
    ├── commands/                     # Custom slash commands
    └── settings.json                 # Hooks config (optional)
```

---

## 5. Types & Schemas as Source of Truth

- **Engine shapes:** `modelx/models.py` dataclasses. Every persisted object flows through these.
- **Wire shapes:** `dashboard/server.py` response dicts, mirrored by `dashboard/frontend/src/types.ts`.
- **SQLite schema:** all DDL in `modelx/db.py`.

Rule for agents: *"All engine data shapes are defined in `modelx/models.py`. Read it before modifying any feature."*

---

## 6. Operator's Guide

**File:** `README.md`

Must contain copy-pasteable instructions for a Python-fluent operator:

- **Quick Start** — prerequisites (exact versions), clone, install, configure (`.env`), run (one command), verify (how to confirm it works)
- **Configs** — `agents.yaml` and `contracts.yaml` field references
- **Live mode vs. settlement** — how and when to run each
- **Dashboard launch** — backend + frontend commands
- **Troubleshooting** — OpenRouter errors, missing keys, malformed YAML, dead tickers

Every command must be copy-pasteable.

---

## 7. Custom Commands & Workflows

Reusable agent workflows stored as files, not copy-pasted prompts.

| Command | Purpose | Location |
|---------|---------|----------|
| **kickoff** | Session start: load context → plan → pause for approval | `.claude/commands/` |
| **implement** | Feature build: plan → execute → self-review → doc sync | Same |
| **debug** | Bug fix: reproduce → isolate → fix → doc sync | Same |
| **refactor** | Cleanup: logic audit → inventory → audit → fix → doc sync | Same |
| **review** | Code review: correctness, architecture, UX, simplicity | Same |
| **spec** | Feature spec: interview → structured specification | Same |
| **cleanup** | Hygiene sweep: dead code, imports, consistency | Same |
| **doc-sync** | Update all context docs to match reality | Same |
| **preflight** | Pre-change check: read schemas, architecture, conventions, identify risks | Same |
| **logic-audit** | Force structural reasoning before code changes | Same |
| **ui-design** | Dashboard UI design workflow grounded in personas | Same |

---

## 8. Hooks & Automated Guardrails (optional)

Configurable in `.claude/settings.json`:

- **Post-edit hook** — auto-format written files (when a formatter is adopted)
- **Pre-commit hook** — compileall + pytest + frontend typecheck
- **Sensitive-path guard** — block writes to `.env`, `modelx.db`, `episode_traces.json`

Not yet configured in this repo — a follow-up task.

---

## 9. Component Registry

**File:** `docs/stack-status.md`

Tracks the status of every component: **PROD / MOCK / STUB / OFF**. Updated by the agent via `/doc-sync`.

---

## 10. Doc Sync Protocol (The Feedback Loop)

The mechanism that keeps everything above accurate. Runs at the end of **every** workflow. See `.claude/commands/doc-sync.md` for the full checklist.

---

## Summary: The Minimum Viable Setup

If you're starting from zero, set up in this priority order:

1. **Instructions file** (`CLAUDE.md`, < 100 lines)
2. **Architecture doc** (`docs/architecture.md`)
3. **Dataclasses in one place** (`modelx/models.py`)
4. **300-line target per file**
5. **Task tracking** (`tasks/todo.md`, `tasks/lessons.md`)
6. **Kickoff workflow** (`/kickoff`)
7. **Doc-sync workflow** (`/doc-sync`)
8. **Operator's guide** (`README.md`)
9. **Conventions doc** (`docs/conventions.md`)
10. **Hooks** (post-edit formatter, pre-commit typecheck) — optional
