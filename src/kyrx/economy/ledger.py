"""Token and time economy ledger.

Every response the server sends is measured in tokens, and compared with what
a naive fleet would have spent to obtain the same information. The ledger is
the product's main argument: it is measured, not claimed.

The ledger is persisted, so the economy of a run survives the process that
produced it.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path

from kyrx.economy.tokens import measurement_source

__all__ = ["EconomySnapshot", "TokenLedger"]


@dataclass(frozen=True, slots=True)
class EconomySnapshot:
    """A point-in-time view of the economy."""

    tokens_served: int = 0
    tokens_avoided: int = 0
    calls: int = 0
    model_calls: int = 0
    cache_hits: int = 0
    cache_misses: int = 0
    by_tool: dict[str, int] = field(default_factory=dict)
    llm_prompt_tokens: int = 0
    llm_completion_tokens: int = 0
    llm_latency_ms: float = 0.0
    llm_usage_source: str = "unavailable"
    token_source: str = "unavailable"  # noqa: S105
    input_price_per_mtok: float = 0.0
    output_price_per_mtok: float = 0.0

    @property
    def llm_tokens(self) -> int:
        """Real tokens the model API charged for, all calls together."""
        return self.llm_prompt_tokens + self.llm_completion_tokens

    @property
    def total_cost_tokens(self) -> int:
        """Unified cost of one run: substrate tokens served plus LLM tokens."""
        return self.tokens_served + self.llm_tokens

    @property
    def total_cost_usd(self) -> float:
        """Unified dollar cost, from the configured prices per million tokens."""
        return (
            self.tokens_served / 1_000_000 * self.input_price_per_mtok
            + self.llm_prompt_tokens / 1_000_000 * self.input_price_per_mtok
            + self.llm_completion_tokens / 1_000_000 * self.output_price_per_mtok
        )

    @property
    def cost_breakdown(self) -> dict[str, float]:
        """Share of the unified cost each side is responsible for."""
        total = self.total_cost_tokens
        if total == 0:
            return {"substrateShare": 0.0, "llmShare": 0.0}
        return {
            "substrateShare": round(self.tokens_served / total, 4),
            "llmShare": round(self.llm_tokens / total, 4),
        }

    @property
    def naive_cost_usd(self) -> float:
        """What a naive fleet would have paid, at the same price."""
        naive_tokens = self.tokens_served + self.tokens_avoided
        return naive_tokens / 1_000_000 * self.input_price_per_mtok

    @property
    def saving_ratio(self) -> float:
        """Share of the naive cost that never crossed a boundary."""
        total = self.tokens_served + self.tokens_avoided
        return 0.0 if total == 0 else self.tokens_avoided / total

    def to_dict(self) -> dict[str, object]:
        """A serialisable view."""
        return {
            "tokensServed": self.tokens_served,
            "tokensAvoided": self.tokens_avoided,
            "savingRatio": round(self.saving_ratio, 4),
            "calls": self.calls,
            "modelCalls": self.model_calls,
            "cacheHits": self.cache_hits,
            "cacheMisses": self.cache_misses,
            "byTool": dict(sorted(self.by_tool.items())),
            "llmPromptTokens": self.llm_prompt_tokens,
            "llmCompletionTokens": self.llm_completion_tokens,
            "llmTokens": self.llm_tokens,
            "llmLatencyMs": round(self.llm_latency_ms, 3),
            "llmUsageSource": self.llm_usage_source,
            "tokenSource": self.token_source,
            "totalCostTokens": self.total_cost_tokens,
            "totalCostUsd": round(self.total_cost_usd, 6),
            "naiveCostUsd": round(self.naive_cost_usd, 6),
            "costBreakdown": self.cost_breakdown,
            "inputPricePerMtok": self.input_price_per_mtok,
            "outputPricePerMtok": self.output_price_per_mtok,
        }


class TokenLedger:
    """Accumulates what the plane served and what it saved.

    The persistence is deferred: writing the file on every call would put a
    disk write in the middle of the latency path, which is exactly what the
    economy is supposed to protect.
    """

    PERSIST_INTERVAL_S = 2.0

    def __init__(
        self,
        path: Path | None = None,
        *,
        input_price_per_mtok: float = 0.0,
        output_price_per_mtok: float = 0.0,
    ) -> None:
        self._path = path
        self._input_price = input_price_per_mtok
        self._output_price = output_price_per_mtok
        self._served = 0
        self._avoided = 0
        self._calls = 0
        self._model_calls = 0
        self._by_tool: dict[str, int] = {}
        self._llm_prompt_tokens = 0
        self._llm_completion_tokens = 0
        self._llm_latency_ms = 0.0
        self._llm_usage_source = "unavailable"
        self._last_persist = 0.0
        if path is not None and path.exists():
            self._restore(path)

    def record(self, tool: str, *, served: int, avoided: int = 0) -> None:
        """Record one response."""
        self._served += max(0, served)
        self._avoided += max(0, avoided)
        self._calls += 1
        self._by_tool[tool] = self._by_tool.get(tool, 0) + max(0, served)
        self._persist()

    def record_model_call(self) -> None:
        """Record a call to the model."""
        self._model_calls += 1
        self._persist()

    def record_llm_usage(
        self,
        *,
        prompt_tokens: int = 0,
        completion_tokens: int = 0,
        latency_ms: float = 0.0,
        source: str = "unavailable",
        calls: int = 0,
    ) -> None:
        """Record the real usage a model API reported.

        This is the measured side of the economy: the counts come from the
        model's own ``usage`` block, not from a text-length estimate.
        """
        self._llm_prompt_tokens += max(0, prompt_tokens)
        self._llm_completion_tokens += max(0, completion_tokens)
        self._llm_latency_ms += max(0.0, latency_ms)
        self._model_calls += max(0, calls)
        if source != "unavailable":
            self._llm_usage_source = source
        self._persist()

    def snapshot(self) -> EconomySnapshot:
        """The current economy."""
        return EconomySnapshot(
            tokens_served=self._served,
            tokens_avoided=self._avoided,
            calls=self._calls,
            model_calls=self._model_calls,
            by_tool=dict(self._by_tool),
            llm_prompt_tokens=self._llm_prompt_tokens,
            llm_completion_tokens=self._llm_completion_tokens,
            llm_latency_ms=self._llm_latency_ms,
            llm_usage_source=self._llm_usage_source,
            token_source=measurement_source(),
            input_price_per_mtok=self._input_price,
            output_price_per_mtok=self._output_price,
        )

    @classmethod
    def load(cls, path: Path) -> TokenLedger:
        """Read back a persisted ledger."""
        ledger = cls()
        if path.exists():
            ledger._restore(path)
        return ledger

    def _restore(self, path: Path) -> None:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self._served = int(data.get("tokensServed", 0))
        self._avoided = int(data.get("tokensAvoided", 0))
        self._calls = int(data.get("calls", 0))
        self._model_calls = int(data.get("modelCalls", 0))
        self._llm_prompt_tokens = int(data.get("llmPromptTokens", 0))
        self._llm_completion_tokens = int(data.get("llmCompletionTokens", 0))
        self._llm_latency_ms = float(data.get("llmLatencyMs", 0.0))
        source = data.get("llmUsageSource")
        if isinstance(source, str) and source:
            self._llm_usage_source = source
        self._input_price = float(data.get("inputPricePerMtok", self._input_price))
        self._output_price = float(data.get("outputPricePerMtok", self._output_price))
        by_tool = data.get("byTool", {})
        if isinstance(by_tool, dict):
            self._by_tool = {str(name): int(value) for name, value in by_tool.items()}

    def _persist(self, *, force: bool = False) -> None:
        if self._path is None:
            return
        now = time.monotonic()
        if not force and now - self._last_persist < self.PERSIST_INTERVAL_S:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.snapshot().to_dict(), indent=2, sort_keys=True)
        self._path.write_text(payload, encoding="utf-8")
        self._last_persist = now

    def flush(self) -> None:
        """Write the ledger now."""
        self._persist(force=True)
