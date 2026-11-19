"""Turn legal-site HTML into plain text and result lists.

The parsers are deliberately generic (no site-specific CSS selectors): the sites change their markup, and
these were written without live access to them. They pick the main text block by text density and the
result links by the URL pattern that repeats most among numbered links.
"""

from __future__ import annotations

import re
from collections import Counter
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag

_YEH_KAF = str.maketrans({"ي": "ی", "ك": "ک", "ى": "ی"})
_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
_DROP = ("script", "style", "noscript", "svg", "iframe", "nav", "header", "footer", "form", "button", "select")
_BLOCKS = ("article", "main", "section", "div", "td")
_NAV_WORDS = {
    "صفحه اصلی", "خانه", "ورود", "خروج", "ثبت نام", "تماس با ما", "درباره ما", "راهنما", "جستجو", "جستجوی پیشرفته",
    "بعدی", "قبلی", "اول", "آخر", "صفحه بعد", "صفحه قبل", "home", "login", "next", "previous",
}  # fmt: skip
CHALLENGE_MARKERS = ("__arcsjs", "در ﺣﺎل اﻧﺘﻘﺎل", "در حال انتقال به سایت", "Transferring to the website")


def fa(s: str) -> str:
    """Arabic ي/ك to Persian ی/ک."""
    return s.translate(_YEH_KAF)


def ascii_digits(s: str) -> str:
    return s.translate(_DIGITS)


def is_challenge(html: str) -> bool:
    """True for a CDN bot-check page (ArvanCloud) instead of real content."""
    return len(html) < 30_000 and any(m in html for m in CHALLENGE_MARKERS)


def _clean_text(s: str) -> str:
    s = s.replace("\xa0", " ")
    s = re.sub(r"[ \t\r\f\v]+", " ", s)
    s = re.sub(r"\n\s*\n\s*\n+", "\n\n", "\n".join(line.strip() for line in s.split("\n")))
    return fa(s.strip())


def _soup(html: str) -> BeautifulSoup:
    soup = BeautifulSoup(html, "html.parser")
    for el in soup(_DROP):
        el.decompose()
    return soup


def _text(el: Tag) -> str:
    for br in el.find_all("br"):
        br.replace_with("\n")
    return _clean_text(el.get_text("\n"))


def _density(el: Tag) -> float:
    """Text length minus link text: the body of a ruling is long and mostly unlinked."""
    text = len(el.get_text(" ", strip=True))
    links = sum(len(a.get_text(" ", strip=True)) for a in el.find_all("a"))
    return text - 2 * links


def main_text(html: str) -> dict[str, str]:
    """Title and main text block of a page (a ruling, a law, an opinion)."""
    soup = _soup(html)
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    h1 = soup.find(["h1", "h2"])
    if h1 and h1.get_text(strip=True):
        title = h1.get_text(" ", strip=True)
    body = soup.body or soup
    best, best_score = body, _density(body) * 0.6  # the whole body wins only if no block is close to it
    for el in body.find_all(_BLOCKS):
        score = _density(el)
        if score > best_score:
            best, best_score = el, score
    # Prefer the innermost block that keeps nearly all of the winner's text (drops sidebars, keeps the ruling).
    while True:
        inner = [c for c in best.find_all(_BLOCKS, recursive=False) if _density(c) >= 0.85 * best_score]
        if len(inner) != 1:
            break
        best = inner[0]
    return {"title": fa(re.sub(r"\s+", " ", title).strip()), "text": _text(best)}


def site_of(host: str) -> str:
    """'ara.jri.ac.ir' -> 'jri.ac.ir', 'www.qavanin.ir' -> 'qavanin.ir'."""
    labels = host.lower().rstrip(".").split(".")
    n = 3 if len(labels) >= 3 and labels[-2] in ("ac", "gov", "co", "org", "net", "sch", "id") else 2
    return ".".join(labels[-n:])


def _path_key(path: str) -> str:
    """'/Judge/Text/12345' -> '/judge/text/#': the shape shared by all result links of one site."""
    parts = [p for p in path.lower().split("/") if p]
    return "/" + "/".join("#" if re.search(r"\d{3,}", p) else p for p in parts)


def result_links(html: str, base_url: str, limit: int = 20) -> list[dict[str, str]]:
    """Links on a search-result page that point to documents (rulings), with a short snippet around each."""
    soup = _soup(html)
    host = urlparse(base_url).hostname or ""
    found: dict[str, dict[str, str]] = {}
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        url = urljoin(base_url, href)
        u = urlparse(url)
        if u.hostname and host and site_of(u.hostname) != site_of(host):
            continue
        title = fa(re.sub(r"\s+", " ", a.get_text(" ", strip=True)))
        if title.lower() in _NAV_WORDS or re.fullmatch(r"[\d\s«»<>.]*", ascii_digits(title)):
            continue  # menu items and pager numbers
        numbered = bool(re.search(r"\d{3,}", ascii_digits(u.path + "?" + u.query)))
        if not numbered and len(title) < 15:
            continue
        if url in found:
            continue
        block = a.find_parent(["tr", "li", "article"]) or a.find_parent("div") or a.parent
        snippet = _clean_text(block.get_text(" ", strip=True)) if block else ""
        snippet = re.sub(r"\s+", " ", snippet.replace(title, "", 1)).strip()[:400]
        found[url] = {"title": title[:300], "url": url, "snippet": snippet, "_key": _path_key(u.path), "_num": numbered}
    items = list(found.values())
    # The result rows share one URL shape (e.g. /judge/text/#); keep that shape when it clearly dominates.
    shapes = Counter(i["_key"] for i in items if i["_num"])
    if shapes:
        shape, n = shapes.most_common(1)[0]
        if n >= 3:
            items = [i for i in items if i["_key"] == shape]
    for i in items:
        i.pop("_key"), i.pop("_num")
    return items[:limit]


def page_slice(text: str, offset: int, max_chars: int) -> dict[str, object]:
    """A window of a long text, cut at a paragraph or sentence edge where possible."""
    chunk = text[offset : offset + max_chars]
    out: dict[str, object] = {"chars_total": len(text), "offset": offset}
    if offset + max_chars < len(text):
        cut = max(chunk.rfind("\n"), chunk.rfind("."), chunk.rfind("؛"))
        if cut > max_chars * 0.6:
            chunk = chunk[: cut + 1]
        out["next_offset"] = offset + len(chunk)
    out["text"] = chunk
    return out
