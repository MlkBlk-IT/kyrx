"""Sandbox invariants: host-only capabilities, quotas, and no network egress."""

from __future__ import annotations

import pytest
from wasmtime import FuncType, ValType

from kyrx.errors import SandboxError, SandboxTimeoutError
from kyrx.sandbox.isolate import RecordingBridge, SandboxLimits, WasmIsolate

# A module exporting add(i32, i32) -> i32, with no import at all.
ADD_MODULE = (b"\x00asm\x01\x00\x00\x00"
    b"\x01\x07\x01\x60\x02\x7f\x7f\x01\x7f"
    b"\x03\x02\x01\x00"
    b"\x07\x07\x01\x03add\x00\x00"
    b"\x0a\x09\x01\x07\x00\x20\x00\x20\x01\x6a\x0b")

# A module that never returns: block { loop { br 0 } }.
# A function body needs one more end than the blocks it opens: the loop, the
# block, and the function itself.
LOOP_MODULE = (b"\x00asm\x01\x00\x00\x00"
    b"\x01\x04\x01\x60\x00\x00"
    b"\x03\x02\x01\x00"
    b"\x07\x07\x01\x03run\x00\x00"
    b"\x0a\x0c\x01\x0a\x00\x03\x40\x03\x40\x0c\x00\x0b\x0b\x0b")

# A module importing env.log, the only capability the host may grant.
# Its body calls the imported function, so the host call is observable.
HOST_MODULE = (b"\x00asm\x01\x00\x00\x00"
    b"\x01\x05\x01\x60\x01\x7f\x00"
    b"\x02\x0b\x01\x03env\x03log\x00\x00"
    b"\x03\x02\x01\x00"
    b"\x07\x07\x01\x03run\x00\x01"
    b"\x0a\x08\x01\x06\x00\x20\x00\x10\x00\x0b")

# The same module, importing env.net: a capability the host never grants.
# The name keeps the length of the granted one, or the section sizes shift.
FETCH_MODULE = (b"\x00asm\x01\x00\x00\x00"
    b"\x01\x05\x01\x60\x01\x7f\x00"
    b"\x02\x0b\x01\x03env\x03net\x00\x00"
    b"\x03\x02\x01\x00"
    b"\x07\x07\x01\x03run\x00\x01"
    b"\x0a\x08\x01\x06\x00\x20\x00\x10\x00\x0b")


def test_a_module_runs_and_returns_its_result() -> None:
    isolate = WasmIsolate(SandboxLimits(fuel=1_000_000))

    result = isolate.run(ADD_MODULE, call="add", args=(2, 3))

    assert result.result == 5


def test_the_host_function_is_the_only_capability() -> None:
    isolate = WasmIsolate(SandboxLimits(fuel=1_000_000))
    bridge = RecordingBridge()

    def log(_value: int) -> None:
        bridge.record("env.log")

    bridge.register("env.log", FuncType([ValType.i32()], []), log)

    isolate.run(HOST_MODULE, bridge=bridge, call="run", args=(1,))

    assert bridge.calls() == ("env.log",)


def test_an_ungranted_import_cannot_be_instantiated() -> None:
    isolate = WasmIsolate(SandboxLimits(fuel=1_000_000))

    with pytest.raises(SandboxError):
        isolate.run(FETCH_MODULE, call="run", args=(1,))


def test_a_module_that_never_returns_is_stopped() -> None:
    isolate = WasmIsolate(SandboxLimits(fuel=50_000, timeout_ms=200))

    with pytest.raises(SandboxTimeoutError):
        isolate.run(LOOP_MODULE, call="run")


def test_the_fuel_budget_is_reported() -> None:
    isolate = WasmIsolate(SandboxLimits(fuel=1_000_000))

    result = isolate.run(ADD_MODULE, call="add", args=(2, 3))

    assert result.fuel_used > 0
