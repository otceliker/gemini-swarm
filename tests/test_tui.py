"""Headless smoke test for the Textual TUI — composes & mounts, no terminal needed."""
from __future__ import annotations

import asyncio

from swarm.ui.app import SwarmApp


def test_app_mounts_and_starts_in_url_phase():
    async def scenario():
        app = SwarmApp()
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.phase == "url"
            assert app.query_one("#prompt")
            assert app.query_one("#roster")
            assert app.query_one("#convo")

    asyncio.run(scenario())
