# DECISIONS

Every decision the mission specification did not settle, with the options that
were rejected and the reason. The governing rule is §1 of the specification:
**compute where it is deterministic, infer where it is ambiguous** — and, for
every choice left open, prefer the option that minimises the number of tokens
crossing an agent boundary.

Status values: `accepted`, `superseded`, `open`.

---

## D1 — Implementation stack: Python 3.12–3.14

**Status:** accepted · **Date:** 2026-11-04

The specification mandates TypeScript strict on Node 22 (§5, §18). The product
directive of the same date re-frames KYRX as a shipped product rather than a
local prototype, and moves the implementation to Python 3.12–3.14.

*Rejected:* TypeScript/Node 22 — a product ships as wheels and console scripts,
not as a Node bundle; and V8 offers no fuel metering, so the per-node CPU quota
of §12 could not be enforced (see D3).

*Rejected:* Go — no first-class MCP Python/Go parity for the schema layer, and
the tool contract is defined by the wire format, not the language.

*Token impact:* none at runtime. The wire contract of the ten tools is
unchanged, so a TypeScript client and a Python client interoperate.

*Consequence:* "strict, no `any`" becomes `mypy --strict` with
`disallow_any_explicit = true`; "noUncheckedIndexedAccess" becomes
`no_implicit_optional` plus explicit `Mapping` lookups.

---

## D2 — Content addressing: the `blake3` distribution

**Status:** accepted · **Date:** 2026-11-04

`blake3` (Rust, prebuilt wheels for CPython 3.12–3.14) is used for content
addressing.

*Rejected:* hand-rolled BLAKE3 in Python — a product cannot ship a home-grown
hash; the audit cost alone disqualifies it.
*Rejected:* `blake3-wasm` — needs a JavaScript runtime next to the Python one.

*Token impact:* one hash call per artifact write, no model traffic.

---

## D3 — Execution sandbox: wasmtime

**Status:** accepted · **Date:** 2026-11-04

Sandboxed execution runs on `wasmtime` (50.x): a real WASM isolate with fuel
metering, an epoch-based hard deadline and a memory limiter.

*Rejected:* `node:vm` / `worker_threads` — no fuel metering, so the CPU quota
of §12 would have been an unenforceable timeout, and the file/network
capability set is not structurally closed.
*Rejected:* WASI — it reintroduces filesystem and socket preopens, which is
exactly the egress §8 and I8 forbid.

*Consequence:* the 30 s hard timeout and the per-node memory quota of §12 are
enforceable; the residual gap is that fuel accounting is approximate, which the
sandbox documents.

*Token impact:* none.

---

## D4 — Transports: stdio and streamable HTTP

**Status:** accepted · **Date:** 2026-11-04

The server runs on stdio (local fleets) and on streamable HTTP (remote fleets),
the latter behind token verification when `KYRX_AUTH_MODE=token`.

*Rejected:* stdio only — not shippable to a fleet that is not on this host.
*Rejected:* SSE — superseded by streamable HTTP in the MCP specification.

*Open:* the OAuth provider story (`mcp.server.auth`) is deferred to the
product-hardening phase; token mode ships first.

*Token impact:* none on the tool contract.

---

## D5 — Token accounting: a character-based estimator

**Status:** accepted · **Date:** 2026-11-04

Token counts are estimated at `ceil(len(text) / 4)`.

*Rejected:* `tiktoken` — model-specific tables, heavy dependency, and the
plane is model-agnostic by design (§2).
*Rejected:* a vendored BPE — same objection, plus maintenance burden.

*Consequence:* the acceptance thresholds of §14 (85 % saving, 500-token spawn,
300-token verification) are **estimates**, and the bench report labels them as
such. Exact accounting requires a tokenizer plug-in behind the same interface.

*Token impact:* none — the estimator never leaves the server.

---

## D6 — Replay determinism: no wall-clock in projections

**Status:** accepted · **Date:** 2026-11-04

Projections exclude every wall-clock field: `Artifact.created_at`, lease
timestamps, journal write times. Timings live in the metrics channel, never in
the state.

*Rationale:* I7 demands byte-identical replay. A timestamp embedded in a
projection makes every replay differ.

*Token impact:* none.

---

## D7 — `Entry.artifact` is optional for tombstones

