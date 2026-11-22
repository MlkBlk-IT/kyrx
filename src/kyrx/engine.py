"""Composition root: the object graph of one KYRX process.

The engine owns the store, the blackboard, the leases, the cache, the audit
trail and the economy ledger, and exposes the operations the MCP tools call.
Everything is deterministic except the two model entry points, which are
counted and audited.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import json
import time
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from typing import Literal
from uuid import uuid4

from kyrx.board.blackboard import Blackboard
from kyrx.board.journal import Journal
from kyrx.cache.cache import ScopedCache
from kyrx.cache.invalidation import Scope
from kyrx.config import Settings
from kyrx.economy.ledger import TokenLedger
from kyrx.economy.tokens import count_tokens
from kyrx.errors import (
    LeaseNotHeldError,
    NotFoundError,
    NotImplementedPhaseError,
    ToolError,
    ValidationError,
)
from kyrx.lease.manager import LeaseManager
from kyrx.mcp.schemas import (
    ClaimOutput,
    ManifestOutput,
    NativeStep,
    ProveOutput,
    PublishOutput,
)
from kyrx.plan.dag import Plan
from kyrx.plan.native import NativePlanner
from kyrx.reduce.projection import (
    Projection,
    canonical_bytes,
    project_state,
)
from kyrx.reduce.tiers import EXCERPT_CHARS, TIER_EXCERPT, TIER_FULL
from kyrx.route.router import AmbiguityRouter, LLMClient, OpenAICompatibleClient
from kyrx.security.audit import AuditLog
from kyrx.security.limits import (
    MAX_ARTIFACT_BYTES,
    MAX_INTENT_CHARS,
    MAX_STEPS,
    MAX_SUMMARY_CHARS,
    check_length,
)
from kyrx.security.ratelimit import RateLimiter
from kyrx.store.blobstore import BlobStore
from kyrx.trace.metrics import Metrics
from kyrx.types import Entry, Handle, Producer

__all__ = ["Engine", "key_for"]

STEPS_SYSTEM_PROMPT = (
    "You decompose an intent into steps. Reply with JSON only: "
    '{"steps": [{"name": "snake_case_name", "consumes": ["other_step_name"], '
    '"ambiguous": false, "ambiguity_score": 0, "ambiguity_reason": ""}]}. '
    "A step consumes the steps it needs. Mark a step ambiguous only when its "
    "output cannot be decided from what it consumes, and always justify it."
)


def key_for(dag_id: str, branch: str, name: str) -> str:
    """Blackboard key of a node output."""
    return f"dag/{dag_id}/{branch}/{name}"


def node_edge(node_id: str) -> str:
    """Synthetic edge standing for a node itself, so a leaf is still scoped."""
    return f"node:{node_id}"


class Engine:
    """The coordination plane."""

    def __init__(self, settings: Settings, *, client: LLMClient | None = None) -> None:
        self.settings = settings
        self.store = BlobStore(settings.blob_dir)
        self.journal = Journal(settings.journal_path)
        self.board = Blackboard(store=self.store, journal=self.journal)
        self.leases = LeaseManager(
            offer_ttl_s=settings.lease_offer_ttl_s,
            run_ttl_s=settings.lease_run_ttl_s,
            steal_after_s=settings.lease_steal_after_s,
        )
        self.cache = ScopedCache()
        self.metrics = Metrics()
        self.ledger = TokenLedger(
            settings.economy_path,
            input_price_per_mtok=settings.input_price_per_mtok,
            output_price_per_mtok=settings.output_price_per_mtok,
        )
        requested = settings.effective_mode
        # "auto" starts on the fast single-agent path over stdio (one client
        # process) and fully protected over http, where a lone visitor cannot
        # be proven. It can only move from solo to fleet, never back.
        self.solo_mode = requested == "solo" or (
            requested == "auto" and settings.transport == "stdio"
        )
        self._adaptive = requested == "auto" and self.solo_mode
        self._agents: set[str] = set()
        self.audit = AuditLog(None if self.solo_mode else settings.audit_path)
        self.limiter = RateLimiter(
            capacity=settings.rate_limit_calls,
            window_s=settings.rate_limit_window_s,
        )
        self.planner = NativePlanner()
        self.plans: dict[str, Plan] = {}
        self._client = client
        self.router: AmbiguityRouter | None = (
            AmbiguityRouter(client, self.store) if client is not None else None
        )

    def configure_client(self, client: LLMClient) -> None:
        """Attach the model client used by the router and by plan synthesis."""
        self._client = client
        self.router = AmbiguityRouter(client, self.store)

    @classmethod
    def from_settings(cls, settings: Settings) -> Engine:
        """Build an engine, wiring the model client when the settings define one."""
        client: LLMClient | None = None
        if settings.llm_configured:
            client = OpenAICompatibleClient(
                base_url=settings.llm_base_url or "",
                model=settings.llm_model or "",
                api_key=settings.llm_api_key.get_secret_value() if settings.llm_api_key else None,
                timeout_s=settings.llm_timeout_s,
            )
        return cls(settings, client=client)

    # ------------------------------------------------------------------ mode

    @property
    def mode(self) -> Literal["solo", "fleet"]:
        """The protection level in force right now."""
        return "solo" if self.solo_mode else "fleet"

    def observe_agent(self, agent_id: str) -> None:
        """Note an agent id; the second distinct one switches the plane to fleet.

        Only ``claim`` carries a real agent identity (the actor of every other
        tool is a node, a lease, a plan or a key), so only claims are observed.
        """
        if not self._adaptive:
            return
        self._agents.add(agent_id)
        if len(self._agents) > 1:
            self._escalate()

    def _escalate(self) -> None:
        """Switch to fleet for good: audit to disk with its history, limiter on."""
        self.audit.record(
            tool="engine",
            actor="kyrx",
            outcome="mode",
            detail="solo->fleet",
            ts=time.time(),
        )
        self.audit.attach(self.settings.audit_path)
        self.solo_mode = False
        self._adaptive = False
        self.metrics.increment("mode.escalated")

    # ----------------------------------------------------------------- guard

    def authorize(self, actor: str, tool: str) -> None:
        """Check the rate limit of an actor before a call is served."""
        if self.solo_mode:
            return
        if not self.limiter.allow(f"{tool}:{actor}"):
            raise ToolError(
                "rate limit exceeded",
                tool=tool,
                actor=actor,
                remaining=self.limiter.remaining(f"{tool}:{actor}"),
            )

    @asynccontextmanager
    async def guard(self, tool: str, actor: str) -> AsyncIterator[None]:
        """Rate limit, audit and account one tool call."""
        self.authorize(actor, tool)
        if self.solo_mode:
            # No rate limiter, no disk, no thread hop: a single agent has no
            # concurrent actor to arbitrate against or prove anything to.
            try:
                yield
            except Exception as exc:
                self.audit.record(
                    tool=tool,
                    actor=actor,
                    outcome="error",
                    detail=type(exc).__name__,
                    ts=time.time(),
                )
                raise
            else:
                self.audit.record(tool=tool, actor=actor, outcome="ok", ts=time.time())
            return
        try:
            yield
        except Exception as exc:
            await self._audit(tool, actor, "error", type(exc).__name__)
            raise
        else:
            await self._audit(tool, actor, "ok")

    async def _audit(self, tool: str, actor: str, outcome: str, detail: str = "") -> None:
        """Append to the audit trail without blocking the event loop."""
        await asyncio.to_thread(
            self.audit.record,
            tool=tool,
            actor=actor,
            outcome=outcome,
            detail=detail,
            ts=time.time(),
        )

    # ---------------------------------------------------------------- intent

    async def intent(
        self,
        intent: str,
        budget_tokens: int,
        steps: list[NativeStep] | None = None,
    ) -> str:
        """Materialise a plan and return its identifier."""
        check_length(intent, limit=MAX_INTENT_CHARS, field="intent")
        if steps is None:
            steps = await self._synthesise_steps(intent)
        if len(steps) > MAX_STEPS:
            raise ValidationError(f"a plan holds at most {MAX_STEPS} steps", steps=len(steps))
        dag_id = f"plan-{uuid4().hex[:8]}"
        plan = self.planner.derive(
            dag_id=dag_id,
            steps=steps,
            branch="main",
            budget_tokens=budget_tokens,
        )
        self.plans[dag_id] = plan
        self.metrics.increment("intent.calls")
        self.ledger.record("intent", served=count_tokens(intent), avoided=0)
        return dag_id

    async def _synthesise_steps(self, intent: str) -> list[NativeStep]:
        """Ask the model for the steps of a plan, in the native shape."""
        if self._client is None:
            raise NotImplementedPhaseError(
                "plan synthesis needs a model client; declare the steps instead",
                intent=intent[:120],
            )
        if hasattr(self._client, "complete_with_usage"):
            raw, usage = await self._client.complete_with_usage(
                system=STEPS_SYSTEM_PROMPT, prompt=intent
            )
            latency_ms = getattr(self._client, "last_latency_ms", lambda: 0.0)()
            self.ledger.record_llm_usage(
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                latency_ms=latency_ms,
                source=usage.source,
                calls=1,
            )
        else:
            raw = await self._client.complete(system=STEPS_SYSTEM_PROMPT, prompt=intent)
            self.ledger.record_model_call()
        try:
            payload = json.loads(raw)
            return [NativeStep.model_validate(item) for item in payload["steps"]]
        except Exception as exc:
            raise ToolError("the model did not return valid steps", reason=str(exc)) from exc

    # --------------------------------------------------------------- manifest

    async def manifest(self, dag_id: str, branch: str) -> ManifestOutput:
        """Compact description of a branch."""
        plan = self._require_plan(dag_id)
        nodes = [node.id for node in plan.nodes]
        payload = canonical_bytes({"dagId": dag_id, "branch": branch, "nodeIds": nodes})
        artifact = self.store.put(
            payload,
            media_type="application/json",
            producer=Producer(agent_id="kyrx", node_id=dag_id),
        )
        served = count_tokens(",".join(nodes))
        entries = await self.board.versions_prefix(f"dag/{dag_id}/")
        naive = self._naive_cost([entry.artifact for entry in entries if entry.artifact])
        self.ledger.record("manifest", served=served, avoided=max(0, naive - served))
        return ManifestOutput(handle=artifact.id, node_ids=nodes)

    # ------------------------------------------------------------------ lease

    async def claim(self, node_id: str, agent_id: str) -> ClaimOutput:
        """Take a node exclusively, or report the result already in flight."""
        self.observe_agent(agent_id)
        outcome = await self.leases.claim(node_id, agent_id)
        self.metrics.increment("claim.calls")
        if outcome.granted and outcome.lease is not None:
            await self.board.set_status(
                node_id=node_id,
                key=self._key_of_node(node_id),
                status="leased",
            )
            self.ledger.record("claim", served=count_tokens(node_id), avoided=0)
            return ClaimOutput(
                node_id=node_id,
                agent_id=agent_id,
                lease_id=outcome.lease.id,
                expires_at=outcome.lease.expires_at,
            )
        self.ledger.record("claim", served=count_tokens(node_id), avoided=0)
        return ClaimOutput(
            node_id=node_id,
            agent_id=agent_id,
            inflight_handle=outcome.inflight_handle,
        )

    async def beat(self, lease_id: str) -> None:
        """Renew a lease."""
        await self.leases.beat(lease_id)
        self.metrics.increment("beat.calls")

    async def fail(self, lease_id: str, reason: str) -> None:
        """Release a node after a failure."""
        await self.leases.fail(lease_id, reason)
        self.metrics.increment("fail.calls")

    # ---------------------------------------------------------------- publish

    async def publish(
        self,
        node_id: str,
        artifact_ref: str,
        summary: str,
    ) -> PublishOutput:
        """Write a node result to the blackboard as a new version."""
        check_length(summary, limit=MAX_SUMMARY_CHARS, field="summary")
        handle = self._resolve_artifact(artifact_ref)
        plan = self._plan_of_node(node_id)
        node = plan.node(node_id)
        if node is None:
            raise NotFoundError(f"unknown node {node_id}", node_id=node_id)
        scope = self._scope_of(plan, node_id)
        key = self._key_of_node(node_id)
        lease = self.leases.lease_of(node_id)
        if lease is None or lease.state not in ("claimed", "running"):
            raise LeaseNotHeldError(
                f"node {node_id} is not leased, a result cannot be published",
                node_id=node_id,
                state=lease.state if lease is not None else "none",
            )
        entry = await self.board.write(
            key=key,
            artifact=handle,
            invalidation_scope=sorted(scope.edges),
            summary=summary,
        )
        await self.leases.complete(lease.id, handle)
        await self.board.set_status(node_id=node_id, key=key, status="done")
        self.cache.invalidate(scope.edges)
        self.metrics.increment("publish.calls")
        self.ledger.record("publish", served=count_tokens(summary), avoided=0)
        return PublishOutput(key=entry.key, seq=entry.seq, artifact=handle)

    def _resolve_artifact(self, artifact_ref: str) -> Handle:
        """Accept a known handle, or an inline payload to ingest."""
        if artifact_ref.startswith("inline:"):
            try:
                payload = base64.b64decode(artifact_ref.removeprefix("inline:"), validate=True)
            except binascii.Error as exc:
                raise ValidationError(
                    "an inline artifactRef must be valid base64",
                    artifact_ref=artifact_ref[:80],
                ) from exc
            if len(payload) > MAX_ARTIFACT_BYTES:
                raise ValidationError(
                    f"an artifact holds at most {MAX_ARTIFACT_BYTES} bytes",
                    size=len(payload),
                )
            artifact = self.store.put(
                payload,
                media_type="application/octet-stream",
                producer=Producer(agent_id="kyrx", node_id="publish"),
            )
            return artifact.id
        if not artifact_ref.startswith("blake3:"):
            raise ToolError(
                "artifactRef must be a handle or an inline payload",
                artifact_ref=artifact_ref[:80],
            )
        return artifact_ref

    # ------------------------------------------------------------------- read

    async def read(
        self,
        *,
        handle: Handle | None = None,
        key: str | None = None,
        seq: int | None = None,
        tier: int = 1,
        fields: Sequence[str] | None = None,
    ) -> object:
        """Read an artifact or an entry, at the requested tier."""
        if handle is None:
            if key is None:
                raise ToolError("read needs a handle or a key")
            if seq is not None:
                entry = await self.board.entry(key, seq)
            else:
                entry = await self.board.latest(key)
            if entry is None or entry.artifact is None:
                raise NotFoundError(f"no entry for {key}", key=key)
            handle = entry.artifact
        data = self.store.get(handle)
        naive = count_tokens(data)
        if tier >= TIER_FULL:
            served = naive
        elif tier >= TIER_EXCERPT:
            served = count_tokens(data.decode("utf-8", errors="replace")[:EXCERPT_CHARS])
        else:
            served = count_tokens(str(len(data)))
        self.ledger.record("read", served=served, avoided=max(0, naive - served))
        if tier >= TIER_FULL:
            return data.decode("utf-8", errors="replace")
        if tier >= TIER_EXCERPT:
            return data.decode("utf-8", errors="replace")[:EXCERPT_CHARS]
        rendered: dict[str, object] = {"handle": handle, "size": len(data)}
        if fields:
            rendered = {name: value for name, value in rendered.items() if name in fields}
        return rendered

    # ------------------------------------------------------------------ prove

    async def prove(self, key: str) -> ProveOutput:
        """Record scoped evidence that a key holds nothing."""
        scope = self._scope_of_key(key)
        proof = self.cache.prove_absent(key, scope)
        self.metrics.increment("prove.calls")
        self.ledger.record("prove", served=count_tokens(key), avoided=0)
        return ProveOutput(key=proof.key, scope=list(proof.scope), proven_at=proof.proven_at)

    # ---------------------------------------------------------------- project

    async def project(self, dag_id: str, tier: int) -> Projection:
        """Render the state of a DAG at a tier, from the cache when possible."""
        plan = self.plans.get(dag_id)
        scope = self._plan_scope(plan)
        cache_key = f"projection:{dag_id}:{tier}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            projection = Projection.from_payload(dag_id, tier, cached)
            self.metrics.increment("project.cache_hit")
            self.ledger.record("project", served=projection.token_estimate, avoided=0)
            return projection

        entries = await self.board.versions_prefix(f"dag/{dag_id}/")
        projection = project_state(
            dag_id,
            entries,
            tier,
            reader=self.store.get,
            statuses=self.board.statuses_by_key(),
        )
        self.cache.put(cache_key, projection.payload, scope)
        naive = self._naive_cost([entry.artifact for entry in entries if entry.artifact])
        self.metrics.increment("project.calls")
        self.ledger.record(
            "project",
            served=projection.token_estimate,
            avoided=max(0, naive - projection.token_estimate),
        )
        return projection

    # ------------------------------------------------------------------ trace

    async def trace(self, dag_id: str) -> bytes:
        """The replayable journal of a DAG, as canonical bytes."""
        entries = await self.board.versions_prefix(f"dag/{dag_id}/")
        payload = canonical_bytes(
            {
                "dagId": dag_id,
                "entries": [
                    {"key": entry.key, "seq": entry.seq, "artifact": entry.artifact}
                    for entry in entries
                ],
            }
        )
        self.ledger.record("trace", served=count_tokens(payload), avoided=0)
        return payload

    # --------------------------------------------------------------- internals

    def _require_plan(self, dag_id: str) -> Plan:
        plan = self.plans.get(dag_id)
        if plan is None:
            raise NotFoundError(f"unknown plan {dag_id}", dag_id=dag_id)
        return plan

    def _plan_of_node(self, node_id: str) -> Plan:
        for plan in self.plans.values():
            if plan.node(node_id) is not None:
                return plan
        raise NotFoundError(f"unknown node {node_id}", node_id=node_id)

    def _scope_of(self, plan: Plan, node_id: str) -> Scope:
        return Scope(plan.edges_touching(node_id) | {node_edge(node_id)})

    def _plan_scope(self, plan: Plan | None) -> Scope:
        if plan is None:
            return Scope(frozenset())
        return Scope(plan.edges() | {node_edge(node.id) for node in plan.nodes})

    def _scope_of_key(self, key: str) -> Scope:
        for plan in self.plans.values():
            for node in plan.nodes:
                for output in node.outputs:
                    if key_for(plan.dag_id, plan.branch, output) == key:
                        return self._scope_of(plan, node.id)
        return Scope(frozenset())

    def _key_of_node(self, node_id: str) -> str:
        """Blackboard key of the first output of a node."""
        plan = self._plan_of_node(node_id)
        node = plan.node(node_id)
        if node is None:
            raise NotFoundError(f"unknown node {node_id}", node_id=node_id)
        if not node.outputs:
            return f"dag/{plan.dag_id}/out/{node_id}"
        return key_for(plan.dag_id, plan.branch, node.outputs[0])

    def _naive_cost(self, handles: Sequence[Handle]) -> int:
        """Tokens a naive fleet would spend to read the same artifacts.

        Counted with the same real tokenizer as the served side, so the
        saving ratio compares two measurements of the same unit.
        """
        total = 0
        for handle in handles:
            try:
                total += count_tokens(self.store.get(handle))
            except NotFoundError:
                continue
        return max(1, total)

    def record_router_usage(self) -> None:
        """Fold the router's measured usage into the ledger.

        Called once the router has resolved the ambiguous nodes, so the real
        model cost of a run lands in the economy report next to the substrate
        cost the plane served.
        """
        if self.router is None:
            return
        stats = self.router.stats()
        self.ledger.record_llm_usage(
            prompt_tokens=stats.prompt_tokens,
            completion_tokens=stats.completion_tokens,
            latency_ms=stats.latency_ms,
            source=stats.usage_source,
            calls=0,
        )

    async def live_entries(self) -> list[Entry]:
        """Every live entry, used by the collector."""
        return await self.board.versions_prefix("dag/")
