# Changelog

All notable changes to KYRX are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-11-15

### Added
- MCP client integration: `kyrx init <client>` (6 clients), `kyrx token`,
  HTTP transport with Bearer target, `docs/integration.md`.
- Automated e2e tests (`tests/test_e2e.py`): stdio and HTTP subprocess runs
  covering the full tool cycle and the auth 401/401/200 path.
- KYRX errors now surface to MCP clients as structured messages
  (`[validation_error] ...`, `[not_found] ...`) instead of the opaque
  `Error executing tool X`.

### Fixed
- **HTTP auth**: a valid Bearer token was rejected with 401.
  `BearerTokenVerifier` did not set `AccessToken.resource`, which the SDK
  compares against the server URL per RFC 8707. Regression test added.
- Malformed base64 in `inline:` artifact refs now raises a clean
  `ValidationError` instead of a raw `binascii.Error`.
- `mypy --strict`: 0 errors (was 3 preexisting + 1 introduced by the
  optional `tiktoken` dependency, now silenced at the guarded import).

## [0.2.0] - 2026-11-05

### Added
- **Native planner**: `intent` takes steps, not a graph. The server derives
  the dependencies, the order and the budgets.
- **Security**: bearer authentication that fails closed on the HTTP
  transport, constant-time comparison, audience validation, per-actor rate
  limiting, hash-chained audit trail, and bounded inputs everywhere.
- **Economy**: a token ledger measuring what the plane served against what a
  naive fleet would have spent, exposed as `kyrx://economy` and `kyrx stats`.
- **Projection cache**: a projection is served from the cache and invalidated
  by the DAG edges of the write that touches it.
- **Adversarial and chaos suites**: an adversarial agent and eight concurrent
  agents with random failures, proving the journal stays replayable.
- MCP resources `kyrx://economy` and `kyrx://plans`.

### Changed
- Python **3.14** is now the floor; the development environment was rebuilt.
- The lease state machine keeps its eight documented transitions: a failed
  lease is finished and a new cycle starts, an expired lease is stolen.
- The ledger persistence is deferred by two seconds: writing it on every call
  put a disk write in the latency path (p95 claim 88 ms → 0,3 ms).

### Measured
- Reference bench: 9 criteria out of 9. Spawn 178 tokens, verification 226
  tokens, deduplication 100 %, 6 model calls, replay identical, p95 claim
  0,62 ms, p95 projection 0,22 ms, 96,1 % of tokens saved.

## [0.1.0] - 2026-11-04

### Added
- Project skeleton: src-layout package, isolated Python environment, strict
  `mypy` + `ruff` configuration, `pytest` harness.
- Content-addressed store with reference-counted collection, versioned
  blackboard with an append-only journal and monotone per-key sequences.
- Lease state machine with heartbeats, expiry and node stealing.
- Pure reducers for the four projection tiers, scoped cache, ambiguity
  router, replay journal, metrics, WASM sandbox.
- The ten MCP tools and their schemas.

### Changed
- Implementation stack moved from TypeScript/Node 22 to Python (product
  distribution via wheels; see `DECISIONS.md` D1).
