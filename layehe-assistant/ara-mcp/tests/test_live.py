"""Calls the real sites; run with `pytest -m live` from a network that reaches them (inside Iran, no VPN)."""

import asyncio
import json

import pytest

from ara_mcp import browser, server

pytestmark = pytest.mark.live


def test_live_search_and_read():
    async def go():
        try:
            found = json.loads(await server.ara_search(query="مطالبه وجه چک"))
            assert found["items"], found.get("page_text", "")[:500]
            doc = json.loads(await server.legal_read(url=found["items"][0]["url"]))
            assert len(doc["text"]) > 300
        finally:
            await browser.close()

    asyncio.run(go())
