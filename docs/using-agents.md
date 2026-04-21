# Using Coding Agents on ModelX

This is the operator's playbook for coding this repo with the help of AI agents. Claude Code is the primary harness. The slash commands under `.claude/commands/` drive the workflow.

---

## 1. One-time setup

Do this once per machine. Skip to §2 if you've already done it.

1. **Clone the repo and install dependencies.**
   ```bash
   git clone <repo-url> model-x
   cd model-x
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   cd dashboard/frontend && npm install && cd ../..
   ```

2. **Create `.env` at the repo root** with at minimum:
   ```
   OPENROUTER_API_KEY=sk-or-v1-your-key
   ```

3. **Confirm the stack runs locally** before involving any agent:
   ```bash
   python3 run_live.py --contract contracts.yaml --agents agents.yaml --db modelx.db
   ```
   If it ticks through at least one MM + HF phase cleanly, you're good. If not, fix it before running an agent — agents cannot debug a broken bootstrap.

4. **Open the repo in Claude Code.** The agent auto-loads `CLAUDE.md`. Confirm the `.claude/commands/` slash commands appear in the palette.

---

## 2. Every session starts the same way

1. **Run `/kickoff` with your task description.** For example:
   ```
   /kickoff add a per-agent win-rate column to the MM scoring table
   ```
   You can also run `/kickoff` with no arguments and then describe the task when prompted.

2. **Wait for the plan.** `/kickoff` reads `tasks/todo.md`, `tasks/progress.md`, and the relevant source files, then outputs a structured plan:
   - **Goal** — one sentence
   - **Approach** — one paragraph
   - **Files to create/modify** — explicit paths
   - **Verification** — how you'll know it works
   - **Risks/open questions** — anything you need to answer

3. **Review the plan carefully.** This is the cheapest place to catch a misunderstanding. Ask clarifying questions, reject scope you don't want, or ask the agent to investigate further before committing to an approach.

4. **Approve explicitly.** Say `go` / `proceed` / `approved` only when the plan is what you actually want. A vague "sure" or silent pass-through is how scope creep happens.

---

## 3. Pick the right command after `/kickoff`

Once the plan is approved, run **one** of these commands. Ask yourself one question: *what kind of task is this?*

1. **New feature, multi-file** → `/spec` → (approve the spec) → `/implement`
2. **New feature or change, small (≤ 1–2 files)** → `/implement` directly
3. **Bug fix** → `/debug`
4. **Dashboard UI change that affects layout or interactions** → `/ui-design` → `/implement`
5. **Code quality / structural cleanup** → `/refactor`
6. **Reviewing a PR or another agent's work** → `/review`
7. **Periodic hygiene (dead code, unused imports, stale deps)** → `/cleanup`

### Support commands (auto-invoked — you rarely call these directly)

- **`/preflight`** — auto-invoked by `/implement` when the plan touches >3 files or crosses the dashboard API boundary. Loads schemas, maps blast radius, checks lessons. Read-only.
- **`/logic-audit`** — auto-invoked by `/debug` after 2 failed fix attempts, and by `/refactor` as Phase 0. Structural review that ends with findings; never modifies code.
- **`/doc-sync`** — auto-invoked at the end of `/implement` and `/refactor`. Walks every context doc and updates what changed.

You *can* call them manually if you want.

**Rule of thumb:** always `/kickoff` first, then pick one primary command. Don't jump straight into `/implement` or `/debug` without a plan.

---

## 4. What happens during the work

1. **Read every diff the agent proposes** before it lands. Don't rubber-stamp — if a change looks wrong, say so.

2. **Watch for scope creep.** If the agent starts editing files outside the approved plan, stop it. Ask why. If the reason is legitimate, approve the new scope explicitly; if not, revert.

3. **Run verification yourself at the end of the turn:**
   ```bash
   python3 -m compileall modelx/ dashboard/ -q
   python3 -m pytest tests/ -x -q
   cd dashboard/frontend && npx tsc --noEmit
   ```
   If any fail, the agent should fix them before proposing a commit.

4. **If the agent gets stuck:**
   - **Bug fix failing 2+ times in a row:** tell the agent to run `/logic-audit`. Two failed fixes almost always means the bug is structural and surface patches won't hold.
   - **Plan no longer matches reality:** interrupt, describe what you see, ask for a revised plan.

