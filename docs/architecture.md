# Architecture

## Layers

```
                 ┌──────────────────────────────────────────┐
   agents ──────►│  mcp/        tool surface + schemas       │
                 └────────────────────┬─────────────────────┘
                                      │  handles, never prose
        ┌─────────────────────────────┼─────────────────────────────┐
        │                             │                             │
   ┌────▼─────┐                ┌──────▼──────┐              ┌───────▼──────┐
   │  plan/   │                │   lease/    │              │   route/     │
   │  DAG     │                │  state      │              │  ambiguity   │
   │  validate│                │  machine    │              │  router      │
   └────┬─────┘                └──────┬──────┘              └───────┬──────┘
        │                             │                             │
        └──────────────┬──────────────┴──────────────┬──────────────┘
                       │                             │
              ┌────────▼────────┐           ┌────────▼────────┐
              │    board/       │           │    reduce/      │
              │  keys, entries  │◄─────────►│  projections    │
              │  journal, seq   │           │  tier 0..3      │
              └────────┬────────┘           └────────▲────────┘
                       │                             │
              ┌────────▼────────┐           ┌────────┴────────┐
              │    store/       │           │     cache/      │
              │  CAS + GC       │           │  scoped cache   │
              └─────────────────┘           └─────────────────┘
                       │                             │
              ┌────────▼────────┐           ┌────────┴────────┐
              │    trace/       │           │   sandbox/      │
              │  replay, metrics│           │  wasm isolate   │
              └─────────────────┘           └─────────────────┘
```

## Invariant map

| Invariant | Owner | Proof |
|---|---|---|
| I1 no full context crosses a boundary | `mcp/schemas.py`, `mcp/tools.py` | spawn payload test (§14) |
| I2 immutability | `store/blobstore.py`, `types.py` | content-address test |
| I3 exclusive writes, non-blocking reads | `lease/manager.py` | lease state machine tests |
| I4 scoped invalidation | `cache/invalidation.py` | stale-entry test |
| I5 deterministic nodes call no model | `route/router.py` | model-call counter in bench |
| I6 budget degrades the tier | `reduce/tiers.py` | budget exhaustion test |
| I7 byte-identical replay | `reduce/projection.py`, `trace/replay.py` | replay determinism test |
| I8 no network egress from the sandbox | `sandbox/isolate.py` | capability test |
| I9 total order on writes | `board/sequence.py` | concurrent-write test |
| I10 explainable ambiguity | `plan/ambiguity.py`, `types.py` | missing-reason rejection test |

## Data flow of one task

1. `intent` materialises a DAG: nodes are `deterministic` unless the plan
   builder cannot decide the output, in which case the node is `ambiguous` with
   a score and a reason (I10).
2. `manifest` hands over a compact branch description; `project` renders the
   state at the requested tier.
3. `claim` takes an exclusive lease, or returns the handle of a result already
   in flight — the deduplication path that keeps concurrent identical lookups at
   one execution.
4. The agent works outside the server, then `publish` writes the blob, assigns
   the next sequence number for the key, and declares the DAG edges the write
   depends on.
5. Those edges define the invalidation scope: derived projections, negative
   proofs whose scope intersects the key, and child entries are invalidated —
   by edge traversal, never by a global sweep.
6. `trace` returns the journal that reproduces the whole state byte for byte.
