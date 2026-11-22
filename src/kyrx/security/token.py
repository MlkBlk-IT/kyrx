"""Bearer token generation for the HTTP transport.

The HTTP transport refuses to start without a token (fail closed), so the
product hands out a good one instead of letting people invent a string.
"""

from __future__ import annotations

import secrets

__all__ = ["generate_token"]

_TOKEN_BYTES = 32


def generate_token(nbytes: int = _TOKEN_BYTES) -> str:
    """A URL-safe random token carrying ``nbytes`` bytes of entropy (43 characters for 32)."""
    return secrets.token_urlsafe(nbytes)
