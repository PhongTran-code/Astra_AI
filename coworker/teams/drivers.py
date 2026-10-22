"""Provider-neutral driver boundary for Team agent execution.

The legacy ``TurnEngine`` owns provider calls, tool loops, and surface-oriented
events.  Team code must not depend on that concrete engine forever, so this
module defines the small lifecycle surface the durable worker needs and wraps
the current engine as the first adapter.

The adapter intentionally exposes the existing ``Event`` stream as a
transitional internal type.  ``execution_events.AgentExecutionEvent`` is the
durable Team contract and is applied by the worker immediately before
persistence; a future provider adapter can emit the same internal events
without changing Team routing, leases, or projections.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any, Protocol, runtime_checkable

from ..events import Event


class DriverError(RuntimeError):
    """A driver lifecycle operation was used in an invalid state."""


@runtime_checkable
class AgentDriver(Protocol):
    """Execution lifecycle required by the canonical Team worker."""

    @property
    def messages(self) -> list[dict[str, Any]]:
        """Current provider-neutral conversation state used for final output."""
        ...

    def start(self, instruction: str) -> None:
        """Prepare a new turn for streaming."""
        ...

    def cancel(self) -> None:
        """Request best-effort interruption of the active turn."""
        ...

    async def stream(self) -> AsyncIterator[Event]:
        """Yield events for the turn prepared by :meth:`start`."""
        ...

    async def resume(self) -> AsyncIterator[Event]:
        """Resume a durable prompt-suspended turn."""
        ...

    async def recover(self) -> AsyncIterator[Event]:
        """Recover a driver-owned turn after worker restart."""
        ...


class TurnEngineDriver:
    """Adapt the current ``TurnEngine`` to the Team ``AgentDriver`` SPI."""

    def __init__(self, engine: Any) -> None:
        required = ("run", "resume", "request_interrupt", "messages")
        missing = [name for name in required if not hasattr(engine, name)]
        if missing:
            raise DriverError(
                "TurnEngine adapter requires: " + ", ".join(sorted(missing))
            )
        self.engine = engine
        self._stream: AsyncIterator[Event] | None = None

    @property
    def messages(self) -> list[dict[str, Any]]:
        return self.engine.messages

    def start(self, instruction: str) -> None:
        if not isinstance(instruction, str) or not instruction.strip():
            raise DriverError("driver instruction must be a non-empty string")
        if self._stream is not None:
            raise DriverError("driver already has an active turn")
        self._stream = self.engine.run(instruction)

    def cancel(self) -> None:
        self.engine.request_interrupt()

    async def stream(self) -> AsyncIterator[Event]:
        stream = self._stream
        if stream is None:
            raise DriverError("driver.start must be called before stream")
        self._stream = None
        async for event in stream:
            yield event

    async def resume(self) -> AsyncIterator[Event]:
        async for event in self.engine.resume():
            yield event

    async def recover(self) -> AsyncIterator[Event]:
        """Use TurnEngine's durable resume path as the first recovery strategy."""

        async for event in self.resume():
            yield event
