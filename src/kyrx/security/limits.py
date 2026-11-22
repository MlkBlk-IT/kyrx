"""Input limits.

Every value that crosses the boundary is bounded: an unbounded input is a
denial of service waiting to happen.
"""

from __future__ import annotations

from kyrx.errors import ValidationError

__all__ = [
    "MAX_ARTIFACT_BYTES",
    "MAX_BRANCH_CHARS",
    "MAX_INTENT_CHARS",
    "MAX_KEY_CHARS",
    "MAX_STEPS",
    "MAX_STEP_NAME",
    "MAX_SUMMARY_CHARS",
    "check_length",
]

MAX_INTENT_CHARS = 2_000
MAX_STEPS = 64
MAX_STEP_NAME = 64
MAX_SUMMARY_CHARS = 280
MAX_ARTIFACT_BYTES = 8 * 1024 * 1024
MAX_KEY_CHARS = 256
MAX_BRANCH_CHARS = 128


def check_length(value: str, *, limit: int, field: str) -> str:
    """Reject a value longer than the limit, and return it when it fits."""
    if len(value) > limit:
        raise ValidationError(
            f"{field} is longer than {limit} characters",
            field=field,
            length=len(value),
        )
    return value
