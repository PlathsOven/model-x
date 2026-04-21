# Decisions

Append-only log. When making a significant architectural or process choice, add a new entry. Never rewrite an old entry — add a new one that supersedes it and reference the predecessor.

Format per entry: **Date — Decision**. Then `Context:`, `Decision:`, `Rationale:`, `Consequences:`.

---

## 2026 — Single-process CLI architecture over a client/server split

**Context:** Prediction exchanges naturally suggest a server-plus-client shape. That adds ops burden (two deploys, WebSocket transport, auth), and ModelX's core value is comparing LLM agents — not serving real-time users.

**Decision:** One Python process (`run_live.py`) is the exchange. It owns the wall-clock tick, persists to a single SQLite file, and never exposes an external API. A separate read-only dashboard (`dashboard/`) inspects the SQLite file and the traces JSON — it is a debug surface, not a user-facing product.

**Rationale:** Operational simplicity. The operator can `Ctrl-C` at any time and restart; state rebuilds from the DB. Adding a new contract is "edit `contracts.yaml` and restart." There is no need for cross-machine state coordination when the whole system is one file + one process.

**Consequences:** No real-time multi-user experience. No streaming UI updates. The dashboard polls every 2s. Scaling to hundreds of simultaneous markets would eventually require a different architecture, but we are nowhere near that.

---

## 2026 — SQLite via stdlib, no ORM

**Context:** The engine needs to persist quotes, orders, fills, cycle state, and accounts for every market. Persistence is essential for resume-from-Ctrl-C and for the dashboard.

**Decision:** Use `sqlite3` from the standard library. No ORM, no query builder, no migrations framework. All DDL and CRUD helpers live in `modelx/db.py`. `modelx.db` is treated as disposable per-run state.

**Rationale:** SQLite is zero-ops, embedded, transactional, and blazingly fast for the scales involved. An ORM would add a dependency and an indirection layer for queries that are naturally expressed as 5-line SQL. Schema changes are rare enough that hand-written DDL is cheaper than a migration tool.

**Consequences:** Schema changes require explicit decisions about whether to wipe or migrate existing `modelx.db` files. The project default is "wipe" — per-run state is disposable. Long-lived deployments that want accumulated history need to handle that out of band.

---

## 2026 — Plain dataclasses over Pydantic

**Context:** Most Python web/data projects today reach for Pydantic at module boundaries. ModelX has no external API boundary — the only boundary that crosses out of Python is the dashboard JSON → `dashboard/frontend/src/types.ts`, and that is a one-way, hand-maintained mirror.

**Decision:** All engine data shapes are plain `@dataclass` in `modelx/models.py`. YAML configs parse with a safe loader and are trusted. LLM responses are parsed defensively — a parse failure skips that agent for that cycle, it does not abort.

**Rationale:** Pydantic's validation and magic are overkill for a project whose only input sources are (a) a YAML file the operator wrote and (b) LLM responses that we have to parse defensively regardless. Dataclasses are stdlib, zero-magic, and produce readable diffs.

**Consequences:** Developers can't rely on automatic validation at boundaries. The codebase instead relies on "parse defensively, skip and log on malformed input, keep running." The dashboard JSON contract is hand-maintained — when a dataclass field changes, both the server serializer and `types.ts` need updating in the same commit.

---

## 2026 — OpenRouter as the single LLM gateway

**Context:** The exchange needs to support heterogeneous LLMs from multiple providers (Anthropic, OpenAI, DeepSeek, Gemini, etc.). Per-provider SDKs would mean N client libraries, N auth schemes, N response shapes.

**Decision:** All LLM calls go through OpenRouter. `modelx/agents/openrouter.py` is the only HTTP client, using `httpx`. Any OpenRouter-supported model can be a participant by writing its id in `agents.yaml`.

**Rationale:** One auth key, one base URL, one response shape. If a provider is added or removed, OpenRouter handles it; ModelX code does not change. Rotating between models mid-experiment is just editing `agents.yaml`.

