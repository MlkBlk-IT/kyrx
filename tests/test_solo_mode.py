"""Single-agent fast path: ``Settings.solo_mode`` and its effect on the engine.

``solo_mode`` skips the rate limiter, keeps the audit trail in memory and drops
the thread hop. These tests pin what it changes and, as much, what it does not.
"""

from __future__ import annotations

import asyncio
import base64
from pathlib import Path

import pytest

from kyrx.config import Settings
from kyrx.engine import Engine
from kyrx.errors import LeaseNotHeldError, ToolError
from kyrx.mcp.schemas import NativeStep


def _solo(data_dir: Path, rate_limit_calls: int = 120) -> Settings:
    return Settings(data_dir=data_dir, solo_mode=True, rate_limit_calls=rate_limit_calls)


def _steps() -> list[NativeStep]:
    return [NativeStep(name="n0", consumes=[], ambiguous=False)]


def _inline(text: str) -> str:
    return "inline:" + base64.b64encode(text.encode("utf-8")).decode("ascii")


async def _call(engine: Engine, tool: str = "read", actor: str = "a") -> None:
    async with engine.guard(tool, actor):
        pass


# ------------------------------------------------------------------ settings


def test_solo_mode_is_off_by_default(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KYRX_SOLO_MODE", raising=False)

    assert Settings(data_dir=data_dir).solo_mode is False


def test_solo_mode_reads_the_environment(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KYRX_SOLO_MODE", "true")

    assert Settings(data_dir=data_dir).solo_mode is True


# --------------------------------------------------------------------- audit


async def test_solo_audit_stays_in_memory(data_dir: Path) -> None:
    settings = _solo(data_dir)
    engine = Engine(settings)

    await _call(engine)
    await _call(engine, "claim")

    assert engine.solo_mode is True
    assert [record.tool for record in engine.audit.records()] == ["read", "claim"]
    assert engine.audit.verify() is True
    assert not settings.audit_path.exists()


async def test_prod_audit_is_still_written_to_disk(settings: Settings) -> None:
    engine = Engine(settings)

    await _call(engine)

    assert engine.solo_mode is False
    assert settings.audit_path.exists()
    assert len(settings.audit_path.read_text(encoding="utf-8").splitlines()) == 1


async def test_solo_records_ok_and_error_outcomes(data_dir: Path) -> None:
    engine = Engine(_solo(data_dir))

    await _call(engine, "read")
    with pytest.raises(ValueError, match="boom"):
        async with engine.guard("publish", "node-1"):
            raise ValueError("boom")

    ok, failed = engine.audit.records()
    assert (ok.tool, ok.outcome, ok.detail) == ("read", "ok", "")
    assert (failed.tool, failed.actor, failed.outcome) == ("publish", "node-1", "error")
    assert failed.detail == "ValueError"
    assert engine.audit.verify() is True


async def test_solo_guard_does_not_hop_to_a_thread(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = Engine(_solo(data_dir))

    async def _forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("solo guard must not call asyncio.to_thread")

    monkeypatch.setattr(asyncio, "to_thread", _forbidden)

    await _call(engine)

    assert len(engine.audit.records()) == 1


async def test_prod_guard_hops_to_a_thread(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine = Engine(settings)
    hops: list[object] = []
    real = asyncio.to_thread

    async def _spy(func: object, /, *args: object, **kwargs: object) -> object:
        hops.append(func)
        return await real(func, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(asyncio, "to_thread", _spy)

    await _call(engine)

    assert len(hops) == 1


# ---------------------------------------------------------------- rate limit


async def test_solo_ignores_the_rate_limit(data_dir: Path) -> None:
    engine = Engine(_solo(data_dir, rate_limit_calls=2))

    for _ in range(10):
        await _call(engine)

    assert len(engine.audit.records()) == 10


async def test_prod_still_enforces_the_rate_limit(data_dir: Path) -> None:
    engine = Engine(Settings(data_dir=data_dir, mode="fleet", rate_limit_calls=2))

    await _call(engine)
    await _call(engine)
    with pytest.raises(ToolError, match="rate limit exceeded"):
        await _call(engine)


async def test_solo_survives_past_the_default_window_capacity(data_dir: Path) -> None:
    """The default limiter refuses the 121st call of one tool for one actor."""
    solo = Engine(_solo(data_dir / "solo"))
    prod = Engine(Settings(data_dir=data_dir / "prod", mode="fleet"))
    capacity = prod.settings.rate_limit_calls

    for _ in range(capacity + 30):
        await _call(solo)
    for _ in range(capacity):
        await _call(prod)

    assert len(solo.audit.records()) == capacity + 30
    with pytest.raises(ToolError, match="rate limit exceeded"):
        await _call(prod)


async def test_a_rejected_call_is_not_audited_in_prod(data_dir: Path) -> None:
    engine = Engine(Settings(data_dir=data_dir, mode="fleet", rate_limit_calls=1))
    await _call(engine)

    with pytest.raises(ToolError):
        await _call(engine)

    assert len(engine.audit.records()) == 1


# ------------------------------------------------------------------- leasing


async def test_solo_still_enforces_leases(data_dir: Path) -> None:
    """Solo mode does not short-circuit the lease manager (yet)."""
    engine = Engine(_solo(data_dir))
    dag_id = await engine.intent("do the thing", 1000, _steps())
    node = engine.plans[dag_id].nodes[0]

    with pytest.raises(LeaseNotHeldError):
        await engine.publish(node.id, _inline("result"), "summary")


async def test_solo_runs_the_full_cycle(data_dir: Path) -> None:
    engine = Engine(_solo(data_dir))
    dag_id = await engine.intent("do the thing", 1000, _steps())
    node = engine.plans[dag_id].nodes[0]

    claim = await engine.claim(node.id, "solo-agent")
    published = await engine.publish(node.id, _inline("the result"), "summary")

    assert claim.lease_id is not None
    assert await engine.read(handle=published.artifact, tier=3) == "the result"
