"""Typed error hierarchy.

Every failure a caller can observe carries a stable machine-readable code.
No bare strings are ever raised.
"""

from __future__ import annotations

__all__ = [
    "AmbiguityViolationError",
    "BudgetExceededError",
    "ConfigError",
    "ConflictError",
    "HandleViolationError",
    "IllegalTransitionError",
    "KeyViolationError",
    "KyrxError",
    "LeaseError",
    "LeaseExpiredError",
    "LeaseNotHeldError",
    "NotFoundError",
    "NotImplementedPhaseError",
    "ReplayMismatchError",
    "SandboxError",
    "SandboxTimeoutError",
    "ToolError",
    "ValidationError",
]


class KyrxError(Exception):
    """Base class for every error raised by KYRX."""

    code = "kyrex_error"

    def __init__(self, message: str, **details: object) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, object] = details

    def __str__(self) -> str:
        return f"[{self.code}] {self.message}"


class ConfigError(KyrxError):
    """Invalid or missing configuration."""

    code = "config_error"


class ValidationError(KyrxError):
    """A payload failed schema or semantic validation."""

    code = "validation_error"


class KeyViolationError(ValidationError):
    """A blackboard key failed the character whitelist."""

    code = "key_violation"


class HandleViolationError(ValidationError):
    """A handle is not a well-formed content address."""

    code = "handle_violation"


class NotFoundError(KyrxError):
    """A referenced entity does not exist."""

    code = "not_found"


class ConflictError(KyrxError):
    """A write conflicts with the current state of the key."""

    code = "conflict"


class LeaseError(KyrxError):
    """Base class for lease failures."""

    code = "lease_error"


class IllegalTransitionError(LeaseError):
    """The requested lease state transition is not allowed."""

    code = "illegal_transition"


class LeaseExpiredError(LeaseError):
    """The lease is no longer valid."""

    code = "lease_expired"


class LeaseNotHeldError(LeaseError):
    """The caller does not hold the lease it claims to hold."""

    code = "lease_not_held"


class BudgetExceededError(KyrxError):
    """A node exceeded its token budget."""

    code = "budget_exceeded"


class AmbiguityViolationError(KyrxError):
    """An ambiguous node was produced without a score and a reason."""

    code = "ambiguity_violation"


class SandboxError(KyrxError):
    """The execution sandbox refused or failed the workload."""

    code = "sandbox_error"


class SandboxTimeoutError(SandboxError):
    """The sandbox hit its hard deadline and was terminated."""

    code = "sandbox_timeout"


class ReplayMismatchError(KyrxError):
    """A replay produced a different result than the recorded one."""

    code = "replay_mismatch"


class ToolError(KyrxError):
    """An MCP tool call failed."""

    code = "tool_error"


class NotImplementedPhaseError(KyrxError):
    """A skeleton module whose implementation belongs to a later phase."""

    code = "not_implemented"
