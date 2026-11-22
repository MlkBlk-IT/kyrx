"""Real token measurement (not an estimate).

Two sources, in order of preference:

1. A real tokenizer (``tiktoken``) when it is installed. The encoding is the
   ``cl100k_base`` one, which is what the GPT family and most OpenAI-compatible
   back ends bill on.
2. A byte-based fallback (four characters per token, D5) when no tokenizer is
   available. The fallback is marked as such by :func:`measurement_source`, so
   a report never presents an estimate as a measurement.

On the model side the truth comes from the API itself: every OpenAI-compatible
response carries a ``usage`` block with the exact prompt and completion token
counts. :class:`TokenUsage` carries it back to the ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

__all__ = [
    "TokenUsage",
    "count_tokens",
    "measurement_source",
    "tokenizer_available",
]

try:
    import tiktoken

    _TIKTOKEN_OK = True
except Exception:
    _TIKTOKEN_OK = False

_ENCODING_NAME = "cl100k_base"


@lru_cache(maxsize=1)
def _encoding() -> tiktoken.Encoding | None:
    """The real tokenizer, or ``None`` when tiktoken is not installed."""
    if not _TIKTOKEN_OK:
        return None
    try:
        return tiktoken.get_encoding(_ENCODING_NAME)
    except Exception:
        return None


def tokenizer_available() -> bool:
    """Whether a real tokenizer backs the counts."""
    return _encoding() is not None


def measurement_source() -> str:
    """Name of the source of the counts, so a report can disclose it."""
    return _ENCODING_NAME if tokenizer_available() else "estimate-len4"


def count_tokens(payload: bytes | str) -> int:
    """Token count of a payload.

    Uses the real tokenizer when available, falls back to four characters per
    token otherwise. Never returns zero: an empty payload still costs one
    token to a model.
    """
    text = payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else payload
    if not text:
        return 1
    enc = _encoding()
    if enc is not None:
        return max(1, len(enc.encode(text, disallowed_special=())))
    return max(1, -(-len(text) // 4))


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Exact token usage reported by a model API for one call."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    source: str = "api-usage"

    @property
    def total(self) -> int:
        """Prompt plus completion."""
        return self.prompt_tokens + self.completion_tokens

    def to_dict(self) -> dict[str, int | str]:
        """A serialisable view."""
        return {
            "promptTokens": self.prompt_tokens,
            "completionTokens": self.completion_tokens,
            "total": self.total,
            "source": self.source,
        }
