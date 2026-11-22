"""WASM execution isolate (I8).

The isolate runs a WebAssembly module with host functions as its only
capability: no filesystem, no sockets, no environment. A module that imports
anything else fails to instantiate, so network egress is structurally
impossible rather than merely forbidden.

The CPU budget is metered with wasmtime fuel, the memory budget with a store
limit, and the wall-clock budget with an epoch deadline driven by a watchdog
thread.

The host-function bridge is the only place where the module talks to the
outside. Its typing is delegated to the wasmtime SDK, which is dynamically
typed at that boundary; the rest of the package stays strictly typed.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from wasmtime import Config, Engine, Func, FuncType, Linker, Module, Store, Trap

from kyrx.errors import SandboxError, SandboxTimeoutError

__all__ = [
    "HostBridge",
    "HostCapability",
    "RecordingBridge",
    "SandboxLimits",
    "SandboxResult",
    "WasmIsolate",
]

WATCHDOG_TICK_S = 0.01


@dataclass(frozen=True, slots=True)
class SandboxLimits:
    """The quotas of one node."""

    timeout_ms: int = 30_000
    memory_bytes: int = 64 * 1024 * 1024
    fuel: int = 200_000_000


@dataclass(frozen=True, slots=True)
class SandboxResult:
    """Outcome of a sandboxed execution."""

    result: object = None
    fuel_used: int = 0
    host_calls: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class HostCapability:
    """One function the sandboxed module may call."""

    name: str
    signature: FuncType
    function: Callable[..., object]


class HostBridge:
    """The capabilities a sandboxed module may call."""

    def capabilities(self) -> Mapping[str, HostCapability]:
        """Capabilities, keyed by ``module.name``."""
        raise NotImplementedError

    def calls(self) -> tuple[str, ...]:
        """Names of the host functions that were called."""
        raise NotImplementedError


@dataclass
class RecordingBridge(HostBridge):
    """A bridge that records every call it receives."""

    _capabilities: dict[str, HostCapability] = field(default_factory=dict)
    _calls: list[str] = field(default_factory=list)

    def register(
        self,
        name: str,
        signature: FuncType,
        function: Callable[..., object],
    ) -> None:
        """Expose a host function under ``module.name``."""
        self._capabilities[name] = HostCapability(
            name=name,
            signature=signature,
            function=function,
        )

    def capabilities(self) -> Mapping[str, HostCapability]:
        return dict(self._capabilities)

    def calls(self) -> tuple[str, ...]:
        return tuple(self._calls)

    def record(self, name: str) -> None:
        """Note that a host function was called."""
        self._calls.append(name)


class WasmIsolate:
    """Runs a WASM module under fuel, memory and deadline quotas."""

    def __init__(self, limits: SandboxLimits | None = None) -> None:
        self._limits = limits or SandboxLimits()
        config = Config()
        config.consume_fuel = True
        config.epoch_interruption = True
        self._engine = Engine(config)

    @property
    def limits(self) -> SandboxLimits:
        return self._limits

    def run(
        self,
        module_bytes: bytes,
        *,
        bridge: HostBridge | None = None,
        call: str | None = None,
        args: tuple[object, ...] = (),
    ) -> SandboxResult:
        """Instantiate and run a module, then return its result."""
        store = Store(self._engine)
        store.set_fuel(self._limits.fuel)
        store.set_limits(memory_size=self._limits.memory_bytes)
        store.set_epoch_deadline(max(1, int(self._limits.timeout_ms / 1000 / WATCHDOG_TICK_S)))

        linker = Linker(self._engine)
        if bridge is not None:
            for name, capability in bridge.capabilities().items():
                module_name, _, function_name = name.partition(".")
                linker.define_func(
                    module_name,
                    function_name,
                    capability.signature,
                    capability.function,
                )

        stop = threading.Event()
        watchdog = threading.Thread(target=self._tick, args=(stop,), daemon=True)
        watchdog.start()
        try:
            module = Module(self._engine, module_bytes)
            instance = linker.instantiate(store, module)
            result: object = None
            if call is not None:
                exports = instance.exports(store)
                target = exports[call]
                if not isinstance(target, Func):
                    raise SandboxError(f"export {call!r} is not a function")
                result = target(store, *args)
            fuel_used = self._limits.fuel - store.get_fuel()
            return SandboxResult(
                result=result,
                fuel_used=fuel_used,
                host_calls=bridge.calls() if bridge is not None else (),
            )
        except Trap as exc:
            raise SandboxTimeoutError(
                f"the sandbox stopped the module: {exc}",
                fuel=self._limits.fuel,
                timeout_ms=self._limits.timeout_ms,
            ) from exc
        except SandboxError:
            raise
        except Exception as exc:
            raise SandboxError(f"the sandbox refused the module: {exc}") from exc
        finally:
            stop.set()

    def _tick(self, stop: threading.Event) -> None:
        while not stop.wait(WATCHDOG_TICK_S):
            self._engine.increment_epoch()
