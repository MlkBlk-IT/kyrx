"""Process-level proofs: KYRX launched the way a real MCP client launches it.

These tests start the server as a subprocess and talk to it over the wire, so
they cover what unit tests cannot: the transport, the bearer check and the
tool surface exactly as an agent sees it.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import closing
from pathlib import Path

import httpx2
import pytest
from blake3 import blake3
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

from kyrx.security.audit import GENESIS
from kyrx.security.token import generate_token

TOOLS = {
    "beat",
    "claim",
    "fail",
    "intent",
    "manifest",
    "project",
    "prove",
    "publish",
    "read",
    "trace",
}
HELLO = "inline:aGVsbG8="  # base64 of "hello"
_STEPS = [{"name": "a"}, {"name": "b", "consumes": ["a"]}]


async def _exercise(session: ClientSession) -> None:
    """Run a real coordination cycle through the public tools."""
    await session.initialize()
    assert {tool.name for tool in (await session.list_tools()).tools} == TOOLS

    plan = await session.call_tool(
        "intent", {"intent": "e2e", "budgetTokens": 1000, "steps": _STEPS}
    )
    assert plan.structured_content is not None
    dag_id = plan.structured_content["dagId"]

    manifest = await session.call_tool("manifest", {"dagId": dag_id})
    assert manifest.structured_content is not None
    node = manifest.structured_content["nodeIds"][0]

    claim = await session.call_tool("claim", {"nodeId": node, "agentId": "e2e"})
    assert claim.structured_content is not None
    lease = claim.structured_content["leaseId"]
    assert not (await session.call_tool("beat", {"leaseId": lease})).is_error

    published = await session.call_tool(
        "publish", {"nodeId": node, "artifactRef": HELLO, "summary": "done"}
    )
    assert published.structured_content is not None
    artifact = published.structured_content["artifact"]
    assert not (await session.call_tool("read", {"handle": artifact})).is_error

    trace = await session.call_tool("trace", {"dagId": dag_id})
    assert trace.structured_content is not None
    assert len(trace.structured_content["entries"]) == 1

    refused = await session.call_tool(
        "publish", {"nodeId": node, "artifactRef": "inline:not base64!", "summary": "x"}
    )
    assert refused.is_error
    assert "[validation_error]" in str(refused.content)

    unknown = await session.call_tool("manifest", {"dagId": "does-not-exist"})
    assert unknown.is_error
    assert "[not_found]" in str(unknown.content)


async def test_stdio_serves_the_ten_tools(tmp_path: Path) -> None:
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "kyrx", "serve", "--transport", "stdio"],
        env={"KYRX_DATA_DIR": str(tmp_path / "var")},
    )

    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await _exercise(session)


async def test_stdio_switches_to_fleet_when_a_second_agent_claims(tmp_path: Path) -> None:
    """The plane finds out by itself, over the wire, that it is no longer alone."""
    data = tmp_path / "var"
    audit = data / "audit.ndjson"
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "kyrx", "serve", "--transport", "stdio"],
        env={"KYRX_DATA_DIR": str(data)},
    )

    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        await session.initialize()
        plan = await session.call_tool(
            "intent", {"intent": "e2e", "budgetTokens": 1000, "steps": _STEPS}
        )
        assert plan.structured_content is not None
        manifest = await session.call_tool("manifest", {"dagId": plan.structured_content["dagId"]})
        assert manifest.structured_content is not None
        nodes = manifest.structured_content["nodeIds"]

        first = await session.call_tool("claim", {"nodeId": nodes[0], "agentId": "agent-1"})
        assert not first.is_error
        assert not audit.exists()  # one agent: fast path, nothing written to disk

        second = await session.call_tool("claim", {"nodeId": nodes[1], "agentId": "agent-2"})
        assert not second.is_error
        assert audit.exists()  # a second agent: protections on, history flushed

    previous, tools = GENESIS, []
    for line in audit.read_text(encoding="utf-8").splitlines():
        digest, payload = line.split(" ", 1)
        assert blake3(payload.encode("utf-8")).hexdigest() == digest
        body = json.loads(payload)
        assert body["prev"] == previous
        previous = digest
        tools.append(body["tool"])
    assert tools == ["intent", "manifest", "claim", "engine", "claim"]


def _free_port() -> int:
    with closing(socket.socket()) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def http_server(tmp_path: Path) -> Iterator[tuple[str, str]]:
    """A KYRX HTTP server on a free loopback port, and its bearer token."""
    port = _free_port()
    token = generate_token()
    env = {
        **os.environ,
        "KYRX_AUTH_MODE": "token",
        "KYRX_AUTH_TOKEN": token,
        "KYRX_DATA_DIR": str(tmp_path / "var"),
    }
    proc = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-m",
            "kyrx",
            "serve",
            "--transport",
            "http",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 30
        while True:
            if proc.poll() is not None:
                pytest.fail("the KYRX HTTP server exited before accepting connections")
            with closing(socket.socket()) as sock:
                sock.settimeout(0.5)
                if sock.connect_ex(("127.0.0.1", port)) == 0:
                    break
            if time.monotonic() > deadline:
                pytest.fail("the KYRX HTTP server did not open its port within 30 s")
            time.sleep(0.2)
        yield f"http://127.0.0.1:{port}/mcp", token
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


async def test_http_checks_the_bearer_token_and_serves_the_ten_tools(
    http_server: tuple[str, str],
) -> None:
    url, token = http_server
    initialize = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "e2e", "version": "0"},
        },
    }
    headers = {"Accept": "application/json, text/event-stream"}

    async with httpx2.AsyncClient() as plain:
        assert (await plain.post(url, json=initialize, headers=headers)).status_code == 401
        wrong = {**headers, "Authorization": "Bearer wrong"}
        assert (await plain.post(url, json=initialize, headers=wrong)).status_code == 401

    async with (
        httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as authed,
        streamable_http_client(url, http_client=authed) as streams,
        ClientSession(streams[0], streams[1]) as session,
    ):
        await _exercise(session)
