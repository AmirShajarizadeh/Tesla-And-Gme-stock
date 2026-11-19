import asyncio
import functools
import http.server
import json
import threading
from pathlib import Path

import pytest

from ara_mcp import browser, server
from ara_mcp.browser import FetchError
from ara_mcp.extract import site_of

F = Path(__file__).parent / "fixtures"
CHROMIUM = "/opt/pw-browsers/chromium"


def test_allowed_sites():
    assert server._allowed("https://ara.jri.ac.ir/Judge/Text/1")
    assert server._allowed("https://qavanin.ir/Law/TreeText/?IDS=1")
    for bad in ("https://evil.com/x", "file:///etc/passwd", "https://jri.ac.ir.evil.com/"):
        with pytest.raises(FetchError):
            server._allowed(bad)


@pytest.fixture(scope="module")
def site():
    """Serve the fixtures on localhost; /results.html is what the search form submits to."""
    (F / "results.html").write_text((F / "search.html").read_text(encoding="utf-8"), encoding="utf-8")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(F))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}/"
    srv.shutdown()
    (F / "results.html").unlink()


@pytest.mark.skipif(not Path(CHROMIUM).exists(), reason="needs a local Chromium")
def test_search_and_read_through_browser(site, monkeypatch):
    monkeypatch.setenv("ARA_MCP_BROWSER", CHROMIUM)
    monkeypatch.setattr(server, "ARA_BASE", site + "search.html")
    monkeypatch.setattr(server, "SITES", server.SITES | {site_of("127.0.0.1")})

    async def go():
        try:
            found = json.loads(await server.ara_search(query="مطالبه وجه چک"))
            ruling = json.loads(await server.legal_read(url=site + "ruling.html"))
        finally:
            await browser.close()
        return found, ruling

    found, ruling = asyncio.run(go())
    assert "results.html" in found["results_url"]  # the query was typed into the box and submitted
    assert len(found["items"]) == 4
    assert "ماده ۳۵۸" in ruling["text"]

