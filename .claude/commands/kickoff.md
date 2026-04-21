---
description: Session start — restore context, read relevant files, output a plan, pause for approval
---

## /kickoff — Session Start

Run at the top of any non-trivial session. Kickoff is the phase where you transition from "cold start" to "enough context to propose a plan." Never skip it; never start editing before it completes.

---

### 1. Read the Instruction Layer

- `CLAUDE.md` is auto-loaded, but re-read it explicitly here so the hard rules (market structure, matching rules, scoring definitions, surgical commits) are top-of-mind.

### 2. Restore Project State

- `tasks/todo.md` — what was in progress last session? What was blocked? What landed?
- `tasks/progress.md` — is there a mid-task handoff note to resume?
- `tasks/lessons.md` — skim the recent entries for anything relevant to the task you're about to start.

If `tasks/progress.md` has an unfinished handoff and the user's new task is different, ask explicitly whether to resume the handoff or park it.

### 3. Ground in the User's Experience

- `docs/user-journey.md` — every change ultimately lands in front of the operator running the exchange, the LLM agents quoting in it, or a human participant typing at the CLI. Re-read the persona and the flow the current task touches, so you can frame "what does the user see when this ships?"

### 4. Accept the Task

Read the user's task description carefully. Restate it in one sentence to confirm understanding. Call out any ambiguity — don't infer. If the task is unclear, ask **one** round of clarifying questions before proceeding.

### 5. Read Relevant Source

Use the Key Files table in `docs/architecture.md` as a map to find the files the task will touch. Read them fully — do not skim. If a file you expect to exist is not in the table, grep for it and flag the table as stale in your plan output.

### 6. Output a Step-by-Step Plan

Structure the plan as:
- **Goal** — one sentence, the outcome the user will see
- **Approach** — one paragraph, the chosen strategy (not a survey of alternatives)
- **Files to create / modify** — explicit paths with one-line reasons
- **Verification** — how you will know it worked (compileall, pytest, frontend typecheck, manual smoke test)
- **Risks / open questions** — anything that would change the plan if answered differently

If the task touches >3 files or crosses the dashboard API boundary (between `dashboard/server.py` and `dashboard/frontend/src/types.ts`), delegate to `/preflight` before proposing the plan.

### 7. Pause for Approval

**Do not write code.** Wait for the user to approve the plan explicitly, or to request changes. If the user says "go" or similar unambiguous approval, proceed to `/implement` (or `/debug` / `/refactor` as appropriate).

If the plan is rejected, revise and re-present. Do not treat "no" as a request to simplify silently — ask what changed.
