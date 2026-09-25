"""A silent stretch in a turn must not look like a dead connection.

A 60s screen froze the event loop; the Next.js rewrite proxy cuts an idle
upstream at 30s, the server saw a disconnect and cancelled the turn, and the
page waited forever for a `done` that never came.
"""
import asyncio

from backend.routers.chat import _with_keepalive


def test_silence_yields_heartbeats_and_the_event_still_arrives():
    async def slow():
        yield "start"
        await asyncio.sleep(0.35)          # a tool that takes a while
        yield "done"

    async def collect():
        return [e async for e in _with_keepalive(slow(), every=0.1)]

    got = asyncio.run(collect())
    assert got[0] == "start" and got[-1] == "done"
    assert got.count(None) >= 2            # heartbeats while waiting
    assert [e for e in got if e is not None] == ["start", "done"]


def test_a_busy_stream_gets_no_heartbeats():
    async def fast():
        for i in range(5):
            yield i

    async def collect():
        return [e async for e in _with_keepalive(fast(), every=5)]

    assert asyncio.run(collect()) == [0, 1, 2, 3, 4]
