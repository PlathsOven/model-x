---
description: Interview the user and write a complete feature spec to tasks/spec-<name>.md before any code is written
---

## /spec — Feature Specification Writer

For any feature that is bigger than a one-file change, write a spec before writing code. A good spec stops entire classes of rework: ambiguous requirements, missed edge cases, silent scope creep, and "the user actually wanted something different" surprises.

The spec lives at `tasks/spec-<feature-name>.md` and is the source of truth until the feature ships. `/implement` may reference it; `/review` will check the final code against it.

---

### 1. Interview the User

Ask one round of focused questions. Do not assume — the whole point of the spec phase is to surface assumptions before they harden into code. Cover:

**User stories**
- Who is the user for this feature? (Operator running the exchange? Agent author? Dashboard viewer? Human CLI participant?)
- What job are they trying to do?
- What would they do today without this feature? (The current workaround tells you what the feature is replacing.)

**Acceptance criteria**
- What observable behavior proves the feature works? (A new CLI flag? A new dashboard view? A new scoring column? A new agent prompt section?)
- What is the success metric after it ships?

**Edge cases**
- What happens when OpenRouter errors / times out / returns malformed JSON?
- What happens when the SQLite db is missing, empty, or has a partial cycle from a prior `Ctrl-C`?
- What happens when two markets settle on the same tick?
- What happens with `model: human` in live mode (not supported today — does this feature change that)?
- What happens when position limit partial-fills collide with pro-rata allocation?

**Performance**
- Is this on the tick hot path (every `phase_duration_seconds`)?
- Is this during settlement (one-shot per market)?
- What is the expected number of concurrent markets / agents / cycles?

**Security**
- Does this touch the OpenRouter API key handling?
- Does this change what ends up in `episode_traces.json` (which is meant to be shareable)?

**Integration points**
- Which lanes does it cross? (`modelx/`, `dashboard/server.py`, `dashboard/frontend/`)
- Does it require a dataclass change in `modelx/models.py` or an interface change in `dashboard/frontend/src/types.ts`?
- Does it require a SQLite schema change in `modelx/db.py`?
- Does it introduce a new external dependency (added to `requirements.txt` / `dashboard/frontend/package.json`)?

**Out of scope**
- What have we explicitly decided NOT to build in this pass?
- What is the "phase 2" that will tempt someone to pre-build?

Ask all questions up front in one batch. Then wait for answers before writing anything.

### 2. Write the Spec

Create `tasks/spec-<feature-name>.md` with this structure:

```markdown
# Spec: <feature name>

## Overview
<2–3 sentences: what and why>

## Requirements
### User stories
- As a <persona>, I want <action>, so that <outcome>

### Acceptance criteria
- [ ] <observable behavior 1>
- [ ] <observable behavior 2>

### Performance
- <latency / throughput / size target>

### Security
- <API key / trace / PII constraints>

## Technical Approach
<1–2 paragraphs: the chosen implementation path. Name the data flow. Reference the cycle lifecycle phase if applicable.>

### Data shape changes
- `modelx/models.py`: <new / changed dataclasses>
- `modelx/db.py`: <new tables / columns / migrations>
- `dashboard/server.py`: <new endpoints / changed response shapes>
- `dashboard/frontend/src/types.ts`: <new / changed interfaces>
- Dataclass + serialization + TS must stay in sync. Python dataclass is upstream.

### Files to create
- `<path>` — <purpose>

### Files to modify
- `<path>` — <what changes>

## Test Cases
- <happy path scenario>
- <edge case: empty state>
- <edge case: OpenRouter failure>
- <edge case: resume from DB>
- <edge case: position limit / pro-rata, if applicable>

## Out of Scope
- <thing 1 and why>
- <thing 2 and why>
```

### 3. Present & Confirm

Show the spec to the user. Ask:
- Are the acceptance criteria complete?
- Did I miss an edge case?
- Is anything in "Out of Scope" actually in scope?

Iterate until the user approves. Only after approval should `/implement` be invoked against the spec.