**Status:** accepted · **Date:** 2026-11-04

The specification types `Entry.artifact` as a mandatory handle. A tombstone by
definition points at nothing, so the handle is optional in that case and
mandatory otherwise.

*Rationale:* forcing a placeholder handle into tombstones would pollute the
reference count that the GC of §4 computes from live entries.

*Token impact:* none.

---

## D8 — Persistence: NDJSON journal plus blob files

**Status:** accepted · **Date:** 2026-11-04

The journal is an append-only NDJSON file; artifacts are stored as files under
`var/blobs/<prefix>/<hash>`; the index lives in memory.

*Rejected:* SQLite — write amplification and migrations on the hot path, for a
write pattern that is already serialised per key.
*Rejected:* PostgreSQL — infrastructure a single-process plane does not need
yet.

*Consequence:* the plane is single-process. Multi-process operation requires a
lock on the journal; see the open questions below.

*Token impact:* none.

---

## D9 — The model call is an injected interface

**Status:** accepted · **Date:** 2026-11-04

The ambiguity router depends on an `LLMClient` protocol, supplied by the
embedding product. No model runtime is vendored.

*Rationale:* §2 states the model call is an interface, not an implementation.
It also keeps the router testable without a network.

*Token impact:* none — the hint returns as a handle, never as injected prose.

---

## D10 — `manifest` returns a handle and node ids, not the full plan

**Status:** accepted · **Date:** 2026-11-04

`manifest` returns a manifest handle plus the node ids of the branch. Kinds,
statuses and ambiguity scores are fetched through `project(dagId, tier=1)`.

*Rationale:* the spawn budget of §14 (500 tokens) cannot carry per-node detail
for a 40-node branch **and** a summary. Splitting the two keeps the spawn
payload constant in size regardless of plan size.

*Cost:* one extra round trip per spawn.
*Gain:* the spawn payload shrinks, and tier selection stays the single
mechanism that controls how much prose crosses the boundary.

---

## D11 — `intent` is bimodal: the agent may submit the plan

**Status:** accepted · **Date:** 2026-11-04

`intent` accepts an optional `plan`. When the plan is present the server
materialises it **deterministically, with zero model call**; when it is absent
the server asks the injected model to decompose the free-form intent, then
validates and materialises the result.

*Rationale:* the agent usually holds the context the server cannot see, so the
agent can propose the decomposition; but the server must also serve agents that
only have an intent. §6 designates `intent` as the single model-facing entry
point, which is exactly where plan synthesis belongs.

*Rejected:* always calling the model — it would break I5 for callers that
already know their plan, and pay a model call to rebuild a structure the caller
already had.
*Rejected:* never calling the model — the free-form intent path would not
exist, and the router would have nothing to decompose.

*Consequence:* the model call inside `intent` is counted like any other, and a
plan that the model cannot decide becomes an `ambiguous` node carrying its score
and its reason (I10) rather than a silent guess.

---

## D12 — One write lock for the whole blackboard

**Status:** accepted · **Date:** 2026-11-04

All writes go through a single critical section that allocates the sequence
number and appends to the journal.

*Rationale:* I9 requires a total order on writes, and the journal is a single
append-only file, so the two orders must be the same order. One lock makes that
structural instead of a protocol between two locks.

*Rejected:* per-key locks plus a separate journal lock — two lock orders means
either a deadlock risk or a second ordering rule to prove.
*Rejected:* an optimistic compare-and-swap on the sequence — more machinery for
the same guarantee, and the failure mode is a retried write rather than a
correct one.

*Cost:* writes serialise. Reads never take the lock (I3), and writes are
publish events, not the hot path.

---

## D13 — Python 3.14 is the floor

**Status:** accepted · **Date:** 2026-11-05

The package requires `>=3.14,<3.15`. Python 3.12 was supported during the
skeleton phase and dropped when the product directive settled on 3.14.

*Rationale:* a premium product is built on one runtime, not five. Every
dependency (blake3, wasmtime, the MCP SDK, pydantic) ships 3.14 wheels, so
nothing is pinned to a source build.

*Consequence:* the development environment is a 3.14 virtual environment, and
the CI matrix targets 3.14 only.

---

## D14 — The plan is native: the agent declares steps, never a graph

