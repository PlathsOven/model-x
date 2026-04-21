---
description: Structural audit — check for accidental complexity before refactoring or rewriting a subsystem
---

## /logic-audit — Logical Simplicity Audit

A 6-step structural review that asks "is this the simplest shape that satisfies the requirement?" before any refactor, rewrite, or third debugging attempt. Logic audits catch **accidental complexity** — layers, indirection, and state that exist because of how the code was written, not because the problem demanded them.

**When to run:**
- Before a `/refactor` session (Phase 0)
- After 2 failed fix attempts in `/debug`, before a third
- When a reviewer says "this feels too complicated"
- When reading an area and you cannot build a mental model of it in 5 minutes

---

### 1. Draw the Data Flow

Starting from the entry point (CLI invocation, HTTP request, cycle tick), trace every transformation the data undergoes until it reaches its destination (DB write, printed table, dashboard JSON response). Draw it as a linear pipeline:

```
input → [step 1: what] → [step 2: what] → ... → output
```

For each step, note:
- **What shape** the data has (dataclass? dict? SQL row tuple? TS interface?)
- **Which file** owns that step
- **What function** performs the transformation

If you cannot draw this flow from memory after reading the code, that itself is a finding — the code is too entangled.

### 2. Count the Abstractions

List every abstraction layer the data crosses: classes, interfaces, wrappers, adapters, managers, factories, decorators, middleware, context providers.

For each layer, ask:
- **What does it add?** (Type safety? Retry logic? Caching? Logging? Separation of concerns?)
- **Would the code break without it?** (If the layer were inlined, would anything actually fail, or would the code just be shorter?)
- **Does it exist to satisfy a real constraint, or because it felt "professional"?**

Count the layers. Three layers for a phase handler is normal; seven is a smell.

### 3. Check for Accidental Complexity

Common sources in this codebase:
- [ ] **State that could be derived** — a field cached on a dataclass that could be computed from other fields on demand
- [ ] **Parallel data structures** — the same information stored in two shapes that must be kept in sync (e.g., an in-memory list and a DB row that drift apart)
- [ ] **Wrapping for wrapping's sake** — a function whose body is a single delegating call with no transformation. Inline it.
- [ ] **Generic parameters that are never varied** — a `T` or `TypeVar` that has exactly one actual type at every call site
- [ ] **Configuration knobs with one setting** — an option that's always the default, a strategy param always set to `"default"`
- [ ] **Intermediate DTOs** — a separate type that exists only to hand off data between two adjacent functions
- [ ] **Manager/Service/Handler chains** — a `FooManager` that calls `FooService` that calls `FooHandler`. Collapse if no layer branches.
- [ ] **Hand-rolled async plumbing** when `asyncio.gather` + a dataclass would do

### 4. Propose the Simplest Alternative

For the area under audit, sketch what the **smallest correct implementation** would look like. Do not worry yet whether the current code can be incrementally reshaped into this — just describe the target shape:
- How many files?
- How many functions?
- What are the types at the boundaries?
- What is the data flow in plain English?

The alternative must satisfy every **real** requirement (observable behavior, persistence, scoring correctness, resume-from-DB semantics, dashboard contracts). Drop anything that exists for hypothetical future requirements.

### 5. Identify the Root Design Decision

The current shape is usually the downstream consequence of one or two decisions made early on. Find them. Typical root decisions:
- "We'll use inheritance for X" → leads to deep class hierarchies that could be composition
- "We'll store state in a class attribute" → leads to mutation bugs that could be pure functions
- "We'll make this configurable" → leads to branches that are never exercised
- "We'll cache this in memory and also in the DB" → leads to staleness bugs

Name the decision. Name when it was made (commit, PR, or "inherited from vibe-code era"). Ask whether the reasoning still holds today.

### 6. Present Findings

Output a report structured as:

```
## Logic Audit: <area>

### Data flow
<linear pipeline diagram>

### Abstraction count
<N layers; list each with "adds: X" or "adds: nothing">

### Accidental complexity findings
- <finding 1> (severity: high | medium | low)
- <finding 2>
- ...

### Simplest alternative
<file count, function count, type shape, data flow>

### Root design decision
<the one or two calls that shape everything downstream>

### Recommendation
<hold the current shape | incremental simplification | full rewrite>
```

**STOP. Do NOT refactor.** Wait for human decision on which path to take. Logic audits are diagnostic; the fix is a separate action.
