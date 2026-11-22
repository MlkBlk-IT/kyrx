"""The ambiguity router (I5).

The model is called only on nodes flagged ambiguous. Its answer returns as a
handle: the hint is stored as an artifact and never injected into another
agent's context as prose.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

import httpx

from kyrx.economy.tokens import TokenUsage, count_tokens
from kyrx.errors import NotImplementedPhaseError
from kyrx.store.blobstore import BlobStore
from kyrx.types import Handle, Node, Producer

__all__ = ["AmbiguityRouter", "LLMClient", "OpenAICompatibleClient", "RouterStats"]

SYSTEM_PROMPT = (
    "You resolve one ambiguous step of a plan. Answer with the resolved result only. "
    "No preamble, no explanation, no restating the question."
)


class LLMClient(Protocol):
    """The small model, as an interface (§2: an interface, not an implementation)."""

    async def complete(self, *, system: str, prompt: str) -> str: ...


class OpenAICompatibleClient:
    """Calls any OpenAI-compatible chat completion endpoint.

    The exact token usage reported by the API is captured on every call, so a
    consumer can record measured numbers instead of counts derived from the
    text length. The last usage is exposed through :meth:`last_usage` and also
    returned by :meth:`complete_with_usage`.
    """

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._last_usage: TokenUsage = TokenUsage(source="unavailable")
        self._last_latency_ms: float = 0.0

    async def complete(self, *, system: str, prompt: str) -> str:
        """Ask the model for a completion and return its text."""
        text, _ = await self.complete_with_usage(system=system, prompt=prompt)
        return text

    async def complete_with_usage(
        self, *, system: str, prompt: str
    ) -> tuple[str, TokenUsage]:
        """Ask the model and return ``(text, usage)`` with the measured usage."""
        headers = {"Authorization": f"Bearer {self._api_key}"} if self._api_key else {}
        payload = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0,
        }
        started = time.perf_counter()
        async with httpx.AsyncClient(timeout=self._timeout_s) as client:
            response = await client.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            body = response.json()
        self._last_latency_ms = (time.perf_counter() - started) * 1000.0
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices:
            raise NotImplementedPhaseError("the model returned no choice")
        message = choices[0].get("message", {})
        content = message.get("content")
        if not isinstance(content, str):
            raise NotImplementedPhaseError("the model returned no content")
        self._last_usage = self._read_usage(body)
        return content, self._last_usage

    @staticmethod
    def _read_usage(body: dict[str, object]) -> TokenUsage:
        """Extract the exact usage block, or mark it unavailable."""
        usage = body.get("usage")
        if not isinstance(usage, dict):
            return TokenUsage(source="unavailable")
        try:
            prompt_tokens = int(usage.get("prompt_tokens", 0))
            completion_tokens = int(usage.get("completion_tokens", 0))
        except (TypeError, ValueError):
            return TokenUsage(source="unavailable")
        return TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            source="api-usage",
        )

    def last_usage(self) -> TokenUsage:
        """The usage reported by the most recent call."""
        return self._last_usage

    def last_latency_ms(self) -> float:
        """Wall-clock latency of the most recent call, in milliseconds."""
        return self._last_latency_ms


@dataclass(frozen=True, slots=True)
class RouterStats:
    """How many model calls the router made, and what they really cost."""

    calls: int
    nodes_resolved: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: float = 0.0
    usage_source: str = "unavailable"

    @property
    def total_tokens(self) -> int:
        """Prompt plus completion, as reported by the model API."""
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> dict[str, object]:
        """A serialisable view."""
        return {
            "calls": self.calls,
            "nodesResolved": self.nodes_resolved,
            "promptTokens": self.prompt_tokens,
            "completionTokens": self.completion_tokens,
            "totalTokens": self.total_tokens,
            "latencyMs": round(self.latency_ms, 3),
            "usageSource": self.usage_source,
        }


class AmbiguityRouter:
    """Resolves ambiguous nodes by calling the small model."""

    def __init__(
        self,
        client: LLMClient,
        store: BlobStore,
        *,
        max_calls_per_node: int = 1,
    ) -> None:
        self._client = client
        self._store = store
        self._max_calls_per_node = max_calls_per_node
        self._calls = 0
        self._resolved: dict[str, int] = {}
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._latency_ms = 0.0
        self._usage_source = "unavailable"

    async def resolve(self, node: Node, inputs: Sequence[Handle]) -> Handle:
        """Resolve one ambiguous node and return the handle of the hint."""
        if node.kind != "ambiguous":
            raise NotImplementedPhaseError(
                "a deterministic node never reaches the router (I5)",
                node_id=node.id,
            )
        used = self._resolved.get(node.id, 0)
        if used >= self._max_calls_per_node:
            raise NotImplementedPhaseError(
                "the call budget of this node is exhausted",
                node_id=node.id,
                calls=used,
            )
        prompt = self._prompt(node, inputs)
        self._calls += 1
        self._resolved[node.id] = used + 1
        started = time.perf_counter()
        if hasattr(self._client, "complete_with_usage"):
            hint, usage = await self._client.complete_with_usage(
                system=SYSTEM_PROMPT, prompt=prompt
            )
            latency_ms = getattr(
                self._client, "last_latency_ms", lambda: 0.0
            )() or (time.perf_counter() - started) * 1000.0
            self._prompt_tokens += usage.prompt_tokens
            self._completion_tokens += usage.completion_tokens
            self._latency_ms += latency_ms
            self._usage_source = usage.source
        else:
            hint = await self._client.complete(system=SYSTEM_PROMPT, prompt=prompt)
            self._latency_ms += (time.perf_counter() - started) * 1000.0
            self._prompt_tokens += count_tokens(SYSTEM_PROMPT) + count_tokens(prompt)
            self._completion_tokens += count_tokens(hint)
            self._usage_source = "estimate"
        artifact = self._store.put(
            hint.encode("utf-8"),
            media_type="text/plain",
            producer=Producer(agent_id="router", node_id=node.id),
        )
        return artifact.id

    def stats(self) -> RouterStats:
        """Call counters and measured usage of the router."""
        return RouterStats(
            calls=self._calls,
            nodes_resolved=len(self._resolved),
            prompt_tokens=self._prompt_tokens,
            completion_tokens=self._completion_tokens,
            latency_ms=self._latency_ms,
            usage_source=self._usage_source,
        )

    @staticmethod
    def _prompt(node: Node, inputs: Sequence[Handle]) -> str:
        references = ", ".join(inputs) if inputs else "no input"
        return (
            f"Node: {node.id}\n"
            f"Reason it is ambiguous: {node.ambiguity_reason}\n"
            f"Ambiguity score: {node.ambiguity_score}\n"
            f"Input handles: {references}\n"
            "Resolve the node."
        )