**Status:** accepted · **Date:** 2026-11-05

`intent` takes an optional list of **steps**. A step says what it produces and
what it consumes. KYRX derives the dependencies, the order and the budgets
itself.

*Rejected:* letting the agent submit a graph with explicit dependencies — the
agent would have to think about scheduling, which is the server's job, and a
malformed graph would be the agent's error to prevent.

*Rationale:* the graph is an implementation detail of the plane. What the
agent knows is "this step needs that one"; what the server knows is how to
schedule it.

*Consequence:* the public tool surface has no graph vocabulary at all. When
the agent declares no step, the model proposes steps in the same native shape
— never in the shape of a graph.

*Token impact:* the plan costs one line per step, not one object per node and
one array per edge.

---

## D15 — Security that fails closed

**Status:** accepted · **Date:** 2026-11-05

- The HTTP transport refuses to start without a bearer token
  (`KYRX_AUTH_MODE=token`). A plane that holds a fleet's state is never
  exposed unauthenticated by accident.
- Tokens are compared in constant time, and the audience of a token is
  checked against the resource server URL.
- Every tool call is rate limited per actor and per tool.
- Every tool call is recorded in a **hash-chained** audit trail: each record
  carries the hash of the previous one, so a missing or altered record breaks
  the chain.
- Every input is bounded: intent, summary, step names, step count, key
  length, artifact size.

*Rationale:* a premium product is judged on what it refuses. The audit chain
answers "can the record be trusted", the rate limit answers "can one agent
drown the plane", and the caps answer "can one payload exhaust the disk".

*Token impact:* none. The security layer sits behind the tool surface and
never adds to a response.

---

## D16 — The economy is measured, not claimed

**Status:** accepted · **Date:** 2026-11-05

A token ledger records, for every response, the tokens served and the tokens
a naive fleet would have spent for the same information. The ledger is
persisted, exposed as the MCP resource `kyrx://economy` and printed by
`kyrx stats`.

*Rationale:* the product's argument is economic. An argument that is not
measured is a slogan.

*Consequence:* the ledger's persistence is **deferred by two seconds**.
Writing the file on every call put a disk write in the middle of the latency
path: the p95 claim latency was 88 ms with it, and 0,3 ms without it. The
economy must not be paid for by the economy.

*Token impact:* this decision is the one that protects the token budget: a
cached projection is served without recomputing it, and the scope of that
cache is the edges of the DAG, so a write invalidates exactly what it touches.

---

## D17 — A failed lease is over, an expired lease is stolen

**Status:** accepted · **Date:** 2026-11-05

The state machine of §7 keeps its eight transitions untouched. When a node's
lease fails, that lease is finished: a new lease cycle starts for the node
(`offered → claimed`). When a node's lease expires, the next claim **steals**
it (`expired → stolen`).

*Rationale:* the chaos suite found the hole: without this, a node whose agent
died was never worked on again. The fix keeps the eight documented transitions
and adds no transition: a new cycle is a new lease.

*Consequence:* an agent that claims an expired node takes it over, which is
the work stealing the specification asks for.

---

## D18 — One typed exception at the FFI boundary

**Status:** accepted · **Date:** 2026-11-05

`kyrx.sandbox.isolate` is the single module allowed to use `Any`, and only at
the wasmtime host-function boundary, which is dynamically typed by the SDK.
Every other module is `mypy --strict` with `disallow_any_explicit`.

*Rationale:* banning `Any` in the domain code is what keeps the contracts
honest; a foreign FFI is not domain code.

---

## Open questions

| # | Question | Blocks | Proposed default |
|---|---|---|---|
| Q1 | OAuth provider versus API key for remote fleets | product hardening | **done: bearer token, fail-closed**; OAuth later |
| Q2 | Persistence engine beyond NDJSON (SQLite, PostgreSQL) | multi-process operation | SQLite with WAL |
| Q3 | Telemetry: OpenTelemetry export versus local metrics only | observability | local metrics and ledger, OTEL optional |
| Q4 | Multi-process operation and journal locking | horizontal scale | single process until measured |
| Q5 | Licensing model and distribution channel | commercial launch | proprietary, direct download |
| Q6 | Host capability catalogue for the sandbox | richer reducers | one capability first, extend on demand |
