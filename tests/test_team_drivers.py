"""Contract tests for the provider-neutral Team driver boundary."""

import asyncio

import pytest

from coworker.events import Event, EventType
from coworker.teams import AgentDriver, DriverError, TurnEngineDriver


class _FakeTurnEngine:
    def __init__(self) -> None:
        self.messages = [{"role": "assistant", "content": "done"}]
        self.started: list[str] = []
        self.cancelled = False

    def run(self, instruction: str):
        self.started.append(instruction)

        async def events():
            yield Event(EventType.TURN_START, {"input": instruction})
            yield Event(EventType.TURN_END, {"status": "success"})

        return events()

    async def resume(self):
        yield Event(EventType.TURN_START, {"input": "(resumed)"})

    def request_interrupt(self) -> None:
        self.cancelled = True


def test_turn_engine_adapter_exposes_lifecycle_without_provider_details():
    engine = _FakeTurnEngine()
    driver = TurnEngineDriver(engine)

    assert isinstance(driver, AgentDriver)
    with pytest.raises(DriverError, match="non-empty"):
        driver.start("  ")

    driver.start("build it")
    events = asyncio.run(_collect(driver.stream()))
    assert [event.type for event in events] == [EventType.TURN_START, EventType.TURN_END]
    assert engine.started == ["build it"]
    assert driver.messages == engine.messages

    driver.cancel()
    assert engine.cancelled is True


def test_turn_engine_adapter_resume_and_recover_share_the_durable_path():
    async def collect_both():
        resumed = await _collect(driver.resume())
        recovered = await _collect(driver.recover())
        return resumed, recovered

    driver = TurnEngineDriver(_FakeTurnEngine())
    resumed, recovered = asyncio.run(collect_both())
    assert resumed[0].data == {"input": "(resumed)"}
    assert recovered[0].data == {"input": "(resumed)"}


async def _collect(events):
    return [event async for event in events]
