---
description: Code review audit for lane compliance, quality, conventions, user journey, and logical simplicity
---

## /review — Code Review

### 1. Context Load
Read `CLAUDE.md` and `docs/conventions.md` to restore the canonical patterns. Skim `docs/architecture.md` to confirm which lanes the change should have stayed within.

### 2. Scope
Identify the files/lane to review. This may be:
- A specific lane directory (e.g., `modelx/agents/`)
- A set of recently changed files
- Another agent's completed work
- A PR diff

### 3. 10-Point Audit Checklist

Walk every file in scope against all ten pillars. Be concrete — cite file paths and line numbers for every finding.

**1. Correctness**
- [ ] Does the code do what it claims?
- [ ] Are all documented acceptance criteria (from a spec, if one exists) satisfied?
- [ ] Are error paths handled, not just the happy path?
- [ ] For engine changes: does the matching / scoring math still agree with the definitions in `CLAUDE.md`?

**2. Architecture & Lane Compliance**
- [ ] All changes stay within the expected lane(s) (`modelx/`, `dashboard/`, `tests/`, root scripts)
- [ ] Cross-lane imports flow in the allowed direction (`dashboard/` reads `modelx/`, never the reverse)
- [ ] `dashboard/` remains read-only with respect to `modelx.db` — no writes from the server or frontend
- [ ] Data flows match the cycle lifecycle in `docs/architecture.md`

**3. Conventions**
- [ ] Patterns used match the established ones in `docs/conventions.md` (plain dataclasses, stdlib sqlite3, httpx for HTTP, async for fan-out, no ORMs, no Pydantic)
- [ ] Naming is consistent with existing codebase patterns
- [ ] No new dependencies without justification
- [ ] Commit messages follow conventional format

**4. User Journey**
- [ ] The change is grounded in a real persona or flow from `docs/user-journey.md`
- [ ] No raw stack traces surfaced to the operator's terminal — errors print as readable lines and the run continues where appropriate
- [ ] For dashboard changes: "waiting for data" / missing-state paths still render

**5. Security**
- [ ] No secrets committed (API keys, tokens, `.env` contents)
- [ ] OpenRouter API key read from env, never hardcoded
- [ ] No credentials in logs or traces (`episode_traces.json` should not leak the API key)
- [ ] Untrusted YAML from `agents.yaml` / `contracts.yaml` is parsed with a safe loader

**6. Performance**
- [ ] No blocking IO in async code paths (especially inside the supervisor tick loop)
- [ ] `asyncio.gather` used for per-agent fan-out, not sequential awaits
- [ ] DB reads in hot paths are indexed / bounded (not a full table scan on every tick)

**7. Logical Simplicity**
- [ ] The diff is the simplest shape that satisfies the requirement
- [ ] No accidental complexity (extra layers, wrappers, intermediate DTOs) — see `/logic-audit` §3 for the common smells
- [ ] No abstractions added for hypothetical future requirements
- [ ] If you cannot hold the change in your head after one read, flag it

**8. Slop Indicators**
- [ ] No hallucinated imports (every `from` target actually exists)
- [ ] No copy-paste artifacts (variable names that reference a different context)
- [ ] No commented-out code blocks
- [ ] No abandoned experiments or debug leftovers (`print("here")`, stray `breakpoint()`, `console.log`)
- [ ] No half-implemented features that are called but don't work

**9. Test Coverage**
- [ ] New logic in `modelx/` is covered by a pytest under `tests/`
- [ ] Edge cases exercised: empty orderbook, position limit partial fill, OpenRouter parse error, resume from DB mid-cycle, PENDING_SETTLEMENT transition
- [ ] Tests hit real sqlite + real (small) matching, not heavy mocking

**10. Symptom vs. Root Cause**
- [ ] Any bug fix in the diff fixes the root cause, not a downstream symptom
- [ ] If the diff has a "primary fix" and a "secondary fix," the secondary is interrogated — it's often the real cause
- [ ] No workarounds for behavior that should be corrected upstream
- [ ] No feature flags or compatibility shims added to avoid fixing the underlying problem

### 4. Doc Sync Check
Delegate to `/doc-sync` (or at minimum verify its checkpoints are clean):
- If new directories, dependencies, or cycle-lifecycle changes were introduced, `docs/architecture.md` has been updated
- If user-facing flow changed, `docs/user-journey.md` has been updated
- If component status transitioned, `docs/stack-status.md` has been updated
- If a new pattern appeared, `docs/conventions.md` has been updated or the pattern is flagged as drift

### 5. Report
Output a summary:
- **Pass** — no issues found across all 10 pillars
- **Issues** — list each with pillar number, file path, line, severity (blocker / major / minor), and recommended fix

### 6. Fix (if requested)
If the user approves fixes, apply them surgically and re-run the audit checklist on changed files only. Commit each category of fix as its own conventional commit.