**Consequences:** Dependency on OpenRouter availability — if OpenRouter is down, no LLM agent can participate. OpenRouter takes a small fee over raw provider pricing; acceptable for this use case. Multiple rotating OpenRouter API keys are supported (recent commit) to spread rate-limit exposure.

---

## 2026 — `asyncio.gather` for concurrent agent fan-out

**Context:** Each phase asks every participating agent for a decision. Serializing would multiply latency by agent count; a 10-agent market on a 30-minute tick is trivial serially, but a 10-second tick is not.

**Decision:** Each phase builds per-agent contexts, then fans out LLM calls concurrently via `asyncio.gather`. One slow agent delays the phase close by the slowest call, not by the sum. Parse errors on one agent skip only that agent's decision; the phase completes.

**Rationale:** Python's cooperative concurrency is well-suited to "N concurrent outbound HTTP calls" and adds no process overhead. Exception handling inside `gather` is clean — per-task results are collected and inspected individually.

**Consequences:** Every agent-facing function in the critical path must be `async`. A blocking call inside an async function silently stalls the whole fan-out. Reviewers should flag blocking IO in async paths.

---

## 2026 — Manual settlement via a separate CLI

**Context:** The real-world values contracts settle against (CPI prints, earnings, closing prices) become known on external schedules. We cannot hard-code a settlement trigger in the run loop.

**Decision:** When a contract's `settlement_date` passes, the supervisor transitions it to `PENDING_SETTLEMENT` and stops advancing its cycles. Other contracts keep running. The operator runs `settle.py --market <id> --value <float>` when they have the real value; it writes the settlement value, computes all scores, persists lifetime stats, and prints the final tables.

**Rationale:** Decoupling settlement from the tick loop keeps the run loop simple and makes re-settling with corrected values straightforward (just run `settle.py` again). An operator can wait days between the settlement_date and actually knowing the value.

**Consequences:** The `PENDING_SETTLEMENT` state must be surfaced in the dashboard and respected by the supervisor. If the operator never runs `settle.py`, lifetime stats never populate. `--force` allows early settlement when needed.

---

## 2026 — Read-only dashboard with mtime-driven auto-reload

**Context:** The operator wants to inspect live markets without restarting anything. Common operator question: "why did the last cycle produce this fill?" — answerable from the DB and traces, not requiring engine state in memory.

**Decision:** `dashboard/server.py` is a FastAPI process that never writes to `modelx.db`. It checks `os.path.getmtime` on every request; if the mtime has advanced, it rebuilds `AppState` under a `threading.Lock`. The frontend polls `/api/episode` every 2 seconds and uses a `loaded_at` epoch field as `dataVersion` to invalidate per-view data fetches.

**Rationale:** No need for the engine and dashboard to talk directly. Mtime is cheap, race-free enough for single-writer / multi-reader, and lets the dashboard be arbitrarily restarted without affecting the engine. Polling is trivial compared to push, and 2s is faster than the operator can read a screen anyway.

**Consequences:** Brief races are possible (reload mid-DB-commit can see one fewer cycle); the next poll catches up. Very large DBs would make the rebuild expensive, but at that scale the whole architecture needs revisiting.

---

## 2026 — Multiple rotating OpenRouter API keys

**Context:** Heavy runs across many markets and agents can bump into per-key rate limits on popular models.

**Decision:** The OpenRouter client supports a rotating list of API keys, drawn from the environment. Each request picks from the pool.

**Rationale:** Horizontal scaling of rate-limit budget with no code change to agents or prompts. Keys are disposable at the OpenRouter account level.

**Consequences:** Key management becomes per-operator; the operator is responsible for revoking leaked keys. Traces never include the API key — only the model id and response. Review audit: grep for `OPENROUTER_API_KEY` in anything that lands in `episode_traces.json`.
