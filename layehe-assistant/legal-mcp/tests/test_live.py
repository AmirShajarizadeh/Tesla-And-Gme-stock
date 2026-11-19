"""Calls the real sites; run with `pytest -m live` from a network that reaches them (inside Iran, no VPN)."""

import asyncio
import json

import pytest

from legal_mcp import ara, browser

pytestmark = pytest.mark.live


def test_live_search_and_read():
    async def go():
        try:
            found = json.loads(await ara.ara_search(query="مطالبه وجه چک"))
            assert found["items"], found.get("page_text", "")[:500]
            doc = json.loads(await ara.legal_read(url=found["items"][0]["url"]))
            assert len(doc["text"]) > 300
        finally:
            await browser.close()

    asyncio.run(go())


def test_live_law_article():
    from legal_mcp import qavanin

    async def go():
        try:
            found = json.loads(await qavanin.law_search(query="قانون مدنی", kind="law"))
            civil = next(i for i in found["items"] if i["title"].strip() == "قانون مدنی")
            art = json.loads(await qavanin.law_read(ids=civil["id"], article="10"))
            assert "قراردادهای خصوصی" in art["sections"][0]["text"]
        finally:
            await browser.close()

    asyncio.run(go())
