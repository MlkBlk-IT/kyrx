"""Bearer token verification for the HTTP transport.

The comparison is constant time, so a wrong token cannot be guessed by
timing it. A server configured for remote use refuses to start without a
token: failing closed is the only safe default for a premium product.
"""

from __future__ import annotations

import hmac
from collections.abc import Sequence

from mcp.server.auth.provider import AccessToken

__all__ = ["BearerTokenVerifier"]


class BearerTokenVerifier:
    """Verifies a static bearer token."""

    def __init__(
        self,
        token: str,
        *,
        required_scopes: Sequence[str] = (),
        client_id: str = "kyrx",
        resource: str | None = None,
    ) -> None:
        self._token = token
        self._required = tuple(required_scopes)
        self._client_id = client_id
        self._resource = resource

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return the access token when the bearer token matches."""
        if not hmac.compare_digest(token, self._token):
            return None
        return AccessToken(
            token=token,
            client_id=self._client_id,
            scopes=list(self._required),
            resource=self._resource,
        )
