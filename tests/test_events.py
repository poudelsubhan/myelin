import asyncio

from myelin.console.bus import EventBus


async def test_fanout_replay_and_restart_sequence(tmp_path):
    bus = EventBus(tmp_path)
    first, second = bus.subscribe("run"), bus.subscribe("run")
    a, b = asyncio.create_task(anext(first)), asyncio.create_task(anext(second))
    await asyncio.sleep(0)
    await bus.emit("run", "run.started", {})
    assert (await a).seq == (await b).seq == 1
    await first.aclose()
    await second.aclose()
    restarted = EventBus(tmp_path)
    assert (await restarted.emit("run", "run.finished", {})).seq == 2
    assert [e.seq for e in restarted.replay("run", 1)] == [2]
