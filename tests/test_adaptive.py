"""Adaptive coordination mode: the plane finds out by itself if one agent or several use it.

"auto" starts on the fast single-agent path (stdio) or fully protected (http),
and switches to fleet for good the first time a second agent id claims a node.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from blake3 import blake3

from kyrx.config import Settings
from kyrx.engine import Engine
from kyrx.errors import ToolError
from kyrx.mcp.schemas import NativeStep
from kyrx.security.audit import GENESIS, AuditLog


def _steps(count: int = 4) -> list[NativeStep]:
    return [NativeStep(name=f"n{i}", consumes=[], ambiguous=False) for i in range(count)]


async def _engine_with_plan(settings: Settings, count: int = 4) -> tuple[Engine, list[str]]:
    engine = Engine(settings)
    dag_id = await engine.intent("do the thing", 1000, _steps(count))
    return engine, [node.id for node in engine.plans[dag_id].nodes]


async def _call(engine: Engine, tool: str = "read", actor: str = "a") -> None:
    async with engine.guard(tool, actor):
        pass


def _disk_chain(path: Path) -> list[dict[str, str]]:
    """Read the audit file back and check every link of the chain."""
    previous = GENESIS
    records: list[dict[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        digest, payload = line.split(" ", 1)
        assert blake3(payload.encode("utf-8")).hexdigest() == digest
        body = json.loads(payload)
        assert body["prev"] == previous
        assert body["index"] == len(records)
        previous = digest
        records.append(body)
    return records


# ------------------------------------------------------------------ settings


def test_the_default_mode_is_auto(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("KYRX_MODE", raising=False)
    monkeypatch.delenv("KYRX_SOLO_MODE", raising=False)

    settings = Settings(data_dir=data_dir)

    assert settings.mode == "auto"
    assert settings.effective_mode == "auto"


def test_the_mode_reads_the_environment(data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KYRX_MODE", "fleet")

    assert Settings(data_dir=data_dir).effective_mode == "fleet"


def test_the_legacy_solo_flag_still_forces_solo(data_dir: Path) -> None:
    settings = Settings(data_dir=data_dir, solo_mode=True)

    assert settings.effective_mode == "solo"


# --------------------------------------------------------------- start state


def test_auto_starts_solo_over_stdio(data_dir: Path) -> None:
    engine = Engine(Settings(data_dir=data_dir, mode="auto", transport="stdio"))

    assert engine.mode == "solo"
    assert engine.audit.persistent is False


def test_auto_starts_protected_over_http(data_dir: Path) -> None:
    engine = Engine(Settings(data_dir=data_dir, mode="auto", transport="http"))

    assert engine.mode == "fleet"
    assert engine.audit.persistent is True


async def test_a_forced_solo_never_switches(data_dir: Path) -> None:
    engine, nodes = await _engine_with_plan(Settings(data_dir=data_dir, mode="solo"))

    await engine.claim(nodes[0], "agent-1")
    await engine.claim(nodes[1], "agent-2")

    assert engine.mode == "solo"


async def test_a_forced_fleet_stays_fleet(data_dir: Path) -> None:
    engine, nodes = await _engine_with_plan(Settings(data_dir=data_dir, mode="fleet"))

    await engine.claim(nodes[0], "agent-1")

    assert engine.mode == "fleet"
    assert engine.audit.persistent is True


# ----------------------------------------------------------------- detection


async def test_one_agent_with_many_tasks_does_not_switch(data_dir: Path) -> None:
    """Tools other than claim use a node, plan or key as actor: not an agent identity."""
    engine, nodes = await _engine_with_plan(Settings(data_dir=data_dir, mode="auto"))

    for node in nodes:
        await engine.claim(node, "agent-1")
        await _call(engine, "publish", node)
        await _call(engine, "project", f"plan-{node}")
        await _call(engine, "read", f"key-{node}")

    assert engine.mode == "solo"


async def test_the_same_agent_claiming_again_does_not_switch(data_dir: Path) -> None:
    engine, nodes = await _engine_with_plan(Settings(data_dir=data_dir, mode="auto"))

    await engine.claim(nodes[0], "agent-1")
    await engine.claim(nodes[0], "agent-1")
    await engine.claim(nodes[1], "agent-1")

    assert engine.mode == "solo"


async def test_a_second_agent_switches_to_fleet(data_dir: Path) -> None:
    engine, nodes = await _engine_with_plan(Settings(data_dir=data_dir, mode="auto"))

    await engine.claim(nodes[0], "agent-1")
    before = engine.mode
    await engine.claim(nodes[1], "agent-2")

    assert (before, engine.mode) == ("solo", "fleet")
    assert engine.audit.persistent is True


async def test_the_switch_is_one_way(data_dir: Path) -> None:
    engine, nodes = await _engine_with_plan(Settings(data_dir=data_dir, mode="auto"))
    await engine.claim(nodes[0], "agent-1")
    await engine.claim(nodes[1], "agent-2")

    for node in nodes[2:]:
        await engine.claim(node, "agent-2")
        await _call(engine)

    assert engine.mode == "fleet"


# --------------------------------------------------------------- the switch


async def test_the_switch_keeps_the_history_on_disk(settings: Settings) -> None:
    solo_settings = settings.model_copy(update={"mode": "auto"})
    engine, nodes = await _engine_with_plan(solo_settings)
    for tool in ("read", "project", "trace"):
        await _call(engine, tool)
    assert not solo_settings.audit_path.exists()

    await engine.claim(nodes[0], "agent-1")
    await engine.claim(nodes[1], "agent-2")
    await _call(engine, "read")

    records = _disk_chain(solo_settings.audit_path)
    assert [r["tool"] for r in records[:3]] == ["read", "project", "trace"]
    event = next(r for r in records if r["tool"] == "engine")
    assert (event["outcome"], event["detail"]) == ("mode", "solo->fleet")
    assert records[-1]["tool"] == "read"
    assert engine.audit.verify() is True
    assert len(records) == len(engine.audit.records())


async def test_the_claim_that_reveals_the_second_agent_is_audited(settings: Settings) -> None:
    auto = settings.model_copy(update={"mode": "auto"})
    engine, nodes = await _engine_with_plan(auto)
    async with engine.guard("claim", "agent-1"):
        await engine.claim(nodes[0], "agent-1")

    async with engine.guard("claim", "agent-2"):
        await engine.claim(nodes[1], "agent-2")

    records = _disk_chain(auto.audit_path)
    assert [r["tool"] for r in records] == ["claim", "engine", "claim"]
    assert records[-1]["actor"] == "agent-2"
    assert records[-1]["outcome"] == "ok"


async def test_the_rate_limit_turns_on_after_the_switch(data_dir: Path) -> None:
    settings = Settings(data_dir=data_dir, mode="auto", rate_limit_calls=2)
    engine, nodes = await _engine_with_plan(settings)

    for _ in range(5):
        await _call(engine)
    await engine.claim(nodes[0], "agent-1")
    await engine.claim(nodes[1], "agent-2")

    await _call(engine)
    await _call(engine)
    with pytest.raises(ToolError, match="rate limit exceeded"):
        await _call(engine)


# ------------------------------------------------------------- audit itself


async def test_concurrent_calls_lose_no_audit_record(data_dir: Path) -> None:
    """Regression: parallel writes from threads used to drop and reorder lines."""
    settings = Settings(data_dir=data_dir, mode="fleet", rate_limit_calls=10_000)
    engine = Engine(settings)

    await asyncio.gather(*(_call(engine, "read", f"actor-{i}") for i in range(300)))

    assert len(_disk_chain(settings.audit_path)) == 300
    assert engine.audit.verify() is True


def test_attach_writes_the_history_once(tmp_path: Path) -> None:
    log = AuditLog()
    for tool in ("read", "claim", "publish"):
        log.record(tool=tool, actor="a", outcome="ok", ts=1.0)
    path = tmp_path / "audit.ndjson"

    log.attach(path)
    log.attach(path)
    log.record(tool="trace", actor="a", outcome="ok", ts=2.0)

    records = _disk_chain(path)
    assert [r["tool"] for r in records] == ["read", "claim", "publish", "trace"]
    assert log.persistent is True
