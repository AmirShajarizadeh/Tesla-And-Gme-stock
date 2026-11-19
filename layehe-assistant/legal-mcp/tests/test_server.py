import asyncio
import functools
import http.server
import json
import threading
from pathlib import Path

import pytest

from legal_mcp import app, ara, browser
from legal_mcp.browser import FetchError
from legal_mcp.extract import site_of

F = Path(__file__).parent / "fixtures"
CHROMIUM = "/opt/pw-browsers/chromium"


def test_allowed_sites():
    assert app.allowed("https://ara.jri.ac.ir/Judge/Text/1")
    assert app.allowed("https://qavanin.ir/Law/TreeText/?IDS=1")
    for bad in ("https://evil.com/x", "file:///etc/passwd", "https://jri.ac.ir.evil.com/"):
        with pytest.raises(FetchError):
            app.allowed(bad)


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
    monkeypatch.setenv("LEGAL_MCP_BROWSER", CHROMIUM)
    monkeypatch.setattr(app, "ARA_BASE", site + "search.html")
    monkeypatch.setattr(app, "SITES", app.SITES | {site_of("127.0.0.1")})

    async def go():
        try:
            found = json.loads(await ara.ara_search(query="مطالبه وجه چک"))
            ruling = json.loads(await ara.legal_read(url=site + "ruling.html"))
        finally:
            await browser.close()
        return found, ruling

    found, ruling = asyncio.run(go())
    assert "results.html" in found["results_url"]  # the query was typed into the box and submitted
    assert len(found["items"]) == 4
    assert "ماده ۳۵۸" in ruling["text"]



@pytest.fixture()
def qavanin_site(tmp_path):
    """A local copy of the qavanin.ir paths: / (search) and /Law/TreeText/ (law text)."""
    q = F / "qavanin"
    (tmp_path / "index.html").write_text((q / "search.html").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "Law" / "TreeText").mkdir(parents=True)
    (tmp_path / "Law" / "TreeText" / "index.html").write_text((q / "law.html").read_text(encoding="utf-8"), encoding="utf-8")
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(tmp_path))
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.mark.skipif(not Path(CHROMIUM).exists(), reason="needs a local Chromium")
def test_law_tools_through_browser(qavanin_site, monkeypatch):
    from legal_mcp import qavanin

    monkeypatch.setenv("LEGAL_MCP_BROWSER", CHROMIUM)
    monkeypatch.setattr(qavanin, "BASE", qavanin_site)
    qavanin._cache.clear()

    async def go():
        try:
            found = json.loads(await qavanin.law_search(query="آیین دادرسی", kind="law"))
            art = json.loads(await qavanin.law_read(ids=found["items"][0]["id"], article="84", date="1400/1/1"))
        finally:
            await browser.close()
        return found, art

    found, art = asyncio.run(go())
    assert found["total"] == 3 and found["items"][0]["url"].startswith(qavanin_site + "/Law/TreeText/?IDS=")
    assert art["text_as_of"] == "1400/01/01"
    assert [s["label"] for s in art["sections"]] == ["ماده 84", "تبصره"]