5. **Agent asks for approval on a non-trivial decision:** give a direct answer. "Yes / no / use option B" beats "up to you" — agents trained to defer will stall waiting for a signal.

---

## 5. Verify and commit

The agent will propose commits with explicit `git add <files>` and a conventional commit message. **You approve each commit individually.**

1. **Read the commit message.** It should describe *why*, not just *what*. If it's vague ("update files"), ask for a better one.

2. **Read the file list.** The agent stages specific paths. If anything unexpected is in the list, ask why.

3. **Run `git diff --cached`** yourself if you want an extra safety check before approving.

4. **Say `commit`** to execute the commit.

5. **Never push unless you explicitly ask.** The harness is configured to never auto-push.

6. **Commit messages use conventional prefixes:** `feat:`, `fix:`, `chore:`, `refactor:`, `docs:`, `test:`.

**Never allow `git add .` or `git add -A`.** Only explicit paths. This prevents `.env`, `modelx.db`, `episode_traces.json`, or build artifacts from leaking into a commit.

---

## 6. Between sessions

Three files hold session-crossing state. Know them.

- **`tasks/todo.md`** — active work tracker. Three sections: In Progress, Completed This Session, Blocked. Agents read this at `/kickoff` to restore context.
- **`tasks/progress.md`** — mid-task handoff. If a session ends before a task finishes, the agent writes a handoff note here so the next session can resume.
- **`tasks/lessons.md`** — the self-improvement log. Every time an agent makes a mistake and you correct it (or an approach succeeds in a non-obvious way), the lesson lands here.

At the end of a productive session, skim `tasks/todo.md`. If anything's out of sync, fix it before closing the session.

---

## 7. When things go wrong

| Symptom | Cause / Fix |
|---|---|
| `/debug` keeps failing on the same bug | Invoke `/logic-audit`. Two failed fixes = structural problem. Surface patches won't hold. |
| Dataclass and TypeScript interface have drifted | `modelx/models.py` (via `dashboard/server.py` serialization) is upstream. Update `dashboard/frontend/src/types.ts` to match. |
| `modelx.db` is locked / in use | `run_live.py` is writing it. SQLite readers (dashboard) cope; other writers should not exist. |
| Dashboard shows stale data after a change | Mtime didn't advance or the 2s poll hasn't fired. Click "Reload data" in the sidebar to force a refetch. |
| OpenRouter parses fail for every cycle | Check the model id in `agents.yaml` — some model ids require the `/chat/completions` route, others don't. Pull the raw response from `episode_traces.json` and adjust the parser or the prompt. |
| Agent proposes to add a new dependency | Pause. Check `requirements.txt` / `dashboard/requirements.txt` / `dashboard/frontend/package.json` — does something already cover the need? If not, justify the new dep explicitly before approving. |
| Agent hallucinates an import or function that doesn't exist | Grep for the target before running the change. If the agent keeps doing this in one area, run `/cleanup` on that area to flag all hallucinated references at once. |
| `tasks/progress.md` handoff is stale or wrong | Delete it. Stale handoffs are worse than no handoffs — the next session will act on the wrong context. |
| Schema change in `modelx/db.py` breaks an existing `modelx.db` | Expected — `modelx.db` is disposable. Back it up first if you care, then wipe and restart. A migration story lives only if we add one. |

---

## 8. The five things that matter most

If you forget this document, remember these five rules:

1. **Always `/kickoff` first.** Never jump into `/implement` or `/debug` without a plan.
2. **Review every diff, approve every commit.** Agents are fast; you are the brake.
3. **Never `git add .`** — always explicit paths. Never push without asking.
4. **The dashboard is read-only.** It reads `modelx.db`; it never writes to it.
5. **Write lessons down.** Every correction becomes a line in `tasks/lessons.md`. That's how the agent gets smarter on your specific codebase.

---

## Reference

- `CLAUDE.md` — the rules the agents read (you should read it too)
- `docs/architecture.md` — component map, cycle lifecycle, Key Files table
- `docs/conventions.md` — patterns used, patterns avoided, schema source-of-truth
- `docs/decisions.md` — append-only decision log
- `docs/user-journey.md` — operator + agent-author + human + dashboard-viewer personas
- `docs/product.md` — the "why" behind ModelX
- `docs/stack-status.md` — PROD / MOCK / STUB / OFF per component
- `tasks/todo.md`, `tasks/progress.md`, `tasks/lessons.md` — session-crossing state
- `.claude/commands/` — the 11 slash commands
