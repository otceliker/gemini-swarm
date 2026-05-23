"""Headless mount + event-rendering test for the engine TUI."""
from __future__ import annotations

import asyncio

from swarm.engine import events as E
from swarm.engine.events import Event
from swarm.ui.engine_app import EngineApp


def test_engine_app_mounts_and_renders_events():
    async def scenario():
        app = EngineApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.phase == "source"
            assert app.query_one("#channel")
            assert app.query_one("#bible")
            # feed a few events the way the bus would, on the UI thread
            app._on_event(Event(E.SEGMENTS, {"segments": ["chunk-0000", "chunk-0001"]}))
            app._on_event(Event(E.MUTATION, {"segment": "chunk-0000", "state": "start"}))
            app._on_event(Event(E.MUTATION,
                                {"segment": "chunk-0000", "state": "done", "ok": True, "summary": "x"}))
            app._on_event(Event(E.DECISION, {"text": "Troy is a containment project"}))
            await pilot.pause()
            assert app.segments == ["chunk-0000", "chunk-0001"]
            assert app.state["chunk-0000"] == "passed"

    asyncio.run(scenario())
