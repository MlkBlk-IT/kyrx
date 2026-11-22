"""Security invariants: authentication, rate limiting, audit chain, input limits."""

from __future__ import annotations

from pathlib import Path

import pytest

from kyrx.config import Settings
from kyrx.engine import Engine
from kyrx.errors import ConfigError, ToolError, ValidationError
from kyrx.mcp.server import build_server
from kyrx.security.audit import AuditLog
from kyrx.security.auth import BearerTokenVerifier
from kyrx.security.limits import MAX_ARTIFACT_BYTES, check_length
from kyrx.security.ratelimit import RateLimiter


class FakeClock:
    """A clock the test drives by hand."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def test_the_right_token_is_accepted() -> None:
    verifier = BearerTokenVerifier("s3cret", required_scopes=["kyrx:write"])

    token = await verifier.verify_token("s3cret")

    assert token is not None
    assert token.scopes == ["kyrx:write"]


async def test_a_wrong_token_is_refused() -> None:
    verifier = BearerTokenVerifier("s3cret")

    assert await verifier.verify_token("wrong") is None


async def test_the_accepted_token_carries_the_resource_it_is_bound_to() -> None:
    # The SDK refuses a token whose resource is not the server URL (RFC 8707).
    verifier = BearerTokenVerifier("s3cret", resource="http://127.0.0.1:8765/mcp")

    token = await verifier.verify_token("s3cret")

    assert token is not None
    assert token.resource == "http://127.0.0.1:8765/mcp"


async def test_the_http_transport_refuses_to_run_without_a_token() -> None:
    settings = Settings(transport="http", data_dir=Path("var"))

    with pytest.raises(ConfigError):
        build_server(settings)


async def test_the_http_transport_runs_with_a_token() -> None:
    bearer = "s3cret-for-the-test"
    settings = Settings(
        transport="http",
        auth_mode="token",
        auth_token=bearer,
        data_dir=Path("var"),
    )

    server = build_server(settings)

    assert server is not None


def test_the_rate_limiter_refuses_past_its_capacity() -> None:
    clock = FakeClock()
    limiter = RateLimiter(capacity=3, window_s=60, clock=clock)

    assert all(limiter.allow("agent") for _ in range(3))
    assert limiter.allow("agent") is False
    assert limiter.remaining("agent") == 0


def test_the_rate_limiter_forgets_the_old_window() -> None:
    clock = FakeClock()
    limiter = RateLimiter(capacity=2, window_s=10, clock=clock)
    limiter.allow("agent")
    limiter.allow("agent")

    clock.advance(11)

    assert limiter.allow("agent") is True


def test_the_audit_chain_verifies() -> None:
    audit = AuditLog()
    audit.record(tool="claim", actor="agent-1", outcome="ok")
    audit.record(tool="publish", actor="agent-1", outcome="ok")

    assert audit.verify() is True
    assert len(audit.records()) == 2


def test_a_tampered_audit_record_breaks_the_chain() -> None:
    audit = AuditLog()
    audit.record(tool="claim", actor="agent-1", outcome="ok")
    audit.record(tool="publish", actor="agent-1", outcome="ok")
    audit._records[0] = audit._records[0].__class__(
        index=0,
        ts=0.0,
        tool="claim",
        actor="intruder",
        outcome="ok",
        detail="",
        prev_hash=audit._records[0].prev_hash,
        hash=audit._records[0].hash,
    )

    assert audit.verify() is False


def test_the_audit_log_is_persisted(tmp_path: Path) -> None:
    path = tmp_path / "audit.ndjson"
    audit = AuditLog(path)
    audit.record(tool="claim", actor="agent-1", outcome="ok")

    assert path.exists()
    assert path.read_text(encoding="utf-8").count("\n") == 1


def test_a_too_long_text_is_refused() -> None:
    with pytest.raises(ValidationError):
        check_length("x" * 100, limit=10, field="intent")


def test_the_artifact_size_is_capped() -> None:
    assert MAX_ARTIFACT_BYTES == 8 * 1024 * 1024


async def test_the_guard_audits_a_failure(settings: Settings) -> None:
    engine = Engine(settings)

    with pytest.raises(ValidationError):
        async with engine.guard("claim", "agent-1"):
            raise ValidationError("boom")

    assert engine.audit.records()[-1].outcome == "error"


async def test_the_guard_refuses_a_flood(settings: Settings) -> None:
    engine = Engine(settings)
    engine.limiter = RateLimiter(capacity=1, window_s=60)

    async with engine.guard("claim", "agent-1"):
        pass

    with pytest.raises(ToolError, match="rate limit"):
        async with engine.guard("claim", "agent-1"):
            pass
