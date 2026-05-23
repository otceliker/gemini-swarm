"""Headless smoke test for the Textual TUI — composes & mounts, no terminal needed."""
from __future__ import annotations

import asyncio

from swarm.ui.app import SwarmApp, parse_mentions


def test_parse_mentions_routes_domains_vs_chat():
    valid = {"security", "openapi"}
    assert parse_mentions("@security add rate limiting", valid) == (["security"], "add rate limiting")
    assert parse_mentions("what does @nonexistent do?", valid) == ([], "what does @nonexistent do?")
    assert parse_mentions("@security @openapi sync schema", valid) == (["security", "openapi"], "sync schema")
    assert parse_mentions("just a plain question", valid) == ([], "just a plain question")


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
