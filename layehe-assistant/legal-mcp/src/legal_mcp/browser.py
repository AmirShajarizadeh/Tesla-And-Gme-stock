"""One shared headless browser (Playwright) for the legal sites.

A real browser gets past the CDN bot check (ArvanCloud) and renders pages that build their results with
JavaScript. It looks for a browser in this order: LEGAL_MCP_BROWSER (path to a Chrome/Chromium/Edge binary),
the Chromium that `playwright install chromium` downloads, then the installed Google Chrome, then Microsoft
Edge (always present on Windows), so most users need no extra download.
"""

from __future__ import annotations

import asyncio
import os
from typing import Any

from mcp.server.mcpserver.exceptions import ToolError
from playwright.async_api import Browser, BrowserContext, Error as PwError, Page, Playwright, async_playwright

from .extract import is_challenge

NAV_TIMEOUT = 30_000  # ms
CHALLENGE_WAIT = 15  # s for the bot check to set its cookie and reload
MAX_PAGES = 2  # concurrent pages: the sites are slow and rate-limit


class FetchError(ToolError):
    pass


_pw: Playwright | None = None
_browser: Browser | None = None
_context: BrowserContext | None = None
_lock = asyncio.Lock()
_slots = asyncio.Semaphore(MAX_PAGES)


def _proxy() -> dict[str, str] | None:
    p = os.environ.get("LEGAL_MCP_PROXY", "").strip()
    return {"server": p} if p else None


async def _launch(pw: Playwright) -> Browser:
    opts: dict[str, Any] = {"headless": os.environ.get("LEGAL_MCP_HEADLESS", "1") != "0", "proxy": _proxy()}
    attempts: list[dict[str, Any]] = []
    if path := os.environ.get("LEGAL_MCP_BROWSER", "").strip():
        attempts.append({"executable_path": path})
    attempts += [{}, {"channel": "chrome"}, {"channel": "msedge"}]
    errors = []
    for extra in attempts:
        try:
            return await pw.chromium.launch(**opts, **extra)
        except PwError as e:
            errors.append(str(e).splitlines()[0])
    raise FetchError(
        "No browser found. Install Google Chrome, or run `uvx playwright install chromium`, "
        "or set LEGAL_MCP_BROWSER to a Chrome/Edge path. Details: " + " | ".join(errors)
    )


async def context() -> BrowserContext:
    """One shared context, so a solved bot check (a cookie) serves every later call."""
    global _pw, _browser, _context
    async with _lock:
        if _browser is None or not _browser.is_connected():
            _pw = _pw or await async_playwright().start()
            _browser = await _launch(_pw)
            _context = None
        if _context is None:
            _context = await _browser.new_context(locale="fa-IR", timezone_id="Asia/Tehran")
    return _context


async def _settle(page: Page) -> None:
    """Wait out a bot-check page and let late JavaScript finish."""
    for _ in range(CHALLENGE_WAIT):
        if not is_challenge(await page.content()):
            break
        await asyncio.sleep(1)
    else:
        raise FetchError("The site kept showing its bot check. Turn off VPN, or try again in a minute.")
    try:
        await page.wait_for_load_state("networkidle", timeout=8_000)
    except PwError:
        pass  # pages with long-polling never go idle; what has loaded is enough


class open_page:
    """`async with open_page(url) as page:` a new tab that has finished loading `url`."""

    def __init__(self, url: str | None = None):
        self.url = url

    async def __aenter__(self) -> Page:
        await _slots.acquire()
        try:
            self.page = await (await context()).new_page()
            self.page.set_default_timeout(NAV_TIMEOUT)
            if self.url:
                await goto(self.page, self.url)
            return self.page
        except BaseException:
            await self._close()
            raise

    async def _close(self) -> None:
        try:
            if getattr(self, "page", None):
                await self.page.close()
        finally:
            _slots.release()

    async def __aexit__(self, *exc: object) -> None:
        await self._close()


async def fetch(url: str) -> tuple[str, str]:
    """HTML and final URL of a page."""
    async with open_page(url) as p:
        return await p.content(), p.url


async def goto(page: Page, url: str) -> None:
    try:
        resp = await page.goto(url, wait_until="domcontentloaded")
    except PwError as e:
        msg = str(e).splitlines()[0]
        if "ERR_NAME_NOT_RESOLVED" in msg or "ERR_CONNECTION" in msg or "Timeout" in msg:
            raise FetchError(
                f"Could not reach {url} ({msg}). These sites usually answer only from inside Iran: "
                "turn off VPN, or set LEGAL_MCP_PROXY to an Iranian proxy."
            ) from None
        raise FetchError(f"Opening {url} failed: {msg}") from None
    if resp and resp.status == 403:
        raise FetchError(f"{url} refused the request (HTTP 403). Turn off VPN or set LEGAL_MCP_PROXY.")
    if resp and resp.status >= 400:
        raise FetchError(f"{url} answered HTTP {resp.status}.")
    await _settle(page)


async def close() -> None:
    global _pw, _browser, _context
    if _browser:
        await _browser.close()
    if _pw:
        await _pw.stop()
    _pw = _browser = _context = None
